"""INSTALL-003: nada ejecutable se descarga desde una referencia mutable.

Una URL con `latest` o una rama no es inmutable: los bytes que sirve pueden
cambiar sin que cambie el enlace. HTTPS garantiza QUIEN te lo da, no QUE te da.
Por eso todo ejecutable descargado necesita version exacta y SHA-256 verificado
antes de extraerlo o correrlo.

Ninguna prueba de este archivo descarga nada: leen los archivos versionados.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MANIFEST = RAIZ / "scripts" / "downloads_manifest.json"
MARKETPLACES = (RAIZ / ".claude-plugin" / "marketplace.json",
                RAIZ / ".agents" / "plugins" / "marketplace.json")
WORKFLOWS = sorted((RAIZ / ".github" / "workflows").glob("*.yml"))
INSTALADOR = RAIZ / "scripts" / "instalar.ps1"

#: Archivos que DOCUMENTAN el problema o el pasado, no lo ejecutan.
HISTORICOS = ("CHANGELOG.md", "llms.txt", "docs/audits/")


def version_del_proyecto() -> str:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)


# ------------------------------------------------------------ marketplaces ---
@pytest.mark.parametrize("ruta", MARKETPLACES, ids=lambda p: p.parent.name)
def test_marketplace_no_apunta_a_una_rama(ruta):
    """Una rama instala lo que haya HOY en ella; un tag, bytes concretos."""
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    refs = re.findall(r'"ref"\s*:\s*"([^"]+)"', ruta.read_text(encoding="utf-8"))
    assert refs, f"{ruta} no declara ningun ref"
    for ref in refs:
        assert ref not in ("main", "master", "develop", "HEAD", "latest"), (
            f"{ruta} instala desde la rama movil '{ref}'")
        assert ref.startswith("v"), f"{ruta} usa un ref no versionado: {ref}"
    assert datos  # el JSON parsea


@pytest.mark.parametrize("ruta", MARKETPLACES, ids=lambda p: p.parent.name)
def test_marketplace_declara_la_version_del_proyecto(ruta):
    """Si divergen, el marketplace instala codigo distinto del que se publica."""
    esperado = "v" + version_del_proyecto()
    refs = set(re.findall(r'"ref"\s*:\s*"([^"]+)"', ruta.read_text(encoding="utf-8")))
    assert refs == {esperado}, (
        f"{ruta} apunta a {refs} y el proyecto es {esperado}")


# ---------------------------------------------------------------- manifest ---
def test_el_manifest_no_admite_url_mutable_ni_hash_vacio():
    datos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entradas = datos["downloads"]
    assert entradas, "el manifest esta vacio"
    for nombre, e in entradas.items():
        assert re.fullmatch(r"[0-9a-f]{64}", e.get("sha256", "")), (
            f"{nombre} no declara un SHA-256 valido")
        assert e.get("version"), f"{nombre} no declara version"
        assert isinstance(e.get("max_bytes"), int) and e["max_bytes"] > 0, (
            f"{nombre} no declara tamano maximo")
        url = e.get("url", "")
        assert url.startswith("https://"), f"{nombre} no usa https"
        for movil in ("/latest/", "/main/", "/master/", "/HEAD/"):
            assert movil not in url, f"{nombre} usa una URL mutable: {url}"
        assert e["version"] in url, (
            f"la URL de {nombre} no contiene su version: {url}")
        assert e.get("official_source") and e.get("purpose")


def test_el_workflow_y_el_manifest_no_divergen():
    """Un hash en dos sitios es un hash que acabara desincronizado."""
    contenido = (RAIZ / ".github/workflows/publish-mcp.yml").read_text(encoding="utf-8")
    assert "downloads_manifest.json" in contenido, (
        "publish-mcp no consume el manifest; URL y hash podrian divergir")
    datos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pub = datos["downloads"]["mcp-publisher"]
    assert pub["url"] not in contenido, (
        "la URL esta duplicada en el workflow ademas de en el manifest")


# --------------------------------------------------------------- workflows ---
def test_ningun_workflow_descarga_desde_latest():
    for w in WORKFLOWS:
        texto = w.read_text(encoding="utf-8")
        assert "releases/latest" not in texto, (
            f"{w.name} descarga desde una URL mutable")


def test_el_publisher_se_verifica_antes_de_extraer():
    texto = (RAIZ / ".github/workflows/publish-mcp.yml").read_text(encoding="utf-8")
    assert "sha256sum" in texto, "no se comprueba el hash del publisher"
    assert "--max-filesize" in texto, "no se acota el tamano de la descarga"
    # El orden importa: verificar DESPUES de extraer no protege de nada.
    assert texto.index("sha256sum") < texto.index("tar xzf"), (
        "se extrae antes de verificar el hash")
    assert "| tar" not in texto, (
        "sigue habiendo una descarga canalizada directamente a tar")
    assert "trap 'rm -rf" in texto, "el temporal no se limpia siempre"


# ------------------------------------------------------------- pendientes ----
def test_el_one_paste_sigue_pendiente_y_esta_declarado():
    """Honestidad sobre lo que AUN no esta arreglado.

    El one-paste `irm .../main/instalar.ps1 | iex` sigue vivo: su hash solo se
    puede fijar cuando `instalar.ps1` sea definitivo, en la fase siguiente.
    Esta prueba existe para que el pendiente no se olvide ni se disfrace.
    """
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "instalar.ps1 | iex" in readme, (
        "si el one-paste ya se arreglo, retira esta prueba y cierra "
        "INSTALL-003 en la matriz")
