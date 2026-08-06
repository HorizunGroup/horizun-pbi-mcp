# Tool catalog — 133

Generated from the contract frozen in `tests/golden/tools_v1.json`.
The **34 baseline** tools keep their name, parameters, types, defaults and response shape since version 0.1.0.

> Not sure where to start? Call **`pbi_start_here`**: it looks at the project's
> real state and answers with the three or four steps that matter right now,
> each with its reason. This catalog is the reference; that tool is the path.

The counts come from `tests/test_tool_contract.py`, which is what the suite
verifies. They used to be written by hand, and the header once said 101 with 112
tools registered.

| Block | No. |
|---|---|
| Original baseline | 34 |
| A — platform | 7 |
| B — semantic model | 16 |
| C — PBIR authoring | 18 |
| D — declarative spec | 8 |
| E — comprehensive audit | 6 |
| F — workflows | 8 |
| F R5 — atomicity | 2 |
| G — `.pbix` conversion | 3 |
| H — composition, theme and bookmarks | 7 |
| I — pre-delivery verification | 3 |
| J — data loading | 2 |
| L — design and entry point | 5 |
| M — work cycle | 1 |
| N — refactoring | 1 |
| O — session exit | 1 |
| P — intent brief | 2 |
| Q — data diagnostics | 1 |
| R — external sources | 1 |
| S — ecosystem port | 2 |
| T — exports and SharePoint | 5 |
| **Total** | **133** |

---

## Session, capabilities and recovery

| Tool | What it does |
|---|---|
| `pbi_health_check` | General status: dependencies, DLLs, session, pending journals |
| `pbi_capabilities` | What can be done **now**, and what can't, with the reason |
| `pbi_session_info` | Active model and project, and their freshness |
| `pbi_list_desktop_models` | Open Power BI Desktop instances |
| `pbi_select_model` | Sets the active model (requires a port if there are several) |
| `pbi_test_connection` | Validates the connection |
| `pbi_validate_desktop_render` | Captures the report's exact window by PID, without focus; only closes Desktop if the tool opened it |
| `pbi_close_desktop` | **Destructive** (`confirm`): closes ONLY the Desktop instance serving that file, verifies identity by name+start time, re-checks the file is no longer open |
| `pbi_list_pending_journals` | Journals of operations left half-done |
| `pbi_inspect_journal` | Compares a journal with the current state (read-only) |

## Model query and exploration

| Tool | What it does |
|---|---|
| `pbi_run_dax` | Read-only DAX, with row, byte, timeout and export limits |
| `pbi_validate_measures` | Validates DAX without modifying the model |
| `pbi_model_summary` | Compact summary to get your bearings |
| `pbi_search_model` | Search by name and within the DAX |
| `pbi_get_object` | Detail of a table, column or measure |
| `pbi_measure_dependencies` | What it depends on and who uses it |
| `pbi_column_dependencies` | What breaks if you touch a column |
| `pbi_list_tables` · `pbi_list_measures` · `pbi_list_relationships` | Inventories |
| `pbi_list_hierarchies` · `pbi_list_roles` · `pbi_list_perspectives` · `pbi_list_partitions` | Secondary objects |
| `pbi_document_model` | Markdown documentation of the model |

## Model modification

> `mode`: `live` (Desktop open) or `pbip` (Desktop closed). **`both` is disabled**; see `docs/PHASE_1A_DESIGN.md`.

| Tool | Destructive |
|---|---|
| `pbi_create_measure` · `pbi_update_measure` | No |
| `pbi_delete_measure` | **Yes** (`confirm`) |
| `pbi_rename_measure` | No — updates TMDL header, DAX refs and report visuals in ONE transaction; qualified refs and bookmarks come back as warnings, never silently |
| `pbi_set_column_visibility` · `pbi_hide_columns` | No |
| `pbi_set_relationship_direction` · `pbi_disable_auto_date_time` | No |
| `pbi_refresh_model` | Irreversible. Devuelve `rows_by_table`: un refresh puede terminar en 'ok' y cargar CERO filas |
| `pbi_open_and_refresh` | Irreversible. Abre en Desktop y refresca en una llamada: un `.pbip` recien abierto trae el modelo SIN datos |

## Project and plans

| Tool | What it does |
|---|---|
| `pbi_open_pbip_project` · `pbi_validate_pbip_project` | Open and validate the `.pbip` |
| `pbi_backup_pbip_project` | Backup with hash manifest |
| `pbi_plan_change` | Computes a plan with a diff and `plan_token`, without writing |
| `pbi_apply_plan` | Applies the plan if the state hasn't changed |

## `.pbix` → `.pbip` conversion

| Tool | What it does |
|---|---|
| `pbi_inspect_pbix` | X-ray of the `.pbix` without converting it or opening Desktop |
| `pbi_list_convertible_pbix` | Preview of a folder: what gets copied, what needs translating, what needs Desktop |
| `pbi_convert_pbix_to_pbip` | Generates the project (PBIR report + TMDL model); accepts a file or a folder |

## Composition and visual identity

| Tool | What it does |
|---|---|
| `pbi_list_themes` | Available palettes, verified against color blindness, with their use scenario |
| `pbi_apply_theme` | Writes the theme and declares it in the report (all three parts or none) |
| `pbi_add_image_resource` | Embeds an image and declares it: without both, the visual comes out empty |
| `pbi_list_report_resources` | Declared resources, on disk, and the ones that don't match |
| `pbi_set_conditional_format` | Color driven by the data: turns a matrix into a heat map |

## Semantic model authoring

| Tool | What it does |
|---|---|
| `pbi_create_calculated_column` | DAX calculated column, declared before the partition |
| `pbi_create_relationship` | Relationship between columns, in `relationships.tmdl` |
| `pbi_create_hierarchy` | Hierarchy over columns of the same table |

## Proposals and data quality

| Tool | What it does |
|---|---|
| `pbi_propose_dashboard` | Classifies the model and proposes complete designs, with their reasoning |
| `pbi_profile_data` | Profiles the VALUES: out-of-range percentages, empty columns |
| `pbi_add_table_from_source` | Table from SQL Server / PostgreSQL / OData / Web JSON. Columns are declared (no credentials ⇒ no schema read, and columns are never invented); the response always states that the first refresh needs a human in Desktop |
| `pbi_define_port_contract` · `pbi_check_contract` | The ecosystem port as a DATA CONTRACT (not an API bus): datasets with a shared key, validated against incoming files (structure) and against the live model — returns the port keys ready as `critical_fields` for the brief |
| `pbi_diagnose_data` | Content-level checks on the LIVE model: orphan keys, duplicated grain, calendar gaps, and the brief's critical-field thresholds — every finding carries its DAX proof and sample culprits |

## Report authoring (PBIR)

| Tool | Destructive |
|---|---|
| `pbi_report_capabilities` · `pbi_get_visual` · `pbi_list_report_pages` · `pbi_list_visuals` | No |
| `pbi_create_visual` · `pbi_duplicate_visual` · `pbi_create_html_visual` · `pbi_add_custom_visual` | No |
| `pbi_delete_visual` | **Yes** (`confirm`) |
| `pbi_set_visual_title` · `pbi_set_visual_z_order` · `pbi_replace_visual_field` · `pbi_copy_visual_format` · `pbi_update_visual_position` · `pbi_set_visual_filter` | No |
| `pbi_duplicate_page` · `pbi_rename_page` · `pbi_reorder_pages` | No |
| `pbi_delete_page` | **Yes** (`confirm`) |
| `pbi_detect_layout_issues` · `pbi_align_visuals` · `pbi_distribute_visuals` · `pbi_normalize_page_layout` · `pbi_arrange_visuals` | No |
| `pbi_document_report_layout` · `pbi_export_page_html` | No |

## Declarative builder

| Tool | What it does |
|---|---|
| `pbi_list_page_presets` | The 6 presets with their blocks |
| `pbi_generate_page_spec` | Draft spec from a preset |
| `pbi_validate_page_spec` | Validates schema and references, with JSON path |
| `pbi_preview_page_spec` | HTML mockup with the final positions |
| `pbi_diff_page_spec` | Compares against an existing page |
| `pbi_apply_page_spec` | Materializes in one transaction (`dry_run` available) |
| `pbi_validate_generated_page` | Verifies the page already written |
| `pbi_page_building_blocks` · `pbi_preview_spec_html` · `pbi_create_page_from_spec` · `pbi_generate_report_page` | Original (baseline) flow |

## Audit

| Tool | What it does |
|---|---|
| `pbi_audit_project` | Comprehensive: model + report + layout, JSON/MD/HTML |
| `pbi_audit_model` · `pbi_audit_report_only` · `pbi_analyze_model_quality` | By domain |
| `pbi_list_audit_rules` · `pbi_list_autofix_rules` | Catalogs |
| `pbi_plan_audit_fixes` | Plans fixes for **specific** rules |
| `pbi_apply_audit_fixes` | **Destructive** (`confirm`) |

`pbi_audit_report_only` includes two rules for visuals that Power BI refuses
to draw — the failure no schema can see, because the JSON is valid and the
fault is in the field configuration: `report_scatter_axis_not_aggregated`
(Details plus non-aggregated X/Y) and `report_slicer_below_height_floor` (under its floor: 76px with a visible
header, 48px without — both measured against the official CLI).

## Excel, PDF and SharePoint

| Tool | What it does |
|---|---|
| `pbi_export_report_content` | Exports the report CONTENT — the data behind each visual, or a query the client declares — to `.xlsx`/`.pdf` under `outputs/content/`. Needs the live model: opens Desktop if needed and refuses to export when the model is open but unprocessed. Every sheet declares which filters were applied and which could not be |
| `pbi_export_excel` | Verified `.xlsx` with summary, model, relationships, pages, visuals, audit and optional read-only DAX data. Reopens the workbook before publishing it under `outputs/excel/` |
| `pbi_generate_pdf_report` | Executive, technical or audit PDF; can embed PNG/JPEG captures returned by `pbi_validate_desktop_render`; logical verification is mandatory and Poppler render verification is reported when available |
| `pbi_sharepoint_list_folder` | Lists a SharePoint Online folder through Microsoft Graph v1.0, with pagination, optional recursion and explicit item limit |
| `pbi_sharepoint_download_folder` | Downloads a filtered folder as an all-or-nothing staged batch under `outputs/sharepoint/`, with size checks and SHA-256 re-read |

SharePoint uses app-only MSAL authentication. Credentials only come from
`HORIZUN_PBI_MCP_SHAREPOINT_TENANT_ID`, `_CLIENT_ID` and `_CLIENT_SECRET`;
tokens and secrets never form part of a tool signature or response.

## Workflows

| Tool | Result |
|---|---|
| `pbi_build_dashboard` · `pbi_build_executive_page` · `pbi_build_evm_page` | A complete, verified page |
| `pbi_repair_broken_references` | Diagnoses and repairs with `mapping` |
| `pbi_normalize_report` | Geometry of every page |
| `pbi_prepare_delivery` | Delivery checklist + plan |
| `pbi_compare_live_to_pbip` | Memory vs. disk differences |
| `pbi_generate_technical_documentation` | Full Markdown |

> All workflows default to `dry_run=true`.

---

## Design and entry point

Close two gaps of the same kind: having the pieces isn't the same as knowing how to use them.

| Tool | What it does |
|---|---|
| `pbi_start_here` | Looks at the real state and gives the next steps, with the reason for each |
| `pbi_define_brief` | Writes the intent brief (`pbi-brief.json` next to the .pbip): what the dashboard is FOR, answered by the human. Proposals, design system and the guide consume it |
| `pbi_get_brief` | Reads the brief, or returns the questions to ask the user when it doesn't exist |
| `pbi_list_design_systems` | Available systems: each one decides theme, canvas, grid and text scale all at once |
| `pbi_apply_design_system` | Applies the system and returns the grid, to place things by hand on the same guides |
| `pbi_compose_page` | Composes an entire page on the grid from intent |
| `pbi_reflow_pages` | The way back after changing system: rescales already-written pages to the new canvas and recomputes the text colour baked in at composition time |

## Risk classes

Every tool declares its class in [`src/tools/risk.py`](../src/horizun_pbi_mcp/tools/risk.py),
and the server translates it into the MCP `annotations` (`readOnlyHint`,
`destructiveHint`, `idempotentHint`, `openWorldHint`) so the client can decide
what to run without asking and what to warn about. The table is contrasted
against the code by `tests/test_tool_annotations.py`: it is not documentation
that can quietly drift.

| Class | No. | Behavior | `readOnlyHint` |
|---|---|---|---|
| `read_only` | 54 | Doesn't modify anything of the user's and leaves no file behind | `true` |
| `read_only_emits_file` | 12 | Doesn't touch the project, but writes a report/export into `outputs/` | `false` |
| `read_external` | 1 | Reads SharePoint through Microsoft Graph; no local or remote write | `true`, `openWorldHint: true` |
| `read_external_emits_file` | 1 | Reads SharePoint and publishes a verified download under `outputs/` | `false`, `openWorldHint: true` |
| `side_effect_external` | 2 | Opens — and sometimes closes — Power BI Desktop: `pbi_open_in_desktop`, `pbi_validate_desktop_render` | `false` |
| `write_reversible` | 52 | Transaction with journal; rollback on failure | `false` |
| `write_destructive` | 9 | Requires `confirm=true`, including deletes, recovery and backup purge | `false`, `destructiveHint: true` |
| `write_irreversible` | 2 | Refresh operations whose external effect cannot be rolled back | `false`, `destructiveHint: true` |
| `unsupported` | — | `mode="both"` and cloud/Fabric — declared with their reason in `pbi_capabilities` | — |

Two notes on why the boundary sits where it does:

- **`read_only_emits_file` is not `read_only`.** `readOnlyHint` means the tool
  doesn't modify its environment, and creating a file modifies it. Writing a
  report into `outputs/` doesn't touch the user's project, but it isn't
  "read-only" for the protocol either.
- **`idempotentHint` is `false` on everything that writes**, even though the
  server has idempotency by `request_id`. That protection is the client's
  opt-in: with no `request_id`, two identical calls to `pbi_create_visual`
  create two visuals. Announcing `true` would promise a guarantee that depends
  on the caller doing their part.
