"""Ningun texto para el usuario puede llevar el literal `None`.

El caso concreto: `COUNTROWS(FILTER(...))` devuelve BLANK cuando el filtro no
resuelve, y el diagnostico lo interpolaba tal cual:

    "None fila(s) de 'Avance' caen al (Blank) de la relacion..."

Convertirlo a cero habria sido peor que dejarlo feo: "0 filas afectadas"
AFIRMA que no hay ninguna, y lo cierto es que no se pudo contar. Son dos
estados distintos y el resultado tiene que distinguirlos.
"""
from __future__ import annotations

import json
import re

import pytest

from horizun_pbi_mcp.services import data_diagnose as dd

#: `None` como palabra suelta. `NoneType` o `Ninguno` no cuentan.
_RE_NONE = re.compile(r"\bNone\b")


def _textos_para_el_usuario(nodo, ruta="$"):
    """Todas las cadenas de la respuesta que una persona va a leer.

    Se excluyen las consultas DAX (`query`), que son evidencia literal, no
    prosa: si el DAX dijera `None` seria porque el modelo se llama asi.
    """
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            if clave == "query":
                continue
            yield from _textos_para_el_usuario(valor, f"{ruta}.{clave}")
    elif isinstance(nodo, list):
        for i, valor in enumerate(nodo):
            yield from _textos_para_el_usuario(valor, f"{ruta}[{i}]")
    elif isinstance(nodo, str):
        yield ruta, nodo


class _MotorSinConteo:
    """El motor responde a la consulta de huerfanas SIN el conteo de filas."""

    def run(self, _session, consulta, max_rows=None):
        if "huerfanas" in consulta:
            return {"columns": ["[huerfanas]", "[filas_afectadas]",
                                "[claves_en_blanco]"],
                    "rows": [[3, None, 0]]}
        if "TOPN" in consulta:
            return {"columns": ["[clave]"], "rows": [["X1"], ["X2"]]}
        return {"columns": ["[filas]", "[claves]"], "rows": [[10, 10]]}


def _modelo() -> dict:
    return {
        "tables": [
            {"name": "Avance", "columns": [{"name": "Clave",
                                            "data_type": "string"}]},
            {"name": "Presupuesto", "columns": [{"name": "Clave",
                                                 "data_type": "string"}]},
        ],
        "measures": [],
        "relationships": [
            {"from_table": "Avance", "from_column": "Clave",
             "to_table": "Presupuesto", "to_column": "Clave",
             "is_active": True},
        ],
    }


@pytest.fixture
def diagnostico(monkeypatch):
    from horizun_pbi_mcp.powerbi import dax_runner

    monkeypatch.setattr(dax_runner, "run_dax", _MotorSinConteo().run)
    return dd.diagnose(object(), _modelo())


def test_ninguna_salida_para_el_usuario_contiene_el_literal_none(diagnostico):
    ofensores = [(ruta, texto)
                 for ruta, texto in _textos_para_el_usuario(diagnostico)
                 if _RE_NONE.search(texto)]
    assert ofensores == [], f"texto con 'None' interpolado: {ofensores}"


def test_lo_que_no_se_pudo_contar_se_conserva_como_null(diagnostico):
    """`null` en la evidencia, no cero: son afirmaciones distintas."""
    hallazgo = next(h for h in diagnostico["findings"]
                    if h["rule"] == "claves_huerfanas")

    assert hallazgo["evidence"]["affected_rows"] is None
    assert hallazgo["evidence"]["affected_rows_determined"] is False
    assert json.dumps(hallazgo["evidence"])  # serializa como null, no como "None"


def test_el_mensaje_dice_que_no_se_pudo_determinar(diagnostico):
    hallazgo = next(h for h in diagnostico["findings"]
                    if h["rule"] == "claves_huerfanas")

    assert "No se pudo determinar" in hallazgo["impact"]
    assert "fila(s) de 'Avance' caen" not in hallazgo["impact"]


def test_el_chequeo_se_marca_como_parcial(diagnostico):
    hallazgo = next(h for h in diagnostico["findings"]
                    if h["rule"] == "claves_huerfanas")

    assert hallazgo["partial"] is True
    assert hallazgo["undetermined"] == ["affected_rows"]
    assert diagnostico["partial_checks"] == 1


def test_cuando_si_se_puede_contar_el_mensaje_es_el_de_siempre(monkeypatch):
    class _Motor:
        def run(self, _session, consulta, max_rows=None):
            if "huerfanas" in consulta:
                return {"columns": ["[huerfanas]", "[filas_afectadas]",
                                    "[claves_en_blanco]"],
                        "rows": [[3, 12, 0]]}
            if "TOPN" in consulta:
                return {"columns": ["[clave]"], "rows": [["X1"]]}
            return {"columns": ["[filas]", "[claves]"], "rows": [[10, 10]]}

    from horizun_pbi_mcp.powerbi import dax_runner

    monkeypatch.setattr(dax_runner, "run_dax", _Motor().run)
    resultado = dd.diagnose(object(), _modelo())
    hallazgo = next(h for h in resultado["findings"]
                    if h["rule"] == "claves_huerfanas")

    assert "12 fila(s) de 'Avance' caen" in hallazgo["impact"]
    assert hallazgo["evidence"]["affected_rows"] == 12
    assert hallazgo["partial"] is False
    assert resultado["partial_checks"] == 0
