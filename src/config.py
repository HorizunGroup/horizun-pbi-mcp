"""Configuracion y estado de sesion de Horizun PBI MCP.

- `Settings`: configuracion estatica leida de variables de entorno / .env.
- `Session`: estado en memoria (modelo activo, proyecto .pbip activo) que
  persiste entre llamadas de tools mientras el servidor MCP este vivo, y se
  guarda de forma best-effort en outputs/session.json para sobrevivir reinicios.

No hay NADA hardcodeado a la ruta de un usuario concreto: todo sale de env o
de rutas relativas a la raiz del proyecto.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

# src/config.py -> raiz del proyecto es el padre de src/
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Lee una variable admitiendo el prefijo nuevo y el antiguo.

    Precedencia: HORIZUN_PBI_MCP_* gana sobre PBI_MCP_*, que se conserva por
    compatibilidad con instalaciones existentes.
    """
    from branding import env as _branded_env

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
        # .env de la raiz del proyecto (si existe)
        load_dotenv(PROJECT_ROOT / ".env")
        outputs = _env_path("PBI_MCP_OUTPUTS_DIR", PROJECT_ROOT / "outputs")
        log_file = _env("PBI_MCP_LOG_FILE", str(outputs / "powerbi_mcp.log"))
        return cls(
            libs_dir=_env_path("PBI_MCP_LIBS_DIR", PROJECT_ROOT / "libs"),
            outputs_dir=outputs,
            backups_dir=_env_path("PBI_MCP_BACKUPS_DIR", PROJECT_ROOT / "backups"),
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

    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.RLock()
        self._active_model: Optional[ActiveModel] = None
        self._active_pbip: Optional[ActivePbip] = None
        # Huella ya verificada en este proceso. Una sesion recargada de disco
        # arranca SIN verificar: no se confia en lo que quedo guardado.
        self._verified_fingerprint: Optional[str] = None
        self._session_file = settings.outputs_dir / "session.json"
        self._load_persisted()

    # -- modelo activo --
    @property
    def active_model(self) -> Optional[ActiveModel]:
        with self._lock:
            return self._active_model

    def set_active_model(self, model: ActiveModel) -> None:
        with self._lock:
            self._active_model = model
            # Se acaba de descubrir: cuenta como verificada en este proceso.
            self._verified_fingerprint = model.session_fingerprint
            self._persist()

    def require_active_model(self, verify: bool = True) -> ActiveModel:
        """Devuelve el modelo activo, comprobando que la sesion siga siendo la misma.

        No basta con que el puerto vuelva a estar abierto: Power BI Desktop
        reasigna puertos en cada arranque y otro proceso puede haber tomado el
        anterior. Si la sesion quedo obsoleta o el puerto lo ocupa otra cosa, se
        falla con un mensaje accionable en vez de consultar el modelo equivocado.
        """
        from powerbi.errors import NoActiveModelError

        with self._lock:
            model = self._active_model
            if model is None:
                raise NoActiveModelError(
                    "No hay un modelo activo. Ejecuta pbi_list_desktop_models y luego "
                    "pbi_select_model para elegir el modelo local abierto."
                )
            verified = self._verified_fingerprint

        if not verify:
            return model

        # Ya verificada en esta ejecucion y sin cambios: no se repite el escaneo.
        if verified is not None and verified == model.session_fingerprint:
            return model

        from powerbi.desktop_discovery import StaleSessionError, verify_model

        status = verify_model(model)
        if status["status"] != "ok":
            raise StaleSessionError(
                f"La sesion guardada ya no es valida: {status['reason']} "
                "Ejecuta pbi_list_desktop_models y pbi_select_model de nuevo.",
                details={"status": status["status"], "recorded": model.to_dict()},
            )
        with self._lock:
            self._verified_fingerprint = model.session_fingerprint
        return model

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
        from powerbi.errors import NoActivePbipError

        with self._lock:
            if self._active_pbip is None:
                raise NoActivePbipError(
                    "No hay un proyecto .pbip activo. Ejecuta pbi_open_pbip_project primero."
                )
            return self._active_pbip

    # -- persistencia best-effort --
    def _persist(self) -> None:
        data = {
            "active_model": self._active_model.to_dict() if self._active_model else None,
            "active_pbip": self._active_pbip.to_dict() if self._active_pbip else None,
        }
        try:
            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._session_file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._session_file)
        except OSError:
            pass

    def _load_persisted(self) -> None:
        try:
            if self._session_file.exists():
                data = json.loads(self._session_file.read_text(encoding="utf-8"))
                am = data.get("active_model")
                ap = data.get("active_pbip")
                if am:
                    self._active_model = ActiveModel(**am)
                if ap:
                    self._active_pbip = ActivePbip(**ap)
        except (OSError, ValueError, TypeError):
            pass


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
