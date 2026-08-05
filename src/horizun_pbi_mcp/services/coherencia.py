"""¿El modelo VIVO y el proyecto EN DISCO son el mismo archivo?

El servidor mantiene dos estados a la vez —`active_model` (lo que Power BI
Desktop tiene en memoria) y `active_pbip` (la carpeta en disco)— y hasta ahora
ninguna validacion cruzaba los dos. Pueden apuntar a archivos distintos, de
proyectos distintos, de CLIENTES distintos, y todo responde con normalidad
porque cada mitad es valida por separado.

El caso real que lo motiva: `pbi_select_model` servia el modelo de un `.pbix`
mientras `pbi_start_here` reportaba otro `.pbip` como proyecto activo.
`pbi_report_capabilities` devolvia los visuales del segundo. Se estuvo a punto
de escribir cuatro paginas dentro del informe equivocado, y solo se detecto
porque el conteo de visuales no cuadraba. Sin esa casualidad, el trabajo de
otro proyecto se corrompe en silencio.

La senal es precisa y ya existia a medias: `ActiveModel` guarda el `pid` del
Power BI Desktop que sirve el modelo, y `project_state` sabe correlacionar un
`.pbip` con los archivos que un proceso tiene abiertos. Cruzarlos responde la
pregunta exacta: **ese** Desktop, ¿tiene abierto **este** proyecto?

Tres veredictos, y la diferencia entre el segundo y el tercero es la que evita
que esto estorbe:

- `same`        el proceso que sirve el modelo tiene abierto este proyecto.
- `different`   tiene abierto OTRO archivo. Es el caso peligroso y se bloquea.
- `unknown`     no se pudo comprobar (permisos, proceso que ya no esta). Se
                AVISA, nunca se bloquea: negarse por no poder verificar
                convertiria un permiso denegado en una sesion inservible.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("coherencia")

SAME = "same"
DIFFERENT = "different"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"


class ProyectoYModeloDivergenError(PowerBIMCPError):
    """El modelo vivo y el proyecto en disco son archivos distintos."""

    code = "active_model_project_mismatch"


def _archivos_del_proceso(pid: int) -> Optional[List[str]]:
    """Rutas abiertas por ese PID, o None si no se puede saber."""
    try:
        import psutil
    except ImportError:                                  # pragma: no cover
        return None
    try:
        proc = psutil.Process(pid)
        return [h.path for h in (proc.open_files() or [])]
    except Exception:                                    # noqa: BLE001
        # NoSuchProcess, AccessDenied, OSError: en todos, "no se sabe".
        return None


def _pistas_de_otro_archivo(rutas: List[str]) -> List[str]:
    """Archivos de Power BI que ese proceso tiene abiertos."""
    interesantes = []
    for r in rutas:
        bajo = r.casefold()
        if bajo.endswith((".pbix", ".pbip")) or ".report" in bajo or \
                ".semanticmodel" in bajo:
            interesantes.append(r)
    return interesantes


def check(session) -> Dict[str, Any]:
    """Veredicto de coherencia entre el modelo activo y el proyecto activo.

    Nunca lanza: es un diagnostico. Quien quiera bloquear usa `assert_coherente`.
    """
    modelo = getattr(session, "active_model", None)
    proyecto = getattr(session, "active_pbip", None)

    if modelo is None or proyecto is None:
        return {"state": NOT_APPLICABLE,
                "reason": ("Solo hay uno de los dos estados activos: no puede "
                           "haber divergencia."),
                "has_live_model": modelo is not None,
                "has_project": proyecto is not None}

    detalle_modelo = {"port": getattr(modelo, "port", None),
                      "pid": getattr(modelo, "pid", None),
                      "catalog": getattr(modelo, "catalog", None),
                      "workspace": getattr(modelo, "workspace", None)}
    detalle_proyecto = {"pbip_path": proyecto.pbip_path,
                        "name": proyecto.report_name}

    pid = getattr(modelo, "pid", None)
    if not pid:
        return {"state": UNKNOWN,
                "reason": ("El modelo activo no tiene PID asociado, asi que no "
                           "se puede comprobar que sirva a este proyecto."),
                "model": detalle_modelo, "project": detalle_proyecto}

    rutas = _archivos_del_proceso(int(pid))
    if rutas is None:
        return {"state": UNKNOWN,
                "reason": (f"No se pudieron leer los archivos abiertos del "
                           f"proceso {pid} (permisos, o ya no existe). No se "
                           "afirma coherencia ni divergencia."),
                "model": detalle_modelo, "project": detalle_proyecto}

    from horizun_pbi_mcp.services import project_state

    raices = [Path(proyecto.project_dir)]
    for d in (proyecto.report_dir, proyecto.semantic_model_dir):
        if d:
            raices.append(Path(d))

    for ruta in rutas:
        if project_state._references_project(ruta, raices, proyecto.pbip_path):
            return {"state": SAME,
                    "reason": (f"El proceso {pid} que sirve el modelo tiene "
                               "abierto este mismo proyecto."),
                    "evidence": ruta,
                    "model": detalle_modelo, "project": detalle_proyecto}

    otros = _pistas_de_otro_archivo(rutas)
    if not otros:
        return {"state": UNKNOWN,
                "reason": (f"El proceso {pid} no muestra ningun archivo de "
                           "Power BI abierto; no hay con que comparar."),
                "model": detalle_modelo, "project": detalle_proyecto}

    return {
        "state": DIFFERENT,
        "reason": (f"El modelo activo lo sirve el proceso {pid}, que tiene "
                   f"abierto OTRO archivo, no '{Path(proyecto.pbip_path).name}'. "
                   "El modelo en vivo y el proyecto en disco son cosas "
                   "distintas: lo que consultes con DAX no describe el informe "
                   "que vas a escribir."),
        "evidence": sorted(set(otros))[:5],
        "model": detalle_modelo, "project": detalle_proyecto,
        "how_to_fix": ("Elige uno: `pbi_open_pbip_project` con el proyecto del "
                       "modelo que estas consultando, o `pbi_select_model` con "
                       "el puerto del Desktop que tiene abierto este proyecto "
                       "(`pbi_list_desktop_models` los enumera)."),
    }


def assert_coherente(session, operacion: str) -> Optional[Dict[str, Any]]:
    """Bloquea si esta CONFIRMADO que son archivos distintos.

    Se niega igual que se niega escribir con Desktop abierto: el precedente ya
    existe y funciona. Pero solo ante `different` — con `unknown` se deja pasar
    y se avisa, porque no poder verificar no es lo mismo que estar mal, y
    convertir un permiso denegado en un bloqueo dejaria el servidor inutil en
    cualquier maquina con politicas estrictas.
    """
    veredicto = check(session)
    if veredicto["state"] != DIFFERENT:
        return veredicto
    raise ProyectoYModeloDivergenError(
        f"{operacion} se detuvo: {veredicto['reason']} {veredicto['how_to_fix']}",
        details=veredicto)
