"""Pruebas de generacion de hojas (page_builder), edicion de modelo (model_edit)
y preservacion de estilo del titulo al clonar visuales."""
from horizun_pbi_mcp.pbip import model_edit, page_builder, pbir_reader, project_locator, tmdl_reader
from horizun_pbi_mcp.pbip.tmdl_reader import _definition_dir
from horizun_pbi_mcp.pbip.visual_factory import _set_title


def test_building_blocks(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    md = tmdl_reader.read_semantic_model(active)
    bb = page_builder.building_blocks(active, md)
    assert bb["canvas"]["width"] and bb["canvas"]["height"]
    assert any(t["name"] == "Ventas" for t in bb["model"]["tables"])
    assert bb["existing_pages"]
    assert "card" in bb["supported_visual_types"]


def test_create_page_from_spec(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    spec = {"page_name": "Test EVM", "canvas": {"width": 1280, "height": 720},
            "visuals": [{"type": "card", "title": "Total",
                         "fields": {"values": ["[Total]"]},
                         "position": {"x": 0, "y": 0, "width": 200, "height": 120}}]}
    res = page_builder.create_page_from_spec(active, spec, {"Total": "Ventas"})
    assert res["page_id"]
    vs = pbir_reader.list_visuals(active, res["page_id"])
    assert len(vs) == 1 and vs[0]["title"] == "Total"
    assert vs[0]["measures"] == ["Ventas[Total]"]


def test_create_page_auto_layout(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    # sin position -> se auto-acomoda con layout grid
    spec = {"page_name": "Auto", "canvas": {"width": 1280, "height": 720}, "layout": "grid",
            "visuals": [{"type": "card", "title": f"K{i}", "fields": {"values": ["[Total]"]}}
                        for i in range(4)]}
    res = page_builder.create_page_from_spec(active, spec, {"Total": "Ventas"})
    vs = pbir_reader.list_visuals(active, res["page_id"])
    assert len(vs) == 4
    assert all(v["position"].get("width", 0) > 0 for v in vs)


def test_spec_to_html(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    spec = {"page_name": "P", "canvas": {"width": 1280, "height": 720},
            "visuals": [{"type": "card", "title": "KPI X", "fields": {"values": ["[Total]"]},
                         "position": {"x": 0, "y": 0, "width": 200, "height": 120}}]}
    html = page_builder.spec_to_html(active, spec, standalone=True)
    assert "KPI X" in html and "pb-canvas" in html and "<!doctype html>" in html


def test_hide_column_pbip_roundtrip(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    r = model_edit.set_column_hidden_pbip(active, "Ventas", "Monto", True)
    assert r["changed"]
    md = tmdl_reader.read_semantic_model(active)
    col = next(c for t in md["tables"] if t["name"] == "Ventas"
              for c in t["columns"] if c["name"] == "Monto")
    assert col["is_hidden"] is True
    r2 = model_edit.set_column_hidden_pbip(active, "Ventas", "Monto", False)
    assert r2["changed"]


def test_auto_datetime_pbip(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    model_edit.set_auto_datetime_pbip(active, enabled=False)
    text = (_definition_dir(active) / "model.tmdl").read_text(encoding="utf-8")
    assert "__PBI_TimeIntelligenceEnabled = 0" in text


def test_relationship_direction_pbip(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    rp = _definition_dir(active) / "relationships.tmdl"
    rp.write_text(
        "relationship abc123\n"
        "\tcrossFilteringBehavior: bothDirections\n"
        "\tfromColumn: Ventas.Monto\n"
        "\ttoColumn: Otra.X\n",
        encoding="utf-8")
    r = model_edit.set_relationship_direction_pbip(active, "Ventas", "Otra", "single")
    assert r["changed"] and r["matched"] == 1
    assert "bothDirections" not in rp.read_text(encoding="utf-8")


def test_set_title_preserves_style():
    vis = {"visualContainerObjects": {"title": [{"properties": {
        "text": {"expr": {"Literal": {"Value": "'viejo'"}}},
        "fontColor": {"solid": {"color": "#2EC4B6"}}}}]}}
    _set_title(vis, "Nuevo Titulo")
    props = vis["visualContainerObjects"]["title"][0]["properties"]
    assert "Nuevo Titulo" in props["text"]["expr"]["Literal"]["Value"]
    assert "fontColor" in props  # el estilo de la plantilla se conserva
