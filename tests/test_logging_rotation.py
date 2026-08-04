"""Fase N — la rotacion del log no puede tumbar nada ni ensuciar stderr.

El defecto, reproducible en Windows con el .pbip en OneDrive:

    --- Logging error ---
    PermissionError: [WinError 32] The process cannot access the file because
    it is being used by another process:
    'outputs/powerbi_mcp.log' -> 'outputs/powerbi_mcp.log.1'

`RotatingFileHandler` renombra dentro de `emit()`, no al abrir, asi que el
`try/except` que envolvia la apertura no lo cubria: `handleError` escupia el
traceback por stderr en mitad de `doctor.py` y del contract check. Ambos salian
con codigo 0, lo que hacia que un traceback pareciera salida limpia.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from horizun_pbi_mcp import logging_config
from horizun_pbi_mcp.logging_config import (SafeRotatingFileHandler, purgar_logs,
                            ruta_log_de_este_proceso)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def limpiar():
    logging_config._reset_para_pruebas()          # noqa: SLF001
    yield
    logging_config._reset_para_pruebas()          # noqa: SLF001


# ============================================ el traceback ya no puede salir ==
def test_un_rename_bloqueado_no_emite_traceback(tmp_path, capsys, monkeypatch):
    """REGRESION del WinError 32. Antes imprimia '--- Logging error ---'."""
    destino = tmp_path / "app.log"
    handler = SafeRotatingFileHandler(destino, maxBytes=80, backupCount=2,
                                      encoding="utf-8")
    log = logging.getLogger("prueba_rotacion")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    def rename_bloqueado(*_a, **_k):
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(os, "rename", rename_bloqueado)

    for i in range(40):
        log.info("linea de relleno numero %d para forzar el rollover", i)

    salida = capsys.readouterr()
    assert "Logging error" not in salida.err
    assert "Traceback" not in salida.err
    assert "PermissionError" not in salida.err
    assert handler.fallos_rotacion > 0, "la prueba no llego a forzar la rotacion"


def test_tras_un_fallo_de_rotacion_se_sigue_registrando(tmp_path, monkeypatch):
    """Degradarse no es enmudecer: el log tiene que seguir escribiendo."""
    destino = tmp_path / "app.log"
    handler = SafeRotatingFileHandler(destino, maxBytes=80, backupCount=2,
                                      encoding="utf-8")
    log = logging.getLogger("prueba_rotacion2")
    log.handlers = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False

    monkeypatch.setattr(os, "rename", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError(32, "retenido")))
    for i in range(30):
        log.info("relleno %d", i)
    monkeypatch.undo()

    log.info("MARCA-DESPUES-DEL-FALLO")
    handler.flush()
    assert "MARCA-DESPUES-DEL-FALLO" in destino.read_text(encoding="utf-8")


def test_si_no_se_puede_abrir_el_archivo_el_servidor_arranca(tmp_path, monkeypatch):
    """El arranque no depende de poder escribir el log."""
    def no_se_puede(*_a, **_k):
        raise OSError("disco lleno")

    monkeypatch.setattr(Path, "mkdir", no_se_puede)
    log = logging_config.setup_logging("INFO", str(tmp_path / "x" / "app.log"))
    assert log is not None
    log.info("sigo funcionando")


# ================================================== un archivo por proceso ====
def test_cada_proceso_escribe_en_su_archivo():
    ruta = ruta_log_de_este_proceso("outputs/powerbi_mcp.log")
    assert str(os.getpid()) in ruta.name
    assert ruta.name.endswith(".log")


def test_no_se_reetiqueta_un_nombre_que_ya_trae_pid():
    ya = f"outputs/powerbi_mcp.{os.getpid()}.log"
    assert ruta_log_de_este_proceso(ya) == Path(ya)


def test_varios_procesos_concurrentes_no_dejan_errores(tmp_path):
    """Ocho procesos REALES arrancando a la vez y forzando el rollover.

    Con un unico `powerbi_mcp.log` compartido esto es exactamente la carrera
    que fallaba: varios `os.rename` sobre el mismo destino.
    """
    script = tmp_path / "escritor.py"
    script.write_text(
        "import sys, logging\n"
        f"sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from horizun_pbi_mcp import logging_config\n"
        f"log = logging_config.setup_logging('INFO', {str(tmp_path / 'concurrente.log')!r})\n"
        "for i in range(400):\n"
        "    log.info('proceso %s linea %s de relleno para forzar rotacion', "
        "__import__('os').getpid(), i)\n"
        "print('OK')\n",
        encoding="utf-8")

    procesos = [subprocess.Popen([sys.executable, str(script)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace")
                for _ in range(8)]
    salidas = [p.communicate(timeout=120) for p in procesos]

    for i, (out, err) in enumerate(salidas):
        assert procesos[i].returncode == 0, f"proceso {i} murio: {err[-400:]}"
        assert "OK" in out
        assert "Logging error" not in err, f"proceso {i} emitio error de logging:\n{err[-600:]}"
        assert "Traceback" not in err, f"proceso {i} emitio traceback:\n{err[-600:]}"

    creados = list(tmp_path.glob("concurrente.*.log*"))
    assert len(creados) >= 2, (
        f"se esperaba un archivo por proceso, hay {len(creados)}")


# =================================================== retencion acotada ========
def test_la_purga_respeta_el_tope_de_archivos(tmp_path):
    for i in range(20):
        f = tmp_path / f"app.{1000 + i}.log"
        f.write_text("x", encoding="utf-8")
        os.utime(f, (time.time() - i * 60, time.time() - i * 60))

    purgar_logs(tmp_path, "app", max_archivos=5, max_dias=999)
    assert len(list(tmp_path.glob("app.*.log*"))) == 5


def test_la_purga_borra_los_caducados(tmp_path):
    viejo = tmp_path / "app.1234.log"
    viejo.write_text("x", encoding="utf-8")
    antiguo = time.time() - 30 * 86400
    os.utime(viejo, (antiguo, antiguo))

    nuevo = tmp_path / "app.5678.log"
    nuevo.write_text("x", encoding="utf-8")

    purgar_logs(tmp_path, "app", max_archivos=99, max_dias=14)
    assert not viejo.exists()
    assert nuevo.exists()


def test_la_purga_nunca_borra_el_archivo_de_este_proceso(tmp_path):
    mio = ruta_log_de_este_proceso(str(tmp_path / "app.log"))
    mio.write_text("x", encoding="utf-8")
    antiguo = time.time() - 90 * 86400
    os.utime(mio, (antiguo, antiguo))

    purgar_logs(tmp_path, "app", max_archivos=0, max_dias=1)
    assert mio.exists(), "se borro el log del proceso en marcha"


def test_un_archivo_retenido_no_rompe_la_purga(tmp_path, monkeypatch):
    f = tmp_path / "app.1234.log"
    f.write_text("x", encoding="utf-8")
    os.utime(f, (0, 0))

    monkeypatch.setattr(Path, "unlink", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError(32, "retenido")))
    assert purgar_logs(tmp_path, "app") == []       # no lanza


# ================================================== stdout sigue intocable ====
def test_ningun_handler_escribe_en_stdout(tmp_path):
    log = logging_config.setup_logging("INFO", str(tmp_path / "app.log"))
    for h in log.handlers:
        destino = getattr(h, "stream", None)
        assert destino is not sys.stdout, (
            "un handler escribe en stdout: rompe el canal JSON-RPC del MCP")


def test_el_servidor_no_ensucia_stdout_con_logs(tmp_path):
    """Arranque real: stdout debe quedar limpio de lineas de log."""
    script = tmp_path / "arranque.py"
    script.write_text(
        f"import sys; sys.path.insert(0, {str(REPO / 'src')!r})\n"
        "from horizun_pbi_mcp.server import build_server\n"
        "import asyncio\n"
        "tools = asyncio.run(build_server().list_tools())\n"
        "print(len(tools))\n",
        encoding="utf-8")
    res = subprocess.run([sys.executable, str(script)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         cwd=str(tmp_path), timeout=120)

    assert res.returncode == 0, res.stderr[-800:]
    from tests.test_tool_contract import EXPECTED_COUNT

    assert res.stdout.strip() == str(EXPECTED_COUNT), (
        f"stdout contiene algo mas que el resultado: {res.stdout!r}")
    assert "Logging error" not in res.stderr
