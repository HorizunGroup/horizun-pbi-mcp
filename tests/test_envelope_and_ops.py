"""Macrofase A — envelope, observabilidad, idempotencia, planes y tools operativas."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from pbip import project_locator
from powerbi.errors import PowerBIMCPError, ValidationError
from services import envelope, operations, planning, project_state, telemetry
from services import txn as txn_service
from tests.fixtures import synthetic
from tools._common import guard


@pytest.fixture(autouse=True)
def registro_limpio():
    operations.registro().limpiar()
    yield
    operations.registro().limpiar()


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session, pbip.parent, isolated_settings


# ================================================================= envelope ===
def test_el_envelope_es_aditivo():
    """Los campos originales sobreviven intactos."""
    res = guard(lambda: {"count": 3, "tables": ["a"], "output_path": "/x/y.md"})
    assert res["ok"] is True
    assert res["count"] == 3 and res["tables"] == ["a"]
    for campo in ("status", "request_id", "operation", "duration_ms",
                  "warnings", "side_effects"):
        assert campo in res


@pytest.mark.parametrize("payload,esperado", [
    ({"count": 1}, envelope.SUCCESS),
    ({"warnings": ["algo"]}, envelope.WARNING),
    ({"planned": True}, envelope.PLANNED),
    ({"consistent": False}, envelope.WARNING),
])
def test_estados_de_exito(payload, esperado):
    assert guard(lambda: payload)["status"] == esperado


@pytest.mark.parametrize("code,esperado", [
    ("validation_error", envelope.ERROR),
    ("project_open_in_desktop", envelope.CONFLICT),
    ("stale_session", envelope.CONFLICT),
    ("dual_mode_not_safely_available", envelope.CONFLICT),
    ("rollback_incomplete", envelope.ROLLBACK_INCOMPLETE),
    ("bulk_partially_applied", envelope.ROLLBACK_INCOMPLETE),
])
def test_estados_de_error(code, esperado):
    class E(PowerBIMCPError):
        pass

    E.code = code

    def explota():
        raise E("fallo de prueba")

    res = guard(explota)
    assert res["ok"] is False
    assert res["error"] == code
    assert res["status"] == esperado


def test_el_error_conserva_message_y_error():
    def explota():
        raise ValidationError("mensaje original del motor")

    res = guard(explota)
    assert res["error"] == "validation_error"
    assert res["message"] == "mensaje original del motor"


def test_error_inesperado_no_tumba_la_tool():
    def explota():
        raise RuntimeError("boom")

    res = guard(explota)
    assert res["ok"] is False and res["error"] == "unexpected"
    assert res["type"] == "RuntimeError"


def test_la_operacion_se_deduce_del_marco_de_llamada():
    def pbi_operacion_de_prueba():
        return guard(lambda: {"x": 1})

    assert pbi_operacion_de_prueba()["operation"] == "pbi_operacion_de_prueba"


def test_los_side_effects_resumen_lo_tocado():
    res = guard(lambda: {"transaction": {"journal": "/bk/j1", "committed": True,
                                         "files": [{"path": "a.json"}]}})
    efectos = res["side_effects"]
    assert efectos[0]["kind"] == "files"
    assert efectos[0]["files"] == ["a.json"]


def test_cada_llamada_tiene_su_request_id():
    a, b = guard(lambda: {}), guard(lambda: {})
    assert a["request_id"] != b["request_id"]


# ============================================================== telemetria ===
@pytest.mark.parametrize("clave,valor", [
    ("query", "EVALUATE Ventas"),
    ("expression", "SUM(Ventas[Monto])"),
    ("rows", [[1, 2], [3, 4]]),
    ("password", "hunter2"),
    ("token", "abc123"),
])
def test_los_campos_sensibles_no_se_registran(clave, valor):
    redactado = telemetry.redact({clave: valor})[clave]
    # Se conserva la FORMA (longitud, nº de elementos), nunca el contenido.
    assert isinstance(redactado, str) and redactado.startswith("<"), \
        f"'{clave}' se registro tal cual: {redactado!r}"
    assert redactado != valor


def test_las_rutas_se_acortan():
    r = telemetry.redact({"path": r"C:\Users\alguien\Secreto\Informe.pbip"})
    assert "Users" not in r["path"] and "Informe.pbip" in r["path"]


def test_las_credenciales_en_texto_libre_se_enmascaran():
    r = telemetry.redact({"nota": "conecta con Password=secreto123;"})
    assert "secreto123" not in r["nota"] and "***" in r["nota"]


def test_el_formateador_produce_json_por_linea():
    registro = logging.LogRecord("t", logging.INFO, "f", 1, "tool_call", (), None)
    registro.request_id = "abc"
    registro.operation = "pbi_x"
    registro.extra_data = {"query": "EVALUATE X"}
    linea = telemetry.JsonFormatter().format(registro)
    datos = json.loads(linea)
    assert datos["request_id"] == "abc" and datos["operation"] == "pbi_x"
    assert "EVALUATE X" not in linea


def test_el_logging_no_va_nunca_a_stdout(capsys):
    guard(lambda: {"x": 1})
    assert capsys.readouterr().out == "", "stdout es el canal JSON-RPC"


# =========================================================== idempotencia ====
def test_reintento_identico_devuelve_lo_guardado():
    reg = operations.registro()
    args = {"table": "Fact"}
    assert reg.comprobar_request("r1", args) is None
    reg.guardar_resultado("r1", {"ok": True, "applied": 2})
    replay = reg.comprobar_request("r1", args)
    assert replay["applied"] == 2 and replay["idempotent_replay"] is True


def test_mismo_request_id_con_otros_argumentos_es_conflicto():
    reg = operations.registro()
    reg.comprobar_request("r2", {"a": 1})
    with pytest.raises(operations.RequestIdConflictError):
        reg.comprobar_request("r2", {"a": 2})


def test_el_orden_de_las_claves_no_altera_la_huella():
    assert (operations.args_fingerprint({"a": 1, "b": 2})
            == operations.args_fingerprint({"b": 2, "a": 1}))


def test_sin_request_id_no_hay_memoria():
    assert operations.registro().comprobar_request(None, {"a": 1}) is None


# ================================================================= planes ====
def test_el_plan_no_escribe_nada(proyecto):
    session, project, _ = proyecto
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"
    antes = fact.read_bytes()

    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    assert p["planned"] is True and p["changes"] == 1
    assert fact.read_bytes() == antes, "un plan nunca escribe"
    assert p["files"][0]["diff"], "el plan debe traer un diff legible"


def test_aplicar_el_plan_escribe(proyecto):
    session, project, _ = proyecto
    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    r = planning.apply(session, p["plan_token"])
    assert r["applied"] == 1
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"
    assert "isHidden" in fact.read_text(encoding="utf-8")


def test_un_plan_obsoleto_se_rechaza(proyecto):
    session, project, _ = proyecto
    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"
    fact.write_text(fact.read_text(encoding="utf-8") + "\n// externo\n",
                    encoding="utf-8")
    with pytest.raises(operations.PlanTokenStaleError):
        planning.apply(session, p["plan_token"])


def test_un_plan_no_se_aplica_dos_veces(proyecto):
    session, _project, _ = proyecto
    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    planning.apply(session, p["plan_token"])
    with pytest.raises(operations.PlanNotFoundError):
        planning.apply(session, p["plan_token"])


def test_token_inexistente(proyecto):
    session, _project, _ = proyecto
    with pytest.raises(operations.PlanNotFoundError):
        planning.apply(session, "plan_inventado")


def test_plan_de_medida(proyecto):
    session, project, _ = proyecto
    p = planning.plan(session, "create_measure",
                      {"table": "Fact", "name": "Nueva", "expression": "1"})
    assert p["meta"]["action"] == "created"
    planning.apply(session, p["plan_token"])
    fact = project / "Demo.SemanticModel" / "definition" / "tables" / "Fact.tmdl"
    assert "measure Nueva" in fact.read_text(encoding="utf-8")


def test_operacion_no_planificable(proyecto):
    session, _project, _ = proyecto
    with pytest.raises(ValidationError) as exc:
        planning.plan(session, "operacion_inventada", {})
    assert "available" in exc.value.details


def test_el_plan_respeta_la_politica_de_desktop(proyecto, monkeypatch):
    session, project, _ = proyecto
    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    monkeypatch.setattr(project_state, "detect",
                        lambda a, **k: project_state.ProjectOpenState(
                            project_state.OPEN, "high", "abierto"))
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        planning.apply(session, p["plan_token"])


# ========================================================= tools operativas ===
@pytest.fixture
def tools(session, monkeypatch):
    import sys

    sys.path.insert(0, "src")
    import config as cfg
    from server import build_server

    monkeypatch.setattr(cfg, "_session", session)
    return build_server()


def llamar(mcp, nombre, args=None):
    import asyncio

    res = asyncio.run(mcp.call_tool(nombre, args or {}))
    payload = res[1] if isinstance(res, tuple) else res
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    return payload


def test_health_check(proyecto, tools):
    r = llamar(tools, "pbi_health_check")
    assert r["ok"] is True
    ids = {c["check"] for c in r["checks"]}
    assert {"python", "analysis_services_dlls", "active_pbip"} <= ids


def test_capabilities_declara_both_como_no_soportado(proyecto, tools):
    r = llamar(tools, "pbi_capabilities")
    assert r["ok"] is True
    assert r["capabilities"]["dual_mode_both"]["available"] is False
    assert r["capabilities"]["dual_mode_both"]["unsupported"] is True
    assert r["modes"]["both"] is False
    assert any(o["operation"] == "hide_columns" for o in r["planned_operations"])


def test_capabilities_refleja_el_estado_de_desktop(proyecto, tools, monkeypatch):
    monkeypatch.setattr(project_state, "detect",
                        lambda a, **k: project_state.ProjectOpenState(
                            project_state.OPEN, "high", "abierto"))
    r = llamar(tools, "pbi_capabilities")
    assert r["capabilities"]["report_write_pbir"]["available"] is False
    assert "Desktop" in r["capabilities"]["report_write_pbir"]["reason"]


def test_session_info(proyecto, tools):
    r = llamar(tools, "pbi_session_info")
    assert r["ok"] is True and r["active_pbip"] is not None
    assert r["active_pbip"]["writable"] is True


def test_journals_vacios_al_principio(proyecto, tools):
    r = llamar(tools, "pbi_list_pending_journals", {"only_pending": False})
    assert r["ok"] is True and r["count"] == 0


def test_journals_tras_una_escritura(proyecto, tools):
    session, project, _ = proyecto
    p = planning.plan(session, "hide_columns",
                      {"columns": [{"table": "Fact", "column": "Amount"}]})
    planning.apply(session, p["plan_token"])

    r = llamar(tools, "pbi_list_pending_journals", {"only_pending": False})
    assert r["count"] == 1 and r["journals"][0]["status"] == "committed"
    assert r["needs_attention"] == 0

    detalle = llamar(tools, "pbi_inspect_journal",
                     {"journal": r["journals"][0]["journal"]})
    assert detalle["ok"] is True and detalle["restorable"] is True
    assert detalle["files"][0]["backup_available"] is True
    assert detalle["files"][0]["matches_original"] is False, "el archivo ya cambio"


def test_inspect_journal_ajeno_se_rechaza(proyecto, tools, tmp_path):
    ajeno = tmp_path / "otro_journal"
    ajeno.mkdir()
    r = llamar(tools, "pbi_inspect_journal", {"journal": str(ajeno)})
    assert r["ok"] is False and r["error"] == "validation_error"


def test_plan_y_apply_por_las_tools(proyecto, tools):
    plan = llamar(tools, "pbi_plan_change",
                  {"operation": "hide_columns",
                   "arguments": {"columns": [{"table": "Fact", "column": "Amount"}]}})
    assert plan["ok"] is True and plan["status"] == envelope.PLANNED
    aplicado = llamar(tools, "pbi_apply_plan", {"plan_token": plan["plan_token"]})
    assert aplicado["ok"] is True and aplicado["applied"] == 1


def test_apply_plan_exige_confirm(proyecto, tools):
    plan = llamar(tools, "pbi_plan_change",
                  {"operation": "hide_columns",
                   "arguments": {"columns": [{"table": "Fact", "column": "Amount"}]}})
    r = llamar(tools, "pbi_apply_plan",
               {"plan_token": plan["plan_token"], "confirm": False})
    assert r["ok"] is False
