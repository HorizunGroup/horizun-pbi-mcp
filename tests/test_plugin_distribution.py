"""Contrato de distribución directa como plugin de Codex y Claude."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_ningun_fichero_publicado_lleva_acentos_rotos():
    """Mojibake: UTF-8 leido como ANSI. Se publico de verdad en la 1.5.2.

    Un bump de version hecho con PowerShell 5.1 (`Get-Content -Raw` lee con la
    codepage ANSI y `WriteAllText` escribe UTF-8) convirtio 'auditoria' con
    tilde en 'auditorA-a' dentro de la descripcion de los DOS plugin.json y de
    los mensajes de instalacion que la persona ve en pantalla. Ningun test lo
    vio porque todos comprueban el contenido, no como esta codificado.

    Se revisan los ficheros que el usuario acaba leyendo: manifiestos, textos
    del instalador y documentacion de portada.
    """
    sospechosas = ("Ã", "â€", "Â\xa0", "ï»¿")
    objetivos = [
        ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json",
        ".mcp/server.json", "scripts/plugin_bootstrap.py",
        "scripts/plugin_launcher.py", "README.md", "docs/INSTALL.md",
        "CHANGELOG.md",
    ]
    sucios = []
    for relativo in objetivos:
        ruta = REPO / relativo
        if not ruta.exists():
            continue
        texto = ruta.read_text(encoding="utf-8")
        for aguja in sospechosas:
            if aguja in texto:
                linea = next((n for n, l in enumerate(texto.splitlines(), 1)
                              if aguja in l), 0)
                sucios.append(f"{relativo}:{linea} contiene {aguja!r}")
    assert not sucios, (
        "acentos rotos por codificacion (edita con UTF-8 explicito, nunca con "
        "Get-Content/WriteAllText de PowerShell):\n  " + "\n  ".join(sucios))


def test_el_lanzador_cmd_esquiva_el_alias_de_la_store():
    """launch.cmd debe resolver un Python REAL y explicar el fallo si no hay."""
    contenido = (REPO / "scripts/launch.cmd").read_text(encoding="ascii")
    assert "plugin_launcher.py" in contenido, "debe delegar en el launcher real"
    assert "WindowsApps" in contenido, "debe filtrar el alias de la Store"
    assert "1>&2" in contenido, "el remedio sale por stderr, no por el stdio MCP"
    # Un candidato se acepta por CORRER, no por existir: un py.exe huerfano
    # supera cualquier prueba de presencia y luego muere con codigo 103.
    assert "-c " in contenido, "cada candidato se prueba ejecutandolo"
    # Sin CALL, un candidato .bat/.cmd (shims de pyenv-win, wrappers
    # corporativos) se lleva el control y no lo devuelve: el lanzador moria
    # mudo justo donde promete no hacerlo.
    assert "call %*" in contenido, "los candidatos se invocan con call"
    assert "call %PYREAL%" in contenido, "el arranque final tambien usa call"


def test_el_piso_de_version_del_lanzador_sigue_al_de_pyproject():
    """Si pyproject sube el minimo, el lanzador tiene que enterarse.

    El lanzador comprueba la version ANTES de arrancar el servidor; si se
    queda atras, un Python demasiado viejo pasa el filtro y falla mucho mas
    tarde, dentro del servidor, con un error que no menciona la version.
    """
    import re

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    declarado = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"', pyproject)
    assert declarado, "pyproject.toml debe declarar requires-python"
    esperado = (int(declarado.group(1)), int(declarado.group(2)))

    lanzador = (REPO / "scripts/launch.cmd").read_text(encoding="ascii")
    usado = re.search(r"version_info\[:2\]\s*>=\s*\((\d+),\s*(\d+)\)", lanzador)
    assert usado, "launch.cmd debe comprobar la version del candidato"
    assert (int(usado.group(1)), int(usado.group(2))) == esperado, (
        f"launch.cmd exige {usado.group(1)}.{usado.group(2)} y pyproject "
        f"{esperado[0]}.{esperado[1]}")


def _correr_lanzador(tmp_path, candidatos: dict[str, int]) -> subprocess.CompletedProcess:
    """Corre launch.cmd viendo SOLO los candidatos dados (Windows).

    `candidatos` mapea nombre -> codigo de salida del shim, para simular las
    formas medidas de "tener python y no tenerlo". Tambien se vacian las
    carpetas conocidas, o el Python real de la maquina rescataria la prueba.
    """
    binarios = tmp_path / "bin"
    binarios.mkdir(parents=True)
    for nombre, codigo in candidatos.items():
        (binarios / f"{nombre}.cmd").write_text(
            f"@echo off\nexit /b {codigo}\n", encoding="ascii")
    vacio = tmp_path / "sin-python"
    vacio.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = str(binarios)
    env["LOCALAPPDATA"] = str(vacio)
    env["ProgramFiles"] = str(vacio)
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    return subprocess.run(
        ["cmd", "/d", "/c", str(REPO / "scripts/launch.cmd")],
        env=env, input="", capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(os.name != "nt", reason="launch.cmd es el arranque de Windows")
def test_el_lanzador_distingue_python_viejo_de_python_ausente(tmp_path):
    """Los dos fallos tienen remedios distintos y no pueden dar el mismo texto."""
    viejo = _correr_lanzador(tmp_path / "viejo", {"python": 9, "py": 9})
    assert viejo.returncode == 1
    assert "anterior a 3.10" in viejo.stderr, viejo.stderr
    assert viejo.stdout == "", "un fallo no puede ensuciar el stdio MCP"

    ausente = _correr_lanzador(tmp_path / "ausente", {})
    assert ausente.returncode == 1
    assert "No hay un Python real" in ausente.stderr, ausente.stderr


@pytest.mark.skipif(os.name != "nt", reason="launch.cmd es el arranque de Windows")
def test_el_lanzador_no_se_queda_con_un_py_huerfano(tmp_path):
    """py.exe sobrevive a la desinstalacion de Python: existe y no sirve.

    Antes se elegia por existir, se moria con 'Python 3.x not found' (103) y
    el plugin quedaba mudo. Ahora ese candidato se descarta como cualquier
    otro que no corre, y el mensaje es el de siempre, con su remedio.
    """
    huerfano = _correr_lanzador(tmp_path, {"py": 103})
    assert huerfano.returncode == 1
    assert "No hay un Python real" in huerfano.stderr, huerfano.stderr
    assert "103" not in huerfano.stderr, "el codigo crudo no es un mensaje util"


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
