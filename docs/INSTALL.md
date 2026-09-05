# Installing and registering Horizun PBI MCP

## Free ChatGPT Desktop and free Claude Desktop

Both desktop clients can run the same local MCP without an API key or a paid
connector plan. The server stays on the Windows machine because it must reach
Power BI Desktop and local PBIP files.

### ChatGPT Desktop (Free)

1. Run the verified one-paste installer in the next section. It adds Horizun
   to the personal plugin marketplace at
   `%USERPROFILE%\.agents\plugins\marketplace.json`; valid existing entries
   are preserved and a changed file is backed up first.
2. Restart ChatGPT Desktop, open **Plugins**, choose **Personal**, open
   **Horizun PBI MCP**, and select **Install**.
3. Start a new chat. On first use the plugin exposes
   `pbi_install_runtime` and `pbi_install_status` while it prepares the local
   runtime. When status is `ready`, restart ChatGPT Desktop once; the complete
   `pbi_*` toolset then appears.

ChatGPT and Codex use the same plugin package and marketplace entry. The
installer does not need an OpenAI API key.

### Claude Desktop (Free)

**Before you start.** Windows; a Claude Desktop able to install MCPB extensions
with `manifest_version` **0.4**; network access on first run; and Power BI
Desktop if you want the live layer. You do **not** need Claude Code, a
preinstalled Python, or an edit to `claude_desktop_config.json`.

Verified on **Claude Desktop 1.46388.3** (Microsoft Store build): the bundle
installs from the UI, lands as `local.mcpb.horizungroup.horizun-pbi-mcp`, and
its server starts and answers real tool calls. Both `manifest_version: 0.4` and
`server.type: "uv"` — the runtime Anthropic's own packaging tool still labels
*experimental* — were accepted as declared. Older builds may not accept 0.4; if
yours refuses the file, that is the first thing to check.

1. Download
   [`horizun-pbi-mcp-2.1.0.mcpb`](https://github.com/HorizunGroup/horizun-pbi-mcp/releases/download/v2.1.0/horizun-pbi-mcp-2.1.0.mcpb)
   and check its SHA-256 against the `SHA256SUMS` published with the release.
2. Double-click the file, or in Claude Desktop open **Settings → Extensions →
   Advanced settings → Install Extension**, and approve the installation. The
   bundle is **not code-signed**, so the client may say so; the digest in
   `SHA256SUMS` is what proves which bytes you have.
3. **First run.** Start a new chat. The extension exposes only
   `pbi_install_runtime` and `pbi_install_status` while it prepares its own
   local runtime; preparation starts on its own, and `pbi_install_runtime` is
   there to restart it if it never began. Ask for `pbi_install_status` until
   `state` is `ready` — it downloads a private virtual environment, the
   Analysis Services libraries and the PBIR schemas, so on a normal connection
   allow a few minutes.
4. **Restart Claude Desktop once.** Then confirm: a new chat should offer the
   full `pbi_*` toolset (139 tools, `pbi_start_here` among them). If you still
   see only the two installer tools, the client is still holding the bootstrap
   session — reopen it.

#### If something fails

`pbi_install_status` is the diagnosis, not a progress bar. Read these fields:

| Field | What it tells you |
|---|---|
| `state` | `not_installed`, `installing`, `ready`, `failed`, `corrupt` |
| `log` | Path of the install log. A successful run leaves it empty — progress is not written there; a crash leaves its traceback in it |
| `message` | The structured reason. This, not the log, is where a handled failure explains itself |
| `data_dir` | Where everything lives: `%LOCALAPPDATA%\HorizunPbiMcp\plugin` |
| `dependencias.source` | `lock` means pinned versions verified by SHA-256 |
| `validator.state` | `failed_optional` is **not** a failed install (see below) |
| `degradacion` / `sirviendo` | Whether it fell back to the last runtime that worked |

The PBIR validator is optional and needs Node. If it reports `failed_optional`
the server is fully usable; only the extra check of report files against
Microsoft's official CLI is missing. An install is broken only when `state` is
`failed` or `corrupt`, and in both cases the file named in `log` says why.

#### What the free plan covers

Everything here. The server runs as a **local process on your machine**,
launched by Claude Desktop over stdio, which is why it can reach Power BI
Desktop and your `.pbip` files at all. Local MCP extensions are not a paid
feature. A paid plan would only matter for a *remote* connector — a server
Anthropic reaches over the network — and that model could not open your local
Power BI Desktop, so it is not an upgrade path for this tool but a different
architecture.

The `.mcpb` is built only from committed files, is included in the release's
`SHA256SUMS`, and is re-read by the release pipeline before publication. It
cannot include a developer's ignored outputs, backups, PBIX/PBIP files, or
local credentials.

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

The block is deliberately not a one-liner. `irm <url> | iex` executes whatever
the URL returns at that moment; this pins a released version, caps the download
size, verifies the **SHA-256** and only then runs the script with `&` — never
`iex`. A hash mismatch means **nothing is executed**. It is the same block as
in the README and in the `horizun-pbi-setup` skill, kept identical by a test;
the canonical copy is [`scripts/one_paste.ps1`](../scripts/one_paste.ps1).

```powershell
$ErrorActionPreference = 'Stop'
$url = 'https://github.com/HorizunGroup/horizun-pbi-mcp/releases/download/v2.1.0/horizun-pbi-mcp-instalar.ps1'
$sha = '1d92ed68b805af3dbb95614ac918008b1fe4c328a11bb8ccefba1d75cf581582'
$max = 131072
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('horizun-' + [guid]::NewGuid().ToString('N') + '.ps1')
# En que punto se quedo, para que el mensaje final diga la verdad y no una
# formula. Antes, un instalador que se ejecutaba y devolvia error terminaba
# imprimiendo "No se ejecuto nada que no coincidiera con el hash publicado":
# cierto en lo literal y enganoso en lo que la persona entiende, que es que no
# se ejecuto nada.
$fase = 'descarga'
$ejecutado = $false
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $peticion = [Net.HttpWebRequest]::Create($url)
    $peticion.UserAgent = 'horizun-pbi-mcp-one-paste'
    $peticion.Timeout = 60000
    $respuesta = $peticion.GetResponse()
    if ($respuesta.ContentLength -gt $max) {
        throw ("El servidor anuncia " + $respuesta.ContentLength + " bytes y el maximo aceptado es " + $max + ". No se descarga nada.")
    }
    $entrada = $respuesta.GetResponseStream()
    $salida = [IO.File]::Open($tmp, 'Create', 'Write', 'None')
    $total = 0
    try {
        $bloque = New-Object byte[] 8192
        while (($leidos = $entrada.Read($bloque, 0, $bloque.Length)) -gt 0) {
            $total += $leidos
            if ($total -gt $max) {
                throw ("La descarga supero " + $max + " bytes mientras bajaba. Se aborta sin ejecutar nada.")
            }
            $salida.Write($bloque, 0, $leidos)
        }
    } finally {
        $salida.Dispose(); $entrada.Dispose(); $respuesta.Dispose()
    }
    if ($total -eq 0) { throw "La descarga llego vacia. No se ejecuta nada." }
    $fase = 'verificacion'
    if ($sha -cnotmatch '^[0-9a-f]{64}$') {
        throw "El hash publicado en el bloque no es un SHA-256 de 64 hex en minusculas. No se ejecuta nada."
    }
    $flujo = [IO.File]::Open($tmp, 'Open', 'Read', 'Read')
    try {
        $algoritmo = [Security.Cryptography.SHA256]::Create()
        try { $digest = $algoritmo.ComputeHash($flujo) } finally { $algoritmo.Dispose() }
    } finally { $flujo.Dispose() }
    $real = [BitConverter]::ToString($digest).Replace('-', '').ToLowerInvariant()
    if ($real.Length -ne 64) {
        throw "No se pudo calcular el SHA-256 de lo descargado. No se ejecuta nada."
    }
    if ($real -ne $sha) {
        throw ("SHA-256 NO coincide. Esperado " + $sha + ", recibido " + $real + ". No se ejecuta nada.")
    }
    $fase = 'ejecucion'
    Write-Host ("SHA-256 verificado sobre " + $total + " bytes. Ejecutando el instalador...") -ForegroundColor Green
    $ps = [IO.Path]::Combine($PSHOME, 'powershell.exe')
    if (-not [IO.File]::Exists($ps)) {
        $ps = [IO.Path]::Combine([Environment]::SystemDirectory, 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    }
    if (-not [IO.File]::Exists($ps)) {
        throw "No se encontro Windows PowerShell. No se ejecuta nada."
    }
    & $ps -NoProfile -ExecutionPolicy Bypass -File $tmp
    $ejecutado = $true
    if ($LASTEXITCODE -ne 0) {
        throw ("El instalador termino con codigo " + $LASTEXITCODE + ".")
    }
} catch {
    Write-Host ""
    Write-Host ("[ERROR] Instalacion abortada: " + $_.Exception.Message) -ForegroundColor Red
    if ($ejecutado) {
        Write-Host "        El instalador SI llego a ejecutarse: sus bytes coincidian con el hash publicado, y fallo durante la instalacion." -ForegroundColor Red
    } else {
        Write-Host ("        No se ejecuto nada: se aborto en la fase '" + $fase + "', antes de lanzar el instalador.") -ForegroundColor Red
    }
    throw
} finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
```

It checks and installs everything at **user level**: real Python (dodging the
Microsoft Store alias that silently kills MCP servers), Git, optional Node for
the official PBIR validator, the user execution policy and the plugin
registration for ChatGPT Desktop and Claude Code. **Claude Code itself is not installed by this script** — there is
no pinned, hash-verifiable build to run, so it is detected, and if missing you
get a pointer to Anthropic's official docs instead of a remote script piped
into your shell. It is **idempotent**: if something stays pending (e.g. IT must
approve an install), fix it and paste the same block again — nothing is
repeated, nothing breaks.

**To see the plan without any of it happening**, clone the repo and run
`powershell -NoProfile -File scripts/instalar.ps1 -DryRun`. It reports detected
prerequisites, missing dependencies, planned actions and registrable clients,
and it cannot download, install, register, write a file or change the execution
policy — every effect goes through a single gate that dry-run closes.

When it prints `LISTO`, restart ChatGPT Desktop and install the plugin from the
**Personal** source. In Claude Code, the first session prepares the runtime by
itself. In either client, `pbi_install_status` shows progress; restart the
client once when it says `ready` and the complete `pbi_*` toolset appears.

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

## Direct plugin for ChatGPT, Codex and Claude Code

This is the recommended path for end users. It doesn't require a dedicated
executable installer or hand-editing MCP files:

```bash
# Codex CLI
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
# Then open `/plugins`, select the Horizun marketplace and install
# `horizun-pbi-mcp`.

# Claude Code
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

Setup starts automatically on the first session. While it progresses
you'll see `pbi_install_runtime` and `pbi_install_status`; after restarting
the client the 139 tools will appear. Nothing needs to be downloaded or run
separately. The runtime and verified downloads stay in the plugin's local
data, outside the repository and your projects.

Python 3.10+ is still a requirement: it's the local process that talks to
Power BI Desktop. Node 20 is only needed for the optional PBIR validator.

For Claude Desktop, prefer the release `.mcpb`: Claude supplies the bootstrap
runtime and no manual Python or JSON configuration is needed.

Reproducible guide from scratch. At the end, an MCP client should see 139 `pbi_*` tools.

---

## 1. Requirements

| Requirement | Why | Mandatory |
|---|---|---|
| **Windows** | Power BI Desktop only exists on Windows | For the LIVE layer. The ON-DISK layer (`.pbip`) works on any OS |
| **Python ≥ 3.10** | Tested on 3.14.3 | Yes |
| **.NET Framework 4.x** | Used by `pythonnet` (`netfx` runtime) | For the LIVE layer |
| **Power BI Desktop** | Runs the local `msmdsrv.exe` engine | For the LIVE layer |
| **Analysis Services DLLs** | ADOMD.NET + TOM. Vendored into `libs/`, no admin or GAC | Yes |
| **`comtypes`** | UI Automation, to export a `.pbip` as `.pbix` | Only for `pbi_export_pbix` / `pbi_finalize_delivery` |

---

## 2. Installation

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/fetch_libs.py
```

The second command downloads the Analysis Services DLLs to `libs/`. It doesn't require administrator permissions and doesn't touch the GAC.

### Optional: exporting `.pbip` to `.pbix`

```bash
python -m pip install "horizun-pbi-mcp[export]"
```

Only needed for `pbi_export_pbix` and `pbi_finalize_delivery`. Microsoft publishes no API to convert the format, so the tool drives Power BI Desktop's own **Save As** — and that needs UI Automation, which is COM (`comtypes`). Win32 messages are not enough: `CB_SETCURSEL` changes what the file-type dropdown *reads* without notifying the application, and Desktop goes on saving a `.pbip` project with a `.pbix` name.

Everything else — DAX, TMDL, PBIR, audits — works without it. The plugin installer tries to add it on Windows and reports the result under `export_extra`; if it isn't there, `pbi_capabilities` says so in `pbix_export` and `python scripts/doctor.py` raises it as a warning, never as a failure.

The interface is driven from a **separate process**. A blocked COM call cannot be cancelled from the inside, so the timeout is enforced by the operating system terminating that helper — no COM thread is ever left alive inside the MCP server.

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
horizun-pbi-completar
```

The second command is **not optional**. The wheel cannot ship the Analysis
Services DLLs (Microsoft binaries) or the PBIR schemas (no redistribution
permission), so a bare `pip install` leaves a server that starts, speaks MCP and
answers all 139 tools — and cannot work: the LIVE layer has nothing to talk to
the model with, and every PBIR write fails with `schema_unavailable`.
`horizun-pbi-completar` downloads both, verified by SHA-256, and
`horizun-pbi-completar --check` tells you where you stand without downloading
anything. `pbi_health_check` reports the same thing in its `completeness` block.

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
| `adomd_not_installed` / `tom_not_installed` | Missing DLLs | `horizun-pbi-completar` (installed package) or `python scripts/fetch_libs.py` (from the clone) |
| `clr_not_available` | .NET missing | Try `PBI_MCP_DOTNET_RUNTIME=coreclr` |
| `pbir_not_enabled` | The report isn't in PBIR | Save as `.pbip` with the enhanced report format |
| Visual changes don't show up | PBIR loads on open | Close and reopen Desktop |
| Report changes were lost | Desktop was open and saved over them | Edit the PBIR **with Desktop closed**. Backups are in `backups/` |
| Server starts but the client doesn't see it | Wrong path or interpreter in the config | `python scripts/make_mcp_config.py --client <your-client>` and paste again |
| Session pointing to a dead port | Stale `outputs/session.json` | `python scripts/doctor.py` detects it; delete the file or reselect |

---

## 7. Coexistence with other Power BI MCPs

Prefixes don't clash (`pbi_*` vs `pbir_*`), so several servers can be registered at once.

**Caution:** this server coordinates its own writers with project locks and
external-change detection, but a different MCP product does not participate in
those locks. Use only one writer product per project at a time. See
[CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md).
