"""Cada grafico de un preset pinta una medida distinta.

Auditoria del 2026-11-07 contra Desktop real: `pbi_build_executive_page` con
dos medidas dibujo el grafico protagonista y el de apoyo con la misma serie
("Total monto por categoria" dos veces). Todos los graficos usaban la primera.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import project_locator, tmdl_reader
from horizun_pbi_mcp.services import workflows
from tests.fixtures import synthetic


def _graficos(spec):
    return [v["fields"]["values"][0] for v in spec["visuals"] if "category" in v["fields"]]


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    project_locator.open_project(session, str(synthetic.materialize(tmp_path)))
    active = session.require_active_pbip()
    return active, tmdl_reader.read_semantic_model(active)


def test_dos_medidas_dan_dos_graficos_distintos(proyecto):
    active, md = proyecto
    w = workflows.build_executive_page(active, md, measures=["TotalAmount", "Ratio Pct"],
                                       category="Calendar[Year]", dry_run=True)
    graficos = _graficos(w["stages"][1]["result"]["spec"])
    assert graficos == ["[TotalAmount]", "[Ratio Pct]"]


def test_una_medida_no_repite_el_grafico(proyecto):
    active, md = proyecto
    w = workflows.build_executive_page(active, md, measures=["TotalAmount"],
                                       category="Calendar[Year]", dry_run=True)
    assert _graficos(w["stages"][1]["result"]["spec"]) == ["[TotalAmount]"]
    assert any("se omite" in a for a in w["warnings"])


def test_el_spec_generado_tampoco_repite(session, isolated_settings):
    from mcp.server.fastmcp import FastMCP

    from horizun_pbi_mcp.tools import spec_tools

    mcp = FastMCP("t")
    spec_tools.register(mcp)
    fn = mcp._tool_manager.get_tool("pbi_generate_page_spec").fn
    res = fn(page_name="R", preset="executive", measures=["A", "B", "C"],
             category="T[c]")
    res = res.get("result", res)
    graficos = _graficos(res["spec"])
    assert graficos == ["[A]", "[B]", "[C]"]
    assert len(set(graficos)) == len(graficos)
