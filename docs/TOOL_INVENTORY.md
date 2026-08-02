# Inventory of the 34 tools

_Frozen in `tests/golden/tools_v1.json`. Verified via a real MCP handshake over stdio._

## Risk classes

| Class | No. | What it means |
|---|---|---|
| `read_only` | 16 | Doesn't modify anything of the user's. Some write artifacts to `outputs/` |
| `session_state` | 2 | Changes which model/project is active. Persists in `outputs/session.json` |
| `write_safe` | 1 | Only creates a backup |
| `write_reversible` | 13 | Modifies model or report. Makes a prior backup |
| `write_irreversible` | 1 | Alters data with no going back |
| `write_destructive` | 1 | Deletes definitions. Requires `confirm=true` |

> **Real guarantee status (baseline):** the backup exists, but there's **no restoration**, **no `dry_run`**, **no `expected_state`**, and **no re-read verification**. The `write_reversible` class describes the *intent*, not a closed guarantee. Completed in Phase 1.

---

## 1. Session, connection and DAX

| Tool | Class | Precondition | Returns | When NOT to use it |
|---|---|---|---|---|
| `pbi_list_desktop_models` | `read_only` | none | `count`, `instances[]` (port, pid, catalog, number of tables), `diagnostics` | — |
| `pbi_select_model` | `session_state` | ≥1 live instance | `active_model` | If there are several instances it **requires** `port`: it doesn't pick on its own, and that's deliberate |
| `pbi_run_dax` | `read_only`\* | active model | `columns`, `rows`, `truncated`, `elapsed_ms` | \*It's read-only **by intent, not by validation**: it doesn't check the statement type |
| `pbi_test_connection` | `read_only` | active model | `connected`, `elapsed_ms` | — |
| `pbi_validate_measures` | `read_only` | active model | per measure: `valid`, `value`, `error` | Use it **before** `pbi_create_measure`: it validates without touching the model |

## 2. Model documentation

| Tool | Class | Precondition | Returns |
|---|---|---|---|
| `pbi_list_tables` | `read_only` | active model (`live`) or `.pbip` (`pbip`) | `tables[]` with columns and types |
| `pbi_list_measures` | `read_only` | same | `measures[]` with DAX, format, folder |
| `pbi_list_relationships` | `read_only` | same | `relationships[]` with cardinality and cross filter |
| `pbi_analyze_model_quality` | `read_only` | same | `issue_count`, `by_severity`, `issues[]` |
| `pbi_document_model` | `read_only` | same | path of the Markdown in `outputs/` |

> `pbi_analyze_model_quality` applies **7 heuristic rules** to the model (`src/reporting.py:23`). They have no stable identifier or `auto_fix_available`, and there's no report rule at all. Redone in Phase 4.

## 3. Measures — `mode: live | pbip | both`

| Tool | Class | Precondition | Returns |
|---|---|---|---|
| `pbi_create_measure` | `write_reversible` | depends on `mode` | `action`, `before`, `after` |
| `pbi_update_measure` | `write_reversible` | same | same. Anything unspecified is kept |
| `pbi_delete_measure` | **`write_destructive`** | same + **`confirm=true`** | `action`, `before` |

**`mode` semantics:**
- `live` → Desktop's in-memory model (TOM). **Requires user Ctrl+S** to persist.
- `pbip` → TMDL file on disk, with backup.
- `both` → both, with errors isolated: if one side fails, the other is still attempted and `consistent: false` is returned.

## 4. Model editing

| Tool | Class | Note |
|---|---|---|
| `pbi_set_column_visibility` | `write_reversible` | `mode: live\|pbip\|both` |
| `pbi_hide_columns` | `write_reversible` | Batch version of the above. **No preview or diff** |
| `pbi_set_relationship_direction` | `write_reversible` | Switching to `single` can alter totals that depended on the bidirectional filter |
| `pbi_disable_auto_date_time` | `write_reversible` | `pbip` only. **Only prevents NEW tables**: existing `LocalDateTable_*` tables stay, and deleting them can break visuals using the automatic hierarchy |

## 5. `.pbip` project

| Tool | Class | Returns |
|---|---|---|
| `pbi_open_pbip_project` | `session_state` | detected structure + warnings if PBIR or TMDL is missing |
| `pbi_validate_pbip_project` | `read_only` | `valid`, `checks`, `warnings` |
| `pbi_backup_pbip_project` | `write_safe` | backup path (`mode: folder\|zip`, `scope: report\|model\|both`) |

## 6. Refresh

| Tool | Class | Note |
|---|---|---|
| `pbi_refresh_model` | **`write_irreversible`** | Local only, never the Service. `type: full\|calculate\|clear_values`. No going back except refreshing again |

## 7. PBIR Report

| Tool | Class | Precondition | Note |
|---|---|---|---|
| `pbi_list_report_pages` | `read_only` | `.pbip` with PBIR | — |
| `pbi_list_visuals` | `read_only` | same | `page` accepts id or display name |
| `pbi_document_report_layout` | `read_only` | same | Markdown in `outputs/` |
| `pbi_create_visual` | `write_reversible` | same | Clones a template of the same type. Without a template it warns that it must be validated in Desktop |
| `pbi_add_custom_visual` | `write_reversible` | same | Registers in `publicCustomVisuals`. Desktop downloads it from AppSource on open |
| `pbi_create_html_visual` | `write_reversible` | same | Requires a measure that **returns HTML** |
| `pbi_update_visual_position` | `write_reversible` | same | — |
| `pbi_arrange_visuals` | `write_reversible` | same | `layout: grid\|dashboard\|executive_summary\|custom` |
| `pbi_generate_report_page` | `write_reversible` | same | Heuristic composition. Doesn't invent fields: reports the ones it ignores |

> ⚠️ **All PBIR write tools share an unmitigated risk in the baseline:** none of them check whether Power BI Desktop has the project open. If it does and the user saves, it overwrites what was written to disk. There's only a text warning (`RELOAD_HINT`).

## 8. Declarative sheet generation

| Tool | Class | Role in the flow |
|---|---|---|
| `pbi_page_building_blocks` | `read_only` | **1.** Inventory: model + catalog of existing visuals + canvas |
| `pbi_preview_spec_html` | `read_only` | **3.** HTML mockup of the proposed spec, without writing anything |
| `pbi_create_page_from_spec` | `write_reversible` | **4.** Materializes the PBIR page |
| `pbi_export_page_html` | `read_only` | Mockup of an **already existing** page |

Intended flow: `building_blocks` → *(the LLM builds the spec)* → `preview_spec_html` → *(the user approves)* → `create_page_from_spec`.

**Missing today:** `schema_version` in the spec, schema validation, reference validation against the model, prior `diff` and `rollback`. Phase 3.

---

## Appendix: parameters that should be `enum`

No tool declares `enum` in its `inputSchema`; they're validated by hand at runtime. Converting them is Phase 1 work and is **additive** (doesn't break the contract).

| Parameter | Tools | Values accepted today |
|---|---|---|
| `mode` | 6 write tools | `live`, `pbip`, `both` |
| `source` | 5 documentation tools | `live`, `pbip` |
| `layout` | `arrange_visuals`, `generate_report_page` | `grid`, `dashboard`, `executive_summary`, `custom` |
| `direction` | `set_relationship_direction` | `single`, `both` |
| `type` | `refresh_model` | `full`, `calculate`, `clear_values`, `automatic`, `data_only` |
| `mode` / `scope` | `backup_pbip_project` | `folder`/`zip` · `report`/`model`/`both` |
| `visual_type` | `create_visual` | 8 aliases → 8 real PBIR types |
