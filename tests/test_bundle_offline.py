"""G4.7 — el bundle offline, la mitad que sí se puede cerrar aquí.

`EXTERNAL_GATES.md` tenía a G4.7 en la lista de lo imposible y su propia ficha
decía «la parte *construir el bundle* sí es trabajo local y queda pendiente».
Una confesión de trabajo local dentro del documento de lo que no se puede hacer
localmente: con eso se pudo afirmar «no queda trabajo local».

Lo que se comprueba aquí **no necesita ninguna VM**:

* el formato y su manifiesto, que se verifica con un hash **aparte** —uno que se
  verifica a sí mismo no verifica nada—;
* que un byte cambiado **aborte antes de promover**, no después;
* que la instalación no toque la red: se prohíbe `socket` y `subprocess`
  durante todo el procedimiento y se exige que termine igual;
* que la promoción sea atómica y con rollback, reusando el ciclo de vida
  compartido;
* los límites de tamaño, contra un ZIP que se descomprime hasta llenar el disco.

Lo que sigue siendo externo es una cosa: ejecutarlo en una **VM realmente
desconectada** o detrás de un proxy corporativo. Prohibir `socket` en el proceso
demuestra que el código no sale a la red; no demuestra qué hace Windows con un
proxy mal configurado.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import zipfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _cargar_modulo(nombre: str, archivo: str):
    spec = importlib.util.spec_from_file_location(
        nombre, RAIZ / "scripts" / archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _cargar():
    return _cargar_modulo("bundle_bajo_prueba", "bundle.py")


@pytest.fixture
def bd():
    return _cargar()


CONTENIDO = {
    "wheelhouse/horizun_pbi_mcp-2.0.0-py3-none-any.whl": b"rueda propia",
    "wheelhouse/mcp-1.29.0-py3-none-any.whl": b"rueda de mcp",
    "libs/Microsoft.AnalysisServices.AdomdClient.dll": b"dll adomd",
    "libs/Microsoft.AnalysisServices.Tabular.dll": b"dll tom",
    "schemas/pbir/report.json": b'{"$schema": "x"}',
    "validator/cli.tgz": b"tarball del validador",
}


@pytest.fixture
def preparado(tmp_path):
    base = tmp_path / "preparado"
    for rel, datos in CONTENIDO.items():
        ruta = base / rel
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(datos)
    return base


@pytest.fixture
def bundle(bd, preparado, tmp_path):
    manifiesto = bd.manifiesto_de(preparado, "2.0.0", "requirements-py314.lock")
    return bd.empaquetar(preparado, tmp_path / "b.zip", manifiesto)


def _sin_red(monkeypatch, bd):
    """Prohíbe abrir un socket o lanzar un proceso durante la instalación."""
    def _prohibido(*a, **k):
        raise AssertionError("la instalacion desde el bundle salio a la red")

    monkeypatch.setattr(socket, "socket", _prohibido)
    monkeypatch.setattr(socket, "create_connection", _prohibido)
    monkeypatch.setattr(bd.subprocess, "run", _prohibido)


# ============================ formato y manifiesto ==========================

def test_el_manifiesto_lleva_hash_y_tamano_de_cada_archivo(bd, preparado):
    m = bd.manifiesto_de(preparado, "2.0.0", "lock")
    assert set(m["archivos"]) == set(CONTENIDO)
    for rel, datos in CONTENIDO.items():
        assert m["archivos"][rel]["sha256"] == hashlib.sha256(datos).hexdigest()
        assert m["archivos"][rel]["bytes"] == len(datos)
    assert m["total_bytes"] == sum(len(d) for d in CONTENIDO.values())
    assert sorted(m["componentes"]) == ["libs", "schemas", "validator", "wheelhouse"]


def test_el_hash_del_manifiesto_va_fuera_del_manifiesto(bd, bundle):
    """Un manifiesto que se verifica a si mismo no verifica nada."""
    with zipfile.ZipFile(bundle) as z:
        crudo = z.read(bd.MANIFIESTO)
        aparte = z.read(bd.HASH_DEL_MANIFIESTO).decode()
    assert hashlib.sha256(crudo).hexdigest() == aparte
    assert aparte not in crudo.decode("utf-8")


def test_un_bundle_recien_construido_se_verifica(bd, bundle):
    m = bd.verificar(bundle)
    assert m["version"] == "2.0.0"
    assert len(m["archivos"]) == len(CONTENIDO)


def test_un_bundle_vacio_no_se_construye(bd, tmp_path):
    with pytest.raises(bd.BundleError, match="nada que empaquetar"):
        bd.manifiesto_de(tmp_path, "2.0.0", "lock")


# ============================ manipulación =================================

def _reescribir(bundle: Path, destino: Path, cambios: dict) -> Path:
    """Copia el ZIP sustituyendo miembros. `None` los borra."""
    with zipfile.ZipFile(bundle) as origen, zipfile.ZipFile(destino, "w") as salida:
        for info in origen.infolist():
            if info.filename in cambios:
                nuevo = cambios[info.filename]
                if nuevo is not None:
                    salida.writestr(info.filename, nuevo)
                continue
            salida.writestr(info.filename, origen.read(info.filename))
        for nombre, datos in cambios.items():
            if nombre not in set(origen.namelist()) and datos is not None:
                salida.writestr(nombre, datos)
    return destino


def test_un_byte_cambiado_aborta_ANTES_de_promover(bd, bundle, tmp_path,
                                                   monkeypatch):
    """La propiedad central: un bundle manipulado no toca el disco.

    No basta con que falle: tiene que fallar **sin haber escrito nada**. La
    diferencia entre rechazar un archivo y tener que limpiar una instalacion a
    medias es justo lo que hace usable un instalador offline.
    """
    malo = _reescribir(bundle, tmp_path / "malo.zip",
                       {"libs/Microsoft.AnalysisServices.Tabular.dll": b"dll tox"})
    destino = tmp_path / "instalado"

    llamadas = []
    monkeypatch.setattr(bd.promotion, "crear_staging",
                        lambda *a, **k: llamadas.append("staging"))

    with pytest.raises(bd.BundleError, match="HASH DISTINTO"):
        bd.instalar(malo, destino)

    assert not llamadas, "se llego a crear el staging con un bundle manipulado"
    assert not destino.exists()
    assert not list(tmp_path.glob(f"{bd.promotion.PREFIJO_STAGING}*"))


def test_el_manifiesto_manipulado_se_detecta(bd, bundle, tmp_path):
    """Cambiar el hash esperado dentro del manifiesto no cuela."""
    with zipfile.ZipFile(bundle) as z:
        m = json.loads(z.read(bd.MANIFIESTO))
    m["archivos"]["libs/Microsoft.AnalysisServices.Tabular.dll"]["sha256"] = "0" * 64
    malo = _reescribir(bundle, tmp_path / "malo.zip",
                       {bd.MANIFIESTO: json.dumps(m).encode()})
    with pytest.raises(bd.BundleError, match="su propio hash"):
        bd.verificar(malo)


def test_un_archivo_extra_sin_declarar_se_rechaza(bd, bundle, tmp_path):
    """Un bundle que admite extras admite un extra malicioso."""
    malo = _reescribir(bundle, tmp_path / "malo.zip",
                       {"wheelhouse/sorpresa.whl": b"no estaba en el manifiesto"})
    with pytest.raises(bd.BundleError, match="sin declarar"):
        bd.verificar(malo)


def test_un_archivo_declarado_y_ausente_se_rechaza(bd, bundle, tmp_path):
    malo = _reescribir(bundle, tmp_path / "malo.zip", {"validator/cli.tgz": None})
    with pytest.raises(bd.BundleError, match="no estan"):
        bd.verificar(malo)


def test_un_tamano_que_no_cuadra_se_rechaza(bd, bundle, tmp_path):
    """Aunque el hash del manifiesto siga siendo suyo, el tamano delata."""
    with zipfile.ZipFile(bundle) as z:
        m = json.loads(z.read(bd.MANIFIESTO))
    m["archivos"]["validator/cli.tgz"]["bytes"] = 999999
    crudo = json.dumps(m).encode()
    malo = _reescribir(bundle, tmp_path / "malo.zip",
                       {bd.MANIFIESTO: crudo,
                        bd.HASH_DEL_MANIFIESTO: hashlib.sha256(crudo).hexdigest()})
    with pytest.raises(bd.BundleError, match="bytes y el archivo tiene"):
        bd.verificar(malo)


def test_una_version_de_bundle_desconocida_se_rechaza(bd, bundle, tmp_path):
    with zipfile.ZipFile(bundle) as z:
        m = json.loads(z.read(bd.MANIFIESTO))
    m["bundle_version"] = 99
    crudo = json.dumps(m).encode()
    malo = _reescribir(bundle, tmp_path / "malo.zip",
                       {bd.MANIFIESTO: crudo,
                        bd.HASH_DEL_MANIFIESTO: hashlib.sha256(crudo).hexdigest()})
    with pytest.raises(bd.BundleError, match="entiende la"):
        bd.verificar(malo)


def test_sin_manifiesto_no_hay_bundle(bd, bundle, tmp_path):
    malo = _reescribir(bundle, tmp_path / "malo.zip", {bd.MANIFIESTO: None})
    with pytest.raises(bd.BundleError, match="falta"):
        bd.verificar(malo)


# ============================ límites de tamaño ============================

def test_un_bundle_gigante_no_se_abre(bd, bundle, monkeypatch):
    """Se rechaza por el tamano del ARCHIVO, sin llegar a descomprimir."""
    monkeypatch.setattr(bd, "LIMITE_BUNDLE", 10)
    with pytest.raises(bd.BundleError, match="no se abre"):
        bd.verificar(bundle)


def test_un_miembro_gigante_no_se_empaqueta(bd, preparado, monkeypatch):
    monkeypatch.setattr(bd, "LIMITE_MIEMBRO", 5)
    with pytest.raises(bd.BundleError, match="limite por archivo"):
        bd.manifiesto_de(preparado, "2.0.0", "lock")


# ============================ instalación sin red ==========================

def test_instala_sin_tocar_la_red(bd, bundle, tmp_path, monkeypatch):
    """El gate, en una línea: PyPI, GitHub y npm bloqueados, y aun así instala."""
    _sin_red(monkeypatch, bd)
    destino = tmp_path / "datos" / "2.0.0"

    r = bd.instalar(bundle, destino)

    assert r["archivos"] == len(CONTENIDO)
    for rel, datos in CONTENIDO.items():
        assert (destino / rel).read_bytes() == datos


def test_lo_instalado_es_byte_a_byte_lo_del_bundle(bd, bundle, tmp_path,
                                                   monkeypatch):
    _sin_red(monkeypatch, bd)
    destino = tmp_path / "datos" / "2.0.0"
    bd.instalar(bundle, destino)
    for rel, ref in bd.verificar(bundle)["archivos"].items():
        assert hashlib.sha256((destino / rel).read_bytes()).hexdigest() == ref["sha256"]


def test_reinstalar_conserva_la_version_anterior_y_luego_la_recoge(
        bd, bundle, tmp_path, monkeypatch):
    """Promocion del ciclo de vida compartido: journal, `.previous-` y limpieza."""
    _sin_red(monkeypatch, bd)
    destino = tmp_path / "datos" / "2.0.0"
    bd.instalar(bundle, destino)
    r = bd.instalar(bundle, destino)

    assert r["respaldos_recogidos"] >= 1, (
        "la segunda instalacion no aparto la anterior, o no la recogio")
    assert not list(destino.parent.glob(f"{bd.promotion.PREFIJO_ANTERIOR}*"))
    assert not (destino.parent / bd.promotion.JOURNAL).exists()


def test_si_falla_la_promocion_lo_anterior_sigue_entero(bd, bundle, tmp_path,
                                                        monkeypatch):
    _sin_red(monkeypatch, bd)
    destino = tmp_path / "datos" / "2.0.0"
    bd.instalar(bundle, destino)
    antes = {f.name: f.read_bytes() for f in destino.rglob("*") if f.is_file()}

    rename_real = bd.promotion.os.rename

    def _falla(origen, target):
        if Path(origen).name.startswith(bd.promotion.PREFIJO_STAGING):
            raise OSError("promocion interrumpida a proposito")
        return rename_real(origen, target)

    monkeypatch.setattr(bd.promotion.os, "rename", _falla)
    with pytest.raises(bd.promotion.PromocionError):
        bd.instalar(bundle, destino)

    assert {f.name: f.read_bytes()
            for f in destino.rglob("*") if f.is_file()} == antes
    assert not list(destino.parent.glob(f"{bd.promotion.PREFIJO_STAGING}*"))


def test_dos_instalaciones_a_la_vez_no_se_pisan(bd, bundle, tmp_path, monkeypatch):
    _sin_red(monkeypatch, bd)
    destino = tmp_path / "datos" / "2.0.0"
    bd.instalar(bundle, destino)

    class Ocupado:
        adquirido = False

        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(bd.cerrojos, "CerrojoDeCicloDeVida", Ocupado)
    with pytest.raises(bd.BundleError, match="en curso"):
        bd.instalar(bundle, destino)


# ============================ la CLI y el runbook ==========================

def test_la_cli_verifica_y_no_saca_trazas(bd, bundle, capsys):
    assert bd.main(["verificar", str(bundle)]) == 0
    assert "integro" in capsys.readouterr().out


def test_la_cli_falla_con_codigo_1_y_mensaje(bd, tmp_path, capsys):
    assert bd.main(["verificar", str(tmp_path / "no-esta.zip")]) == 1
    assert "FALLO:" in capsys.readouterr().err


def test_la_cli_instala(bd, bundle, tmp_path, capsys):
    destino = tmp_path / "datos" / "2.0.0"
    assert bd.main(["instalar", str(bundle), "--destino", str(destino)]) == 0
    assert "Instalado desde el bundle" in capsys.readouterr().out


def test_el_runbook_documenta_construir_e_instalar():
    """Un bundle sin runbook es un archivo que nadie sabe usar."""
    texto = (RAIZ / "docs" / "RUNBOOK_INSTALACION.md").read_text(encoding="utf-8")
    for comando in ("scripts/bundle.py construir", "scripts/bundle.py verificar",
                    "scripts/bundle.py instalar"):
        assert comando in texto, f"el runbook no documenta `{comando}`"
    assert "No existe un bundle offline" not in texto, (
        "el runbook sigue diciendo que el bundle no existe")


# ==================== el oráculo real: pip sin índice ======================

@pytest.mark.packaging
def test_el_bundle_real_instala_las_134_tools_sin_indice(tmp_path_factory):
    """G4.7, la mitad local, contra pip de verdad y con la red cerrada.

    Las pruebas de arriba usan contenido sintético: demuestran el formato, la
    verificación y la promoción, que es donde están los defectos interesantes.
    Esta demuestra la otra mitad —que lo que el bundle lleva **sirve**— y no se
    puede fingir: se construye el wheelhouse de verdad y después se instala con
    `--no-index`, que le prohíbe a pip mirar a PyPI aunque haya red.

    Si el bundle no llevara una sola rueda, `--no-index` lo delataría aquí y no
    en la máquina de quien no tiene salida a internet.
    """
    import os
    import subprocess
    import sys

    if os.environ.get("PBI_MCP_PACKAGING_OFFLINE") == "1":
        pytest.skip("PBI_MCP_PACKAGING_OFFLINE=1 declarado a mano: construir el "
                    "wheelhouse necesita indice")

    base = tmp_path_factory.mktemp("bundle_real")
    bd = _cargar()

    # El wheelhouse se construye desde el lock de ESTE interprete, y solo hay
    # lock donde se ha podido generar fielmente (ver `generar_lock.MATRIZ`).
    # Sin el, esto no mide el bundle: mide que falta un lock, y eso ya lo dice
    # su propia prueba.
    generar = _cargar_modulo("generar_lock_bundle", "generar_lock.py")
    mio = generar.version_en_curso()
    if not generar.ruta_de(mio, "win_amd64").is_file():
        pytest.skip(f"no hay lock para py{mio}: generalo con este interprete "
                    "(`python scripts/generar_lock.py`) y repite")

    assert bd.main(["construir", "--salida", str(base / "entrega"),
                    "--componentes", "wheelhouse"]) == 0
    zips = list((base / "entrega").glob("*.zip"))
    assert len(zips) == 1, zips

    destino = base / "datos" / "2.0.0"
    assert bd.main(["instalar", str(zips[0]), "--destino", str(destino)]) == 0
    wheelhouse = destino / "wheelhouse"
    assert len(list(wheelhouse.glob("*.whl"))) >= 40

    venv = base / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True,
                   capture_output=True, timeout=600)
    py = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run(
        [str(py), "-m", "pip", "install", "--no-index",
         "--find-links", str(wheelhouse), "horizun-pbi-mcp"],
        capture_output=True, text=True, timeout=2400)
    assert r.returncode == 0, (
        "el bundle no basta para instalar sin indice:\n"
        f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")

    r = subprocess.run(
        [str(py), "-c", "import asyncio;from horizun_pbi_mcp.server import "
                        "build_server;print(len(asyncio.run(build_server()"
                        ".list_tools())))"],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONPATH": "", "PBI_MCP_LOG_LEVEL": "CRITICAL"})
    assert r.returncode == 0, f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    assert r.stdout.strip().splitlines()[-1] == "134", (
        f"instalado desde el bundle, el servidor no anuncia 134 tools: "
        f"{r.stdout[-500:]}")
