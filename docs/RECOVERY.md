# Recovery guide

What to do when something is left half-done. **Nothing below deletes data**: every operation leaves the original in a journal.

---

## 1. First thing: look, don't touch

```bash
python scripts/doctor.py
```

From an MCP client:

```
pbi_health_check          → are there pending journals?
pbi_list_pending_journals → which ones, and from what operation
pbi_inspect_journal       → which files it touches and whether they still match the original
```

`pbi_inspect_journal` is **read-only**. It restores nothing; it tells you whether restoring is needed.

---

## 2. How to read a journal

Each journal is a folder in the project's backup root:

```
<backups>/<name>_<hash12>/<date>_<request_id>/
    manifest.json     which operation, when, which files, with their sha256
    files/            copy of the original of each touched file
```

`manifest.json` → `status`:

| State | Means | Action |
|---|---|---|
| `committed` | Finished fine | None |
| `rolled_back` | Failed and was reverted | None, unless there are conflicts |
| `compensated` | Something already committed was undone | None |
| `open` | **The process died halfway through** | Review |
| `unreadable` | The manifest can't be read | Review by hand |

And per file, `outcome`:

| Outcome | Means |
|---|---|
| `restored` | Returned to its original state |
| `unchanged` | It was never actually written |
| `rollback_conflict` | **Changed externally afterward**; not touched, on purpose |
| `rollback_failed` | Restore was attempted and failed |

---

## 3. Journal `open`: manual recovery

Means the process died between writing and closing. The original is in `files/`.

1. Close Power BI Desktop.
2. `pbi_inspect_journal` on that journal. Check each file's `matches_original`:
   - `true` → that file is already back to how it started.
   - `false` → it has our half-done write, or an external change.
3. Copy from `files/<relative path>` over `<project>/<relative path>`.
4. Inspect again: `matches_original` must be `true` for all of them.

**There's no automatic restoration on startup**, on purpose: resuming an operation the user may have already undone by hand could be worse than leaving it alone.

---

## 4. `rollback_conflict`: not a failure

Someone modified the file **after** we wrote it. The rollback respected that instead of overwriting it.

Your call:

- **Keep the external change** → nothing to do.
- **Go back to the original** → copy it from `files/`.

---

## 5. Specific situations

| Symptom | What happened | Solution |
|---|---|---|
| `project_open_in_desktop` | Desktop has the project open, or it couldn't be ruled out | Close it completely and retry. It's intentional |
| `stale_session` | The port changed or another process holds it | `pbi_list_desktop_models` and `pbi_select_model` |
| `plan_token_stale` | The project changed since the plan was computed | Regenerate the plan |
| `request_id_conflict` | Same `request_id`, different arguments | Use a new one |
| `dual_mode_not_safely_available` | `mode="both"` | Choose `live` or `pbip` |
| `rollback_incomplete` | The reversion wasn't clean | Follow §3 with the error's journal |
| `bulk_partially_applied` | Disk was written and the live side failed, without being able to compensate | `details.journal` has the originals |
| `.tmp` inside the `.pbip` | Shouldn't happen since 1A | It's garbage: delete it. The original is intact |
| Changes not showing in Desktop | PBIR loads on open | Close and reopen the report |
| Lost model changes | With `mode="live"` they don't persist without Ctrl+S | Reapply them and save |

---

## 6. Returning to a known state

```
pbi_backup_pbip_project(mode="folder", scope="both")
```

Creates a full copy with a hash manifest. To restore it, close Desktop and copy the folder back.

Backups and journals are **never purged automatically**, and any you already had in your project are left untouched.

---

## 7. What never happens

- Nothing is written outside the active project.
- No PBIR write happens if Desktop might have the project open.
- JSON that doesn't parse is never overwritten.
- An external change is never overwritten during a rollback.
- Success is never reported if the reversion wasn't clean.
