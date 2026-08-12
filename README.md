# Horizun PBI MCP

**MCP** (Model Context Protocol) server for working with **local Power BI Desktop** and **`.pbip`** projects from Claude Code.

**v1.5.1** — 134 tools. Covers two complementary Power BI layers plus verified document exports, report **content** export and read-only SharePoint ingestion:

**Install = one prompt.** With Claude Code open, paste:

> Instala el MCP de Power BI de Horizun (HorizunGroup/horizun-pbi-mcp): agrega su marketplace, instala el plugin, corre su instalador de un pegado si falta algún prerequisito, resuelve los pendientes que marque, y no pares hasta que `pbi_install_status` diga `ready` y aparezcan las tools `pbi_*`.

No Claude Code yet? One paste in a normal PowerShell window (no admin; it can install Claude Code too — details in [`docs/INSTALL.md`](docs/INSTALL.md)):

```powershell
irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex
```

| Layer | For what | How |
|---|---|---|
| **Live** (Power BI Desktop open on `localhost:<port>`) | Query data (DAX), document the model, create/edit measures, refresh | ADOMD.NET + TOM via `pythonnet` |
| **On disk** (`.pbip` project) | Generate/arrange visuals, edit the model durably | TMDL (model) + PBIR (report), editing files |

> **Key rule:** the local endpoint **only exposes the DATA layer** (semantic model). **Visuals/pages/layout are NOT** in that endpoint or in any live API — they're edited via PBIR files. This MCP respects that separation: it doesn't try to move visuals "live".

---

## Documentation

| Document | For what |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | Install and register the server in Claude Code, Claude Desktop, Codex or a stdio client |
| [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md) | The 34 baseline tools: domain, risk class, preconditions |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Current architecture, structural debt and invariants |
| [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) | Coexistence with other Power BI MCPs, with verification levels |
| [`AGENTS.md`](AGENTS.md) | Rules for modifying this repository without breaking the contract |
| [`docs/TOOL_CATALOG.md`](docs/TOOL_CATALOG.md) | The 133 tools by block, with their risk class |
| [`docs/DUAL_MODE.md`](docs/DUAL_MODE.md) | Why `mode="both"` is blocked (R15) |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | The two PBIR validation layers and their limits |
| [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) | What is checked before publishing |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | What remains open, with evidence and how to check it |
| [`docs/TUTORIAL.md`](docs/TUTORIAL.md) | From installation to a dashboard, step by step |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Threat model, guarantees and what it does **not** promise |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | What to do when something is left half-done |
| [`docs/PHASE_1A_DESIGN.md`](docs/PHASE_1A_DESIGN.md) | Design of the security layer |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |
| [`tests/fixtures/README.md`](tests/fixtures/README.md) | Fixture strategy: versioned synthetic + ignored local copy |

---

## What it does

- **Live DAX:** runs queries against the open model and returns columns/rows with timings.
- **Documentation:** tables, columns, measures, relationships, hierarchies, roles (RLS) and quality analysis → Markdown.
- **Measures:** create/edit/delete DAX measures in the open model (`live`), in the TMDL file (`pbip`) or in both (`both`).
- **Local refresh:** refreshes the open model in Desktop (not the Service).
- **PBIP:** open/validate projects, automatic backups.
- **`.pbix` → `.pbip` conversion:** report to PBIR (copied if the `.pbix` already carries it, translated if it keeps the legacy format) and model to TMDL, single file or batch folder.
- **PBIR visuals:** list/document visuals, create visuals (cloning real templates from the report), move/resize and arrange by layouts.

## What it does NOT do

- It doesn't move or create visuals "live" on the open canvas (Power BI Desktop doesn't expose an API for that). Visuals are edited via PBIR files with the `.pbip` project.
- It doesn't refresh or publish to the **Power BI Service** (local only).
- It doesn't extract the model from a `.pbix` without Power BI Desktop: the `DataModel` stream is a backup compressed with XPress9 that only the Analysis Services engine knows how to read. When converting, the `.pbix` is opened in Desktop to serialize the model.
- It doesn't translate **legacy** format bookmarks to PBIR: their state model is different and the conversion reports them as pending (`dropped`) instead of losing them silently. Creating new bookmarks is possible (`pbi_create_bookmark`).
- It doesn't invent fields or nonexistent measures when generating pages.

---

## Requirements

- **Windows** (Power BI Desktop is Windows-only) with **Power BI Desktop** installed.
- **Python 3.10+** (tested on 3.14).
- **.NET Framework 4.x** (comes with Windows) — used by `pythonnet`.
- Python dependencies: `mcp` (includes FastMCP), `pythonnet`, `psutil`,
  `python-dotenv`, `openpyxl`, `reportlab`, `pypdf` and `msal`.
- **ADOMD.NET + TOM DLLs** (Analysis Services). Downloaded without admin rights via `scripts/fetch_libs.py` (no need to install in the GAC).
- To edit/create **visuals**: the report saved as **`.pbip` with PBIR** enabled.
- *(Optional)* Tabular Editor **is not required** — see [Technical decisions](#technical-decisions).

---

## Installation

### Direct from Codex or Claude (recommended)

You don't need to download or register a `.exe`, create `.mcp.json` or manually
locate this repository. The plugin sets up an isolated Python environment in
the client's data folder and verifies every download.

**Codex:**

```bash
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
codex plugin add horizun-pbi-mcp@horizun
```

**Claude Code:**

```bash
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

When the first session opens, the plugin runs the full setup automatically in
the background. Check `pbi_install_status`; once it finishes, restart the
client and the 133 `pbi_*` tools will be available. There are no downloads or
additional scripts the user needs to run manually.

> **Honest technical limit:** there's no dedicated executable, but you do need
> Windows, Power BI Desktop and Python 3.10+. The server must run locally:
> a remote MCP cannot access Desktop's local engine or your `.pbip` files.

### Manual installation for development

```bash
cd horizun-pbi-mcp

# 1) Python dependencies
python -m pip install -r requirements.txt
#   or:  python -m pip install -e .

# 2) Analysis Services DLLs (ADOMD.NET + TOM) -> libs/ folder
#    Pinned version (19.84.1) and SHA-256 verified before installing.
python scripts/fetch_libs.py

# 3) Official PBIR schemas (needed to WRITE)
#    Without them, every PBIR write fails with schema_unavailable.
python scripts/fetch_pbir_schemas.py

# 4) (optional, recommended) Microsoft's official PBIR validator
#    Requires Node >= 20. Adds semantic validation of the full report.
python scripts/fetch_report_validator.py

# 5) (optional) configuration
copy .env.example .env    # and edit it
```

Check the result at any time:

```bash
python scripts/doctor.py
```

### Verify

With **Power BI Desktop open** on a report:

```bash
python -m horizun_pbi_mcp.server   # starts the MCP server (stdio); Ctrl+C to exit
```

For a quick test without MCP, in Python:

```python
import sys; sys.path.insert(0, "src")
from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.powerbi import desktop_discovery, dax_runner
s = get_session()
print(desktop_discovery.discover_instances())
desktop_discovery.select_model(s)
print(dax_runner.run_dax(s, 'EVALUATE ROW("ok", 1)'))
```

---

## Registering with an MCP client

Full guide for **Claude Code, Claude Desktop, Codex and generic stdio clients**: [`docs/INSTALL.md`](docs/INSTALL.md).

Each client resolves environment variables, the working directory and the Python interpreter differently, so instead of a `${VAR}` template that fails on half of them, there's a generator that resolves the absolute paths on your machine:

```bash
python scripts/make_mcp_config.py --client all
```

It only prints. To create this repository's local `.mcp.json` (which is in `.gitignore`):

```bash
python scripts/make_mcp_config.py --client claude-code --write
```

Before registering anything, check the installation:

```bash
python scripts/doctor.py
```

Exits with code **0** if everything mandatory is fine. It distinguishes missing dependency, missing DLL, server that won't start, unexpected MCP contract, Desktop closed, stale session and multiple instances. Power BI Desktop being closed does **not** fail the base diagnostic (use `--require-desktop` if you want to require it).

### Environment variables (all optional)

| Variable | Default | Description |
|---|---|---|
| `HORIZUN_PBI_MCP_LIBS_DIR` | `./libs` | Folder with the ADOMD.NET/TOM DLLs |
| `HORIZUN_PBI_MCP_DOTNET_RUNTIME` | `netfx` | pythonnet runtime (`netfx` or `coreclr`) |
| `HORIZUN_PBI_MCP_MAX_ROWS` | `1000` | Default row limit in DAX |
| `HORIZUN_PBI_MCP_OUTPUTS_DIR` | `./outputs` | Documentation and `change_log.md` |
| `HORIZUN_PBI_MCP_BACKUPS_DIR` | `./backups` | `.pbip` backups |
| `HORIZUN_PBI_MCP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `HORIZUN_PBI_MCP_DEFAULT_PBIP` | — | `.pbip` to open on startup |
| `HORIZUN_PBI_MCP_SHAREPOINT_TENANT_ID` | — | Microsoft Entra tenant for SharePoint app-only access |
| `HORIZUN_PBI_MCP_SHAREPOINT_CLIENT_ID` | — | Application/client ID registered in Entra |
| `HORIZUN_PBI_MCP_SHAREPOINT_CLIENT_SECRET` | — | Client secret; environment only, never a tool argument |
| `HORIZUN_PBI_MCP_PDFTOPPM` | auto-detected | Optional exact path to Poppler `pdftoppm` for PDF render verification |

---

## Available tools (133)

> Full catalog by block: [`docs/TOOL_CATALOG.md`](docs/TOOL_CATALOG.md).
> Baseline inventory with risk class and preconditions: [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md).
> Names and signatures are frozen in `tests/golden/tools_v1.json` and verified by `tests/test_tool_contract.py`.

**Connection / DAX**
- `pbi_list_desktop_models` — lists open models (port, connection string, catalog, number of tables).
- `pbi_select_model` — sets the active model (by `port` if there are several).
- `pbi_run_dax` — runs DAX (`query`, `max_rows`).
- `pbi_test_connection` — validates the active connection.
- `pbi_validate_measures` — validates measure DAX WITHOUT modifying the model (dry-run with `DEFINE MEASURE`); useful before creating them.
- `pbi_validate_desktop_render` — opens a `.pbix`/`.pbip`, captures the exact window by PID without depending on focus and only closes Desktop if the tool itself opened it.

**Documentation (Phase 3)**
- `pbi_list_tables`, `pbi_list_measures`, `pbi_list_relationships` — with `source: live|pbip`.
- `pbi_analyze_model_quality` — typical model issues.
- `pbi_document_model` — complete documentation in Markdown to `outputs/`.

**Excel, PDF and SharePoint**
- `pbi_export_excel` — verified workbook with model, report, audit and optional read-only DAX rows.
- `pbi_generate_pdf_report` — executive/technical/audit PDF with optional dashboard PNG/JPEG captures.
- `pbi_sharepoint_list_folder` — lists SharePoint Online folders through Microsoft Graph with pagination and limits.
- `pbi_sharepoint_download_folder` — staged all-or-nothing download to `outputs/sharepoint/`, verified by size and SHA-256.
- `pbi_export_report_content` — exports the report **content**: the data behind each visual, or a query the client declares. Needs the live model; refuses to export an unprocessed one instead of writing a blank file.

**Measures (Phase 4)** — `mode: live|pbip|both`, `overwrite`
- `pbi_create_measure`, `pbi_update_measure`, `pbi_delete_measure` (destructive: `confirm=true`).

**Refresh (Phase 5)**
- `pbi_refresh_model` — `type: full|calculate|clear_values`, `tables` optional (local).

**PBIP project (Phase 6)**
- `pbi_open_pbip_project` (`path`), `pbi_validate_pbip_project`, `pbi_backup_pbip_project` (`mode: folder|zip`, `scope: report|model|both`).

**`.pbix` → `.pbip` conversion**
- `pbi_inspect_pbix` — X-ray of the file without converting it or opening Desktop: report format, whether it carries its own model, pages and resources.
- `pbi_list_convertible_pbix` — preview of a folder: what would be copied, what would need translating and which ones need Desktop.
- `pbi_convert_pbix_to_pbip` — generates the project. Accepts a `.pbix` or a folder (`recursive`), and returns per file what was written, the warnings and what was left out (`dropped`).

> The report is translated without Desktop, but the **model** requires opening each `.pbix` in Power BI Desktop (the session is reused if it's already open, and closed if the tool opened it). With `include_model=false` only the report half is generated, instantly. The original `.pbix` is never modified.
>
> Power BI Desktop **does not open a `.pbip` with paths of 260 characters or more**: pick a short `out_dir` (`C:\pbip`). The tool checks this before writing and aborts with the detail instead of leaving a project that won't open.

**Model editing**
- `pbi_set_column_visibility` / `pbi_hide_columns` — hide/show columns (e.g. IDs). `mode: live|pbip|both`.
- `pbi_set_relationship_direction` — cross filter `single|both` of a relationship. `mode: live|pbip|both`.
- `pbi_disable_auto_date_time` — enables/disables "Auto date/time" (`pbip` only).

**PBIR Report (Phases 7–10)**
- `pbi_list_report_pages`, `pbi_list_visuals` (`page`), `pbi_document_report_layout`.
- `pbi_create_visual` — `page`, `visual_type`, `fields`, `position`, `title` (clones an existing visual as a template).
- `pbi_update_visual_position`, `pbi_arrange_visuals` (`layout: grid|dashboard|executive_summary|custom`).
- `pbi_generate_report_page` — assisted page generation from the model.

**HTML inside Power BI**
- `pbi_add_custom_visual` — registers an AppSource custom visual in the report (defaults to **HTML Content**, which renders HTML/SVG from a DAX measure).
- `pbi_create_html_visual` — creates an HTML Content visual bound to a measure that returns HTML (`html_measure`).
- `pbi_create_measure` with `data_category: "ImageUrl"` — measures that return an **SVG** data-URI and render as an image in native tables/matrices.

**Natural-language sheet generation**
- `pbi_page_building_blocks` — content inventory (model + catalog of existing visuals + canvas) to design a sheet.
- `pbi_preview_spec_html` — **HTML** mockup of a proposed sheet (review before writing).
- `pbi_create_page_from_spec` — materializes a full PBIR sheet from a `spec` (clones existing visuals by style).
- `pbi_export_page_html` — exports an existing page to an HTML mockup.

Every tool returns `{"ok": true/false, ...}`; on error it includes `error` (code) and `message` (the engine's original message, never hidden).

> **Sheet generation flow:** `pbi_page_building_blocks` → (Claude interprets your instruction and builds a `spec`) → `pbi_preview_spec_html` (you review the HTML) → `pbi_create_page_from_spec` (the PBIR gets written).

---

## Usage examples (in natural language with Claude)

- **Run DAX:** *"List the open models, select the only one, and run `EVALUATE TOPN(10, Sales)`."*
- **Document:** *"Document the active model and analyze its quality."* → generates `outputs/model_documentation_*.md`.
- **Create measure:** *"Create the measure `Margin % = DIVIDE([Profit],[Sales])` in the Sales table, format `0.0%`, mode both."*
- **List visuals:** *"Open the `.pbip` at C:/…/Report.pbip and list the visuals on the 'Summary' page."*
- **Create visual:** see [`examples/sample_visual_specs.json`](examples/sample_visual_specs.json).
- **Arrange page:** *"Arrange the 'Summary' page with executive_summary layout."*

More DAX in [`examples/sample_queries.md`](examples/sample_queries.md).

> ⚠️ **PBIR editing and Desktop state:** **report** edits (visuals/layout) are made to files; it's best to make them with **Power BI Desktop closed** and reopen it to see them (if Desktop is open and you save, it overwrites the changes on disk). **Live model** edits (`live` measures) require Desktop **open** and are persisted on save (Ctrl+S).

---

## Troubleshooting

- **Doesn't detect the port / "No se detecto ningun modelo":** open the report in Power BI Desktop; the port changes on every startup (the MCP discovers it on its own). If you use the Microsoft Store version, it's still detected by process.
- **`adomd_not_installed` / `tom_not_installed`:** run `python scripts/fetch_libs.py`. Check that `libs/Microsoft.AnalysisServices.AdomdClient.dll` exists.
- **`clr_not_available`:** .NET is missing; try `PBI_MCP_DOTNET_RUNTIME=coreclr`.
- **DAX error:** the engine's message is returned as-is in `message`. Check the syntax (EVALUATE, quotes).
- **`pbir_not_enabled`:** the report isn't in PBIR. Save as `.pbip` and enable *Power BI Project (PBIR) format* under Options → Preview Features (if applicable to your version) before saving.
- **Power BI doesn't reload visual changes:** close it and reopen it; PBIR loads on open, not live.
- **Permissions/OneDrive:** if the `.pbip` is in OneDrive, close Desktop before editing files and wait for OneDrive to finish syncing; backups are saved in `backups/`.

---

## Technical decisions

- **TOM via `pythonnet` (not the Tabular Editor CLI).** Evaluated: (1) Tabular Editor 2 CLI, (2) `pythonnet` loading TOM, (3) editing TMDL directly. Since `pythonnet` works on Python 3.14 and the ADOMD.NET/TOM DLLs can be **vendored into `libs/` without admin or GAC**, loading them directly with `pythonnet` (runtime `netfx`) was chosen. It's more stable, has no external installation dependencies, and gives full control (create/edit measures and refresh, just like Tabular Editor). **Durable** editing is still available via **TMDL** in `.pbip`.
- **Visuals by cloning.** `pbi_create_visual` clones an existing visual of the same type as a template (preserving the format/theme scaffolding) and only falls back to a minimal template if none exists, warning that it must be validated in Desktop.
- **Security (Phase 11):** automatic backup before every `.pbip` write; atomic JSON (never leaves corrupted files); doesn't overwrite unreadable JSON; path validation; `change_log.md` in `outputs/`; destructive operations require `confirm=true`.

## Limitations / open risks

None of these is a defect that can be fixed from here. They're documented because they affect what the server can promise.

### Schemas Microsoft doesn't publish

Power BI Desktop writes `visualContainer/2.10.0` and `2.11.0` in recent
reports, and those URLs return **404** at the official source. The same
happens with `bookmarks/2.0.0`. **Microsoft's own official CLI can't validate
them either**: it emits `PBIR_SCHEMA_UNREACHABLE` and skips validation of
those files.

For `visualContainer`, 2.10/2.11 are compared against 2.7 because that
downgrade was measured against 275 real files and only differs in what a
later version might add. `bookmarks/2.0.0` is **blocked** with
`schema_unavailable` because there's no earlier version of the same family to
check it against.

Measured on a real 443-document report: 176 validate, 240 remain blocked for this reason.

**G10 remains a documented release exception.**

### `mode="both"` blocked

`live` requires Power BI Desktop open; `pbip` requires it closed. There's no system state in which both destinations can be safely written in a single call. See [`docs/DUAL_MODE.md`](docs/DUAL_MODE.md). **R15 open.**

### `filters` and `interactions` in the page spec

They are **rejected** with `unsupported_feature` indicating the exact JSON path. They aren't silently dropped. Their serialization to PBIR is pending.

### Others

- **PBIR** must be enabled in the `.pbip`; `pbi_validate_pbip_project` checks this.
- The **friendly name** of the open report isn't always readable from the engine (port + catalog are reported instead).
- The on-disk **TMDL parser** is pragmatic (tables, columns, measures, relationships); for rich metadata, use the `live` path.
- `pbi_generate_report_page` is a **heuristic composition**; it doesn't invent fields and warns about what it ignores.
- The server **starts without Node**; what gets blocked are the writes that need the official validator.

---

## Project structure

```
horizun-pbi-mcp/
├─ src/horizun_pbi_mcp/    # single installable package
│  ├─ server.py            # FastMCP + tool registration
│  ├─ config.py            # settings + session (active model/pbip)
│  ├─ logging_config.py
│  ├─ reporting.py         # Markdown documentation + quality
│  ├─ branding.py          # product identity and version
│  ├─ powerbi/             # live layer (ADOMD/TOM)
│  ├─ pbip/                # on-disk layer (TMDL/PBIR)
│  ├─ services/            # security, validation, audit, workflows
│  ├─ tools/               # MCP tools by area
│  └─ utils/               # JSON, files, validation, change_log
├─ scripts/fetch_libs.py   # downloads Analysis Services DLLs
├─ examples/  tests/  outputs/  libs/
├─ README.md  PLAN.md  pyproject.toml  requirements.txt  .env.example
```

## Tests

```bash
python -m pytest -q
```

**2039 tests, 3 skipped.** The skips are environmental and say how to run them:

| Skipped | Condition |
|---|---|
| `test_run_dax_live` | Requires a Power BI Desktop instance serving a model. `python -m pytest -m live` |
| `test_no_llega_a_cero_por_acumular_infos` | Requires the synthetic model to trigger only informational rules |

Available markers:

```bash
python -m pytest -m "not packaging"     # fast: skips wheel and sdist
python -m pytest -m live                # against an open Power BI Desktop
python -m pytest -m live_validator      # against Microsoft's official CLI
```

Verify the MCP contract (the 133 tools are frozen):

```bash
python -m tests.contract_utils
```

Returns 0 if there are no breaks, 1 if there are, with a report stating **what** changed and **whether it breaks compatibility**.

Installation diagnostics:

```bash
python scripts/doctor.py
```

## License

Open source under the [Apache License 2.0](LICENSE). Also see
[NOTICE](NOTICE) for third-party attributions and trademarks.
