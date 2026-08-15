"""CONTRACT-002 / G2.2 — retirar una clave del payload tiene que romper la suite.

`tests/golden/tools_v1.json` congela el `output_shape` **declarado**, y para las
tools con envelope genérico ese declarado es `{"result": ...}`: no dice nada del
contenido. Así que **retirar o renombrar una clave del payload rompía a un
cliente y pasaba en verde**, sin que `python -m tests.contract_utils` dijera una
palabra.

Ese es exactamente el modo de fallo contra el que la red del contrato existe:
la suite daba la misma sensación de seguridad tanto si el payload seguía igual
como si le habían quitado la mitad.

El gate lo pide por mutación: *quitar una clave de una respuesta y exigir rojo*.
Aquí se hace — y se comprueba también lo contrario, porque una red que se
dispara con cada añadido es una red que alguien acabará desactivando.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import payload_contract as pc

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def actual():
    from tests.payload_muestras import capturar

    return capturar()


# ============================================================================
def test_el_payload_de_hoy_coincide_con_el_congelado(actual):
    """La red, en su uso normal."""
    d = pc.diferencias(pc.cargar(), actual)
    assert not d["breaking"], (
        "el interior de un payload cambió de forma incompatible:\n  "
        + "\n  ".join(d["breaking"])
        + "\n\nSi es DELIBERADO y está ratificado: "
          "python -m tests.payload_contract --write")


def test_el_golden_no_esta_vacio():
    """Un golden vacío pasa siempre y no ata nada."""
    congelado = pc.cargar()
    assert congelado, "no hay ningún payload congelado"
    assert "pbi_health_check" in congelado
    assert "pbi_capabilities" in congelado


# --------------------------------------------------------------- mutaciones --
def test_quitar_una_clave_del_payload_pone_la_suite_en_ROJO(actual):
    """La mutación que pide G2.2, sobre una clave que un cliente sí lee."""
    mutado = json.loads(json.dumps(actual))
    del mutado["pbi_health_check"]["healthy"]

    d = pc.diferencias(pc.cargar(), mutado)

    assert d["breaking"], "quitar `healthy` del payload no rompió nada"
    assert any("healthy" in r for r in d["breaking"]), d["breaking"]


def test_renombrar_una_clave_tambien(actual):
    """Renombrar es quitar y añadir: la mitad que rompe tiene que verse."""
    mutado = json.loads(json.dumps(actual))
    mutado["pbi_capabilities"]["capacidades"] = mutado["pbi_capabilities"].pop(
        next(iter(mutado["pbi_capabilities"])))

    d = pc.diferencias(pc.cargar(), mutado)
    assert d["breaking"], "renombrar una clave no rompió nada"


def test_quitar_una_clave_ANIDADA_tambien(actual):
    """Lo que el `output_shape` declarado nunca podría ver."""
    mutado = json.loads(json.dumps(actual))
    del mutado["pbi_health_check"]["server"]["version"]

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("server.version" in r for r in d["breaking"]), d["breaking"]


def test_cambiar_el_tipo_de_una_clave_tambien(actual):
    """Que `healthy` pase de `bool` a `str` rompe a quien lo evalúe."""
    mutado = json.loads(json.dumps(actual))
    mutado["pbi_health_check"]["healthy"] = "str"

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("healthy" in r and "bool" in r for r in d["breaking"]), d["breaking"]


def test_una_tool_que_deja_de_producir_payload_rompe(actual):
    mutado = json.loads(json.dumps(actual))
    del mutado["pbi_capabilities"]

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("ya no produce" in r for r in d["breaking"]), d["breaking"]


# ------------------------------------------------ lo que NO puede romper ----
def test_anadir_una_clave_es_compatible_y_no_rompe(actual):
    """Añadir está permitido. Una red que se dispara con cada añadido es una
    red que alguien acabará desactivando, y entonces no protege de nada."""
    mutado = json.loads(json.dumps(actual))
    mutado["pbi_health_check"]["campo_nuevo"] = "str"

    d = pc.diferencias(pc.cargar(), mutado)
    assert not d["breaking"], d["breaking"]
    assert any("campo_nuevo" in c for c in d["compatible"]), d["compatible"]


def test_una_tool_nueva_es_compatible(actual):
    mutado = json.loads(json.dumps(actual))
    mutado["pbi_tool_futura"] = {"algo": "str"}

    d = pc.diferencias(pc.cargar(), mutado)
    assert not d["breaking"], d["breaking"]


def test_una_lista_con_mas_elementos_no_es_una_ruptura(actual):
    """El numero de elementos depende del proyecto de prueba, no del contrato.

    Congelar la longitud convertiría cada fixture nuevo en una ruptura fingida,
    y las rupturas fingidas son las que enseñan a ignorar el rojo.
    """
    mutado = json.loads(json.dumps(actual))
    mutado["pbi_health_check"]["checks"] *= 3

    d = pc.diferencias(pc.cargar(), mutado)
    assert not d["breaking"], d["breaking"]


# ----------------------------------------------------------- la forma ------
def test_la_forma_no_guarda_valores():
    """Un golden con valores dentro filtra datos y falla por la hora del día."""
    congelado = json.dumps(pc.cargar())
    for filtrado in ("C:\\\\Users", "/Users/", "request_id\": \"a"):
        assert filtrado not in congelado, (
            f"el golden de payloads guarda valores, no formas: {filtrado}")


def test_el_alcance_esta_declarado():
    """Un golden parcial vale; creerlo completo, no."""
    crudo = json.loads((RAIZ / "tests" / "golden" / "payloads_v1.json")
                       .read_text(encoding="utf-8"))
    assert "sin Power BI Desktop" in crudo["note"], (
        "el golden no declara que solo cubre lo obtenible sin Desktop")
