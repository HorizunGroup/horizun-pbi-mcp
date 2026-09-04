"""Regresiones de seguridad halladas por la auditoria multiagente Desktop."""
from __future__ import annotations

import json
import threading
import time
import zipfile

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.powerbi import desktop_capture, desktop_discovery
from horizun_pbi_mcp.powerbi import desktop_identity, desktop_launcher, refresh
from horizun_pbi_mcp.powerbi import uia_helper
from horizun_pbi_mcp.powerbi.errors import RefreshError, RefreshTimeoutError
from horizun_pbi_mcp.services import pbix_export
from tests.test_exportacion_pbix import (_AdaptadorFalso, _Abierto, _exportar,
                                         entorno)  # noqa: F401
from tests.test_helper_sin_com import _UiaFalso


def test_reuso_rechaza_pbip_acreditado_solo_por_titulo(tmp_path, monkeypatch):
    objetivo = tmp_path / "a" / "Demo.pbip"
    objetivo.parent.mkdir()
    objetivo.write_text("{}", encoding="utf-8")
    abierto = _Abierto(objetivo, launched_by_us=False)
    monkeypatch.setattr(desktop_identity, "identify", lambda *a, **k: {
        "desktop_pid": abierto.desktop_pid,
        "desktop_window_title": "Demo - Power BI Desktop",
        "project_path": None, "path_match": True,
        "identity_confidence": desktop_identity.MEDIUM,
        "identity_evidence": [{"signal": "path_match", "status": "by_title"}],
    })

    with pytest.raises(pbix_export.PbixExportError) as exc:
        pbix_export._identidad_verificada(abierto, objetivo)  # noqa: SLF001
    assert exc.value.details["reason"] == "desktop_reuse_path_unverified"


def test_fallback_no_hace_clic_si_invoke_abre_modal(monkeypatch):
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda _h: True)
    monkeypatch.setattr(uia_helper, "_hasta_que", lambda *a, **k: None)
    monkeypatch.setattr(uia_helper, "_modales", lambda *a, **k: [{
        "hwnd": 99, "kind": "confirm_replace", "owned_by_dialog": True}])
    clics = []
    monkeypatch.setattr(uia_helper, "clic_dinamico",
                        lambda *a, **k: clics.append(1))

    with pytest.raises(uia_helper.HelperError) as exc:
        uia_helper._confirmar(_UiaFalso(), 22, 4321)  # noqa: SLF001
    assert exc.value.detalles["reason"] == "modal_open_after_invoke"
    assert clics == []


def test_destino_reciente_pero_inalterado_no_cuenta_como_export_nuevo(tmp_path):
    destino = tmp_path / "reciente.pbix"
    destino.write_bytes(b"archivo anterior")
    antes = pbix_export._estado_archivo(destino, con_hash=True)  # noqa: SLF001

    with pytest.raises(pbix_export.PbixExportNotVerified) as exc:
        pbix_export.verificar_salida(destino, desde=time.time() - 1,
                                     antes=antes)
    assert exc.value.details["reason"] == "output_unchanged_from_preflight"


def test_el_helper_no_confunde_destino_preexistente_con_guardado(tmp_path):
    destino = tmp_path / "existente.pbix"
    destino.write_bytes(b"anterior")
    antes = pbix_export._estado_archivo(destino)  # noqa: SLF001
    espera = pbix_export.esperar_escritura_terminada(
        destino, timeout=0.2, gracia=0.1, antes=antes)
    assert espera["stable"] is False


def test_cuarentena_bloquea_lease_hasta_que_el_worker_termine(session):
    model = ActiveModel(host="localhost", port=51000,
                        connection_string="Data Source=localhost:51000",
                        catalog="Demo", pid=100, process_started=1000.0,
                        session_fingerprint="f")
    session.set_active_model(model)
    liberar = threading.Event()
    worker = threading.Thread(target=liberar.wait, name="horizun-refresh",
                              daemon=True)
    worker.start()
    session.quarantine_active_model(model, worker)

    with pytest.raises(RefreshError) as exc:
        with session.active_model_lease():
            pytest.fail("un lease entro durante la cuarentena")
    assert exc.value.details["reason"] == "refresh_still_running"

    liberar.set()
    worker.join(timeout=2)
    with session.active_model_lease() as recovered:
        assert recovered is model


def test_timeout_registra_cuarentena_antes_de_propagar(monkeypatch):
    liberar = threading.Event()
    vistos = []

    class Model:
        Tables = []
        Expressions = []

        def SaveChanges(self):  # noqa: N802
            liberar.wait(timeout=5)

    class Server:
        def CancelCommand(self):  # noqa: N802
            return None

    monkeypatch.setattr(refresh, "_GRACIA_TRAS_CANCELAR", 0.01)
    with pytest.raises(RefreshTimeoutError) as exc:
        refresh._guardar_con_plazo(  # noqa: SLF001
            Server(), Model(), 1, on_unconfirmed=lambda t: vistos.append(t))
    try:
        assert vistos and vistos[0].is_alive()
        assert exc.value.details["session_quarantined"] is True
    finally:
        liberar.set()
        if vistos:
            vistos[0].join(timeout=2)


def test_recuperacion_read_only_no_persiste_session_json(session, monkeypatch):
    old = ActiveModel(host="localhost", port=50000,
                      connection_string="Data Source=localhost:50000",
                      catalog="Old", pid=1, process_started=1.0,
                      session_fingerprint="old")
    session.set_active_model(old)
    session._verified_at_monotonic = 0.0  # noqa: SLF001
    persisted = session._session_file.read_bytes()  # noqa: SLF001
    candidate = {
        "status": "ok", "host": "localhost", "port": 51000,
        "connection_string": "Data Source=localhost:51000", "catalog": "New",
        "database_name": "New", "model_name": "New", "pid": 2,
        "create_time": 2.0, "workspace": "w", "session_fingerprint": "new",
    }
    monkeypatch.setattr(desktop_discovery, "verify_model",
                        lambda _m: {"status": "stale", "reason": "murio"})
    monkeypatch.setattr(desktop_discovery, "discover_instances",
                        lambda: [candidate])

    assert session.require_active_model().port == 51000
    assert session._session_file.read_bytes() == persisted  # noqa: SLF001

    # La recuperacion deja de ser transitoria cuando una mutacion va a usarla.
    assert session.require_active_model(for_mutation=True).port == 51000
    guardado = json.loads(session._session_file.read_text(encoding="utf-8"))  # noqa: SLF001
    assert guardado["active_model"]["port"] == 51000


def test_un_pbit_no_es_cualquier_zip_con_una_carpeta_report(tmp_path):
    destino = tmp_path / "falso.pbit"
    with zipfile.ZipFile(destino, "w") as zf:
        zf.writestr("Version", "1.28".encode("utf-16-le"))
        zf.writestr("Report/cualquier-cosa", b"x")
    with pytest.raises(pbix_export.PbixExportNotVerified) as exc:
        pbix_export._inspeccionar_plantilla(  # noqa: SLF001
            destino, espera_modelo=False)
    assert exc.value.details["summary"]["report_format"] == "none"


def test_captura_analiza_el_mismo_ultimo_frame_que_guarda(monkeypatch, tmp_path):
    variado = (b"\x00\x00\xff\x00" + b"\x00\xff\x00\x00" +
               b"\xff\x00\x00\x00" + b"\x10\x20\x30\x00")
    uniforme = b"\xff\xff\xff\x00" * 4
    frames = iter([(4, 1, variado), (4, 1, uniforme), (4, 1, uniforme)])
    monkeypatch.setattr(desktop_capture, "_assert_desktop_identity",
                        lambda *a: None)
    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda _pid: [
        desktop_capture.DesktopWindow(20, 777, "Demo - Power BI Desktop",
                                      "PBIDesktop", 4, 1)])
    monkeypatch.setattr(desktop_capture, "_capture_window_bgra",
                        lambda _h: next(frames))
    monkeypatch.setattr(desktop_capture.time, "sleep", lambda _s: None)
    monkeypatch.setattr(desktop_identity, "esperar_identidad_de_ventana",
                        lambda *a, **k: {"settled": True, "status": "settled_match"})
    opened = _Abierto(tmp_path / "Demo.pbix")

    result = desktop_capture.capture_opened(
        opened, timeout=2, settle_seconds=1, output_dir=tmp_path,
        data_loaded=False)
    assert result["frame_uniform"] is True
    assert result["capture_representative"] is False


def test_backup_de_exportacion_es_unico_incluso_en_el_mismo_instante(
        tmp_path, isolated_settings):
    destino = tmp_path / "Demo.pbix"
    destino.write_bytes(b"original")
    a = pbix_export._respaldar(destino)  # noqa: SLF001
    b = pbix_export._respaldar(destino)  # noqa: SLF001
    assert a != b
    assert a.read_bytes() == b.read_bytes() == b"original"


def test_close_fallido_no_declara_restaurado_sobre_writer_posible(
        entorno, monkeypatch):  # noqa: F811
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"original")
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda _opened: {"closed": False, "reason": "sigue vivo"})

    with pytest.raises(pbix_export.PbixRestoreFailed) as exc:
        _exportar(entorno, _AdaptadorFalso(contenido=b""),
                  out_path=str(destino), overwrite=True)
    assert exc.value.details["restore"]["restored"] is False
    pre = exc.value.details["cleanup"]["compensation_preconditions"]
    assert pre["close_verified"] is False


def test_quietud_para_compensar_se_reinicia_ante_un_cambio_tardio(tmp_path):
    destino = tmp_path / "Demo.pbix"
    destino.write_bytes(b"primero")

    def cambiar_tarde():
        time.sleep(0.3)
        destino.write_bytes(b"segundo contenido")

    writer = threading.Thread(target=cambiar_tarde, daemon=True)
    inicio = time.monotonic()
    writer.start()
    quietud = pbix_export._esperar_quietud_compensacion(  # noqa: SLF001
        destino, timeout=1.5, stable_seconds=0.5)
    writer.join(timeout=1)

    assert quietud["quiet"] is True
    assert quietud["state"]["size"] == len(b"segundo contenido")
    assert time.monotonic() - inicio >= 0.75


def test_finalize_delivery_propaga_confirm_reuse(session, monkeypatch, tmp_path):
    session._active_pbip = type("P", (), {"pbip_path": str(tmp_path / "Demo.pbip")})()
    recibido = {}

    def fake_export(_session, **kwargs):
        recibido.update(kwargs)
        return {"saved_as_verified": True}

    monkeypatch.setattr(pbix_export, "export", fake_export)
    result = pbix_export.finalize_delivery(session, confirm_reuse=True)
    assert recibido["confirm_reuse"] is True
    assert result["delivered"] is True
