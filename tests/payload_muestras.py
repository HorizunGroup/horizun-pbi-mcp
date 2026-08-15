"""Las respuestas reales de las que se congela la forma (CONTRACT-002).

Una muestra por tool, obtenida **ejecutándola de verdad** sobre un proyecto
sintético en un directorio temporal. No hay payloads escritos a mano: un golden
inventado congela lo que alguien creía que devolvía la tool, que es peor que no
tener golden porque además parece que lo tienes.

Alcance: lo que se puede obtener sin Power BI Desktop. Lo demás es TEST-003.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent


class _Mcp:
    """Recolecta las funciones que cada módulo registra como tool."""

    def __init__(self) -> None:
        self.tools: Dict[str, Callable] = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _proyecto_sintetico(raiz: Path) -> Path:
    """Un `.pbip` mínimo con la forma que el servidor sabe leer."""
    proj = raiz / "proj"
    rep = proj / "MyReport.Report"
    (rep / "definition" / "pages" / "pg1" / "visuals").mkdir(parents=True)
    (rep / ".platform").write_text(json.dumps({
        "metadata": {"type": "Report", "displayName": "MyReport"},
        "config": {"version": "2.0"}}), encoding="utf-8")
    (rep / "definition.pbir").write_text(json.dumps({
        "version": "4.0",
        "datasetReference": {"byPath": {"path": "../MyReport.SemanticModel"}}}),
        encoding="utf-8")
    (proj / "MyReport.pbip").write_text(json.dumps({
        "version": "1.0",
        "artifacts": [{"report": {"path": "MyReport.Report"}}]}), encoding="utf-8")
    return proj / "MyReport.pbip"


def _payload(salida: Any) -> Any:
    """El interior, no el envelope: `result` cuando lo hay."""
    if isinstance(salida, dict) and "result" in salida:
        return salida["result"]
    return salida


def capturar() -> Dict[str, Any]:
    """Ejecuta cada tool de la muestra y devuelve la FORMA de su payload.

    El entorno se fija a PROPOSITO en el estado "recien instalado, sin DLL y sin
    esquemas". No es capricho: `pbi_health_check` enumera lo que falta, asi que
    en una maquina con todo instalado ese campo sale vacio y en una recien
    hecha sale poblado. Un golden que dependiera de eso fallaria al cambiar de
    maquina y nadie sabria si el contrato se rompio o solo el entorno.
    """
    from horizun_pbi_mcp import config
    from horizun_pbi_mcp.config import Session, Settings
    from horizun_pbi_mcp.services import guide
    from horizun_pbi_mcp.tools import ops_tools
    from tests.payload_contract import forma

    with tempfile.TemporaryDirectory(prefix="hz_payloads_") as tmp:
        raiz = Path(tmp)
        pbip = _proyecto_sintetico(raiz)

        previas = config._settings
        previo_esquemas = os.environ.get("HORIZUN_PBI_MCP_SCHEMAS_DIR")
        vacio = raiz / "sin-esquemas"
        vacio.mkdir()
        os.environ["HORIZUN_PBI_MCP_SCHEMAS_DIR"] = str(vacio)
        config._settings = Settings(
            libs_dir=raiz / "libs", outputs_dir=raiz / "outputs",
            backups_dir=raiz / "backups", max_rows=100, command_timeout=30,
            dotnet_runtime="netfx", log_level="INFO", log_file=None,
            default_pbip=None)
        config._settings.ensure_dirs()
        try:
            mcp = _Mcp()
            ops_tools.register(mcp)

            muestras: Dict[str, Any] = {}
            # Solo las que no necesitan un modelo vivo ni escriben nada.
            for nombre in ("pbi_health_check", "pbi_capabilities"):
                fn = mcp.tools.get(nombre)
                if fn is None:                               # pragma: no cover
                    continue
                muestras[nombre] = forma(_payload(fn()))

            # `situacion` es la primera respuesta que lee un agente: si pierde
            # una clave, empieza a ciegas.
            muestras["guide.situacion"] = forma(
                guide.situacion(Session(config._settings)))
        finally:
            config._settings = previas
            if previo_esquemas is None:
                os.environ.pop("HORIZUN_PBI_MCP_SCHEMAS_DIR", None)
            else:
                os.environ["HORIZUN_PBI_MCP_SCHEMAS_DIR"] = previo_esquemas
        del pbip
    return muestras
