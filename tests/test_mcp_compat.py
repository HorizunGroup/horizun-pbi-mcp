"""Fase J2 — vigilancia de la unica dependencia sobre API privada de `mcp`.

`server.build_server()` hace:

    mcp._mcp_server.version = branding.VERSION

porque `FastMCP.__init__` no acepta `version`. Es API privada. Si una version
futura de `mcp` la retira o la renombra, el servidor seguiria arrancando pero
anunciaria en el handshake la version de la LIBRERIA en lugar de la del
producto, y nadie se enteraria.

Estas pruebas convierten ese fallo silencioso en uno ruidoso, y documentan por
que `mcp` esta acotada por arriba en pyproject.toml.
"""
from __future__ import annotations

import asyncio
import inspect
import re
from pathlib import Path

import pytest

from horizun_pbi_mcp import branding
from mcp.server.fastmcp import FastMCP
from horizun_pbi_mcp.server import build_server

REPO = Path(__file__).resolve().parent.parent


def test_fastmcp_sigue_sin_aceptar_version():
    """Si algun dia lo acepta, hay que dejar de tocar el atributo privado."""
    parametros = inspect.signature(FastMCP.__init__).parameters
    assert "version" not in parametros, (
        "FastMCP.__init__ ya acepta 'version': migra server.build_server() a la "
        "API publica y quita el acceso a mcp._mcp_server.version.")


def test_el_atributo_privado_sigue_existiendo():
    servidor = FastMCP("sonda")
    assert hasattr(servidor, "_mcp_server"), (
        "FastMCP ya no expone _mcp_server: el servidor no puede fijar su "
        "version y anunciaria la de la libreria mcp.")
    assert hasattr(servidor._mcp_server, "version"), (
        "El servidor interno ya no tiene 'version'.")


def test_el_gestor_de_tools_sigue_permitiendo_anotarlas():
    """La segunda dependencia sobre API privada: `mcp._tool_manager`.

    `server._anotar_riesgo()` recorre ahi las tools ya registradas para
    declararles su clase de riesgo, en vez de repetir el argumento en las 118
    firmas. Si una version futura de `mcp` renombra el gestor o deja de
    devolver los objetos vivos, el servidor arrancaria igual pero las tools
    saldrian SIN annotations: el cliente perderia la senal de que distingue una
    lectura de un borrado, y nadie se enteraria.
    """
    servidor = FastMCP("sonda")

    @servidor.tool()
    def pbi_sonda() -> dict:
        """Tool de prueba."""
        return {}

    assert hasattr(servidor, "_tool_manager"), (
        "FastMCP ya no expone _tool_manager: migra server._anotar_riesgo() a "
        "la API publica.")
    registradas = servidor._tool_manager.list_tools()
    assert registradas, "_tool_manager.list_tools() ya no devuelve las tools"
    assert hasattr(registradas[0], "annotations"), (
        "las tools del gestor ya no tienen 'annotations'")

    # Los objetos tienen que ser los VIVOS: si list_tools() empezara a
    # devolver copias, anotarlas no tendria ningun efecto sobre el handshake.
    registradas[0].annotations = None
    from mcp.types import ToolAnnotations

    registradas[0].annotations = ToolAnnotations(readOnlyHint=True)
    publicadas = asyncio.run(servidor.list_tools())
    assert publicadas[0].annotations is not None, (
        "anotar lo que devuelve _tool_manager.list_tools() ya no llega al "
        "handshake: _anotar_riesgo() no surtiria efecto.")


def test_el_servidor_anuncia_la_version_del_producto():
    servidor = build_server()
    assert servidor._mcp_server.version == branding.VERSION


def test_las_88_tools_se_registran_con_esta_version_de_mcp():
    tools = asyncio.run(build_server().list_tools())
    assert len(tools) >= 88
    assert all(t.name.startswith("pbi_") for t in tools)


def test_la_cota_de_mcp_esta_declarada():
    """La cota superior no es decorativa: sin ella el aviso no serviria.

    La busqueda exige un especificador de version pegado a 'mcp' para no
    engancharse con la entrada 'mcp' de `keywords`, que es solo una etiqueta
    de PyPI y no declara ninguna cota.
    """
    texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    linea = re.search(r'"mcp(?:>=|==|~=|<)[^"]*"', texto)
    assert linea, "no encuentro la dependencia 'mcp' en pyproject.toml"
    assert "<2" in linea.group(0), (
        f"la dependencia mcp debe estar acotada por arriba: {linea.group(0)}")


def test_hay_licencia_y_coincide_con_pyproject():
    """La licencia publica debe ser Apache-2.0 completa y coherente."""
    licencia = REPO / "LICENSE"
    assert licencia.exists(), "falta el archivo LICENSE"
    texto = licencia.read_text(encoding="utf-8")
    assert "Apache License" in texto
    assert "Version 2.0, January 2004" in texto
    assert "https://www.apache.org/licenses/" in texto

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "Apache-2.0"' in pyproject
    assert "License :: OSI Approved" not in pyproject, (
        "el classifier de licencia esta obsoleto; SPDX es la fuente canonica")


def test_la_licencia_aclara_que_las_dll_no_se_redistribuyen():
    """J5: las DLL de Microsoft no van en el repositorio ni en el paquete."""
    texto = (REPO / "NOTICE").read_text(encoding="utf-8")
    assert "NO redistribuye" in texto or "does NOT redistribute" in texto
    assert "fetch_libs" in texto

    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert any(l.strip().rstrip("/") == "libs" for l in gitignore.splitlines()), (
        "libs/ debe estar ignorado: no se versionan DLL de terceros")


# ============================ coherencia de version para la release publica ===
def test_la_version_visible_y_la_de_pep440_son_la_misma():
    """`1.0.0-rc.1` y `1.0.0rc1` deben describir la MISMA release.

    Publicar un paquete que por dentro dice `1.0.0` estable mientras el tag de
    Git dice `-rc.1` engana sobre la madurez de lo que se instala.
    """
    from packaging.version import Version

    assert Version(branding.VERSION) == Version(branding.VERSION_PEP440), (
        f"{branding.VERSION!r} y {branding.VERSION_PEP440!r} no son la misma "
        "version")


def test_pyproject_declara_la_version_pep440_de_branding():
    import re

    texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', texto, re.M)
    assert m, "no encuentro `version` en pyproject.toml"
    assert m.group(1) == branding.VERSION_PEP440, (
        f"pyproject dice {m.group(1)!r} y branding {branding.VERSION_PEP440!r}")


def test_el_readme_anuncia_la_version_visible():
    texto = (REPO / "README.md").read_text(encoding="utf-8")
    assert branding.VERSION in texto, (
        f"el README no menciona {branding.VERSION}")


def test_el_changelog_tiene_la_entrada_de_esta_version():
    texto = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{branding.VERSION}]" in texto, (
        f"el CHANGELOG no tiene una entrada para {branding.VERSION}")


def test_el_manifiesto_del_registro_mcp_declara_esta_version():
    """`.mcp/server.json` es lo que se publica al registro oficial al taguear.

    Si se queda atras, el workflow del tag reenvia una version ya publicada y
    el registro responde 400 `cannot publish duplicate version`: la release
    sale en PyPI pero el registro sigue anunciando la anterior. Aqui se cazaba
    ya la coherencia de pyproject, README y CHANGELOG; este archivo faltaba.
    """
    import json

    datos = json.loads((REPO / ".mcp" / "server.json").read_text(encoding="utf-8"))
    assert datos["version"] == branding.VERSION, (
        f".mcp/server.json dice {datos['version']!r} y branding "
        f"{branding.VERSION!r}")

    # El bloque `packages` es lo que le dice al registro DE DONDE se instala.
    # Lleva su propia version, y una version anidada que nadie vigile se queda
    # atras en silencio: el registro anunciaria la 2.0.1 apuntando al paquete
    # de PyPI de una version anterior, que es peor que no anunciar nada.
    paquetes = datos.get("packages") or []
    assert paquetes, ".mcp/server.json sin `packages`: el registro no diria como instalarlo"
    pypi = [p for p in paquetes if p.get("registryType") == "pypi"]
    assert len(pypi) == 1, f"se espera exactamente un paquete pypi, hay {len(pypi)}"
    assert pypi[0]["identifier"] == "horizun-pbi-mcp", pypi[0]
    assert pypi[0]["version"] == branding.VERSION, (
        f"packages[pypi].version dice {pypi[0]['version']!r} y branding "
        f"{branding.VERSION!r}")
    assert pypi[0]["transport"]["type"] == "stdio", pypi[0]
