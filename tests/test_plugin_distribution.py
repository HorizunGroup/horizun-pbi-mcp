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
        assert server["command"] == "python"
        assert server["args"] == ["${CLAUDE_PLUGIN_ROOT}/scripts/plugin_launcher.py"]


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
