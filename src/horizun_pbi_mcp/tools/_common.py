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


def _es_seguro_reintentar(salida: Dict[str, Any]) -> bool:
    """Si tras el fallo el proyecto quedo como estaba, reintentar es seguro.

    Ante la duda se dice que NO. Un `unexpected` pudo dejar algo a medias, y
    afirmar lo contrario seria inventarse una garantia.
    """
    if salida.get("error") in ("bulk_partially_applied", "rollback_incomplete",
                              "unexpected"):
        return False
    return salida.get("status") not in ("rollback_incomplete", "partial_failure")


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

    with timer:
        try:
            resultado = fn()
            payload = resultado if isinstance(resultado, dict) else {"result": resultado}
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
            telemetry.log_operation(
                log, operation=op, request_id=rid, duration_ms=timer.ms,
                status=salida["status"], ok=False, error_code=exc.code)
            return salida

        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado en %s", op)
            salida = envelope.failure(
                "unexpected", str(exc), None, operation=op, request_id=rid,
                duration_ms=timer.ms, exc_type=type(exc).__name__)
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
        previo = idempotency.comenzar(store, rid, op, payload)
    except PowerBIMCPError as exc:
        return envelope.failure(exc.code, exc.message, exc.details,
                                operation=op, request_id=rid, duration_ms=0)
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
            idempotency.terminar_ok(store, rid, op, payload, salida)
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
                                       safe_to_retry=seguro)
        except Exception as exc:                       # noqa: BLE001
            log.exception("No se pudo persistir el fallo de %s", op)
            salida.setdefault("warnings", []).append(
                "No se pudo guardar el registro de idempotencia del fallo: "
                f"{type(exc).__name__}: {exc}")
            salida["idempotency_persisted"] = False
    return salida
