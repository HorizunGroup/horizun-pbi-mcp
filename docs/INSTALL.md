# Installing and registering Horizun PBI MCP

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
the client the 128 tools will appear. Nothing needs to be downloaded or run
separately. The runtime and verified downloads stay in the plugin's local
data, outside the repository and your projects.

Python 3.10+ is still a requirement: it's the local process that talks to
Power BI Desktop. Node 20 is only needed for the optional PBIR validator.

Reproducible guide from scratch. At the end, an MCP client should see 128 `pbi_*` tools.

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
