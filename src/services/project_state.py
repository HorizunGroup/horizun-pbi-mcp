"""Deteccion de si Power BI Desktop puede tener abierto un proyecto .pbip.

LIMITE HONESTO, PRIMERO: esto NO impide que Power BI Desktop sobrescriba el
informe mas tarde. Desktop mantiene su propia copia en memoria y al guardar
escribe encima. Lo unico que se consigue aqui es no escribir NOSOTROS cuando
hay indicios de que el proyecto esta abierto.

Todas las senales son de SOLO LECTURA. No se renombra, no se escribe un
temporal, no se intenta un `os.replace` de prueba ni ninguna otra mutacion
sobre archivos reales del proyecto para averiguar si estan bloqueados. La
comprobacion previa al reemplazo pertenece a la transaccion de escritura
(`services.txn`), no al detector de estado.

Politica de la Fase 1A (estricta y no desactivable):

    closed  verificado  -> escritura permitida
    open                -> escritura bloqueada
    unknown             -> escritura bloqueada

No hay variable de entorno ni parametro que lo relaje. El modo `warn` y la
confirmacion por llamada se disenaran en la Fase 1B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from services import paths as safe_paths

log = get_logger("project_state")

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"

#: Nombres de proceso de Power BI Desktop (escritorio y version de la Store).
_DESKTOP_PREFIXES = ("pbidesktop",)
#: Motor tabular local que Desktop levanta.
_ENGINE_NAMES = ("msmdsrv.exe",)


class ProjectOpenInDesktopError(PowerBIMCPError):
    """Se bloqueo una escritura PBIR porque el proyecto puede estar abierto.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de la
    Fase 1A. Hereda de `PowerBIMCPError`, asi que `guard()` la serializa igual.
    """

    code = "project_open_in_desktop"


@dataclass
class ProjectOpenState:
    state: str
    confidence: str                       # "high" | "medium"
    reason: str
    signals: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def writable(self) -> bool:
        return self.state == CLOSED

    def to_dict(self) -> Dict[str, Any]:
        return {"state": self.state, "confidence": self.confidence,
                "reason": self.reason, "signals": self.signals}


def _project_roots(active: ActivePbip) -> List[Path]:
    roots = [Path(active.project_dir)]
    for d in (active.report_dir, active.semantic_model_dir):
        if d:
            roots.append(Path(d))
    return roots


def _references_project(candidate: str, roots: List[Path], pbip_path: str) -> bool:
    if not candidate:
        return False
    try:
        cand = Path(candidate)
    except (OSError, ValueError):
        return False
    try:
        if cand.resolve() == Path(pbip_path).resolve():
            return True
    except OSError:
        pass
    for root in roots:
        if safe_paths.is_inside(root, cand):
            return True
    return False


#: Vida de la cache del escaneo de procesos, en segundos.
#:
#: Enumerar procesos cuesta ~150 ms. Sin cache, una operacion que escribe cinco
#: visuales pagaria cinco escaneos. La ventana es deliberadamente minima: para
#: colarse haria falta que Power BI Desktop abriera el proyecto DENTRO de ese
#: segundo, y aun asi la transaccion vuelve a comprobar el fingerprint de cada
#: archivo antes y despues de escribir.
_CACHE_TTL_SECONDS = 1.0
_cache: Dict[str, Any] = {}


def _cache_key(active: ActivePbip) -> str:
    return str(Path(active.project_dir).resolve())


def invalidate_cache() -> None:
    """Descarta el estado cacheado (usado por las pruebas)."""
    _cache.clear()


def detect(active: ActivePbip, *, use_cache: bool = True) -> ProjectOpenState:
    """Determina el estado del proyecto usando solo senales de lectura."""
    import time

    key = _cache_key(active)
    if use_cache:
        hit = _cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1]

    state = _detect_uncached(active)
    _cache[key] = (time.monotonic(), state)
    return state


def _detect_uncached(active: ActivePbip) -> ProjectOpenState:
    try:
        import psutil
    except ImportError:                       # pragma: no cover
        return ProjectOpenState(
            UNKNOWN, "medium",
            "psutil no esta disponible: no se puede inspeccionar los procesos.")

    roots = _project_roots(active)
    signals: List[Dict[str, Any]] = []
    desktop_procs, engine_procs = [], []

    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name.startswith(_DESKTOP_PREFIXES):
            desktop_procs.append(proc)
        elif name in _ENGINE_NAMES:
            engine_procs.append(proc)

    # --- Sin Desktop ni motor: cerrado con alta confianza ---
    if not desktop_procs and not engine_procs:
        return ProjectOpenState(
            CLOSED, "high",
            "No hay ningun proceso de Power BI Desktop ni del motor tabular.",
            [{"signal": "process_scan", "desktop": 0, "engine": 0}])

    # --- Motor sin Desktop: hay algo vivo que no podemos atribuir ---
    if not desktop_procs and engine_procs:
        return ProjectOpenState(
            UNKNOWN, "medium",
            f"Hay {len(engine_procs)} proceso(s) del motor tabular (msmdsrv) pero "
            "ningun Power BI Desktop identificable: no se puede saber que "
            "proyecto tienen cargado.",
            [{"signal": "engine_without_desktop", "engine": len(engine_procs)}])

    # --- Desktop presente: buscar referencias al proyecto ---
    denied = 0
    for proc in desktop_procs:
        pid = proc.pid

        # Senal 1: linea de comandos (a menudo trae el archivo abierto).
        try:
            for arg in (proc.cmdline() or []):
                if _references_project(arg, roots, active.pbip_path):
                    signals.append({"signal": "cmdline", "pid": pid, "match": arg})
                    return ProjectOpenState(
                        OPEN, "high",
                        f"El proceso de Power BI Desktop {pid} tiene el proyecto en "
                        "su linea de comandos.", signals)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied += 1
            signals.append({"signal": "cmdline", "pid": pid, "result": "denied"})

        # Senal 2: archivos abiertos (requiere permisos; a veces se deniega).
        try:
            for handle in (proc.open_files() or []):
                if _references_project(handle.path, roots, active.pbip_path):
                    signals.append({"signal": "open_files", "pid": pid,
                                    "match": handle.path})
                    return ProjectOpenState(
                        OPEN, "high",
                        f"El proceso de Power BI Desktop {pid} mantiene abierto un "
                        "archivo del proyecto.", signals)
            signals.append({"signal": "open_files", "pid": pid, "result": "no_match"})
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied += 1
            signals.append({"signal": "open_files", "pid": pid, "result": "denied"})

    if denied:
        return ProjectOpenState(
            UNKNOWN, "medium",
            f"Hay {len(desktop_procs)} proceso(s) de Power BI Desktop, pero el "
            f"sistema denego {denied} consulta(s) de inspeccion. No se puede "
            "descartar que tengan este proyecto abierto.", signals)

    return ProjectOpenState(
        CLOSED, "medium",
        f"Hay {len(desktop_procs)} proceso(s) de Power BI Desktop y se pudo "
        "inspeccionar todos: ninguno referencia este proyecto.", signals)


def assert_writable(active: ActivePbip, *, operation: str = "escritura PBIR") -> ProjectOpenState:
    """Aplica la politica estricta. Lanza si el estado no es `closed`.

    No admite override: en la Fase 1A la proteccion no se puede desactivar.
    """
    state = detect(active)
    if state.writable:
        log.info("Estado del proyecto: %s (%s) — %s se permite",
                 state.state, state.confidence, operation)
        return state

    if state.state == OPEN:
        mensaje = (
            f"{operation} bloqueada: Power BI Desktop tiene abierto este proyecto. "
            "Si escribieramos ahora, al guardar en Desktop (Ctrl+S) se "
            "sobrescribirian los cambios en disco. Cierra el informe y repite.")
    else:
        mensaje = (
            f"{operation} bloqueada: no se pudo verificar que el proyecto NO este "
            "abierto en Power BI Desktop. La politica de esta fase bloquea tambien "
            "el estado indeterminado, para no arriesgar una perdida silenciosa. "
            "Cierra Power BI Desktop por completo y repite.")

    raise ProjectOpenInDesktopError(
        mensaje,
        details={"state": state.state, "confidence": state.confidence,
                 "reason": state.reason, "signals": state.signals,
                 "policy": "strict", "note": (
                     "Este bloqueo evita que escriba el MCP; NO impide que Power BI "
                     "Desktop sobrescriba el archivo despues por su cuenta.")},
    )
