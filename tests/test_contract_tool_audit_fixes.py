"""Regresiones de contrato y honestidad encontradas por la auditoria amplia."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from horizun_pbi_mcp.services import envelope
from horizun_pbi_mcp.tools import documentation_tools, ops_tools, risk


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.mark.parametrize("value,expected", [
    ("live", "live"),
    (" LIVE ", "live"),
    ("PBIP", "pbip"),
])
def test_documentation_source_se_normaliza(value, expected):
    assert documentation_tools._normalizar_source(value) == expected


def test_documentation_source_invalido_no_cae_a_live(monkeypatch):
    monkeypatch.setattr(
        documentation_tools.model_reader, "read_model",
        lambda *_: pytest.fail("un source invalido cayo silenciosamente a live"))
    with pytest.raises(Exception) as exc:
        documentation_tools._load_model_data("garbage")
    assert getattr(exc.value, "code", None) == "validation_error"


def test_envelope_conserva_status_y_operation_de_negocio():
    result = envelope.success(
        {"status": "no_change", "operation": "hide_columns", "applied": 0},
        operation="pbi_apply_plan", request_id="r1", duration_ms=1)
    assert result["status"] == envelope.SUCCESS
    assert result["operation"] == "pbi_apply_plan"
    assert result["result_status"] == "no_change"
    assert result["target_operation"] == "hide_columns"


@pytest.mark.parametrize("code", [
    "idempotency_conflict", "request_in_progress", "request_outcome_unknown",
    "recovery_conflict", "page_conflict", "active_model_project_mismatch",
    "plan_expired", "plan_operation_mismatch", "plan_payload_tampered",
    "plan_project_mismatch",
])
def test_taxonomia_actual_de_conflictos(code):
    assert envelope._status_for_error(code) == envelope.CONFLICT


def test_transaction_failed_no_se_disfraza_de_conflicto():
    assert envelope._status_for_error("transaction_failed") == envelope.ERROR


def test_side_effects_incluye_artifact_desktop_cierre_y_refresh():
    exportado = envelope.success(
        {"output_pbix": "x.pbix", "desktop_pid": 9, "launched_by_us": True},
        operation="pbi_export_pbix", request_id="e", duration_ms=1)
    assert {e["kind"] for e in exportado["side_effects"]} >= {"artifact"}

    cerrado = envelope.success(
        {"was_open": True, "verified_closed": True, "pid": 9},
        operation="pbi_close_desktop", request_id="c", duration_ms=1)
    assert {"kind": "desktop", "action": "closed", "pid": 9} in cerrado["side_effects"]

    refrescado = envelope.success(
        {"status": "ok", "refresh_type": "full"},
        operation="pbi_refresh_model", request_id="f", duration_ms=1)
    assert any(e.get("action") == "refresh" for e in refrescado["side_effects"])


def test_toda_firma_request_id_lo_propaga_o_usa_guard_mutation():
    raiz = Path("src/horizun_pbi_mcp/tools")
    faltan = []
    for path in raiz.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for fn in (n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name.startswith("pbi_")):
            params = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
            if "request_id" not in params:
                continue
            for call in (n for n in ast.walk(fn) if isinstance(n, ast.Call)):
                if not isinstance(call.func, ast.Name) or call.func.id != "guard":
                    continue
                keywords = {k.arg for k in call.keywords}
                if "request_id" not in keywords:
                    faltan.append(f"{path.name}:{fn.name}")
    assert not faltan, f"firmas que aceptan y pierden request_id: {faltan}"


def test_close_desktop_repite_request_id_sin_cerrar_dos_veces(
        monkeypatch, isolated_settings):
    from horizun_pbi_mcp.powerbi import desktop_launcher
    from horizun_pbi_mcp.tools import dax_tools

    calls = []
    monkeypatch.setattr(
        desktop_launcher, "close_desktop_by_identity",
        lambda *a, **k: calls.append((a, k)) or {
            "closed": True, "was_open": True, "verified_closed": True})
    mcp = _Mcp()
    dax_tools.register(mcp)

    args = {"desktop_pid": 42, "desktop_started": 100.0,
            "confirm": True, "request_id": "close-once"}
    first = mcp.tools["pbi_close_desktop"](**args)
    second = mcp.tools["pbi_close_desktop"](**args)

    assert first["ok"] is True and second["ok"] is True
    assert len(calls) == 1
    assert second["idempotent_replay"] is True


def test_dlls_cualesquiera_no_habilitan_analysis_services(
        isolated_settings):
    isolated_settings.libs_dir.mkdir(parents=True, exist_ok=True)
    for name in ("a.dll", "b.dll", "c.dll"):
        (isolated_settings.libs_dir / name).write_bytes(b"x")
    state = ops_tools._estado_dlls_analysis_services(isolated_settings)
    assert state["available"] is False
    assert state["adomd"]["available"] is False
    assert state["tom"]["available"] is False


def test_dlls_separan_adomd_y_tom(isolated_settings):
    isolated_settings.libs_dir.mkdir(parents=True, exist_ok=True)
    state = ops_tools._estado_dlls_analysis_services(isolated_settings)
    for name in state["adomd"]["required"]:
        (isolated_settings.libs_dir / name).write_bytes(b"test")
    only_adomd = ops_tools._estado_dlls_analysis_services(isolated_settings)
    assert only_adomd["adomd"]["available"] is True
    assert only_adomd["tom"]["available"] is False


def test_capabilities_sin_modelo_distingue_adomd_y_tom(
        monkeypatch, session, isolated_settings):
    from horizun_pbi_mcp import config

    monkeypatch.setattr(config, "_session", session)
    mcp = _Mcp()
    ops_tools.register(mcp)
    result = mcp.tools["pbi_capabilities"]()
    assert result["ok"] is True
    assert result["capabilities"]["dax_query"]["available"] is False
    assert result["capabilities"]["model_read_live"]["available"] is False
    assert "analysis_services_dlls" in result["capabilities"]


def test_validate_render_refresh_exige_confirm(monkeypatch):
    from horizun_pbi_mcp.tools import dax_tools

    mcp = _Mcp()
    dax_tools.register(mcp)
    result = mcp.tools["pbi_validate_desktop_render"](
        path="dummy.pbix", refresh=True)
    assert result["ok"] is False
    assert result["error"] == "validation_error"
    assert "confirm=true" in result["message"]
    annotations = risk.annotations_for("pbi_validate_desktop_render")
    assert annotations["readOnlyHint"] is False
    assert annotations["destructiveHint"] is True


def test_finalize_delivery_propaga_confirm_reuse(monkeypatch):
    from horizun_pbi_mcp.services import pbix_export
    from horizun_pbi_mcp.tools import workflow_tools

    received = {}
    monkeypatch.setattr(
        pbix_export, "finalize_delivery",
        lambda *a, **k: received.update(k) or {"saved_as_verified": True})
    mcp = _Mcp()
    workflow_tools.register(mcp)
    result = mcp.tools["pbi_finalize_delivery"](confirm_reuse=True)
    assert result["ok"] is True
    assert received["confirm_reuse"] is True
