"""Local previews must not erase failures, coverage or recovery evidence."""
from copy import deepcopy

import pytest

from horizun_pbi_mcp.services.compact_response import compact_response


@pytest.mark.parametrize("status", ["error", "conflict", "rollback_incomplete"])
def test_errors_are_preserved_verbatim(status):
    payload = {"ok": False, "status": status, "error": "failure", "message": "failed",
               "details": {"trace": "x" * 9000, "files": list(range(100))}}
    assert compact_response("pbi_create_measure", payload) is payload


def test_committed_transaction_summarized_without_losing_recovery_or_warnings():
    payload = {"ok": True, "status": "warning", "warnings": ["partial coverage"],
               "pbip": {"transaction": {"committed": True, "clean": True,
                         "journal": "journal/path", "files": [{"path": str(i), "hash": "a" * 64}
                                                                 for i in range(80)],
                         "schema_unchecked": ["schema_missing"]}},
               "model_validation": {"valid": False, "parse_checked": False,
                                    "findings": [{"severity": "error"}] * 60,
                                    "limitations": ["requires refresh"]}}
    original = deepcopy(payload)
    result = compact_response("pbi_create_measure", payload)
    txn = result["pbip"]["transaction"]
    assert "files" not in txn
    assert txn["file_count"] == 80
    assert txn["journal"] == "journal/path"
    assert txn["schema_unchecked"] == ["schema_missing"]
    assert result["model_validation"] == payload["model_validation"]
    assert result["warnings"] == payload["warnings"]
    assert result["status"] == "warning"
    assert len(str(result)) < len(str(payload))
    assert payload == original


def test_ambiguous_transaction_keeps_all_details():
    payload = {"ok": True, "status": "warning", "transaction": {
        "committed": False, "clean": False, "files": list(range(100)), "journal": "J"}}
    assert compact_response("pbi_create_measure", payload) == payload


def test_partial_failure_is_preserved_even_with_ok_true():
    payload = {"ok": True, "status": "partial_failure", "transaction": {
        "committed": True, "clean": True, "files": list(range(100)), "journal": "J"}}
    assert compact_response("pbi_create_measure", payload) is payload


def test_table_and_column_previews_have_explicit_counts():
    tables = [{"name": f"T{i}", "columns": [{"name": f"C{j}"} for j in range(50)]}
              for i in range(30)]
    payload = {"ok": True, "status": "success", "count": 30, "tables": tables}
    result = compact_response("pbi_list_tables", payload)
    assert result["count"] == len(result["tables"]) == 20
    assert result["total_tables"] == 30
    assert len(result["tables"][0]["columns"]) == 20
    reductions = result["_compact"]["reductions"]
    assert reductions[0]["original_count"] == 30
    assert reductions[1]["original_count"] == 50
    assert len(payload["tables"]) == 30


def test_import_dry_run_omissions_are_measured_not_fabricated():
    payload = {"ok": True, "status": "planned", "dry_run": True,
               "tmdl": "x" * 2000, "m": "y" * 1000, "warnings": ["header inferred"]}
    result = compact_response("pbi_add_table_from_file", payload)
    assert result["dry_run"] is True
    assert result["status"] == "planned"
    assert result["warnings"] == payload["warnings"]
    assert result["_compact"]["reductions"] == [
        {"field": "tmdl", "omitted_characters": 2000},
        {"field": "m", "omitted_characters": 1000}]


def test_validation_and_desktop_identity_are_not_generic_truncation_targets():
    payload = {"ok": True, "status": "warning", "valid": False, "parse_checked": False,
               "coverage_complete": False, "selected": False, "identity": {"status": "unknown"},
               "findings": [{"severity": "error", "message": str(i)} for i in range(100)],
               "warnings": ["warning"] * 40, "limitations": ["limit"] * 40}
    for name in ("pbi_validate_tmdl", "pbi_validate_pbip_project", "pbi_open_in_desktop"):
        assert compact_response(name, payload) == payload
