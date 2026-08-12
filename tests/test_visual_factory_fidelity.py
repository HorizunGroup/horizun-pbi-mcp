"""Fidelidad y saneamiento al reconstruir visuales desde plantillas PBIR."""
from __future__ import annotations

import json
import math

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator, visual_factory
from horizun_pbi_mcp.powerbi.errors import VisualFactoryError
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


def test_una_opcion_desconocida_se_rechaza_con_la_lista_de_validas(
        proyecto_con_plantillas):
    """`style: "dropdown"` pasaba la validacion sin queja y no hacia nada."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            proyecto_con_plantillas, "slicer", {"values": ["Calendar[Year]"]},
            POS, options={"style": "dropdown"})
    assert "style" in exc.value.details["unsupported"]
    assert "format.mode" in str(exc.value), "la pista debe decir donde va"


def test_las_anclas_del_degradado_no_son_opciones_del_visual(
        proyecto_con_plantillas):
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            proyecto_con_plantillas, "card", {"values": ["[Ratio Pct]"]}, POS,
            measure_index={"Ratio Pct": "Fact"},
            options={"min_value": 0, "mid_value": 50})
    assert "pbi_set_conditional_format" in str(exc.value)


def test_opciones_de_tarjeta_en_un_grafico_se_rechazan(
        proyecto_con_plantillas):
    """`value_color` en un barChart se ignoraba en silencio."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            proyecto_con_plantillas, "clusteredColumnChart",
            {"y": ["[Ratio Pct]"], "category": ["Calendar[Year]"]}, POS,
            measure_index={"Ratio Pct": "Fact"},
            options={"value_color": "#FF0000"})
    assert "value_color" in exc.value.details["unsupported"]


def test_un_decorativo_no_admite_el_bloque_format(proyecto_con_plantillas):
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            proyecto_con_plantillas, "textbox", {}, POS,
            options={"text": "Hola", "format": {"dataLabels": True}})
    assert "format" in exc.value.details["unsupported"]


def test_build_visual_de_extremo_a_extremo_aplica_marco_de_color(
        proyecto_con_plantillas):
    """No solo la funcion interna: el camino completo (clonar plantilla +
    _rutas_formato_generadas + el guardian de formato) tiene que aceptar el
    marco sin lanzar FormatOracleMismatch."""
    salida = visual_factory.build_visual(
        proyecto_con_plantillas, "card", {"values": ["[Ratio Pct]"]}, POS,
        title="KPI", measure_index={"Ratio Pct": "Fact"},
        options={"background_color": "#FDECDD", "border_color": "#F47920",
                 "border_radius": 10, "bold_value": True})

    vco = salida["visual"]["visual"]["visualContainerObjects"]
    assert vco["background"][0]["properties"]["show"]["expr"]["Literal"]["Value"] == "true"
    assert vco["border"][0]["properties"]["radius"]["expr"]["Literal"]["Value"] == "10.0D"
    # El titulo y el valor en negrita, pedidos en la MISMA llamada, sobreviven
    # junto al marco nuevo: uno no debe pisar al otro.
    assert vco["title"][0]["properties"]["text"]["expr"]["Literal"]["Value"] == "'KPI'"
    assert salida["visual"]["visual"]["objects"]["labels"][0]["properties"]["bold"][
        "expr"]["Literal"]["Value"] == "true"


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
