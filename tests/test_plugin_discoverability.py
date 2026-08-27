"""Discovery metadata and prompt-evaluation guards for the public plugin."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def test_discovery_golden_set_covers_submission_and_routing() -> None:
    data = _json("docs/openai/discovery-evals-v1.json")
    prompts = data["prompts"]
    ids = [case["id"] for case in prompts]

    assert data["schema_version"] == 1
    assert len(ids) == len(set(ids)), "los ids de evaluacion deben ser unicos"
    assert sum(case["kind"] in {"direct", "indirect"} for case in prompts) >= 5
    assert sum(case["kind"] == "negative" for case in prompts) >= 3
    assert {case["locale"] for case in prompts} == {"en", "es"}

    for case in prompts:
        assert case["kind"] in {"direct", "indirect", "negative"}
        assert case["prompt"].strip()
        expected = case["expected"]
        assert isinstance(expected["activate"], bool)
        assert isinstance(expected["mutates"], bool)
        assert expected["workflow"].strip()

    safety_cases = [case for case in prompts
                    if case["kind"] == "negative" and case["expected"]["activate"]]
    assert len(safety_cases) >= 3
    assert all(case["expected"].get("safe_behavior") for case in safety_cases)


def test_workflow_skill_is_discoverable_and_preserves_safety_boundaries() -> None:
    skill = (ROOT / "skills/powerbi-project-workflows/SKILL.md").read_text(
        encoding="utf-8")

    assert "[TODO" not in skill
    assert "Power BI Service" in skill and "Fabric" in skill
    assert 'mode="both"' in skill
    for tool in (
        "pbi_health_check",
        "pbi_prepare_project",
        "pbi_audit_project",
        "pbi_run_dax",
        "pbi_validate_measures",
        "pbi_build_dashboard",
        "pbi_plan_audit_fixes",
        "pbi_prepare_delivery",
    ):
        assert tool in skill, f"la skill no enruta el workflow por {tool}"


def test_plugin_metadata_is_outcome_first_without_selection_gaming() -> None:
    codex = _json(".codex-plugin/plugin.json")
    claude = _json(".claude-plugin/plugin.json")
    registry = _json(".mcp/server.json")
    interface = codex["interface"]

    assert codex["description"] == claude["description"]
    assert codex["keywords"] == claude["keywords"]
    assert "Power BI Desktop" in codex["description"]
    assert "PBIP" in codex["description"]
    assert {"power-bi", "powerbi", "dax", "pbip", "pbir", "tmdl"} <= set(
        codex["keywords"])
    assert 0 < len(registry["description"]) <= 100
    assert "Power BI Desktop" in registry["description"]
    assert "PBIP" in registry["description"]

    prompts = interface["defaultPrompt"]
    assert len(prompts) == 3
    assert len(set(prompts)) == 3
    assert all(0 < len(prompt) <= 128 for prompt in prompts)
    assert all("install" not in prompt.casefold() for prompt in prompts), (
        "los starters deben mostrar valor, no gastar la portada en instalacion")

    public_copy = "\n".join([
        codex["description"],
        interface["shortDescription"],
        interface["longDescription"],
        *prompts,
    ])
    forbidden = re.compile(r"\b(pick[_ -]?me|best|official)\b", re.IGNORECASE)
    assert not forbidden.search(public_copy), (
        "los metadatos no deben intentar manipular la seleccion del modelo")


def test_search_surfaces_link_to_the_scope_overview() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    overview = (ROOT / "docs/POWER_BI_MCP.md").read_text(encoding="utf-8")

    assert "docs/POWER_BI_MCP.md" in readme
    assert "docs/POWER_BI_MCP.md" in llms
    assert "Power BI Desktop" in overview and "TMDL" in overview and "PBIR" in overview
    assert "does not administer Power BI Service" in overview
