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

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import paths as safe_paths

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

    try:
        for proc in psutil.process_iter(["name", "pid"]):
            try:
                name = (proc.info.get("name") or "").lower()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if name.startswith(_DESKTOP_PREFIXES):
                desktop_procs.append(proc)
            elif name in _ENGINE_NAMES:
                engine_procs.append(proc)
    except Exception as exc:                  # noqa: BLE001
        # Si el escaneo entero se cae, no hay ninguna base para decir
        # "cerrado". Antes la excepcion salia cruda como `unexpected`.
        return ProjectOpenState(
            UNKNOWN, "medium",
            f"No se pudo enumerar los procesos ({type(exc).__name__}: {exc}). "
            "Sin ese listado no se puede descartar que Power BI Desktop tenga "
            "este proyecto abierto.",
            [{"signal": "process_scan", "result": "error", "error": str(exc)}])

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
    #: Procesos de los que NO se logro averiguar que tienen cargado. Un .pbip
    #: abierto se ve exactamente asi: sin descriptores sobre su carpeta y sin
    #: la ruta en la linea de comandos.
    sin_identificar: List[int] = []
    for proc in desktop_procs:
        pid = proc.pid
        identificado = False

        # Senal 1: linea de comandos (a menudo trae el archivo abierto).
        try:
            argumentos = list(proc.cmdline() or [])
            for arg in argumentos:
                if _references_project(arg, roots, active.pbip_path):
                    signals.append({"signal": "cmdline", "pid": pid, "match": arg})
                    return ProjectOpenState(
                        OPEN, "high",
                        f"El proceso de Power BI Desktop {pid} tiene el proyecto en "
                        "su linea de comandos.", signals)
            # Mas alla del ejecutable hay algo que mirar: se sabe que abrio.
            identificado = identificado or len(argumentos) > 1
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied += 1
            signals.append({"signal": "cmdline", "pid": pid, "result": "denied"})

        # Senal 2: archivos abiertos (requiere permisos; a veces se deniega).
        try:
            handles = list(proc.open_files() or [])
            for handle in handles:
                if _references_project(handle.path, roots, active.pbip_path):
                    signals.append({"signal": "open_files", "pid": pid,
                                    "match": handle.path})
                    return ProjectOpenState(
                        OPEN, "high",
                        f"El proceso de Power BI Desktop {pid} mantiene abierto un "
                        "archivo del proyecto.", signals)
            signals.append({"signal": "open_files", "pid": pid, "result": "no_match"})
            identificado = identificado or bool(handles)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            denied += 1
            signals.append({"signal": "open_files", "pid": pid, "result": "denied"})

        if not identificado:
            sin_identificar.append(pid)

    # Una inspeccion denegada ya deja el estado indeterminado: no hace falta
    # mirar ventanas para saber que no se puede afirmar "cerrado".
    if denied:
        return ProjectOpenState(
            UNKNOWN, "medium",
            f"Hay {len(desktop_procs)} proceso(s) de Power BI Desktop, pero el "
            f"sistema denego {denied} consulta(s) de inspeccion. No se puede "
            "descartar que tengan este proyecto abierto.", signals)

    # --- Senal 3: titulo de ventana ---
    # Solo para los procesos que las dos senales anteriores no supieron
    # explicar. Es la unica correlacion que existe para un .pbip, y sin ella
    # este detector declaraba CERRADO un proyecto ABIERTO (CORE-001).
    if sin_identificar:
        from horizun_pbi_mcp.powerbi import desktop_launcher

        stem = Path(active.pbip_path).stem
        ventanas = desktop_launcher.coincidencias_por_titulo(
            stem, sin_identificar)

        if len(ventanas.pids) == 1:
            pid = ventanas.pids[0]
            signals.append({"signal": "window_title", "pid": pid, "match": stem})
            return ProjectOpenState(
                # "medium": el titulo es el NOMBRE del informe. Basta para no
                # escribir, no para jurar que es esta carpeta y no otra con un
                # proyecto que se llama igual.
                OPEN, "medium",
                f"La ventana del proceso {pid} de Power BI Desktop se titula "
                f"'{stem}', como este proyecto.", signals)

        if len(ventanas.pids) > 1:
            signals.append({"signal": "window_title",
                            "pids": list(ventanas.pids), "result": "ambiguous"})
            return ProjectOpenState(
                UNKNOWN, "medium",
                f"Hay {len(ventanas.pids)} ventanas de Power BI Desktop tituladas "
                f"'{stem}'. El titulo no distingue cual corresponde a esta "
                "carpeta, y una de ellas puede ser este proyecto.", signals)

        if ventanas.error:
            signals.append({"signal": "window_title", "result": "error",
                            "detail": ventanas.error})
            return ProjectOpenState(
                UNKNOWN, "medium",
                f"No se pudieron enumerar las ventanas de Power BI Desktop "
                f"({ventanas.error}). Es la unica senal que delata un .pbip "
                "abierto, asi que sin ella no se puede afirmar que este cerrado.",
                signals)

        if ventanas.sin_titulos:
            signals.append({"signal": "window_title",
                            "pids": list(ventanas.sin_titulos),
                            "result": "no_title"})
            return ProjectOpenState(
                UNKNOWN, "medium",
                f"Hay {len(ventanas.sin_titulos)} proceso(s) de Power BI Desktop "
                "cuyo contenido no se pudo determinar: sin descriptores del "
                "proyecto, sin ruta en la linea de comandos y sin titulo de "
                "ventana legible. Un .pbip abierto se ve exactamente asi.",
                signals)

    return ProjectOpenState(
        CLOSED, "medium",
        f"Hay {len(desktop_procs)} proceso(s) de Power BI Desktop, se pudo "
        "determinar que tiene cargado cada uno y ninguno es este proyecto.",
        signals)


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
