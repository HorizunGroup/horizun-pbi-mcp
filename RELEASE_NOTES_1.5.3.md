# Horizun PBI MCP v1.5.3

Same tool surface as 1.5.2 (134 tools, contract untouched). This release is
about being **easy to install and safe to say yes to** — and about repairing
something 1.5.2 published broken.

## 1.5.2 shipped corrupted text. Use 1.5.3 instead.

The 1.5.2 version bump was done with PowerShell 5.1, where `Get-Content -Raw`
reads with the ANSI codepage while `WriteAllText` writes UTF-8. Every accented
character it passed through was mangled. What went out corrupted:

- the `description` in **both** plugin manifests — the text shown in the plugin
  marketplace;
- install progress messages in `plugin_bootstrap.py` — text the user reads on
  screen while waiting.

Nothing functional broke, and no data was ever at risk. It was ugly in exactly
the place a first-time user looks, which is its own kind of expensive.

The full test suite never saw it, and that is the real lesson: **every test
checked content, none checked encoding.** There is now a guard across the
published manifests, the installer text and the front documentation, and it was
verified by pointing it at the actual file 1.5.2 shipped — it fails.

## The one-paste installer no longer depends on Node

Installing Claude Code went through `npm`, so it needed Node — whose MSI is
usually per-machine and fails without administrator. On the empty PC this
script exists to rescue, Node failing meant Claude Code never installed and the
whole thing stalled.

It now uses Anthropic's official installer first (`irm https://claude.ai/install.ps1`),
falls back to npm, and adds `~\.local\bin` to the running process's PATH so the
next step finds `claude` without anyone closing and reopening a terminal.

Two more honesty fixes in the same script:

- it announced "plugin registered" **unconditionally** — a dead install said
  goodbye in green. It now re-reads `claude plugin list` and reports what is
  actually there;
- `Set-ExecutionPolicy` can write the setting *and still throw* when a more
  specific scope overrides it, which turned a correct install into a yellow
  "pending". The verdict now comes from re-reading the effective policy.

## A README that answers both questions

Someone evaluating this has two questions, in this order: *how hard is it to
install*, and *what am I letting into my work machine*. The README now answers
both up front — two labelled install paths with one paste each and measured
timings, then a section stating point by point, checkable in this repository,
that it needs no administrator, sends nothing anywhere (the `telemetry` module
is local redacted logging to stderr), touches the network only for
version-pinned SHA-256-verified downloads, backs up before every write, and
**blocks a write it cannot prove is safe** rather than guessing.

## Verification

- The fresh-PC path was rehearsed end to end: the one-paste installer finished
  `LISTO` with zero pending items, registered plugin **1.5.3**, and a runtime
  built from nothing reached `ready` in **78 s** across its five stages; a
  second session then served **134 tools**.
- The encoding guard was verified against the real 1.5.2 artifact, not a
  synthetic one.
- Full suite with packaging, contract check, `doctor.py` and the three
  hash-verified downloads: green. The one test that needs Power BI Desktop
  closed is environmental and is covered by the clean-machine CI run.
