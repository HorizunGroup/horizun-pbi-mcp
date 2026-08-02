"""Filtrar un visual EXISTENTE (`pbi_set_visual_filter` / `update_visual_filters`).

`filter_builder.build_filter` ya resolvia el par field/alias correctamente
desde hacia tiempo, pero nada lo conectaba con un visual YA ESCRITO: la unica
forma de filtrar un visual existente era escribir `filterConfig` a mano
directo en el JSON, exactamente la trampa que ese modulo existe para evitar.
"""
from __future__ import annotations

import pytest

from pbip import pbir_reader, pbir_writer, project_locator
from powerbi.errors import PathSecurityError, ValidationError
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip()


def _leer(active, page, visual_id):
    from utils.json_utils import read_json
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
