"""Configuracion y estado de sesion de Horizun PBI MCP.

- `Settings`: configuracion estatica leida de variables de entorno / .env.
- `Session`: estado en memoria (modelo activo, proyecto .pbip activo) que
  persiste entre llamadas de tools mientras el servidor MCP este vivo, y se
  guarda de forma best-effort en outputs/session.json para sobrevivir reinicios.

No hay NADA hardcodeado a la ruta de un usuario concreto: todo sale de env o
de `data_root()`.

Donde caen `outputs/` y `backups/` cuando nadie lo configura
-----------------------------------------------------------
Antes se resolvian relativos a la raiz DEL REPOSITORIO. Eso funciona mientras
se ejecuta desde un clon, y deja de hacerlo en cuanto alguien instala el
paquete: `config.py` pasa a vivir en `site-packages/horizun_pbi_mcp/`, y esa
"raiz" es el arbol de librerias del entorno virtual. Los respaldos de los
proyectos Power BI del usuario acababan en algo como
`.venv\\Lib\\site-packages\\backups` y desaparecian en la siguiente
reinstalacion, que es justo lo que un respaldo no puede hacer.

Ahora se distingue el caso: si al lado del paquete esta el `pyproject.toml`
del repositorio, se trabaja desde un clon y se conservan las rutas de siempre
—para no mover los datos de quien ya lo usa asi—; si no, es una instalacion y
se usa el directorio de datos del usuario del sistema operativo. Las variables
de entorno siguen mandando sobre ambos.
"""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("config")

#: src/horizun_pbi_mcp/config.py -> la raiz del clon es el abuelo de src/.
#: Solo es "la raiz del proyecto" cuando de verdad se ejecuta desde un clon;
#: `running_from_checkout()` es quien lo decide, no esta constante.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

#: Nombre de la carpeta bajo el directorio de datos del usuario.
_APP_DIR_NAME = "horizun-pbi-mcp"


def running_from_checkout() -> bool:
    """Si se esta ejecutando desde un clon del repositorio y no instalado.

    La senal es el `pyproject.toml` del propio proyecto junto al `src/`. Se
    comprueba que sea EL nuestro y no un `pyproject.toml` cualquiera: en un
    monorepo ajeno, o si alguien copia el paquete dentro de otro proyecto, el
    fichero existe y no dice nada de nosotros.
    """
    pyproject = PROJECT_ROOT / "pyproject.toml"
    if not (pyproject.is_file() and (PROJECT_ROOT / "src").is_dir()):
        return False
    try:
        cabecera = pyproject.read_text(encoding="utf-8", errors="ignore")[:2000]
    except OSError:
        return False
    return 'name = "horizun-pbi-mcp"' in cabecera


def _home_o_temporal() -> Optional[Path]:
    """El perfil del usuario, o None si el entorno no permite deducirlo.

    `Path.home()` **lanza** `RuntimeError` cuando no hay `USERPROFILE` ni
    `HOME`. No es hipotetico: un servidor MCP lanzado como servicio, o desde un
    cliente que limpia el entorno, se queda sin esas variables, y ahi esta
    funcion se llama al arrancar. Dejar escapar la excepcion mataba el proceso
    antes del primer mensaje del protocolo, que es la peor forma de fallar:
    sin traza visible para quien lo configuro.
    """
    try:
        return Path.home()
    except RuntimeError:
        return None


def data_root() -> Path:
    """Raiz de los datos generados (outputs/, backups/) cuando no hay env.

    Desde un clon: la raiz del repositorio, como siempre. Instalado: el
    directorio de datos del usuario del sistema —`%LOCALAPPDATA%` en Windows,
    `XDG_DATA_HOME` o `~/.local/share` en el resto—, nunca el arbol de
    librerias del entorno virtual.

    Nunca lanza. Si el entorno no da ni perfil de usuario, cae al directorio
    temporal: en ese escenario cualquier eleccion es un compromiso, y arrancar
    y poder avisar por `pbi_health_check` es mejor que morir sin decir nada.
    Quien necesite una ubicacion estable la fija con
    `HORIZUN_PBI_MCP_BACKUPS_DIR`, que sigue teniendo prioridad sobre esto.
    """
    if running_from_checkout():
        return PROJECT_ROOT

    hogar = _home_o_temporal()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        raiz = Path(base) if base else (hogar / "AppData" / "Local" if hogar else None)
    else:
        base = os.environ.get("XDG_DATA_HOME")
        raiz = Path(base) if base else (hogar / ".local" / "share" if hogar else None)

    if raiz is None:
        import tempfile

        raiz = Path(tempfile.gettempdir())
    return raiz / _APP_DIR_NAME


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Lee una variable admitiendo el prefijo nuevo y el antiguo.

    Precedencia: HORIZUN_PBI_MCP_* gana sobre PBI_MCP_*, que se conserva por
    compatibilidad con instalaciones existentes.
    """
    from horizun_pbi_mcp.branding import env as _branded_env

    sin_prefijo = key.replace("PBI_MCP_", "", 1)
    return _branded_env(sin_prefijo, default)


def _env_path(key: str, default: Path) -> Path:
    val = _env(key)
    return Path(val).expanduser() if val else default


def _env_int(key: str, default: int) -> int:
    val = _env(key)
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


@dataclass
class Settings:
    """Configuracion estatica del servidor."""

    libs_dir: Path
    outputs_dir: Path
    backups_dir: Path
    max_rows: int
    command_timeout: int
    dotnet_runtime: str
    log_level: str
    log_file: Optional[str]
    default_pbip: Optional[str]

    @classmethod
    def load(cls) -> "Settings":
        # .env de la raiz del clon (si existe). Instalado no hay clon ni .env.
        load_dotenv(PROJECT_ROOT / ".env")
        raiz = data_root()
        outputs = _env_path("PBI_MCP_OUTPUTS_DIR", raiz / "outputs")
        log_file = _env("PBI_MCP_LOG_FILE", str(outputs / "powerbi_mcp.log"))
        return cls(
            # Las DLL de Analysis Services las descarga `fetch_libs.py` junto al
            # paquete, asi que su sitio no depende de donde vayan los datos.
            libs_dir=_env_path("PBI_MCP_LIBS_DIR", raiz / "libs"),
            outputs_dir=outputs,
            backups_dir=_env_path("PBI_MCP_BACKUPS_DIR", raiz / "backups"),
            max_rows=_env_int("PBI_MCP_MAX_ROWS", 1000),
            command_timeout=_env_int("PBI_MCP_COMMAND_TIMEOUT", 120),
            dotnet_runtime=_env("PBI_MCP_DOTNET_RUNTIME", "netfx") or "netfx",
            log_level=_env("PBI_MCP_LOG_LEVEL", "INFO") or "INFO",
            log_file=log_file if log_file else None,
            default_pbip=_env("PBI_MCP_DEFAULT_PBIP"),
        )

    def ensure_dirs(self) -> None:
        for d in (self.outputs_dir, self.backups_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


@dataclass
class ActiveModel:
    """Modelo tabular local activo (Power BI Desktop en localhost:<puerto>).

    Los cuatro ultimos campos son la IDENTIDAD de la sesion. Existen porque un
    puerto abierto no prueba que sea la misma sesion: Power BI Desktop asigna
    un puerto distinto en cada arranque y el sistema puede reutilizar tanto el
    puerto como el identificador de proceso.
    """

    host: str
    port: int
    connection_string: str
    catalog: Optional[str] = None
    database_name: Optional[str] = None
    model_name: Optional[str] = None
    pid: Optional[int] = None
    process_started: Optional[float] = None
    workspace: Optional[str] = None
    session_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ActivePbip:
    """Proyecto .pbip activo en disco."""

    pbip_path: str
    project_dir: str
    report_dir: Optional[str] = None
    semantic_model_dir: Optional[str] = None
    report_name: Optional[str] = None
    has_pbir: bool = False
    has_tmdl: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Session:
    """Estado mutable de la sesion, seguro para hilos."""

    # Una comprobacion de identidad implica enumerar procesos, puertos y
    # consultar el catalogo. Se permite reutilizarla solo durante una ventana
    # muy corta; nunca durante toda la vida del servidor MCP.
    _MODEL_VERIFICATION_TTL_SECONDS = 1.0

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        # Serializa exclusivamente seleccion/mutaciones live. No se reutiliza
        # `_lock`: un refresh largo no debe bloquear operaciones PBIP en disco.
        self._model_operation_lock = threading.RLock()
        self._active_model: Optional[ActiveModel] = None
        self._active_pbip: Optional[ActivePbip] = None
        # Huella ya verificada en este proceso. Una sesion recargada de disco
        # arranca SIN verificar: no se confia en lo que quedo guardado.
        self._verified_fingerprint: Optional[str] = None
        self._verified_identity: Optional[tuple[Any, ...]] = None
        self._verified_at_monotonic: Optional[float] = None
        self._session_file = settings.outputs_dir / "session.json"
        # Lo pone `_load_persisted`; se declara aqui para que `_persist`
        # nunca pueda mirarlo antes de que exista.
        self._session_corrupta: Optional[str] = None
        self._load_persisted()

    # -- modelo activo --
    @property
    def active_model(self) -> Optional[ActiveModel]:
        with self._lock:
            return self._active_model

    def set_active_model(self, model: ActiveModel) -> None:
        with self._model_operation_lock:
            with self._lock:
                self._active_model = model
                # Se acaba de descubrir: cuenta como verificada en este proceso.
                self._verified_fingerprint = model.session_fingerprint
                self._verified_identity = self._model_identity(model)
                self._verified_at_monotonic = time.monotonic()
                self._persist()

    @staticmethod
    def _model_identity(model: ActiveModel) -> tuple[Any, ...]:
        """Identidad inmutable usada para ligar la cache a una seleccion."""
        workspace = (os.path.normcase(os.path.normpath(model.workspace))
                     if model.workspace else None)
        return (
            model.host.casefold(), int(model.port), model.pid,
            model.process_started, workspace, model.catalog,
            model.session_fingerprint,
        )

    def _invalidate_model_verification(self) -> None:
        self._verified_fingerprint = None
        self._verified_identity = None
        self._verified_at_monotonic = None

    def require_active_model(self, verify: bool = True) -> ActiveModel:
        """Devuelve el modelo activo, comprobando que la sesion siga siendo la misma.

        No basta con que el puerto vuelva a estar abierto: Power BI Desktop
        reasigna puertos en cada arranque y otro proceso puede haber tomado el
        anterior. Si la sesion quedo obsoleta o el puerto lo ocupa otra cosa, se
        falla con un mensaje accionable en vez de consultar el modelo equivocado.
        """
        from horizun_pbi_mcp.powerbi.errors import NoActiveModelError

        with self._lock:
            model = self._active_model
            if model is None:
                raise NoActiveModelError(
                    "No hay un modelo activo. Ejecuta pbi_list_desktop_models y luego "
                    "pbi_select_model para elegir el modelo local abierto."
                )
            verified = self._verified_fingerprint
            verified_identity = self._verified_identity
            verified_at = self._verified_at_monotonic
            identity = self._model_identity(model)

        if not verify:
            return model

        # La cache esta ligada a TODA la identidad y caduca como maximo en un
        # segundo. Asi una sesion valida no se reutiliza indefinidamente si
        # Desktop muere o Windows recicla el PID/puerto.
        if verified is not None and verified == model.session_fingerprint \
                and verified_identity == identity and verified_at is not None:
            age = time.monotonic() - verified_at
            if 0.0 <= age < self._MODEL_VERIFICATION_TTL_SECONDS:
                return model

        from horizun_pbi_mcp.powerbi.desktop_discovery import StaleSessionError, verify_model

        try:
            status = verify_model(model)
        except Exception:
            # Un fallo del propio descubrimiento tampoco deja una identidad
            # certificada. Si otro hilo ya selecciono otro modelo, no se borra
            # la cache nueva.
            with self._lock:
                if self._active_model is model \
                        and self._model_identity(model) == identity:
                    self._invalidate_model_verification()
            raise
        if status["status"] != "ok":
            with self._lock:
                if self._active_model is model \
                        and self._model_identity(model) == identity:
                    self._invalidate_model_verification()
            raise StaleSessionError(
                f"La sesion guardada ya no es valida: {status['reason']} "
                "Ejecuta pbi_list_desktop_models y pbi_select_model de nuevo.",
                details={"status": status["status"], "recorded": model.to_dict()},
            )
        with self._lock:
            # La seleccion pudo cambiar en otro hilo mientras se enumeraban
            # procesos. Nunca certificar la identidad vieja como si fuera la
            # nueva.
            if self._active_model is not model \
                    or self._model_identity(model) != identity:
                self._invalidate_model_verification()
                raise StaleSessionError(
                    "El modelo activo cambio durante la comprobacion de "
                    "identidad. Reintenta la operacion.",
                    details={"status": "changed_during_verification"},
                )
            self._verified_fingerprint = model.session_fingerprint
            self._verified_identity = identity
            self._verified_at_monotonic = time.monotonic()
        return model

    @contextmanager
    def active_model_lease(self) -> Iterator[ActiveModel]:
        """Fija la seleccion activa durante una operacion live mutante.

        `require_active_model()` devuelve una foto segura, pero otro hilo podia
        ejecutar `set_active_model()` entre esa llamada y `SaveChanges()`. La
        mutacion terminaba entonces sobre un modelo que ya no era el activo.
        Mantener el lock de operaciones hasta salir serializa seleccion y
        escrituras TOM, sin bloquear PBIP ni lecturas DAX concurrentes.
        """
        with self._model_operation_lock:
            model = self.require_active_model()
            yield model

    # -- proyecto pbip activo --
    @property
    def active_pbip(self) -> Optional[ActivePbip]:
        with self._lock:
            return self._active_pbip

    def set_active_pbip(self, pbip: ActivePbip) -> None:
        with self._lock:
            self._active_pbip = pbip
            self._persist()

    def require_active_pbip(self) -> ActivePbip:
        from horizun_pbi_mcp.powerbi.errors import NoActivePbipError

        with self._lock:
            if self._active_pbip is None:
                raise NoActivePbipError(
                    "No hay un proyecto .pbip activo. Ejecuta pbi_open_pbip_project primero."
                )
            return self._active_pbip

    # -- persistencia best-effort --
    @property
    def persisted_state(self) -> Dict[str, Any]:
        """Que le paso al `session.json` al arrancar.

        `ok` cuando se leyo (o no existia); `corrupt` cuando existe y no es
        JSON valido. En ese caso el archivo NO se toca —ni se reescribe, ni se
        renombra, ni se borra— y la sesion arranca en blanco. Es el unico
        estado que hay que contar hacia fuera: todo lo demas sigue igual, pero
        el modelo y el proyecto activos ya no sobreviven a un reinicio y nadie
        se enteraria.
        """
        estado: Dict[str, Any] = {"path": str(self._session_file),
                                  "state": "corrupt" if self._session_corrupta
                                  else "ok"}
        if self._session_corrupta:
            estado["reason"] = self._session_corrupta
            estado["consequence"] = (
                "La sesion arranco vacia y no se persistira nada mientras el "
                "archivo siga asi: el modelo y el proyecto activos se perderan "
                "al reiniciar.")
            estado["recovery"] = (
                "Miralo y borralo tu si no te dice nada. No se sobreescribe "
                "solo, a proposito: nadie sabe que habia dentro.")
        return estado

    def _persist(self) -> None:
        if self._session_corrupta:
            # Invariante: no se pisa un JSON que no parsea. Perder la sesion
            # persistida es reversible; destruir el archivo que explica que
            # paso, no.
            log.warning(
                "No se persiste la sesion: %s esta corrupto (%s). Se deja "
                "intacto.", self._session_file, self._session_corrupta)
            return
        data = {
            "active_model": self._active_model.to_dict() if self._active_model else None,
            "active_pbip": self._active_pbip.to_dict() if self._active_pbip else None,
        }
        try:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            from horizun_pbi_mcp.services.txn import durable_write

            durable_write(
                self._session_file,
                json.dumps(data, indent=2, ensure_ascii=False,
                           allow_nan=False).encode("utf-8"))
        except OSError:
            pass

    def _load_persisted(self) -> None:
        self._session_corrupta: Optional[str] = None
        if not self._session_file.exists():
            return
        try:
            crudo = self._session_file.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(crudo)
        except ValueError as exc:
            # Antes se ignoraba en silencio y el primer `_persist()` lo pisaba.
            self._session_corrupta = f"{type(exc).__name__}: {exc}"
            log.error("session.json corrupto, se conserva sin tocar: %s (%s)",
                      self._session_file, exc)
            return
        try:
            am = data.get("active_model") if isinstance(data, dict) else None
            ap = data.get("active_pbip") if isinstance(data, dict) else None
            if am:
                self._active_model = ActiveModel(**am)
            if ap:
                self._active_pbip = ActivePbip(**ap)
        except (TypeError, AttributeError) as exc:
            # JSON valido pero con otra forma: no es corrupcion del archivo, y
            # reescribirlo con la forma buena es justo lo que toca.
            log.warning("session.json no tiene la forma esperada (%s): se "
                        "ignora su contenido.", exc)


# --- singletons de proceso ---
_settings: Optional[Settings] = None
_session: Optional[Session] = None
_settings_lock = threading.Lock()
_session_lock = threading.Lock()


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                s = Settings.load()
                s.ensure_dirs()
                _settings = s
    return _settings


def get_session() -> Session:
    global _session
    if _session is None:
        # Resolver settings ANTES de tomar el lock de sesion para evitar
        # un deadlock por doble adquisicion.
        settings = get_settings()
        with _session_lock:
            if _session is None:
                _session = Session(settings)
    return _session
