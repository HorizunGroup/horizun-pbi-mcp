"""Local-only presentation after execution; never alters the persisted result.

Compact only known data fields. Errors, warnings, validation findings, coverage,
Desktop identity and recovery information are deliberately not generic truncation
targets. A compact response is a preview, not evidence that omitted data is absent.
"""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

LIST_LIMIT = 20


def compact_response(name: str, result: Any) -> Any:
    # Preserve error/conflict/partial-recovery payloads verbatim, including traces.
    if not isinstance(result, dict) or result.get("ok") is not True:
        return result
    if result.get("status") in {"error", "conflict", "rollback_incomplete", "partial_failure"}:
        return result
    if result.get("consistent") is False:
        return result
    out = deepcopy(result)
    reductions: list[dict[str, Any]] = []

    def trim(container, key, path):
        value = container.get(key)
        if isinstance(value, list) and len(value) > LIST_LIMIT:
            container[key] = value[:LIST_LIMIT]
            reductions.append({"field": path, "original_count": len(value),
                               "returned_count": LIST_LIMIT})
            return True
        return False

    def transactions(node, path=""):
        if not isinstance(node, dict):
            return
        for key, value in list(node.items()):
            field = f"{path}.{key}" if path else key
            if key == "transaction" and isinstance(value, dict):
                files = value.get("files")
                # A failed or ambiguous transaction needs all of its evidence.
                if value.get("committed") is True and value.get("clean") is True and isinstance(files, list):
                    value.pop("files")
                    value["file_count"] = len(files)
                    reductions.append({"field": field + ".files", "original_count": len(files),
                                       "returned_count": 0})
            elif key in {"pbip", "live", "result"}:
                transactions(value, field)

    transactions(out)
    for index, effect in enumerate(out.get("side_effects") or []):
        if isinstance(effect, dict) and effect.get("kind") == "files" and effect.get("committed") is True:
            trim(effect, "files", f"side_effects[{index}].files")

    if name == "pbi_list_tables":
        if trim(out, "tables", "tables"):
            out.setdefault("total_tables", len(result["tables"]))
            out["count"] = len(out["tables"])
        for index, table in enumerate(out.get("tables") or []):
            if isinstance(table, dict):
                for key in ("columns", "measures", "partitions"):
                    trim(table, key, f"tables[{index}].{key}")
    elif name == "pbi_add_table_from_file":
        trim(out, "columns", "columns")
        for key in ("tmdl", "m"):
            value = out.get(key)
            if isinstance(value, str) and len(value) > 512:
                out.pop(key)
                reductions.append({"field": key, "omitted_characters": len(value)})
    elif name == "pbi_run_dax" and trim(out, "rows", "rows"):
        out["row_count"] = len(out["rows"])
        out["truncated"] = True
        if isinstance(out.get("stats"), dict):
            out["stats"]["rows_returned"] = len(out["rows"])
            out["stats"]["approx_bytes"] = len(json.dumps(
                out["rows"], ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))
            out["stats"]["truncated_by_profile"] = True

    if reductions:
        out["_compact"] = {
            "profile": "local", "reductions": reductions,
            "note": "Preview only: omitted content is not absent. Filter tables/DAX for detail; "
                    "for full responses set HORIZUN_PBI_MCP_PROFILE=full and restart the server/client.",
        }
    return out
