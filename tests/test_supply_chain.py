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


def test_el_readme_declara_el_nombre_del_servidor_para_el_registro():
    """Sin esta linea, `publicar-mcp` falla con 400 y arrastra a la release.

    El registro MCP comprueba la propiedad del paquete leyendo `mcp-name:` en
    el README **del paquete publicado en PyPI**, no en el repositorio. Cuando
    rechazo la 2.0.2 el dano no fue quedarse sin entrada en el registro: como
    `publicar-github-release` depende de `publicar-mcp`, la release no se creo,
    y el bloque de un pegado -que ya apuntaba a /v2.0.2/- quedo en 404 con la
    version ya publicada en PyPI e irreemplazable.

    Se valida contra `.mcp/server.json` en vez de contra una constante para que
    renombrar el servidor rompa aqui y no en mitad de una publicacion.
    """
    nombre = json.loads((RAIZ / ".mcp/server.json").read_text(encoding="utf-8"))["name"]
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {nombre}" in readme, (
        f"README.md no declara 'mcp-name: {nombre}'. El registro MCP la exige "
        "para validar la propiedad del paquete de PyPI, y sin ella la release "
        "de GitHub ni siquiera llega a crearse")


def test_todos_los_sitios_de_version_coinciden_con_branding():
    """El gate del release comprueba esto AL TAGUEAR; aqui se comprueba antes.

    La lista vivia solo en `scripts/release_verify.py`, asi que una divergencia
    se descubria al publicar -o no se descubria-. Y estaba incompleta: le
    faltaba `scripts/plugin_bootstrap.py`, que es con SU version con la que el
    launcher decide si el runtime instalado sirve o hay que reinstalar. Un
    plugin actualizado a 2.0.2 seguiria dando por buena la 2.0.1 en disco. Se
    colaba porque solo lo miraba una prueba de empaquetado, por el handshake.
    """
    import sys

    sys.path.insert(0, str(RAIZ / "scripts"))
    import release_verify

    from horizun_pbi_mcp import branding

    desfasados = {}
    for rel, extraer in release_verify.SITIOS_DE_VERSION.items():
        declarada = extraer((RAIZ / rel).read_text(encoding="utf-8"))
        if declarada != branding.VERSION:
            desfasados[rel] = declarada
    assert not desfasados, (
        f"declaran una version distinta de branding ({branding.VERSION}): "
        f"{desfasados}")


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
    contenido = (RAIZ / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "downloads_manifest.json" in contenido, (
        "release.yml no consume el manifest; URL y hash podrian divergir")
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
    texto = (RAIZ / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "sha256sum" in texto, "no se comprueba el hash del publisher"
    assert "--max-filesize" in texto, "no se acota el tamano de la descarga"
    # El orden importa: verificar DESPUES de extraer no protege de nada.
    assert texto.index("sha256sum") < texto.index("tar xzf"), (
        "se extrae antes de verificar el hash")
    assert "| tar" not in texto, (
        "sigue habiendo una descarga canalizada directamente a tar")
    assert "trap 'rm -rf" in texto, "el temporal no se limpia siempre"


# ------------------------------------------------- instalador y Claude -------
def test_el_instalador_no_ejecuta_el_bootstrap_remoto_de_claude():
    texto = INSTALADOR.read_text(encoding="utf-8")
    assert "claude.ai/install.ps1" not in texto, (
        "instalar.ps1 sigue ofreciendo ejecutar un instalador remoto de "
        "Anthropic; no hay version inmutable ni hash que verificar")


def test_los_documentos_no_recomiendan_ejecutar_el_bootstrap_de_claude():
    for nombre in ("README.md", "docs/INSTALL.md"):
        texto = (RAIZ / nombre).read_text(encoding="utf-8")
        assert "claude.ai/install.ps1 | iex" not in texto, (
            f"{nombre} recomienda ejecutar codigo remoto sin verificar")


# ------------------------------------------------------------- el one-paste --
def test_el_one_paste_ya_no_ejecuta_desde_una_rama():
    """Lo que antes era un pendiente declarado, ahora es una regla.

    La version anterior de esta prueba EXIGIA que `instalar.ps1 | iex` siguiera
    en el README: era un recordatorio de que el defecto seguia ahi y no se podia
    arreglar todavia, porque el hash de un instalador que iba a cambiar no se
    puede fijar. Ya se fijo, asi que el recordatorio se invierte y pasa a
    prohibir lo que antes obligaba a conservar.

    El detalle -los diez requisitos del bloque, servidor HTTP local incluido-
    vive en tests/test_one_paste.py. Aqui queda la guarda de supply chain.
    """
    readme = (RAIZ / "README.md").read_text(encoding="utf-8")
    assert "instalar.ps1 | iex" not in readme, (
        "el README volvio al one-paste que ejecuta lo que devuelva una rama")
    assert (RAIZ / "scripts" / "one_paste.ps1").exists(), (
        "falta la fuente canonica del bloque de un pegado")


def test_el_instalador_publicado_esta_congelado_en_el_manifest():
    """Un ejecutable publicado sin hash declarado es una URL en la que confiar."""
    datos = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entrada = datos["downloads"].get("instalar.ps1")
    assert entrada, "instalar.ps1 no figura en el manifest de descargas"
    assert entrada["source_path"] == "scripts/instalar.ps1"
    assert entrada["eol"] == "lf" and entrada["encoding"] == "ascii"
    assert entrada["bom"] is False
