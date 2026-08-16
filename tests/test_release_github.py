"""RELEASE-004 — la GitHub Release, probada entera sin publicar nada.

El defecto era de omision: `release.yml` publicaba en PyPI y en el MCP Registry
y **no creaba ninguna release**, mientras el bloque de un pegado del README, de
`docs/INSTALL.md` y de la skill descargaba `horizun-pbi-mcp-instalar.ps1` de
`releases/download/v<version>/...`. El camino de instalacion que se le ofrece a
la gente apuntaba a un asset que ningun job creaba.

`tests/test_release_pipeline.py` comprueba la ESTRUCTURA del workflow: que el
job existe, que espera a los cuatro anteriores y que no pide mas permisos de
los que necesita. Aqui se comprueba lo otro, que es donde vive el riesgo de
verdad: **que hace el script cuando el remoto no esta como se esperaba**.

Todo corre contra un cliente falso que se comporta como la API de releases -con
sus assets, sus estados y sus digests-, asi que se pueden ejercitar los caminos
que no se pueden ensayar de otra manera: un rerun sobre una release completa,
un asset que ya existe con otros bytes, una subida a medias, un asset de mas, y
el caso que motiva todo esto: que el instalador publicado no sea el que dice el
manifest, o que su URL no sea la que la gente pega.

El artefacto se fabrica aqui a mano en vez de construirlo de verdad -eso ya lo
hace `tests/test_release_artifacts.py`, y tarda minutos-: lo que se prueba en
este archivo es la logica de publicacion, no la de construccion.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import release_publish as rp                                     # noqa: E402
import release_verify as rv                                      # noqa: E402

MANIFEST = RAIZ / "scripts" / "downloads_manifest.json"
INSTALADOR = RAIZ / "scripts" / "instalar.ps1"


def _entrada() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["downloads"]["instalar.ps1"]


def _version_declarada() -> str:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)


def _sha(crudo: bytes) -> str:
    return hashlib.sha256(crudo).hexdigest()


# ------------------------------------------------------- artefacto sintetico --
@pytest.fixture
def version() -> str:
    return _version_declarada()


@pytest.fixture
def artefacto(tmp_path, version) -> Path:
    """Un artefacto con la misma FORMA que el de `release_build.py`.

    Con el instalador de verdad, porque `release_verify` lo compara contra el
    manifest; y con las notas de la version, porque el cuerpo de la release
    sale de ellas.
    """
    raiz = tmp_path / "artefactos"
    dist, meta = raiz / "dist", raiz / "meta"
    dist.mkdir(parents=True)
    meta.mkdir(parents=True)

    archivos = {
        f"dist/horizun_pbi_mcp-{version}-py3-none-any.whl": b"PK\x03\x04 wheel\n",
        f"dist/horizun_pbi_mcp-{version}.tar.gz": b"\x1f\x8b sdist\n",
        "meta/sbom.cdx.json": b'{"bomFormat":"CycloneDX"}\n',
        f"meta/RELEASE_NOTES_{version}.md": (
            f"# {version}\n\nNotas de esta version.\n".encode()),
        "meta/MIGRACION_1x_A_2.0.md": b"# Migracion\n\nComo adaptarse.\n",
    }
    for rel, crudo in archivos.items():
        (raiz / rel).write_bytes(crudo)

    asset = meta / _entrada()["name"]
    asset.write_bytes(INSTALADOR.read_bytes())

    lineas = [f"{_sha((raiz / rel).read_bytes())}  {rel}" for rel in archivos]
    lineas.append(f"{_sha(asset.read_bytes())}  meta/{asset.name}")
    (meta / "SHA256SUMS").write_text("\n".join(lineas) + "\n",
                                     encoding="ascii", newline="\n")
    (meta / "build.json").write_text('{"resumen": true}\n',
                                     encoding="utf-8", newline="\n")
    return raiz


@pytest.fixture
def tag(version) -> str:
    return f"v{version}"


# ------------------------------------------------------------ cliente falso --
class ClienteFalso:
    """La API de releases, lo justo para que el flujo se pueda ejercitar.

    Registra ademas cada escritura: asi una prueba puede afirmar que un rerun
    **no escribio nada**, que es lo que significa «idempotente» y no «volvio a
    salir 0».
    """

    def __init__(self, repo="HorizunGroup/horizun-pbi-mcp", release=None,
                 assets=None):
        self.repo = repo
        self.release = release
        self.contenido: dict[str, bytes] = dict(assets or {})
        self.meta: dict[str, dict] = {}
        self.escrituras: list[str] = []
        self.descargas: list[str] = []
        for nombre in self.contenido:
            self.meta.setdefault(nombre, {})

    # --- lo que usa release_publish ---
    def release_por_tag(self, tag):
        if self.release is None:
            return None
        return dict(self.release, tag_name=tag)

    def crear_release(self, tag, nombre, cuerpo):
        self.escrituras.append(f"crear:{tag}")
        self.release = {
            "id": 1, "tag_name": tag, "name": nombre, "body": cuerpo,
            "draft": False, "prerelease": False,
            "html_url": f"https://github.com/{self.repo}/releases/tag/{tag}",
            "upload_url": (f"https://uploads.github.com/repos/{self.repo}"
                           "/releases/1/assets{?name,label}"),
        }
        return dict(self.release)

    def assets(self, release):
        salida = []
        for i, (nombre, crudo) in enumerate(sorted(self.contenido.items())):
            extra = self.meta.get(nombre, {})
            salida.append({
                "id": 100 + i, "name": nombre, "size": len(crudo),
                "state": extra.get("state", "uploaded"),
                "browser_download_url": extra.get(
                    "browser_download_url",
                    f"https://github.com/{self.repo}/releases/download/"
                    f"{(self.release or {}).get('tag_name', 'v0')}/{nombre}"),
            })
        return salida

    def subir_asset(self, release, nombre, contenido):
        self.escrituras.append(f"subir:{nombre}")
        self.contenido[nombre] = contenido
        self.meta.setdefault(nombre, {})
        return {"name": nombre}

    def descargar_asset(self, asset):
        self.descargas.append(asset["name"])
        return self.contenido[asset["name"]]


def _cliente_completo(artefacto, tag, **cambios) -> ClienteFalso:
    """Un remoto que ya tiene la release entera y correcta."""
    assets = {n: p.read_bytes()
              for n, p in rv.assets_publicables(artefacto).items()}
    assets.update(cambios)
    cuerpo = rp.cuerpo_de_la_release(artefacto, tag)
    release = {
        "id": 1, "tag_name": tag, "name": tag, "body": cuerpo,
        "draft": False, "prerelease": False,
        "html_url": f"https://github.com/HorizunGroup/horizun-pbi-mcp/releases/tag/{tag}",
        "upload_url": ("https://uploads.github.com/repos/HorizunGroup/"
                       "horizun-pbi-mcp/releases/1/assets{?name,label}"),
    }
    return ClienteFalso(release=release, assets=assets)


# ================================= el listado exacto de assets ===============
def test_los_assets_son_exactamente_los_firmados(artefacto, version):
    """Ni uno mas ni uno menos, y la lista sale de SHA256SUMS, no de un glob."""
    nombres = set(rv.assets_publicables(artefacto))
    assert nombres == {
        f"horizun_pbi_mcp-{version}-py3-none-any.whl",
        f"horizun_pbi_mcp-{version}.tar.gz",
        "horizun-pbi-mcp-instalar.ps1",
        "sbom.cdx.json",
        f"RELEASE_NOTES_{version}.md",
        "MIGRACION_1x_A_2.0.md",
        "SHA256SUMS",
    }, f"la lista de assets cambio: {sorted(nombres)}"


def test_el_minimo_exigido_esta_cubierto(artefacto):
    nombres = set(rv.assets_publicables(artefacto))
    assert "horizun-pbi-mcp-instalar.ps1" in nombres, "sin instalador, 404"
    assert "SHA256SUMS" in nombres, "sin sumas nadie puede comprobar un asset suelto"
    assert any(n.startswith("sbom") for n in nombres)
    assert any(n.endswith(".whl") for n in nombres)
    assert any(n.endswith(".tar.gz") for n in nombres)
    assert any(n.startswith("RELEASE_NOTES") for n in nombres)
    assert any(n.startswith("MIGRACION") for n in nombres)


def test_un_publicable_sin_firmar_no_llega_a_publicarse(artefacto, tag):
    """Lo que no esta en SHA256SUMS no se publica: se para el pipeline."""
    (artefacto / "meta" / "colado.ps1").write_bytes(b"# esto no lo firmo nadie\n")
    with pytest.raises(SystemExit, match="sin declarar"):
        rp.publicar(artefacto, tag, ClienteFalso())


# ===================================== el camino feliz, desde cero ===========
def test_desde_cero_crea_la_release_y_sube_todo(artefacto, tag):
    cliente = ClienteFalso()
    resumen = rp.publicar(artefacto, tag, cliente)

    assert resumen["release_creada"] is True
    assert resumen["draft"] is False, "una release en borrador no sirve el asset"
    assert set(resumen["assets"]) == set(rv.assets_publicables(artefacto))
    assert all(v == "subido" for v in resumen["assets"].values())
    assert f"crear:{tag}" in cliente.escrituras


def test_lo_publicado_es_byte_a_byte_lo_firmado(artefacto, tag):
    cliente = ClienteFalso()
    rp.publicar(artefacto, tag, cliente)

    for nombre, ruta in rv.assets_publicables(artefacto).items():
        assert cliente.contenido[nombre] == ruta.read_bytes(), (
            f"{nombre} se publico con otros bytes")


def test_se_verifican_los_digests_DESPUES_de_subir(artefacto, tag):
    """Que el POST no diera error no dice que al otro lado este lo que mandamos."""
    cliente = ClienteFalso()
    rp.publicar(artefacto, tag, cliente)

    esperados = set(rv.assets_publicables(artefacto))
    assert esperados <= set(cliente.descargas), (
        "hay assets que se subieron y nunca se releyeron: "
        f"{sorted(esperados - set(cliente.descargas))}")


def test_un_asset_que_el_remoto_devuelve_cambiado_para_la_publicacion(
        artefacto, tag):
    """El caso que solo se ve releyendo: se sube bien y al otro lado hay otra cosa."""
    cliente = ClienteFalso()
    original = cliente.subir_asset

    def sabotear(release, nombre, contenido):
        if nombre == "sbom.cdx.json":
            contenido = contenido + b"\n"
        return original(release, nombre, contenido)

    cliente.subir_asset = sabotear
    with pytest.raises(SystemExit, match="quedo publicado con otro digest"):
        rp.publicar(artefacto, tag, cliente)


# ===================================== idempotencia ante un rerun ============
def test_un_rerun_sobre_una_release_completa_termina_verde_sin_escribir(
        artefacto, tag):
    """«Idempotente» es que no escriba, no que vuelva a salir 0."""
    cliente = _cliente_completo(artefacto, tag)
    resumen = rp.publicar(artefacto, tag, cliente)

    assert resumen["release_creada"] is False
    assert set(resumen["assets"].values()) == {"ya estaba, identico"}
    assert cliente.escrituras == [], (
        f"un rerun escribio: {cliente.escrituras}")


def test_un_rerun_a_medias_completa_solo_lo_que_falta(artefacto, tag):
    cliente = _cliente_completo(artefacto, tag)
    del cliente.contenido["sbom.cdx.json"]
    resumen = rp.publicar(artefacto, tag, cliente)

    assert resumen["assets"]["sbom.cdx.json"] == "subido"
    assert cliente.escrituras == ["subir:sbom.cdx.json"], (
        f"se toco algo mas que lo que faltaba: {cliente.escrituras}")


# ===================================== lo que tiene que PARAR ================
def test_un_asset_que_ya_existe_con_otros_bytes_no_se_reemplaza(artefacto, tag):
    """El nucleo de la regla: un tag publicado es inmutable.

    Alguien pudo descargar ese archivo. Reescribirlo bajo el mismo nombre y el
    mismo tag es como dos personas «instalan la misma version» y tienen cosas
    distintas.
    """
    cliente = _cliente_completo(
        artefacto, tag, **{"sbom.cdx.json": b'{"bomFormat":"otro"}\n'})

    with pytest.raises(SystemExit, match="con OTROS bytes"):
        rp.publicar(artefacto, tag, cliente)
    assert cliente.escrituras == [], "se escribio antes de parar"
    assert cliente.contenido["sbom.cdx.json"] == b'{"bomFormat":"otro"}\n', (
        "el asset ajeno se piso; la regla es no tocarlo, ni siquiera al fallar")


def test_el_instalador_que_ya_existe_distinto_es_el_caso_grave(artefacto, tag):
    """Ese archivo se ejecuta en la maquina de la gente."""
    cliente = _cliente_completo(
        artefacto, tag,
        **{_entrada()["name"]: INSTALADOR.read_bytes() + b"\n# de mas\n"})

    with pytest.raises(SystemExit, match="con OTROS bytes"):
        rp.publicar(artefacto, tag, cliente)


def test_una_subida_a_medias_no_se_da_por_buena(artefacto, tag):
    cliente = _cliente_completo(artefacto, tag)
    cliente.meta["sbom.cdx.json"] = {"state": "starter"}

    with pytest.raises(SystemExit, match="subida a medias"):
        rp.publicar(artefacto, tag, cliente)


def test_un_asset_de_mas_en_la_release_para_la_publicacion(artefacto, tag):
    """Un publicable fuera de SHA256SUMS es por donde entra lo que nadie vio."""
    cliente = _cliente_completo(artefacto, tag, **{"extra.exe": b"MZ"})

    with pytest.raises(SystemExit, match="assets que nadie firmo"):
        rp.publicar(artefacto, tag, cliente)


def test_una_release_en_borrador_no_se_da_por_publicada(artefacto, tag):
    cliente = _cliente_completo(artefacto, tag)
    cliente.release["draft"] = True

    with pytest.raises(SystemExit, match="BORRADOR"):
        rp.publicar(artefacto, tag, cliente)


def test_una_release_con_otras_notas_no_se_reescribe(artefacto, tag):
    cliente = _cliente_completo(artefacto, tag)
    cliente.release["body"] = "notas de otra cosa"

    with pytest.raises(SystemExit, match="OTRAS notas"):
        rp.publicar(artefacto, tag, cliente)


def test_el_cuerpo_con_crlf_no_rompe_la_idempotencia(artefacto, tag):
    """GitHub devuelve el cuerpo con CRLF aunque se envie con LF."""
    cliente = _cliente_completo(artefacto, tag)
    cliente.release["body"] = cliente.release["body"].replace("\n", "\r\n")

    resumen = rp.publicar(artefacto, tag, cliente)
    assert resumen["release_creada"] is False
    assert cliente.escrituras == []


# ============================ el instalador remoto, que es el que se pega ====
def test_el_instalador_publicado_tiene_el_sha_del_manifest(artefacto, tag):
    cliente = ClienteFalso()
    resumen = rp.publicar(artefacto, tag, cliente)
    assert resumen["instalador"]["sha256"] == _entrada()["sha256"]


def test_si_la_url_publicada_no_es_la_del_manifest_se_para(artefacto, tag):
    """El defecto exacto del one-paste: la URL que se pega y el asset no cuadran."""
    cliente = ClienteFalso()
    original = cliente.assets

    def con_otra_url(release):
        salida = original(release)
        for a in salida:
            if a["name"] == _entrada()["name"]:
                a["browser_download_url"] = (
                    "https://github.com/HorizunGroup/horizun-pbi-mcp/releases/"
                    "download/v0.0.0/horizun-pbi-mcp-instalar.ps1")
        return salida

    cliente.assets = con_otra_url
    with pytest.raises(SystemExit, match="la URL publicada no es la que pega"):
        rp.publicar(artefacto, tag, cliente)


def test_la_url_del_manifest_apunta_al_tag_que_se_publica(tag):
    """Sin esto, se publica v2.0.1 y el one-paste sigue leyendo el tag anterior."""
    entrada = _entrada()
    assert f"/download/{tag}/" in entrada["url"], (
        f"el manifest descarga de {entrada['url']} y se publica {tag}")
    assert entrada["url"].endswith("/" + entrada["name"])


# ============================ la version, atada al tag =======================
def test_publicar_un_tag_que_el_arbol_no_declara_se_para(artefacto):
    with pytest.raises(SystemExit, match="no todos lo declaran"):
        rp.publicar(artefacto, "v9.9.9", ClienteFalso())


def test_sin_notas_de_esa_version_no_hay_cuerpo_que_publicar(artefacto, tag,
                                                             version):
    (artefacto / "meta" / f"RELEASE_NOTES_{version}.md").unlink()
    # Deja de estar firmado, asi que la verificacion lo detiene antes.
    with pytest.raises(SystemExit):
        rp.publicar(artefacto, tag, ClienteFalso())
