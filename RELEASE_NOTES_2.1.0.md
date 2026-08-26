# Horizun PBI MCP v2.1.0

A minor release: **139 tools**, five of them new, and the frozen contract of the
original 34 untouched. The regenerated golden reports exactly one compatible
change — a longer description on `pbi_export_pbix`.

It also carries what `2.0.2` documented. That version was prepared but its tag
and release were never created, so its two fixes ship here rather than under a
number nobody can install. Its notes remain in `RELEASE_NOTES_2.0.2.md` and its
changelog entry is kept as written.

## A `.pbip` can finally leave as a `.pbix`

A `.pbip` is not a deliverable. Whoever receives it needs Power BI Desktop and
the whole folder; what they usually expect is one file they can open. There was
no way to produce it from here.

Microsoft publishes no API for that conversion. `pbi_export_pbix` therefore
automates the **supported** flow — Power BI Desktop's own `File > Save As` —
rather than stitching a `.pbix` by hand with TOM, which opens sometimes and
breaks other times.

```
pbi_finalize_delivery(path="C:\\proyectos\\Informe.pbip", format="pbix")
```

resolves the project, prepares it, validates the TMDL, exports through Desktop,
inspects the result and leaves exactly the deliverable open.

### What is checked, and why each check exists

- **The right window is driven**, not the one that appeared during the launch
  window.
- **The file type is chosen, never inherited.** The dropdown's default offer is
  `.pbip`; the option next to it is `.pbit`, a template with no data that looks
  exactly like a deliverable.
- **Controls resolve by automation id and control type**, never by screen
  position. A click at (412, 588) depends on the DPI, the monitor and the
  language.
- **A visible dialog is a classified modal with a suggested action**, never a
  timeout — and it is never dismissed automatically, because some of them mean
  losing data.
- **The dialog disappearing is not the file being written.** Desktop closes the
  box and writes afterwards. Existence, extension, size, an mtime inside this
  run, and openability by this repository's own `.pbix` reader are all checked
  before anything is called saved.

`overwrite=false` fails *before* opening any window. `overwrite=true` backs the
destination up and restores it if the export fails — and says so out loud if the
restore itself fails.

### Why it needed three attempts, and what that cost

The first three attempts all produced a `.pbip` with a `.pbix` name, and the
report I wrote after them blamed Power BI. That conclusion was wrong. **None of
the three causes were Power BI's:**

1. **`save_as_completo` lived inside a `Protocol` nothing inherits.** The
   service checked `hasattr(...)`, found nothing, and fell back to the older
   route. The fix had never executed once — five "failing variants" were five
   runs of the same dead code. Reading the source suggested the opposite, which
   is what made it survive so long.
2. **The `INPUT` structures for `SendInput` were rebuilt on every call.** ctypes
   compares types by class identity, so it refused the array with
   `incompatible types, INPUT instance instead of INPUT instance` — a message
   that names the same type twice and explains nothing. The file name was never
   typed.
3. **The wait for the file used the operation's global timeout.** A save that
   was never going to happen watched an empty folder for a quarter of an hour
   before saying anything.

Two design consequences came out of it:

**Win32 messages cannot commit the file type.** `CB_SETCURSEL` changes what the
dropdown *reads* without notifying the application, so Desktop went on saving
with the previous filter. Committing it needs UI Automation, which is COM.

**COM cannot live in the server process.** Importing it pins the thread's
apartment and breaks `pythonnet` with *«Cannot change thread mode after it is
set»*. A blocked COM call also cannot be cancelled from the inside: the earlier
attempt ran it in a daemon thread and called `join(timeout)` a timeout, when it
was only looking away — the thread stayed inside COM for the life of the server.
The dialog is now driven from a **separate process**, and the deadline is
enforced by the operating system terminating it.

**And the interface is waited on, not slept through.** The first version put
fixed `sleep` calls after injecting keystrokes — 0.3 s, 0.4 s, 0.8 s. Synthetic
keys reach the system queue instantly but the application consumes them at its
own pace, so on an idle machine those margins were wasted and on a busy one they
were not enough: the filename field got read half-written (76 characters
requested, 31 read) and that was reported as a *typing* failure. It was a
synchronization failure. Every margin is now a wait for a **state** — the field
contains the path, the list has options, the dropdown has closed — polled every
50 ms. It is both steadier and faster: a save went from 5.3 s to 3.1–4.5 s, and
exhausting the deadline now accuses the synchronization rather than the write.

`comtypes` ships as the optional `export` extra:

```
pip install "horizun-pbi-mcp[export]"
```

Without it, everything else — DAX, TMDL, PBIR, audits — works exactly as before.
`pbi_capabilities` reports it under `pbix_export`, and `scripts/doctor.py`
raises it as a warning, never as a failure.

## Also new

- **`pbi_prepare_project`** — the front door for settling *which* project a call
  means, converting a `.pbix` first when that is what it was given.
- **`pbi_get_power_query` / `pbi_update_power_query`** — in a `.pbip` the M code
  has no file of its own: it lives inside the TMDL, in each table's `partition`
  and in `expressions.tmdl`. The block is located by structure and replaced
  whole, never by regex — a regex breaks on the first query with an `in` inside
  a string literal, and it breaks silently.

## Fixed

- **A folder with two projects picked one alphabetically, in silence.**
  `sorted(matches)[0]`. With `Antiguo.pbip` and `Nuevo.pbip` side by side,
  measures, pages and publication all went to the wrong project and reported
  success. A folder now resolves only when it holds exactly one candidate.
- **DAX identifiers are case-insensitive; the resolver was not.**
  `Cronograma[Fecha]` against a table named `CRONOGRAMA` came back as
  `measure_broken_reference` — an ERROR, with a score penalty, for a measure the
  engine runs fine.
- **Secrets survived a conversion.** A token pasted into a `Web.Contents` header
  is invisible inside a `.pbix` and plain text the moment it becomes a `.pbip`.
  High-confidence findings now block publication. Fail-closed, with no
  `allow_secrets` flag: a flag to publish a secret knowingly is a flag someone
  eventually passes.
- **Engine pid and Desktop pid were conflated** — `msmdsrv.exe` and
  `PBIDesktop.exe` are different processes, and anything driving a window by the
  wrong one was driving the wrong process.
- **`None fila(s)` in `pbi_diagnose_data`**, and auto date/time tables drowning
  every actionable finding.

## Verification

- **Against a real Power BI Desktop, through the public tool.** A synthetic
  `.pbip` came out as a **13,939-byte `.pbix`** with `report_format='pbir'` and
  a data model inside, via `pbi_finalize_delivery`, in about 17 seconds — with
  the generated file left open and no orphaned processes or helpers. The human
  baseline it was measured against produced 13,946 bytes.
- The success criterion was deliberately not "the dropdown says PBIX". It was:
  the requested `.pbix` exists, no `.pbip` substitute exists, the reader
  inspects it, `saved_as_verified` and `opened_path_verified` are both true, and
  the public tool completes the flow.
- **Five consecutive live runs on a machine in active use: 5 of 5.** The
  earlier version passed once and then failed twice with different partial
  lengths, which is what exposed the fixed-margin defect. Intermittency was
  fixed by synchronizing on state, not by widening the margin.
- Dependency locks intact: 5 locks, 216 pinned dependencies.
- Contract: 139 tools, one compatible change.
- CodeQL: the 34 alerts this branch introduced are fixed in code — none
  dismissed. They were one real defect (a test whose assertion could not fail)
  and 33 hygiene notes.

## Upgrading

Nothing to do beyond the usual. Add `[export]` if you want to produce `.pbix`
files; skip it and the other 137 tools behave identically.
