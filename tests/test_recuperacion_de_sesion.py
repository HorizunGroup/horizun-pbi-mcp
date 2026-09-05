"""Una sesion con el puerto muerto se recupera con la regla de `pbi_select_model`.

Al reiniciar Power BI Desktop, `session.json` conservaba un puerto que ya no
existia y TODAS las tools -hasta `pbi_capabilities`- fallaban con
`stale_session`, cada una a su manera. La recuperacion vive ahora en un solo
sitio, `Session.require_active_model` -> `desktop_discovery.recuperar_sesion`:

- una sola instancia viva y verificable: se selecciona y la respuesta lo
  declara en `session_recovery`;
- varias: ambiguedad con los candidatos y la llamada exacta;
- ninguna: error accionable.

Y dos cosas que NO hace: reconectar a un puerto por estar abierto -si lo
ocupa otro proceso es otra instancia- ni reproducir ninguna operacion.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.powerbi import desktop_discovery as dd
from horizun_pbi_mcp.tools._common import guard


def _instancia(port, *, pid=200, catalog="Demo", status="ok", **extra):
    base = {"host": "localhost", "port": port,
            "connection_string": f"Data Source=localhost:{port}",
            "catalog": catalog, "database_name": catalog, "model_name": "Model",
            "table_count": 3, "tables_sample": ["Ventas"], "status": status,
            "warnings": [], "pid": pid, "create_time": 2000.0 + pid,
            "session_fingerprint": f"fp-{port}-{pid}"}
    base.update(extra)
    return base


@pytest.fixture
def sesion_caducada(session, monkeypatch):
    """La sesion apunta al puerto 50000 y ese motor ya no existe."""
    viejo = ActiveModel(host="localhost", port=50000,
                        connection_string="Data Source=localhost:50000",
                        catalog="Demo", pid=100, process_started=1000.0,
                        session_fingerprint="fp-viejo")
    monkeypatch.setattr(dd, "discover_instances", lambda: [])
    session.set_active_model(viejo)
    # La cache de verificacion caduca en un segundo; se invalida a mano para
    # que la siguiente llamada vuelva a mirar.
    session._invalidate_model_verification()             # noqa: SLF001
    return session, viejo


def test_con_una_sola_instancia_viva_se_reconecta_y_se_declara(
        sesion_caducada, monkeypatch):
    session, viejo = sesion_caducada
    nueva = _instancia(50123, pid=300)
    monkeypatch.setattr(dd, "discover_instances", lambda: [nueva])

    modelo = session.require_active_model()

    assert modelo.port == 50123
    assert modelo.pid == 300
    assert session.active_model.port == 50123
    nota = session.consume_recovery()
    assert nota["recovered"] is True
    assert nota["previous"] == {"port": 50000, "pid": 100, "catalog": "Demo"}
    assert nota["selected"]["port"] == 50123
    assert session.consume_recovery() is None, "la nota se entrega una vez"


def test_sin_instancias_vivas_el_error_es_accionable(sesion_caducada, monkeypatch):
    session, _ = sesion_caducada
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50000, status="unreachable",
                                            catalog=None)])

    with pytest.raises(dd.StaleSessionError) as fallo:
        session.require_active_model()

    assert fallo.value.details["recovery"] == "no_instances"
    assert "pbi_select_model" in str(fallo.value)
    assert session.active_model.port == 50000, "no se cambio la sesion"


def test_con_varias_instancias_no_se_elige_a_ciegas(sesion_caducada, monkeypatch):
    session, _ = sesion_caducada
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50123, pid=300),
                                 _instancia(50456, pid=400)])

    with pytest.raises(dd.StaleSessionError) as fallo:
        session.require_active_model()

    detalles = fallo.value.details
    assert detalles["recovery"] == "ambiguous"
    assert detalles["ports"] == [50123, 50456]
    assert detalles["candidates"][0]["select_with"] == "pbi_select_model(port=50123)"
    assert session.active_model.port == 50000
    assert session.consume_recovery() is None


def test_el_puerto_viejo_ocupado_por_otro_proceso_no_se_reconecta_solo(
        sesion_caducada, monkeypatch):
    """Nunca se reconecta 'porque el puerto volvio a abrirse'.

    Si lo ocupa otro proceso, es OTRA instancia sirviendo quien sabe que. Eso
    es un `mismatch`, no un puerto muerto: se exige seleccion explicita y se
    listan los candidatos con la llamada exacta.
    """
    session, _ = sesion_caducada
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50000, pid=999)])

    with pytest.raises(dd.StaleSessionError) as fallo:
        session.require_active_model()

    detalles = fallo.value.details
    assert detalles["status"] == "mismatch"
    assert detalles["recovery"] == "explicit_selection_required"
    assert detalles["candidates"][0]["select_with"] == "pbi_select_model(port=50000)"
    assert session.active_model.pid == 100, "se adopto otro proceso a ciegas"
    assert session.consume_recovery() is None


def test_la_respuesta_de_la_tool_lleva_la_recuperacion(sesion_caducada,
                                                       monkeypatch):
    from horizun_pbi_mcp import config as cfg

    session, _ = sesion_caducada
    monkeypatch.setattr(cfg, "_session", session)
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50123, pid=300)])

    salida = guard(lambda: {"port": session.require_active_model().port},
                   operation="pbi_algo")

    assert salida["ok"] is True and salida["port"] == 50123
    assert salida["session_recovery"]["selected"]["port"] == 50123


def test_sin_recuperacion_la_respuesta_no_lleva_el_campo(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg

    monkeypatch.setattr(cfg, "_session", session)
    salida = guard(lambda: {"x": 1}, operation="pbi_algo")
    assert "session_recovery" not in salida


def test_una_sesion_sana_no_se_toca(session, monkeypatch):
    sano = _instancia(50123, pid=300)
    monkeypatch.setattr(dd, "discover_instances", lambda: [sano])
    modelo = dd.select_model(session)
    session._invalidate_model_verification()             # noqa: SLF001

    assert session.require_active_model() is modelo
    assert session.consume_recovery() is None
