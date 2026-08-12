"""El proyecto de la sesion anterior se ofrece, no se reactiva.

Reactivarlo en silencio hizo que `pbi_validate_pbip_project` corriera contra
el proyecto de AYER (`C:\\Demos TorreAurora`) sin que nadie lo pidiera ni lo
supiera. Ahora `session.json` conserva la pista, pero la primera tool que
necesite proyecto exige confirmarlo con `pbi_open_pbip_project`, y el error
trae la ruta para que confirmar sea una llamada, no una busqueda.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.config import ActiveModel, ActivePbip, Session
from horizun_pbi_mcp.powerbi.errors import NoActivePbipError
from tests.conftest import make_settings


def _pbip(tmp_path):
    return ActivePbip(pbip_path=str(tmp_path / "Ayer.pbip"),
                      project_dir=str(tmp_path))


@pytest.fixture
def settings_con_sesion_previa(tmp_path):
    s = make_settings(tmp_path)
    s.ensure_dirs()
    anterior = Session(s)
    anterior.set_active_pbip(_pbip(tmp_path))
    return s


def test_reiniciar_no_reactiva_el_proyecto_de_ayer(settings_con_sesion_previa):
    sesion = Session(settings_con_sesion_previa)

    assert sesion.active_pbip is None
    assert sesion.restored_pbip is not None
    with pytest.raises(NoActivePbipError) as exc:
        sesion.require_active_pbip()
    assert exc.value.details["reason"] == "pbip_restored_needs_confirmation"
    assert exc.value.details["restored_path"].endswith("Ayer.pbip")


def test_confirmar_el_proyecto_limpia_el_candidato(settings_con_sesion_previa,
                                                   tmp_path):
    sesion = Session(settings_con_sesion_previa)
    sesion.set_active_pbip(_pbip(tmp_path))

    assert sesion.restored_pbip is None
    assert sesion.require_active_pbip().pbip_path.endswith("Ayer.pbip")


def test_persistir_otra_cosa_no_borra_la_pista(settings_con_sesion_previa):
    """Elegir un modelo persiste la sesion; la pista del proyecto sobrevive."""
    sesion = Session(settings_con_sesion_previa)
    sesion.set_active_model(ActiveModel(
        host="localhost", port=51000, connection_string="x", catalog="c"))

    otra = Session(settings_con_sesion_previa)
    assert otra.restored_pbip is not None
    assert otra.restored_pbip.pbip_path.endswith("Ayer.pbip")


def test_sin_sesion_previa_el_error_es_el_de_siempre(tmp_path):
    s = make_settings(tmp_path)
    s.ensure_dirs()
    with pytest.raises(NoActivePbipError) as exc:
        Session(s).require_active_pbip()
    assert "pbi_open_pbip_project" in str(exc.value)
    assert not exc.value.details
