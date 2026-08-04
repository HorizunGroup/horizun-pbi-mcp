# Horizun PBI MCP v1.1.0

Feature release. Everything in it came out of real use — five gaps hit while
building a construction-budget dashboard end to end, and fourteen limitations
written down during a second full session — not from a roadmap. One new tool
(`pbi_open_and_refresh`, 118 → 119) and zero breaking changes: the frozen
contract holds.

## Added

- **Risk class declared to the client**: all 119 tools now publish MCP
  `annotations` (`readOnlyHint`, `destructiveHint`, …), so a client can
  auto-allow reads and warn before deletions. The hand-written table is
  checked against AST evidence from the code; an unclassified tool is
  announced as destructive, never as read-only.
- **Bounded inventories**: `detail='summary'` and `tables=[...]` in
  `pbi_list_tables` / `pbi_list_measures` (−98% response size measured on a
  real project). Defaults unchanged.
- **Header-row autodetection and `skip_rows`** in `pbi_add_table_from_file`:
  the classic ERP export (title in row 1, real header in row 2) now loads,
  and the generated M query skips the same rows the profiler skipped.
- **`format` block per visual** (slicer mode, data labels, legend) validated
  against the official catalog, plus incremental z-order.
- **`mode='auto'`** on the six dual tools — resolves live/pbip from the real
  state before any effect; the default stays `live` (frozen contract).
- **`rows_by_table`** in `pbi_refresh_model` — a refresh can succeed having
  loaded zero rows; now it says so — and the new **`pbi_open_and_refresh`**.

## Changed

- **Single installable package `horizun_pbi_mcp`**: the wheel used to install
  ten top-level names (`config`, `server`, `services`, `tools`, `utils`, …)
  into `site-packages`, colliding with half the Python ecosystem. Launching
  from a clone is now `python -m horizun_pbi_mcp.server` with
  `PYTHONPATH=<repo>/src`; `scripts/make_mcp_config.py` emits the right form.
- **User data out of the library tree**: installed via pip, `outputs/` and
  `backups/` now go to the OS user-data directory instead of vanishing inside
  the virtualenv on the next reinstall. From a clone, paths are unchanged.

## Fixed

- The official validator reported schema errors without listing a single one;
  `validation_report.diagnostics[]` now carries rule, severity, file, JSON
  path and the CLI's message, including preexisting findings.
- Three claims the server made that weren't true: the refresh persistence
  note (false for `.pbip`), theme presets defining data labels without
  turning them on, and a spec hint describing a shape the validator rejects.
- `textbox`/`image` errors now say where the property goes
  (`options.text` / `options.resource`), with a complete example.
- `scripts/fetch_pbir_schemas.py` pointed at the pre-repackaging manifest
  path, breaking plugin bootstrap, the CI schema step and the README install
  instructions. Now guarded by a test that loads every script and checks its
  declared repo paths exist.
- An intermittent failure in `test_hide_columns_bulk` (session-verification
  TTL expiring mid-test) fixed by removing the clock from the equation.

## Evidence

- 119 tools; MCP contract: 0 breaking changes (additive only), golden updated.
- 1699 tests passed, 3 skipped with their condition documented.
- Every behavioral fix mutation-verified: reverting it makes a test fail.
- Clean-install validated end to end: wheel → venv → MCP handshake over stdio
  (119 tools) → console command, from outside the repository.
- `scripts/doctor.py` exit 0; CI green on Windows, Python 3.10 and 3.13.
