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

import copy
import json
from pathlib import Path

import pytest

from tests import payload_contract as pc

RAIZ = Path(__file__).resolve().parent.parent

#: Las claves del golden son `<tool>.<escenario>` desde que el
#: muestreo recorre las 134 en dos entornos deterministas. Antes
#: eran el nombre pelado, y solo habia dos.
SALUD = "pbi_health_check.sin-proyecto"
CAPS = "pbi_capabilities.sin-proyecto"


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
    assert SALUD in congelado
    assert CAPS in congelado


# --------------------------------------------------------------- mutaciones --
def test_quitar_una_clave_del_payload_pone_la_suite_en_ROJO(actual):
    """La mutación que pide G2.2, sobre una clave que un cliente sí lee."""
    mutado = json.loads(json.dumps(actual))
    del mutado[SALUD]["healthy"]

    d = pc.diferencias(pc.cargar(), mutado)

    assert d["breaking"], "quitar `healthy` del payload no rompió nada"
    assert any("healthy" in r for r in d["breaking"]), d["breaking"]


def test_renombrar_una_clave_tambien(actual):
    """Renombrar es quitar y añadir: la mitad que rompe tiene que verse."""
    mutado = json.loads(json.dumps(actual))
    mutado[CAPS]["capacidades"] = mutado[CAPS].pop(
        next(iter(mutado[CAPS])))

    d = pc.diferencias(pc.cargar(), mutado)
    assert d["breaking"], "renombrar una clave no rompió nada"


def test_quitar_una_clave_ANIDADA_tambien(actual):
    """Lo que el `output_shape` declarado nunca podría ver."""
    mutado = json.loads(json.dumps(actual))
    del mutado[SALUD]["server"]["version"]

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("server.version" in r for r in d["breaking"]), d["breaking"]


def test_cambiar_el_tipo_de_una_clave_tambien(actual):
    """Que `healthy` pase de `bool` a `str` rompe a quien lo evalúe."""
    mutado = json.loads(json.dumps(actual))
    mutado[SALUD]["healthy"] = "str"

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("healthy" in r and "bool" in r for r in d["breaking"]), d["breaking"]


def test_una_tool_que_deja_de_producir_payload_rompe(actual):
    mutado = json.loads(json.dumps(actual))
    del mutado[CAPS]

    d = pc.diferencias(pc.cargar(), mutado)
    assert any("ya no produce" in r for r in d["breaking"]), d["breaking"]


# ------------------------------------------------ lo que NO puede romper ----
def test_anadir_una_clave_es_compatible_y_no_rompe(actual):
    """Añadir está permitido. Una red que se dispara con cada añadido es una
    red que alguien acabará desactivando, y entonces no protege de nada."""
    mutado = json.loads(json.dumps(actual))
    mutado[SALUD]["campo_nuevo"] = "str"

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
    mutado[SALUD]["checks"] *= 3

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
    assert "COBERTURA_PAYLOADS.md" in crudo["note"], (
        "el golden no dice donde esta la cobertura tool por tool")
    from tests.test_tool_contract import EXPECTED_COUNT

    assert crudo["tools_cubiertas"] == EXPECTED_COUNT, (
        f"el golden cubre {crudo['tools_cubiertas']} tools de "
        f"{EXPECTED_COUNT}. G2.2 se cerro con TODAS congeladas: si baja, "
        "vuelve a estar abierto")


def test_la_cobertura_publicada_coincide_con_el_recorrido():
    """G2.2. «El resto necesita Desktop» era una hipotesis; esto la mide.

    El documento sale de recorrer las 134 por `call_tool`: si alguien anade una
    tool o cambia lo que la bloquea, el recuento cambia y esto lo dice.
    """
    from tests import cobertura_payloads as cp
    from tests.payload_muestras import recorrer

    assert cp.DOC.is_file(), (
        "falta docs/COBERTURA_PAYLOADS.md. Generalo con: "
        "python -m tests.cobertura_payloads")
    assert cp.DOC.read_text(encoding="utf-8") == cp.documento(recorrer()[1])


def test_toda_exclusion_tiene_una_dependencia_medida():
    """Nada puede quedar «pendiente» sin decir de que depende.

    Es lo que separa este inventario del anterior: antes la exclusion era «el
    resto necesita Desktop», sin comprobarlo. Ahora cada tool sin payload trae
    el motivo que se midio ejecutandola.
    """
    from tests.payload_muestras import recorrer

    resumen = recorrer()[1]
    mudas = [n for n, d in resumen.items()
             if d["estado"] == "pendiente" and not d["bloqueo"]]
    assert not mudas, f"pendientes sin dependencia declarada: {mudas}"


def test_ninguna_tool_se_queda_sin_llamada_valida_escrita():
    """El cierre de G2.2, y la puerta para que no se vuelva a abrir.

    El hallazgo era que **77 tools** no tenian payload congelado solo porque
    nadie les habia escrito una llamada valida, y eso se estaba contando como
    si lo impidiera Power BI Desktop. Ya no queda ninguna: cada una tiene su
    entrada en `tests/payload_argumentos.py`.

    Una tool nueva sin llamada valida vuelve a encender esto, que es justo
    cuando hay que escribirsela y no seis meses despues.
    """
    from tests.payload_muestras import recorrer

    resumen = recorrer()[1]
    sin_llamada = sorted(
        n for n, d in resumen.items()
        if (d["bloqueo"] or "").startswith("requiere-argumentos"))
    assert not sin_llamada, (
        f"sin llamada valida escrita: {sin_llamada}. Anadela a "
        "tests/payload_argumentos.py; «requiere argumentos» no es un "
        "impedimento externo, es trabajo")


def test_todas_tienen_payload_congelado():
    """G2.2 literal: todas, y ninguna exclusion."""
    from tests.payload_muestras import recorrer
    from tests.test_tool_contract import EXPECTED_COUNT

    resumen = recorrer()[1]
    pendientes = sorted(n for n, d in resumen.items()
                        if d["estado"] == "pendiente")
    assert not pendientes, f"sin payload congelado: {pendientes}"
    assert len(resumen) == EXPECTED_COUNT


def test_las_dependencias_que_quedan_son_del_ENTORNO_y_estan_medidas():
    """Lo que sigue sin poder congelarse es el payload de EXITO de algunas.

    Y esa exclusion ya no se supone: durante la pasada con argumentos la red y
    los procesos estan PROHIBIDOS, asi que una tool que necesite el entorno
    tropieza con la prohibicion y queda registrada con su motivo. Lo que no
    puede haber es una dependencia declarada sin medir.
    """
    from tests.payload_muestras import recorrer

    resumen = recorrer()[1]
    modelo = [n for n, d in resumen.items()
              if (d["bloqueo"] or "").startswith("modelo-vivo")]
    assert modelo, (
        "ninguna tool declara depender de un modelo vivo: o el muestreo dejo de "
        "ejecutarlas, o alguien las tiene abiertas en Desktop")
    for n, d in resumen.items():
        if d["estado"] != "exito-congelado":
            assert d["bloqueo"], f"{n} no dice por que no tiene payload de exito"


#: Tres de las que entraron con la ampliación a 134: una de informe, una de
#: modelo y una de error de dominio. Si la red solo mordiera sobre las dos
#: originales, congelar 174 muestras no habría servido de nada.
NUEVAS = ("pbi_list_visuals.con-argumentos",
          "pbi_get_object.con-argumentos",
          "pbi_run_dax.con-argumentos")


@pytest.mark.parametrize("clave", NUEVAS)
def test_quitar_una_clave_de_una_tool_NUEVA_tambien_rompe(clave):
    """La red tiene que morder igual en las 134, no solo en las dos de siempre.

    Se muta la respuesta de HOY —no el golden—: quitarle una clave a lo que la
    tool devuelve es lo que rompe a un cliente. Al revés sería el caso
    contrario, el de una clave añadida, que es compatible a propósito.
    """
    congelado = pc.cargar()
    assert clave in congelado, (
        f"{clave} no está en el golden: el muestreo dejó de cubrirla")

    hoy = copy.deepcopy(congelado)
    quitada = next(iter(hoy[clave]))
    del hoy[clave][quitada]

    d = pc.diferencias(congelado, hoy)
    assert d["breaking"], (
        f"quitarle `{quitada}` a {clave} no rompe: esa muestra no está atada")
    assert any(quitada in linea for linea in d["breaking"])


def test_una_tool_que_deja_de_contestar_rompe_aunque_sea_nueva():
    """Perder una muestra entera es la forma más silenciosa de romper."""
    congelado = pc.cargar()
    hoy = copy.deepcopy(congelado)
    del hoy[NUEVAS[0]]

    d = pc.diferencias(congelado, hoy)
    assert d["breaking"], f"perder {NUEVAS[0]} entera no rompe nada"
