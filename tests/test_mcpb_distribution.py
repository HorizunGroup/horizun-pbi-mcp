"""Claude Desktop's one-click MCPB is small, reproducible and private-data safe."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _builder():
    path = ROOT / "scripts" / "build_mcpb.py"
    spec = importlib.util.spec_from_file_location("_build_mcpb_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build(tmp_path: Path, name: str = "horizun.mcpb") -> Path:
    output = tmp_path / name
    _builder().build(output, repo=ROOT, ref="HEAD")
    return output


def test_manifest_declares_a_windows_uv_bundle() -> None:
    manifest = json.loads(
        (ROOT / "packaging/claude-desktop/manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == "0.4"
    assert manifest["name"] == "horizun-pbi-mcp"
    assert manifest["server"]["type"] == "uv"
    assert manifest["server"]["entry_point"] == "payload/scripts/plugin_launcher.py"
    assert manifest["compatibility"]["platforms"] == ["win32"]
    command = manifest["server"]["mcp_config"]
    assert command["command"] == "uv"
    assert "${__dirname}/payload/scripts/plugin_launcher.py" in command["args"]


def test_bundle_is_reproducible_and_contains_only_committed_payload(tmp_path: Path) -> None:
    first = _build(tmp_path, "first.mcpb")
    second = _build(tmp_path, "second.mcpb")
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()).digest()

    with zipfile.ZipFile(first) as bundle:
        names = bundle.namelist()
        assert "manifest.json" in names
        assert "pyproject.toml" in names
        assert "payload/scripts/plugin_launcher.py" in names
        assert "payload/scripts/plugin_bootstrap.py" in names
        assert "payload/pyproject.toml" in names
        assert "payload/src/horizun_pbi_mcp/server.py" in names
        assert any(name.startswith("payload/scripts/locks/") for name in names)
        forbidden = ("outputs/", "backups/", "tests/", ".git/", ".claude/", ".bak-")
        assert not [name for name in names if any(part in name for part in forbidden)]

        bootstrap = bundle.read("pyproject.toml").decode("ascii")
        assert "dependencies = []" in bootstrap
        assert "horizun-pbi-mcp-bootstrap" in bootstrap


def test_extracted_launcher_exposes_installer_tools_without_dependencies(
        tmp_path: Path) -> None:
    bundle_path = _build(tmp_path)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(bundle_path) as bundle:
        bundle.extractall(extracted)

    env = os.environ.copy()
    env["HORIZUN_PBI_PLUGIN_DATA"] = str(tmp_path / "data")
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(extracted / "payload/scripts/plugin_launcher.py")],
        cwd=extracted, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-11-25", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    stdout, stderr = process.communicate(
        "\n".join(json.dumps(item) for item in requests) + "\n", timeout=15)
    assert process.returncode == 0, stderr
    replies = [json.loads(line) for line in stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["name"].endswith("-installer")
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "pbi_install_runtime", "pbi_install_status"]
    assert stderr == ""
