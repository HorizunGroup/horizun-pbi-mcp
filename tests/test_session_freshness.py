"""Fase 1A — sesiones obsoletas y reutilizadas.

Que el puerto vuelva a estar abierto NO prueba que sea la misma sesion: Power
BI Desktop asigna un puerto distinto en cada arranque y el sistema puede
reutilizar tanto el puerto como el identificador de proceso.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import config as config_module
from config import ActiveModel
from powerbi import desktop_discovery
from powerbi import model_writer
from powerbi.desktop_discovery import StaleSessionError
from powerbi.errors import NoActiveModelError


def instancia(port=50000, pid=111, create_time=1000.0, catalog="cat-A",
              status="ok", **extra):
    inst = {"host": "localhost", "port": port, "pid": pid,
            "create_time": create_time, "catalog": catalog,
            "database_name": catalog, "model_name": "Model",
            "connection_string": f"Data Source=localhost:{port}",
            "workspace": r"C:\PBI\Workspace-A",
            "status": status, "table_count": 3, "warnings": [], **extra}
    inst["session_fingerprint"] = desktop_discovery.session_fingerprint(inst)
    return inst


def modelo_desde(inst):
    return ActiveModel(
        host=inst["host"], port=inst["port"],
        connection_string=inst["connection_string"], catalog=inst["catalog"],
        database_name=inst["database_name"], model_name=inst["model_name"],
        pid=inst["pid"], process_started=inst["create_time"],
        workspace=inst.get("workspace"),
        session_fingerprint=inst["session_fingerprint"])


@pytest.fixture
def instancias(monkeypatch):
    def _set(lista):
        monkeypatch.setattr(desktop_discovery, "discover_instances",
                            lambda: list(lista))
    return _set


# ------------------------------------------------------------ verify_model ---
def test_misma_sesion_es_ok(instancias):
    inst = instancia()
    instancias([inst])
    assert desktop_discovery.verify_model(modelo_desde(inst))["status"] == "ok"


def test_puerto_desaparecido_es_stale(instancias):
    inst = instancia()
    modelo = modelo_desde(inst)
    instancias([])
    res = desktop_discovery.verify_model(modelo)
    assert res["status"] == "stale"
    assert "puerto" in res["reason"]


def test_puerto_que_no_responde_es_stale(instancias):
    inst = instancia()
    modelo = modelo_desde(inst)
    instancias([instancia(status="unreachable")])
    assert desktop_discovery.verify_model(modelo)["status"] == "stale"


def test_puerto_tomado_por_otro_proceso_es_mismatch(instancias):
    """El caso peligroso: el puerto vive, pero es de otro."""
    modelo = modelo_desde(instancia(pid=111))
    instancias([instancia(pid=999)])
    res = desktop_discovery.verify_model(modelo)
    assert res["status"] == "mismatch"
    assert "999" in res["reason"]


def test_pid_reutilizado_con_otra_hora_de_arranque_es_mismatch(instancias):
    modelo = modelo_desde(instancia(pid=111, create_time=1000.0))
    instancias([instancia(pid=111, create_time=9999.0)])
    res = desktop_discovery.verify_model(modelo)
    assert res["status"] == "mismatch"
    assert "reutilizo" in res["reason"]


def test_mismo_pid_y_puerto_en_otro_workspace_es_mismatch(instancias):
    modelo = modelo_desde(instancia(workspace=r"C:\PBI\Workspace-A"))
    instancias([instancia(workspace=r"C:\PBI\Workspace-B")])
    res = desktop_discovery.verify_model(modelo)
    assert res["status"] == "mismatch"
    assert "workspace" in res["reason"]


def test_otro_catalogo_en_el_mismo_puerto_es_mismatch(instancias):
    modelo = modelo_desde(instancia(catalog="cat-A"))
    instancias([instancia(catalog="cat-B")])
    res = desktop_discovery.verify_model(modelo)
    assert res["status"] == "mismatch"
    assert "cat-B" in res["reason"]


def test_huella_distinta_es_mismatch(instancias):
    inst = instancia()
    modelo = modelo_desde(inst)
    modelo.session_fingerprint = "huella_antigua"
    instancias([inst])
    assert desktop_discovery.verify_model(modelo)["status"] == "mismatch"


def test_pequena_deriva_de_reloj_no_es_mismatch(instancias):
    modelo = modelo_desde(instancia(create_time=1000.0))
    inst = instancia(create_time=1000.4)
    inst["session_fingerprint"] = modelo.session_fingerprint
    instancias([inst])
    assert desktop_discovery.verify_model(modelo)["status"] == "ok"


# ------------------------------------------------------ huella de sesion -----
def test_la_huella_distingue_sesiones():
    a = desktop_discovery.session_fingerprint(
        {"port": 1, "pid": 2, "create_time": 3.0, "catalog": "x"})
    b = desktop_discovery.session_fingerprint(
        {"port": 1, "pid": 9, "create_time": 3.0, "catalog": "x"})
    c = desktop_discovery.session_fingerprint(
        {"port": 1, "pid": 2, "create_time": 3.0, "catalog": "y"})
    assert a != b and a != c and len(a) == 16


def test_la_huella_distingue_workspaces():
    base = {"port": 1, "pid": 2, "create_time": 3.0, "catalog": "x"}
    a = desktop_discovery.session_fingerprint(
        {**base, "workspace": r"C:\PBI\Workspace-A"})
    b = desktop_discovery.session_fingerprint(
        {**base, "workspace": r"C:\PBI\Workspace-B"})
    assert a != b


# --------------------------------------------------- integracion en sesion ---
def test_require_active_model_falla_si_la_sesion_esta_obsoleta(session, instancias):
    inst = instancia()
    session.set_active_model(modelo_desde(inst))
    instancias([])                                  # el puerto desaparece
    session._verified_fingerprint = None            # forzar re-verificacion

    with pytest.raises(StaleSessionError) as exc:
        session.require_active_model()
    assert exc.value.code == "stale_session"
    assert "pbi_select_model" in exc.value.message


def test_require_active_model_falla_si_el_puerto_es_de_otro(session, instancias):
    session.set_active_model(modelo_desde(instancia(pid=111)))
    instancias([instancia(pid=777)])
    session._verified_fingerprint = None

    with pytest.raises(StaleSessionError) as exc:
        session.require_active_model()
    assert exc.value.details["status"] == "mismatch"


def test_require_active_model_pasa_si_todo_coincide(session, instancias):
    inst = instancia()
    session.set_active_model(modelo_desde(inst))
    instancias([inst])
    session._verified_fingerprint = None
    assert session.require_active_model().port == inst["port"]


def test_cache_de_identidad_caduca_y_detecta_puerto_reutilizado(
        session, instancias, monkeypatch):
    """Regresion: antes, la primera validacion habilitaba cache para siempre."""
    now = [100.0]
    monkeypatch.setattr(config_module, "time",
                        SimpleNamespace(monotonic=lambda: now[0]),
                        raising=False)
    original = instancia(pid=111)
    session.set_active_model(modelo_desde(original))

    # Desktop muere y el mismo puerto pasa a otra instancia. Al vencer el TTL
    # debe descubrirlo sin que la prueba manipule campos privados de Session.
    instancias([instancia(pid=999)])
    now[0] += 1.0
    with pytest.raises(StaleSessionError) as exc:
        session.require_active_model()
    assert exc.value.details["status"] == "mismatch"
    assert session._verified_fingerprint is None


def test_mutacion_live_revalida_antes_de_conectar(
        session, instancias, monkeypatch):
    now = [200.0]
    monkeypatch.setattr(config_module, "time",
                        SimpleNamespace(monotonic=lambda: now[0]),
                        raising=False)
    session.set_active_model(modelo_desde(instancia(pid=111)))
    instancias([instancia(pid=777)])
    now[0] += 1.0

    conectado = []

    def no_debe_conectar(_model):
        conectado.append(True)
        raise AssertionError("no debe conectar a una identidad obsoleta")

    monkeypatch.setattr(model_writer, "connect", no_debe_conectar)
    with pytest.raises(StaleSessionError):
        model_writer.set_column_hidden(session, "T", "C", True)
    assert conectado == []


def test_verify_false_no_consulta_el_sistema(session, instancias):
    inst = instancia()
    session.set_active_model(modelo_desde(inst))

    def explota():
        raise AssertionError("no deberia consultarse")

    instancias([])
    session._verified_fingerprint = None
    # Con verify=False no se comprueba nada: uso interno para diagnostico.
    assert session.require_active_model(verify=False).port == inst["port"]


def test_sin_modelo_activo_el_mensaje_sigue_siendo_el_de_siempre(session):
    with pytest.raises(NoActiveModelError):
        session.require_active_model()


def test_una_sesion_cargada_de_disco_arranca_sin_verificar(isolated_settings):
    """No se confia en lo que quedo guardado en session.json."""
    from config import Session

    s = Session(isolated_settings)
    assert s._verified_fingerprint is None


# ---------------------------------------------- puertos obsoletos en disco ---
def test_los_archivos_de_puerto_huerfanos_no_se_borran(monkeypatch, tmp_path):
    """El descubrimiento los marca como inalcanzables, pero no los elimina."""
    ws = tmp_path / "AnalysisServicesWorkspace_x" / "Data"
    ws.mkdir(parents=True)
    port_file = ws / "msmdsrv.port.txt"
    port_file.write_text("59999", encoding="utf-8")

    monkeypatch.setattr(desktop_discovery, "_ports_from_processes", lambda: [])
    monkeypatch.setattr(desktop_discovery, "_workspace_port_files",
                        lambda: [{"host": "localhost", "port": 59999, "pid": None,
                                  "create_time": None, "source": "port_file",
                                  "workspace": str(ws.parent)}])
    monkeypatch.setattr(desktop_discovery, "_port_is_listening",
                        lambda host, port: False)

    res = desktop_discovery.discover_instances()
    assert len(res) == 1
    assert res[0]["status"] == "unreachable"
    assert res[0]["source"] == "port_file"
    assert port_file.exists(), "no se elimina nada del disco del usuario"


def test_descubrimiento_combina_pid_y_workspace(monkeypatch):
    monkeypatch.setattr(desktop_discovery, "_ports_from_processes", lambda: [{
        "host": "localhost", "port": 50000, "pid": 321,
        "create_time": 123.0, "source": "process",
    }])
    monkeypatch.setattr(desktop_discovery, "_workspace_port_files", lambda: [{
        "host": "localhost", "port": 50000, "pid": None,
        "create_time": None, "source": "port_file",
        "workspace": r"C:\PBI\Workspace-A",
    }])
    monkeypatch.setattr(desktop_discovery, "_port_is_listening",
                        lambda _host, _port: True)
    monkeypatch.setattr(desktop_discovery, "_enrich", lambda host, port: {
        **instancia(port=port), "host": host,
    })

    [found] = desktop_discovery.discover_instances()
    assert found["pid"] == 321
    assert found["create_time"] == 123.0
    assert found["workspace"] == r"C:\PBI\Workspace-A"
