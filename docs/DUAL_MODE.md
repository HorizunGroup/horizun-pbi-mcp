# `mode="both"` — why it's blocked (R15)

**Status: OPEN by design. `both` has not been enabled in any release (v1.0.0 onward).**

This document explains why, what's kept in the meantime, and how it would have to be designed if it's ever implemented. **It doesn't describe anything that exists today.**

---

## The problem, in one sentence

`live` and `pbip` write to **two different resources** that don't share a transaction, and their preconditions are **mutually exclusive**.

| Mode | Writes to | Requires |
|---|---|---|
| `live` | `msmdsrv.exe`'s in-memory model | Power BI Desktop **open** |
| `pbip` | TMDL/PBIR files on disk | Power BI Desktop **closed** |

`live` is only possible if Desktop is open, because the engine only exists while it is. `pbip` writes files that Desktop overwrites on save, so the strict policy blocks it when Desktop is `open` or `unknown`.

**There is no system state in which both destinations can be safely written in a single call.**

## What used to happen

`both` applied `live` first and `pbip` after. With Desktop open —the only state in which `live` works— the result was a **deterministic partial state**: one `SaveChanges` executed, the change live in memory, the disk untouched, and `consistent: False` in the response.

Measured, not assumed: the column ended up hidden in the in-memory model and the TMDL untouched.

## What it does today

The six dual tools reject `mode="both"` with `dual_mode_not_safely_available` **before any effect**: before connecting to TOM, validating against the engine, reading to plan, creating a journal, making a backup, or touching a file.

`pbi_create_measure` · `pbi_update_measure` · `pbi_delete_measure` · `pbi_set_column_visibility` · `pbi_hide_columns` · `pbi_set_relationship_direction`

No environment-variable bypass. `tests/test_dual_mode_guard.py` verifies this tool by tool, checking there was no connection, no `SaveChanges`, no write, no change-log entry, no journal, and that the project's fingerprint didn't change.

`_apply_both_compensated()` remains in `tools/model_edit_tools.py` as an **internal mechanism**, with direct unit tests. It's not reachable from the public tool and doesn't justify accepting `both`.

---

## Future design: two-stage saga — NOT IMPLEMENTED

If this is revisited, `both` **cannot** be presented as a transaction. There's no distributed transaction between Analysis Services and the file system, and faking one would misrepresent the guarantee.

The honest form is a **saga**: two stages with explicit compensation, and a result stating whether the compensation was complete.

```
1. live preflight       is there a session? does the object exist? is the change valid?
2. PBIR preflight       is Desktop closed? supported version? valid schema?
3. snapshot             prior state of BOTH destinations, with fingerprints
4. combined plan        what changes on each side, in memory
5. confirmation         the user approves the combined plan
6. stage 1              apply to one destination
7. verification 1       re-read and check
8. stage 2              apply to the other
9. verification 2       re-read and check
10. compensation        if stage 2 fails, undo stage 1
11. result              applied | compensated | partial_failure
```

**Step 10 is the one that decides whether this can exist.** Compensating `live` means reverting in memory with another `SaveChanges`, which can fail on its own. Compensating `pbip` is a file rollback, which we already know how to do. A failure in the compensation leaves `partial_failure`, which **is not success** and requires manual intervention.

And the underlying problem remains: steps 6–9 require Desktop to be open for `live` and closed for `pbip`. Any real saga would have to ask the user to close Desktop between the two stages, which turns it into a **guided workflow**, not an atomic operation.

### Conditions to enable it

Not before having, all of them:

1. failure tests at **every** boundary (6, 7, 8, 9) with verified compensation;
2. a test of the compensation **itself** failing, with `partial_failure` correctly reported;
3. a response that unambiguously distinguishes `applied`, `compensated` and `partial_failure`;
4. an explicit decision on the open/closed Desktop conflict.

**R15 doesn't close until then.** In the meantime, the recommendation is to pick one destination:

- **`live`** to iterate quickly with Desktop open, knowing it's lost if you close without saving;
- **`pbip`** for durable, versionable changes, with Desktop closed.
