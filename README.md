# Horizun PBI MCP

[![CI](https://github.com/HorizunGroup/horizun-pbi-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/HorizunGroup/horizun-pbi-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/horizun-pbi-mcp?cacheSeconds=3600)](https://pypi.org/project/horizun-pbi-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/horizun-pbi-mcp?cacheSeconds=3600)](https://pypi.org/project/horizun-pbi-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**Build and fix Power BI reports by describing what you want.**
Ask in plain language; the server runs the DAX, edits the model, writes the
report pages — and checks its own work afterwards.

![How it works](https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/docs/assets/como-funciona.png)

**v2.1.0** · 139 tools · Windows · Python 3.10+ · Claude Code, Codex, any MCP client

## Install

No repository clone, manual DLL download or `.mcp.json` editing is required.
Pick the line that matches you.

### Claude Code

```powershell
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

### Codex

```powershell
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
```

Then open `/plugins`, pick the **Horizun** marketplace and install
`horizun-pbi-mcp`.

### Or just ask the agent — easiest of all

Paste this into Claude Code or Codex and let it install itself:

> Install Horizun PBI MCP from `HorizunGroup/horizun-pbi-mcp`. Complete the runtime
> setup, watch `pbi_install_status` until it reports `ready`, restart the session,
> and confirm the 139 `pbi_*` tools are available.

### A brand-new Windows PC

One paste in PowerShell installs the prerequisites first — version-pinned and
SHA-256 verified, no administrator rights. See [`docs/INSTALL.md`](docs/INSTALL.md).

### Finishing up

The first setup downloads the runtime and takes a few minutes. Run
`pbi_install_status` until it says `ready`, then restart your client once so the
tools load. If anything looks stuck, [`docs/INSTALL.md`](docs/INSTALL.md) covers
repair and offline installs.

## Your first minute

1. Open Power BI Desktop with any report.
2. Ask: *"List the Power BI models that are open and connect to the first one."*
3. Ask: *"Run a read-only DAX query that returns revenue by month for this year."*

If step 2 answers, everything works.

## Things worth asking for

```text
Audit this PBIP project and explain the highest-risk issues before changing anything.
```

```text
This measure returns blank for December. Find out why.
```

```text
Create a report page from this specification, validate it, and show me the layout.
```

```text
Document every measure in the model and export it to Excel.
```

## What it provides

| Area | Capabilities |
|---|---|
| Live model | Discover Desktop sessions, run DAX, inspect metadata, refresh, and manage measures through TOM |
| Semantic model | Read and edit TMDL tables, columns, measures, relationships, roles and calculation groups |
| Report authoring | Create pages and visuals, clone templates, arrange layouts, apply themes, bookmarks and interactions |
| Conversion | Convert `.pbix` to `.pbip`, migrate supported structures and validate the result |
| Quality | Audit models and reports, document schemas, detect broken references and normalize report structures |
| Delivery | Export verified PDF, Word, Excel and PowerPoint artifacts; ingest SharePoint data read-only |

See the [tool catalog](docs/TOOL_CATALOG.md) for all 139 tools and their risk
classifications.

## Why it is safe to point at real work

- Local-first: Power BI Desktop communication stays on `localhost`.
- No telemetry, and the MCP server needs no account.
- Project writes stay inside the active project directory.
- Every project write makes a backup and is re-read to confirm it landed.
- Destructive tools require `confirm=true`.
- JSON writes are atomic; invalid JSON is never written over good JSON.
- Downloaded runtime components are version-pinned and SHA-256 verified.
- Logs go to stderr or files, so stdout stays a clean JSON-RPC channel.

Full guarantees and failure behavior: [security model](docs/SECURITY.md) and
[recovery guide](docs/RECOVERY.md). Report vulnerabilities privately as described
in [SECURITY.md](SECURITY.md).

## Requirements

- Windows 10 or 11.
- Python 3.10 or newer.
- Power BI Desktop — only for the live layer (DAX, refresh, capture, render checks).
- A `.pbip` project with PBIR enabled, for report-file authoring.
- Node.js 20+ only for the optional Microsoft PBIR validator.

Claude Code, Codex and Power BI Desktop are external programs; this project does
not install them. Without Desktop the `.pbip` tools still work — the live tools
do not.

## Known limits

- Power BI does not expose pages or visuals through its live local endpoint, so
  those are edited in PBIR files while Desktop is safely closed.
- `mode="both"` is disabled for dual live/disk writes: Desktop being open and a
  PBIP project being safe to edit are mutually exclusive states.
- The server does not publish to or refresh the Power BI Service.
- Three PBIR schemas referenced by Power BI are not published by Microsoft;
  affected writes fail closed instead of guessing.

## Documentation

| Document | Purpose |
|---|---|
| [Installation](docs/INSTALL.md) | Setup, repair, offline installation and MCP client registration |
| [Tutorial](docs/TUTORIAL.md) | First connection through report authoring |
| [Tool catalog](docs/TOOL_CATALOG.md) | All tools, grouped by capability and risk |
| [Architecture](docs/ARCHITECTURE.md) | Components, boundaries and invariants |
| [Security](docs/SECURITY.md) | Threat model and operational guarantees |
| [Validation](docs/VALIDATION.md) | TMDL/PBIR validation layers and known limits |
| [Migration guide](docs/MIGRACION_1x_A_2.0.md) | Breaking changes from 1.x to 2.0 |
| [Contributing](CONTRIBUTING.md) | Development workflow and pull-request requirements |

## Development

```powershell
git clone https://github.com/HorizunGroup/horizun-pbi-mcp.git
cd horizun-pbi-mcp
python -m pip install -e .
python scripts/fetch_libs.py
python scripts/fetch_pbir_schemas.py
python scripts/doctor.py
```

Before opening a pull request:

```powershell
python -m pytest -q
python scripts/doctor.py
python -m tests.contract_utils
```

The MCP contract is frozen and checked against `tests/golden/tools_v1.json`.
Real `.pbix`, `.pbip`, credentials, DLLs, outputs and backups are never
committed. See [AGENTS.md](AGENTS.md) for the repository invariants.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## MCP Registry

This package is the one published under the server name below. The registry
reads this line from the README of the **PyPI package** to confirm that whoever
publishes the metadata also owns the package, so it has to live here rather
than in `.mcp/server.json` alone.

```
mcp-name: io.github.HorizunGroup/horizun-pbi-mcp
```
