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
        lambda value, timeout: {"path": "capture.png", "desktop_pid": 777})

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
        lambda value, timeout: {"path": "capture.png", "desktop_pid": 777})
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
        lambda value, timeout: {"path": "capture.png", "desktop_pid": 777})
    monkeypatch.setattr(
        desktop_launcher, "close",
        lambda value: (_ for _ in ()).throw(OSError("acceso denegado")))

    result = mcp.tools["pbi_validate_desktop_render"]("Ventas.pbix")

    assert result["ok"] is True
    assert result["desktop_close"]["reason"] == "desktop_close_failed"
    assert result["warnings"]


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
