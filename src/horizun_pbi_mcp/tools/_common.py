"""Utilidades compartidas por las tools MCP: envelope, logging y errores.

`guard()` es el unico punto por el que salen las respuestas. Desde la Macrofase
A envuelve el resultado en un envelope ADITIVO: conserva `ok` y todos los campos
que la tool ya devolvia, y anade metadatos (`status`, `request_id`, `operation`,
`duration_ms`, `warnings`, `side_effects`).

El nombre de la operacion se deduce del marco de llamada, para no tener que
tocar las 34 firmas existentes.
"""
from __future__ import annotations

import sys
from typing import Any, Callable, Dict, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import envelope, telemetry

log = get_logger("tools")


def _marco_de_la_tool():
    """Sube por la pila hasta el marco de la tool (`pbi_*`). None si no hay."""
    frame = sys._getframe(1)
    profundidad = 0
    while frame is not None and profundidad < 12:
        if frame.f_code.co_name.startswith("pbi_"):
            return frame
        frame = frame.f_back
        profundidad += 1
    return None


def _infer_operation() -> str:
    """Nombre de la tool que esta ejecutandose."""
    frame = _marco_de_la_tool()
    return frame.f_code.co_name if frame is not None else "unknown_operation"


def _argumentos_de_la_tool(frame) -> Dict[str, Any]:
    """Argumentos con los que se llamo la tool, leidos de su marco.

    Se usan como payload de idempotencia: asi la huella es la de los argumentos
    REALES, sin que cada tool tenga que repetirlos a mano al llamar a guard()
    —que es justo donde se colarian discrepancias—.
    """
    if frame is None:
        return {}
    codigo = frame.f_code
    nombres = codigo.co_varnames[:codigo.co_argcount + codigo.co_kwonlyargcount]
    locales = frame.f_locals
    return {n: locales[n] for n in nombres
            if n in locales and n not in ("request_id", "self", "mcp")}


#: Errores que pueden haber dejado algo a medias. Reintentar sobre uno de estos
#: no es reintentar: es aplicar dos veces media operacion.
_NUNCA_REINTENTABLES = ("bulk_partially_applied", "rollback_incomplete",
                        "unexpected")

#: Errores que solo son reintentables si ELLOS MISMOS acreditan que lo que
#: lanzaron ya termino. El hilo que ejecuta `SaveChanges()` es `daemon=True` y
#: no se puede parar, asi que aqui no vale el silencio: si no consta que el
#: motor confirmo la cancelacion, se asume que sigue corriendo.
_REINTENTABLES_SOLO_SI_CANCELADO = ("refresh_timeout",)


def _es_seguro_reintentar(salida: Dict[str, Any]) -> bool:
    """Si tras el fallo el proyecto quedo como estaba, reintentar es seguro.

    Ante la duda se dice que NO. Un `unexpected` pudo dejar algo a medias, y
    afirmar lo contrario seria inventarse una garantia.

    CORE-003. La respuesta de un timeout de refresh decia dos cosas a la vez en
    el mismo objeto: que el comando *puede seguir ejecutandose en Power BI
    Desktop* (`cancel_confirmed: false`) y que reintentar era seguro. Las dos no
    pueden ser verdad. Reintentar con ese consejo solapa dos `SaveChanges()`
    sobre el mismo modelo.

    La causa era la forma del clasificador, no un codigo olvidado: enumeraba lo
    peligroso y daba por seguro TODO lo demas, asi que fallaba hacia el lado
    optimista cada vez que aparecia un error nuevo. Por eso ademas de listar
    `refresh_timeout` se mira el HECHO: cualquier salida que declare
    `cancel_confirmed: false` esta afirmando que algo sigue corriendo, venga del
    codigo que venga. La contradiccion pasa a ser inexpresable en vez de
    improbable, que es lo que pedia el criterio de cierre.
    """
    if salida.get("error") in _NUNCA_REINTENTABLES:
        return False
    detalles = salida.get("details")
    detalles = detalles if isinstance(detalles, dict) else {}
    # El HECHO, venga del codigo que venga: declarar `cancel_confirmed: false`
    # es afirmar que algo sigue corriendo.
    if detalles.get("cancel_confirmed") is False:
        return False
    # Y para los que cancelan, el silencio tampoco acredita: hace falta un `true`
    # explicito. Sin esto, un `refresh_timeout` al que le faltaran los detalles
    # volveria a caer en el `True` por defecto.
    if (salida.get("error") in _REINTENTABLES_SOLO_SI_CANCELADO
            and detalles.get("cancel_confirmed") is not True):
        return False
    return salida.get("status") not in ("rollback_incomplete", "partial_failure")


def alias_unico(**candidatos: Any) -> Optional[Any]:
    """UN valor entre varios nombres equivalentes de un mismo parametro.

    Las tools admiten alias -`path`, `pbip_path`, `project_path`; `name` y
    `object_name`- porque son los nombres que uno escribe de forma natural.
    Lo que no se hace es ADIVINAR: si llegan dos con valores distintos, se
    devuelve un conflicto claro. Ignorar uno en silencio es como no aplicar
    un filtro y contestar con todo el modelo.
    """
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    dados = {nombre: valor for nombre, valor in candidatos.items()
             if valor is not None and str(valor).strip() != ""}
    if not dados:
        return None
    valores = {str(v).strip() for v in dados.values()}
    if len(valores) > 1:
        nombres = " y ".join(f"`{n}`" for n in dados)
        raise ValidationError(
            f"{nombres} son el mismo parametro y llegaron distintos; no se "
            "adivina cual vale.",
            details={"conflict": {n: str(v) for n, v in dados.items()},
                     "reason": "alias_conflict"})
    return next(iter(dados.values()))


def ruta_de_proyecto(path: Optional[str] = None,
                     pbip_path: Optional[str] = None,
                     project_path: Optional[str] = None):
    """La ruta del proyecto sobre la que operar, con sus alias y el activo.

    `pbi_session_info` devuelve `active_pbip.pbip_path`, asi que ese es el
    nombre que uno escribe despues; pero las tools de Desktop lo llamaban
    `path` y rechazaban el otro con un error de validacion. Se aceptan los
    tres nombres -y ninguno: entonces se usa el proyecto activo, que el
    servidor ya conoce-. Una CARPETA de proyecto se resuelve con la misma
    regla que `pbi_prepare_project`: un unico `.pbip` vale; con varios se
    dice cuales hay, y no se elige uno por orden alfabetico.
    """
    from pathlib import Path

    from horizun_pbi_mcp.config import get_session
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    elegida = alias_unico(path=path, pbip_path=pbip_path,
                          project_path=project_path)
    if elegida is None:
        activo = get_session().active_pbip
        if activo is None:
            raise ValidationError(
                "No se indico proyecto y no hay ninguno activo. Pasa `path` "
                "(o `pbip_path` / `project_path`), o abre uno con "
                "pbi_open_pbip_project.",
                details={"parameter": "path"})
        elegida = activo.pbip_path
    ruta = Path(str(elegida)).expanduser()
    if ruta.is_dir():
        from horizun_pbi_mcp.services import project_resolver

        ruta, _motivo = project_resolver.resolver_entrada(ruta)
    return ruta.resolve()


def _nota_de_recuperacion() -> Optional[Dict[str, Any]]:
    """La recuperacion de sesion que ocurrio DURANTE esta tool, si la hubo.

    Se lee del singleton sin crearlo: `guard()` corre tambien en pruebas y en
    tools que no tocan la sesion, y crearla aqui leeria `session.json` de
    quien ejecuta la suite.
    """
    from horizun_pbi_mcp import config

    sesion = getattr(config, "_session", None)
    if sesion is None:
        return None
    tomar = getattr(sesion, "consume_recovery", None)
    return tomar() if callable(tomar) else None


def guard(fn: Callable[[], Any], *, operation: Optional[str] = None,
          request_id: Optional[str] = None) -> Dict[str, Any]:
    """Ejecuta `fn` y devuelve siempre un dict serializable con envelope.

    - Exito: {"ok": True, ...resultado, "status": ..., "request_id": ...}.
    - Error de dominio: {"ok": False, "error": <codigo>, "message": ...}.
    - Error inesperado: {"ok": False, "error": "unexpected", ...}.

    Nunca oculta el mensaje original del motor.
    """
    op = operation or _infer_operation()
    rid = request_id or envelope.new_request_id()
    timer = envelope.Timer()
    # Una nota que quedara de una llamada anterior no es evidencia de ESTA:
    # se descarta antes de ejecutar nada.
    _nota_de_recuperacion()

    with timer:
        try:
            resultado = fn()
            payload = resultado if isinstance(resultado, dict) else {"result": resultado}
            recuperacion = _nota_de_recuperacion()
            if recuperacion:
                # La tool reconecto una sesion caducada por el camino: se
                # dice en la respuesta, no solo en el log.
                payload = {**payload, "session_recovery": recuperacion}
            salida = envelope.success(payload, operation=op, request_id=rid,
                                      duration_ms=timer.ms)
            telemetry.log_operation(
                log, operation=op, request_id=rid, duration_ms=timer.ms,
                status=salida["status"], ok=True)
            return salida

        except PowerBIMCPError as exc:
            salida = envelope.failure(exc.code, exc.message, exc.details,
                                      operation=op, request_id=rid,
                                      duration_ms=timer.ms)
            recuperacion = _nota_de_recuperacion()
            if recuperacion:
                salida["session_recovery"] = recuperacion
            telemetry.log_operation(
                log, operation=op, request_id=rid, duration_ms=timer.ms,
                status=salida["status"], ok=False, error_code=exc.code)
            return salida

        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado en %s", op)
            salida = envelope.failure(
                "unexpected", str(exc), None, operation=op, request_id=rid,
                duration_ms=timer.ms, exc_type=type(exc).__name__)
            recuperacion = _nota_de_recuperacion()
            if recuperacion:
                salida["session_recovery"] = recuperacion
            telemetry.log_operation(
                log, operation=op, request_id=rid, duration_ms=timer.ms,
                status=salida["status"], ok=False, error_code="unexpected")
            return salida


def guard_mutation(fn: Callable[[], Any]) -> Dict[str, Any]:
    """`guard()` para tools que MUTAN, con idempotencia por `request_id`.

    El `request_id` y el payload se leen del marco de la tool: el payload es
    literalmente con lo que se la llamo, sin duplicar la lista de argumentos en
    cada sitio.

    Sin `request_id` no hay proteccion posible —cada llamada seria una peticion
    distinta—, asi que se comporta como `guard()`. Es opt-in del cliente, igual
    que la cabecera `Idempotency-Key` de cualquier API con mutaciones.
    """
    from horizun_pbi_mcp.services import idempotency

    marco = _marco_de_la_tool()
    op = marco.f_code.co_name if marco is not None else "unknown_operation"
    rid = (marco.f_locals.get("request_id") if marco is not None else "") or ""
    rid = str(rid).strip()
    if not rid:
        return guard(fn, operation=op)

    payload = {"operation": op, "arguments": _argumentos_de_la_tool(marco)}
    store = idempotency.store_por_defecto()

    try:
        intento = idempotency.comenzar_intento(store, rid, op, payload)
        previo = intento.replay
    except PowerBIMCPError as exc:
        salida = envelope.failure(exc.code, exc.message, exc.details,
                                  operation=op, request_id=rid, duration_ms=0)
        # Un resultado desconocido NO es seguro de reintentar: pudo aplicarse.
        # Decirlo en el sobre, y no solo en `details`, es lo que impide que un
        # cliente lo reintente en bucle.
        if isinstance(exc.details, dict) and "safe_to_retry" in exc.details:
            salida["safe_to_retry"] = exc.details["safe_to_retry"]
        return salida
    except Exception as exc:                           # noqa: BLE001
        # Todavia no se ejecuto la mutacion: es seguro fallar cerrado.
        log.exception("No se pudo iniciar la idempotencia de %s", op)
        salida = envelope.failure(
            "idempotency_unavailable",
            f"No se pudo reservar request_id antes de escribir: {exc}",
            {"original_type": type(exc).__name__}, operation=op,
            request_id=rid, duration_ms=0)
        salida["safe_to_retry"] = True
        return salida
    if previo is not None:
        return previo

    salida = guard(fn, operation=op, request_id=rid)
    if salida.get("ok"):
        try:
            idempotency.terminar_ok(store, rid, op, payload, salida,
                                    attempt_id=intento.attempt_id)
        except Exception as exc:                       # noqa: BLE001
            # La mutacion YA termino. Propagar esta excepcion haria que el
            # cliente creyera que fallo y la repitiera, duplicando paginas o
            # reemplazando de nuevo un proyecto que si se confirmo.
            log.exception("La mutacion %s se aplico, pero no se pudo guardar "
                          "su registro de idempotencia", op)
            salida.setdefault("warnings", []).append(
                "El cambio se aplico, pero no se pudo persistir su registro "
                "de idempotencia. No reintentes automaticamente esta misma "
                f"peticion: {type(exc).__name__}: {exc}")
            salida["status"] = envelope.WARNING
            salida["idempotency_persisted"] = False
            salida["safe_to_retry"] = False
    else:
        seguro = _es_seguro_reintentar(salida)
        salida["safe_to_retry"] = seguro
        try:
            idempotency.terminar_error(store, rid, op, payload, salida,
                                       safe_to_retry=seguro,
                                       attempt_id=intento.attempt_id)
        except Exception as exc:                       # noqa: BLE001
            log.exception("No se pudo persistir el fallo de %s", op)
            salida.setdefault("warnings", []).append(
                "No se pudo guardar el registro de idempotencia del fallo: "
                f"{type(exc).__name__}: {exc}")
            salida["idempotency_persisted"] = False
    return salida
