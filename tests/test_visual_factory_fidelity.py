"""Fidelidad y saneamiento al reconstruir visuales desde plantillas PBIR."""
from __future__ import annotations

import json
import math

import pytest

from pbip import pbir_reader, project_locator, visual_factory
from powerbi.errors import VisualFactoryError
from tests.fixtures import synthetic


POS = {"x": 0, "y": 0, "width": 400, "height": 240}


@pytest.fixture
def proyecto_con_plantillas(session, tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip()


def test_roundtrip_reader_factory_conserva_aggregation_y_referencias(tmp_path):
    """Antes, `aggregation` se perdia al reducir el campo a una cadena."""
    original = {
        "field": {"Aggregation": {
            "Expression": {"Column": {
                "Expression": {"SourceRef": {"Entity": "Fact"}},
                "Property": "Amount",
            }},
            "Function": 0,
        }},
        "queryRef": "Sum(Fact.Amount)",
        "nativeQueryRef": "Sum of Amount",
    }
    archivo = tmp_path / "visual.json"
    archivo.write_text(json.dumps({
        "name": "v1",
        "position": POS,
        "visual": {
            "visualType": "columnChart",
            "query": {"queryState": {"Y": {"projections": [original]}}},
        },
    }), encoding="utf-8")

    leido = pbir_reader.read_visual_file(archivo)
    rehecho = visual_factory._build_query(
        "columnChart", leido["fields"], {}, [])

    assert rehecho["queryState"]["Y"]["projections"][0] == original


def test_title_none_no_hereda_el_titulo_de_la_plantilla(proyecto_con_plantillas):
    salida = visual_factory.build_visual(
        proyecto_con_plantillas, "card", {"values": ["[Ratio Pct]"]}, POS,
        title=None, measure_index={"Ratio Pct": "Fact"})

    assert salida["origin"].startswith("clonado de")
    vco = salida["visual"]["visual"].get("visualContainerObjects", {})
    assert "title" not in vco


def test_clon_descarta_selectores_que_apuntan_al_campo_anterior(
        proyecto_con_plantillas):
    plantilla = visual_factory.find_template(proyecto_con_plantillas, "card")
    assert plantilla is not None
    documento = json.loads(plantilla.read_text(encoding="utf-8"))
    bloque = {
        "properties": {"backColor": {"solid": {"color": {
            "expr": {"Literal": {"Value": "'#FF0000'"}},
        }}}},
        "selector": {
            "metadata": "Fact.TotalAmount",
            "data": [{"dataViewWildcard": {"matchingOption": 1}}],
        },
    }
    vigente = json.loads(json.dumps(bloque))
    vigente["selector"]["metadata"] = "Fact.Ratio Pct"
    documento["visual"].setdefault("objects", {})["values"] = [bloque, vigente]
    plantilla.write_text(json.dumps(documento), encoding="utf-8")

    salida = visual_factory.build_visual(
        proyecto_con_plantillas, "card", {"values": ["[Ratio Pct]"]}, POS,
        measure_index={"Ratio Pct": "Fact"})

    objetos = salida["visual"]["visual"].get("objects", {})
    metadatos = {
        bloque.get("selector", {}).get("metadata")
        for bloques in objetos.values() if isinstance(bloques, list)
        for bloque in bloques if isinstance(bloque, dict)
    }
    assert "Fact.TotalAmount" not in metadatos
    assert "Fact.Ratio Pct" in metadatos
    assert any("formato" in aviso and "campos" in aviso
               for aviso in salida["warnings"])


@pytest.mark.parametrize("clave,valor", [
    ("width", 0),
    ("height", -1),
    ("width", math.nan),
    ("height", math.inf),
    ("x", -math.inf),
])
def test_geometria_invalida_se_rechaza_antes_de_buscar_plantilla(
        proyecto_con_plantillas, monkeypatch, clave, valor):
    posicion = dict(POS)
    posicion[clave] = valor
    monkeypatch.setattr(
        visual_factory, "find_template",
        lambda *_: pytest.fail("se busco plantilla antes de validar geometria"))

    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            proyecto_con_plantillas, "card", {"values": ["[TotalAmount]"]},
            posicion, measure_index={"TotalAmount": "Fact"})

    assert exc.value.details["rule"] in {
        "finite_position", "positive_dimensions"}
