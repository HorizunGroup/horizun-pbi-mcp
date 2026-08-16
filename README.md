# Horizun PBI MCP

[![CI](https://github.com/HorizunGroup/horizun-pbi-mcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/HorizunGroup/horizun-pbi-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/horizun-pbi-mcp)](https://pypi.org/project/horizun-pbi-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/horizun-pbi-mcp)](https://pypi.org/project/horizun-pbi-mcp/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

Local Power BI automation for Codex, Claude Code and other MCP clients.
Horizun PBI MCP connects to Power BI Desktop for live DAX/TOM operations and
works directly with `.pbip` projects for durable TMDL/PBIR editing.

**v2.0.1** · 134 tools · Windows · Python 3.10+

## Install

No repository clone, manual DLL download or `.mcp.json` is required.

### Codex

Add the marketplace:

```text
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
```

Open `/plugins`, select the **Horizun** marketplace and install
`horizun-pbi-mcp`.

You can also paste this prompt into Codex:

> Install Horizun PBI MCP from `HorizunGroup/horizun-pbi-mcp`. Complete the runtime setup, monitor `pbi_install_status` until it reports `ready`, restart the session, and verify that the 134 `pbi_*` tools are available.

### Claude Code

```text
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

Open a new session and run `pbi_install_status`. When it reports `ready`,
restart Claude once so the `pbi_*` tools are loaded.

### Verify

1. Open Power BI Desktop with a report.
2. Run `pbi_list_desktop_models`.
3. Select a model with `pbi_select_model` if more than one is open.
4. Test the connection with `pbi_test_connection`.

The first runtime setup normally takes a few minutes. If Python or another
prerequisite is missing, use the verified Windows bootstrap in
[`docs/INSTALL.md`](docs/INSTALL.md).

## What it provides

| Area | Capabilities |
|---|---|
| Live model | Discover Desktop sessions, run DAX, inspect metadata, refresh, and manage measures through TOM |
| Semantic model | Read and edit TMDL tables, columns, measures, relationships, roles and calculation groups |
| Report authoring | Create pages and visuals, clone templates, arrange layouts, apply themes, bookmarks and interactions |
| Conversion | Convert `.pbix` to `.pbip`, migrate supported structures and validate the result |
| Quality | Audit models and reports, document schemas, detect broken references and normalize report structures |
| Delivery | Export verified PDF, Word, Excel and PowerPoint artifacts; ingest SharePoint data read-only |

See the complete [tool catalog](docs/TOOL_CATALOG.md) for all 134 tools and
their risk classifications.

## Example requests

```text
List the Power BI models currently open and select the sales model.
```

```text
Run a read-only DAX query that returns revenue by month for the current year.
```

```text
Audit this PBIP project and explain the highest-risk issues before changing anything.
```

```text
Create a report page from this specification, validate it and show me the resulting layout.
```

## Safety model

- Local-first: Power BI Desktop communication stays on `localhost`.
- No telemetry or account is required by the MCP server.
- Project writes are restricted to the active project directory.
- Every project write creates a backup and is verified after completion.
- Destructive tools require `confirm=true`.
- JSON writes are atomic and invalid JSON is never overwritten.
- Downloaded runtime components are version-pinned and SHA-256 verified.
- Logs go to stderr or files; stdout remains the JSON-RPC channel.

Read the [security model](docs/SECURITY.md) and [recovery guide](docs/RECOVERY.md)
for the full guarantees and failure behavior. Vulnerabilities can be reported
privately as described in [SECURITY.md](SECURITY.md).

## Requirements

- Windows 10 or 11.
- Python 3.10 or newer.
- Power BI Desktop for live DAX/TOM, refresh, capture and render validation.
- A `.pbip` project with PBIR enabled for report-file authoring.
- Node.js 20+ only for the optional Microsoft PBIR validator.

Codex and Claude Code are external clients. Power BI Desktop is not installed
by this project.
Without Desktop, the `.pbip` tools still work; the live tools do not.

## Important limitations

- Power BI does not expose pages or visuals through its live local endpoint;
  those are edited in PBIR files while Desktop is safely closed.
- `mode="both"` is disabled for dual live/disk writes because Desktop being
  open and a PBIP project being safe to edit are mutually incompatible states.
- The server does not publish to or refresh the Power BI Service.
- Three PBIR schemas referenced by Power BI are not currently published by
  Microsoft; affected writes fail closed instead of guessing.

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
