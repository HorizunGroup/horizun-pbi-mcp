# Phase 1A — critical risk containment

_Implemented on top of the Phase 0 commit `82bc6c9`. No changes to the contract of the 34 tools._

This phase adds no functionality. It closes five risks that allowed writing outside the project, overwriting concurrent changes, leaving a `.pbip` half-done, or running arbitrary DAX.

---

## 1. New modules

| Module | Single responsibility |
|---|---|
| `src/services/paths.py` | No read/write path leaves the active project |
| `src/services/dax_guard.py` | Only queries recognized as read-only are run |
| `src/services/project_state.py` | No PBIR write happens if Desktop might have the project open |
| `src/services/txn.py` | Compensated transaction: journal, verification and rollback |

---

## 2. Paths (`paths.py`)

Two problems, two functions:

- **`safe_identifier()`** — a page or visual id is an *identifier*, not a path. It rejects, **before touching disk**: separators, `.`/`..`, absolute paths, drive syntax (`C:\x` and `C:x`), UNC (`\\srv\r`), extended (`\\?\`), device (`\\.\`), NTFS ADS (`file.json:stream`), reserved names (`CON`, `NUL`, `AUX`, `COM1`…), empty components and components with a trailing dot or space.
- **`assert_not_path_syntax()`** — more permissive, for display names: allows `"Executive Summary 2026"` but rejects any path syntax.
- **`ensure_contained()`** — resolves links (junctions and reparse points) on both ends and compares with `os.path.normcase`, because NTFS is case-insensitive. Detects a drive change.
- **`assert_still_contained()`** — the same check, under its own name, to be called **right before writing**: a junction can change target between validation and write.

> `Path('base') / 'C:/other'` returns `C:/other`. That's why each component must be validated before joining, not just the normalized resulting string.

---

## 3. DAX classifier (`dax_guard.py`)

**It's not a DAX parser.** It's a deliberately narrow lexical classifier, `fail-closed`.

**Step 1 — lexical scan.** The text is scanned recognizing comments (`//`, `--`, non-nesting `/* */`), strings (`"…"`, escape `""`), quoted identifiers (`'…'`, escape `''`) and bracketed ones (`[…]`, escape `]]`). Their content is replaced with an opaque sentinel. An unclosed delimiter → rejection.

**Step 2 — classification**, only on the residue. The query is allowed if its **entire structure** fits a recognized shape:

| Shape | Example |
|---|---|
| `evaluate` | `EVALUATE TOPN(10, Sales)` |
| `define_evaluate` | `DEFINE MEASURE T[M] = 1 EVALUATE ROW("v", T[M])` |
| `dmv_select` | `SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES` |

Rejected: XMLA (`<`), DDL keywords as a standalone token, `;` (could chain statements), `DEFINE` without `EVALUATE`, `SELECT` whose `FROM` isn't exactly `$SYSTEM.<rowset>`, `EVALUATE`+`SELECT` mixes, concatenated tokens (`EVALUATEX`), and **everything ambiguous**.

Since literals are neutralized first, `EVALUATE ROW("DROP TABLE", 1)` is still read-only, and `SELECTCOLUMNS` isn't confused with `SELECT`.

**No escape hatch.** There's no environment variable that relaxes the policy; there's a test that verifies it.

---

## 4. Open project (`project_state.py`)

**Honest limit:** this **doesn't prevent** Power BI Desktop from overwriting the report afterward. Desktop has its own in-memory copy and overwrites on save. All this achieves is not writing *ourselves* when there are signs it's open. The error message says so explicitly.

**Signals, all read-only.** Nothing is renamed, no temp file is written, no test `os.replace` is attempted on real files:

| Situation | State |
|---|---|
| Neither `PBIDesktop.exe` nor `msmdsrv.exe` | `closed` (high) |
| `msmdsrv` with no attributable `PBIDesktop` | `unknown` |
| `PBIDesktop` with the project in `cmdline()` or `open_files()` | `open` (high) |
| `PBIDesktop` present but the system denies inspection | `unknown` |
| `PBIDesktop` inspectable and none reference the project | `closed` (medium) |

**Policy (strict, cannot be disabled):** only `closed` allows writing. `open` and `unknown` block. `warn` mode and per-call confirmation are left for 1B.

There's a **1-second** cache over the process scan: enumerating processes costs ~150 ms and a page with five visuals would pay for five scans. The window is minimal and the transaction re-validates each file's fingerprint anyway.

---

## 5. Compensated transaction (`txn.py`)

The file system **offers no multi-file atomicity**. This is a *compensated* transaction: between the first and last `os.replace` there's a window in which the project is half-done. What's guaranteed is that the window is short, that the journal allows going back, and that **success is never reported if the rollback wasn't clean**.

```
PLAN      fingerprint (sha256 + size, or "absent") of each target
SNAPSHOT  copy the targets to the journal + manifest (status: open)
PRE-CHECK re-verify the fingerprint right before each replacement
WRITE     temp file → flush → fsync → validate → os.replace → clean up in finally
POST      re-read from disk and compare against what was meant to be written
COMMIT    close the manifest   |   FAILURE → ROLLBACK
```

The fingerprint is checked **three times**. Timestamps are never used. `absent` is a first-class state: if a file we planned to create appeared in the meantime, it's a collision.

### Concurrency-aware rollback

A file is only touched if its current content **still matches what we wrote**:

| Situation | Result |
|---|---|
| Pre-existing, no external change | `restored` (byte for byte, verified) |
| Created by the transaction, no external change | `restored` (removed) |
| Changed after our write | `rollback_conflict` — **not touched** |
| Never actually written | `unchanged` |
| Restore attempted and failed | `rollback_failed` |

If any file ends up in `rollback_conflict` or `rollback_failed`, the propagated error is `RollbackIncompleteError`, with the journal and per-file detail — not a normal failure.

### Backup destination

`resolve_backup_root()` **fails actionably before touching the project** if:

- there's no `PBI_MCP_BACKUPS_DIR` configured (no default destination is silently chosen);
- the destination falls inside the `.pbip`, the `.Report` or the `.SemanticModel`;
- the project is inside the backups folder (recursion);
- the destination isn't writable.

Each project uses a `<name>_<hash12>` subdirectory where the hash is `sha256` of its normalized absolute path: **two `Demo.pbip` in different folders never share backups**.

**No automatic purge in 1A**, and pre-existing user backups are never deleted.

---

## 6. Sessions (`desktop_discovery.py`, `config.py`)

That the port is open again proves nothing: Desktop assigns a new port on every startup and the system reuses ports and PIDs.

`ActiveModel` now stores the session's **identity**: `pid`, `process_started`, `workspace` and `session_fingerprint` (hash of port + pid + start time + catalog).

`verify_model()` returns:

| State | When |
|---|---|
| `ok` | everything matches |
| `stale` | the port no longer exists or doesn't respond |
| `mismatch` | the port is held by another process, the PID was reused, or it serves another catalog |

`require_active_model()` checks this and raises `StaleSessionError` with an actionable message. A session reloaded from `session.json` starts **unverified**: what's saved is not trusted.

Discovery also does a **0.5s TCP pre-check** before attempting ADOMD. Without it, orphaned `msmdsrv.port.txt` files (there were **5** on this machine) triggered a full connection with a long timeout against each dead port. **None are deleted**: they're just marked `unreachable`.

---

## 7. Durability and leftovers

`durable_write()` centralizes writing: temp file in the same directory → `flush` → `fsync` → validate the temp file → `os.replace` → **clean up the temp file in `finally`**.

This fixes a real bug: on Windows, `os.replace` fails with `WinError 5` if another process has the destination open —exactly the open-Desktop scenario— and it used to leave an orphaned `visual.json.tmp` **inside the user's `.pbip`**. The original was always left intact; the problem was the garbage.

No `fsync` on the directory is done: Windows doesn't support it.

---

## 7 bis. Bulk composition (Phase 1A.1)

Phase 1A left the individual writers safe, but flows writing **several** files still chained N single-file transactions. A page with 5 visuals that failed on the 3rd left 2 written and a half-done page.

### Bulk API, at the PBIR layer

No tool coordinates temp files, journals or rollback: that responsibility lives in `pbip/pbir_writer.py` and `services/txn.py`.

| Function | Files it covers |
|---|---|
| `create_page_with_visuals(...)` | `page.json` + `pages.json` + N × `visual.json` |
| `update_visuals_bulk(...)` | N × `visual.json` |
| `write_visual_with_registration(...)` | `report.json` + `visual.json` |

All receive content **already validated and built**, and run in **a single transaction**. The individual APIs (`write_visual`, `update_visual_position`, `create_page`, `add_public_custom_visual`) still exist for single-object tools.

### Mandatory order in the flows

```
1. validate the full spec
2. resolve FINAL positions
3. build ALL visuals in memory   ← fails here if something can't be assembled
4. compute all target files
5. open ONE transaction
6. write the set
7. verify
8. full rollback on any failure
```

Step 3 is what guarantees that **a failure building visual N produces no write at all**: the page never gets created. And in `pbi_generate_report_page` the visuals are built already with their final position, instead of being written with a provisional one to be repositioned later.

### Real atomicity limits

- **There's no file-system atomicity.** Between the first and last `os.replace` the project is half-done. What exists is journal-based compensation.
- One logical operation produces **a single journal**, not N full backups. The journal knows **all** affected files.
- The rollback covers **modified, created and deleted** files, and also removes the **directories** the transaction created that ended up empty (previously it left an orphaned `<pageId>/` without `page.json`, which the page reader itself misinterpreted).

### Behavior on conflict

If someone externally modifies a file:

| Moment | Result |
|---|---|
| Before we write it | The transaction **aborts** at the pre-check; the external change is preserved |
| After writing it, before verifying | Post-verification detects it; the file is marked `rollback_conflict` |
| During rollback | **Not overwritten**; marked `rollback_conflict` and the journal is kept |

In all those cases the operation ends in `RollbackIncompleteError`, **never in success**.

### Packaging

`pyproject.toml` omitted `services*` from `packages.find.include` **and** `reporting` from `py-modules`. A `pip install -e .` didn't reveal it, because it resolves everything from `src/`. The `tests/test_packaging.py` test builds a real wheel, installs it in a venv and verifies startup with 34 tools outside the repository.

---

## 8. Risk status

| Id | Risk | Status |
|---|---|---|
| R2 | Corrupting a `.pbip`: no lock, no `expected_state`, no Desktop detection | **Closed** in 1A |
| R3 | PBIR write traversal | **Closed** in 1A |
| R5 | Backups without retention or validated location | **Partially closed**: validated location and manifest; purge is left out on purpose |
| R6 | `session.json` pointing to a dead port | **Closed** in 1A |
| R7 | `pbi_run_dax` without read-only validation | **Closed** in 1A |
| R11 | `.tmp` leftover inside the `.pbip` | **Closed** in 1A |
| R12 | Empty page directory after a rollback | **Closed** in 1A.1 |
| R13 | Multi-file PBIR flow atomicity | **Partially closed**: the 5 PBIR flows are single transactions; `pbi_hide_columns` (TMDL) is still pending |
| R14 | Incomplete packaging | **Closed** in 1A.1, with an installed-wheel test |

## 7 ter. `pbi_hide_columns` (Phase 1A.2)

It was the last non-atomic multi-file flow, and had a more serious defect than the transaction count: **it called another tool decorated with `guard()`**. Errors turned into data, the loop kept going, and the batch returned `ok:true` with the failures buried in `results`.

### Fix

- **`hide_columns_service()`**, undecorated, in `tools/model_edit_tools.py`. Tools wrap services; never other tools.
- **Full validation before writing**: `columns` type, each `table` and `column` non-empty, valid names, duplicates detected. A failure indicates **index, table and column**, and nothing is written, `SaveChanges` is never called and no journal is created.
- **TMDL batch**: each `.tmdl` is located and read **once**, changes are grouped by file, mutated in memory and written in **a single transaction**.
- **TOM batch**: one connection, validation of every table and column, capture of `before_hidden`, and **a single `SaveChanges`** regardless of N.
- **Exact duplicates**: applied once, but reported at every position. The operation is idempotent.
- **Empty list**: previous behavior preserved (not an error).

### Semantics of `count`

`count` is still the **number of entries requested**, duplicates included — same as before, when it was `len(results)` with one result per iteration. `results` keeps one entry per request, in the same order, even though it's internally grouped by file. Discarded duplicates go in `duplicates_ignored`, a field that's **added**, not a change of meaning.

### What TOM guarantees, and what it doesn't

`SaveChanges()` sends the batch in a single operation, but **it's not a distributed transaction**. If the engine rejects the batch, the in-memory objects may stay modified until Power BI Desktop reloads. What is guaranteed: no partial writes on our part, and a failed validation persists nothing (`SaveChanges` is called **zero** times).

### `mode="both"`: compensated

```
1. validate BOTH destinations         ← if the live side doesn't validate, disk isn't touched
2. write the TMDL batch (restorable journal)
3. apply the batch live (1 SaveChanges)
4. on failure → compensate disk from the journal
5. verify the compensation
6. report the conflict without hiding it
```

One detail that was costly to find: `SaveChanges` can throw a **raw .NET exception**. If it escaped unwrapped, compensation didn't run and the disk stayed modified with the live model intact. Now it's wrapped as `live_write_failed`, and the coordinator catches `Exception`, not just `PowerBIMCPError`.

A total failure reaches the outer `guard()` as a domain exception (`bulk_partially_applied`), not as a list of successes and errors.

---

## 7 quater. `mode="both"` blocked (Phase 1A.3)

When testing the public flow, a contradiction appeared that tests with doubles didn't reveal:

```
live → needs Power BI Desktop OPEN   (TOM talks to msmdsrv.exe)
pbip → needs Desktop CLOSED          (strict 1A policy)
```

**There is no system state in which both can be safely written in a single call.** And since the dual implementation applied `live` first and `pbip` after, with Desktop open the result was a **deterministic partial state**. Verified on the code at `7adb725`:

```
_dual result:        live applied: True | pbip applied: False
                     pbip_error: project_open_in_desktop | consistent: False
real effects:        SaveChanges: 1 | column hidden in TOM: True | TMDL: unchanged
```

### Real mode matrix

| Mode | Requirement | Status |
|---|---|---|
| `live` | Desktop open and valid session | **Available** |
| `pbip` | Project closed or verifiably safe | **Available** |
| `both` | Mutually incompatible requirements | **Blocked in 1A** |

### Central precondition

`services/dual_mode.py` exposes `assert_mode_is_safely_executable(mode)`, run **first** in every dual tool: before opening a TOM connection, validating objects against the engine, creating a journal, reading to plan, or touching a file. The decision lives in one place; no tool duplicates it.

It also centralizes `normalize_mode()` and `run_dual()`, which were duplicated in `measure_tools` and `model_edit_tools` (audit debt A2). `run_dual` no longer runs both sides isolating errors: it propagates the exception, instead of turning it into a `consistent: False` with half the work done.

**No environment-variable bypass.**

### The compensated coordinator, as an internal mechanism

`_apply_both_compensated()` is kept and tested directly, but **it's not reachable from the public tool** and doesn't justify accepting `both`. Phase 1B will decide between a two-stage workflow, persisting only via TOM, abandoning `both`, or another coordination.

### Corrected error taxonomy

| Code | When | Intervention |
|---|---|---|
| `bulk_apply_failed` | It failed and compensation left **everything** as it was (`applied_to: "none"`) | No |
| `bulk_partially_applied` | Compensation incomplete or in conflict | **Yes** |

Previously, a clean compensation ended up as a `BulkPartialError` with `applied_to: "none"` — semantically contradictory: it prompted a manual search for something that didn't exist.

---

### Residual risks

1. **`mode="both"` is blocked, not solved.** A system limit, not a code one. It's risk **R15**, and it remains **open**: today there's no way to apply a change to both destinations in a single operation. The user must choose `live` (and save with Ctrl+S) or `pbip` (with Desktop closed).
2. **The window between the first and last `os.replace`** isn't atomic at the file-system level. If the process dies there, the journal allows **manual** recovery; there's no automatic resume on startup.
3. **The 1s cache** of project state: to slip through, Desktop would have to open the project within that window.
4. **No journal purging.** They pile up in the backups folder until a policy is defined.
5. **`backup_before_edit`** still exists for routes that haven't migrated to a transaction. It doesn't know how to restore.

---

## 9. Pending for 1B

- Uniform envelope (`status/target/before/after/validation/backup/warnings`).
- `request_id` and `dry_run` exposed as parameters.
- `expected_state` supplied by the client (concurrency **between** calls; in 1A it's internal to each operation).
- `warn` mode and per-call confirmation for the open-project policy.
- Enums in `mode`, `source`, `layout`, `direction`, `type`, `scope` — **with the `RESTRICTED CONTRACT` category** in the comparator: narrowing `string`→`enum` is not compatible by default.
- General structured logging.
- A single transaction for the batch operations in `page_builder.py` and `visual_tools.py` (today they do N single-file transactions).
