"""INSTALL-006 — publicar esquemas y validador sin dejar mezclas a medias.

Los dos instaladores auxiliares escribian ENCIMA del directorio vivo:

  - `fetch_pbir_schemas.instalar()` descargaba a un temporal, verificaba los
    hashes y despues copiaba archivo por archivo sobre `<cache>/schemas/pbir`.
    Si la copia se cortaba a la mitad quedaba una mezcla de esquemas viejos y
    nuevos: un estado que nadie ha probado nunca y que no se distingue a
    simple vista de uno bueno. Y los archivos que dejaban de estar en el
    manifiesto se quedaban ahi para siempre, porque copiar no borra.

  - `fetch_report_validator.instalar()` ejecutaba `npm install --prefix` sobre
    el directorio vivo. `npm install` escribe cientos de archivos y no es
    atomico: interrumpirlo deja el validador anterior mezclado con medio
    validador nuevo. Un CLI a medias es peor que ninguno, porque existe y
    arranca.

Lo que se comprueba aqui no es el camino bueno -ese es el facil- sino que
**despues de cada fallo el destino anterior sigue byte a byte como estaba**, y
que no queda staging, temporal ni journal sin resolver. La inyeccion recorre el
primer archivo, el del medio y el ultimo a proposito: un rollback probado solo
en el ultimo paso no ha demostrado nada sobre los anteriores.

Nada de esto toca la red ni la instalacion real: se sustituye la descarga y se
trabaja siempre bajo `tmp_path`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))


def _cargar(nombre: str):
    """El modulo del PAQUETE. `scripts/` solo conserva un envoltorio.

    La logica se movio a `horizun_pbi_mcp.completado` para que viaje en el
    wheel (INSTALL-005): una instalacion por `pip` no tiene `scripts/`. Se carga
    por ruta y con nombre unico porque varias pruebas sustituyen constantes del
    modulo y una copia compartida las mezclaria.
    """
    modulo = {"fetch_pbir_schemas": "esquemas",
              "fetch_report_validator": "validador",
              "fetch_libs": "libs"}.get(nombre, nombre)
    spec = importlib.util.spec_from_file_location(
        f"_{modulo}_{uuid.uuid4().hex}",
        RAIZ / "src" / "horizun_pbi_mcp" / "completado" / f"{modulo}.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def esquemas():
    return _cargar("fetch_pbir_schemas")


@pytest.fixture
def validador():
    return _cargar("fetch_report_validator")


# --------------------------------------------------------------- utilidades --
def _manifiesto(documentos: dict[str, bytes]) -> dict:
    return {
        "manifest_version": 1,
        "documents": [
            {"url": f"https://developer.microsoft.com/json-schemas/fabric/{n}",
             "file": n, "sha256": hashlib.sha256(d).hexdigest(),
             "bytes": len(d), "root": True}
            for n, d in documentos.items()],
    }


def _huella(carpeta: Path) -> dict:
    if not carpeta.is_dir():
        return {}
    return {str(p.relative_to(carpeta)): p.read_bytes()
            for p in sorted(carpeta.rglob("*")) if p.is_file()}


def _restos(raiz: Path) -> list[str]:
    """Staging, journals o cuarentenas que hayan quedado sin resolver."""
    if not raiz.is_dir():
        return []
    return sorted(p.name for p in raiz.iterdir()
                  if p.name.startswith((".staging-", ".promotion"))
                  or p.name.endswith(".tmp"))


# ============================================================================
# Esquemas PBIR
# ============================================================================
DOCS = {"a.json": b'{"a":1}', "b.json": b'{"b":2}', "c.json": b'{"c":3}'}


def test_una_instalacion_limpia_publica_todo(esquemas, tmp_path, monkeypatch):
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    destino = tmp_path / "schemas" / "pbir"

    r = esquemas.instalar(_manifiesto(DOCS), destino)

    assert r["installed"] == 3
    assert _huella(destino).keys() == {*DOCS, "_manifest.json"}
    assert _restos(destino.parent) == []


@pytest.mark.parametrize("cual", ["a.json", "b.json", "c.json"],
                         ids=["primer-archivo", "archivo-del-medio",
                              "ultimo-archivo"])
def test_si_falla_una_descarga_el_destino_anterior_no_cambia(
        esquemas, tmp_path, monkeypatch, cual):
    destino = tmp_path / "schemas" / "pbir"
    # Una instalacion anterior, buena y completa.
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    antes = _huella(destino)

    nuevos = {n: d + b"-v2" for n, d in DOCS.items()}

    def _descargar_roto(url):
        nombre = url.split("/")[-1]
        if nombre == cual:
            raise esquemas.SchemaFetchError(f"la red se cayo en {nombre}")
        return nuevos[nombre]

    monkeypatch.setattr(esquemas, "descargar", _descargar_roto)

    with pytest.raises(esquemas.SchemaFetchError):
        esquemas.instalar(_manifiesto(nuevos), destino)

    assert _huella(destino) == antes, (
        f"fallar en {cual} dejo el destino modificado o mezclado")
    assert _restos(destino.parent) == [], (
        f"fallar en {cual} dejo restos: {_restos(destino.parent)}")


def test_un_hash_que_no_cuadra_no_publica_nada(esquemas, tmp_path, monkeypatch):
    destino = tmp_path / "schemas" / "pbir"
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    antes = _huella(destino)

    # El manifiesto dice una cosa y el servidor devuelve otra.
    monkeypatch.setattr(esquemas, "descargar", lambda url: b'{"suplantado":1}')
    with pytest.raises(esquemas.SchemaFetchError, match="HASH DISTINTO"):
        esquemas.instalar(_manifiesto(DOCS), destino)

    assert _huella(destino) == antes
    assert _restos(destino.parent) == []


def test_un_archivo_que_se_escribe_truncado_no_se_publica(esquemas, tmp_path,
                                                          monkeypatch):
    """El hash de la descarga no demuestra que el archivo se escribiera entero.

    Un disco lleno o un antivirus dejan un fichero corto sin que `write_bytes`
    se queje. Por eso se relee del disco todo lo preparado antes de publicarlo.
    """
    destino = tmp_path / "schemas" / "pbir"
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    antes = _huella(destino)

    nuevos = {n: d + b"-v2" for n, d in DOCS.items()}
    monkeypatch.setattr(esquemas, "descargar", lambda url: nuevos[url.split("/")[-1]])
    original = esquemas._verificar_preparado

    def _truncar_y_verificar(staging, manifiesto):
        (staging / "b.json").write_bytes(b"tru")     # el disco se quedo corto
        return original(staging, manifiesto)

    monkeypatch.setattr(esquemas, "_verificar_preparado", _truncar_y_verificar)

    with pytest.raises(esquemas.SchemaFetchError, match="bytes"):
        esquemas.instalar(_manifiesto(nuevos), destino)

    assert _huella(destino) == antes
    assert _restos(destino.parent) == []


def test_si_falla_el_rename_final_el_destino_sigue_utilizable(esquemas, tmp_path,
                                                              monkeypatch):
    destino = tmp_path / "schemas" / "pbir"
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    antes = _huella(destino)

    nuevos = {n: d + b"-v2" for n, d in DOCS.items()}
    monkeypatch.setattr(esquemas, "descargar", lambda url: nuevos[url.split("/")[-1]])
    monkeypatch.setattr(esquemas.promotion, "promover",
                        lambda *a, **k: (_ for _ in ()).throw(
                            esquemas.promotion.PromocionError("no se pudo")))

    with pytest.raises(esquemas.promotion.PromocionError):
        esquemas.instalar(_manifiesto(nuevos), destino)

    assert _huella(destino) == antes
    assert _restos(destino.parent) == []


def test_el_destino_vivo_no_se_toca_hasta_el_instante_de_publicar(
        esquemas, tmp_path, monkeypatch):
    """La ventana que el diseño anterior no podia cerrar.

    El instalador viejo verificaba los hashes antes de copiar -eso estaba
    bien- pero luego copiaba archivo por archivo ENCIMA del destino. Entre la
    primera copia y la ultima habia un intervalo, por corto que fuera, en el
    que el destino era una mezcla de esquemas viejos y nuevos. Ninguna
    inyeccion de fallo *previa* a la copia detecta esa ventana: hay que mirar
    el destino EN EL INSTANTE de publicar.

    Aqui se mira. Justo antes del `rename`, el destino tiene que ser todavia,
    byte a byte, el de antes.
    """
    destino = tmp_path / "schemas" / "pbir"
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    antes = _huella(destino)

    nuevos = {n: d + b"-v2" for n, d in DOCS.items()}
    monkeypatch.setattr(esquemas, "descargar", lambda url: nuevos[url.split("/")[-1]])

    visto: dict = {}
    original = esquemas.promotion.promover

    def _mirar_y_promover(raiz, staging, dest):
        visto["destino"] = _huella(dest)
        return original(raiz, staging, dest)

    monkeypatch.setattr(esquemas.promotion, "promover", _mirar_y_promover)
    esquemas.instalar(_manifiesto(nuevos), destino)

    assert visto["destino"] == antes, (
        "el destino ya estaba modificado antes de publicar: hubo un instante "
        "con esquemas viejos y nuevos mezclados")
    assert _huella(destino) == {n: d for n, d in nuevos.items()} | {
        "_manifest.json": _huella(destino)["_manifest.json"]}


def test_los_archivos_obsoletos_desaparecen_con_la_publicacion(esquemas, tmp_path,
                                                               monkeypatch):
    """Copiar no borra: los esquemas retirados se quedaban ahi para siempre."""
    destino = tmp_path / "schemas" / "pbir"
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)
    assert (destino / "c.json").is_file()

    reducido = {n: d for n, d in DOCS.items() if n != "c.json"}
    esquemas.instalar(_manifiesto(reducido), destino)

    assert not (destino / "c.json").exists(), (
        "el esquema retirado del manifiesto sigue en el destino")
    assert _huella(destino).keys() == {*reducido, "_manifest.json"}


def test_una_publicacion_interrumpida_se_recupera_al_siguiente_intento(
        esquemas, tmp_path, monkeypatch):
    """Determinista: la siguiente instalacion no se encuentra un limbo."""
    destino = tmp_path / "schemas" / "pbir"
    raiz = destino.parent
    monkeypatch.setattr(esquemas, "descargar", lambda url: DOCS[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(DOCS), destino)

    # Se reproduce el corte entre los dos renombrados de la promocion.
    import os

    apartado = raiz / f"{esquemas.promotion.PREFIJO_ANTERIOR}pbir-1-abc"
    os.rename(destino, apartado)
    (raiz / esquemas.promotion.JOURNAL).write_text(json.dumps({
        "esquema": esquemas.promotion.ESQUEMA_JOURNAL,
        "fase": "anterior-apartado", "staging": ".staging-perdido",
        "destino": "pbir", "anterior": apartado.name}), encoding="utf-8")
    assert not destino.exists()

    esquemas.instalar(_manifiesto(DOCS), destino)

    assert _huella(destino).keys() == {*DOCS, "_manifest.json"}
    assert _restos(raiz) == []


# ============================================================================
# Validador PBIR (npm)
# ============================================================================
RELATIVA = Path("node_modules/@microsoft/powerbi-report-authoring-cli/dist/cli.js")


def _npm_falso(destino_por_llamada, *, falla_en_install=False):
    """Sustituye `_correr` para no depender de npm ni de la red."""
    def _correr(args, cwd=None, timeout=900):
        texto = " ".join(str(a) for a in args)
        if "pack" in texto:
            (Path(cwd) / "paquete.tgz").write_bytes(b"tarball")
            return subprocess.CompletedProcess(args, 0, "", "")
        if "install" in texto:
            if falla_en_install:
                return subprocess.CompletedProcess(args, 1, "", "npm exploto")
            prefijo = Path(args[args.index("--prefix") + 1])
            cli = prefijo / RELATIVA
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_text("// cli falso\n", encoding="utf-8")
            for relleno in range(3):
                (prefijo / "node_modules" / f"dep{relleno}.js").write_text(
                    "x", encoding="utf-8")
            destino_por_llamada.append(prefijo)
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "v20.0.0", "")
    return _correr


@pytest.fixture
def validador_preparado(validador, monkeypatch):
    """El validador con node/npm/tarball simulados y su verificacion enchufada."""
    monkeypatch.setattr(validador, "comprobar_node", lambda: 20)
    monkeypatch.setattr(validador.shutil, "which", lambda n: f"/falso/{n}")
    monkeypatch.setattr(validador, "verificar_tarball", lambda ruta: None)
    monkeypatch.setattr(
        validador, "_verificar_preparado",
        lambda staging: ((staging / RELATIVA), validador.VERSION)
        if (staging / RELATIVA).is_file() else
        (_ for _ in ()).throw(validador.InstalacionFallida("no aparece el CLI")))
    return validador


def test_npm_nunca_escribe_sobre_el_validador_vivo(validador_preparado, tmp_path,
                                                   monkeypatch):
    """El defecto en una linea: `--prefix` apuntaba al destino."""
    prefijos: list[Path] = []
    monkeypatch.setattr(validador_preparado, "_correr", _npm_falso(prefijos))
    destino = tmp_path / "cache" / "validator"

    validador_preparado.instalar(destino)

    assert prefijos, "npm install no llego a ejecutarse"
    assert all(p != destino for p in prefijos), (
        f"npm escribio directamente en el destino vivo: {prefijos}")
    assert all(p.name.startswith(".staging-") for p in prefijos), prefijos
    assert (destino / RELATIVA).is_file(), "no publico el CLI"
    assert _restos(destino.parent) == []


def test_si_npm_falla_el_validador_anterior_queda_intacto(validador_preparado,
                                                          tmp_path, monkeypatch):
    destino = tmp_path / "cache" / "validator"
    monkeypatch.setattr(validador_preparado, "_correr", _npm_falso([]))
    validador_preparado.instalar(destino)
    (destino / RELATIVA).write_text("// version buena\n", encoding="utf-8")
    antes = _huella(destino)

    monkeypatch.setattr(validador_preparado, "_correr",
                        _npm_falso([], falla_en_install=True))
    with pytest.raises(validador_preparado.InstalacionFallida, match="npm install"):
        validador_preparado.instalar(destino)

    assert _huella(destino) == antes, "quedo una mezcla del viejo y el nuevo"
    assert _restos(destino.parent) == []


def test_si_el_cli_preparado_no_aparece_no_se_publica(validador_preparado,
                                                      tmp_path, monkeypatch):
    """npm puede terminar en 0 y no dejar lo que hacia falta."""
    destino = tmp_path / "cache" / "validator"
    monkeypatch.setattr(validador_preparado, "_correr", _npm_falso([]))
    validador_preparado.instalar(destino)
    antes = _huella(destino)

    def _npm_vacio(args, cwd=None, timeout=900):
        if "pack" in " ".join(str(a) for a in args):
            (Path(cwd) / "p.tgz").write_bytes(b"t")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(validador_preparado, "_correr", _npm_vacio)
    with pytest.raises(validador_preparado.InstalacionFallida):
        validador_preparado.instalar(destino)

    assert _huella(destino) == antes
    assert _restos(destino.parent) == []


def test_el_validador_vivo_no_se_toca_hasta_el_instante_de_publicar(
        validador_preparado, tmp_path, monkeypatch):
    """Lo mismo para npm, donde la ventana duraba cientos de archivos."""
    destino = tmp_path / "cache" / "validator"
    monkeypatch.setattr(validador_preparado, "_correr", _npm_falso([]))
    validador_preparado.instalar(destino)
    (destino / RELATIVA).write_text("// version buena\n", encoding="utf-8")
    antes = _huella(destino)

    visto: dict = {}
    original = validador_preparado.promotion.promover

    def _mirar_y_promover(raiz, staging, dest):
        visto["destino"] = _huella(dest)
        return original(raiz, staging, dest)

    monkeypatch.setattr(validador_preparado, "_correr", _npm_falso([]))
    monkeypatch.setattr(validador_preparado.promotion, "promover",
                        _mirar_y_promover)
    validador_preparado.instalar(destino)

    assert visto["destino"] == antes, (
        "npm ya habia escrito en el destino vivo antes de publicar")


def test_un_tarball_que_no_cuadra_no_llega_a_npm_install(validador, tmp_path,
                                                         monkeypatch):
    """La verificacion del paquete va ANTES de tocar nada."""
    monkeypatch.setattr(validador, "comprobar_node", lambda: 20)
    monkeypatch.setattr(validador.shutil, "which", lambda n: f"/falso/{n}")
    prefijos: list[Path] = []
    monkeypatch.setattr(validador, "_correr", _npm_falso(prefijos))
    destino = tmp_path / "cache" / "validator"

    with pytest.raises(validador.InstalacionFallida, match="NO coincide"):
        validador.instalar(destino)

    assert prefijos == [], "se ejecuto npm install con un tarball no verificado"
    assert not destino.exists()
    assert _restos(destino.parent) == []


def test_los_dos_instaladores_usan_el_ciclo_de_vida_compartido(esquemas,
                                                               validador):
    """Una sola promocion, o habria dos formas distintas de quedarse a medias."""
    for modulo in (esquemas, validador):
        assert hasattr(modulo, "promotion"), modulo.__name__
        assert modulo.promotion.ESQUEMA_JOURNAL == 2
        assert callable(modulo.promotion.promover)
        assert callable(modulo.promotion.recuperar)


def test_el_staging_se_descarta_aunque_el_sistema_lo_tenga_ocupado(tmp_path,
                                                                   monkeypatch):
    """Lo destapo CI: `rmtree(..., ignore_errors=True)` se rinde en silencio.

    En Windows, matar un proceso NO cierra sus handles al instante. Durante ese
    hueco el borrado falla, y con `ignore_errors=True` el staging sobrevivia sin
    que nadie se enterara: la prueba de G4.3 que mata `npm` a mitad quedaba con
    un `.staging-` huerfano en el runner y verde en esta maquina.

    Aqui el primer intento falla a proposito y se exige que el segundo lo
    consiga. Reintentar es lo correcto -el handle se libera solo-; lo que no
    valia era rendirse a la primera y callarlo.
    """
    from horizun_pbi_mcp.lifecycle import promotion

    staging = tmp_path / f"{promotion.PREFIJO_STAGING}prueba"
    (staging / "dentro").mkdir(parents=True)
    (staging / "dentro" / "a.txt").write_text("x", encoding="utf-8")

    real = promotion.shutil.rmtree
    fallos = {"n": 0}

    def _ocupado_una_vez(ruta, *a, **k):
        if fallos["n"] == 0 and Path(ruta) == staging:
            fallos["n"] += 1
            raise PermissionError("el sistema tiene el archivo abierto")
        return real(ruta, *a, **k)

    monkeypatch.setattr(promotion.shutil, "rmtree", _ocupado_una_vez)

    assert promotion.descartar_staging(staging) is True
    assert not staging.exists()
    assert fallos["n"] == 1, "no se llego a simular el handle ocupado"


def test_si_el_staging_NO_se_puede_borrar_se_dice(tmp_path, monkeypatch):
    """Y si de verdad no se puede, se devuelve `False` en vez de fingir.

    Quien llama puede decirlo en su salida. La limpieza del ciclo de vida
    reconoce el prefijo y lo recogera mas tarde, pero «mas tarde» no es «no
    queda nada», y esa diferencia es la que el informe tiene que poder contar.
    """
    from horizun_pbi_mcp.lifecycle import promotion

    staging = tmp_path / f"{promotion.PREFIJO_STAGING}atascado"
    staging.mkdir()

    monkeypatch.setattr(promotion.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(
                            PermissionError("ocupado para siempre")))
    monkeypatch.setattr(promotion, "INTENTOS_DE_BORRADO", 2)

    assert promotion.descartar_staging(staging) is False
    assert staging.exists()
