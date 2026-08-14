# Horizun PBI MCP v1.5.5

Same tool surface as 1.5.4 (134 tools, contract untouched). One release, one
job: the install stops showing up on screen, and stops happening over and over.

## A `pip` console popped up on every start

Opening Claude — or just turning the PC on — put a console window in the user's
face with `pip install horizun-pbi-mcp` scrolling by. Two things had to be true
for that, and both were ours.

The installer is launched `DETACHED_PROCESS`, that is, **with no console of its
own**, so it survives the client restarting the MCP server mid-install. On
Windows, when a process without a console starts a console application, the
system creates a **visible** console for the child unless *that* `CreateProcess`
asks otherwise. `CREATE_NO_WINDOW` is not inherited: the parent having it means
nothing. Redirecting stdout doesn't help either — the console is assigned
whether or not there is anywhere to write.

Every subprocess of the install now goes through one helper,
`flags_sin_ventana()`, and a test walks the AST of the bootstrap scripts so the
next download script added can't quietly forget it.

One window survived that fix and was only found by watching the screen:
`venv.EnvBuilder(with_pip=True)` starts `ensurepip` in a process of its own,
with no flags we can pass it. The venv is now created by `python -m venv` as a
normal step, so it inherits the headless console like everything else.

Measured, not assumed: a from-scratch install launched exactly the way the
plugin launches it, with a watcher enumerating top-level console windows — **0
new windows**, `state: ready`, validator installed.

## The runtime rebuilt itself whenever the client renamed its folder

The install root hung off `CLAUDE_PLUGIN_DATA`, and the **name** of that folder
is the client's to choose. On one machine it went from
`horizun-pbi-mcp-horizun` to `horizun-pbi-mcp-inline` in under 15 hours. Each
rename meant a full rebuild — venv, pip, Analysis Services DLLs, 23 PBIR
schemas, 586 npm files of the report validator — and the previous ~1 GB folder
stayed behind forever, because nothing ever looked for a runtime that already
existed somewhere else.

The runtime now lives at a **stable** root that no client names:
`%LOCALAPPDATA%\HorizunPbiMcp\plugin\<version>`. Only the explicit
`HORIZUN_PBI_PLUGIN_DATA` override moves it. Alongside that:

- **Upgrading doesn't re-download what's already verified.** A new version
  seeds its runtime from the previous one (or from the pre-1.5.5 layout) before
  installing: pip upgrades the package inside the existing venv and the
  hash-verified downloads are skipped. Measured on the same machine: **14
  seconds** instead of a full rebuild.
- **User data stopped being versioned.** `outputs/` and `backups/` sit at the
  stable root and survive every upgrade. Only rebuildable content lives under
  the version folder — which is what makes deleting orphans safe.
- **Orphans are cleaned up** after a successful install: caches of other
  versions, leftovers of the old layout, and client folders left behind by a
  previous name, empty ones included. Anything the user generated in a
  condemned folder is moved to the stable root first, never deleted, and every
  removal is listed in `pbi_install_status`.

## An orphaned lock froze the plugin at `installing` forever

Killing the installer half-way — closing the PC, for instance — left an
`install.lock` that nobody owned. The next installer gave up on sight, and the
launcher only ever retried on `not_installed` or a version change, so the
status stayed `installing` permanently and no amount of restarting helped.

The lock now records its PID and is stolen when that process is gone, which
also unfreezes machines already stuck by 1.5.4 (a lock written by the old
format names nobody, so it counts as orphaned). On Windows the liveness check
opens a handle: `os.kill(pid, 0)` doesn't ask there, it **terminates**.

## One thing deliberately left without the flag

The launcher's hand-off to the real server does **not** pass
`CREATE_NO_WINDOW`, and there is a test that keeps it that way. That call
doesn't redirect stdio — those are the client's pipes — and a new console would
make the server read from the console instead, hanging the MCP handshake
forever. Found by running it, not by reading it.

## Verification

- From-scratch install launched detached, with a window watcher: 0 new console
  windows, `ready`, validator installed.
- Simulated upgrade from a 1.5.4 runtime: seeded (`heredado_de`), finished in 14
  seconds, deleted the old cache plus two stale client folders, kept a
  third-party plugin folder untouched and **moved a user backup** to the stable
  root instead of deleting it.
- Two launches with different client folder names against a ready runtime: 134
  tools both times, no reinstall, no client folder created.
- A status frozen at `installing` with a dead PID's lock: recovers by itself to
  `ready` and releases the lock.
- Full suite green: 2174 passed / 3 skipped, plus the 14 packaging tests.
