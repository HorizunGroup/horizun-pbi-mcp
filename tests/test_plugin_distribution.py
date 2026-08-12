"""Contrato de distribución directa como plugin de Codex y Claude."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from horizun_pbi_mcp import branding
REPO = Path(__file__).resolve().parent.parent


def _json(relative: str) -> dict:
    return json.loads((REPO / relative).read_text(encoding="utf-8"))


def test_los_dos_manifiestos_son_coherentes_y_apache():
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        manifest = _json(relative)
        assert manifest["name"] == branding.MCP_SERVER_NAME
        assert manifest["version"] == branding.VERSION
        assert manifest["license"] == "Apache-2.0"
        server = manifest["mcpServers"][branding.MCP_SERVER_NAME]
        # `cmd` + launch.cmd y NO `python` a secas: el alias de la Microsoft
        # Store se hacia pasar por Python y el plugin moria mudo antes de
        # poder autodiagnosticarse (medido en campo, sesion 2026-08-12).
        assert server["command"] == "cmd"
        assert server["args"] == ["/d", "/c",
                                  "${CLAUDE_PLUGIN_ROOT}/scripts/launch.cmd"]


def test_el_lanzador_cmd_esquiva_el_alias_de_la_store():
    """launch.cmd debe resolver un Python REAL y explicar el fallo si no hay."""
    contenido = (REPO / "scripts/launch.cmd").read_text(encoding="ascii")
    assert "plugin_launcher.py" in contenido, "debe delegar en el launcher real"
    assert "WindowsApps" in contenido, "debe filtrar el alias de la Store"
    assert "where py" in contenido, "el py launcher es la primera opcion"
    assert "1>&2" in contenido, "el remedio sale por stderr, no por el stdio MCP"


def test_el_instalador_de_un_pegado_es_ascii_y_sin_admin():
    """instalar.ps1: PS 5.1 lee UTF-8 sin BOM con codepage OEM — solo ASCII.

    Y nunca debe pedir elevacion: el publico objetivo NO tiene administrador.
    """
    crudo = (REPO / "scripts/instalar.ps1").read_bytes()
    crudo.decode("ascii")  # explota si alguien cuela un acento
    texto = crudo.decode("ascii")
    assert "--scope user" in texto, "toda instalacion winget es a nivel usuario"
    assert "RunAs" not in texto and "Start-Process -Verb" not in texto, (
        "el instalador no puede pedir elevacion")
    assert "CurrentUser" in texto, "la politica de ejecucion se toca solo del usuario"
    docs = (REPO / "docs/INSTALL.md").read_text(encoding="utf-8")
    assert "instalar.ps1 | iex" in docs, "INSTALL.md debe abrir con el pegado"


def test_los_marketplaces_publican_el_mismo_plugin():
    claude = _json(".claude-plugin/marketplace.json")
    codex = _json(".agents/plugins/marketplace.json")
    assert claude["name"] == codex["name"] == "horizun"
    assert claude["plugins"][0]["name"] == branding.MCP_SERVER_NAME
    assert codex["plugins"][0]["name"] == branding.MCP_SERVER_NAME
    assert claude["plugins"][0]["source"]["url"].startswith("https://github.com/HorizunGroup/")
    assert codex["plugins"][0]["source"]["url"].startswith("https://github.com/HorizunGroup/")


def test_skill_de_instalacion_no_conserva_placeholders():
    skill = (REPO / "skills/horizun-pbi-setup/SKILL.md").read_text(encoding="utf-8")
    assert "[TODO" not in skill
    assert "pbi_install_runtime" in skill
    assert "pbi_install_status" in skill


def test_launcher_limpio_expone_el_instalador_por_stdio(tmp_path):
    env = os.environ.copy()
    env["HORIZUN_PBI_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(REPO / "scripts/plugin_launcher.py")],
        cwd=str(REPO), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                             "clientInfo": {"name": "pytest", "version": "1"}}}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    stdout, stderr = process.communicate(
        json.dumps(initialize) + "\n" + json.dumps(listing) + "\n", timeout=10)
    assert process.returncode == 0, stderr
    replies = [json.loads(line) for line in stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["version"] == branding.VERSION
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "pbi_install_runtime", "pbi_install_status"]
    assert stderr == ""
