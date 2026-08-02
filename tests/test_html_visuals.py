"""Pruebas del soporte HTML dentro de Power BI: visual HTML Content,
registro en publicCustomVisuals y medidas con dataCategory (SVG ImageUrl)."""
import json
from pathlib import Path

from pbip import pbir_writer, project_locator, tmdl_reader, tmdl_writer
from pbip.visual_factory import HTML_CONTENT_TYPE, build_visual


OFFICIAL_HTML_CONTENT_GUID = "htmlContent443BE3AD55E043BF878BED274D3A6855"


def test_guid_html_content_coincide_con_el_paquete_de_appsource():
    """El GUID es un identificador externo, no una constante autoconsistente.

    El valor se tomo de ``visual.guid`` en el ``package.json`` del paquete
    HTML Content 1.6.0.0 exportado por Power BI Desktop. El valor anterior
    terminaba en A6865: factory y writer coincidian entre si, pero apuntaban a
    un visual que no existe y las pruebas tautologicas no podian detectarlo.
    """
    assert HTML_CONTENT_TYPE == OFFICIAL_HTML_CONTENT_GUID
    assert pbir_writer.HTML_CONTENT_GUID == OFFICIAL_HTML_CONTENT_GUID


def test_html_content_visual_build(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    built = build_visual(active, "htmlContent", {"values": ["[Total]"]},
                         {"x": 0, "y": 0, "width": 800, "height": 600},
                         measure_index={"Total": "Ventas"})
    assert built["actual_type"] == OFFICIAL_HTML_CONTENT_GUID
    assert built["visual"]["visual"]["visualType"] == OFFICIAL_HTML_CONTENT_GUID
    qs = built["visual"]["visual"]["query"]["queryState"]
    # el rol del visual HTML Content se llama 'content'
    assert "content" in qs
    proj = qs["content"]["projections"][0]
    assert proj["field"]["Measure"]["Property"] == "Total"


def test_add_public_custom_visual(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    r1 = pbir_writer.add_public_custom_visual(active, OFFICIAL_HTML_CONTENT_GUID)
    assert r1["added"] is True
    # idempotente
    r2 = pbir_writer.add_public_custom_visual(active, OFFICIAL_HTML_CONTENT_GUID)
    assert r2["added"] is False
    rep = json.loads((Path(active.report_dir) / "definition" / "report.json")
                     .read_text(encoding="utf-8"))
    assert rep["publicCustomVisuals"].count(OFFICIAL_HTML_CONTENT_GUID) == 1


def test_measure_with_data_category(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    svg = "\"data:image/svg+xml;utf8,\" & \"<svg/>\""
    tmdl_writer.create_measure_pbip(active, "Ventas", "SVG Punto", svg,
                                    None, None, "SVG", overwrite=True,
                                    data_category="ImageUrl")
    from pbip.tmdl_reader import find_table_file
    text = find_table_file(active, "Ventas").read_text(encoding="utf-8")
    assert "dataCategory: ImageUrl" in text
    # y el modelo re-parsea sin romperse
    md = tmdl_reader.read_semantic_model(active)
    assert any(m["name"] == "SVG Punto" for m in md["measures"])
