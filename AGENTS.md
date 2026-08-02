# Repository rules for agents

Operating instructions for any agent (or person) modifying Horizun PBI MCP.
They take priority over any general convention.

---

## 1. Before touching anything

```bash
python -m pytest -q                    # must pass green
python scripts/doctor.py               # must exit with code 0
```

If the baseline is already broken, **fix it or report it first**. Don't build on red.

---

## 2. The MCP contract is untouchable

The **34** baseline tools are frozen in `tests/golden/tools_v1.json`.

**Forbidden without explicit approval:**
- removing a tool
- renaming a tool
- removing a parameter
- adding a **required** parameter
- changing a parameter's type
- changing a default value
- changing the response shape

**Allowed:**
- adding new tools
- adding **optional parameters with a default**
- adding new fields to the response dict
- improving descriptions

Check at any time:

```bash
python -m tests.contract_utils
```

Returns 0 if there are no breaks, 1 if there are, with a report stating **what** changed and **whether it breaks compatibility** — not a dump of two JSONs.

After a deliberate, approved change:

```bash
python -m tests.contract_utils --write
```

---

## 3. Invariants no phase can break

1. **stdout is the JSON-RPC channel.** All logging goes to stderr or a file. A debug `print()` breaks the client connection.
2. **Never overwrite JSON that doesn't parse.** If it can't be read, abort.
3. **Every write to the user's project:** backup before, re-read after.
4. **No write path leaves the active project.** Use `ensure_within_base()`.
5. **No fields are invented** that don't exist in the model. If a field doesn't exist, report it; don't guess.
6. **Destructive tools require `confirm=true`.**
7. **Prefer cloning a real template** over hand-building visual JSON.

---

## 4. Real data: never enters git

| Never version | Do version |
|---|---|
| Real `.pbix`, `.pbip`, `.Report/`, `.SemanticModel/` | `tests/fixtures/synthetic/**` |
| `libs/` (DLLs) | `scripts/fetch_libs.py` |
| `outputs/`, `backups/`, `*.log` | `*.example.*` templates |
| `.env`, `.mcp.json`, credentials | `.env.example`, `.mcp.json.example` |
| `tests/fixtures/local/` | `docs/`, `tests/` |

Before any commit:

```bash
git status --short --ignored
```

Synthetic fixtures **contain no** commercial names, data or information from any real project. If you need real PBIR structure, use the ignored local fixture (`scripts/setup_local_fixture.py`) and **never promote it to `synthetic/` without anonymizing it and without review**.

---

## 5. Tests

| Level | Where | Rule |
|---|---|---|
| Unit | `tests/test_*.py` | No real I/O outside `tmp_path` |
| Synthetic fixtures | `tests/fixtures/synthetic/` | Use `materialize(tmp_path)`. **Never** write over the versioned fixture |
| MCP contract | `tests/test_tool_contract.py` | Must always pass |
| Live | marked `@pytest.mark.skip` or `live` | Not run on their own. Never destructive against a real model |
| Local fixture | marked `local_fixture` | Read-only. Skipped if the folder doesn't exist |

**Path traversal tests:** the "outside" must be created **inside pytest's `tmp_path`** (`synthetic.outside_marker_dir()`). Never point at a real machine path, not even to demonstrate a failure.

---

## 6. Git and contributions

This is the **public** repository. Contributions go through **branches and pull requests**.

- **Never `force-push` to `main`.** Rewriting published history breaks any clone and any reference to a commit.
- One branch per change, with a name that says what it does.
- **Before opening a PR**, all three green:

  ```bash
  python -m pytest -q
  python scripts/doctor.py
  python -m tests.contract_utils
  ```

  CI repeats them on `windows-latest` with Python 3.10 and 3.13. A red PR is not reviewed.

- **The MCP contract is frozen.** See section 2: adding is free, changing or removing is not.
- **Real data is never versioned**: no one's `.pbix`, no one's `.pbip`, no DLLs, no `outputs/`, no `backups/`, no `.env`, no `.mcp.json`. See section 4.
- One commit per logical change. The message explains **what was wrong**, not just what was touched.
- Nothing is published to PyPI from this repository.

Extended contribution guide: [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## 7. Risk status

Three states, and only three: **closed**, **partially closed**, **open**.
Detail in `docs/PHASE_1A_DESIGN.md`.

| Id | Risk | Status |
|---|---|---|
| R2 | Corrupting a `.pbip`: no Desktop detection, no post-write verification | **Closed** (1A) |
| R3 | Write traversal in PBIR | **Closed** (1A) |
| R5 | Backups without validated location or retention | **Closed** (F/R5) — validated location, hash identification, manifest, and purge via `pbi_purge_backups`: dry-run by default, validated root, only recognizable journals, symlinks not followed, and the most recent one plus all pending ones are always kept |
| R6 | `session.json` pointing to a dead or reused port | **Closed** (1A) |
| R7 | `pbi_run_dax` without read-only validation | **Closed** (1A) |
| R11 | `.tmp` leftover inside the `.pbip` when `os.replace` fails | **Closed** (1A) |
| R12 | Rollback left empty, orphaned page directories | **Closed** (1A.1) |
| R13 | **Multi-file PBIR flow atomicity** | **Partially closed** — see below |
| R14 | Incomplete packaging (`services*`, `reporting`) | **Closed** (1A.1), with an installed-wheel test |

### R13 — multi-file atomicity, in detail

**Don't mark this risk closed while a single flow remains uncovered.**

| Flow | Files | Status |
|---|---|---|
| `pbi_create_page_from_spec` | page.json + pages.json + N visual.json | ✅ one transaction |
| `pbi_arrange_visuals` | N visual.json | ✅ one transaction |
| `pbi_generate_report_page` | page.json + pages.json + N visual.json | ✅ one transaction |
| `pbi_create_html_visual` | report.json + visual.json | ✅ one transaction |
| `pbir_writer.create_page` | page.json + pages.json | ✅ one transaction |
| `pbi_hide_columns` (`pbip`) | N TMDL files | ✅ one transaction (1A.2) |
| `pbi_hide_columns` (`live`) | N TOM columns | ✅ a single `SaveChanges` (1A.2) |

#### Inventory expansion (Phase D) — flows that were missing

The inventory above was built with **lexical** searches (`grep` for `project_transaction` inside a `for`). That method has a blind spot: **the transaction opens inside the called function, not inside the loop**. Two high-level workflows fell right into that gap and didn't show up:

| Flow | What it did | Evidence |
|---|---|---|
| `pbi_repair_broken_references` | One transaction **per visual**, inside a `for`, with `except Exception` that **kept going**. If the fifth one failed, the previous four stayed committed and the tool returned `ok:true` with a list of failures. | `workflows.py:222` (before) |
| `pbi_normalize_report` | One transaction **per page**. Atomic within each one, but if the third failed, the first two were left rearranged. | `workflows.py:273` (before) |

And a third defect, at the **commit** boundary:

| Flow | What it did |
|---|---|
| `txn._ProjectTransactionCM.__exit__` | Called `commit()` unprotected. If the commit itself failed (manifest, disk, permissions), the exception escaped **without rolling back**: the files stayed written and the operation looked failed. |

**Fixed in Phase D**: `pbir_writer.plan_visuals_bulk()` and `pbir_edit.plan_replace_visual_field()` were factored out (pure, no writes), the two workflows now compile everything and write in **one** transaction, and `__exit__` rolls back if the commit fails.

`tests/test_workflow_atomicity.py` injects failures on first / middle / last write, prior validation, commit and compensation, and requires byte-for-byte restoration and zero orphaned directories. **Six of those tests fail against the previous commit.**

It also includes two static checks: a lexical one (transaction inside a `for`) and **one that covers the blind spot**: a loop that calls a function that opens its own transaction.

**Also verified**: exactly **1 `SaveChanges` per function**, 7 in total, counting AST `Call` nodes and not text occurrences — a `grep` counts 6 in `set_columns_hidden_bulk` because the docstring mentions it. The original R13 claim holds.

**R13 (single-destination atomicity): closed.** All flows targeting the same destination are single transactions or a single `SaveChanges`.

---

## 7 bis. R15 — dual consistency: **OPEN**

This risk **is not closed and will not be closed in Phase 1A.** It's a system limit.

| Mode | Requirement | Status |
|---|---|---|
| `live` | Power BI Desktop **open** and valid session | ✅ Available |
| `pbip` | Project **closed** or verifiably safe | ✅ Available |
| `both` | **Mutually incompatible requirements** | 🚫 **Blocked in 1A** |

`live` talks to `msmdsrv.exe`, which only exists if Desktop is open. `pbip` writes files that Desktop overwrites on save, so the strict policy blocks it if Desktop is `open` or `unknown`. **There is no system state in which both destinations can be safely written in a single call.**

What it used to do: apply `live` first, then `pbip`. With Desktop open — the only state in which `live` is possible — the result was a **deterministic partial state**: 1 `SaveChanges` executed, column hidden in memory, disk untouched, `consistent: False`.

**Now:** the six dual tools reject `mode="both"` with `dual_mode_not_safely_available` **before any effect** — before connecting to TOM, validating against the engine, creating a journal, reading to plan, or touching a file. No environment-variable bypass.

The six: `pbi_create_measure`, `pbi_update_measure`, `pbi_delete_measure`, `pbi_set_column_visibility`, `pbi_hide_columns`, `pbi_set_relationship_direction`.

### The compensated coordinator is still there, as an internal mechanism

`_apply_both_compensated()` in `tools/model_edit_tools.py` implements disk→memory with compensation. **It's not reachable from the public tool** and doesn't justify accepting `both`. It's kept with direct unit tests because Phase 1B will have to decide between: a two-stage workflow, persisting only via TOM and letting the user save, abandoning `both`, or another safe coordination.

### Coordinator error taxonomy

| Code | When | Requires intervention |
|---|---|---|
| `bulk_apply_failed` | It failed and compensation left **everything** as it was | No |
| `bulk_partially_applied` | Compensation was left incomplete or in conflict | **Yes**, with the journal |

"Partial" is never reported when the restoration was complete.

### How to audit this yourself

Don't trust the list above. The five patterns to search for:

```bash
# 1. A decorated tool calling another decorated tool
grep -rn "@mcp.tool" -A40 src/tools/ | grep -E "pbi_[a-z_]+\("
# 2. Writes inside loops
grep -rnE "for |while " -A6 src/ | grep -E "write_|SaveChanges|create_page"
# 3. Transactions opened inside loops  ← the bad pattern
grep -rn "project_transaction\|with transaction" src/
# 4. Multiple SaveChanges in the same function
grep -rn "SaveChanges" src/powerbi/
# 5. backup_before_edit (doesn't restore; only for unmigrated paths)
grep -rn "backup_before_edit" src/
```

A `for` **inside** a transaction is correct. A transaction **inside** a `for` is not.

**Never make a tool call another decorated tool.** `guard()` turns errors into data: the loop continues, the outer result says `ok:true` and the failures stay buried in the list. Extract an undecorated service and have both tools wrap it.

Don't mark any risk as closed without a regression test that **fails before** the fix and passes after.
