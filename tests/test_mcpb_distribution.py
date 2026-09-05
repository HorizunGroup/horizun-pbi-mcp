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

import pytest


ROOT = Path(__file__).resolve().parent.parent


def _builder():
    path = ROOT / "scripts" / "build_mcpb.py"
    spec = importlib.util.spec_from_file_location("_build_mcpb_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launcher():
    """El launcher del bundle, como modulo, para leer sus constantes."""
    path = ROOT / "scripts" / "plugin_launcher.py"
    spec = importlib.util.spec_from_file_location("_launcher_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    return module


def _build(tmp_path: Path, name: str = "horizun.mcpb") -> Path:
    output = tmp_path / name
    _builder().build(output, repo=ROOT, ref="HEAD")
    return output


def _extraer(tmp_path: Path) -> Path:
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(_build(tmp_path)) as bundle:
        bundle.extractall(extracted)
    return extracted


def _initialize(version: str) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": version, "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"}}}


def _hablar_con(script: Path, tmp_path: Path, peticiones: list) -> tuple:
    """Lanza ESE launcher y le habla JSON-RPC por stdio."""
    env = os.environ.copy()
    env["HORIZUN_PBI_PLUGIN_DATA"] = str(tmp_path / "data")
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=script.parent, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    entrada = "\n".join(json.dumps(item) for item in peticiones) + "\n"
    stdout, stderr = process.communicate(entrada, timeout=60)
    assert process.returncode == 0, stderr
    return [json.loads(line) for line in stdout.splitlines() if line.strip()], stderr


def _hablar(extracted: Path, tmp_path: Path, peticiones: list) -> tuple:
    """Igual, contra el launcher EXTRAIDO del bundle."""
    return _hablar_con(
        extracted / "payload/scripts/plugin_launcher.py", tmp_path, peticiones)


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


def test_el_entry_point_declarado_existe_dentro_del_bundle(tmp_path: Path) -> None:
    """Un `entry_point` que no esta en el ZIP es una extension que no arranca.

    El manifest lo declara y el constructor empaqueta por listas separadas:
    nada obligaba a que fueran la misma ruta.
    """
    manifest = json.loads(
        (ROOT / "packaging/claude-desktop/manifest.json").read_text(encoding="utf-8"))
    with zipfile.ZipFile(_build(tmp_path)) as bundle:
        nombres = set(bundle.namelist())
    assert manifest["server"]["entry_point"] in nombres
    # Y el comando tiene que apuntar a ESE archivo, no a otro que exista.
    args = manifest["server"]["mcp_config"]["args"]
    apuntado = [a for a in args if a.startswith("${__dirname}/")]
    assert apuntado == ["${__dirname}/" + manifest["server"]["entry_point"]]


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


def test_el_bundle_lleva_lo_confirmado_y_no_el_arbol_de_trabajo(tmp_path: Path) -> None:
    """MCPB-002. El constructor lee de Git a proposito, y nada lo comprobaba.

    Un arbol de trabajo sucio puede tener PBIX reales, outputs o credenciales.
    Si alguien cambiara `git show` por leer del disco, el paquete publicado se
    los llevaria y las pruebas seguirian verdes: solo miraban NOMBRES.

    Se monta un repositorio sintetico con lo justo que el constructor exige,
    se ensucia un archivo del payload en disco, y se comprueba que el bundle
    lleva el blob CONFIRMADO.
    """
    repo = tmp_path / "repo"
    (repo / "scripts" / "locks").mkdir(parents=True)
    (repo / "src" / "horizun_pbi_mcp").mkdir(parents=True)
    (repo / "packaging" / "claude-desktop").mkdir(parents=True)

    manifest = json.loads(
        (ROOT / "packaging/claude-desktop/manifest.json").read_text(encoding="utf-8"))
    (repo / "packaging/claude-desktop/manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        'version = "%s"\n' % manifest["version"], encoding="utf-8")
    (repo / "README.md").write_text("sintetico\n", encoding="utf-8")
    for nombre in ("fetch_libs.py", "fetch_pbir_schemas.py",
                   "fetch_report_validator.py", "plugin_bootstrap.py",
                   "plugin_launcher.py"):
        (repo / "scripts" / nombre).write_text("# marcador\n", encoding="utf-8")
    (repo / "scripts/locks/requirements.lock").write_text("", encoding="utf-8")
    espiado = repo / "src/horizun_pbi_mcp/__init__.py"
    espiado.write_text("CONFIRMADO = 1\n", encoding="utf-8")

    entorno = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=repo, check=True, env=entorno,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Se ensucia DESPUES de confirmar: esto es lo que no debe viajar.
    espiado.write_text("SECRETO_DEL_ARBOL_SUCIO = 1\n", encoding="utf-8")
    (repo / "outputs").mkdir()
    (repo / "outputs" / "informe.pbix").write_bytes(b"datos reales")

    salida = tmp_path / "sintetico.mcpb"
    _builder().build(salida, repo=repo, ref="HEAD")
    with zipfile.ZipFile(salida) as bundle:
        nombres = bundle.namelist()
        contenido = bundle.read("payload/src/horizun_pbi_mcp/__init__.py").decode()
    assert contenido == "CONFIRMADO = 1\n", "empaqueto el arbol sucio"
    assert not [n for n in nombres if "outputs" in n or n.endswith(".pbix")]


def test_extracted_launcher_exposes_installer_tools_without_dependencies(
        tmp_path: Path) -> None:
    extracted = _extraer(tmp_path)
    replies, stderr = _hablar(extracted, tmp_path, [
        _initialize("2025-11-25"),
        # Una notificacion no lleva `id` y no debe producir respuesta: si el
        # launcher contestara, el cliente veria una respuesta sin peticion.
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "pbi_install_status", "arguments": {}}},
    ])
    assert [r.get("id") for r in replies] == [1, 2, 3], (
        "la notificacion produjo respuesta, o falta alguna")
    assert replies[0]["result"]["serverInfo"]["name"].endswith("-installer")
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "pbi_install_runtime", "pbi_install_status"]
    estado = json.loads(replies[2]["result"]["content"][0]["text"])
    assert estado["state"] == "not_installed"
    assert replies[2]["result"]["isError"] is False
    assert stderr == ""


@pytest.mark.parametrize("pedida", ["2024-11-05", "2025-03-26", "2025-06-18"])
def test_el_bootstrap_negocia_la_version_que_pide_el_cliente(
        tmp_path: Path, pedida: str) -> None:
    """MCPB-001. El bootstrap contestaba SIEMPRE con su propia version.

    Este launcher es lo que corre en el PRIMER arranque dentro de Claude
    Desktop, antes de que exista runtime. La especificacion MCP es explicita:
    si el servidor soporta la version pedida DEBE responder esa misma, y el
    cliente que recibe otra deberia desconectarse. El servidor ya instalado
    negocia bien -lo hace el SDK-, asi que las dos mitades de la misma
    extension se comportaban distinto justo en el arranque.

    La prueba anterior pedia `2025-11-25`, que es exactamente la constante
    cableada en el launcher: no podia detectarlo.

    Se habla con el launcher del arbol -no con el del bundle- porque el
    constructor empaqueta desde Git: un arreglo sin confirmar no estaria en el
    ZIP y la prueba mediria el commit anterior. Que el bundle lleva ESTE
    archivo lo cubre `test_el_entry_point_declarado_existe_dentro_del_bundle`.
    """
    replies, _ = _hablar_con(
        ROOT / "scripts/plugin_launcher.py", tmp_path, [_initialize(pedida)])
    assert replies[0]["result"]["protocolVersion"] == pedida


def test_una_version_desconocida_recibe_una_que_si_hablamos(tmp_path: Path) -> None:
    """Si la pedida no se puede hablar, se ofrece una propia; nunca la ajena."""
    replies, _ = _hablar_con(
        ROOT / "scripts/plugin_launcher.py", tmp_path, [_initialize("1999-01-01")])
    devuelta = replies[0]["result"]["protocolVersion"]
    assert devuelta != "1999-01-01"
    assert devuelta in _launcher().VERSIONES_SOPORTADAS


def test_el_launcher_habla_las_mismas_versiones_que_el_servidor_real() -> None:
    """Las dos mitades de la extension tienen que aceptar lo mismo.

    Si el SDK aprende una version nueva y el bootstrap no, el primer arranque
    rechaza lo que el servidor instalado si habla.
    """
    from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

    assert set(_launcher().VERSIONES_SOPORTADAS) == set(SUPPORTED_PROTOCOL_VERSIONS)
