"""Exercise the real MCP boundary, including schemas, execution and discovery."""
from __future__ import annotations

import asyncio
import json
import math

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session

from horizun_pbi_mcp.server import _TOOL_MODULES, _anotar_riesgo, build_server
from horizun_pbi_mcp.tool_profiles import LOCAL_DESCRIPTIONS, PROFILE_ENV, minimal_schema


@pytest.fixture(autouse=True)
def isolated_profile(monkeypatch, isolated_settings, session):
    from horizun_pbi_mcp import config
    monkeypatch.setattr(config, "_session", session)
    monkeypatch.delenv(PROFILE_ENV, raising=False)


def call(server, tool_name, **arguments):
    response = asyncio.run(server.call_tool(tool_name, arguments))
    return response[1]["result"]


def listed(server):
    return {tool.name: tool.model_dump(mode="json", by_alias=True, exclude_none=True)
            for tool in asyncio.run(server.list_tools())}


def test_default_full_preserves_every_original_schema_and_description():
    import logging
    original = FastMCP("original")
    for module in _TOOL_MODULES:
        module.register(original)
    _anotar_riesgo(original, logging.getLogger(__name__))
    before = listed(original)
    after = listed(build_server())
    assert set(after) == set(before) | {"pbi_find_tool"}
    assert {name: after[name] for name in before} == before
    assert listed(build_server(profile="full")) == after


def test_local_registers_only_allowlist_and_cannot_call_hidden(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, " LoCaL ")
    server = build_server()
    assert set(listed(server)) == set(LOCAL_DESCRIPTIONS)
    assert "pbi_create_relationship" in listed(server)
    assert {t.name for t in server._tool_manager.list_tools()} == set(LOCAL_DESCRIPTIONS)
    with pytest.raises(ToolError, match="Unknown tool"):
        call(server, "pbi_delete_measure", table="T", name="M", confirm=True)
    # Profile is bound to the server, not to the environment at call time.
    monkeypatch.setenv(PROFILE_ENV, "full")
    found = call(server, "pbi_find_tool", query="pbi_delete_measure")
    assert found["profile"] == "local"
    assert found["matches"][0]["in_active_profile"] is False


def test_bad_profile_fails_before_opening_default_project(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "locla")
    with pytest.raises(ValueError, match="full.*local"):
        build_server()


def test_doctor_checks_full_and_allows_only_compatible_additions(monkeypatch):
    from scripts import doctor
    monkeypatch.setenv(PROFILE_ENV, "local")
    report = doctor.Report()
    tools = doctor.check_server_boots(report)
    assert "pbi_delete_measure" in {t.name for t in tools}
    doctor.check_contract(report, tools)
    assert report.exit_code == 0
    # Removing a frozen tool is still rejected, even with the same total count.
    from copy import deepcopy
    broken = deepcopy(tools)
    for tool in broken:
        if tool.name == "pbi_create_measure":
            tool.name = "pbi_unrecognized_replacement"
    report = doctor.Report()
    doctor.check_contract(report, broken)
    assert report.exit_code == 1


def test_local_schemas_preserve_all_parameters_defaults_constraints_and_risks():
    full = listed(build_server())
    local = listed(build_server(profile="local"))
    for name, tool in local.items():
        assert tool["inputSchema"] == minimal_schema(full[name]["inputSchema"])
        assert tool["outputSchema"] == full[name]["outputSchema"]
        assert tool["annotations"] == full[name]["annotations"]
    assert local["pbi_find_tool"]["annotations"]["readOnlyHint"] is True
    assert local["pbi_refresh_model"]["inputSchema"]["properties"]["confirm"]["default"] is False
    assert minimal_schema({"title": "Schema", "properties": {
        "title": {"title": "Title", "type": "string"}}}) == {
            "properties": {"title": {"type": "string"}}}


def test_wire_listing_token_budget():
    async def measure(profile):
        async with create_connected_server_and_client_session(build_server(profile=profile)) as client:
            result = await client.list_tools()
            # Include annotations, output schemas and JSON-RPC envelope; default
            # JSON whitespace/ASCII escaping is a conservative char/4 estimate.
            wire = {"jsonrpc": "2.0", "id": 2, "result": result.model_dump(
                mode="json", by_alias=True, exclude_none=True)}
            return len(result.tools), math.ceil(len(json.dumps(wire)) / 4)
    local_count, local_tokens = asyncio.run(measure("local"))
    full_count, full_tokens = asyncio.run(measure("full"))
    print(f"tools/list: local={local_count}/{local_tokens} tokens; full={full_count}/{full_tokens} tokens")
    assert local_count == 13
    assert local_tokens < 4000
    assert local_tokens < full_tokens


@pytest.mark.parametrize("query,target", [
    ("medida", "pbi_create_measure"), ("measure", "pbi_create_measure"),
    ("MEDÍ", "pbi_create_measure"), ("tabla", "pbi_list_tables"),
    ("table", "pbi_list_tables"), ("gráfico", "pbi_create_page_from_spec"),
    ("visual", "pbi_create_page_from_spec"), ("chart", "pbi_create_page_from_spec"),
    ("validar", "pbi_validate_tmdl"), ("validate", "pbi_validate_tmdl"),
    ("cargar", "pbi_add_table_from_file"), ("load", "pbi_add_table_from_file"),
    ("import", "pbi_add_table_from_file"), ("cargar archivo", "pbi_add_table_from_file"),
    ("borrar medida", "pbi_delete_measure"), ("relationship", "pbi_create_relationship"),
])
def test_bilingual_partial_accent_insensitive_search(query, target):
    result = call(build_server(profile="local"), "pbi_find_tool", query=query)
    assert result["ok"] is True
    assert target in {m["name"] for m in result["matches"]}


@pytest.mark.parametrize("profile,target,active", [
    ("local", "pbi_delete_measure", False), ("full", "pbi_delete_measure", True),
    ("local", "pbi_create_relationship", True), ("full", "pbi_create_relationship", True),
])
def test_exact_tool_search_and_activation(profile, target, active):
    result = call(build_server(profile=profile), "pbi_find_tool", query=target)
    match = result["matches"][0]
    assert match["name"] == target
    assert match["in_active_profile"] is active
    assert "\n" not in match["description"]
    if not active:
        assert "HORIZUN_PBI_MCP_PROFILE=full" in match["activation"]
        assert "restart" in match["activation"]
    else:
        assert "activation" not in match


@pytest.mark.parametrize("query", ["", "   ", "la de the", "zznonexistentzz"])
def test_empty_or_unknown_search_does_not_invent_tools(query):
    result = call(build_server(profile="local"), "pbi_find_tool", query=query)
    assert result["matches"] == []
    assert result["total_matches"] == 0


def test_original_guards_and_validation_still_execute():
    server = build_server(profile="local")
    with pytest.raises(ToolError):
        call(server, "pbi_create_measure", table="T")
    blocked = call(server, "pbi_refresh_model")
    assert blocked["ok"] is False
    assert "confirm=true" in blocked["message"]
    dual = call(server, "pbi_create_measure", table="T", name="M", expression="1", mode="both")
    assert dual["error"] == "dual_mode_not_safely_available"


def test_response_layer_is_local_only_and_does_not_mutate_source(monkeypatch):
    from copy import deepcopy
    from horizun_pbi_mcp.tools import dax_tools
    source = {"columns": ["n"], "rows": [[i] for i in range(80)], "row_count": 80,
              "truncated": False, "stats": {"rows_returned": 80},
              "warnings": ["Keep this warning"], "coverage_complete": False}
    original = deepcopy(source)
    monkeypatch.setattr(dax_tools.dax_runner, "run_dax", lambda *a: source)
    full = call(build_server(), "pbi_run_dax", query="EVALUATE T")
    local = call(build_server(profile="local"), "pbi_run_dax", query="EVALUATE T")
    assert full["rows"] == source["rows"]
    assert "_compact" not in full
    assert local["rows"] == source["rows"][:20]
    assert local["row_count"] == local["stats"]["rows_returned"] == 20
    assert local["truncated"] is True
    assert local["warnings"] == full["warnings"]
    assert local["status"] == full["status"]
    assert local["coverage_complete"] is False
    assert source == original


def test_local_offline_workflow_writes_and_rereads_real_synthetic_files(tmp_path, session):
    from pathlib import Path
    from horizun_pbi_mcp.pbip import tmdl_reader

    server = build_server(profile="local")
    csv = tmp_path / "data.csv"
    csv.write_text("Category,Amount\nA,10\nB,20\n", encoding="utf-8")
    created = call(server, "pbi_create_pbip_project", out_dir=str(tmp_path), name="Demo")
    assert created["ok"], created
    assert Path(created["pbip_path"]).is_file()
    opened = call(server, "pbi_open_pbip_project", path=created["pbip_path"])
    assert opened["ok"], opened
    added = call(server, "pbi_add_table_from_file", path=str(csv), table_name="Data")
    assert added["ok"], added
    tables = call(server, "pbi_list_tables", source="pbip", tables=["Data"])
    assert {c["name"] for c in tables["tables"][0]["columns"]} == {"Category", "Amount"}
    categories_csv = tmp_path / "categories.csv"
    categories_csv.write_text("Category\nA\nB\n", encoding="utf-8")
    categories = call(server, "pbi_add_table_from_file",
                      path=str(categories_csv), table_name="Categories")
    assert categories["ok"], categories
    relationship = call(server, "pbi_create_relationship",
                        from_table="Data", from_column="Category",
                        to_table="Categories", to_column="Category", name="Data_Category")
    assert relationship["ok"], relationship
    model = tmdl_reader.read_semantic_model(session.require_active_pbip())
    assert model["relationships"] == [{
        "name": "Data_Category", "from_table": "Data", "from_column": "Category",
        "to_table": "Categories", "to_column": "Category",
        "is_active": True, "cross_filtering": "OneDirection",
    }]
    measure = call(server, "pbi_create_measure", table="Data", name="Total",
                   expression="SUM('Data'[Amount])", mode="pbip", format_string=0,
                   request_id="local-measure")
    assert measure["ok"], measure
    # Idempotency still uses the original tool's frame and original result.
    repeated = call(server, "pbi_create_measure", table="Data", name="Total",
                    expression="SUM('Data'[Amount])", mode="pbip", format_string=0,
                    request_id="local-measure")
    assert repeated["ok"], repeated
    data = tmdl_reader.read_semantic_model(session.require_active_pbip())
    assert len([m for m in data["measures"] if m["name"] == "Total"]) == 1
    page = call(server, "pbi_create_page_from_spec", spec={
        "page_name": "Summary", "layout": "grid", "visuals": [
            {"type": "card", "title": "Total", "fields": {"Values": ["Data[Total]"]}}]})
    assert page["ok"], page
    active = session.require_active_pbip()
    visuals = list(Path(active.report_dir).glob("definition/pages/*/visuals/*/visual.json"))
    assert len(visuals) == 1
    assert "Total" in visuals[0].read_text(encoding="utf-8")
    tmdl = call(server, "pbi_validate_tmdl", use_tom=False)
    assert tmdl["ok"] and tmdl["valid"], tmdl
    assert tmdl["parse_checked"] is False
    validation = call(server, "pbi_validate_pbip_project")
    assert validation["ok"], validation
