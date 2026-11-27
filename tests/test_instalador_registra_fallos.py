"""Un paso del instalador que falla deja su error en `install.log`.

Auditoria del 2026-11-07: una instalacion limpia del plugin fallo en
`python -m venv` y el registro quedo en 0 bytes. El instalador corre desprendido
(DETACHED_PROCESS) y cada hijo con CREATE_NO_WINDOW: un hijo que solo HEREDA
stdout/stderr escribe en una consola oculta nueva, no en el registro.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name != "nt", reason="consolas de Windows")


def test_el_error_de_un_paso_llega_al_registro_desprendido(tmp_path):
    hijo = tmp_path / "hijo.py"
    hijo.write_text(textwrap.dedent(f"""
        import importlib.util, subprocess, sys
        spec = importlib.util.spec_from_file_location(
            "bs", {str(RAIZ / "scripts" / "plugin_bootstrap.py")!r})
        bs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bs)
        try:
            bs._run([sys.executable, "-c",
                     "import sys; print('PASO-ROTO-7731', file=sys.stderr); sys.exit(3)"],
                    env=dict(__import__('os').environ), intentos=1)
        except subprocess.CalledProcessError:
            pass
    """), encoding="utf-8")
    registro = tmp_path / "install.log"
    with open(registro, "a", encoding="utf-8") as log:
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        rc = subprocess.Popen([sys.executable, str(hijo)], stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT, close_fds=True,
                              creationflags=flags, cwd=str(tmp_path)).wait(timeout=120)
    texto = registro.read_text(encoding="utf-8", errors="replace")
    assert rc == 0, texto
    assert "PASO-ROTO-7731" in texto
    assert "[install]" in texto
