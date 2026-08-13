# Installing and registering Horizun PBI MCP

## If you already have Claude Code: ONE PROMPT — start here

Paste this into Claude Code and let the agent fight the dependencies for you.
English or Spanish, same result:

> Install the Horizun Power BI MCP (HorizunGroup/horizun-pbi-mcp): add its
> marketplace, install the plugin, run its one-paste installer if any
> prerequisite is missing, resolve whatever it marks as pending, and don't
> stop until `pbi_install_status` says `ready` and the `pbi_*` tools appear.

> Instala el MCP de Power BI de Horizun (HorizunGroup/horizun-pbi-mcp):
> agrega su marketplace, instala el plugin, corre su instalador de un pegado
> si falta algún prerequisito, resuelve los pendientes que marque, y no pares
> hasta que `pbi_install_status` diga `ready` y aparezcan las tools `pbi_*`.

The plugin ships a setup skill (`horizun-pbi-setup`) with the full runbook —
field symptoms included — so the agent knows every remedy: the Store-alias
trap, missing Git, execution policy, stale PATH, network races, and what to
request from IT when nothing can be installed.

## No Claude Code yet: one-paste install (Windows, no admin)

Paste this into **PowerShell** (a normal window; administrator NOT needed).
It also installs Claude Code itself when npm is available:

```powershell
irm https://raw.githubusercontent.com/HorizunGroup/horizun-pbi-mcp/main/scripts/instalar.ps1 | iex
```

It checks and installs everything at **user level**: real Python (dodging the
Microsoft Store alias that silently kills MCP servers), Git, optional Node for
the official PBIR validator, the user execution policy, Claude Code itself (via
npm when available) and the plugin registration. It is **idempotent**: if
something stays pending (e.g. IT must approve an install), fix it and paste the
same command again — nothing is repeated, nothing breaks.

When it prints `LISTO`, open Claude Code: the first session prepares the
runtime by itself (`pbi_install_status` shows progress), then restart Claude
once and the `pbi_*` tools appear.

**If IT blocks winget**: the script prints exactly which package ids to request
(`Python.Python.3.12`, `Git.Git`, `OpenJS.NodeJS.LTS` — all user-scope). That
printout is the ticket to hand to your IT team.

### Known traps this path already dodges

| Symptom in the field | Cause | Handled by |
|---|---|---|
| Plugin dead, no error anywhere | `python` resolves to the Microsoft Store alias (WindowsApps shim) | `launch.cmd` rejects WindowsApps paths and explains via stderr if no real interpreter exists |
| Plugin dead, exit code 103 | Orphaned `py.exe`: Python was uninstalled and the launcher survived, so `py -3` resolves nothing | `launch.cmd` accepts a candidate only if it actually RUNS, then falls through to the next one |
| Obscure failure deep inside the server | Python older than the 3.10 floor: old enough to start, too old to work | each candidate is probed against `pyproject.toml`'s floor; a too-old interpreter gets its own message, not the generic one |
| Plugin dead when Python comes from pyenv-win or a corporate wrapper | those shims are `.bat`/`.cmd`, and a batch file invoked without `call` takes the control and never returns it | every candidate — and the final launch — is invoked with `call` |
| "I just installed it and it still doesn't work" | the already-open terminal keeps the old PATH | `launch.cmd` also probes where winget installs (`%LOCALAPPDATA%\Programs\Python`, `%LOCALAPPDATA%\Python`, `%ProgramFiles%`), so nobody has to reopen windows |
| "Git is required for local sessions" | Claude Code needs Git | installer installs `Git.Git` user-scope |
| Install fails halfway on network | Measured IPv6 DNS race against nuget.org / developer.microsoft.com | bootstrap retries each download step 3x; relaunching resumes (hash-verified) |
| "running scripts is disabled" | PowerShell execution policy | installer sets `RemoteSigned` for the current user only |
| Freshly installed tool "not recognized" | stale PATH in the open terminal | installer refreshes PATH in-session; if it still hides, close and reopen the terminal |

## Direct plugin for Codex and Claude Code

This is the recommended path for end users. It doesn't require a dedicated
executable installer or hand-editing MCP files:

```bash
# Codex
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
codex plugin add horizun-pbi-mcp@horizun

# Claude Code
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

Setup starts automatically on the first session. While it progresses
you'll see `pbi_install_runtime` and `pbi_install_status`; after restarting
the client the 134 tools will appear. Nothing needs to be downloaded or run
separately. The runtime and verified downloads stay in the plugin's local
data, outside the repository and your projects.

Python 3.10+ is still a requirement: it's the local process that talks to
Power BI Desktop. Node 20 is only needed for the optional PBIR validator.

Reproducible guide from scratch. At the end, an MCP client should see 134 `pbi_*` tools.

---

## 1. Requirements

| Requirement | Why | Mandatory |
|---|---|---|
| **Windows** | Power BI Desktop only exists on Windows | For the LIVE layer. The ON-DISK layer (`.pbip`) works on any OS |
| **Python ≥ 3.10** | Tested on 3.14.3 | Yes |
| **.NET Framework 4.x** | Used by `pythonnet` (`netfx` runtime) | For the LIVE layer |
| **Power BI Desktop** | Runs the local `msmdsrv.exe` engine | For the LIVE layer |
| **Analysis Services DLLs** | ADOMD.NET + TOM. Vendored into `libs/`, no admin or GAC | Yes |

---

## 2. Installation

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/fetch_libs.py
```

The second command downloads the Analysis Services DLLs to `libs/`. It doesn't require administrator permissions and doesn't touch the GAC.

### Check before registering anything

```bash
python scripts/doctor.py
```

Must end with `RESULTADO: instalacion operativa` and **exit code 0**. If something mandatory fails, the diagnostic says exactly what and how to fix it.

---

## 3. Registration per client

There's no common portable way: each client resolves variables, the working directory and the Python interpreter its own way. That's why the generator emits **already-resolved absolute paths** on your machine.

```bash
python scripts/make_mcp_config.py --client all
```

Prints the correct snippet for each client. **It doesn't modify any global configuration**: global files are pasted in by hand, on purpose.

### Comparison

| | Claude Code | Claude Desktop | Codex | generic stdio |
|---|---|---|---|---|
| **File** | project's `.mcp.json`, or `~/.claude.json` | `%APPDATA%\Claude\claude_desktop_config.json` | `~/.codex/config.toml` | your client's |
| **Format** | JSON | JSON | **TOML** | usual JSON |
| **Expands `${VAR}`?** | Yes | **Don't assume it** | **Don't assume it** | Unknown |
| **Working directory** | Inherits Claude Code's | Not configurable | Inherits the process's | Varies |
| **Looks up Python?** | No: uses literal `command` | No | No | No |
| **Environment variables** | `env` object | `env` object | `[mcp_servers.x.env]` table | depends on client |
| **Check** | `/mcp` | restart and check the panel | restart and list | `scripts/doctor.py` |

> **Why `${PBI_MCP_HOME}` isn't used in the templates.** Only one of the four clients guarantees expanding it. A template that works on one and silently fails on the other three is worse than an explicit absolute path. If your client does expand variables, you can substitute them afterward: the server doesn't depend on any of them.

### Claude Code

```bash
python scripts/make_mcp_config.py --client claude-code --write
```

Creates `.mcp.json` **inside this repository** (it's in `.gitignore`: it's your local configuration). Restart Claude Code in this folder and verify with `/mcp`.

### Claude Desktop

```bash
python scripts/make_mcp_config.py --client claude-desktop
```

Copy the `mcpServers` block to `%APPDATA%\Claude\claude_desktop_config.json` and restart the application. If you use a virtual environment, point to the `python.exe` **of that venv**.

### Codex

Two official methods. **The first is recommended.**

#### Method 1 — `codex mcp add` with the installed package

Install the package (creates the `horizun-pbi-mcp` executable on the `PATH`) and register it with the Codex CLI:

```bash
python -m pip install horizun-pbi-mcp
```

```bash
codex mcp add horizun-pbi-mcp -- horizun-pbi-mcp
```

```bash
codex mcp list
```

Advantage: there's no absolute path to maintain. If you move the repository or change interpreters, it keeps working.

> If you use a virtual environment, activate it **before** `pip install` and `codex mcp add`: the executable is created inside that venv, and Codex will launch whichever it finds on the `PATH`.

To pass environment variables:

```bash
codex mcp add horizun-pbi-mcp --env HORIZUN_PBI_MCP_LOG_LEVEL=INFO -- horizun-pbi-mcp
```

#### Method 2 — `~/.codex/config.toml` by hand

Useful if you work from the repository without installing the package:

```bash
python scripts/make_mcp_config.py --client codex
```

Paste the resulting TOML section into `~/.codex/config.toml`. It's **TOML**, not JSON:

```toml
[mcp_servers.horizun-pbi-mcp]
command = "C:/path/to/python.exe"
args = ["-m", "horizun_pbi_mcp.server"]

[mcp_servers.horizun-pbi-mcp.env]
HORIZUN_PBI_MCP_LOG_LEVEL = "INFO"
# Required when running from a clone without installing the package:
# executing the module needs src/ on the import path.
PYTHONPATH = "C:/path/to/repository/src"
```

Both paths must be **absolute**: Codex doesn't expand `${VAR}` nor look up the interpreter for you.

#### Check

```bash
codex mcp list
```

`horizun-pbi-mcp` should appear. If not, check that `horizun-pbi-mcp --help` works in the same terminal you launch Codex from.

---

## 4. End-to-end verification

```bash
python -m pytest -q
```

```bash
python scripts/doctor.py --check-dax --check-pbip "tests/fixtures/synthetic/minimal/Demo.pbip"
```

`--check-dax` runs `EVALUATE ROW("ok", 1, "probe", "doctor")` against the open model: strictly read-only. `--check-pbip` opens and validates a `.pbip` without writing anything to it.

If Power BI Desktop isn't open, the base diagnostic **doesn't fail**: it marks those checks as skipped. To require Desktop:

```bash
python scripts/doctor.py --require-desktop
```

---

## 5. Environment variables

All optional. See `.env.example`.

| Variable | Default | For what |
|---|---|---|
| `HORIZUN_PBI_MCP_LIBS_DIR` | `./libs` | Where the DLLs are |
| `HORIZUN_PBI_MCP_DOTNET_RUNTIME` | `netfx` | `netfx` or `coreclr` |
| `HORIZUN_PBI_MCP_MAX_ROWS` | `1000` | Row limit in DAX |
| `HORIZUN_PBI_MCP_COMMAND_TIMEOUT` | `120` | Command timeout (s) |
| `HORIZUN_PBI_MCP_OUTPUTS_DIR` | `./outputs` | Documentation and `change_log.md` |
| `HORIZUN_PBI_MCP_BACKUPS_DIR` | `./backups` | Backups. **Always point it outside the `.pbip`** |
| `HORIZUN_PBI_MCP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `HORIZUN_PBI_MCP_DEFAULT_PBIP` | — | `.pbip` to open on startup |
| `HORIZUN_PBI_MCP_SHAREPOINT_TENANT_ID` | — | Microsoft Entra tenant for SharePoint app-only access |
| `HORIZUN_PBI_MCP_SHAREPOINT_CLIENT_ID` | — | Application/client ID registered in Entra |
| `HORIZUN_PBI_MCP_SHAREPOINT_CLIENT_SECRET` | — | Client secret; environment only, never a tool argument |
| `HORIZUN_PBI_MCP_PDFTOPPM` | auto-detected | Optional exact path to Poppler `pdftoppm` for PDF render verification |

### SharePoint Online setup

The SharePoint tools use Microsoft Graph v1.0 with the application's identity
(`client_credentials`). Register an application in Microsoft Entra ID, grant
the least privileged read permission suitable for your tenant and give admin
consent. Prefer `Sites.Selected` plus an explicit grant only to the sites the
server must read; broader permissions such as `Sites.Read.All` should be a
deliberate tenant decision.

Configure the three `HORIZUN_PBI_MCP_SHAREPOINT_*` variables in the MCP
process environment. Never paste the client secret into a chat or tool call.
`pbi_sharepoint_list_folder` validates the connection without writing locally;
`pbi_sharepoint_download_folder` writes a verified staged copy to
`outputs/sharepoint/` and does not modify SharePoint.

Official references: [Microsoft Graph app-only authentication](https://learn.microsoft.com/en-us/graph/auth-v2-service),
[selected SharePoint permissions](https://learn.microsoft.com/en-us/graph/permissions-selected-overview),
[list folder contents](https://learn.microsoft.com/en-us/graph/api/driveitem-list-children?view=graph-rest-1.0).

---

## 6. Common issues

| Symptom | Cause | Solution |
|---|---|---|
| `No se detecto ningun modelo` | Desktop closed, or port changed | The port changes on every startup; it's discovered automatically. Open the report |
| `adomd_not_installed` / `tom_not_installed` | Missing DLLs | `python scripts/fetch_libs.py` |
| `clr_not_available` | .NET missing | Try `PBI_MCP_DOTNET_RUNTIME=coreclr` |
| `pbir_not_enabled` | The report isn't in PBIR | Save as `.pbip` with the enhanced report format |
| Visual changes don't show up | PBIR loads on open | Close and reopen Desktop |
| Report changes were lost | Desktop was open and saved over them | Edit the PBIR **with Desktop closed**. Backups are in `backups/` |
| Server starts but the client doesn't see it | Wrong path or interpreter in the config | `python scripts/make_mcp_config.py --client <your-client>` and paste again |
| Session pointing to a dead port | Stale `outputs/session.json` | `python scripts/doctor.py` detects it; delete the file or reselect |

---

## 7. Coexistence with other Power BI MCPs

Prefixes don't clash (`pbi_*` vs `pbir_*`), so several servers can be registered at once.

**Caution:** two servers writing to the same `.pbip` don't coordinate with each other. Until Phase 1 adds locking and external-change detection, use only one per project at a time. See [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md).
