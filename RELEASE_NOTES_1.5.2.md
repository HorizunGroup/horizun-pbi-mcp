# Horizun PBI MCP v1.5.2

Same tool surface as 1.5.1 (134 tools, contract untouched). 1.5.1 taught the
launcher to reject the Microsoft Store alias. This one closes the gap that fix
left open: **rejecting a bad name is not the same as picking an interpreter
that works.**

## The launcher now chooses by running, not by existing

`launch.cmd` accepted the first candidate that passed a *presence* test. Three
states were reproduced in a terminal, each passing that test without serving:

| Machine state | What happened | Now |
|---|---|---|
| **Orphaned `py.exe`** — Python uninstalled, the launcher survived | picked it, died with `Python 3.x not found` and exit 103, named no remedy, and never tried the real `python` that WAS on the PATH | discarded like any other candidate that doesn't run; the real interpreter is used |
| **Python below the 3.10 floor** declared in `pyproject.toml` | started fine, then failed much later and worse, inside the server, with an error that never mentioned the version | its own message: too old, plus the exact install command |
| **A `.bat`/`.cmd` candidate** — pyenv-win shims, corporate wrappers | invoked without `call`, a batch file takes the control and never gives it back: the plugin went mute inside the very file written to prevent that | every candidate, and the final launch, go through `call` |

When no candidate works, the message distinguishes **"no Python"** from
**"Python too old"**, because the remedy differs and a single generic text
sends half the people to the wrong fix.

## "I just installed it and this console doesn't see it"

As a last resort the launcher now probes where winget actually installs
(`%LOCALAPPDATA%\Programs\Python`, `%LOCALAPPDATA%\Python`, `%ProgramFiles%`).
The documented field trap was a freshly installed Python invisible to an
already-open terminal; instead of asking someone to close and reopen windows,
the launcher looks where it was put.

## Guided install prompt in the README

The install section now leads with the full prompt used in the field, with its
eight rules: plan before action, announce every step with an honest ETA, show
evidence after each one, refresh the process PATH instead of delegating that to
the user, drive `pbi_install_status` until `ready`, and end any failure in a
diagnosis plus the exact fix command. The table of field failures each rule
answers to is folded away for maintainers.

The measured cause it exists for: an agentic install without those rules burned
15+ minutes that *felt* like a hang, and the user abandoned. The reference
timings deliberately overshoot — nobody ever complained that an install
finished early.

## Verification

- Five launcher scenarios executed for real, not asserted: orphaned `py`, only
  a 3.9, only the Store alias, nothing at all, and the happy path with a full
  MCP handshake.
- The four new tests were checked **by mutation**: dropping the `call` fails
  three of them, lowering the version floor fails the guard that ties
  `launch.cmd` to `pyproject.toml`.
- End-to-end on what users actually receive: the plugin installed **from
  GitHub** into an isolated profile, with a broken `py` first on the PATH,
  answered `serverInfo: horizun-pbi-mcp` and served **134 tools**.
- Full suite with packaging included: **2173 passed, 1 skipped**. Contract
  check exit 0, no changes against the frozen golden (134 tools); `doctor.py`
  exit 0; the three verified downloads (DLLs, PBIR schemas, official
  validator) check their hashes and pass.
- One test does not pass on the development machine and it is **environmental,
  not a defect**: `test_los_autofixes_respetan_la_politica_estricta` needs
  Power BI Desktop closed. With Desktop open — and one of its processes
  refusing inspection — the open-project detector returns `unknown`, and the
  strict policy blocks by design rather than risk a silent overwrite. The
  authoritative green is the CI run on a clean Windows machine, where no
  Desktop exists.
