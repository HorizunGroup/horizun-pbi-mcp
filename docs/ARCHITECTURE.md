# Horizun PBI MCP architecture

_Verified for 2.0.2. Volatile counts are derived from the frozen MCP contract
and the code instead of being copied into this document._

---

## 1. Runtime shape

```text
Codex / Claude / another MCP client
                |
                | JSON-RPC over stdio (stdout is protocol-only)
                v
src/horizun_pbi_mcp/server.py
                |
                | registers the tool modules in _TOOL_MODULES
                v
src/horizun_pbi_mcp/tools/
  signatures, input validation, confirmation and response envelopes
                |
                v
src/horizun_pbi_mcp/services/
  workflows, planning, transactions, locks, idempotency, recovery,
  report/model validation, exports and installation-independent policy
                |
          +-----+-----+
          |           |
          v           v
powerbi/ (LIVE)     pbip/ (ON DISK)
ADOMD.NET / TOM     TMDL / PBIR / resources
Power BI Desktop    project files
```

The public surface is frozen in `tests/golden/tools_v1.json`: 134 tools in
2.0.1. Runtime diagnostics derive that count from the contract and verify the
registered set against it. A documentation regression test forces this page to
be updated when the golden changes.

## 2. Layers and responsibilities

| Layer | Responsibility | Must not do |
|---|---|---|
| `tools/` | MCP signatures, defaults, risk annotations, confirmation and serialization | Hide failures from another decorated tool |
| `services/` | Compile complete operations, enforce policy and coordinate multi-file effects | Open one transaction per item in a batch |
| `powerbi/` | Discover Desktop, run read-only DAX and apply supported TOM operations | Write report pages or PBIR visuals |
| `pbip/` | Read and write TMDL/PBIR, build visuals and project resources | Write outside the active project |
| `lifecycle/` | Validate, promote and recover isolated plugin runtimes | Replace the last known-good runtime before validation |
| `utils/` | Durable JSON/file primitives and shared validation | Print diagnostics to stdout |

The service layer is intentional. A tool must never call another decorated
tool: decorators convert exceptions into MCP data, which can make a caller
continue after a nested failure. Shared behavior belongs in an undecorated
service used by both tools.

## 3. The two Power BI destinations

### LIVE

`powerbi/` talks to the local Analysis Services engine owned by Power BI
Desktop. It exposes the semantic model: tables, columns, measures,
relationships, DAX and refresh. It does not expose report pages or visual
layout.

### ON DISK

`pbip/` operates on `.pbip`, TMDL and PBIR files. It owns pages, visuals,
themes, bookmarks and durable model edits. Project writes require Desktop to
be closed or proven safe because Desktop can overwrite files from its in-memory
copy when it saves.

### Why `mode="both"` is blocked

LIVE requires Desktop open; a safe PBIP write requires it closed. There is no
single state in which both preconditions hold. Public dual-mode tools reject
`both` before the first effect. `mode="auto"` selects one real destination; it
never pretends to update two.

## 4. Mutation protocol

Every project mutation follows this shape:

```text
validate request and destination
        -> resolve/plan every target in memory
        -> acquire project lock
        -> create journal and backups
        -> write the whole destination set
        -> re-read and validate
        -> commit journal
        -> rollback on write, validation or commit failure
```

The implementation is centered on `services/txn.py`. Important guarantees:

- paths are resolved under the active project before writing;
- corrupt JSON is never overwritten;
- multi-file flows use one transaction per destination;
- commit failure also triggers rollback;
- destructive operations require explicit confirmation;
- `request_id` provides persistent idempotency when the client supplies it;
- pending journals are inspectable and recoverable;
- the newest and pending backups survive retention cleanup.

TOM batches use one `SaveChanges`. The public `both` coordinator remains
blocked even though its compensated internal mechanism has direct tests.

## 5. Validation boundaries

PBIR validation has three levels:

1. Official published JSON schemas, pinned and verified by SHA-256.
2. Internal structural checks for areas where Microsoft's schemas are loose.
3. The optional official Power BI report-authoring CLI for cross-file and
   effective-format checks.

Three schema versions referenced by Power BI are not published upstream. The
server reports that limitation instead of inventing schemas or claiming full
coverage.

The final visual-semantic oracle still has a human boundary: a valid report
can render poorly. Desktop capture is automated and process-safe, but judging
composition across every page remains a product limitation documented in
`BACKLOG.md`.

## 6. Plugin lifecycle

The Codex and Claude plugins launch `scripts/launch.cmd`, which finds a real
Python interpreter and delegates to `plugin_launcher.py`. A versioned runtime
is built in staging and promoted only after a real MCP handshake proves the
server identity, version and frozen tool contract.

If an update fails, the previous verified runtime is preserved and can be
served as last-known-good. Runtime caches are versioned; user outputs and
backups live outside them and survive updates or ordinary uninstall.

## 7. Invariants

1. stdout is the JSON-RPC channel; logs go to stderr or a file.
2. Invalid JSON is reported and never overwritten.
3. Every project write is backed up, re-read and validated.
4. No write path leaves the active project or validated output root.
5. Fields absent from the model are reported, never invented.
6. Destructive tools require `confirm=true` or the stronger approved-plan
   token where the frozen contract explicitly defines it.
7. A batch is one transaction or one `SaveChanges` per destination.
8. A decorated MCP tool never calls another decorated MCP tool.
9. Real `.pbix`, `.pbip`, credentials, DLLs and customer data never enter Git.
10. The frozen contract cannot change incompatibly without explicit approval.

## 8. Deliberate limits

- Power BI Desktop and the LIVE layer require Windows.
- A remote MCP cannot control a local Desktop or local PBIP files.
- Two different MCP products do not share locks; only one writer product
  should operate on a project at a time.
- `mode="both"` is unavailable until a safe two-stage product workflow exists.
- Full visual correctness still needs rendered inspection.
- Reproducible dependency locks exist only for interpreter/platform pairs
  actually generated and installed under their own interpreter.
