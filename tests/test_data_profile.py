"""Perfilado de valores: lo que la auditoria de estructura no puede ver.

El caso que motivo esto es real: una columna `pct_codificado` que valia -800
en 15 de 33 filas. El modelo era impecable —tipo correcto, sin relaciones
rotas— y el defecto solo aparecia consultando los datos.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import data_profile as dp


@pytest.mark.parametrize("nombre,esperado", [
    ("pct_codificado", True), ("% Aptos", True), ("porcentaje_ok", True),
    ("PercentComplete", True), ("puntaje", False), ("modelo", False),
    ("impacto", False),
])
def test_reconoce_una_columna_de_porcentaje(nombre, esperado):
    assert dp.es_porcentaje(nombre) is esperado


def test_un_porcentaje_negativo_es_un_error():
    """El caso que motivo la herramienta."""
    h = dp._hallazgos({"table": "qa", "column": "pct_codificado",
                       "rows": 33, "blanks": 0, "distinct": 20,
                       "min": -800.0, "max": 100.0, "query": "EVALUATE ..."})
    assert len(h) == 1
    assert h[0]["rule"] == "porcentaje_fuera_de_rango"
    assert h[0]["severity"] == "error"
    assert h[0]["evidence"]["min"] == -800.0
    assert h[0]["query"], "un hallazgo sin la consulta que lo demuestra no se puede comprobar"


def test_un_porcentaje_en_rango_no_se_reporta():
    assert dp._hallazgos({"table": "qa", "column": "pct_ok", "rows": 10,
                          "blanks": 0, "distinct": 5, "min": 0, "max": 100}) == []


def test_una_columna_que_no_es_porcentaje_puede_ser_negativa():
    """`Brecha al minimo` vale -15 y esta perfectamente bien."""
    assert dp._hallazgos({"table": "qa", "column": "brecha", "rows": 10,
                          "blanks": 0, "distinct": 5, "min": -15, "max": 5}) == []


def test_columna_vacia_del_todo():
    h = dp._hallazgos({"table": "t", "column": "c", "rows": 10, "blanks": 10,
                       "distinct": 0})
    assert h[0]["rule"] == "columna_vacia"
    assert h[0]["severity"] == "warning"


def test_columna_mayormente_vacia_sesga_los_promedios():
    h = dp._hallazgos({"table": "t", "column": "c", "rows": 33, "blanks": 25,
                       "distinct": 5})
    assert any(x["rule"] == "columna_mayormente_vacia" for x in h)
    assert h[0]["evidence"]["blank_ratio"] == pytest.approx(0.758, abs=0.001)


def test_columna_constante_es_solo_informativa():
    h = dp._hallazgos({"table": "t", "column": "c", "rows": 10, "blanks": 0,
                       "distinct": 1})
    assert h[0]["rule"] == "columna_constante"
    assert h[0]["severity"] == "info"


def test_una_columna_ilegible_no_genera_hallazgos_inventados():
    assert dp._hallazgos({"table": "t", "column": "c", "error": "timeout"}) == []


def test_el_nombre_de_tabla_se_escapa_para_dax():
    assert dp._dax_seguro("public qa_runs") == "'public qa_runs'"
    assert dp._dax_seguro("con'comilla") == "'con''comilla'"
