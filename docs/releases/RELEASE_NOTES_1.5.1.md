# Horizun PBI MCP v1.5.1

Same tool surface as 1.5.0 (134 tools, contract untouched). This release is
about the ten minutes BEFORE the first tool call — because this morning a
2h11m training session with six users spent most of it installing, and the
worst failure was mute.

## Install = one prompt

With Claude Code open, paste this and let the agent fight the dependencies:

> Instala el MCP de Power BI de Horizun (HorizunGroup/horizun-pbi-mcp):
> agrega su marketplace, instala el plugin, corre su instalador de un pegado
> si falta algún prerequisito, resuelve los pendientes que marque, y no pares
> hasta que `pbi_install_status` diga `ready` y aparezcan las tools `pbi_*`.

The bundled `horizun-pbi-setup` skill now carries the full field runbook
(symptom → remedy), so that prompt is genuinely enough.

## No Claude Code yet: install in one paste

```powershell
irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex
```

A normal PowerShell window; administrator NOT needed. It installs every
prerequisite at user level — real Python (dodging the Microsoft Store alias),
Git, optional Node for the official validator, the user execution policy,
Claude Code via npm when available — registers the plugin, and prints either
`LISTO` with the one remaining step, or the exact list of what IT must
approve (user-scope package ids). Idempotent: paste it again after fixing
anything; nothing repeats, nothing breaks.

## The mute failure, fixed

The plugin manifests declared `"command": "python"`. On Windows machines where
`python` is the Microsoft Store alias, the launcher never ran — and with it
died `pbi_install_status`, the one component that knows how to explain
problems. A dead MCP with zero output, before the first log line.

Both manifests now start through `scripts/launch.cmd`: it resolves a REAL
interpreter (`py -3` first, then any PATH python that is not the WindowsApps
shim) and, when none exists, writes the remedy to stderr instead of dying
silently.

## Network failures stopped being fatal

The runtime bootstrap performs four downloads (PyPI, NuGet DLLs, PBIR
schemas, npm validator) and the team has a measured IPv6 DNS race that kills
them intermittently. Each step now retries 3x with backoff, and a final
failure states the part that was always true but nobody knew: relaunching
RESUMES from the same step — downloads are hash-verified and never repeated.

## Verification

- 2168 tests passed, 3 skipped (environmental, documented) — final run on the
  tagged tree, packaging included; the handshake reports 1.5.1.
- Live smoke through the NEW entry: `launch.cmd` resolved a real interpreter
  via `py -3` and the full MCP handshake answered
  `serverInfo: horizun-pbi-mcp 1.5.1`.
- Contract check exit 0: no changes against the frozen golden (134 tools).
- New guards: the plugin manifests are pinned to the `launch.cmd` entry, the
  launcher must filter `WindowsApps` and speak through stderr, and
  `instalar.ps1` must stay ASCII-only, user-scope-only and elevation-free.
