"""Fase 1A — deteccion de proyecto abierto en Power BI Desktop.

Politica estricta: solo `closed` permite escribir. `open` y `unknown` bloquean.
Las senales son de SOLO LECTURA: ninguna prueba (ni el codigo) muta un archivo
del proyecto para averiguar si esta bloqueado.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from config import ActivePbip
from services import project_state
from services.project_state import (CLOSED, OPEN, UNKNOWN,
                                    ProjectOpenInDesktopError)
from tests.fixtures import synthetic

pytestmark = pytest.mark.real_project_state


class FakeHandle:
    def __init__(self, path):
        self.path = str(path)


class FakeProc:
    """Proceso simulado. `denied` reproduce un AccessDenied de psutil."""

    def __init__(self, name, pid, files=(), cmdline=(), denied=False):
        self.info = {"name": name, "pid": pid}
        self.pid = pid
        self._files = [FakeHandle(f) for f in files]
        self._cmdline = list(cmdline)
        self._denied = denied

    def open_files(self):
        if self._denied:
            import psutil
            raise psutil.AccessDenied(self.pid)
        return self._files

    def cmdline(self):
        if self._denied:
            import psutil
            raise psutil.AccessDenied(self.pid)
        return self._cmdline


@pytest.fixture
def active(tmp_path):
    pbip = synthetic.materialize(tmp_path)
    return ActivePbip(
        pbip_path=str(pbip), project_dir=str(pbip.parent),
        report_dir=str(synthetic.find_report_dir(pbip)),
        semantic_model_dir=str(synthetic.find_semantic_model_dir(pbip)),
        report_name="Demo", has_pbir=True, has_tmdl=True)


@pytest.fixture
def procesos(monkeypatch):
    """Sustituye la enumeracion de procesos por una lista controlada."""
    import psutil

    def _set(lista):
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(lista))
        project_state.invalidate_cache()
    return _set


# ------------------------------------------------------------- deteccion ----
def test_sin_procesos_es_cerrado(active, procesos):
    procesos([])
    estado = project_state.detect(active)
    assert estado.state == CLOSED and estado.confidence == "high"
    assert estado.writable


def test_motor_sin_desktop_es_desconocido(active, procesos):
    """Hay msmdsrv vivo pero no se puede atribuir a un proyecto."""
    procesos([FakeProc("msmdsrv.exe", 100)])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_desktop_con_archivo_del_proyecto_abierto_es_open(active, procesos):
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 200, files=[victima])])
    estado = project_state.detect(active)
    assert estado.state == OPEN and estado.confidence == "high"


def test_desktop_con_el_pbip_en_la_linea_de_comandos_es_open(active, procesos):
    procesos([FakeProc("PBIDesktop.exe", 201, cmdline=["PBIDesktop.exe",
                                                       active.pbip_path])])
    estado = project_state.detect(active)
    assert estado.state == OPEN


def test_desktop_sin_permisos_es_desconocido(active, procesos):
    procesos([FakeProc("PBIDesktop.exe", 202, denied=True)])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert "denego" in estado.reason


def test_desktop_con_otro_proyecto_es_cerrado(active, procesos, tmp_path):
    otro = tmp_path / "otro_informe" / "x.pbix"
    otro.parent.mkdir(parents=True)
    otro.write_text("", encoding="utf-8")
    procesos([FakeProc("PBIDesktop.exe", 203, files=[otro])])
    estado = project_state.detect(active)
    assert estado.state == CLOSED and estado.confidence == "medium"


def test_varios_desktop_uno_con_el_proyecto(active, procesos, tmp_path):
    otro = tmp_path / "otro.pbix"
    otro.write_text("", encoding="utf-8")
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 204, files=[otro]),
              FakeProc("PBIDesktop.exe", 205, files=[victima])])
    assert project_state.detect(active).state == OPEN


# --------------------------------------------------------------- politica ---
def test_cerrado_permite_escribir(active, procesos):
    procesos([])
    estado = project_state.assert_writable(active)
    assert estado.state == CLOSED


def test_abierto_bloquea(active, procesos):
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 206, files=[victima])])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    assert exc.value.details["state"] == OPEN
    assert exc.value.details["policy"] == "strict"


def test_desconocido_tambien_bloquea(active, procesos):
    """La correccion clave: el estado indeterminado NO permite escribir."""
    procesos([FakeProc("PBIDesktop.exe", 207, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    assert exc.value.details["state"] == UNKNOWN


def test_el_error_no_promete_lo_que_no_puede_cumplir(active, procesos):
    """No se afirma que el bloqueo impida a Desktop sobrescribir despues."""
    procesos([FakeProc("PBIDesktop.exe", 208, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    nota = exc.value.details["note"]
    assert "NO impide" in nota and "Desktop" in nota


def test_no_hay_variable_de_entorno_que_lo_desactive(active, procesos, monkeypatch):
    monkeypatch.setenv("PBI_MCP_PBIR_WRITE_POLICY", "warn")
    monkeypatch.setenv("PBI_MCP_ALLOW_OPEN_PROJECT", "1")
    procesos([FakeProc("PBIDesktop.exe", 209, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError):
        project_state.assert_writable(active)


# ------------------------------------------------------------------ cache ---
def test_la_cache_expira(active, procesos, monkeypatch):
    procesos([])
    assert project_state.detect(active).state == CLOSED

    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 210, files=[victima])])  # invalida cache
    assert project_state.detect(active).state == OPEN


def test_use_cache_false_siempre_reevalua(active, procesos):
    procesos([])
    assert project_state.detect(active, use_cache=False).state == CLOSED
