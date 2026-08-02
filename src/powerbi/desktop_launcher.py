"""Apertura controlada de un .pbix en Power BI Desktop.

Para exportar el modelo hace falta que el motor local lo este sirviendo, y eso
solo pasa con el informe abierto en Desktop. Este modulo se encarga de dejarlo
en ese estado y de identificar CUAL de las instancias del motor corresponde al
archivo que pedimos:

- Si el .pbix ya esta abierto, se reutiliza esa sesion y no se toca nada.
- Si no, se lanza Desktop y se espera a que aparezca una instancia NUEVA. La
  comparacion contra la foto previa es lo que hace fiable la identificacion:
  el puerto es dinamico y el espacio de trabajo del motor no menciona el .pbix.

Solo se cierra lo que abrimos nosotros; una sesion que ya estaba abierta es del
usuario y se deja como estaba.
"""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError, ValidationError
from powerbi import desktop_discovery
from utils.validation import MAX_TIMEOUT_PERMITIDO, validate_limit

log = get_logger("desktop_launcher")

#: Rutas habituales de instalacion. `PBI_DESKTOP_EXE` las sobreescribe.
_RUTAS_CONOCIDAS = (
    r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
    r"C:\Program Files (x86)\Microsoft Power BI Desktop\bin\PBIDesktop.exe",
)
_INTERVALO_SONDEO = 2.0
#: Margen tras ver el catalogo, por si el motor sigue asentando el modelo.
_ESPERA_ESTABILIZACION = 1.5
# Margen para correlacionar un puerto nuevo con el archivo/proceso que se acaba
# de abrir. Evita escoger la apertura concurrente de otro usuario/hilo.
_ESPERA_CORRELACION = 4.0


class DesktopNotFoundError(PowerBIMCPError):
    code = "desktop_not_found"


class DesktopTimeoutError(PowerBIMCPError):
    code = "desktop_timeout"


@dataclass
class OpenedPbix:
    """Sesion de Desktop que sirve un .pbix concreto."""

    pbix_path: str
    instance: Dict[str, Any]
    desktop_pid: Optional[int]
    #: True solo si el proceso lo arrancamos nosotros (y por tanto podemos cerrarlo).
    launched_by_us: bool
    waited_seconds: float
    #: Hora de creacion del proceso Desktop; evita matar un PID reciclado.
    desktop_started: Optional[float] = None


def find_executable() -> Path:
    """Ruta de PBIDesktop.exe. Busca en el entorno, rutas fijas y la Store."""
    desde_entorno = os.environ.get("PBI_DESKTOP_EXE")
    if desde_entorno:
        ruta = Path(desde_entorno)
        if ruta.exists():
            return ruta
        raise DesktopNotFoundError(
            f"PBI_DESKTOP_EXE apunta a una ruta que no existe: {ruta}")

    for candidata in _RUTAS_CONOCIDAS:
        ruta = Path(candidata)
        if ruta.exists():
            return ruta

    # Version de la Microsoft Store: la carpeta lleva la version en el nombre.
    windows_apps = Path(r"C:\Program Files\WindowsApps")
    if windows_apps.exists():
        try:
            tiendas = sorted(windows_apps.glob("Microsoft.MicrosoftPowerBIDesktop*/bin/PBIDesktop.exe"))
        except OSError:  # la carpeta suele estar restringida
            tiendas = []
        if tiendas:
            return tiendas[-1]

    raise DesktopNotFoundError(
        "No se encontro Power BI Desktop. Instalalo o define la variable de "
        "entorno PBI_DESKTOP_EXE con la ruta de PBIDesktop.exe.",
        details={"searched": list(_RUTAS_CONOCIDAS)},
    )


def _procesos_desktop() -> List[Any]:
    import psutil

    salida = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == "pbidesktop.exe":
                salida.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return salida


def _normalized_open_path(value: str | Path) -> str:
    """Normaliza rutas de handles Windows, incluido el prefijo extendido."""
    text = str(value)
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(text))


def proceso_con_archivo_abierto(pbix: Path) -> Optional[int]:
    """PID del Desktop que tiene ese .pbix abierto, si se puede averiguar.

    Desktop mantiene el archivo abierto mientras el informe esta cargado, asi
    que la lista de descriptores lo delata. En Windows `open_files()` puede
    fallar por permisos; en ese caso devolvemos None y el llamador decide.
    """
    import psutil

    objetivo = _normalized_open_path(pbix.resolve())
    for proc in _procesos_desktop():
        try:
            for archivo in proc.open_files():
                if _normalized_open_path(archivo.path) == objetivo:
                    return proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return None


def _es_descendiente(pid: int, ancestro: int) -> bool:
    import psutil

    try:
        proc = psutil.Process(pid)
        for padre in proc.parents():
            if padre.pid == ancestro:
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return False
    return False


def _instancias_utiles() -> List[Dict[str, Any]]:
    """Instancias que YA sirven un modelo CON contenido.

    Power BI Desktop arranca el motor al abrirse y crea la base antes de
    poblarla: hay una ventana de varios segundos en la que el puerto responde y
    el catalogo existe, pero el modelo esta vacio. Serializar ahi produciria un
    TMDL sin tablas sin que nada fallara, asi que la condicion de "listo" es
    que el modelo tenga ya al menos una tabla.
    """
    return [i for i in desktop_discovery.discover_instances()
            if i.get("status") == "ok" and i.get("catalog")
            and (i.get("table_count") or 0) > 0]


def _instancia_de_proceso(desktop_pid: int) -> Optional[Dict[str, Any]]:
    for instancia in _instancias_utiles():
        pid = instancia.get("pid")
        if pid and _es_descendiente(int(pid), desktop_pid):
            return instancia
    return None


def _process_started(pid: Optional[int]) -> Optional[float]:
    if not pid:
        return None
    import psutil

    try:
        return float(psutil.Process(int(pid)).create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return None


def open_pbix(pbix_path: str | Path, timeout: int = 300,
              reuse_open: bool = True) -> OpenedPbix:
    """Deja el .pbix servido por un motor local y devuelve esa instancia."""
    timeout = validate_limit(timeout, "timeout", MAX_TIMEOUT_PERMITIDO)
    assert timeout is not None  # el parametro no es opcional
    pbix = Path(pbix_path).expanduser().resolve()
    if not pbix.is_file():
        raise DesktopNotFoundError(f"El archivo .pbix no existe: {pbix}")
    if pbix.suffix.casefold() not in {".pbix", ".pbip"}:
        raise ValidationError(
            "Power BI Desktop solo puede abrir aqui archivos .pbix o .pbip.",
            details={"path": str(pbix), "extension": pbix.suffix},
        )

    if reuse_open:
        pid_existente = proceso_con_archivo_abierto(pbix)
        if pid_existente:
            instancia = _instancia_de_proceso(pid_existente)
            if instancia:
                log.info("El .pbix ya estaba abierto en Desktop (pid %s, puerto %s)",
                         pid_existente, instancia["port"])
                return OpenedPbix(
                    str(pbix), instancia, pid_existente, False, 0.0,
                    desktop_started=_process_started(pid_existente))
            log.info("Desktop tiene el archivo abierto (pid %s) pero aun no hay "
                     "modelo servido; se espera.", pid_existente)
            instancia = _esperar_instancia_de(pid_existente, timeout)
            return OpenedPbix(
                str(pbix), instancia, pid_existente, False, 0.0,
                desktop_started=_process_started(pid_existente))

    ejecutable = find_executable()
    # La foto previa incluye TODOS los puertos, no solo los que ya sirven un
    # modelo: una sesion abierta que aun esta cargando estrenara catalogo en
    # mitad de nuestra espera y no debe confundirse con la nuestra.
    previas = {i["port"] for i in desktop_discovery.discover_instances()}
    log.info("Abriendo %s en Power BI Desktop (%s instancias previas)",
             pbix.name, len(previas))

    try:
        proceso = subprocess.Popen(
            [str(ejecutable), str(pbix)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        )
    except OSError as exc:
        raise DesktopNotFoundError(
            f"No se pudo ejecutar Power BI Desktop: {exc}",
            details={"executable": str(ejecutable)},
        ) from exc

    inicio = time.monotonic()
    try:
        instancia = _esperar_instancia_nueva(
            previas, timeout, pbix.name, pbix_path=pbix,
            launched_pid=proceso.pid)
    except BaseException:
        # Popen ya produjo un efecto externo. Si la espera falla o se
        # interrumpe, no se deja un Desktop lanzado por nosotros sin dueño.
        provisional = OpenedPbix(
            str(pbix), {}, proceso.pid, True,
            round(time.monotonic() - inicio, 1),
            desktop_started=_process_started(proceso.pid))
        try:
            close(provisional)
        except Exception as exc:                       # noqa: BLE001
            log.warning("No se pudo compensar la apertura fallida de %s: %s",
                        pbix, exc)
        raise
    esperado = time.monotonic() - inicio

    # El proceso lanzado suele reexec-ar: el que sirve el modelo es el ancestro
    # real de msmdsrv, no necesariamente el pid que nos devolvio Popen.
    desktop_pid = _desktop_de_instancia(instancia) or proceso.pid
    return OpenedPbix(
        str(pbix), instancia, desktop_pid, True, round(esperado, 1),
        desktop_started=_process_started(desktop_pid))


def _estabilizar(instancia: Dict[str, Any]) -> Dict[str, Any]:
    """Relee la instancia hasta que el conteo de tablas deje de crecer.

    El modelo se puebla por partes: dos lecturas seguidas con el mismo numero
    de tablas es la señal de que termino de cargar.
    """
    anterior = instancia
    for _ in range(60):
        time.sleep(_ESPERA_ESTABILIZACION)
        actual = next((i for i in _instancias_utiles()
                       if i["port"] == anterior["port"]), None)
        if actual is None:
            raise DesktopTimeoutError(
                f"El motor del puerto {anterior['port']} desaparecio mientras "
                "Power BI Desktop terminaba de cargar el modelo.",
                details={"port": anterior["port"],
                         "phase": "stabilization"},
            )
        expected_pid = anterior.get("pid")
        actual_pid = actual.get("pid")
        expected_started = anterior.get("create_time")
        actual_started = actual.get("create_time")
        if ((expected_pid is not None and actual_pid is not None and
             int(expected_pid) != int(actual_pid)) or
                (expected_started is not None and actual_started is not None and
                 abs(float(expected_started) - float(actual_started)) > 1.0)):
            raise DesktopTimeoutError(
                f"El puerto {anterior['port']} cambio de proceso mientras "
                "Power BI Desktop terminaba de cargar el modelo.",
                details={"port": anterior["port"],
                         "phase": "stabilization_identity",
                         "expected_pid": expected_pid,
                         "actual_pid": actual_pid},
            )
        if actual.get("table_count") == anterior.get("table_count"):
            return actual
        anterior = actual
    return anterior


def _esperar_instancia_nueva(previas: set, timeout: int,
                             nombre: str, *,
                             pbix_path: Optional[Path] = None,
                             launched_pid: Optional[int] = None) -> Dict[str, Any]:
    limite = time.monotonic() + timeout
    candidate = None
    candidate_since = None
    while time.monotonic() < limite:
        time.sleep(_INTERVALO_SONDEO)

        # Correlacion fuerte 1: Desktop mantiene abierto el archivo exacto.
        if pbix_path is not None:
            exact_pid = proceso_con_archivo_abierto(pbix_path)
            if exact_pid:
                exact = _instancia_de_proceso(exact_pid)
                if exact:
                    return _estabilizar(exact)

        nuevas = [i for i in _instancias_utiles() if i["port"] not in previas]

        # Correlacion fuerte 2: el motor desciende del proceso que lanzamos.
        if launched_pid is not None:
            exact = next((i for i in nuevas if i.get("pid") and
                          _es_descendiente(int(i["pid"]), launched_pid)), None)
            if exact:
                return _estabilizar(exact)

        # Si Windows impide leer handles/arbol, se conserva el fallback solo
        # cuando hay UN candidato estable. Con varios no se elige al azar.
        if len(nuevas) == 1:
            current = nuevas[0]
            if candidate is None or candidate.get("port") != current.get("port"):
                candidate = current
                candidate_since = time.monotonic()
            elif (candidate_since is not None and
                  time.monotonic() - candidate_since >= _ESPERA_CORRELACION):
                instancia = _estabilizar(current)
                log.info("Motor listo para %s en el puerto %s (%s tablas)",
                         nombre, instancia["port"], instancia.get("table_count"))
                return instancia
        else:
            candidate = None
            candidate_since = None
    raise DesktopTimeoutError(
        f"Power BI Desktop no llego a servir el modelo de '{nombre}' con datos "
        f"en {timeout} s. Revisa la ventana de Desktop: puede estar pidiendo "
        "credenciales del origen de datos o mostrando un aviso. Si el informe "
        "es grande, sube 'desktop_timeout'.",
        details={"timeout": timeout, "pbix": nombre},
    )


def _esperar_instancia_de(desktop_pid: int, timeout: int) -> Dict[str, Any]:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        instancia = _instancia_de_proceso(desktop_pid)
        if instancia:
            return _estabilizar(instancia)
        time.sleep(_INTERVALO_SONDEO)
    raise DesktopTimeoutError(
        f"El proceso {desktop_pid} de Power BI Desktop tiene el archivo abierto "
        f"pero no sirvio ningun modelo en {timeout} s.",
        details={"pid": desktop_pid, "timeout": timeout},
    )


def _desktop_de_instancia(instancia: Dict[str, Any]) -> Optional[int]:
    import psutil

    pid = instancia.get("pid")
    if not pid:
        return None
    try:
        for padre in psutil.Process(int(pid)).parents():
            if padre.name().lower() == "pbidesktop.exe":
                return padre.pid
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    return None


def close(opened: OpenedPbix, force: bool = False) -> Dict[str, Any]:
    """Cierra la sesion de Desktop, solo si la abrimos nosotros.

    Se termina el proceso en vez de pedirle que cierre: la conversion no
    escribe nada en el modelo, asi que no hay cambios que guardar, y un cierre
    normal se quedaria esperando en un dialogo sin nadie que lo conteste.
    """
    if not opened.launched_by_us and not force:
        return {"closed": False, "reason": "la sesion ya estaba abierta; no se toca"}
    if not opened.desktop_pid:
        return {"closed": False, "reason": "no se identifico el proceso de Desktop"}

    import psutil

    try:
        proceso = psutil.Process(opened.desktop_pid)
    except psutil.NoSuchProcess:
        return {"closed": True, "reason": "el proceso ya no existia"}

    # El PID puede reciclarse entre apertura y cierre. Nunca terminar un
    # proceso distinto solo porque Windows le asigno el mismo numero.
    try:
        if opened.desktop_started is None:
            return {"closed": False,
                    "reason": "desktop_identity_unverifiable",
                    "pid": opened.desktop_pid}
        if proceso.name().casefold() != "pbidesktop.exe":
            return {"closed": False, "reason": "desktop_pid_reused",
                    "pid": opened.desktop_pid}
        if (opened.desktop_started is not None and
                abs(float(proceso.create_time()) -
                    float(opened.desktop_started)) > 1.0):
            return {"closed": False, "reason": "desktop_pid_reused",
                    "pid": opened.desktop_pid}
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return {"closed": False, "reason": "desktop_identity_unverifiable",
                "pid": opened.desktop_pid}

    hijos = []
    try:
        hijos = proceso.children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    for objetivo in [*hijos, proceso]:
        try:
            objetivo.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, vivos = psutil.wait_procs([*hijos, proceso], timeout=15)
    kill_requested = 0
    for objetivo in vivos:
        try:
            objetivo.kill()
            kill_requested += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, supervivientes = psutil.wait_procs(vivos, timeout=5)

    log.info("Cerrada la sesion de Desktop (pid %s)", opened.desktop_pid)
    return {"closed": not supervivientes, "pid": opened.desktop_pid,
            "killed": kill_requested, "children": len(hijos),
            "survivors": [p.pid for p in supervivientes]}
