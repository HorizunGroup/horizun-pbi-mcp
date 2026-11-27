"""`pbi_create_calculated_table` sin `columns` y con Desktop cerrado.

Verificacion del perfil local (31-oct-2026), 18 tareas con Claude Haiku y
qwen3:8b: las dos tareas de tabla calculada que fallaron con Haiku, y varias
de qwen, omitian `columns`. Sin `columns` la tool deduce las columnas
ejecutando la expresion en el motor de Desktop, asi que respondia el
`no_active_model` generico: «usa mode='pbip' con Desktop cerrado». Esta tool
no tiene `mode`, Desktop YA estaba cerrado y abrirlo tampoco servia (escribir
el TMDL exige cerrarlo). Los modelos repetian la misma llamada hasta rendirse.

El error tiene que decir lo unico que funciona con Desktop cerrado: pasar
`columns`, con su forma.
"""
from __future__ import annotations

import asyncio

import pytest

from horizun_pbi_mcp.pbip import model_author, project_locator
from horizun_pbi_mcp.powerbi.errors import NoActiveModelError
from horizun_pbi_mcp.tool_profiles import LOCAL_DESCRIPTIONS


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def test_sin_columns_ni_modelo_el_error_pide_columns(proyecto, session):
    with pytest.raises(NoActiveModelError) as exc:
        model_author.create_calculated_table(
            proyecto, "Semanas", "DISTINCT(Ventas[Region])", session=session)
    mensaje = exc.value.message
    assert "columns" in mensaje
    assert '"data_type"' in mensaje
    # Esta tool no tiene `mode`: mandarla a mode='pbip' es un callejon.
    assert "mode=" not in mensaje
    assert exc.value.details["missing"] == "columns"


def test_sin_columns_no_escribe_nada(proyecto, session):
    from pathlib import Path

    tablas = Path(proyecto.semantic_model_dir) / "definition" / "tables"
    antes = sorted(p.name for p in tablas.iterdir())
    with pytest.raises(NoActiveModelError):
        model_author.create_calculated_table(
            proyecto, "Semanas", "DISTINCT(Ventas[Region])", session=session)
    assert sorted(p.name for p in tablas.iterdir()) == antes


def test_por_la_tool_el_modelo_recibe_la_salida(proyecto, session, monkeypatch):
    """El mismo caso por el registro MCP, como lo vio el modelo."""
    from horizun_pbi_mcp import config
    from horizun_pbi_mcp.server import build_server

    monkeypatch.setattr(config, "_session", session)
    server = build_server(profile="local")
    salida = asyncio.run(server.call_tool(
        "pbi_create_calculated_table",
        {"name": "Semanas", "expression": "DISTINCT(Ventas[Region])"}))[1]
    resultado = salida.get("result", salida)
    assert resultado["ok"] is False
    assert "columns" in resultado["message"]
    assert "mode=" not in resultado["message"]


def test_la_descripcion_local_dice_que_columns_hace_falta_offline():
    texto = LOCAL_DESCRIPTIONS["pbi_create_calculated_table"]
    assert "columns" in texto
    assert "opcional" not in texto.casefold()
