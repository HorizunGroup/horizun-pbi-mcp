"""INSTALL-001 e INSTALL-002 — la actualizacion no puede costar el runtime bueno.

El defecto de INSTALL-001 cabe en una linea: `_semilla()` hacia `shutil.move`
de `runtime`, `libs`, `schemas` y `validator` desde la version anterior a la
carpeta nueva **antes de validar nada**. Casi todos los pasos que vienen
despues son descargas -PyPI, NuGet, developer.microsoft.com, npm- y el equipo
tiene medida una carrera DNS que las tumba de forma intermitente. Cuando una
fallaba, el estado quedaba en `failed` y el runtime N-1 ya no existia: la
persona se quedaba sin instalacion anterior a la que volver, por un fallo de red.

Lo que se comprueba aqui no es que la promocion funcione -eso es el camino
facil- sino que **cada punto de fallo deja N-1 utilizable**. Por eso la tabla
de inyeccion recorre los cinco pasos y no solo uno: un rollback que solo se
prueba en el ultimo paso no ha demostrado nada sobre los otros cuatro.

INSTALL-002 va en el mismo archivo porque comparte el mismo modo de fallo: un
componente OPCIONAL -el validador PBIR- tumbaba la instalacion entera. Node 18
en el PATH y te quedabas sin runtime, sin DLL y sin esquemas.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _bootstrap():
    spec = importlib.util.spec_from_file_location(
        "bootstrap_bajo_prueba", RAIZ / "scripts" / "plugin_bootstrap.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def bootstrap():
    return _bootstrap()


def _sembrar_runtime(carpeta: Path, bs, marca: str = "#viejo") -> Path:
    """Un runtime N-1 creible: interprete, DLL y esquema."""
    p = bs.paths(carpeta.parent, cache=carpeta)
    relativa = p["python"].relative_to(p["runtime"])
    py = carpeta / "runtime" / relativa
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(marca, encoding="utf-8")
    (carpeta / "libs").mkdir(parents=True, exist_ok=True)
    (carpeta / "libs" / "Microsoft.AnalysisServices.dll").write_text(
        "dll", encoding="utf-8")
    (carpeta / "schemas" / "pbir").mkdir(parents=True, exist_ok=True)
    (carpeta / "schemas" / "pbir" / "report.json").write_text("{}", encoding="utf-8")
    return py


# ==================== INSTALL-001: fallo en cada paso ========================
#: Los pasos que `install()` ejecuta con `_run`, en orden. La siembra deja un
#: interprete, asi que la creacion del venv no llega a pedirse.
PASOS = ["pip-upgrade", "pip-install", "fetch_libs", "fetch_pbir_schemas"]


@pytest.mark.parametrize("fallar_en", range(1, len(PASOS) + 1),
                         ids=[f"falla-en-{n}-{PASOS[n - 1]}" for n in
                              range(1, len(PASOS) + 1)])
def test_un_fallo_en_cualquier_paso_deja_el_runtime_anterior_utilizable(
        bootstrap, tmp_path, monkeypatch, fallar_en):
    raiz = tmp_path / "datos"
    anterior = raiz / bootstrap.VERSION
    py_anterior = _sembrar_runtime(anterior, bootstrap)
    antes = py_anterior.read_bytes()

    llamadas = {"n": 0}

    def _run_falla(command, *, env, intentos=3):
        llamadas["n"] += 1
        if llamadas["n"] >= fallar_en:
            raise RuntimeError(f"fallo inyectado en el paso {llamadas['n']}")

    monkeypatch.setattr(bootstrap, "_run", _run_falla)
    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", str(raiz))

    assert bootstrap.install(raiz, include_validator=False) == 1

    estado = bootstrap.read_status(raiz)
    assert estado["state"] == "failed"
    assert estado["ready"] is False
    assert estado["runtime_anterior_utilizable"] is True, (
        "el instalador no reconoce que N-1 sigue ahi")

    # Lo que de verdad importa: el runtime anterior, intacto byte a byte.
    assert py_anterior.is_file(), (
        f"fallar en el paso {fallar_en} destruyo el runtime anterior")
    assert py_anterior.read_bytes() == antes
    assert (anterior / "libs" / "Microsoft.AnalysisServices.dll").is_file()
    assert (anterior / "schemas" / "pbir" / "report.json").is_file()


def test_un_fallo_no_deja_staging_huerfano(bootstrap, tmp_path, monkeypatch):
    raiz = tmp_path / "datos"
    _sembrar_runtime(raiz / bootstrap.VERSION, bootstrap)
    monkeypatch.setattr(bootstrap, "_run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", str(raiz))

    bootstrap.install(raiz, include_validator=False)

    restos = [d.name for d in raiz.iterdir()
              if d.name.startswith(bootstrap._promocion.PREFIJO_STAGING)]
    assert restos == [], f"quedo staging sin recoger: {restos}"


def test_la_preparacion_no_toca_el_runtime_vigente_ni_un_instante(
        bootstrap, tmp_path, monkeypatch):
    """La siembra copia: mientras se prepara, N-1 sigue entero.

    Se comprueba DENTRO del paso, no al final: al final tambien estaria bien si
    el instalador hubiera destruido y restaurado, y eso no es lo mismo.
    """
    raiz = tmp_path / "datos"
    anterior = raiz / bootstrap.VERSION
    py_anterior = _sembrar_runtime(anterior, bootstrap)
    visto = []

    def _run_observa(command, *, env, intentos=3):
        visto.append(py_anterior.is_file())
        raise RuntimeError("basta")

    monkeypatch.setattr(bootstrap, "_run", _run_observa)
    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", str(raiz))
    bootstrap.install(raiz, include_validator=False)

    assert visto and all(visto), (
        "el runtime anterior desaparecio durante la preparacion")


# ==================== promocion y recuperacion ===============================
def test_la_promocion_conserva_el_anterior_y_publica_el_nuevo(bootstrap, tmp_path):
    prom = bootstrap._promocion
    raiz = tmp_path / "datos"
    destino = raiz / bootstrap.VERSION
    _sembrar_runtime(destino, bootstrap, marca="#N-1")

    staging = prom.crear_staging(raiz, bootstrap.VERSION)
    _sembrar_runtime(staging, bootstrap, marca="#N")

    resultado = prom.promover(raiz, staging, destino)

    p = bootstrap.paths(raiz)
    assert p["python"].read_text(encoding="utf-8") == "#N"
    assert not staging.exists(), "el staging tenia que convertirse en el destino"
    conservados = prom.anteriores(raiz)
    assert len(conservados) == 1, "no se conservo N-1"
    assert resultado["anterior"] == str(conservados[0])
    assert (conservados[0] / "runtime").is_dir()


def test_se_puede_volver_al_ultimo_runtime_bueno(bootstrap, tmp_path):
    prom = bootstrap._promocion
    raiz = tmp_path / "datos"
    destino = raiz / bootstrap.VERSION
    _sembrar_runtime(destino, bootstrap, marca="#N-1")
    staging = prom.crear_staging(raiz, bootstrap.VERSION)
    _sembrar_runtime(staging, bootstrap, marca="#N-roto")
    prom.promover(raiz, staging, destino)

    restaurado = prom.restaurar_anterior(raiz, destino)

    assert restaurado is not None, "no habia N-1 al que volver"
    p = bootstrap.paths(raiz)
    assert p["python"].read_text(encoding="utf-8") == "#N-1"


def test_una_promocion_interrumpida_entre_los_dos_renombrados_se_recupera(
        bootstrap, tmp_path):
    """El unico hueco del diseño, y su red.

    Promover un directorio no es atomico: hay que apartar el vigente y poner el
    nuevo, y entre esos dos renombrados existe un instante en el que el destino
    NO existe. Un corte de luz ahi dejaria la instalacion sin runtime.
    """
    prom = bootstrap._promocion
    raiz = tmp_path / "datos"
    destino = raiz / bootstrap.VERSION
    _sembrar_runtime(destino, bootstrap, marca="#N-1")

    staging = prom.crear_staging(raiz, bootstrap.VERSION)
    _sembrar_runtime(staging, bootstrap, marca="#N")

    # Se reproduce el corte: el vigente ya se aparto, el nuevo aun no se puso.
    apartado = raiz / f"{prom.PREFIJO_ANTERIOR}{destino.name}-1"
    os.rename(destino, apartado)
    (raiz / prom.JOURNAL).write_text(json.dumps(
        {"fase": "anterior-apartado", "staging": str(staging),
         "destino": str(destino), "anterior": str(apartado)}), encoding="utf-8")
    assert not destino.exists()

    accion = prom.recuperar(raiz)

    assert accion["accion"] == "reintentada"
    assert destino.is_dir()
    assert bootstrap.paths(raiz)["python"].read_text(encoding="utf-8") == "#N"
    assert not (raiz / prom.JOURNAL).exists()


def test_si_el_staging_se_perdio_la_recuperacion_devuelve_el_anterior(
        bootstrap, tmp_path):
    prom = bootstrap._promocion
    raiz = tmp_path / "datos"
    destino = raiz / bootstrap.VERSION
    _sembrar_runtime(destino, bootstrap, marca="#N-1")
    apartado = raiz / f"{prom.PREFIJO_ANTERIOR}{destino.name}-1"
    os.rename(destino, apartado)
    (raiz / prom.JOURNAL).write_text(json.dumps(
        {"fase": "anterior-apartado", "staging": str(raiz / ".staging-perdido"),
         "destino": str(destino), "anterior": str(apartado)}), encoding="utf-8")

    accion = prom.recuperar(raiz)

    assert accion["accion"] == "revertida"
    assert bootstrap.paths(raiz)["python"].read_text(encoding="utf-8") == "#N-1"


def test_la_recuperacion_no_hace_nada_si_no_hay_promocion_a_medias(
        bootstrap, tmp_path):
    assert bootstrap._promocion.recuperar(tmp_path)["accion"] == "ninguna"


def test_la_limpieza_solo_borra_rutas_con_nuestros_prefijos(bootstrap, tmp_path):
    """Una carpeta que no reconocemos no se toca ni aunque estorbe."""
    prom = bootstrap._promocion
    raiz = tmp_path / "datos"
    raiz.mkdir()
    huerfano = prom.crear_staging(raiz, "1.5.5")
    ajena = raiz / "no-es-nuestra"
    ajena.mkdir()
    (ajena / "dato.txt").write_text("del usuario", encoding="utf-8")

    prom.limpiar(raiz)

    assert not huerfano.exists(), "el staging huerfano tenia que irse"
    assert (ajena / "dato.txt").is_file(), "se borro una carpeta ajena"


def test_dos_instaladores_concurrentes_no_se_pisan(bootstrap, tmp_path):
    cerrojos = bootstrap._cerrojos
    with cerrojos.CerrojoDeCicloDeVida(tmp_path) as primero:
        assert primero.adquirido is True
        with cerrojos.CerrojoDeCicloDeVida(tmp_path) as segundo:
            assert segundo.adquirido is False
    with cerrojos.CerrojoDeCicloDeVida(tmp_path) as tercero:
        assert tercero.adquirido is True, "el cerrojo no se libero al salir"


# ==================== INSTALL-002: el validador es opcional ==================
@pytest.mark.parametrize("version,esperado", [
    (None, "skipped_node_unavailable"),
    ("v18.20.4", "skipped_node_too_old"),
    ("v20.11.1", "eligible"),
    ("v25.8.2", "eligible"),
])
def test_el_preflight_decide_por_la_VERSION_de_node_no_por_su_presencia(
        bootstrap, monkeypatch, version, esperado):
    """`shutil.which("node")` dice que hay un Node, no que sirva.

    Con Node 18 el instalador lanzaba `fetch_report_validator`, el proceso
    fallaba, `_run` acababa lanzando y el `except` general dejaba TODO en
    `failed`: sin runtime, sin DLL y sin esquemas, por un componente que el
    producto declara prescindible.
    """
    monkeypatch.setattr(bootstrap, "version_de_node",
                        lambda: ((None, "") if version is None
                                 else (int(version.lstrip("v").split(".")[0]), version)))
    monkeypatch.setattr(bootstrap.shutil, "which", lambda n: "/usr/bin/npm")

    se_puede, motivo, _ = bootstrap.preflight_validador(True)

    assert motivo == esperado
    assert se_puede is (esperado == "eligible")


def test_sin_npm_el_validador_se_omite_con_motivo(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap, "version_de_node", lambda: (22, "v22.0.0"))
    monkeypatch.setattr(bootstrap.shutil, "which",
                        lambda n: None if n == "npm" else "/usr/bin/node")

    se_puede, motivo, _ = bootstrap.preflight_validador(True)

    assert se_puede is False and motivo == "skipped_npm_unavailable"


def test_un_validador_que_falla_no_tumba_la_instalacion(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap, "preflight_validador",
                        lambda inc: (True, "eligible", "v22.0.0"))
    monkeypatch.setattr(bootstrap, "_run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("npm murio")))
    monkeypatch.delenv("HORIZUN_PBI_REQUIRE_VALIDATOR", raising=False)

    resultado = bootstrap._instalar_validador({"python": Path("py"),
                                               "validator": Path("v")},
                                              {}, True, lambda **kw: None)

    assert resultado["state"] == "failed_optional"
    assert "npm murio" in resultado["error"], "se perdio el motivo del fallo"


def test_solo_el_modo_requerido_convierte_el_fallo_en_fatal(bootstrap, monkeypatch):
    monkeypatch.setattr(bootstrap, "preflight_validador",
                        lambda inc: (True, "eligible", "v22.0.0"))
    monkeypatch.setattr(bootstrap, "_run",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("npm murio")))
    monkeypatch.setenv("HORIZUN_PBI_REQUIRE_VALIDATOR", "1")

    with pytest.raises(OSError):
        bootstrap._instalar_validador({"python": Path("py"), "validator": Path("v")},
                                      {}, True, lambda **kw: None)


def test_con_node_viejo_la_instalacion_llega_a_ready(bootstrap, tmp_path, monkeypatch):
    """El escenario completo de INSTALL-002, de punta a punta."""
    raiz = tmp_path / "datos"
    _sembrar_runtime(raiz / bootstrap.VERSION, bootstrap)
    monkeypatch.setattr(bootstrap, "version_de_node", lambda: (18, "v18.20.4"))
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: None)
    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", str(raiz))

    assert bootstrap.install(raiz, include_validator=True) == 0

    estado = bootstrap.read_status(raiz)
    assert estado["state"] == "ready" and estado["ready"] is True
    assert estado["validator"]["state"] == "skipped_node_too_old"
    assert estado["validator"]["node"] == "v18.20.4", (
        "no se registro la version detectada: doctor no podria explicar el hueco")
