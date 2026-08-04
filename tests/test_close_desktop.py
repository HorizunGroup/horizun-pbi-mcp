"""Cerrar la instancia de Desktop de UN proyecto, sin llevarse otra (#8).

El ciclo real -editar, abrir, mirar, editar- chocaba cinco veces por sesion con
`project_open_in_desktop` sin ninguna salida desde el MCP: habia que ir a
PowerShell a matar el proceso. Y el atajo peligroso era matar PBIDesktop.exe a
ciegas, llevandose la ventana de OTRO informe con trabajo sin guardar.

Lo que se vigila: identidad verificada (nunca el PID a secas), verificacion
final re-comprobando que el archivo ya no este abierto, y confirm obligatorio
-cerrar descarta lo no guardado, y en .pbip eso incluye los datos refrescados-.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import desktop_launcher as dl
from horizun_pbi_mcp.powerbi.errors import ValidationError


@pytest.fixture
def pbip(tmp_path):
    f = tmp_path / "Proyecto.pbip"
    f.write_text("{}", encoding="utf-8")
    return f


# ------------------------------------------------------------ la tool exige ---
def test_sin_confirm_no_se_cierra_nada(monkeypatch, pbip):
    from horizun_pbi_mcp.tools import dax_tools

    class _Mcp:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def dec(fn):
                self.tools[fn.__name__] = fn
                return fn
            return dec

    mcp = _Mcp()
    dax_tools.register(mcp)
    llamado = {"n": 0}
    monkeypatch.setattr(dl, "close_desktop_by_path",
                        lambda p: llamado.update(n=llamado["n"] + 1))

    salida = mcp.tools["pbi_close_desktop"](path=str(pbip))
    assert salida["ok"] is False
    assert "confirm" in salida["message"]
    assert llamado["n"] == 0, "sin confirm no puede llegar a tocar el proceso"


# ------------------------------------------------------- el servicio real ---
def test_archivo_no_abierto_es_un_no_op_declarado(monkeypatch, pbip):
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto", lambda _p: None)
    r = dl.close_desktop_by_path(pbip)
    assert r == {"closed": False, "was_open": False,
                 "reason": "el archivo no esta abierto en ningun Desktop",
                 "path": str(pbip.resolve())}


def test_extension_desconocida_se_rechaza(tmp_path):
    with pytest.raises(ValidationError):
        dl.close_desktop_by_path(tmp_path / "algo.docx")


def test_cierra_y_verifica_que_ya_no_este_abierto(monkeypatch, pbip):
    """El exito no es "terminate no lanzo": es que el archivo ya no este abierto."""
    estados = iter([4242, None])   # abierto antes; cerrado en la re-comprobacion
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto",
                        lambda _p: next(estados))
    monkeypatch.setattr(dl, "_process_started", lambda _pid: 123.0)
    cerrados = {}

    def _close(abierto, force=False):
        cerrados.update(pid=abierto.desktop_pid, force=force,
                        started=abierto.desktop_started)
        return {"closed": True, "pid": abierto.desktop_pid,
                "killed": 0, "children": 0, "survivors": []}

    monkeypatch.setattr(dl, "close", _close)
    r = dl.close_desktop_by_path(pbip)

    assert cerrados == {"pid": 4242, "force": True, "started": 123.0}, (
        "la identidad (hora de arranque) tiene que viajar hasta close()")
    assert r["closed"] is True and r["was_open"] is True
    assert r["verified_closed"] is True


def test_si_el_archivo_sigue_abierto_no_se_miente(monkeypatch, pbip):
    """Un superviviente -u otro proceso con el archivo- degrada el resultado."""
    estados = iter([4242, 5555])   # tras "cerrar", OTRO pid lo tiene abierto
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto",
                        lambda _p: next(estados))
    monkeypatch.setattr(dl, "_process_started", lambda _pid: 123.0)
    monkeypatch.setattr(dl, "close", lambda a, force=False: {
        "closed": True, "pid": a.desktop_pid, "killed": 0,
        "children": 0, "survivors": []})

    r = dl.close_desktop_by_path(pbip)
    assert r["verified_closed"] is False
    assert r["closed"] is False, "decir 'cerrado' con el archivo abierto es mentir"
    assert "5555" in r["reason"]
