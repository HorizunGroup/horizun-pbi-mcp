"""INSTALL-006 (2/2) — publicar tampoco es seguro si dos procesos lo hacen a la vez.

`promotion.recuperar()` documenta en su propio docstring que quien llama debe
tener el cerrojo del ciclo de vida, y los dos publicadores lo llamaban sin
tenerlo. Dentro de `install()` hay un cerrojo en la RAÍZ que los cubre por
arriba, así que el hueco no se ve; pero los dos son **scripts ejecutables por su
cuenta** —el README los documenta así, y el instalador los invoca como
procesos— y ahí no hay ningún cerrojo. Dos procesos podían leer y escribir el
mismo journal y promover sobre el mismo destino.

Estas pruebas usan procesos DE VERDAD (`tests/publicador_de_prueba.py`): lo que
se comprueba es un cerrojo interproceso, y un `threading.Lock` no tiene nada que
ver con eso.

La segunda mitad del archivo mide algo distinto y del mismo hallazgo: que el
respaldo que deja cada publicación no crezca sin límite. En los esquemas eso no
era solo disco — `semillar()` copia la carpeta `schemas` entera al staging de la
versión siguiente, así que los respaldos viajaban de versión en versión.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
PUBLICADOR = RAIZ / "tests" / "publicador_de_prueba.py"
RELATIVA = Path("node_modules/@microsoft/powerbi-report-authoring-cli/dist/cli.js")
ARCHIVOS = ("a.json", "b.json", "c.json")


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


def _manifiesto(documentos: dict[str, bytes]) -> dict:
    return {"manifest_version": 1,
            "documents": [
                {"url": f"https://developer.microsoft.com/json-schemas/fabric/{n}",
                 "file": n, "sha256": hashlib.sha256(d).hexdigest(),
                 "bytes": len(d), "root": True}
                for n, d in documentos.items()]}


def _huella(carpeta: Path) -> dict:
    if not carpeta.is_dir():
        return {}
    return {str(p.relative_to(carpeta)): p.read_bytes()
            for p in sorted(carpeta.rglob("*")) if p.is_file()}


def _restos(raiz: Path) -> list[str]:
    if not raiz.is_dir():
        return []
    return sorted(p.name for p in raiz.iterdir()
                  if p.name.startswith((".staging-", ".promotion"))
                  or p.name.endswith(".tmp"))


def _apartados(raiz: Path, prefijo: str) -> list[Path]:
    return [d for d in raiz.iterdir() if d.name.startswith(prefijo)]


def _marcas(destino: Path) -> set[str]:
    marcas = set()
    for nombre in ARCHIVOS:
        ruta = destino / nombre
        if ruta.is_file():
            marcas.add(json.loads(ruta.read_text(encoding="utf-8"))["marca"])
    return marcas


def _publicar(componente: str, destino: Path, marca: str, *, pausa: float = 0.0,
              esperar: bool = True):
    import os

    entorno = dict(os.environ)
    if pausa:
        entorno["PAUSA_EN_PROMOCION"] = str(pausa)
    proc = subprocess.Popen(
        [sys.executable, str(PUBLICADOR), componente, str(destino), marca],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=entorno)
    if not esperar:
        return proc
    salida, error = proc.communicate(timeout=300)
    return proc.returncode, salida, error


# ============================================================================
# Dos procesos, el mismo destino
# ============================================================================
@pytest.mark.parametrize("componente", ["schemas", "validator"])
def test_dos_publicaciones_simultaneas_no_dejan_mezcla(tmp_path, componente):
    """El primero se detiene en el hueco no atómico; el segundo llega ahí."""
    destino = tmp_path / "cache" / ("schemas/pbir" if componente == "schemas"
                                    else "validator")
    destino.parent.mkdir(parents=True, exist_ok=True)

    primero = _publicar(componente, destino, "UNO", pausa=2.0, esperar=False)
    time.sleep(0.7)
    rc2, _, err2 = _publicar(componente, destino, "DOS")
    rc1 = primero.wait(timeout=300)
    err1 = primero.stderr.read()

    assert rc1 == 0, f"el dueño del cerrojo no pudo publicar: {err1[:400]}"
    # DETERMINISTA, no cuestion de suerte: el primero sigue dentro de su
    # promocion -esta parado en el hueco- cuando el segundo llega, asi que el
    # segundo tiene que encontrarse el cerrojo tomado y apartarse. Sin cerrojo,
    # el segundo terminaba en verde y los dos publicaban sobre el mismo destino.
    assert rc2 != 0, (
        "el segundo proceso publico mientras el primero estaba a medias")
    assert "en curso" in err2.lower(), (
        f"el segundo fallo por algo que no es el cerrojo: {err2[:400]}")

    if componente == "schemas":
        marcas = _marcas(destino)
        assert len(marcas) == 1, f"el destino quedo mezclado: {marcas}"
    else:
        assert (destino / RELATIVA).is_file()
        assert len({(destino / RELATIVA).read_text(encoding="utf-8")}) == 1
    assert _restos(destino.parent) == [], (
        f"quedaron restos sin resolver: {_restos(destino.parent)}")


@pytest.mark.parametrize("componente", ["schemas", "validator"])
def test_una_publicacion_interrumpida_la_termina_el_siguiente(tmp_path,
                                                              componente):
    """El primero muere DENTRO del hueco: el destino no existe un instante.

    El siguiente proceso tiene que encontrarlo, resolverlo y dejar un destino
    completo. Determinista: no puede quedar en un limbo que nadie sepa leer.
    """
    destino = tmp_path / "cache" / ("schemas/pbir" if componente == "schemas"
                                    else "validator")
    destino.parent.mkdir(parents=True, exist_ok=True)
    assert _publicar(componente, destino, "UNO")[0] == 0

    interrumpido = _publicar(componente, destino, "DOS", pausa=30.0, esperar=False)
    time.sleep(2.0)
    interrumpido.kill()
    interrumpido.wait(timeout=60)

    rc, _, err = _publicar(componente, destino, "TRES")

    assert rc == 0, f"no supo recuperarse de la publicacion interrumpida: {err[:400]}"
    if componente == "schemas":
        assert len(_marcas(destino)) == 1, _marcas(destino)
    else:
        assert (destino / RELATIVA).is_file()
    assert _restos(destino.parent) == []


@pytest.mark.parametrize("modulo_nombre,componente", [
    ("fetch_pbir_schemas", "schemas"),
    ("fetch_report_validator", "validator"),
])
def test_el_que_no_tiene_el_cerrojo_no_toca_nada_del_dueno(
        tmp_path, monkeypatch, modulo_nombre, componente):
    """Ni recupera, ni borra, ni promueve sobre lo que controla el primero."""
    modulo = _cargar(modulo_nombre)
    destino = tmp_path / "cache" / ("schemas/pbir" if componente == "schemas"
                                    else "validator")
    raiz = destino.parent
    raiz.mkdir(parents=True, exist_ok=True)

    staging = raiz / ".staging-del-dueno"
    staging.mkdir()
    (staging / "MARCA.txt").write_text("del dueno", encoding="utf-8")
    journal = raiz / modulo.promotion.JOURNAL
    journal.write_text(json.dumps({
        "esquema": modulo.promotion.ESQUEMA_JOURNAL, "fase": "preparada",
        "staging": ".staging-del-dueno", "destino": destino.name,
        "anterior": None}), encoding="utf-8")
    antes = (_huella(staging), journal.read_text(encoding="utf-8"))

    ajeno = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        lock = raiz / modulo.cerrojos.NOMBRE
        lock.write_text(json.dumps({
            "pid": ajeno.pid, "token": "del-dueno", "started": time.time(),
            "proc_creado": modulo.cerrojos.creacion_de_proceso(ajeno.pid)}),
            encoding="utf-8")

        with pytest.raises(Exception) as excinfo:
            if componente == "schemas":
                monkeypatch.setattr(modulo, "descargar", lambda url: b'{"x":1}')
                modulo.instalar(_manifiesto({"a.json": b'{"x":1}'}), destino)
            else:
                monkeypatch.setattr(modulo, "comprobar_node", lambda: 20)
                monkeypatch.setattr(modulo.shutil, "which", lambda n: "/falso")
                modulo.instalar(destino)

        assert "en curso" in str(excinfo.value).lower(), str(excinfo.value)
        assert (_huella(staging), journal.read_text(encoding="utf-8")) == antes, (
            "toco el journal o el staging del proceso que tiene el cerrojo")
        assert lock.read_text(encoding="utf-8").count("del-dueno") == 1
    finally:
        ajeno.kill()
        ajeno.wait(timeout=30)


# ============================================================================
# El respaldo de cada publicación, acotado
# ============================================================================
def test_las_actualizaciones_seguidas_no_acumulan_respaldos(esquemas, tmp_path,
                                                            monkeypatch):
    """Publicado y verificado, el apartado ya no sirve para nada.

    La ventana en la que hacía falta —entre los dos renombrados— se cerró con
    el `rename`. Dejarlo ahí solo garantiza que la siguiente actualización
    encuentre dos, y la siguiente tres.
    """
    destino = tmp_path / "schemas" / "pbir"
    raiz = destino.parent
    cuentas = []
    for n in range(6):
        docs = {k: f'{{"marca":"v{n}"}}'.encode() for k in ARCHIVOS}
        monkeypatch.setattr(esquemas, "descargar",
                            lambda url, d=docs: d[url.split("/")[-1]])
        esquemas.instalar(_manifiesto(docs), destino)
        cuentas.append(len(_apartados(raiz, esquemas.promotion.PREFIJO_ANTERIOR)))

    assert cuentas == [0] * 6, (
        f"los respaldos se acumulan con cada actualizacion: {cuentas}")
    assert _restos(raiz) == []
    assert _marcas(destino) == {"v5"}, "y aun asi el destino quedo al dia"


def test_al_limpiar_no_se_lleva_por_delante_el_N1_del_runtime(esquemas, tmp_path,
                                                              monkeypatch):
    """La trampa de barrer `.previous-` de la raíz que toque.

    Si alguien publica con `--dest <raiz>/schemas`, la raíz del componente es la
    MISMA que la del ciclo de vida del runtime, donde vive el `.previous-` que
    guarda el N−1. Barrer por prefijo a secas se lo llevaría por delante, y con
    él la única instalación a la que se puede volver.
    """
    raiz = tmp_path / "datos"
    destino = raiz / "schemas"
    n1 = raiz / f"{esquemas.promotion.PREFIJO_ANTERIOR}1.5.4-9-abcdef"
    n1.mkdir(parents=True)
    (n1 / "MARCA.txt").write_text("el N-1 del runtime", encoding="utf-8")
    antes = _huella(n1)

    docs = {k: b'{"marca":"x"}' for k in ARCHIVOS}
    monkeypatch.setattr(esquemas, "descargar", lambda url: docs[url.split("/")[-1]])
    esquemas.instalar(_manifiesto(docs), destino)
    esquemas.instalar(_manifiesto(docs), destino)

    assert n1.is_dir() and _huella(n1) == antes, (
        "la limpieza del componente se llevo el N-1 del runtime")
