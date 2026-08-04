"""Branding: identidad del producto y compatibilidad con lo anterior."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from horizun_pbi_mcp import branding
from horizun_pbi_mcp.server import build_server


def test_identidad():
    i = branding.identity()
    assert i["product"] == "Horizun PBI MCP"
    assert i["server_name"] == "horizun-pbi-mcp"
    assert i["package"] == "horizun-pbi-mcp"
    assert i["version"] == branding.VERSION


def test_el_handshake_reporta_el_producto():
    mcp = build_server()
    assert mcp._mcp_server.name == "horizun-pbi-mcp"
    assert mcp._mcp_server.version == branding.VERSION, \
        "sin esto, serverInfo devolveria la version de la libreria mcp"


def test_todas_las_tools_conservan_el_prefijo():
    """El nombre comercial NO es motivo para renombrar el contrato."""
    tools = asyncio.run(build_server().list_tools())
    from tests.test_tool_contract import EXPECTED_COUNT

    assert len(tools) == EXPECTED_COUNT
    malas = [t.name for t in tools if not t.name.startswith(branding.TOOL_PREFIX)]
    assert not malas, f"tools sin el prefijo {branding.TOOL_PREFIX}: {malas}"


def test_el_logger_raiz_es_el_nuevo():
    from horizun_pbi_mcp.logging_config import LOGGER_NAME, get_logger

    assert LOGGER_NAME == "horizun_pbi_mcp"
    assert get_logger("x").name == "horizun_pbi_mcp.x"


# ------------------------------------------------- variables de entorno ------
def test_precedencia_del_prefijo_nuevo(monkeypatch):
    monkeypatch.setenv("HORIZUN_PBI_MCP_MAX_ROWS", "111")
    monkeypatch.setenv("PBI_MCP_MAX_ROWS", "222")
    assert branding.env("MAX_ROWS") == "111", "el prefijo nuevo debe ganar"
    assert branding.env_source("MAX_ROWS") == "HORIZUN_PBI_MCP_MAX_ROWS"


def test_el_prefijo_antiguo_sigue_funcionando(monkeypatch):
    monkeypatch.delenv("HORIZUN_PBI_MCP_MAX_ROWS", raising=False)
    monkeypatch.setenv("PBI_MCP_MAX_ROWS", "333")
    assert branding.env("MAX_ROWS") == "333"
    assert branding.env_source("MAX_ROWS") == "PBI_MCP_MAX_ROWS"


def test_settings_respeta_la_precedencia(monkeypatch, tmp_path):
    from horizun_pbi_mcp import config
    monkeypatch.setenv("HORIZUN_PBI_MCP_MAX_ROWS", "77")
    monkeypatch.setenv("PBI_MCP_MAX_ROWS", "99")
    monkeypatch.setattr(config, "_settings", None)
    assert config.Settings.load().max_rows == 77


def test_sin_variables_se_usa_el_default(monkeypatch):
    for v in ("HORIZUN_PBI_MCP_MAX_ROWS", "PBI_MCP_MAX_ROWS"):
        monkeypatch.delenv(v, raising=False)
    assert branding.env("MAX_ROWS", "1000") == "1000"


# ------------------------------------------------------------ identidad ------
def test_health_check_reporta_el_producto(session, monkeypatch):
    import horizun_pbi_mcp.config as cfg
    monkeypatch.setattr(cfg, "_session", session)
    mcp = build_server()
    res = asyncio.run(mcp.call_tool("pbi_health_check", {}))
    payload = res[1] if isinstance(res, tuple) else res
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    assert payload["server"]["server_name"] == "horizun-pbi-mcp"
    assert payload["server"]["version"] == branding.VERSION


def test_capabilities_reporta_el_producto(session, monkeypatch):
    import horizun_pbi_mcp.config as cfg
    monkeypatch.setattr(cfg, "_session", session)
    mcp = build_server()
    res = asyncio.run(mcp.call_tool("pbi_capabilities", {}))
    payload = res[1] if isinstance(res, tuple) else res
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    assert payload["server"]["product"] == "Horizun PBI MCP"


def test_se_documenta_el_alias_legacy():
    alias = branding.identity()["legacy_aliases"]
    assert alias["server_name"] == "powerbi-mcp"
    assert alias["env_prefix"] == "PBI_MCP_"
    assert "compatibilidad" in alias["note"].lower()
