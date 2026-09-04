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
from typing import Any, Dict, List, Optional, Sequence, Tuple

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.powerbi import desktop_discovery
from horizun_pbi_mcp.utils.validation import MAX_TIMEOUT_PERMITIDO, validate_limit

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


class DesktopPreflightError(PowerBIMCPError):
    """El proyecto PBIP no puede abrirse por errores TMDL conocidos."""

    code = "desktop_preflight_failed"


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


def _preflight_pbip_model(pbip: Path) -> None:
    """Valida TMDL antes de crear una ventana de Desktop.

    Power BI Desktop muestra un Frown genérico cuando encuentra colisiones que
    el parser TMDL acepta, por ejemplo una medida con el mismo nombre que una
    columna. El lint local y, si están disponibles, las DLL de TOM pueden
    explicar el error antes de lanzar Desktop. Los proyectos report-only no
    tienen modelo propio y se dejan pasar.
    """
    if pbip.suffix.casefold() != ".pbip":
        return
    from horizun_pbi_mcp.services import tmdl_validate

    try:
        definition = tmdl_validate.resolve_definition_dir(pbip)
    except tmdl_validate.ReportOnlyProjectError:
        return
    tables_dir = definition / "tables"
    table_files = sorted(tables_dir.glob("*.tmdl")) if tables_dir.is_dir() else []
    if not table_files:
        raise DesktopPreflightError(
            "El proyecto PBIP tiene un modelo semántico vacío: Power BI "
            "Desktop no llegará a servir un motor para este proyecto. "
            "Añade al menos una tabla TMDL o ábrelo como informe-only.",
            details={
                "path": str(pbip),
                "definition_dir": str(definition),
                "rule": "tmdl_empty_model",
                "findings": [{
                    "rule": "tmdl_empty_model",
                    "severity": "error",
                    "path": str(tables_dir),
                }],
                "parse_checked": False,
                "parsed": False,
            },
        )
    resultado = tmdl_validate.validate(definition, use_tom=True)
    errores = [finding for finding in resultado.get("findings", [])
               if finding.get("severity") == "error"]
    if not errores:
        return
    raise DesktopPreflightError(
        "El proyecto PBIP no se puede abrir porque su modelo TMDL tiene "
        f"{len(errores)} error(es). Corrige los hallazgos antes de volver "
        "a abrirlo.",
        details={
            "path": str(pbip),
            "definition_dir": str(definition),
            "rule": "tmdl_preflight",
            "findings": errores,
            "parse_checked": resultado.get("parse_checked"),
            "parsed": resultado.get("parsed"),
        },
    )


@dataclass(frozen=True)
class CoincidenciaPorTitulo:
    """Resultado de correlacionar procesos de Desktop con un nombre de proyecto.

    Un `Optional[int]` no podia distinguir tres respuestas que exigen
    decisiones opuestas: "ninguno coincide", "coinciden varios" y "no se pudo
    mirar". Solo la primera autoriza a afirmar algo.
    """

    #: Procesos con una ventana titulada exactamente como el proyecto.
    pids: Tuple[int, ...] = ()
    #: Procesos de los que no se pudo leer NINGUN titulo. No dicen que no.
    sin_titulos: Tuple[int, ...] = ()
    #: Por que la enumeracion quedo incompleta, si quedo incompleta.
    error: Optional[str] = None

    @property
    def inequivoca(self) -> Optional[int]:
        """El unico PID coincidente, o None si hay cero o mas de uno."""
        return self.pids[0] if len(self.pids) == 1 else None


def coincidencias_por_titulo(stem: str,
                             pids: Sequence[int]) -> CoincidenciaPorTitulo:
    """Correlaciona procesos con un proyecto por el TITULO de su ventana.

    Es la unica correlacion posible para un .pbip: Desktop NO deja ningun
    descriptor abierto sobre la carpeta del proyecto -comprobado con
    `open_files()`, que devuelve cero archivos de esa carpeta- y muchas veces
    tampoco trae la ruta en la linea de comandos, porque se abrio desde la
    lista de recientes.

    Vive aqui y la usan los DOS llamadores que la necesitan: este modulo, para
    no lanzar otra ventana del proyecto que ya esta abierto, y
    `services.project_state`, para no declarar cerrado lo que esta abierto.
    Tener dos copias seria dejarlas divergir, y divergir aqui significa
    autorizar una escritura sobre un informe abierto.

    Coincidencia EXACTA de titulo, normalizada en mayusculas/minusculas y
    espacios. No se acepta coincidencia parcial: un titulo que solo contiene el
    nombre puede ser otro informe, y aqui una equivocacion cuesta datos.
    """
    if os.name != "nt":
        return CoincidenciaPorTitulo(
            error="la correlacion por ventana solo existe en Windows")
    try:
        from horizun_pbi_mcp.powerbi.desktop_capture import _enumerate_windows
    except Exception as exc:                   # noqa: BLE001 - sin captura
        return CoincidenciaPorTitulo(
            error=f"no se pudo cargar la enumeracion de ventanas: {exc}")

    from horizun_pbi_mcp.powerbi.desktop_identity import nombre_de_documento

    objetivo = str(stem).strip().casefold()
    coincidencias: List[int] = []
    sin_titulos: List[int] = []
    fallos: List[str] = []
    for pid in pids:
        try:
            # El sufijo del producto (` - Power BI Desktop`) no es parte del
            # nombre: una ventana recien cargada lo lleva y la misma ventana
            # un rato despues no. Se compara el NOMBRE del documento.
            titulos = [nombre_de_documento(w.title)
                       for w in _enumerate_windows(pid)
                       if w.title and w.title.strip()]
        except Exception as exc:               # noqa: BLE001
            # Se sigue con los demas -un proceso ilegible no invalida al
            # resto- pero el fallo se DEVUELVE: quien decida si el proyecto
            # esta cerrado tiene que saber que la vista quedo incompleta.
            fallos.append(f"pid {pid}: {exc}")
            continue
        if not titulos:
            sin_titulos.append(pid)
        elif objetivo in titulos and pid not in coincidencias:
            coincidencias.append(pid)
    return CoincidenciaPorTitulo(
        pids=tuple(coincidencias), sin_titulos=tuple(sin_titulos),
        error="; ".join(fallos) or None)


def _documentos_de_la_linea_de_comandos(pid: int) -> List[str]:
    """Proyectos que la linea de comandos de ese proceso nombra.

    Es la unica prueba de RUTA que deja un `.pbip`: no hay descriptor abierto
    sobre la carpeta del proyecto, pero cuando la ventana se abrio con la ruta
    como argumento -que es como la abre este servidor- ahi queda escrita.
    """
    import psutil

    try:
        argumentos = psutil.Process(int(pid)).cmdline() or []
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return []
    return [str(a) for a in argumentos
            if str(a).casefold().endswith((".pbip", ".pbix", ".pbit"))]


def _sirve_otro_proyecto(pid: int, objetivo: Path) -> bool:
    """True si la linea de comandos de ese proceso nombra OTRO proyecto.

    Dos proyectos llamados `Demo.pbip` en carpetas distintas producen la misma
    ventana `Demo`, y el titulo no puede distinguirlos. Cuando la linea de
    comandos SI dice cual es, se usa: un `Demo` de otra carpeta no es este
    `Demo`, por mucho que la ventana se llame igual.
    """
    documentos = _documentos_de_la_linea_de_comandos(pid)
    if not documentos:
        return False
    from horizun_pbi_mcp.services import project_resolver

    return not any(project_resolver.misma_ruta(d, objetivo)
                   for d in documentos)


def _pid_por_titulo_de_ventana(stem: str,
                               objetivo: Optional[Path] = None) -> Optional[int]:
    """PID cuya ventana principal se llama exactamente como el proyecto.

    Se exige coincidencia exacta y una sola ventana: ante dos candidatas no se
    elige, porque reutilizar la equivocada es peor que no reutilizar. Sin
    esto, cada apertura del mismo proyecto lanzaba OTRA ventana.

    Y el titulo NO basta por si solo: si la linea de comandos del candidato
    nombra otro proyecto, se descarta. Un `Demo.pbip` de otra carpeta tiene la
    ventana titulada igual y no es el mismo archivo.
    """
    pids = [p.pid for p in _procesos_desktop()]
    if objetivo is not None:
        pids = [pid for pid in pids if not _sirve_otro_proyecto(pid, objetivo)]
    return coincidencias_por_titulo(stem, pids).inequivoca


def proceso_con_archivo_abierto(pbix: Path) -> Optional[int]:
    """PID del Desktop que tiene ese informe abierto, si se puede averiguar.

    Con un .pbix, Desktop mantiene el archivo abierto mientras el informe esta
    cargado y la lista de descriptores lo delata. Con un .pbip no hay ningun
    descriptor que mirar y se cae al titulo de la ventana. En Windows
    `open_files()` puede fallar por permisos; en ese caso devolvemos None y el
    llamador decide.
    """
    import psutil

    ruta = Path(pbix).resolve()
    objetivo = _normalized_open_path(ruta)
    for proc in _procesos_desktop():
        try:
            for archivo in proc.open_files():
                if _normalized_open_path(archivo.path) == objetivo:
                    return proc.pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    if ruta.suffix.casefold() == ".pbip":
        return _pid_por_titulo_de_ventana(ruta.stem, ruta)
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


def resolver_documento(ruta: str | Path) -> Path:
    """El archivo EXACTO que Desktop va a abrir, con el error que corresponde.

    Una carpeta de proyecto se resuelve con la misma regla que el resto del
    servidor (`project_resolver`): UN `.pbip` inequivoco vale; con varios se
    dice cuales hay y no se elige. Y el mensaje de "no existe" nombra el tipo
    real de lo que se pidio: decir "el archivo .pbix no existe" cuando se paso
    un `.pbip` -o una carpeta- mandaba a buscar el archivo equivocado.
    """
    from horizun_pbi_mcp.services import project_resolver

    pedido = Path(str(ruta)).expanduser()
    if pedido.is_dir():
        resuelto, _motivo = project_resolver.resolver_entrada(pedido)
        pedido = resuelto
    pbix = pedido.resolve()
    if not pbix.is_file():
        tipo = pbix.suffix.casefold() or "(sin extension)"
        raise DesktopNotFoundError(
            f"El archivo {tipo} no existe: {pbix}. Pasa la ruta exacta de "
            "un .pbip o .pbix, o la carpeta que contenga un unico .pbip.",
            details={"path": str(pbix), "extension": pbix.suffix,
                     "reason": "document_not_found"})
    if pbix.suffix.casefold() not in {".pbix", ".pbip"}:
        raise ValidationError(
            "Power BI Desktop solo puede abrir aqui archivos .pbix o .pbip.",
            details={"path": str(pbix), "extension": pbix.suffix},
        )
    return pbix


def open_pbix(pbix_path: str | Path, timeout: int = 300,
              reuse_open: bool = True) -> OpenedPbix:
    """Deja el .pbix servido por un motor local y devuelve esa instancia."""
    timeout = validate_limit(timeout, "timeout", MAX_TIMEOUT_PERMITIDO)
    assert timeout is not None  # el parametro no es opcional
    pbix = resolver_documento(pbix_path)

    # Se comprueba SIEMPRE, tambien cuando reuse_open=False. Antes ese modo
    # lanzaba otro PBIDesktop y la correlacion por archivo podia devolver la
    # ventana preexistente; el resultado quedaba marcado launched_by_us=True y
    # close() terminaba una sesion del usuario. No existe una forma segura de
    # forzar una segunda apertura del mismo archivo, asi que se falla cerrado.
    pid_existente = proceso_con_archivo_abierto(pbix)
    if pid_existente:
        if not reuse_open:
            raise ValidationError(
                "El archivo ya esta abierto en Power BI Desktop y no se puede "
                "forzar otra sesion sin arriesgar la ventana existente.",
                details={"path": str(pbix), "pid": pid_existente,
                         "reason": "desktop_file_already_open"},
            )
        if reuse_open:
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

    # Solo se valida el TMDL cuando realmente vamos a crear una ventana. Una
    # sesion ya abierta sirve el modelo que Desktop tiene en memoria y debe
    # poder reutilizarse aunque el estado guardado en disco sea distinto.
    # Para aperturas nuevas, el preflight evita el Frown "Sin título" de PBIP
    # antiguos con medidas que chocan con columnas.
    _preflight_pbip_model(pbix)

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


def _sirve_otro_documento(instancia: Dict[str, Any],
                          objetivo: Optional[Path]) -> bool:
    """True si esta instancia sirve DEMOSTRABLEMENTE otro archivo.

    El fallback por "apareci un puerto nuevo durante la espera" es debil por
    naturaleza: si el usuario abre a mano otro informe justo entonces, el
    puerto nuevo es el suyo. Cuando la ventana tiene abierto un .pbix o .pbit
    concreto y no es el nuestro, eso no es una sospecha: es una prueba, y se
    descarta el candidato en vez de adoptarlo.

    Solo descarta con prueba. Un .pbip no deja descriptor y no se puede
    demostrar nada por esta via: en ese caso NO se descarta.
    """
    if objetivo is None:
        return False
    from horizun_pbi_mcp.powerbi import desktop_identity

    try:
        identidad = desktop_identity.identify(instancia, target=objetivo)
    except Exception as exc:                              # noqa: BLE001
        log.debug("No se pudo identificar el candidato: %s", exc)
        return False
    if identidad.get("project_path") and identidad.get("path_match") is False:
        log.info("Se descarta el puerto %s: su ventana sirve otro documento",
                 instancia.get("port"))
        return True
    return False


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
        if len(nuevas) == 1 and not _sirve_otro_documento(nuevas[0], pbix_path):
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


def _identidad_del_proceso(desktop_pid: int,
                           desktop_started: Optional[float]) -> Dict[str, Any]:
    """Comprueba que ese PID sigue siendo EL Desktop que se registro."""
    import psutil

    if desktop_started is None:
        return {"ok": False, "reason": "desktop_identity_unverifiable",
                "detail": "sin hora de arranque no se distingue un PID "
                          "reciclado; pasa desktop_started tal cual lo "
                          "devolvio la exportacion"}
    try:
        proceso = psutil.Process(int(desktop_pid))
        nombre = (proceso.name() or "").casefold()
        creado = float(proceso.create_time())
    except psutil.NoSuchProcess:
        return {"ok": False, "reason": "process_gone"}
    except (psutil.AccessDenied, OSError, ValueError):
        return {"ok": False, "reason": "desktop_identity_unverifiable"}
    if nombre != "pbidesktop.exe":
        return {"ok": False, "reason": "desktop_pid_reused",
                "actual_process": nombre}
    if abs(creado - float(desktop_started)) > 1.0:
        return {"ok": False, "reason": "desktop_pid_reused",
                "expected_started": desktop_started, "actual_started": creado}
    return {"ok": True, "pid": int(desktop_pid), "create_time": creado}


def close_desktop_by_identity(desktop_pid: int,
                              desktop_started: Optional[float], *,
                              expected_document: Optional[str | Path] = None
                              ) -> Dict[str, Any]:
    """Cierra la instancia identificada por PID + hora de arranque.

    Existe porque tras `pbi_export_pbix(leave_open=true)` la ventana queda
    sobre el `.pbix` recien guardado -que Desktop mantiene en su TempSaves- y
    buscarla por la ruta del `.pbip` original devolvia `was_open=false`. La
    exportacion devuelve `desktop_session` con estos dos datos, y con ellos se
    cierra exactamente esa ventana sin adivinar nada por el nombre.

    Nunca termina otro proceso: si el PID se reciclo, si ya no es Desktop o
    si no hay hora de arranque que lo demuestre, se niega y lo dice. Si se
    pasa `expected_document`, ademas se exige que la ventana NO este sirviendo
    demostrablemente otro archivo.
    """
    identidad = _identidad_del_proceso(int(desktop_pid), desktop_started)
    salida: Dict[str, Any] = {"desktop_pid": int(desktop_pid),
                              "identity": identidad, "was_open": None}
    if identidad.get("reason") == "process_gone":
        salida.update({"closed": True, "was_open": False,
                       "reason": "el proceso ya no existia"})
        return salida
    if not identidad.get("ok"):
        salida.update({"closed": False, "was_open": None,
                       "reason": identidad.get("reason")})
        return salida

    from horizun_pbi_mcp.powerbi import desktop_identity

    titulos = desktop_identity.titulos_de_ventana(int(desktop_pid))
    salida["window_titles"] = titulos[:5]
    if expected_document is not None:
        estado = desktop_identity.clasificar_titulos(
            titulos, Path(str(expected_document)))
        salida["document_match"] = estado
        if estado == desktop_identity.IDENTIDAD_OTRO_DOCUMENTO:
            salida.update({"closed": False, "was_open": True,
                           "reason": "desktop_serves_other_document"})
            return salida

    abierto = OpenedPbix(str(expected_document or ""), {}, int(desktop_pid),
                         False, 0.0, desktop_started=desktop_started)
    salida.update(close(abierto, force=True))
    salida["was_open"] = True
    salida["verified_closed"] = (
        _identidad_del_proceso(int(desktop_pid), desktop_started).get("reason")
        == "process_gone")
    if not salida["verified_closed"]:
        salida["closed"] = False
        salida["reason"] = "el proceso sigue vivo tras pedirle que termine"
    return salida


def _pid_del_documento_guardado(pbix: Path) -> Dict[str, Any]:
    """Correlacion por titulo para un .pbix/.pbit SIN descriptor abierto.

    Un archivo recien guardado con `Guardar como` no deja descriptor sobre el
    destino y la ventana se lanzo con OTRA ruta en su linea de comandos (el
    .pbip de origen), asi que el filtro de "su cmdline nombra otro proyecto"
    lo descartaba siempre. Aqui el filtro es distinto y no mas debil: la
    ventana vale solo si su titulo es exactamente el nombre del archivo, es
    la UNICA con ese titulo, y lo que nombra su linea de comandos -si nombra
    algo- vive en la MISMA carpeta que el archivo pedido. Dos homonimos en
    carpetas distintas producen ambiguedad, no un cierre.
    """
    from horizun_pbi_mcp.services import project_resolver

    pids = [p.pid for p in _procesos_desktop()]
    ventanas = coincidencias_por_titulo(pbix.stem, pids)
    if ventanas.error and not ventanas.pids:
        return {"pid": None, "reason": "window_enumeration_failed",
                "detail": ventanas.error}
    if not ventanas.pids:
        return {"pid": None, "reason": "no_window_with_that_title"}
    compatibles = []
    for pid in ventanas.pids:
        documentos = _documentos_de_la_linea_de_comandos(pid)
        misma_carpeta = all(
            project_resolver.misma_ruta(Path(d).parent, pbix.parent)
            for d in documentos) if documentos else True
        compatibles.append({"pid": pid, "cmdline_documents": len(documentos),
                            "same_folder": misma_carpeta})
    validos = [c for c in compatibles if c["same_folder"]]
    if len(validos) == 1 and len(ventanas.pids) == 1:
        return {"pid": validos[0]["pid"], "reason": "window_title",
                "candidates": compatibles}
    return {"pid": None, "reason": "ambiguous_window",
            "candidates": compatibles}


def close_desktop_by_path(pbix_path: str | Path, *,
                          session: Any = None) -> Dict[str, Any]:
    """Cierra SOLO la instancia de Desktop que tiene abierto ese archivo.

    Existe porque el ciclo real de trabajo -editar, abrir, mirar, editar-
    chocaba cinco veces por sesion con `project_open_in_desktop` sin ninguna
    salida desde el MCP: habia que ir a PowerShell a matar el proceso. Eso, o
    peor: matar PBIDesktop.exe a ciegas y llevarse la ventana de OTRO informe.

    La identidad se verifica igual que en `close()`: nombre del proceso y hora
    de arranque, nunca el PID a secas -Windows los recicla-. Y al final se
    RE-COMPRUEBA que el archivo ya no este abierto, porque "terminate no
    lanzo" no es "la ventana se cerro".
    """
    pbix = Path(pbix_path).expanduser().resolve()
    if pbix.suffix.casefold() not in {".pbix", ".pbip"}:
        raise ValidationError(
            "Solo se cierran sesiones de archivos .pbix o .pbip.",
            details={"path": str(pbix), "extension": pbix.suffix})

    pid = proceso_con_archivo_abierto(pbix)
    matched_by = "open_file"
    if not pid and pbix.suffix.casefold() != ".pbip":
        # Un .pbix recien guardado por `Guardar como` no deja descriptor
        # sobre el destino -Desktop trabaja sobre su copia de TempSaves-.
        # Primero la evidencia de la propia exportacion (pid + hora de
        # arranque registrados al exportar); si no la hay, el titulo, con
        # ambiguedad declarada cuando no distingue.
        if session is None:
            # La sesion del servidor, si ya existe. No se crea aqui: cerrar
            # una ventana no es motivo para leer session.json.
            from horizun_pbi_mcp import config as _config

            session = getattr(_config, "_session", None)
        registro = session.exportacion_de(str(pbix)) if session is not None \
            and hasattr(session, "exportacion_de") else None
        if registro and registro.get("desktop_pid"):
            resultado = close_desktop_by_identity(
                int(registro["desktop_pid"]), registro.get("desktop_started"),
                expected_document=pbix)
            resultado["path"] = str(pbix)
            resultado["matched_by"] = "export_session"
            resultado["verified_closed"] = bool(resultado.get("verified_closed"))
            return resultado
        correlacion = _pid_del_documento_guardado(pbix)
        pid = correlacion.get("pid")
        matched_by = "window_title"
        if not pid and correlacion.get("reason") == "ambiguous_window":
            return {"closed": False, "was_open": None,
                    "reason": "ambiguous_window", "path": str(pbix),
                    "candidates": correlacion.get("candidates"),
                    "hint": ("hay mas de una ventana compatible y ninguna "
                             "prueba cual sirve este archivo; cierra por "
                             "identidad con `desktop_session` de la "
                             "exportacion")}
    if not pid:
        return {"closed": False, "was_open": False,
                "reason": "el archivo no esta abierto en ningun Desktop",
                "path": str(pbix),
                "hint": ("si la ventana quedo sobre un archivo exportado, "
                         "cierra por identidad: pbi_close_desktop("
                         "desktop_pid=..., desktop_started=..., "
                         "confirm=true) con los datos de `desktop_session`")}

    abierto = OpenedPbix(str(pbix), {}, pid, False, 0.0,
                         desktop_started=_process_started(pid))
    resultado = close(abierto, force=True)
    resultado["path"] = str(pbix)
    resultado["was_open"] = True
    resultado["matched_by"] = matched_by

    # Verificacion real: el archivo ya no puede estar abierto en NINGUN pid.
    todavia = proceso_con_archivo_abierto(pbix)
    resultado["verified_closed"] = todavia is None
    if todavia is not None:
        resultado["closed"] = False
        resultado["reason"] = (f"otro proceso (pid {todavia}) sigue con el "
                               "archivo abierto")
    return resultado
