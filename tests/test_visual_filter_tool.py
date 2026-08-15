"""Filtrar un visual EXISTENTE (`pbi_set_visual_filter` / `update_visual_filters`).

`filter_builder.build_filter` ya resolvia el par field/alias correctamente
desde hacia tiempo, pero nada lo conectaba con un visual YA ESCRITO: la unica
forma de filtrar un visual existente era escribir `filterConfig` a mano
directo en el JSON, exactamente la trampa que ese modulo existe para evitar.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, pbir_writer, project_locator
from horizun_pbi_mcp.powerbi.errors import PathSecurityError, ValidationError
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip()


def _leer(active, page, visual_id):
    from horizun_pbi_mcp.utils.json_utils import read_json
    page_dir = pbir_reader.resolve_page_dir(active, page)
    return read_json(page_dir / "visuals" / visual_id / "visual.json")


def test_agrega_un_filtro_categorico(proyecto):
    res = pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
        [{"field": "Calendar[Year]", "values": [2024, 2025]}])

    assert res["before"] is None
    documento = _leer(proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID)
    filtros = documento["filterConfig"]["filters"]
    assert len(filtros) == 1
    assert filtros[0]["field"]["Column"]["Expression"]["SourceRef"] == {
        "Entity": "Calendar"}
    valores = filtros[0]["filter"]["Where"][0]["Condition"]["In"]["Values"]
    assert [v[0]["Literal"]["Value"] for v in valores] == ["2024L", "2025L"]


def test_lista_vacia_quita_el_filterconfig(proyecto):
    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
        [{"field": "Calendar[Year]", "values": [2024]}])
    assert "filterConfig" in _leer(proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID)

    res = pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID, [])

    assert res["after"] is None
    documento = _leer(proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID)
    assert "filterConfig" not in documento, (
        "una lista vacia debe QUITAR la clave, no dejarla como {'filters': []}")


def test_reemplaza_en_vez_de_acumular(proyecto):
    """Dos llamadas seguidas: la segunda REEMPLAZA, no se suma a la primera."""
    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
        [{"field": "Calendar[Year]", "values": [2024]}])
    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
        [{"field": "Fact[Category]", "values": ["A"]}])

    filtros = _leer(proyecto, synthetic.PAGE_ID,
                    synthetic.CARD_TEMPLATE_ID)["filterConfig"]["filters"]
    assert len(filtros) == 1
    assert filtros[0]["field"]["Column"]["Property"] == "Category"


def _crear_slicer(active, visual_id="slicer000000000000", *, con_filtro=True):
    """Un slicer como los que escribe Desktop: dropdown y filtro marcador.

    `objects.general.orientation` lo escribe Power BI, no nosotros, y su valor
    es un literal numerico (`0D`) aunque el catalogo oficial enumere "0"/"1".
    """
    import json

    page_dir = pbir_reader.resolve_page_dir(active, synthetic.PAGE_ID)
    destino = page_dir / "visuals" / visual_id
    destino.mkdir(parents=True, exist_ok=True)
    documento = {
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/visualContainer/2.7.0/schema.json"),
        "name": visual_id,
        "position": {"x": 0, "y": 0, "z": 1, "width": 200, "height": 76,
                     "tabOrder": 1},
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [{
                "field": {"Column": {
                    "Expression": {"SourceRef": {"Entity": "Calendar"}},
                    "Property": "Year"}},
                "queryRef": "Calendar.Year",
                "nativeQueryRef": "Year"}]}}},
            "objects": {"general": [{"properties": {
                "orientation": {"expr": {"Literal": {"Value": "0D"}}}}}]},
            "drillFilterOtherVisuals": True,
        },
    }
    if con_filtro:
        documento["filterConfig"] = {"filters": [{
            "name": "marcadorDelSlicer",
            "field": {"Column": {
                "Expression": {"SourceRef": {"Entity": "Calendar"}},
                "Property": "Year"}},
            "type": "Categorical",
            "filter": {"Version": 2,
                       "From": [{"Name": "c", "Entity": "Calendar", "Type": 0}],
                       "Where": [{"Condition": {"In": {
                           "Expressions": [{"Column": {
                               "Expression": {"SourceRef": {"Source": "c"}},
                               "Property": "Year"}}],
                           "Values": [[{"Literal": {"Value": "2025L"}}]]}}}]},
            "howCreated": "User"}]}
    (destino / "visual.json").write_text(
        json.dumps(documento, indent=2), encoding="utf-8")
    return visual_id


def test_un_slicer_de_desktop_se_puede_filtrar(proyecto):
    """P1: `orientation` ya estaba en el archivo y lo escribio Power BI.

    El validador de formato la rechazaba y dejaba el visual fuera del alcance
    de las tools. Lo preexistente no puede bloquear una escritura que no lo
    toca.
    """
    vid = _crear_slicer(proyecto, con_filtro=False)

    res = pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, vid,
        [{"measure": "Fact[TotalAmount]", "condition": "GreaterThan",
          "value": 0}])

    assert res["transaction"]["committed"] is True
    documento = _leer(proyecto, synthetic.PAGE_ID, vid)
    assert documento["visual"]["objects"]["general"][0]["properties"][
        "orientation"]["expr"]["Literal"]["Value"] == "0D", (
        "la escritura no debe tocar lo que no se le pidio")


def test_filtro_de_medida_tiene_la_forma_que_desktop_entiende(proyecto):
    """P2: `field` por Entity, la consulta por alias, y ComparisonKind."""
    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
        [{"measure": "Fact[TotalAmount]", "condition": "GreaterThan",
          "value": 0}])

    filtro = _leer(proyecto, synthetic.PAGE_ID,
                   synthetic.CARD_TEMPLATE_ID)["filterConfig"]["filters"][0]

    assert filtro["type"] == "Advanced"
    assert filtro["howCreated"] == "User"
    assert filtro["field"]["Measure"] == {
        "Expression": {"SourceRef": {"Entity": "Fact"}},
        "Property": "TotalAmount"}
    condicion = filtro["filter"]["Where"][0]["Condition"]["Comparison"]
    assert condicion["ComparisonKind"] == 1
    assert condicion["Left"]["Measure"]["Expression"]["SourceRef"] == {
        "Source": "f"}, "la mitad interna va por ALIAS, no por nombre"
    assert condicion["Right"]["Literal"]["Value"] == "0L"
    assert filtro["filter"]["From"] == [
        {"Name": "f", "Entity": "Fact", "Type": 0}]


@pytest.mark.parametrize("condition,kind", [
    ("GreaterThan", 1), (">", 1), ("gte", 2), ("<", 3), ("<=", 4), ("=", 0),
])
def test_las_comparaciones_se_escriben_como_las_espera_pbir(condition, kind):
    from horizun_pbi_mcp.pbip import filter_builder

    consulta = filter_builder.build_measure_comparison(
        "Fact", "TotalAmount", condition, 0)
    assert consulta["Where"][0]["Condition"]["Comparison"][
        "ComparisonKind"] == kind


def test_distinto_de_se_escribe_como_not_equal():
    from horizun_pbi_mcp.pbip import filter_builder

    consulta = filter_builder.build_measure_comparison(
        "Fact", "TotalAmount", "!=", 0)
    negado = consulta["Where"][0]["Condition"]["Not"]["Expression"]
    assert negado["Comparison"]["ComparisonKind"] == 0


def test_una_comparacion_inventada_se_rechaza():
    from horizun_pbi_mcp.pbip import filter_builder

    with pytest.raises(filter_builder.FilterBuildError):
        filter_builder.build_measure_comparison(
            "Fact", "TotalAmount", "MasOMenos", 0)


def test_merge_no_borra_la_seleccion_del_slicer(proyecto):
    """P2: el `Categorical` marcador guarda lo que el usuario eligio."""
    vid = _crear_slicer(proyecto)

    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, vid,
        [{"measure": "Fact[TotalAmount]", "condition": "GreaterThan",
          "value": 0}], merge=True)

    filtros = _leer(proyecto, synthetic.PAGE_ID, vid)["filterConfig"]["filters"]
    assert [f["type"] for f in filtros] == ["Categorical", "Advanced"]
    assert filtros[0]["name"] == "marcadorDelSlicer"


def test_sin_merge_el_reemplazo_sigue_siendo_el_de_siempre(proyecto):
    vid = _crear_slicer(proyecto)

    pbir_writer.update_visual_filters(
        proyecto, synthetic.PAGE_ID, vid,
        [{"measure": "Fact[TotalAmount]", "condition": "GreaterThan",
          "value": 0}])

    filtros = _leer(proyecto, synthetic.PAGE_ID, vid)["filterConfig"]["filters"]
    assert [f["type"] for f in filtros] == ["Advanced"]


def test_merge_sustituye_el_filtro_del_mismo_campo(proyecto):
    """Reaplicar sobre la misma medida actualiza, no duplica."""
    vid = _crear_slicer(proyecto)
    for umbral in (0, 5):
        pbir_writer.update_visual_filters(
            proyecto, synthetic.PAGE_ID, vid,
            [{"measure": "Fact[TotalAmount]", "condition": "GreaterThan",
              "value": umbral}], merge=True)

    filtros = _leer(proyecto, synthetic.PAGE_ID, vid)["filterConfig"]["filters"]
    assert len(filtros) == 2
    assert filtros[1]["filter"]["Where"][0]["Condition"]["Comparison"][
        "Right"]["Literal"]["Value"] == "5L"


def test_merge_con_lista_vacia_no_quita_nada(proyecto):
    vid = _crear_slicer(proyecto)

    pbir_writer.update_visual_filters(proyecto, synthetic.PAGE_ID, vid, [],
                                      merge=True)

    filtros = _leer(proyecto, synthetic.PAGE_ID, vid)["filterConfig"]["filters"]
    assert len(filtros) == 1


def test_campo_y_medida_a_la_vez_se_rechaza():
    from horizun_pbi_mcp.pbip import filter_builder

    with pytest.raises(filter_builder.FilterBuildError):
        filter_builder.build_filter({"field": "Calendar[Year]",
                                     "measure": "Fact[TotalAmount]"})


def test_medida_mal_escrita_lo_dice_con_la_clave_correcta():
    from horizun_pbi_mcp.pbip import filter_builder

    with pytest.raises(filter_builder.FilterBuildError) as exc:
        filter_builder.build_filter({"measure": "TotalAmount"})
    assert "'measure'" in str(exc.value)


def test_visual_inexistente_se_rechaza(proyecto):
    with pytest.raises(ValidationError):
        pbir_writer.update_visual_filters(
            proyecto, synthetic.PAGE_ID, "no0000000000000000x",
            [{"field": "Calendar[Year]", "values": [2024]}])


def test_visual_id_con_traversal_es_rechazado(proyecto):
    """La misma puerta de seguridad que ya protege mover un visual: esta
    escritura reusa `_visual_path`, asi que hereda la comprobacion sola."""
    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_writer.update_visual_filters(
            proyecto, synthetic.PAGE_ID,
            "../../../../FUERA_DEL_PROYECTO/x",
            [{"field": "Calendar[Year]", "values": [2024]}])


def test_tipo_de_filtro_invalido_no_escribe_nada(proyecto):
    """Un spec mal formado se rechaza ANTES de tocar el archivo."""
    antes = _leer(proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID)
    with pytest.raises(Exception):
        pbir_writer.update_visual_filters(
            proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID,
            [{"field": "Calendar[Year]", "type": "AlgoRaro", "values": [1]}])
    despues = _leer(proyecto, synthetic.PAGE_ID, synthetic.CARD_TEMPLATE_ID)
    assert antes == despues
