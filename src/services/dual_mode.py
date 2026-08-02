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

from typing import Callable, Dict, Optional

from powerbi.errors import PowerBIMCPError, ValidationError

LIVE = "live"
PBIP = "pbip"
BOTH = "both"
VALID_MODES = (LIVE, PBIP, BOTH)


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
        raise ValidationError(f"mode invalido: '{mode}'. Usa live|pbip|both.")
    return m


def assert_mode_is_safely_executable(mode: Optional[str]) -> str:
    """Normaliza el modo y rechaza `both` SIN producir efecto alguno.

    Debe llamarse lo primero de cada tool dual: antes de conectar a TOM, de
    validar objetos contra el motor, de crear un journal, de leer para
    planificar una escritura o de modificar archivos.
    """
    m = normalize_mode(mode)
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
             pbip_call: Callable[[], object]) -> Dict[str, object]:
    """Despacha al destino correspondiente segun el modo.

    Centraliza lo que antes estaba duplicado en `measure_tools` y en
    `model_edit_tools`. Con `both` bloqueado en la precondicion, aqui solo
    llegan `live` y `pbip`, y el error se propaga tal cual: ya no se convierte
    en un `consistent: False` con la mitad del trabajo hecho.
    """
    out: Dict[str, object] = {"mode": mode}
    if mode == LIVE:
        out[LIVE] = live_call()
    elif mode == PBIP:
        out[PBIP] = pbip_call()
    else:  # pragma: no cover - lo impide assert_mode_is_safely_executable
        raise DualModeNotAvailableError(_MENSAJE, details={"requested_mode": mode})
    return out


#: Nota corta para las descripciones de las tools duales.
MODE_NOTE = ("mode: 'live' (Desktop abierto) o 'pbip' (Desktop cerrado). "
             "'both' esta temporalmente deshabilitado bajo la politica estricta: "
             "los dos destinos exigen estados de Desktop incompatibles.")
