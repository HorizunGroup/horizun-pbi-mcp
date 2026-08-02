# Plan — dedicated MCP for Power BI (local Desktop)

> Design document. Decided with Claude Code on 2026-07-06.
> Status: **plan approved, no code yet.** Agreed start: write code in phases (starting with Phase 0) once decided.

## Objective

Create a **dedicated MCP** server for Power BI that talks to the **local Desktop** (the report open on the PC) and covers:

1. Query data with **DAX** (natural language → DAX → results)
2. **Document** the model (measures, tables, relationships, RLS)
3. **Create/edit** DAX measures
4. **Refresh** the dataset (local)
5. **Generate and arrange** visualizations

## Key reality: the "local Desktop" is TWO layers

With Power BI Desktop open, an Analysis Services engine is exposed at `localhost:<port>`.
That engine **is only the DATA layer** (semantic model). The **REPORT** layer
(visuals, pages, canvas layout) is **NOT** in that endpoint or in any live API.

| Objective | Live (local endpoint)? | How |
|---|---|---|
| Query data with DAX | Yes | ADOMD → `executeQueries` against `localhost` |
| Document the model | Yes | Read metadata via TOM |
| Create/edit DAX measures | Yes | TOM writes to the open model (like Tabular Editor) |
| Refresh dataset (local) | Yes | TOM `RefreshType.Full` (cloud workspaces are out of scope) |
| **Generate/arrange visuals** | **Not live** | Only via PBIP/PBIR files on disk |

## Decision: work in PBIP (project) format

Confirmed: the user will use **`.pbip`** (Power BI Project). Saving the report this way
breaks it down into text files on disk:

```
MyReport.pbip
├─ MyReport.SemanticModel/
│   └─ definition/ …            ← TMDL: tables, measures, relationships (editable text)
└─ MyReport.Report/
    └─ definition/
        └─ pages/<page>/visuals/<visual>/visual.json   ← PBIR: each visual is a JSON
```

- **TMDL** (Tabular Model Definition Language) = model and measures as text.
- **PBIR** (enhanced report format) = each visual is a JSON with type, fields and position (x, y, height, width).

So "generate and arrange visuals" = **writing JSON files**. Power BI Desktop detects the
change on disk and reloads.

> **TO VERIFY before Phase 3:** PBIR was in *preview* during 2024. Confirm whether the
> installed 2026 version is already GA and whether the preview feature is enabled
> (Options → Preview Features). This is the plan's critical technical assumption.

## Recommended architecture (hybrid)

```
Claude Code
    │ MCP (stdio)
    ▼
MCP Server  (Python + FastMCP)
    ├─ ADOMD → localhost:<port>   ← DAX queries + document (LIVE, fast)
    ├─ TOM  → localhost:<port>    ← create/edit measures + refresh (LIVE)
    └─ TMDL + PBIR files          ← generate/arrange visuals + durable measures (DISK)
```

**Split rule:**
- What is **query or data** → live endpoint (fast, immediate).
- What is **durable authoring** (visuals, and optionally measures) → PBIP files.

## Language / libraries

- **MCP server:** Python with **FastMCP** (fastest to set up).
- **Live DAX:** `pyadomd` (requires the **ADOMD.NET** client installed).
- **Writing to the model (TOM):** Python is awkward with TOM (it's .NET). Two paths:
  - `pythonnet` loading TOM, **or**
  - invoking **Tabular Editor 2 (CLI, free)** from the server ← **recommended** (robust, avoids interop).
- **PBIR visuals:** pure JSON editing, language-agnostic (direct Python).

## Roadmap by phases

- **Phase 0 — Connection + DAX.** Discover Desktop's local port, `pyadomd`, `pbi_run_dax` tool.
  It's the "hello world" and already delivers value.
- **Phase 1 — Document.** `pbi_document_model` → measures/tables/relationships to Markdown or Excel.
- **Phase 2 — Measures.** Create/edit DAX (Tabular Editor CLI or TMDL).
- **Phase 3 — Visuals.** Generate and arrange visuals by writing PBIR. **Highest-risk phase** (depends on PBIR GA).
- **Phase 4 — Refresh/management** local.

## Risks / open decisions

- **PBIR in preview** → Phase 3 (visuals) is the highest-risk one. Validate early with a test
  `.pbip` before investing in the rest.
- **Dynamic local port** → Desktop changes port on every startup. The MCP must
  discover it (read the `msmdsrv` process or PBI Desktop's temporary connection file).
- **Live vs. files** → editing measures live via TOM doesn't persist to the `.pbix`/`.pbip` until
  saved. Decide whether the MCP writes live, to files, or both coordinated.
- **Environment prerequisites:** ADOMD.NET client installed; Tabular Editor 2 if using that route.

## Agreed next step

Save this plan (done). Once building is decided, start with **Phase 0** — and before
that, **verify PBIR's status** in the installed version.

---

## Update 2026-07-07 — Implementation (validated on machine)

Built and integrated. Technical validations performed **against the real environment**:

- **`pythonnet` works on Python 3.14.3.** `import clr` OK with the `netfx` runtime.
- **ADOMD.NET + TOM were not installed** (neither GAC nor Program Files), nor was Tabular Editor.
  → The `Microsoft.AnalysisServices.*` DLLs are **vendored** (v19.84.1, net45) in `libs/`
  via `scripts/fetch_libs.py` (NuGet, **without admin/GAC**).
- **Live DAX validated:** connection to `localhost:<port>`, catalog discovery,
  `EVALUATE`, DMVs, and model reading with TOM — all OK against an open Desktop.
- **PBIR confirmed GA** in the test `.pbip` files (`definition.pbir` v4.0, `definition/pages/<id>/`).
- **TMDL** with tab-based indentation (measure = 1 tab, props = 2, expression = 3).

### Technical decision (changes from the original plan)

> **TOM via `pythonnet` with vendored DLLs** — instead of **Tabular Editor 2 CLI**.
> Reason: pythonnet is stable here, the DLLs are obtained without installing anything
> on the system, and this avoids an external dependency (TE2 wasn't installed). It gives the same power
> (create/edit measures, refresh) as TE2 while keeping durable editing via TMDL.

### Bugs found and fixed during validation (via smoke tests)

1. **Deadlock in `config.get_session`**: it took a non-reentrant `Lock` and requested it
   again inside `get_settings`. → separate locks + resolving settings outside the lock.
2. **Backup collision within the same second**: `timestamp()` at second precision made
   `copytree` fail. → short random suffix in the backup name.
3. **"Visible ID" heuristic** didn't detect camelCase (`ClientID`). → broadened pattern.

### Status by phase

Phases 0–11 implemented and tested (live + files). 23 MCP tools registered.
33 `pytest` tests green (the ones requiring Desktop are skipped). README and examples ready.

### Multi-agent adversarial review (5 dimensions + verification)

A review was run with subagents (find → verify) over `src/`. Of 26 raw
findings, 15 confirmed. **Fixes applied:**

- **Path traversal** in `project_locator` (critical): `artifacts`/`byPath` paths from
  the `.pbip` are now validated with `ensure_within_base` against the **project
  directory** (not the report's — `.SemanticModel` is a sibling `../`, which is legitimate).
- **Hang on dead ports**: `AdomdClient` now adds `Connect Timeout` to the connection string and
  sets `CommandTimeout`. Avoids indefinite hangs with stale port files.
- **`.NET .Message`** (PascalCase) in `desktop_discovery` (previously used `message`).
- **Multiline TMDL**: expression lines (including blank ones) are indented to 3 tabs.
- **Validation in `live` mode** (measure name/expression), consistent with `pbip`.
- **`both` mode**: if one side fails, the other is still attempted and the inconsistency is reported.
- **Empty/malformed field references** (`Table[]`, `[`) are now rejected.
- Overly broad `except` narrowed (find_template, list_visuals).

**Finding rejected on merit:** "quote TMDL property values (formatString/
displayFolder)". This is **incorrect**: TMDL property values take the rest of the
line (spaces are valid without quotes), and quoting `formatString` would break it
(`#,0` must go unquoted). Not applied.
