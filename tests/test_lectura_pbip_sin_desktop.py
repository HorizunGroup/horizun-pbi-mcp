"""Lecturas con `source='live'` y Desktop cerrado, con un .pbip activo.

Auditoria del 2026-11-07: tras crear el proyecto, quince lecturas seguidas
(`pbi_list_tables`, `pbi_list_measures`, `pbi_model_summary`...) respondian
`no_active_model` mandando a abrir Desktop y refrescar. La definicion estaba en
disco y `source='pbip'` la leia: el error tiene que decirlo primero.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import project_locator
from horizun_pbi_mcp.powerbi.errors import NoActiveModelError
from horizun_pbi_mcp.services import dual_mode
from horizun_pbi_mcp.tools import documentation_tools, explore_tools
from tests.fixtures import synthetic


def test_con_pbip_activo_el_error_propone_source_pbip(session, tmp_path,
                                                      isolated_settings):
    project_locator.open_project(session, str(synthetic.materialize(tmp_path)))
    with pytest.raises(NoActiveModelError) as exc:
        dual_mode.leer_modelo_live(session)
    assert exc.value.message.startswith("Repite esta misma llamada con source='pbip'")
    assert exc.value.details["suggested_source"] == "pbip"


def test_sin_pbip_activo_el_error_queda_igual(session, isolated_settings):
    with pytest.raises(NoActiveModelError) as exc:
        dual_mode.leer_modelo_live(session)
    assert "source='pbip'" not in exc.value.message


@pytest.mark.parametrize("cargar", [documentation_tools._load_model_data,
                                    explore_tools.load_model])
def test_las_tools_de_lectura_usan_la_pista(cargar, session, tmp_path,
                                            isolated_settings, monkeypatch):
    project_locator.open_project(session, str(synthetic.materialize(tmp_path)))
    monkeypatch.setattr(documentation_tools, "get_session", lambda: session)
    monkeypatch.setattr(explore_tools, "get_session", lambda: session)
    with pytest.raises(NoActiveModelError) as exc:
        cargar("live")
    assert "source='pbip'" in exc.value.message
    # Y la salida que propone funciona.
    assert cargar("pbip")["tables"]
