"""Argumentos que la tool no declara: no se descartan en silencio.

Auditoria del 2026-11-07 contra Desktop real: `pbi_align_visuals(...,
alignment="top")` (el parametro es `edge`) alineo a la izquierda con el
defecto, monto un grafico sobre otro y respondio `ok: true`. FastMCP ignora
las claves que no conoce; el servidor ya no.
"""
from __future__ import annotations

import asyncio

import pytest

from horizun_pbi_mcp.server import build_server


@pytest.fixture
def mcp(isolated_settings):
    return build_server()


def _llamar(mcp, nombre, argumentos):
    return asyncio.run(mcp._tool_manager.call_tool(nombre, argumentos))


def test_una_escritura_con_un_argumento_desconocido_no_se_ejecuta(mcp, monkeypatch):
    from horizun_pbi_mcp.tools import report_tools  # noqa: F401  (registro)

    ejecutadas = []
    tool = mcp._tool_manager.get_tool("pbi_align_visuals")
    monkeypatch.setattr(tool, "fn", lambda **kw: ejecutadas.append(kw) or {"ok": True})
    res = _llamar(mcp, "pbi_align_visuals",
                  {"page": "p", "visual_ids": ["a", "b"], "alignment": "top"})
    assert ejecutadas == []
    assert res["ok"] is False and res["error"] == "unknown_arguments"
    assert res["details"]["unknown_arguments"] == ["alignment"]
    assert "edge" in res["details"]["valid_arguments"]
    assert "No se ejecuto nada" in res["message"]


def test_la_sugerencia_nombra_el_parametro_parecido(mcp):
    res = _llamar(mcp, "pbi_create_measure",
                  {"table": "T", "name": "M", "expresion": "1"})
    assert res["error"] == "unknown_arguments"
    assert res["details"]["suggestions"]["expresion"][0] == "expression"
    assert "quisiste decir 'expression'" in res["message"]


def test_una_lectura_se_ejecuta_y_avisa(mcp):
    res = _llamar(mcp, "pbi_list_page_presets", {"filtro": "x"})
    assert res["ok"] is True
    assert res["ignored_arguments"]["unknown_arguments"] == ["filtro"]
    assert res["status"] == "warning"
    assert any("filtro" in w for w in res["warnings"])


def test_sin_argumentos_desconocidos_nada_cambia(mcp):
    res = _llamar(mcp, "pbi_list_page_presets", {})
    assert res["ok"] is True
    assert "ignored_arguments" not in res


def test_tambien_por_el_camino_del_protocolo(mcp):
    """`FastMCP.call_tool` (lo que usa el cliente real) pasa por el control."""
    contenido = asyncio.run(mcp.call_tool("pbi_create_measure",
                                          {"table": "T", "name": "M", "expresion": "1"}))
    texto = str(contenido)
    assert "unknown_arguments" in texto
