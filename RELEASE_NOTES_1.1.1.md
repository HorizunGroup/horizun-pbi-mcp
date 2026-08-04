# Horizun PBI MCP v1.1.1

Closes the fourteen-item field report completely and cleans the repository of
client names — current state and full history. Two new tools (121 total),
zero breaking changes: the frozen contract holds.

## Added

- **`theme_json` + `fonts`** on `pbi_apply_theme`: full caller themes written
  as-is and corporate typography via `textClasses`, with a warning (and a
  recovery pointer) when a hand-edited theme is being replaced.
- **`pbi_rename_measure`**: TMDL header, DAX references and report visuals in
  one transaction, verified by re-reading. Only unambiguous references are
  rewritten; everything else is reported with its location, never silently.
- **`pbi_close_desktop`**: closes only the instance serving that project,
  identity-verified (name + start time), `confirm`-gated, and re-checks the
  file is actually no longer open.

## Changed

- Client names removed from the repo and its rewritten history, guarded by a
  test that scans every tracked file — and builds its own forbidden literal
  split, so the guard never publishes what it polices.
- `docs/BACKLOG.md` brought current; the `pbi_apply_plan` contract question is
  decided and closed (the `plan_token` is the explicit approval).

## Evidence

- 121 tools; MCP contract: additive changes only, golden updated.
- 1744 tests passed, 3 skipped with their condition documented.
- Every behavioral change mutation-verified: reverting it fails a named test.
- `scripts/doctor.py` exit 0; CI green on Windows, Python 3.10 and 3.13.
