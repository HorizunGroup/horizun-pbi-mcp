"""Regresiones del oraculo estructural de objects, sin datos de negocio."""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import format_oracle


CATALOGO = {
    "visualObjects": {
        "value": {
            "fontColor": {"type": "fill"},
            "fontSize": {"type": "formatting"},
        },
        "icon": {
            "shapeType": {"type": "enum", "values": ["blank", "information"]},
        },
    },
    "visualContainerObjects": {
        "title": {"show": {"type": "bool"}, "text": {"type": "text"}},
    },
}


def _lit(value):
    return {"expr": {"Literal": {"Value": value}}}


def _visual(objects=None, vcos=None):
    return {"visual": {"visualType": "cardVisual",
                       "objects": objects or {},
                       "visualContainerObjects": vcos or {}}}


def test_oraculo_acepta_grupo_propiedad_tipo_y_enum_equivalentes():
    doc = _visual(
        {"value": [{"properties": {
            "fontColor": {"solid": {"color": _lit("'#123456'")}},
        }}], "icon": [{"properties": {"shapeType": _lit("'information'")}}]},
        {"title": [{"properties": {"show": _lit("true"),
                                     "text": _lit("'Titulo'")}}]})
    result = format_oracle.compare_managed_paths(
        doc,
        [("objects", "value", "fontColor"),
         ("objects", "icon", "shapeType"),
         ("visualContainerObjects", "title", "show"),
         ("visualContainerObjects", "title", "text")],
        catalog=CATALOGO)
    assert result["available"] is True
    assert result["errors"] == []


@pytest.mark.parametrize("paths,objects,rule", [
    ([("objects", "labels", "color")],
     {"labels": [{"properties": {"color": {}}}]},
     "format_group_unknown"),
    ([("objects", "value", "color")],
     {"value": [{"properties": {"color": {}}}]},
     "format_property_unknown"),
    ([("objects", "value", "fontColor")],
     {"value": [{"properties": {"fontColor": _lit("'#123456'")}}]},
     "format_property_shape"),
    ([("objects", "icon", "shapeType")],
     {"icon": [{"properties": {"shapeType": _lit("'info'")}}]},
     "format_property_shape"),
])
def test_oraculo_distingue_divergencias_que_el_schema_deja_pasAR(
        paths, objects, rule):
    result = format_oracle.compare_managed_paths(
        _visual(objects), paths, catalog=CATALOGO)
    assert [e["rule"] for e in result["errors"]] == [rule]


def test_oraculo_offline_usa_el_snapshot_administrado(monkeypatch):
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: None)
    result = format_oracle.compare_managed_paths(
        _visual({"value": [{"properties": {"fontSize": _lit("32D")}}]}),
        [("objects", "value", "fontSize")])
    assert result["available"] is True
    assert result["source"] == "managed_snapshot"
    assert result["errors"] == []


def test_oraculo_no_afirma_equivalencia_si_no_hay_ninguna_fuente(monkeypatch):
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: None)
    monkeypatch.setattr(format_oracle, "_snapshot_catalog", lambda _vt: None)
    result = format_oracle.compare_managed_paths(
        _visual({"value": [{"properties": {"fontSize": _lit("32D")}}]}),
        [("objects", "value", "fontSize")])
    assert result["available"] is False
    assert result["errors"] == []


def test_fallo_del_cli_se_cachea_por_tipo(monkeypatch):
    from horizun_pbi_mcp.services import report_validator

    calls = {"estado": 0}
    def unavailable():
        calls["estado"] += 1
        return {"available": False, "cli_path": None}

    format_oracle.clear_cache()
    monkeypatch.setattr(report_validator, "estado", unavailable)
    monkeypatch.setattr(report_validator, "_node", lambda: None)
    assert format_oracle._load_catalog("tipoSinRuntime") is None
    assert format_oracle._load_catalog("tipoSinRuntime") is None
    assert calls["estado"] == 1


def test_assert_oracle_falla_con_diagnostico_sin_copiar_valores():
    with pytest.raises(format_oracle.FormatOracleMismatch) as exc:
        format_oracle.assert_managed_paths(
            _visual({"value": [{"properties": {"color": _lit("'#ABCDEF'")}}]}),
            [("objects", "value", "color")], catalog=CATALOGO)
    assert exc.value.details["rule"] == "format_oracle"
    assert "#ABCDEF" not in str(exc.value.details)


def test_oraculo_puede_comprobar_todas_las_propiedades_del_visual(monkeypatch):
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: CATALOGO)
    doc = _visual({"value": [{"properties": {
        "fontColor": {"solid": {"color": _lit("'#123456'")}},
        "propiedadInventada": _lit("'x'"),
    }}]})
    result = format_oracle.compare_all_managed_objects(doc)
    assert result["equivalence_checked"] is True
    assert [e["rule"] for e in result["errors"]] == ["format_property_unknown"]


def test_oraculo_completo_consulta_cli_si_el_visual_solo_tiene_vco(monkeypatch):
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: CATALOGO)
    doc = _visual(vcos={"grupoInventado": [{"properties": {
        "propiedadInventada": _lit("true"),
    }}]})

    result = format_oracle.compare_all_managed_objects(doc)

    assert result["source"] == "official_cli"
    assert result["equivalence_checked"] is True
    assert [e["rule"] for e in result["errors"]] == ["format_group_unknown"]


def test_oraculo_rechaza_expr_vacio_en_un_fill():
    doc = _visual({"value": [{"properties": {
        "fontColor": {"solid": {"color": {"expr": {}}}},
    }}]})

    result = format_oracle.compare_managed_paths(
        doc, [("objects", "value", "fontColor")], catalog=CATALOGO)

    assert [e["rule"] for e in result["errors"]] == ["format_property_shape"]


def test_oraculo_acepta_fillrule_exportado_por_desktop():
    doc = _visual({"value": [{"properties": {
        "fontColor": {"solid": {"expr": {"FillRule": {}}}},
    }}]})
    result = format_oracle.compare_managed_paths(
        doc, [("objects", "value", "fontColor")], catalog=CATALOGO)
    assert result["errors"] == []


def test_enum_numerico_del_catalogo_acepta_el_literal_de_desktop():
    """`slicer.general.orientation`: el catalogo lista "0"/"1", Desktop escribe 0D.

    Comparar las cadenas tal cual daba `format_property_shape` sobre un slicer
    de dropdown creado por el propio Desktop.
    """
    catalogo = {"visualObjects": {
        "general": {"orientation": {"type": "enum", "values": ["0", "1"]}}}}
    doc = {"visual": {"visualType": "slicer", "objects": {
        "general": [{"properties": {"orientation": _lit("0D")}}]}}}

    result = format_oracle.compare_managed_paths(
        doc, [("objects", "general", "orientation")], catalog=catalogo)

    assert result["errors"] == []


def test_enum_numerico_sigue_rechazando_un_valor_que_no_existe():
    catalogo = {"visualObjects": {
        "general": {"orientation": {"type": "enum", "values": ["0", "1"]}}}}
    doc = {"visual": {"visualType": "slicer", "objects": {
        "general": [{"properties": {"orientation": _lit("7D")}}]}}}

    result = format_oracle.compare_managed_paths(
        doc, [("objects", "general", "orientation")], catalog=catalogo)

    assert [e["rule"] for e in result["errors"]] == ["format_property_shape"]


def test_lo_preexistente_no_se_comprueba_al_escribir_otra_cosa(monkeypatch):
    """La propiedad que ya estaba en disco no puede bloquear una escritura."""
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: CATALOGO)
    previo = _visual({"value": [{"properties": {
        "propiedadQueDesktopEscribio": _lit("'x'")}}]})
    nuevo = _visual({"value": [{"properties": {
        "propiedadQueDesktopEscribio": _lit("'x'"),
        "fontColor": {"solid": {"color": _lit("'#123456'")}},
    }}]})

    result = format_oracle.compare_all_managed_objects(nuevo, previo=previo)

    assert result["errors"] == []
    assert result["preexisting_skipped"] == 1


def test_tocar_lo_preexistente_vuelve_a_comprobarlo(monkeypatch):
    """Si la escritura CAMBIA el valor, deja de ser preexistente."""
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: CATALOGO)
    previo = _visual({"value": [{"properties": {"inventada": _lit("'x'")}}]})
    nuevo = _visual({"value": [{"properties": {"inventada": _lit("'y'")}}]})

    result = format_oracle.compare_all_managed_objects(nuevo, previo=previo)

    assert [e["rule"] for e in result["errors"]] == ["format_property_unknown"]


def test_el_delta_ignora_el_orden_de_los_bloques(monkeypatch):
    """Reescribir un visual puede reordenar los bloques sin cambiar nada."""
    monkeypatch.setattr(format_oracle, "_load_catalog", lambda _vt: CATALOGO)
    previo = _visual({"value": [
        {"properties": {"inventada": _lit("'a'")}},
        {"properties": {"inventada": _lit("'b'")}}]})
    nuevo = _visual({"value": [
        {"properties": {"inventada": _lit("'b'")}},
        {"properties": {"inventada": _lit("'a'")}}]})

    assert format_oracle.compare_all_managed_objects(
        nuevo, previo=previo)["errors"] == []


@pytest.mark.live_validator
def test_snapshot_administrado_es_subconjunto_del_catalogo_oficial():
    """Impide que el fallback offline se separe silenciosamente de Desktop."""
    from horizun_pbi_mcp.services import report_validator

    if not report_validator.estado()["available"]:
        pytest.skip("hace falta el CLI oficial")
    tipos = ["textbox", "shape", "image", "pageNavigator", "actionButton",
             "card", "cardVisual", "table", "tableEx", "matrix",
             "pivotTable", "barChart", "stackedAreaChart",
             "hundredPercentStackedBarChart",
             "hundredPercentStackedColumnChart", "pieChart", "gauge"]
    for visual_type in tipos:
        oficial = format_oracle._load_catalog(visual_type)
        snapshot = format_oracle._snapshot_catalog(visual_type)
        assert oficial is not None and snapshot is not None
        for scope in ("visualObjects", "visualContainerObjects"):
            for grupo, propiedades in snapshot[scope].items():
                assert grupo in oficial[scope], (visual_type, scope, grupo)
                for prop, definicion in propiedades.items():
                    actual = oficial[scope][grupo].get(prop)
                    assert actual is not None, (visual_type, scope, grupo, prop)
                    assert actual.get("type") == definicion.get("type"), (
                        visual_type, scope, grupo, prop, actual, definicion)
                    if definicion.get("type") == "enum":
                        assert set(definicion["values"]) <= set(actual["values"])
