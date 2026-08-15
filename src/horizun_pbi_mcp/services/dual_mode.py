"""Precondicion central del parametro `mode` de las tools duales.

EL PROBLEMA, EN UNA LINEA: los dos destinos exigen estados de Power BI Desktop
mutuamente incompatibles.

    live  -> necesita Desktop ABIERTO   (TOM habla con msmdsrv.exe)
    pbip  -> necesita Desktop CERRADO   (si esta abierto, al guardar sobrescribe
                                         lo que escribamos en disco)

No existe ningun estado del sistema en el que ambos puedan escribirse con
seguridad dentro de una misma llamada. Peor aun: la implementacion dual
aplicaba primero `live` y despues `pbip`, asi que con Desktop abierto el
resultado era un estado PARCIAL determinista — el modelo en memoria cambiado y
el disco intacto.

Mientras la politica estricta este activa, `mode="both"` se rechaza ANTES de
producir cualquier efecto: antes de abrir una conexion TOM, de validar objetos
contra el motor, de crear un journal, de leer para planificar o de tocar un
archivo.

No hay bypass por variable de entorno. La decision de que hacer con `both`
(workflow en dos etapas, persistir solo por TOM, o abandonarlo) corresponde a
la Fase 1B.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, NoReturn, Optional

from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

LIVE = "live"
PBIP = "pbip"
BOTH = "both"
#: `auto` NO es un destino: se resuelve a `live` o a `pbip` mirando el estado
#: real antes de hacer nada. Existe porque el default historico es `live`, que
#: exige Desktop ABIERTO, mientras que el flujo que documenta
#: `pbi_create_pbip_project` -construir desde cero- exige escribir el modelo con
#: Desktop CERRADO. En ese flujo el default fallaba siempre. El default no se
#: cambia: eso romperia el contrato congelado y el comportamiento de clientes ya
#: configurados. Se anade el valor y se dice en el error cual toca.
AUTO = "auto"
VALID_MODES = (LIVE, PBIP, BOTH, AUTO)


class DualModeNotAvailableError(PowerBIMCPError):
    """`mode="both"` no puede ejecutarse con seguridad bajo la politica estricta.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de esta
    fase. `guard()` la serializa como cualquier error de dominio.
    """

    code = "dual_mode_not_safely_available"


_MENSAJE = (
    "mode='both' esta deshabilitado: no puede garantizarse en una sola llamada.\n"
    "  - 'live' necesita Power BI Desktop ABIERTO: TOM escribe en el modelo que "
    "Desktop tiene en memoria.\n"
    "  - 'pbip' necesita Desktop CERRADO: editar los archivos del proyecto "
    "mientras Desktop lo tiene abierto es inseguro, porque al guardar (Ctrl+S) "
    "sobrescribe lo escrito en disco.\n"
    "  - Los dos requisitos son incompatibles, asi que 'both' terminaria "
    "aplicando solo uno de los dos destinos.\n"
    "Elige explicitamente: mode='live' (con Desktop abierto; recuerda guardar "
    "con Ctrl+S) o mode='pbip' (con Desktop cerrado)."
)


def normalize_mode(mode: Optional[str]) -> str:
    """Normaliza y valida el modo. No decide si es ejecutable."""
    m = (mode or LIVE).lower().strip()
    if m not in VALID_MODES:
        raise ValidationError(
            f"mode invalido: '{mode}'. Usa live|pbip|both|auto.")
    return m


def resolver_auto(session: Any) -> str:
    """Convierte `auto` en el destino que el estado ACTUAL permite.

    La regla es la del sistema, no una preferencia: `live` necesita Desktop
    abierto sirviendo el modelo; `pbip` necesita el proyecto en disco y Desktop
    cerrado. Si los dos son posibles gana `live`, que es lo inmediato y no toca
    archivos; si ninguno lo es, se falla diciendo QUE falta, en vez de elegir
    uno y fallar mas adelante por otra razon.
    """
    from horizun_pbi_mcp.services import project_state

    activo = getattr(session, "active_pbip", None)

    # "Hay modelo" no es "hay un modelo GUARDADO": la sesion se restaura de
    # outputs/session.json al arrancar, asi que active_model puede apuntar a
    # un Desktop que se cerro hace dias. Y el escenario tipico de `auto` es
    # exactamente ese: se trabajo en live, se cerro Desktop -que es el estado
    # REQUERIDO para escribir pbip- y se pide crear una medida. Resolver
    # `live` por la sesion fantasma acabaria en StaleSessionError ordenando
    # reabrir, cuando `pbip` era viable y era lo que el estado real permitia.
    # Por eso aqui se VERIFICA, no se mira si el campo esta relleno.
    hay_modelo = False
    if getattr(session, "active_model", None) is not None:
        try:
            session.require_active_model()
            hay_modelo = True
        except Exception:                                # noqa: BLE001
            # Sesion obsoleta o inverificable: para `auto` equivale a que no
            # hay live. Quien quiera el error detallado pide mode='live'.
            hay_modelo = False

    if hay_modelo:
        return LIVE
    if activo is not None:
        # `detect` devuelve un ProjectOpenState, no un dict: pedirle `.get`
        # reventaba con AttributeError -y salia como `unexpected`- justo en el
        # camino que este modo existe para resolver.
        estado = project_state.detect(activo)
        abierto = (estado.get("state") if isinstance(estado, dict)
                   else getattr(estado, "state", None))
        if abierto == project_state.OPEN:
            raise ValidationError(
                "mode='auto' no puede resolverse: el proyecto esta ABIERTO en "
                "Power BI Desktop (no se puede escribir el TMDL) y no hay una "
                "sesion live seleccionada. Ejecuta pbi_select_model para usar "
                "'live', o cierra Desktop para usar 'pbip'.",
                details={"requested_mode": AUTO, "project_state": "open"})
        return PBIP
    raise ValidationError(
        "mode='auto' no puede resolverse: no hay modelo activo ni proyecto "
        ".pbip abierto. Ejecuta pbi_select_model (para 'live') o "
        "pbi_open_pbip_project (para 'pbip').",
        details={"requested_mode": AUTO, "available_modes": [LIVE, PBIP]})


def _pbip_escribible(session: Any) -> bool:
    """Si hay un proyecto .pbip activo y Desktop NO lo tiene abierto.

    Solo cuenta el estado CERRADO con la deteccion a favor: sugerir `pbip`
    cuando no se sabe si Desktop lo tiene abierto seria mandar a alguien a
    escribir un TMDL que Power BI puede sobrescribir al guardar.
    """
    from horizun_pbi_mcp.services import project_state

    activo = getattr(session, "active_pbip", None)
    if activo is None:
        return False
    try:
        estado = project_state.detect(activo)
    except Exception:                                    # noqa: BLE001
        return False
    if isinstance(estado, dict):
        return estado.get("state") == project_state.CLOSED
    return bool(getattr(estado, "writable", False))


#: Errores del destino `live` que en realidad describen "Desktop no esta ahi".
_LIVE_NO_DISPONIBLE = ("stale_session", "no_active_model")


def _con_pista_de_pbip(exc: PowerBIMCPError, session: Any) -> PowerBIMCPError:
    """Anade a un fallo de `live` la salida que el estado real SI permite.

    El defecto de las tools duales es `live`, que necesita Power BI Desktop
    abierto. Con Desktop cerrado y un proyecto .pbip delante, lo que salia era
    `stale_session` mandando ejecutar `pbi_list_desktop_models` y
    `pbi_select_model`: un camino que en ese estado no lleva a ninguna parte,
    porque no hay ningun modelo vivo que seleccionar. Faltaba decir que el
    mismo trabajo se hace con `mode='pbip'`.

    NO se cambia el destino por cuenta propia: escribir en el disco cuando
    alguien pidio el modelo en memoria es justo el error que este modulo existe
    para impedir. Se explica el camino y la decision se deja fuera. Tampoco se
    comprueba nada por adelantado: la pista se calcula cuando el fallo ya
    ocurrio, para no anadir una verificacion de sesion a cada llamada que
    funciona.
    """
    if session is None or exc.code not in _LIVE_NO_DISPONIBLE:
        return exc
    if not _pbip_escribible(session):
        return exc
    detalles = dict(exc.details or {})
    detalles.update({"suggested_mode": PBIP, "requested_mode": LIVE,
                     "reason": "desktop_cerrado_con_pbip_activo"})
    return type(exc)(
        f"{exc.message} Pero hay un proyecto .pbip activo y Desktop no lo tiene "
        "abierto, que es justo el estado que 'pbip' necesita: reintenta con "
        "mode='pbip' (o mode='auto', que mira el estado y elige).",
        details=detalles)


def relanzar_con_pista(exc: PowerBIMCPError, session: Any) -> NoReturn:
    """Relanza el fallo de `live`, con la pista de `pbip` si el estado la avala."""
    enriquecido = _con_pista_de_pbip(exc, session)
    if enriquecido is exc:
        raise exc
    raise enriquecido from exc


def assert_mode_is_safely_executable(mode: Optional[str],
                                    session: Any = None) -> str:
    """Normaliza el modo, resuelve `auto` y rechaza `both` SIN efecto alguno.

    Debe llamarse lo primero de cada tool dual: antes de conectar a TOM, de
    validar objetos contra el motor, de crear un journal, de leer para
    planificar una escritura o de modificar archivos.

    `auto` se resuelve aqui, en el mismo sitio y con el mismo orden: mirar el
    estado antes de producir ningun efecto es justo lo que hace que esta
    funcion sirva.
    """
    m = normalize_mode(mode)
    if m == AUTO:
        if session is None:
            raise ValidationError(
                "mode='auto' necesita la sesion para resolverse.",
                details={"requested_mode": AUTO})
        m = resolver_auto(session)
    if m == BOTH:
        raise DualModeNotAvailableError(
            _MENSAJE,
            details={
                "requested_mode": BOTH,
                "available_modes": [LIVE, PBIP],
                "policy": "strict",
                "live_requires": "Power BI Desktop abierto",
                "pbip_requires": "Power BI Desktop cerrado",
                "reason": "requisitos mutuamente incompatibles",
                "phase": "Se reevaluara en la Fase 1B.",
            },
        )
    return m


def run_dual(mode: str, live_call: Callable[[], object],
             pbip_call: Callable[[], object],
             session: Any = None) -> Dict[str, object]:
    """Despacha al destino correspondiente segun el modo.

    Centraliza lo que antes estaba duplicado en `measure_tools` y en
    `model_edit_tools`. Con `both` bloqueado en la precondicion, aqui solo
    llegan `live` y `pbip`, y el error se propaga tal cual: ya no se convierte
    en un `consistent: False` con la mitad del trabajo hecho.

    Con `session`, un fallo de `live` que en realidad significa "Desktop no
    esta abierto" sale diciendo que `pbip` era viable. El error conserva su
    codigo: lo unico que cambia es que ahora explica una salida que existe.
    """
    out: Dict[str, object] = {"mode": mode}
    if mode == LIVE:
        try:
            out[LIVE] = live_call()
        except PowerBIMCPError as exc:
            relanzar_con_pista(exc, session)
    elif mode == PBIP:
        out[PBIP] = pbip_call()
    else:  # pragma: no cover - lo impide assert_mode_is_safely_executable
        raise DualModeNotAvailableError(_MENSAJE, details={"requested_mode": mode})
    return out
