"""Un `session.json` que no parsea no se sobreescribe. Nunca.

`_load_persisted()` se tragaba el `ValueError` en silencio y arrancaba con la
sesion vacia; el primer `_persist()` —que ocurre en cuanto alguien selecciona
un modelo o abre un proyecto— lo pisaba con el estado nuevo. El archivo que
explicaba que habia pasado desaparecia sin que nadie llegara a verlo, y encima
sin dejar rastro de que hubiera estado roto.

Que se garantiza aqui:

- el archivo queda **byte a byte** como estaba, en el arranque y despues de
  operar;
- nadie lo renombra, lo mueve ni lo borra: eso lo decide una persona que lo
  mire;
- el estado se cuenta hacia fuera —`persisted_state` y `pbi_session_info`—,
  porque un `session.json` corrupto no rompe nada AHORA: se nota al reiniciar,
  cuando ya nadie lo relaciona con esto.

Lo que NO cuenta como corrupto: un JSON valido con otra forma. Ahi el archivo
se entiende, simplemente no dice lo que esperabamos, y reescribirlo con la
forma buena es exactamente lo que toca.
"""
from __future__ import annotations

import json

import pytest

from horizun_pbi_mcp.config import ActiveModel, ActivePbip, Session
from tests.conftest import make_settings

ROTO = b'{"active_model": {"port": 51000, "cata'


@pytest.fixture
def settings_con_session_rota(tmp_path):
    s = make_settings(tmp_path)
    s.ensure_dirs()
    (s.outputs_dir / "session.json").write_bytes(ROTO)
    return s


def archivo(s):
    return s.outputs_dir / "session.json"


# ------------------------------------------------------------- el invariante --
def test_arrancar_con_el_json_roto_no_lo_toca(settings_con_session_rota):
    s = settings_con_session_rota
    Session(s)
    assert archivo(s).read_bytes() == ROTO


def test_operar_despues_tampoco_lo_pisa(settings_con_session_rota, tmp_path):
    """El momento exacto del defecto: `_persist()` tras el arranque fallido."""
    s = settings_con_session_rota
    sesion = Session(s)

    sesion.set_active_pbip(ActivePbip(
        pbip_path=str(tmp_path / "X.pbip"), project_dir=str(tmp_path),
        report_dir=str(tmp_path / "X.Report"), semantic_model_dir=None,
        report_name="X"))

    assert archivo(s).read_bytes() == ROTO, (
        "seleccionar un proyecto persiste la sesion, y ahi se perdia el "
        "archivo que explicaba el problema")


def test_la_sesion_arranca_vacia_pero_sigue_funcionando(settings_con_session_rota):
    """Fallar cerrado no es morirse: el servidor tiene que atender igual."""
    sesion = Session(settings_con_session_rota)
    assert sesion.active_model is None
    assert sesion.active_pbip is None


def test_nadie_renombra_ni_borra_ni_deja_un_bak(settings_con_session_rota, tmp_path):
    s = settings_con_session_rota
    sesion = Session(s)
    sesion.set_active_model(ActiveModel(host="localhost", port=51000,
                                   connection_string="Data Source=localhost:51000",
                                   catalog="c"))

    assert sorted(p.name for p in s.outputs_dir.iterdir()) == ["session.json"], (
        "no se crea copia, no se mueve y no se borra")
    assert archivo(s).read_bytes() == ROTO


# ----------------------------------------------------------------- se cuenta --
def test_el_estado_es_accionable(settings_con_session_rota):
    estado = Session(settings_con_session_rota).persisted_state

    assert estado["state"] == "corrupt"
    assert estado["path"].endswith("session.json")
    assert "reason" in estado, "hay que decir POR QUE no parsea"
    assert "reiniciar" in estado["consequence"], (
        "hay que decir la CONSECUENCIA: se nota al reiniciar, no ahora")
    assert "borralo" in estado["recovery"], (
        "y quien tiene que hacer que, porque el servidor no lo va a hacer solo")


def test_pbi_session_info_lo_saca_como_aviso(settings_con_session_rota,
                                             monkeypatch):
    """No basta con guardarlo: tiene que llegar a quien mira."""
    import asyncio

    import horizun_pbi_mcp.config as cfg
    from horizun_pbi_mcp.server import build_server

    sesion = Session(settings_con_session_rota)
    monkeypatch.setattr(cfg, "_session", sesion)
    monkeypatch.setattr(cfg, "_settings", settings_con_session_rota)

    salida = asyncio.run(build_server().call_tool("pbi_session_info", {}))
    cuerpo = salida[1] if isinstance(salida, tuple) else salida
    if isinstance(cuerpo, dict) and "result" in cuerpo and "ok" not in cuerpo:
        cuerpo = cuerpo["result"]

    assert cuerpo["persisted_session"]["state"] == "corrupt"
    assert any("corrupto" in a for a in (cuerpo.get("warnings") or [])), (
        f"el aviso no llego a la respuesta: {cuerpo.get('warnings')}")


# ------------------------------------------- y lo normal sigue siendo normal --
def test_una_sesion_sana_si_se_persiste(tmp_path):
    """El guard no puede convertirse en un candado sobre el caso bueno."""
    s = make_settings(tmp_path)
    s.ensure_dirs()
    sesion = Session(s)
    sesion.set_active_model(ActiveModel(host="localhost", port=51000,
                                   connection_string="Data Source=localhost:51000",
                                   catalog="c"))

    datos = json.loads(archivo(s).read_text(encoding="utf-8"))
    assert datos["active_model"]["port"] == 51000
    assert Session(s).persisted_state["state"] == "ok"


def test_un_json_valido_con_otra_forma_no_es_corrupcion(tmp_path):
    """Se entiende el archivo; solo no dice lo que esperabamos."""
    s = make_settings(tmp_path)
    s.ensure_dirs()
    archivo(s).write_text('{"active_model": "esto deberia ser un objeto"}',
                          encoding="utf-8")

    sesion = Session(s)
    assert sesion.persisted_state["state"] == "ok"
    assert sesion.active_model is None

    sesion.set_active_model(ActiveModel(host="localhost", port=52000,
                            connection_string="Data Source=localhost:52000",
                            catalog="c"))
    assert json.loads(archivo(s).read_text(
        encoding="utf-8"))["active_model"]["port"] == 52000, (
        "un archivo legible con otra forma SI se reescribe: no hay evidencia "
        "que preservar")


def test_sin_archivo_no_hay_nada_que_conservar(tmp_path):
    s = make_settings(tmp_path)
    s.ensure_dirs()
    assert Session(s).persisted_state["state"] == "ok"
