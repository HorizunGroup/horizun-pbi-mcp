"""Carga del runtime .NET (pythonnet) y de las librerias de Analysis Services.

Decision tecnica (documentada en PLAN.md): en vez de depender de que ADOMD.NET
este instalado en el GAC o de invocar Tabular Editor CLI, **vendorizamos** las
DLLs de Microsoft.AnalysisServices.* en `libs/` y las cargamos por ruta absoluta
con pythonnet. Es lo mas estable y no requiere permisos de administrador.

- ADOMD.NET  -> consultas DAX en vivo (adomd_client / dax_runner).
- TOM        -> lectura/escritura del modelo y refresh (model_reader/writer, refresh).

pythonnet debe elegir el runtime ANTES del primer `import clr`; por eso `import clr`
se hace de forma perezosa dentro de las funciones, nunca a nivel de modulo.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from config import get_settings
from logging_config import get_logger
from powerbi.errors import AdomdNotInstalledError, ClrNotAvailableError, TomNotInstalledError

log = get_logger("clr")

_lock = threading.Lock()
_runtime_loaded = False
_adomd_ns: Optional[Any] = None
_tom_ns: Optional[Any] = None

# DLLs principales que se cargan por ruta absoluta desde libs/.
_ADOMD_DLL = "Microsoft.AnalysisServices.AdomdClient.dll"
_TOM_DEPS = (
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.dll",
    "Microsoft.AnalysisServices.Tabular.Json.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
)


def _ensure_runtime(runtime: str = "netfx") -> None:
    """Selecciona el runtime .NET de pythonnet (una sola vez)."""
    global _runtime_loaded
    if _runtime_loaded:
        return
    from pythonnet import load

    order = [runtime] + [r for r in ("netfx", "coreclr") if r != runtime]
    last_exc: Optional[Exception] = None
    for rt in order:
        try:
            load(rt)
            log.info("Runtime .NET cargado: %s", rt)
            _runtime_loaded = True
            return
        except RuntimeError:
            # Ya habia un runtime cargado en el proceso: lo aceptamos.
            log.debug("Runtime .NET ya estaba cargado; se reutiliza.")
            _runtime_loaded = True
            return
        except Exception as exc:  # pragma: no cover - depende del entorno
            last_exc = exc
            log.warning("No se pudo cargar el runtime .NET '%s': %s", rt, exc)
    raise ClrNotAvailableError(
        "No se pudo inicializar el runtime .NET (pythonnet). Verifica que .NET "
        "Framework 4.x o .NET Core este disponible.",
        details={"cause": str(last_exc) if last_exc else None},
    )


def _add_reference(clr, libs_dir: Path, dll: str, friendly: str, error_cls) -> None:
    dll_path = libs_dir / dll
    if dll_path.exists():
        clr.AddReference(str(dll_path))
        return
    # Fallback: intentar por nombre simple (GAC / probing paths).
    try:
        clr.AddReference(friendly)
    except Exception as exc:
        raise error_cls(
            f"No se encontro la libreria '{friendly}'. Esperaba {dll_path} o el "
            f"ensamblado en el GAC. Ejecuta:  python scripts/fetch_libs.py",
            details={"expected": str(dll_path), "cause": str(exc)},
        ) from exc


def load_adomd() -> Any:
    """Carga y devuelve el namespace Microsoft.AnalysisServices.AdomdClient."""
    global _adomd_ns
    if _adomd_ns is not None:
        return _adomd_ns
    with _lock:
        if _adomd_ns is not None:
            return _adomd_ns
        settings = get_settings()
        _ensure_runtime(settings.dotnet_runtime)
        import clr  # noqa: WPS433 (import perezoso obligatorio)

        _add_reference(
            clr,
            settings.libs_dir,
            _ADOMD_DLL,
            "Microsoft.AnalysisServices.AdomdClient",
            AdomdNotInstalledError,
        )
        import Microsoft.AnalysisServices.AdomdClient as Adomd  # type: ignore

        _adomd_ns = Adomd
        log.info("ADOMD.NET cargado desde %s", settings.libs_dir)
        return Adomd


def load_tom() -> Any:
    """Carga y devuelve el namespace Microsoft.AnalysisServices.Tabular (TOM)."""
    global _tom_ns
    if _tom_ns is not None:
        return _tom_ns
    with _lock:
        if _tom_ns is not None:
            return _tom_ns
        settings = get_settings()
        _ensure_runtime(settings.dotnet_runtime)
        import clr  # noqa: WPS433

        # Cargar dependencias en orden (Core -> ... -> Tabular). LoadFrom tambien
        # resuelve las hermanas desde la misma carpeta, pero somos explicitos.
        for dep in _TOM_DEPS:
            _add_reference(
                clr,
                settings.libs_dir,
                dep,
                dep[:-4],  # nombre simple sin .dll
                TomNotInstalledError,
            )
        import Microsoft.AnalysisServices.Tabular as TOM  # type: ignore

        _tom_ns = TOM
        log.info("TOM cargado desde %s", settings.libs_dir)
        return TOM


def diagnostics() -> dict:
    """Estado del interop para pbi_test_connection / troubleshooting."""
    settings = get_settings()
    libs = settings.libs_dir
    return {
        "libs_dir": str(libs),
        "libs_dir_exists": libs.exists(),
        "adomd_dll_present": (libs / _ADOMD_DLL).exists(),
        "tom_dll_present": (libs / "Microsoft.AnalysisServices.Tabular.dll").exists(),
        "runtime_preference": settings.dotnet_runtime,
        "runtime_loaded": _runtime_loaded,
    }
