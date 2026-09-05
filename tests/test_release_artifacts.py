"""RELEASE-001 — el flujo de artefactos, simulado entero en local.

`ci.yml` probaba en Windows y `publish-pypi.yml` volvia a construir en Ubuntu y
publicaba *eso*: los bytes que la suite aprobo y los bytes que llegaron a PyPI
eran artefactos distintos, y nada comparaba sus digests. La suite en verde no
decia nada sobre lo publicado; solo lo parecia.

Que la correccion viva en `scripts/release_build.py` y `scripts/release_verify.py`
-y no en pasos `run:` de un YAML- es lo que hace posible este archivo. Un
workflow no se puede ejecutar en una maquina de desarrollo, asi que un pipeline
escrito en YAML solo se prueba publicando, que es exactamente el problema que
RELEASE-001 describe. Como scripts, el flujo completo se corre aqui.

Y no basta con que el camino feliz salga bien: lo que hay que demostrar es que
`release_verify` **para el pipeline** cuando el artefacto no es el que se
construyo. Por eso la mitad de abajo son mutaciones sobre el artefacto ya
construido -un byte del wheel, el asset del instalador, un archivo colado en
dist/, la version- y cada una tiene que hacer saltar la verificacion.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CONSTRUIR = RAIZ / "scripts" / "release_build.py"
VERIFICAR = RAIZ / "scripts" / "release_verify.py"


def _version_declarada() -> str:
    """La version se LEE, no se escribe aqui.

    Estaba puesta a mano —`2.0.0`— y esta prueba se volvio roja en el primer
    cambio de version, acusando al pipeline de un defecto que no tenia. Un
    numero copiado a una prueba es la misma clase de duplicado que el hash del
    instalador copiado a la documentacion.
    """
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)

pytestmark = pytest.mark.packaging


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _verificar(raiz: Path, version: str | None = None):
    args = [sys.executable, str(VERIFICAR), "--dir", str(raiz)]
    if version:
        args += ["--expect-version", version]
    return subprocess.run(args, capture_output=True, text=True, timeout=600,
                          encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def artefacto(tmp_path_factory):
    """Una construccion real, reutilizada por todo el modulo."""
    destino = tmp_path_factory.mktemp("release") / "artefactos"
    res = subprocess.run(
        [sys.executable, str(CONSTRUIR), "--outdir", str(destino)],
        capture_output=True, text=True, timeout=3600,
        encoding="utf-8", errors="replace")
    assert res.returncode == 0, (
        "release_build.py fallo; el pipeline no se puede probar:\n"
        f"{res.stdout[-3000:]}\n{res.stderr[-3000:]}")
    return destino


@pytest.fixture
def copia(artefacto, tmp_path):
    """Copia intacta para mutar sin estropear el artefacto del modulo."""
    destino = tmp_path / "artefactos"
    shutil.copytree(artefacto, destino,
                    ignore=shutil.ignore_patterns(".trabajo"))
    return destino


# ------------------------------------------------------- el camino feliz -----
def test_la_construccion_deja_exactamente_wheel_y_sdist(artefacto):
    """`dist/` es lo que se sube tal cual: nada mas puede vivir ahi."""
    nombres = sorted(p.name for p in (artefacto / "dist").iterdir())
    assert len(nombres) == 2, f"dist/ contiene {nombres}"
    assert any(n.endswith(".whl") for n in nombres)
    assert any(n.endswith(".tar.gz") for n in nombres)


def test_el_asset_del_instalador_es_copia_byte_a_byte(artefacto):
    entrada = json.loads(
        (RAIZ / "scripts" / "downloads_manifest.json").read_text(encoding="utf-8")
    )["downloads"]["instalar.ps1"]
    asset = artefacto / "meta" / entrada["name"]

    assert asset.read_bytes() == (RAIZ / "scripts" / "instalar.ps1").read_bytes(), (
        "el asset publicado no es scripts/instalar.ps1 byte a byte")
    assert _sha256(asset) == entrada["sha256"], (
        "el asset no coincide con el SHA-256 que publica el one-paste")


def test_el_sbom_es_cyclonedx_y_lista_las_dependencias_resueltas(artefacto):
    datos = json.loads((artefacto / "meta" / "sbom.cdx.json").read_text(encoding="utf-8"))
    assert datos["bomFormat"] == "CycloneDX"
    assert datos["specVersion"] >= "1.6"

    nombres = {c["name"].lower() for c in datos["components"]}
    # Las de runtime tienen que estar: un SBOM que no las lista describe otro
    # entorno, no el que resuelve este wheel.
    for dependencia in ("mcp", "psutil", "jsonschema", "openpyxl", "msal"):
        assert dependencia in nombres, (
            f"el SBOM no lista {dependencia}; describe {sorted(nombres)[:10]}...")


def test_sha256sums_cubre_todo_lo_publicable(artefacto):
    declarados = {
        l.split("  ", 1)[1]
        for l in (artefacto / "meta" / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        if l.strip()}
    assert any(r.endswith(".whl") for r in declarados)
    assert any(r.endswith(".tar.gz") for r in declarados)
    assert any(r.endswith(".ps1") for r in declarados)
    assert any(r.endswith("sbom.cdx.json") for r in declarados)
    # El bundle de Claude Desktop se colaba por el hueco de esta lista: si
    # alguien quitara el paso que lo construye, `release_verify` no diria nada
    # -solo se queja de lo que SOBRA- y la release saldria sin el instalador de
    # un clic con todas las pruebas en verde.
    assert any(r.endswith(".mcpb") for r in declarados), (
        f"SHA256SUMS no firma ningun .mcpb; declara {sorted(declarados)}")


def test_el_bundle_de_claude_desktop_se_publica_y_es_el_reproducible(artefacto):
    """MCPB-003. Que exista no basta: tiene que ser EL bundle reproducible.

    `release_build` lo construye desde HEAD con `build_mcpb`. Si alguien lo
    sustituyera por una copia del arbol de trabajo, o por un artefacto de otra
    version, el digest firmado dejaria de corresponder al que cualquiera puede
    reconstruir desde el mismo commit.
    """
    import importlib.util
    import zipfile

    version = _version_declarada()
    bundle = artefacto / "meta" / f"horizun-pbi-mcp-{version}.mcpb"
    assert bundle.is_file(), (
        f"la release no lleva el .mcpb; en meta/ hay "
        f"{sorted(p.name for p in (artefacto / 'meta').iterdir())}")

    # Reconstruido aparte desde el mismo HEAD: mismos bytes.
    spec = importlib.util.spec_from_file_location(
        "_build_mcpb_en_release", RAIZ / "scripts" / "build_mcpb.py")
    constructor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(constructor)
    aparte = artefacto.parent / "reconstruido.mcpb"
    constructor.build(aparte, repo=RAIZ, ref="HEAD")
    assert _sha256(bundle) == _sha256(aparte), (
        "el .mcpb publicado no es el que sale de build_mcpb desde HEAD")

    with zipfile.ZipFile(bundle) as z:
        manifiesto = json.loads(z.read("manifest.json").decode("utf-8"))
    assert manifiesto["version"] == version, (
        "el manifest del bundle declara otra version que la release")

    # Y firmado con SU digest, no con el de otro archivo.
    firmados = dict(
        l.split("  ", 1)[::-1]
        for l in (artefacto / "meta" / "SHA256SUMS").read_text(encoding="ascii").splitlines()
        if l.strip())
    assert firmados[bundle.relative_to(artefacto).as_posix()] == _sha256(bundle)


def _release_build():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_release_build_bajo_prueba", CONSTRUIR)
    modulo = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(RAIZ / "scripts"))
    try:
        spec.loader.exec_module(modulo)
    finally:
        sys.path.remove(str(RAIZ / "scripts"))
    return modulo


def test_un_bundle_de_otra_version_para_la_construccion():
    """RELEASE-003. El nombre sale del arbol; el contenido, de HEAD.

    `build_mcpb` lee `pyproject.toml` de HEAD a proposito, para que un arbol
    sucio no cuele nada. El nombre del asset, en cambio, se formatea con la
    version del disco. Con la version subida y sin confirmar las dos fuentes
    divergen en silencio y la release publicaria `...-2.1.1.mcpb` con un
    manifest que dice 2.1.0.

    Ocurrio de verdad preparando la 2.1.1, y ninguna prueba lo detuvo: se vio
    leyendo el JSON de salida a mano.
    """
    rb = _release_build()
    rb.verificar_version_del_bundle({"version": "9.9.9"}, "9.9.9", "x.mcpb")

    with pytest.raises(SystemExit) as fallo:
        rb.verificar_version_del_bundle(
            {"version": "2.1.0"}, "2.1.1", "horizun-pbi-mcp-2.1.1.mcpb")
    assert "2.1.0" in str(fallo.value) and "2.1.1" in str(fallo.value)


def test_el_bundle_publicado_declara_la_version_de_la_release(artefacto):
    """Y sobre el artefacto real, no solo sobre la funcion aislada."""
    import zipfile

    version = _version_declarada()
    bundle = artefacto / "meta" / f"horizun-pbi-mcp-{version}.mcpb"
    with zipfile.ZipFile(bundle) as z:
        assert json.loads(z.read("manifest.json").decode("utf-8"))["version"] == version


def test_el_bundle_entra_entre_los_assets_publicables(artefacto):
    """Lo firmado y lo que se sube tienen que ser la misma lista."""
    sys.path.insert(0, str(RAIZ / "scripts"))
    try:
        import release_verify
    finally:
        sys.path.remove(str(RAIZ / "scripts"))
    assets = release_verify.assets_publicables(artefacto)
    assert any(n.endswith(".mcpb") for n in assets), (
        f"ningun asset publicable es el bundle: {sorted(assets)}")


def test_el_artefacto_recien_construido_se_verifica(artefacto):
    res = _verificar(artefacto, _version_declarada())
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "digests OK" in res.stdout


def test_el_sbom_se_pide_reproducible():
    """Sin esto, dos construcciones de los mismos bytes dan SBOM distintos.

    Y entonces el digest del SBOM deja de significar nada, porque cambia solo.
    """
    texto = CONSTRUIR.read_text(encoding="utf-8")
    assert "--output-reproducible" in texto


# --------------------------------------------- las mutaciones que deben parar --
def test_un_byte_cambiado_en_el_wheel_para_el_pipeline(copia):
    wheel = next((copia / "dist").glob("*.whl"))
    crudo = bytearray(wheel.read_bytes())
    crudo[-1] ^= 0xFF
    wheel.write_bytes(bytes(crudo))

    res = _verificar(copia)
    assert res.returncode != 0, "un wheel alterado paso la verificacion"
    assert "digest distinto" in res.stdout + res.stderr


def test_un_asset_de_instalador_alterado_para_el_pipeline(copia):
    """Es el caso mas grave: ese archivo se ejecuta en la maquina de la gente."""
    asset = copia / "meta" / "horizun-pbi-mcp-instalar.ps1"
    asset.write_bytes(asset.read_bytes() + b"\n# linea de mas\n")

    res = _verificar(copia)
    assert res.returncode != 0, "un instalador alterado paso la verificacion"


def test_un_mcpb_alterado_para_el_pipeline(copia):
    """El bundle es lo que instala la gente con un doble clic.

    Alterarlo despues de firmarlo es la via por la que se publicaria un
    instalador que nadie probo, bajo un digest que ya no le corresponde.
    """
    bundle = next((copia / "meta").glob("*.mcpb"))
    bundle.write_bytes(bundle.read_bytes() + b"colado")

    res = _verificar(copia)
    assert res.returncode != 0, "un .mcpb alterado paso la verificacion"
    assert "digest distinto" in res.stdout + res.stderr


def test_un_mcpb_sin_firmar_para_el_pipeline(copia):
    """Presente en meta/ pero fuera de SHA256SUMS: publicable sin verificar.

    Es distinto de alterarlo. Aqui los bytes son los buenos; lo que falta es la
    firma, y sin ella nadie comprueba nada al descargarlo.
    """
    sumas = copia / "meta" / "SHA256SUMS"
    lineas = [l for l in sumas.read_text(encoding="ascii").splitlines()
              if l.strip() and not l.endswith(".mcpb")]
    sumas.write_text("\n".join(lineas) + "\n", encoding="ascii", newline="\n")

    res = _verificar(copia)
    assert res.returncode != 0, "se acepto un .mcpb que nadie firmo"
    assert "sin declarar" in res.stdout + res.stderr


def test_un_mcpb_ausente_para_el_pipeline(copia):
    """Firmado y desaparecido: la release saldria sin instalador de un clic."""
    next((copia / "meta").glob("*.mcpb")).unlink()

    res = _verificar(copia)
    assert res.returncode != 0, "se acepto una release sin el bundle firmado"
    assert "ausente" in res.stdout + res.stderr


def test_un_archivo_colado_en_dist_para_el_pipeline(copia):
    """Un publicable que nadie declaro es por donde entraria lo que no se probo."""
    (copia / "dist" / "horizun_pbi_mcp-9.9.9-py3-none-any.whl").write_bytes(b"PK\x03\x04")

    res = _verificar(copia)
    assert res.returncode != 0, "se acepto un archivo sin declarar en dist/"
    assert "sin declarar" in res.stdout + res.stderr


def test_un_publicable_ausente_para_el_pipeline(copia):
    (copia / "meta" / "sbom.cdx.json").unlink()

    res = _verificar(copia)
    assert res.returncode != 0, "se acepto un artefacto al que le falta el SBOM"
    assert "ausente" in res.stdout + res.stderr


def test_un_tag_que_no_coincide_con_los_manifiestos_para_el_pipeline(copia):
    res = _verificar(copia, "9.9.9")
    assert res.returncode != 0, (
        "se habria publicado 9.9.9 desde un arbol que declara otra cosa")
    assert "no todos lo declaran" in res.stdout + res.stderr


def test_el_asset_con_crlf_para_el_pipeline(copia):
    """El modo de fallo silencioso: Windows, un editor y el hash deja de cuadrar."""
    asset = copia / "meta" / "horizun-pbi-mcp-instalar.ps1"
    asset.write_bytes(asset.read_bytes().replace(b"\n", b"\r\n"))

    res = _verificar(copia)
    assert res.returncode != 0, "un asset con CRLF paso la verificacion"


def test_sin_sha256sums_no_hay_artefacto_que_valga(copia):
    (copia / "meta" / "SHA256SUMS").unlink()

    res = _verificar(copia)
    assert res.returncode != 0
    assert "release_build" in res.stdout + res.stderr, (
        "el error no dice de donde tenia que venir el artefacto")
