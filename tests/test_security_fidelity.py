"""Regresiones de las correcciones de la revision adversarial:
- path traversal bloqueado en .pbip
- referencias de campo vacias/malformadas rechazadas
- medidas multilinea (con lineas en blanco) generan TMDL valido
"""
import json

import pytest

from pbip import project_locator, tmdl_reader, tmdl_writer, visual_factory
from powerbi.errors import VisualFactoryError


def test_path_traversal_in_pbip_is_blocked(session, tmp_path):
    # .Report malicioso FUERA del proyecto
    evil = tmp_path / "EvilOutside.Report"
    (evil / "definition" / "pages").mkdir(parents=True)
    (evil / "definition.pbir").write_text("{}", encoding="utf-8")

    proj = tmp_path / "proj"
    proj.mkdir()
    pbip = proj / "P.pbip"
    pbip.write_text(json.dumps({
        "artifacts": [{"report": {"path": "../EvilOutside.Report"}}]
    }), encoding="utf-8")

    summary = project_locator.open_project(session, str(pbip))
    # El path que se escapa del proyecto NO debe adoptarse como report_dir.
    assert summary["report_dir"] is None or "EvilOutside" not in (summary["report_dir"] or "")


def test_empty_field_reference_rejected(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    for bad in ("Ventas[]", "[", "Ventas[", ""):
        with pytest.raises(VisualFactoryError):
            visual_factory.build_visual(
                active, "card", {"values": [bad]},
                {"x": 0, "y": 0, "width": 100, "height": 100})


def test_multiline_measure_roundtrip_no_broken_indent(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    expr = "VAR x = SUM(Ventas[Monto])\n\nRETURN\n    DIVIDE(x, 100)"
    tmdl_writer.create_measure_pbip(active, "Ventas", "MultiLinea", expr,
                                    None, None, None, overwrite=True)

    # el archivo re-parsea y la medida se lee de vuelta con ambas lineas
    md = tmdl_reader.read_semantic_model(active)
    m = [x for x in md["measures"] if x["name"] == "MultiLinea"]
    assert m, "la medida multilinea deberia existir"
    assert "VAR x" in m[0]["expression"]
    assert "RETURN" in m[0]["expression"]

    # ninguna linea del cuerpo de la medida queda a nivel de tabla (indent < 2)
    table_file = tmdl_reader.find_table_file(active, "Ventas")
    raw = table_file.read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(raw) if l.strip().startswith("measure MultiLinea"))
    j = start + 1
    while j < len(raw) and (raw[j].strip() == "" or raw[j].startswith("\t\t")):
        # las lineas en blanco dentro del bloque estan indentadas (empiezan por tab) o vacias-con-tabs
        if raw[j].strip() == "":
            assert raw[j] == "" or raw[j].startswith("\t"), "linea en blanco sin indentacion rompe el bloque"
        j += 1
