"""Un estado ilegible es evidencia: diagnosticarlo nunca puede destruirlo."""
from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from horizun_pbi_mcp.lifecycle import runtime_state


RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def bootstrap():
    spec = importlib.util.spec_from_file_location(
        f"_bootstrap_estado_{uuid.uuid4().hex}",
        RAIZ / "scripts" / "plugin_bootstrap.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.mark.parametrize("contenido", [b"{incompleto", b"[1, 2, 3]"])
def test_install_status_corrupto_se_reporta_y_no_se_reescribe(
        bootstrap, tmp_path, contenido):
    p = bootstrap.paths(tmp_path)
    p["cache"].mkdir(parents=True)
    p["status"].write_bytes(contenido)

    status = bootstrap.read_status(tmp_path)

    assert status["state"] == "corrupt"
    assert status["ready"] is False
    assert p["status"].read_bytes() == contenido
    with pytest.raises(bootstrap.EstadoStatusCorrupto):
        bootstrap._write_status(p, state="installing", ready=False)  # noqa: SLF001
    assert p["status"].read_bytes() == contenido
    assert not list(p["cache"].glob(".install-status.json.*.tmp"))

    assert bootstrap.install(tmp_path, include_validator=False) == 1
    assert p["status"].read_bytes() == contenido
    assert not list(tmp_path.glob(f".{bootstrap.VERSION}.staging-*"))


@pytest.mark.parametrize("operacion", ["fallo", "degradacion", "promocion"])
def test_runtime_state_corrupto_bloquea_todos_los_registros(tmp_path, operacion):
    destino = tmp_path / runtime_state.NOMBRE
    original = b'{"esquema": 1, "activo": '
    destino.write_bytes(original)

    with pytest.raises(runtime_state.EstadoRuntimeCorrupto):
        if operacion == "fallo":
            runtime_state.registrar_fallo(
                tmp_path, version="9.9.9", error="fallo medido")
        elif operacion == "degradacion":
            runtime_state.registrar_degradacion(
                tmp_path, carpeta="9.9.9", motivo="runtime roto")
        else:
            runtime_state.registrar_promocion(
                tmp_path,
                nuevo=runtime_state.evidencia(
                    "9.9.9", version="9.9.9", servidor="test", tools=1),
                anterior_apartado=None)

    assert destino.read_bytes() == original
    assert not list(tmp_path.glob(".runtime-state.json.*.tmp"))
