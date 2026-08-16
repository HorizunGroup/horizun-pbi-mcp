# Horizun PBI MCP v2.0.2

A patch release with no new tools and no contract change: still 134 tools, and
the three CONTRACT-003 changes from `2.0.1` are the last ones. What this fixes
is the server **saying things that were not true** — a permanent false alarm, a
fallback that never ran, and an instruction pointing at the wrong file.

Both defects were reported from an outside installation on 2026-08-16, hours
after `2.0.1` shipped, by someone updating through the plugin marketplace.
Neither breaks anything in use. If `2.0.1` works for you, this is a quiet
upgrade.

## The health check said the interop was down. It wasn't.

`pbi_health_check` read `clr_available` and `runtime` out of `diagnostics()`.
Neither key has ever existed — the real ones are `runtime_loaded` and
`runtime_preference`. `.get()` returned `None` for both, `bool(None)` is
`False`, and so every healthy installation started like this:

```json
{"check": "clr", "ok": false, "detail": null, "required": false}
```

with `"warnings": ["clr"]` and `"status": "warning"` on top. A permanent alarm
sending people to diagnose a problem that was not there, and — because the
detail came from the other missing key — not even saying what had failed.

The contradiction was visible from the outside: in the same process,
`pbi_list_desktop_models` reported `runtime_loaded: true` while the health check
called the interop dead.

**Why renaming the key was not the fix.** The .NET runtime loads lazily: only
`load_adomd()` and `load_tom()` ask for it, so a freshly started server has not
loaded it yet and that is entirely correct. Reading `runtime_loaded` directly
would have moved the false negative rather than removing it — from "always red"
to "red until you do something else".

So the interop now reports **three** states, not two:

| State | Meaning | Warns |
|---|---|---|
| `loaded` | the runtime is up | no |
| `not_attempted` | nothing has needed it yet; it loads on the first model operation | no |
| `failed` | it was tried and could not be loaded | **yes** |

The runtime is deliberately **not** probed inside the check. `pbi_health_check`
is advertised as read-only, and loading .NET from it would be a side effect in
the one tool you call precisely to look without touching. If you want a real
probe, that is what `pbi_test_connection` is for.

And the detail is never `null` again — including the failure case, which now
carries the cause. A check that fails without explaining is worse than no check.

## The fallback to `coreclr` was dead code

This one was not reported; it turned up while reading pythonnet 3.1.0 to fix the
first. `_ensure_runtime` treated every `RuntimeError` from `load()` as "a
runtime was already loaded in this process":

```python
except RuntimeError:
    # Ya habia un runtime cargado en el proceso: lo aceptamos.
    _runtime_loaded = True
    return
```

It is exactly backwards. `pythonnet.load()` opens with `if _LOADED: return` —
the already-loaded case returns cleanly and **never raises**. `RuntimeError` is
what genuine failures look like: `Failed to create a .NET runtime (netfx)`,
`No valid runtime selected`, `Failed to initialize Python.Runtime.dll`.

Two consequences. The obvious one: a real failure was recorded as success, and
the error resurfaced later inside `clr.AddReference`, where the message blames a
DLL instead of the runtime. The one that mattered more: the `except` returned
from **inside the loop**, so the `[preferred, netfx, coreclr]` order was never
walked. A machine without .NET Framework never tried the other runtime — it gave
up while reporting that it had worked.

The failure of every attempt is now recorded, which is also what lets the health
check distinguish "not attempted" from "could not" without probing anything.

## The setup skill pointed at the wrong file

`skills/horizun-pbi-setup/SKILL.md` said the one-paste block is reproduced in
full in `README.md` and `docs/INSTALL.md`. The README stopped embedding it when
it became a link to `docs/INSTALL.md`; the same stale claim lived in the header
of `scripts/one_paste.ps1` itself.

The sentence exists to say *do not write this from memory, copy it from there*.
An agent that follows it opens the README, does not find the block, and may
conclude it has to reconstruct it — the exact failure the sentence prevents.

The blocks themselves were already guarded by hash against the canonical file.
The prose announcing them was guarded by nobody. It is now: a test reads both
places and fails if either names a file that does not embed the block.

## Verification

- Red first in all three cases. For the CLR defect, 10 of 11 new tests fail on
  `e3ed562`; the one that already passed is the one pinning that pythonnet's
  already-loaded path raises nothing — the assumption the second defect grew
  from, now held by a test instead of a comment.
- Full suite: **2988 passed, 3 skipped** (the three need Desktop open).
- No contract change, no golden change.

## Upgrading

Nothing to do beyond the usual. The one-paste installer, the marketplace entry
and the PyPI package all move to `2.0.2`; the installer's bytes are unchanged
from `2.0.1`, so its published SHA-256 is the same on purpose.
