"""Servidor MCP de Horizun PBI MCP (FastMCP sobre stdio).

Ejecuta:
    python src/server.py
o, si se instalo el paquete:
    horizun-pbi-mcp   (alias legacy: powerbi-mcp)
"""
from __future__ import annotations

import os
import sys

# Permite ejecutar como script suelto: agrega src/ a sys.path para que
# `from powerbi... import`, `from pbip...`, `import config`, etc. resuelvan.
_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from mcp.server.fastmcp import FastMCP  # noqa: E402

import branding  # noqa: E402
from config import get_session, get_settings  # noqa: E402
from logging_config import get_logger, setup_logging  # noqa: E402
from tools import (  # noqa: E402
    audit_tools,
    convert_tools,
    dax_tools,
    design_tools,
    documentation_tools,
    explore_tools,
    measure_tools,
    model_edit_tools,
    ops_tools,
    page_tools,
    pbip_tools,
    refresh_tools,
    report_tools,
    spec_tools,
    visual_tools,
    workflow_tools,
)

_TOOL_MODULES = (
    audit_tools,
    convert_tools,
    dax_tools,
    design_tools,
    documentation_tools,
    explore_tools,
    measure_tools,
    model_edit_tools,
    ops_tools,
    page_tools,
    pbip_tools,
    refresh_tools,
    report_tools,
    spec_tools,
    visual_tools,
    workflow_tools,
)


def _anotar_riesgo(mcp: FastMCP, log) -> None:
    """Declara la clase de riesgo de cada tool como `annotations` del MCP.

    Se hace en un solo sitio, despues de registrar, en vez de repetir el
    argumento en las 118 firmas: la fuente de verdad es `tools/risk.py` y asi no
    puede divergir de si misma. `annotations_for()` falla cerrado, de modo que
    una tool nueva sin clasificar se anuncia como destructiva —nunca como de
    solo lectura— hasta que alguien la clasifique.
    """
    from mcp.types import ToolAnnotations

    from tools.risk import annotations_for

    for tool in mcp._tool_manager.list_tools():
        tool.annotations = ToolAnnotations(**annotations_for(tool.name))
    log.debug("Clase de riesgo declarada en %d tools.",
              len(mcp._tool_manager.list_tools()))


def build_server() -> FastMCP:
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    log = get_logger("server")

    session = get_session()
    if settings.default_pbip:
        try:
            from pbip import project_locator

            project_locator.open_project(session, settings.default_pbip)
            log.info("Proyecto .pbip por defecto abierto: %s", settings.default_pbip)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo abrir el .pbip por defecto: %s", exc)

    mcp = FastMCP(branding.MCP_SERVER_NAME)
    # FastMCP no acepta `version` en el constructor; el servidor interno
    # la expone en el handshake. Sin esto, serverInfo devolveria la
    # version de la libreria mcp en vez de la del producto.
    mcp._mcp_server.version = branding.VERSION
    for module in _TOOL_MODULES:
        module.register(mcp)
    _anotar_riesgo(mcp, log)

    log.info("%s %s listo. %d modulos de tools registrados.",
             branding.PRODUCT_NAME, branding.VERSION, len(_TOOL_MODULES))
    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()  # transporte stdio por defecto


if __name__ == "__main__":
    main()
