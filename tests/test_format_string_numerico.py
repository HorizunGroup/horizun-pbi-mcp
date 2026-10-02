"""`format_string` numerico: `0` es el formato "0", no un error de tipo.

Caso real («Comite de obra», 1-oct-2026): `pbi_create_measure` con
`format_string: 0` -sin comillas, como lo manda un cliente que lo escribe a
mano- fallo con «Input should be a valid string» antes de llegar a la tool.
Y por `pbi_apply_plan`, que no pasa por esa validacion, el mismo `0` llegaba
al escritor TMDL y `if format_string:` lo descartaba EN SILENCIO por falsy:
la medida se creaba sin formato y la respuesta decia exito.

El esquema publicado no cambia (`string|null`): la conversion ocurre antes de
validar el tipo.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.pbip import project_locator, tmdl_writer
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.utils.validation import normalizar_format_string
from tests.fixtures import synthetic


# ---------------------------------------------------------------- unidad ---
@pytest.mark.parametrize("entrada, esperado", [
    (0, "0"), (100, "100"), ("0", "0"), ("#,0.00", "#,0.00"), (None, None)])
def test_normalizar_convierte_enteros_y_respeta_el_texto(entrada, esperado):
    assert normalizar_format_string(entrada) == esperado


def test_un_decimal_se_rechaza_diciendo_como_pasarlo():
    """`0.00` llega como `0.0`: convertirlo escribiria otro formato."""
    with pytest.raises(ValidationError) as fallo:
        normalizar_format_string(0.0)
    assert "entre comillas" in fallo.value.message


def test_un_booleano_no_se_convierte_en_texto():
    assert normalizar_format_string(True) is True


# ------------------------------------------------------ por el canal MCP ---
@pytest.fixture
def servidor(session, tmp_path, monkeypatch):
    import horizun_pbi_mcp.config as cfg
    from horizun_pbi_mcp.server import build_server

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    session.set_active_model(ActiveModel(
        host="localhost", port=1234, connection_string="Data Source=localhost:1234",
        catalog="cat", database_name="cat", model_name="M",
        pid=1, process_started=1.0, session_fingerprint="fp"))
    monkeypatch.setattr(cfg, "_session", session)
    return build_server(), pbip.parent


def _llamar(mcp, nombre, args):
    resultado = asyncio.run(mcp.call_tool(nombre, args))
    payload = resultado[1] if isinstance(resultado, tuple) else resultado
    if isinstance(payload, dict) and "result" in payload and "ok" not in payload:
        payload = payload["result"]
    if not isinstance(payload, dict):
        payload = json.loads(payload[0].text)
    return payload


def _tmdl_de(proyecto, tabla):
    return next(p for p in proyecto.rglob(f"{tabla}.tmdl")).read_text(
        encoding="utf-8-sig")


def test_create_measure_acepta_un_formato_entero(servidor):
    mcp, proyecto = servidor
    r = _llamar(mcp, "pbi_create_measure", {
        "table": "Fact", "name": "Cruces", "expression": "1",
        "format_string": 0, "mode": "pbip"})

    assert r["ok"] is True, r
    assert "formatString: 0" in _tmdl_de(proyecto, "Fact")


def test_update_measure_acepta_un_formato_entero(servidor):
    mcp, proyecto = servidor
    r = _llamar(mcp, "pbi_update_measure", {
        "table": "Fact", "name": "TotalAmount", "format_string": 0,
        "mode": "pbip"})

    assert r["ok"] is True, r
    bloque = _tmdl_de(proyecto, "Fact").split("measure TotalAmount", 1)[1]
    assert "formatString: 0" in bloque.split("measure ", 1)[0]


def test_create_calculated_column_acepta_un_formato_entero(servidor):
    mcp, proyecto = servidor
    r = _llamar(mcp, "pbi_create_calculated_column", {
        "table": "Fact", "name": "Uno", "expression": "1",
        "data_type": "int64", "format_string": 0})

    assert r["ok"] is True, r
    assert "formatString: 0" in _tmdl_de(proyecto, "Fact")


def test_un_formato_decimal_se_rechaza_con_un_mensaje_util(servidor):
    from mcp.server.fastmcp.exceptions import ToolError

    mcp, _proyecto = servidor
    with pytest.raises(ToolError) as fallo:
        asyncio.run(mcp.call_tool("pbi_create_measure", {
            "table": "Fact", "name": "X", "expression": "1",
            "format_string": 0.0, "mode": "pbip"}))
    assert "entre comillas" in str(fallo.value)


def test_el_esquema_publicado_sigue_siendo_texto(servidor):
    mcp, _proyecto = servidor
    herramientas = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for nombre in ("pbi_create_measure", "pbi_update_measure",
                   "pbi_create_calculated_column"):
        esquema = herramientas[nombre].inputSchema["properties"]["format_string"]
        tipos = sorted(o.get("type") for o in esquema["anyOf"])
        assert tipos == ["null", "string"], (nombre, esquema)


# ---------------------------------- el camino que lo perdia en silencio ---
def test_el_escritor_tmdl_no_descarta_un_cero(session, tmp_path):
    """`pbi_apply_plan` llama aqui sin pasar por la validacion de la tool."""
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))

    tmdl_writer.create_measure_pbip(session.require_active_pbip(), "Fact",
                                    "Cruces", "1", 0)

    assert "formatString: 0" in _tmdl_de(pbip.parent, "Fact")
