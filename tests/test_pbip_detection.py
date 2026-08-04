"""Pruebas de deteccion/estructura de proyectos .pbip y round-trip TMDL/PBIR."""
from pathlib import Path

from horizun_pbi_mcp.pbip import pbir_reader, pbir_writer, project_locator, tmdl_reader, tmdl_writer, visual_factory


def test_open_project_detects_pbir_and_tmdl(session, sample_pbip):
    summary = project_locator.open_project(session, str(sample_pbip))
    assert summary["has_pbir"] is True
    assert summary["has_tmdl"] is True
    active = session.require_active_pbip()
    assert active.report_name == "MyReport"


def test_validate_project(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    result = project_locator.validate_project(session)
    assert result["valid"] is True
    assert result["checks"]["enhanced_report_format"] is True
    assert result["checks"]["page_count"] == 1


def test_tmdl_read(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    md = tmdl_reader.read_semantic_model(active)
    names = {t["name"] for t in md["tables"]}
    assert "Ventas" in names
    assert any(m["name"] == "Total" for m in md["measures"])


def test_tmdl_measure_roundtrip(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()

    tmdl_writer.create_measure_pbip(active, "Ventas", "Margen",
                                    "DIVIDE([Total], 100)", "0.0%", None, "KPIs",
                                    overwrite=True)
    md = tmdl_reader.read_semantic_model(active)
    margen = [m for m in md["measures"] if m["name"] == "Margen"]
    assert margen and margen[0]["format_string"] == "0.0%"
    assert margen[0]["display_folder"] == "KPIs"

    tmdl_writer.delete_measure_pbip(active, "Ventas", "Margen")
    md2 = tmdl_reader.read_semantic_model(active)
    assert not any(m["name"] == "Margen" for m in md2["measures"])
    # la medida original sigue intacta
    assert any(m["name"] == "Total" for m in md2["measures"])


def test_pbir_create_visual_minimal_template(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    built = visual_factory.build_visual(
        active, "card", {"values": ["[Total]"]},
        {"x": 10, "y": 10, "width": 200, "height": 120}, "KPI Total",
        measure_index={"Total": "Ventas"})
    # no hay tarjeta previa -> plantilla minima
    assert built["actual_type"] == "card"
    res = pbir_writer.write_visual(active, "pg1", built["visual"])
    reread = pbir_reader.read_visual_file(Path(res["file"]))
    assert reread["type"] == "card"
    assert reread["measures"] == ["Ventas[Total]"]
    assert reread["title"] == "KPI Total"


def test_pbir_list_pages(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    pages = pbir_reader.list_pages(active)
    assert len(pages) == 1
    assert pages[0]["display_name"] == "P1"
