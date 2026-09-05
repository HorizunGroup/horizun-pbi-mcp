"""Captura de Desktop: regresiones sin abrir Power BI ni cambiar el foco."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.powerbi import desktop_capture, desktop_launcher
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.tools import dax_tools


class _McpCaptura:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorate


def _opened(*, launched_by_us=True):
    return desktop_launcher.OpenedPbix(
        pbix_path=r"C:\informes\Ventas.pbix",
        instance={"port": 51234, "table_count": 2},
        desktop_pid=777,
        launched_by_us=launched_by_us,
        waited_seconds=3.5,
        desktop_started=1234.0,
    )


# ------------------------------------------ vista de captura (page + fit) ----
@pytest.fixture
def pbip_sintetico(session, tmp_path, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.pbip import project_locator
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    # Las tools resuelven la sesion con `get_session()`: sin inyectarla, una
    # prueba de tool leeria el singleton real de quien ejecuta la suite.
    monkeypatch.setattr(cfg, "_session", session)
    return pbip, session.require_active_pbip()


def test_planificar_vista_propone_fit_sin_escribir(pbip_sintetico):
    """El planificador DECIDE; no toca el disco. Ahi vive la atomicidad."""
    pbip, active = pbip_sintetico
    from horizun_pbi_mcp.pbip import pbir_reader

    pagina = pbir_reader.list_pages(active)[0]["name"]
    page_json = Path(pbir_reader.pages_dir(active)) / pagina / "page.json"
    antes = page_json.read_bytes()

    plan = desktop_capture.planificar_vista_de_captura(pbip, fit_to_page=True)

    assert page_json.read_bytes() == antes, "el planificador escribio en disco"
    assert plan["page_id"] in (pagina, "")
    if plan["cambios"]:
        import json as _json
        propuesto = _json.loads(plan["cambios"][page_json].decode("utf-8"))
        assert propuesto["displayOption"] == "FitToPage"


def test_planificar_vista_propone_la_pagina_activa(pbip_sintetico):
    import json

    pbip, active = pbip_sintetico
    from horizun_pbi_mcp.pbip import pbir_reader
    from horizun_pbi_mcp.services import pbir_edit

    nueva = pbir_edit.duplicate_page(
        active, pbir_reader.list_pages(active)[0]["display_name"], "Detalle")
    pages_json = Path(pbir_reader.pages_dir(active)) / "pages.json"
    antes = pages_json.read_bytes()

    plan = desktop_capture.planificar_vista_de_captura(pbip, page="Detalle")

    assert pages_json.read_bytes() == antes, "el planificador escribio en disco"
    assert plan["page_id"] == nueva["page_id"]
    propuesto = json.loads(plan["cambios"][pages_json].decode("utf-8"))
    assert propuesto["activePageName"] == nueva["page_id"]


def test_planificar_vista_rechaza_pagina_inexistente(pbip_sintetico):
    pbip, _active = pbip_sintetico
    with pytest.raises(ValidationError) as exc:
        desktop_capture.planificar_vista_de_captura(pbip, page="NoExiste")
    assert exc.value.details["page"] == "NoExiste"


def test_render_con_page_exige_pbip(monkeypatch, tmp_path):
    """Un .pbix compilado no se puede preparar sin editarlo.

    La guarda tiene que saltar ANTES de abrir nada: si desaparece, esta
    prueba falla al instante en vez de lanzar un Desktop real y esperar
    300 s, que es lo que paso una vez.
    """
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    pbix = tmp_path / "Ventas.pbix"
    pbix.write_bytes(b"pbix")
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: pytest.fail("se intento abrir Desktop"))

    r = mcp.tools["pbi_validate_desktop_render"](str(pbix), page="Portada")
    assert r["ok"] is False
    assert "pbip" in r["message"].casefold()


def test_render_con_sesion_abierta_y_page_no_demostrada_falla_claro(
        pbip_sintetico, monkeypatch):
    """Con la sesion abierta la pagina se elige en la ventana; si no se puede
    DEMOSTRAR, la tool falla en vez de capturar otra pagina."""
    from horizun_pbi_mcp.powerbi import desktop_navigation

    pbip, _active = pbip_sintetico
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda _p: 4242)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(launched_by_us=False))
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda opened, page=None, fit_to_page=False, adapter=None: {
                            "page": {"verified": False, "reason": "sin pestañas"}})
    capturas = []
    monkeypatch.setattr(desktop_capture, "capture_opened",
                        lambda *a, **k: capturas.append(1))

    r = mcp.tools["pbi_validate_desktop_render"](str(pbip), page="Portada",
                                                 confirm_reuse=True)
    assert r["ok"] is False
    assert r["details"]["reason"] == "desktop_open_page_unverified"
    assert capturas == []


def test_elige_informe_por_titulo_antes_que_splash_mas_grande():
    splash = desktop_capture.DesktopWindow(
        1, 777, "Power BI Desktop", "Splash", 1800, 1200)
    report = desktop_capture.DesktopWindow(
        2, 777, "Ventas - Power BI Desktop", "PBIDesktop", 1200, 800)

    chosen = desktop_capture._choose_window(  # noqa: SLF001
        [splash, report], r"C:\informes\Ventas.pbix")

    assert chosen.hwnd == 2


def test_no_adivina_entre_dos_ventanas_ambiguas():
    first = desktop_capture.DesktopWindow(1, 777, "Dialogo A", "Popup", 800, 600)
    second = desktop_capture.DesktopWindow(2, 777, "Dialogo B", "Popup", 1200, 900)

    with pytest.raises(desktop_capture.DesktopCaptureError) as exc:
        desktop_capture._choose_window(  # noqa: SLF001
            [first, second], r"C:\informes\Ventas.pbix")

    assert exc.value.details["reason"] == "desktop_window_ambiguous"


def test_png_estandar_conserva_el_orden_bgr_a_rgb():
    # Un pixel azul y uno rojo, en el orden BGRA devuelto por GetDIBits.
    png = desktop_capture._encode_png(  # noqa: SLF001
        2, 1, b"\xff\x00\x00\x00\x00\x00\xff\x00")

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    position = 8
    idat = b""
    while position < len(png):
        length = struct.unpack(">I", png[position:position + 4])[0]
        kind = png[position + 4:position + 8]
        payload = png[position + 8:position + 8 + length]
        if kind == b"IDAT":
            idat += payload
        position += 12 + length
    assert zlib.decompress(idat) == b"\x00\x00\x00\xff\xff\x00\x00"


def test_capture_opened_escribe_png_atomico_sin_usar_foco(tmp_path, monkeypatch):
    opened = _opened()
    window = desktop_capture.DesktopWindow(
        20, 777, "Ventas - Power BI Desktop", "PBIDesktop", 2, 1)
    identities = []
    monkeypatch.setattr(
        desktop_capture, "_assert_desktop_identity",
        lambda pid, started: identities.append((pid, started)))
    monkeypatch.setattr(desktop_capture, "_enumerate_windows",
                        lambda pid: [window])
    monkeypatch.setattr(
        desktop_capture, "_capture_window_bgra",
        lambda hwnd: (2, 1, b"\xff\x00\x00\x00\x00\x00\xff\x00"))

    result = desktop_capture.capture_opened(
        opened, timeout=1, output_dir=tmp_path)

    target = Path(result["path"])
    assert target.parent == tmp_path.resolve()
    assert target.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result["capture_method"] == "PrintWindow"
    assert result["focus_required"] is False
    assert result["hwnd"] == 20
    assert identities == [(777, 1234.0)]
    assert not list(tmp_path.glob("*.tmp"))


def test_rechaza_pid_reciclado_antes_de_enumerar_ventanas(monkeypatch):
    import psutil

    fake = SimpleNamespace(name=lambda: "notepad.exe", create_time=lambda: 1234.0)
    monkeypatch.setattr(psutil, "Process", lambda pid: fake)

    with pytest.raises(desktop_capture.DesktopCaptureError) as exc:
        desktop_capture._assert_desktop_identity(777, 1234.0)  # noqa: SLF001

    assert exc.value.details["reason"] == "desktop_pid_reused"


def test_tool_cierra_solo_el_desktop_que_ella_abrio(monkeypatch):
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    opened = _opened(launched_by_us=True)
    closed = []
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: opened)
    monkeypatch.setattr(
        desktop_launcher, "close",
        lambda value: closed.append(value) or {"closed": True, "pid": 777})
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **_kw: {"path": "capture.png", "desktop_pid": 777})

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is True
    assert result["desktop_close"]["closed"] is True
    assert closed == [opened]


def test_tool_no_cierra_una_sesion_preexistente(monkeypatch):
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    opened = _opened(launched_by_us=False)
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: opened)
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **_kw: {"path": "capture.png", "desktop_pid": 777})
    monkeypatch.setattr(
        desktop_launcher, "close",
        lambda value: pytest.fail("no debe cerrar una ventana del usuario"))

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is True
    assert result["desktop_close"]["closed"] is False
    assert result["reused_open_session"] is True


def test_tool_compensa_apertura_aunque_falle_la_captura(monkeypatch):
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    opened = _opened(launched_by_us=True)
    closed = []
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: opened)
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda *a, **k: (_ for _ in ()).throw(
            desktop_capture.DesktopCaptureError("sin frame")))
    monkeypatch.setattr(
        desktop_launcher, "close",
        lambda value: closed.append(value) or {"closed": True})

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is False
    assert result["error"] == "desktop_capture_failed"
    assert closed == [opened]


def test_fallo_al_cerrar_no_enmascara_una_captura_valida(monkeypatch):
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    opened = _opened(launched_by_us=True)
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: opened)
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **_kw: {"path": "capture.png", "desktop_pid": 777})
    monkeypatch.setattr(
        desktop_launcher, "close",
        lambda value: (_ for _ in ()).throw(OSError("acceso denegado")))

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is True
    assert result["desktop_close"]["reason"] == "desktop_close_failed"
    assert result["warnings"]


# ------------------------------------- captura con datos (refresh + aviso) ---
def _sin_datos(*_a, **_k):
    return {"data_loaded": False, "tables_checked": 3, "tables_with_rows": 0,
            "reason": "el modelo esta abierto pero SIN datos"}


def test_la_captura_avisa_cuando_el_modelo_no_tiene_datos(monkeypatch):
    """Un .pbip recien abierto rinde tablas EN BLANCO: la foto no es prueba."""
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(launched_by_us=False))
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **_kw: {"path": "capture.png", "desktop_pid": 777})
    monkeypatch.setattr(dax_tools, "_estado_de_datos", _sin_datos)

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is True
    assert result["data_loaded"] is False
    assert any("NO es representativa" in w for w in result["warnings"])


def test_refresh_true_refresca_antes_de_capturar(monkeypatch):
    """El orden importa: refrescar DESPUES de capturar no sirve de nada."""
    from horizun_pbi_mcp.powerbi import desktop_discovery
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    mcp = _McpCaptura()
    dax_tools.register(mcp)
    orden = []
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(launched_by_us=False))
    monkeypatch.setattr(desktop_discovery, "select_model",
                        lambda *a, **k: SimpleNamespace(to_dict=dict))
    monkeypatch.setattr(
        refresh_mod, "refresh_model",
        lambda *a, **k: orden.append("refresh") or {"status": "ok"})
    monkeypatch.setattr(dax_tools, "_estado_de_datos",
                        lambda *a, **k: {"data_loaded": True})

    def captura(value, **kw):
        orden.append(("captura", kw.get("settle_seconds")))
        return {"path": "capture.png", "desktop_pid": 777}

    monkeypatch.setattr(desktop_capture, "capture_opened", captura)

    result = mcp.tools["pbi_validate_desktop_render"](
        "Ventas.pbix", refresh=True, confirm=True)

    assert result["ok"] is True
    assert orden[0] == "refresh"
    assert orden[1][0] == "captura" and orden[1][1] > 0, (
        "tras refrescar hay que esperar a que la ventana deje de repintar")
    assert result["refresh"]["status"] == "ok"


def test_sin_refresh_no_se_toca_la_seleccion_de_modelo(monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_discovery

    mcp = _McpCaptura()
    dax_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(launched_by_us=False))
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **_kw: {"path": "capture.png", "desktop_pid": 777})
    monkeypatch.setattr(dax_tools, "_estado_de_datos",
                        lambda *a, **k: {"data_loaded": True})
    monkeypatch.setattr(
        desktop_discovery, "select_model",
        lambda *a, **k: pytest.fail("sin refresh no hay que seleccionar nada"))

    assert mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")["ok"] is True


def test_el_fotograma_se_da_por_bueno_cuando_deja_de_cambiar(monkeypatch):
    """La sincronizacion es por evento observable, no por un plazo fijo."""
    frames = [(2, 1, b"\x00" * 8), (2, 1, b"\x01" * 8), (2, 1, b"\x01" * 8)]
    monkeypatch.setattr(desktop_capture, "time",
                        SimpleNamespace(monotonic=lambda: 0.0,
                                        sleep=lambda _s: None))
    monkeypatch.setattr(desktop_capture, "_capture_window_bgra",
                        lambda _h: frames.pop(0))
    inicial = desktop_capture._encode_png(2, 1, b"\xAA" * 8)

    # monotonic fijo en 0 agotaria el bucle; se usa un reloj que avanza.
    reloj = iter([0.0, 1.0, 2.0, 3.0, 4.0, 99.0])
    monkeypatch.setattr(desktop_capture, "time",
                        SimpleNamespace(monotonic=lambda: next(reloj),
                                        sleep=lambda _s: None))

    ancho, alto, png, estable = desktop_capture._fotograma_estable(
        1, inicial, 2, 1, 10.0)

    assert estable is True
    assert (ancho, alto) == (2, 1)
    assert png == desktop_capture._encode_png(2, 1, b"\x01" * 8)


# ------------------------------------------------- path / pbip_path / activo --
def test_las_tools_de_desktop_aceptan_pbip_path(monkeypatch, pbip_sintetico):
    pbip, _active = pbip_sintetico
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    vistos = []
    monkeypatch.setattr(
        desktop_launcher, "close_desktop_by_path",
        lambda p: vistos.append(p) or {"was_open": False, "closed": False})

    result = mcp.tools["pbi_close_desktop"](pbip_path=str(pbip), confirm=True)

    assert result["ok"] is True
    assert vistos == [str(Path(pbip).resolve())]


def test_close_desktop_sin_ruta_usa_el_proyecto_activo(monkeypatch, pbip_sintetico):
    pbip, _active = pbip_sintetico
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    vistos = []
    monkeypatch.setattr(
        desktop_launcher, "close_desktop_by_path",
        lambda p: vistos.append(p) or {"was_open": False, "closed": False})

    result = mcp.tools["pbi_close_desktop"](confirm=True)

    assert result["ok"] is True
    assert vistos == [str(Path(pbip).resolve())]


def test_sin_ruta_ni_proyecto_activo_se_dice_que_falta(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg

    monkeypatch.setattr(cfg, "_session", session)
    mcp = _McpCaptura()
    dax_tools.register(mcp)

    result = mcp.tools["pbi_close_desktop"](confirm=True)

    assert result["ok"] is False
    assert result["error"] == "validation_error"


def test_path_y_pbip_path_distintos_no_se_adivinan(pbip_sintetico):
    pbip, _active = pbip_sintetico
    mcp = _McpCaptura()
    dax_tools.register(mcp)

    result = mcp.tools["pbi_close_desktop"](
        path=str(pbip), pbip_path=str(pbip) + "x", confirm=True)

    assert result["ok"] is False
    assert result["error"] == "validation_error"


def test_launcher_no_marca_como_propia_una_ventana_existente_al_forzar(
        tmp_path, monkeypatch):
    report = tmp_path / "existente.pbix"
    report.write_bytes(b"fixture")
    monkeypatch.setattr(
        desktop_launcher, "proceso_con_archivo_abierto", lambda path: 888)
    monkeypatch.setattr(
        desktop_launcher, "find_executable",
        lambda: pytest.fail("no debe lanzar otra sesion"))

    with pytest.raises(ValidationError) as exc:
        desktop_launcher.open_pbix(report, reuse_open=False)

    assert getattr(exc.value, "details", {})["reason"] == \
        "desktop_file_already_open"
