"""Medidas pasadas como `[Total]` en los generadores de paginas.

Auditoria del 2026-11-07 contra Desktop real: `pbi_generate_page_spec` con
`measures=["[Total monto]"]` producia el campo `[[Total monto]]` (y despues
`pbi_apply_page_spec` lo rechazaba), y `pbi_build_executive_page` respondia
"Estas medidas no existen en el modelo" con la medida delante. `[Total]` es
como se escribe una medida en DAX y en los propios specs: tiene que valer.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import project_locator, tmdl_reader
from horizun_pbi_mcp.services import page_spec, workflows
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, tmdl_reader.read_semantic_model(active)


@pytest.mark.parametrize("ref, esperado", [
    ("Total", "Total"),
    ("[Total]", "Total"),
    ("  [Total monto] ", "Total monto"),
    ("Tabla[Columna]", "Tabla[Columna]"),
])
def test_nombre_de_medida(ref, esperado):
    assert page_spec.nombre_de_medida(ref) == esperado


def test_la_pagina_ejecutiva_acepta_medidas_entre_corchetes(proyecto):
    active, md = proyecto
    w = workflows.build_executive_page(
        active, md, measures=["[TotalAmount]", "[Ratio Pct]"],
        category="Calendar[Year]", seed="t", dry_run=True)
    spec = w["stages"][1]["result"]["spec"]
    valores = [r for v in spec["visuals"] for r in v["fields"].get("values", [])]
    assert "[TotalAmount]" in valores
    assert not any(r.startswith("[[") for r in valores), valores


def test_el_spec_generado_no_duplica_corchetes(session, isolated_settings, tmp_path):
    from mcp.server.fastmcp import FastMCP

    from horizun_pbi_mcp.tools import spec_tools

    mcp = FastMCP("t")
    spec_tools.register(mcp)
    herramienta = mcp._tool_manager.get_tool("pbi_generate_page_spec")
    res = herramienta.fn(page_name="R", measures=["[Total]", "Margen"],
                         category="Ventas[region]")
    res = res.get("result", res)
    valores = [r for v in res["spec"]["visuals"] for r in v["fields"].get("values", [])]
    assert "[Total]" in valores and "[Margen]" in valores
    assert not any(r.startswith("[[") for r in valores), valores
