"""Cerrar la ventana que `pbi_export_pbix(leave_open=true)` deja abierta.

Tras exportar, la ventana queda sobre el `.pbix` recien guardado -que Desktop
mantiene en su TempSaves- y `pbi_close_desktop` con la ruta del `.pbip`
original contestaba `was_open=false`. No habia ninguna forma soportada de
cerrarla desde el MCP. Ahora la exportacion devuelve `desktop_session`
(PID + hora de arranque) y el cierre lo acepta, verificando la identidad y
negandose ante un PID reciclado.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi import desktop_identity as di
from horizun_pbi_mcp.powerbi import desktop_launcher as dl
from horizun_pbi_mcp.services import pbix_export
from tests.test_exportacion_pbix import _AdaptadorFalso, entorno, _exportar  # noqa: F401


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def dec(fn):
            self.tools[fn.__name__] = fn
            return fn
        return dec


def _proceso_falso(monkeypatch, *, nombre="PBIDesktop.exe", creado=1000.0,
                   existe=True):
    import psutil

    class _Proc:
        def __init__(self, pid):
            if not existe:
                raise psutil.NoSuchProcess(pid)
            self.pid = pid

        def name(self):
            return nombre

        def create_time(self):
            return creado

    monkeypatch.setattr(psutil, "Process", _Proc)


# ====================== 1) la exportacion deja una referencia usable ======
def test_la_exportacion_devuelve_la_sesion_con_pid_y_arranque(entorno, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dl, "_process_started", lambda pid: 1000.0)
    salida = _exportar(entorno, _AdaptadorFalso())

    sesion = salida["desktop_session"]
    assert sesion["desktop_pid"] == 4321
    assert sesion["desktop_started"] == 1000.0
    assert sesion["document"].endswith("Demo.pbix")
    assert "pbi_close_desktop(desktop_pid=4321" in sesion["close_with"]


def test_si_la_ventana_siguio_por_titulo_no_se_reabre(entorno, monkeypatch):  # noqa: F811
    """Sin descriptor sobre el destino, el titulo demuestra que siguio."""
    entorno["estado"]["sigue_abierto"] = None        # sin handle sobre el .pbix
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto", lambda ruta: None)
    from tests.test_exportacion_pbix import _Abierto

    reaperturas = []

    def _open(ruta, timeout=300, reuse_open=True):
        if str(ruta).casefold().endswith(".pbix"):
            reaperturas.append(ruta)
            pytest.fail("se reabrio el entregable")
        return _Abierto(ruta)

    monkeypatch.setattr(dl, "open_pbix", _open)
    monkeypatch.setattr(di, "esperar_identidad_de_ventana",
                        lambda pid, obj, timeout=60.0, **k: {
                            "status": di.IDENTIDAD_ASENTADA, "settled": True,
                            "polls": 2, "waited_seconds": 1.0})
    monkeypatch.setattr(dl, "_process_started", lambda pid: 1000.0)

    salida = pbix_export.export(
        entorno["session"], adapter=_AdaptadorFalso(), timeout=5,
        out_path=str(entorno["tmp"] / "salida" / "Demo.pbix"))

    assert salida["final_state"]["same_window_followed"] is True
    assert salida["final_state"]["reopened"] is False
    assert salida["final_state"]["window_follow"]["settled"] is True
    assert reaperturas == []


# ============================ 2) cerrar por identidad ======================
def test_cierra_por_identidad_y_verifica_que_el_proceso_murio(monkeypatch):
    vivo = {"v": True}

    def _identidad(pid, started):
        if not vivo["v"]:
            return {"ok": False, "reason": "process_gone"}
        return {"ok": True, "pid": pid, "create_time": started}

    monkeypatch.setattr(dl, "_identidad_del_proceso", _identidad)
    monkeypatch.setattr(di, "titulos_de_ventana", lambda pid: ["Demo"])
    cerrados = []

    def _close(abierto, force=False):
        cerrados.append((abierto.desktop_pid, abierto.desktop_started, force))
        vivo["v"] = False
        return {"closed": True, "pid": abierto.desktop_pid, "killed": 0,
                "children": 0, "survivors": []}

    monkeypatch.setattr(dl, "close", _close)

    r = dl.close_desktop_by_identity(4321, 1000.0)

    assert cerrados == [(4321, 1000.0, True)]
    assert r["closed"] is True and r["was_open"] is True
    assert r["verified_closed"] is True


def test_un_pid_reciclado_no_se_toca(monkeypatch):
    _proceso_falso(monkeypatch, nombre="notepad.exe")
    monkeypatch.setattr(dl, "close",
                        lambda *a, **k: pytest.fail("se termino otro proceso"))

    r = dl.close_desktop_by_identity(4321, 1000.0)

    assert r["closed"] is False
    assert r["reason"] == "desktop_pid_reused"
    assert r["identity"]["actual_process"] == "notepad.exe"


def test_otra_hora_de_arranque_tambien_es_un_pid_reciclado(monkeypatch):
    _proceso_falso(monkeypatch, creado=5000.0)
    monkeypatch.setattr(dl, "close",
                        lambda *a, **k: pytest.fail("se termino otro proceso"))

    r = dl.close_desktop_by_identity(4321, 1000.0)

    assert r["reason"] == "desktop_pid_reused"
    assert r["identity"]["actual_started"] == 5000.0


def test_sin_hora_de_arranque_no_se_cierra_nada(monkeypatch):
    _proceso_falso(monkeypatch)
    monkeypatch.setattr(dl, "close",
                        lambda *a, **k: pytest.fail("se cerro sin identidad"))

    r = dl.close_desktop_by_identity(4321, None)

    assert r["closed"] is False
    assert r["reason"] == "desktop_identity_unverifiable"
    assert "desktop_started" in r["identity"]["detail"]


def test_un_proceso_que_ya_no_existe_es_un_no_op_declarado(monkeypatch):
    _proceso_falso(monkeypatch, existe=False)

    r = dl.close_desktop_by_identity(4321, 1000.0)

    assert r["closed"] is True and r["was_open"] is False


def test_con_ruta_se_exige_que_no_sirva_otro_documento(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "_identidad_del_proceso",
                        lambda pid, started: {"ok": True, "pid": pid,
                                              "create_time": started})
    monkeypatch.setattr(di, "titulos_de_ventana",
                        lambda pid: ["Otro - Power BI Desktop"])
    monkeypatch.setattr(dl, "close",
                        lambda *a, **k: pytest.fail("se cerro otro documento"))

    r = dl.close_desktop_by_identity(4321, 1000.0,
                                     expected_document=tmp_path / "Demo.pbix")

    assert r["closed"] is False
    assert r["reason"] == "desktop_serves_other_document"
    assert r["document_match"] == di.IDENTIDAD_OTRO_DOCUMENTO


# =============================== 3) la tool =================================
def test_la_tool_cierra_por_pid_sin_necesitar_ruta(monkeypatch, session):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import dax_tools

    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    dax_tools.register(mcp)
    recibido = {}
    monkeypatch.setattr(
        dl, "close_desktop_by_identity",
        lambda pid, started, expected_document=None: recibido.update(
            pid=pid, started=started, doc=expected_document) or {
                "closed": True, "was_open": True, "verified_closed": True})
    monkeypatch.setattr(dl, "close_desktop_by_path",
                        lambda p: pytest.fail("se busco por ruta"))

    r = mcp.tools["pbi_close_desktop"](desktop_pid=4321, desktop_started=1000.0,
                                       confirm=True)

    assert r["ok"] is True and r["verified_closed"] is True
    assert recibido == {"pid": 4321, "started": 1000.0, "doc": None}


def test_la_tool_por_pid_sigue_exigiendo_confirm(monkeypatch, session):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import dax_tools

    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    dax_tools.register(mcp)
    monkeypatch.setattr(dl, "close_desktop_by_identity",
                        lambda *a, **k: pytest.fail("cerro sin confirm"))

    r = mcp.tools["pbi_close_desktop"](desktop_pid=4321, desktop_started=1000.0)

    assert r["ok"] is False and "confirm" in r["message"]


# ==================== 4) por ruta, un .pbix se encuentra por titulo =========
def test_un_pbix_sin_descriptor_se_cierra_por_titulo_y_lo_dice(monkeypatch, tmp_path):
    pbix = tmp_path / "Demo.pbix"
    pbix.write_bytes(b"PK")
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto", lambda p: None)
    monkeypatch.setattr(dl, "_pid_por_titulo_de_ventana",
                        lambda stem, objetivo=None: 4242)
    monkeypatch.setattr(dl, "_process_started", lambda pid: 123.0)
    monkeypatch.setattr(dl, "close", lambda a, force=False: {
        "closed": True, "pid": a.desktop_pid, "killed": 0, "children": 0,
        "survivors": []})

    r = dl.close_desktop_by_path(pbix)

    assert r["was_open"] is True
    assert r["matched_by"] == "window_title"


def test_un_pbip_no_abierto_sigue_siendo_no_op_con_pista(monkeypatch, tmp_path):
    pbip = tmp_path / "Demo.pbip"
    pbip.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto", lambda p: None)
    monkeypatch.setattr(dl, "_pid_por_titulo_de_ventana",
                        lambda *a, **k: pytest.fail("un .pbip ya lo mira "
                                                    "proceso_con_archivo_abierto"))

    r = dl.close_desktop_by_path(pbip)

    assert r["was_open"] is False
    assert "desktop_pid" in r["hint"]

