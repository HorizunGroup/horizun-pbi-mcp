# Horizun PBI MCP Architecture

_Status verified on baseline commit `a304e33`. 4,982 lines of Python in `src/`._

---

## 1. Current architecture (what exists today)

```
                        MCP client (Claude Code / Desktop / Codex)
                                        │  JSON-RPC over stdio
                                        ▼
                            src/horizun_pbi_mcp/server.py — build_server()
                                  FastMCP("horizun-pbi-mcp")
                                        │  register(mcp) × 8
        ┌───────────────┬───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼               ▼
   dax_tools     documentation_   measure_tools   model_edit_    visual_tools
   (5 tools)     tools (5)        (3)             tools (4)      (9)
                                                          page_tools (4)
                                                          pbip_tools (3)
                                                          refresh_tools (1)
        └───────────────┴───────────────┬───────────────┴───────────────┘
                                        │  tools/_common.guard()
                                        │  ← ONLY cross-cutting abstraction
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
              src/powerbi/  (LIVE)                src/pbip/  (ON DISK)
        ┌─────────────────────────┐     ┌──────────────────────────────┐
        │ clr_bootstrap  loads CLR│     │ project_locator  locates     │
        │ adomd_client   ADOMD.NET│     │ tmdl_reader/writer  model    │
        │ dax_runner     queries  │     │ pbir_reader/writer  report   │
        │ desktop_discovery ports │     │ visual_factory   cloning     │
        │ model_reader   metadata │     │ layout_engine    geometry    │
        │ model_writer   TOM      │     │ page_builder     sheets+HTML │
        │ refresh                 │     │ backup                       │
        └─────────────────────────┘     └──────────────────────────────┘
                        │                               │
                        └───────────────┬───────────────┘
                                        ▼
                    src/utils/     json_utils (atomic writing)
                                   file_utils · validation · change_log
                    src/config.py  Settings + Session (process singletons)
                    src/reporting.py  Markdown + quality rules
```

### What's well solved

| Aspect | Where | Why it matters |
|---|---|---|
| Live/disk separation | `powerbi/` vs `pbip/` | Reflects a real constraint: the local endpoint **only** exposes data, not visuals |
| Atomic JSON writing | `utils/json_utils.py:39` | Serializes in memory → `.tmp` → `os.replace`. Never leaves a half-written JSON |
| Refusal to overwrite corrupt JSON | `utils/json_utils.py:20` | If it doesn't parse, it raises instead of overwriting |
| Cloning instead of inventing | `pbip/visual_factory.py:166` | An invented `visual.json` almost never opens well; cloning preserves the theme scaffolding |
| Logging outside stdout | `logging_config.py` | stdout is the JSON-RPC channel; writing there breaks the protocol |
| Error isolation in `both` mode | `tools/measure_tools.py:29` | If `live` fails, `pbip` is still attempted and marked `consistent: False` |

### What's missing (structural debt)

| # | Problem | Evidence |
|---|---|---|
| A1 | **No service layer exists.** Tools call the adapters directly | `page_tools.py:16` imports `_measure_index` and `_model_data` from `visual_tools` |
| A2 | **`_dual()` duplicated** almost identically | `measure_tools.py:29` and `model_edit_tools.py:24` |
| A3 | **A tool calling another tool** | `model_edit_tools.py:75`: `pbi_hide_columns` invokes `pbi_set_column_visibility`. Only works because FastMCP 1.28.1's `mcp.tool()` returns the original function |
| A4 | **Scattered backup policy** | Each call decides `do_backup=True/False`; there's no central criterion |
| A5 | **No concurrency control** | `grep -r "expected_state\|request_id\|dry_run" src/` → 0 results |
| A6 | **Unbounded PBIR writes** | `ensure_within_base()` is only used in `project_locator.py:24`; the writers build paths with user input |
| A7 | **No post-write verification** | It writes and returns; nothing re-reads to confirm |
| A8 | **Non-reusable backups** | `backup_before_edit()` creates copies, but no tool knows how to restore them |
| A9 | **Loose schemas** | 0 of 34 tools use `enum`; `mode`, `layout`, `source`, `direction` are free strings |

---

## 2. Target architecture

```
   MCP tools            ← only signature, input validation and serialization
       ↓                  (never open files or connections)
   Application services  ← workflows: create_page, edit_measure, audit
       ↓                  (decide backup, lock, dry-run, verification)
   Domain + validation   ← page spec, positions, model references
       ↓
   Adapters
     ├─ desktop discovery
     ├─ ADOMD.NET
     ├─ TOM
     ├─ TMDL filesystem
     └─ PBIR filesystem
```

### Planned shared services

| Service | Responsibility | Resolves |
|---|---|---|
| `sessions` | discover, select, detect dead session, reconnect | R6 (stale session) |
| `safety` | `dry_run`, `confirm`, `expected_state`, `request_id` | A5 |
| `paths` | every path bounded to the active project | A6 |
| `locking` | per-project lock + open-Desktop detection | R2 |
| `backup` | incremental, with retention and **restoration** | A4, A8 |
| `verify` | re-read after writing and compare | A7 |
| `envelope` | uniform response `status/target/before/after/validation/backup/warnings` | contract |
| `telemetry` | structured logging with request id and duration | observability |

### Migration without breaking anything

The 34 tools are kept **with the same name and the same signature**. New fields are **added** to the response dict; `ok` still exists. The golden in `tests/golden/tools_v1.json` blocks any deviation: a new required parameter or a changed `default` fails the suite with a readable report.

---

## 3. Project invariants

Rules no phase can break. See also [AGENTS.md](../AGENTS.md).

1. **stdout is sacred.** All logging goes to stderr or a file.
2. **Never overwrite JSON that doesn't parse.**
3. **Every project write is preceded by a backup** and followed by a re-read.
4. **No write path leaves the active project.**
5. **No fields are invented** that don't exist in the model.
6. **Destructive tools require `confirm=true`.**
7. **Versioned fixtures contain no real data.**
8. **The 34 baseline tools are not renamed or removed** without a compatibility layer.

---

## 4. External constraint that shapes the whole design

Power BI Desktop's local engine (`msmdsrv.exe` on `localhost:<port>`) exposes **only the semantic model**. Pages, visuals and layout **don't exist** in any live endpoint: only in PBIR files.

Hence the two layers, and hence why editing the report with Power BI Desktop open is dangerous: Desktop has its own copy in memory and overwrites the disk on save. Detecting and blocking this is Phase 1 work.
