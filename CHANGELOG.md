# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Semantic versioning. **The contract of the original 34 tools is never broken.**

---

## [Unreleased]

Nothing pending.

---

## [2.1.0] — 2026-08-25

A minor bump: this batch adds five tools, and the frozen contract of the
original 34 is intact — every change below is additive.

It also carries what `2.0.2` documented. That version was prepared but its tag
and release were never created, so its two fixes ship here rather than under a
number nobody can install. Its entry is kept below as written.

`pbi_export_pbix` was verified against a real Power BI Desktop, not only
against the test double: a synthetic `.pbip` came out as a 13,939-byte `.pbix`
with `report_format='pbir'` and a data model inside, through the public
`pbi_finalize_delivery` tool, in about 17 seconds.

### Added

- **`pbi_prepare_project`, `pbi_export_pbix` and `pbi_finalize_delivery`.** A
  `.pbip` is not a deliverable: whoever receives it needs Power BI Desktop and
  the whole folder. There was no way to produce the `.pbix` from here.
  Microsoft publishes no API for that conversion, so `pbi_export_pbix`
  automates the **supported** flow — Desktop's own `File > Save As` — instead
  of stitching a zip by hand. Controls are resolved by dialog id and window
  class, never by screen coordinates; the file type is *chosen* rather than
  inherited (the default offer can be `.pbit`, a template with no data); a
  visible dialog is reported as a classified modal with a suggested action,
  never as a timeout; and the saved file is verified — existence, extension,
  size, mtime within this run, and openable by the repository's own `.pbix`
  reader — because a dialog disappearing is not a file being written. Windows
  UI automation sits behind an injectable adapter: CI uses a double, and the
  real path is a `live` test that is skipped unless `PBI_MCP_LIVE_EXPORT=1`.

  The dialog is driven **from a separate process** (`powerbi/uia_helper.py`,
  launched by `powerbi/desktop_helper.py`). Two measured reasons, not taste.
  Win32 messages do not commit the file type: `CB_SETCURSEL` changes what the
  dropdown *reads* without notifying the application, so Desktop kept saving a
  `.pbip` project under a `.pbix` name — the combo said one thing and the disk
  said another. Committing it needs UI Automation, which is COM; and importing
  COM in the server pins the thread's apartment, which is what breaks
  `pythonnet` with *«Cannot change thread mode after it is set»*. A blocked COM
  call also cannot be cancelled from the inside: the previous attempt ran it in
  a daemon thread and called `join(timeout)` a timeout, when it was only
  looking away — the thread stayed inside COM for the life of the server. Now
  the deadline is enforced by the operating system terminating the helper.
  `comtypes` ships as the optional `export` extra; without it the server works
  exactly as before and `pbi_capabilities` says so under `pbix_export`.
- **`pbi_get_power_query` and `pbi_update_power_query`.** In a `.pbip` the M
  code has no file of its own: it lives inside the TMDL, in each table's
  `partition` and in `expressions.tmdl`. There was no way to read or change it
  without hand-editing TMDL. The block is located by structure and replaced
  whole — never by regex, which breaks on the first query with an `in` inside
  a string. `dry_run` defaults to true, `expected_sha256` rejects a stale
  write, and the response separates `parse_checked` / `tmdl_load_checked` from
  `m_engine_checked` / `refresh_checked`, which are always false: no M engine
  runs outside Power BI Desktop, so nothing here can claim a query will load.
- **Secret detection and containment (`services/secret_scan.py`).** Converting
  a `.pbix` unpacks what was compressed: a token pasted into a `Web.Contents`
  header stops being invisible and lands in a folder that usually ends up in
  Git. `pbi_convert_pbix_to_pbip` now scans the report in memory — before
  staging and before opening Desktop — and the built tree before publishing.
  High-confidence findings block publication and the staging is retired
  through the existing mechanism; low-confidence ones warn. The value never
  leaves the process: findings carry the rule, the relative file, the
  approximate line, a classification and a short irreversible fingerprint. The
  same detector runs over new M before it is written.
- **Verifiable identity for Desktop instances.** `pbi_list_desktop_models` and
  `pbi_open_in_desktop` now distinguish `engine_pid` (`msmdsrv.exe`) from
  `desktop_pid` (`PBIDesktop.exe`) — they were being conflated — and add the
  window title, the document path when it can be proven, `path_match`,
  `identity_confidence` and the `identity_evidence` behind it. A `.pbip`
  leaves no file handle, so its path stays `null` instead of guessed, and an
  instance serving a different document is no longer accepted for a requested
  path just because it appeared during the launch window.
- **`pbi_audit_project(compact=true)`.** `priority` used to repeat the fifteen
  worst findings in full when they were already in `findings`. In compact mode
  each finding carries a `finding_id`, `priority` is the order as a list of
  those ids, and `groups` folds repetitions by rule with counts and a sample.
  Scores and per-domain counts are untouched, and a call without the parameter
  gets exactly what it got before.
- **`pbi_profile_data` findings now carry the column's known usage**:
  `dependency_count`, who uses it, `usage_status` and `usage_scope`. It
  changes ordering and explanation, never severity — and when the report
  cannot be inspected it says `used_by_visuals: "not_checked"` instead of
  concluding the column is unused.

### Fixed

- **The file name was never actually typed into the dialog.** The `INPUT`
  structures for `SendInput` were rebuilt on every call, so ctypes saw two
  distinct classes with the same name and refused the array with
  `incompatible types, INPUT instance instead of INPUT instance` — a message
  that names the same type twice and explains nothing. They are defined once
  now, and `SendInput`'s return value is checked: it accepts fewer events than
  it was given, without raising, when the session is locked or another process
  holds the input. Ignoring that number is how a name that never reached the
  box gets reported as typed.
- **A whole save path existed that nobody could reach.** `save_as_completo`
  had been written inside the `AdaptadorUI` `Protocol`, which nothing
  inherits, so the real adapter did not have it; the service checked
  `hasattr(...)`, found nothing, and fell back to the Win32 route that does
  not commit the type. Reading the source suggested the opposite. The Protocol
  now declares and the adapter implements, and a regression asserts the
  Protocol holds no bodies.
- **A save that was never going to happen consumed the whole operation's
  budget.** The wait for the file shared the global timeout — 900 s in the live
  test — so an empty folder was watched for a quarter of an hour before
  anything was reported. The two deadlines are now separate: `timeout` covers a
  file that appeared and is still growing, which a large model legitimately
  needs, and a shorter grace period covers it appearing at all, which Desktop
  does in seconds. If the file landed in the project's folder instead of the
  requested one, that is said explicitly — searching only those two folders by
  exact basename, never the disk.
- **The Win32 route was kept "just in case" and read as if it worked.** The
  real adapter still carried `elegir_tipo`, `escribir_ruta` and `confirmar`
  written with window messages — the exact three steps that were measured NOT
  to commit anything: `CB_SETCURSEL` changes what the dropdown reads without
  notifying the application, and `BM_CLICK` on Save closes the box without
  writing a file. Nothing called them any more, and leaving them there is part
  of why the real fix took three attempts to find: the code looked functional.
  They now refuse with `win32_does_not_commit` and point at
  `save_as_completo`. Reading the offered file types still works, because
  reading is not committing.
- **A folder with two projects picked one alphabetically, in silence.**
  `_find_pbip_file` ended in `sorted(matches)[0]`, and the fallbacks for
  `*.Report` and `*.SemanticModel` did the same. With `Antiguo.pbip` and
  `Nuevo.pbip` side by side, everything that followed — measures, pages,
  publication — went to the wrong project with the response in green. A folder
  now resolves only when it holds exactly one candidate; otherwise it fails
  with `ambiguous_pbip_project` and lists them.
- **Two projects with the same file name in different folders were treated as
  the same one.** A `.pbip` leaves no open file handle, so the only
  correlation available is the window title — and a window titled `Demo` says
  nothing about which folder it came from. When the process command line names
  a project, it is used to discard the impostor. The repository's own suite
  found this: a stray `Demo` window made an unrelated preflight test fail.
- **DAX identifiers are case-insensitive; the shared resolver was not.**
  `Cronograma[Fecha]` against a table named `CRONOGRAMA` was reported as
  `measure_broken_reference` — an ERROR, with a score penalty, for a measure
  the engine resolves without blinking. `build_index` and `resolve_reference`
  now keep normalized indexes and return the model's **canonical** name; the
  summary, dependencies, model audit and PBIR reference check all go through
  them. An ambiguous match is declared, never silently resolved.
- **`None fila(s)` in `pbi_diagnose_data`.** When the row count came back
  blank it was interpolated raw. It is not turned into zero either — zero
  asserts there are none, and the truth was that it could not be counted:
  `affected_rows` stays `null`, the sentence drops the number, and the check
  is marked partial.
- **Auto date/time tables drowned the actionable findings.** Power BI creates
  one `LocalDateTable_*` per date column, seven calculated columns each; a
  model with twenty dates produced 140 findings nobody can fix one by one.
  `pbi_analyze_model_quality` now folds them into a single informational
  finding with the counts and sorts issues by severity first.

---

## [2.0.2] — unreleased

Two defects reported from an outside installation on 2026-08-16, hours after
`2.0.1` shipped. Neither breaks anything in use; both are cases of the server
**saying something that is not true**, which is worse than staying quiet.

### Fixed

- **`pbi_health_check` reported the CLR interop as down in every healthy
  installation.** The check read `clr_available` and `runtime` from
  `diagnostics()`; those keys have never existed — the real ones are
  `runtime_loaded` and `runtime_preference`. `bool(None)` is `False`, so the
  server started with `"warnings": ["clr"]`, `status: warning` and
  `detail: null`: a permanent alarm pointing at a problem that was not there,
  and not saying which. In the same process `pbi_list_desktop_models` reported
  `runtime_loaded: true`. Two tools contradicting each other about one state.

  Renaming the key was not enough: the runtime loads **lazily**, only from
  `load_adomd()` / `load_tom()`, so a freshly started server is legitimately
  unloaded and the check would have gone from "always red" to "red until you do
  something else". There are now three states — `loaded`, `not_attempted`,
  `failed` — and only the last one warns. The runtime is **not** probed inside
  the check: `pbi_health_check` is advertised as read-only, and loading .NET
  there would be a side effect in the one tool you call to look without
  touching. The detail is never `null` again.

- **The fallback to `coreclr` never ran.** Found while reading pythonnet 3.1.0
  to fix the above. `_ensure_runtime` treated every `RuntimeError` from
  `load()` as "a runtime was already loaded". It is the other way around:
  `load()` opens with `if _LOADED: return`, so the already-loaded case never
  raises, and `RuntimeError` is what genuine failures look like (`Failed to
  create a .NET runtime (netfx)`, `No valid runtime selected`, `Failed to
  initialize Python.Runtime.dll`). The `except` turned each of those into a
  fake success — the real error resurfaced later in `clr.AddReference`, blaming
  the DLL — and, because it returned from **inside the loop**, a machine
  without .NET Framework never got to try the other runtime: it gave up
  pretending it had worked. The cause of every failed attempt is now recorded,
  which is what lets the health check tell "not attempted" from "could not".

- **The setup skill sent people to copy the one-paste block from
  `README.md`**, which stopped embedding it when it became a link to
  `docs/INSTALL.md`. The same claim lived in the canonical file's own header.
  The instruction exists to say "do not write it from memory"; an agent
  following it does not find the block and concludes it must rebuild it. The
  blocks were guarded by hash; the prose announcing them was guarded by nobody,
  and now is.

---

## [2.0.1] — 2026-08-16

**The first public release of the 2.x line.** Everything listed under `2.0.0`
below ships here; what `2.0.1` adds is the piece of the pipeline that was
missing, and without which none of it reached anyone.

### Fixed

- **The release pipeline never created a GitHub Release** (RELEASE-004). It
  built once, tested those bytes, published to PyPI and published to the MCP
  Registry — and stopped. Meanwhile the one-paste block offered in `README.md`,
  `docs/INSTALL.md` and `skills/horizun-pbi-setup/SKILL.md` downloads
  `horizun-pbi-mcp-instalar.ps1` from `releases/download/v<version>/`: **the
  installation path we hand people pointed at an asset no job ever created.**
  It was a defect of omission — every guard in `tests/test_release_pipeline.py`
  asked whether the existing jobs did something wrong, and none asked whether a
  job was missing.
  There is now a `publicar-github-release` job that depends on `build`, `test`,
  `publicar-pypi` **and** `publicar-mcp`; is the only job in the workflow with
  `contents: write` and has no OIDC token; publishes exactly the files signed in
  `SHA256SUMS`; refuses to replace an asset that already exists with different
  bytes; is idempotent on a rerun; and, **after** uploading, downloads every
  asset back to compare digests and checks that the installer's SHA-256 and
  `browser_download_url` are exactly what `scripts/downloads_manifest.json`
  declares.
- **The release and migration notes are now part of the signed artifact.**
  Published from the checkout they would have been the only bytes of the release
  nobody verified.

### Note on the `2.0.0` tag

The tag `v2.0.0` was created during a **failed publication attempt**: build and
tests passed, `publicar-pypi` failed with `invalid-publisher`, `publicar-mcp`
was skipped, and nothing was published to GitHub Releases, PyPI or the MCP
Registry. The correction is delivered as `2.0.1`.

**The tag was deleted from the remote on 2026-08-16, reversing the earlier
decision to keep it.** The original rule — a public tag may have been fetched by
third parties, so it is never moved or deleted — is a sound default, and it is
recorded here rather than quietly dropped. It was revoked deliberately, for this
one tag, on these grounds: it existed for roughly eighteen hours, nothing was
ever published under it, and the commit it pointed at (`1f0405b`) is still
reachable from `main`. Nobody can therefore find different bytes under a name
they already fetched; the most anyone loses is a dangling reference.

---

## [2.0.0] — never published (failed release attempt; superseded by 2.0.1)

**Breaking.** Three contract changes, ratified in writing before being applied —
the dossier is [`docs/audits/CONTRACT_003_RATIFICATION.md`](docs/audits/CONTRACT_003_RATIFICATION.md)
and it was written *before* the code. Still 134 tools; what changed is what they
require of the caller.

The version jumps to 2.0.0 and not 1.5.5 because **1.5.5 was never published**:
the last release that exists is 1.5.4. Expressing a contract break as a patch on
something nobody has would be dishonest twice over.

### Breaking

- **`pbi_refresh_model` and `pbi_open_and_refresh` now require
  `confirm: true`.** They were the only two of the 134 marked
  `destructiveHint` with nothing to confirm: an agent that decides by *"does it
  have a `confirm`?"* had nothing to ask about, and a refresh locks the model
  for minutes and discards whatever was in memory unsaved.
  **Migration:** add `confirm: true`. Without it you get `validation_error` and
  **nothing runs** — the layer underneath is never reached.
- **`pbi_apply_plan.confirm` now defaults to `false`** (was `true`). It was the
  only `confirm` of the 134 that came pre-opened, and a gate that comes open is
  not a gate. This is the change most likely to break existing callers, because
  omitting a defaulted parameter is the normal thing to do.
  **Migration:** pass `confirm: true` explicitly where you meant to apply.
- **`pbi_open_pbip_project` and `pbi_select_model` are no longer
  `readOnlyHint: true`.** They write *session* state — which project or model
  everything after them points at — so a client treating them as reads could
  repoint the session silently and send the next (genuinely destructive) write
  somewhere else. They are now `session_write`: `readOnlyHint: false`,
  `destructiveHint: false` — they destroy nothing of yours — and
  **`idempotentHint: true`**, which is true and is checked by opening the same
  project twice and comparing the resulting state.
  **Migration:** none in the call; a prudent client may now ask before opening.

### Added

- `docs/MIGRACION_1x_A_2.0.md`: every affected call, before and after.



### Security

- **The one-paste installer no longer executes unverified bytes.** It used to be
  `irm .../main/scripts/instalar.ps1 | iex`: two defects in one line. The branch
  means the bytes can change under the same link without anyone noticing; the
  `iex` means they run without being looked at. HTTPS fixes neither — it tells
  you *who* served the bytes, not *what* they are. The published block now
  downloads a **pinned release asset**, rejects an oversized `Content-Length`,
  caps the stream while it downloads, verifies the **SHA-256** and only then
  runs it with `&`. On any mismatch, **nothing executes** and the temp file is
  removed anyway. Proven against a local HTTP server across eleven failure
  paths, with an on-disk sentinel as the oracle rather than log inspection.
- **The block lives in exactly one place**, `scripts/one_paste.ps1`; README,
  `docs/INSTALL.md` and the setup skill embed it verbatim and a test forbids
  drift. A block maintained in four places becomes four different blocks, and
  the stale one is the one somebody pastes.
- **All GitHub Actions are pinned by full commit SHA**, each with its human
  version alongside. Dependabot (Actions + pip) and CodeQL — with a weekly run,
  because a *new* query can find an *old* defect — are added, and a root
  `SECURITY.md` describes private reporting, scope, supported versions and
  response targets.

### Added

- **`scripts/instalar.ps1 -DryRun`**: diagnoses the machine and prints the plan
  — detected prerequisites, missing dependencies, planned actions, registrable
  clients — while being unable to download, install, register, start a client,
  write a file or change the execution policy. Every effect goes through a
  single gate that dry-run closes, which is what makes "zero effects" provable
  rather than asserted.
- **Two installs a fortnight apart no longer give you two different
  products.** `install()` ran `pip install <repo>`, which **resolves from
  scratch every time**; when one machine worked and another didn't, the first
  question — what got installed on each? — had no answer, because nobody wrote
  it down. `scripts/requirements.lock` now pins all 43 transitive dependencies
  by exact version and SHA-256, `scripts/generar_lock.py` regenerates it by
  asking pip what it would install (`--dry-run --report`, installing nothing)
  and `--check` reports *what* drifted. The installer takes it as the preferred
  path: `pip install --require-hashes -r`, with the local package installed
  separately under `--no-deps` — it has no published hash, and inventing one to
  make the line look complete would have forged the only guarantee the file
  offers. **When the lock doesn't cover the interpreter it falls back to the
  ordinary resolver and says so** in the install status
  (`dependencias.source`, with the reason and the command to regenerate):
  failing the whole install over a guarantee that doesn't apply would be worse
  than the guarantee, and staying quiet would be worse still. Checked against
  real pip — two clean venvs, the lock installed in each, `pip freeze`
  identical and matching what was pinned. The offline bundle and the proxy
  runbook are the half that remains, and they need a VM with no route out.

- **Every tool now gets exercised over MCP, with a negative case
  built from its own schema.** 134 tools declared an `output_shape` and a risk
  class, but only 12 of 107 test files actually executed anything through
  `call_tool`, and nobody had counted negative cases. `docs/INVENTARIO_TOOLS.md`
  publishes the tool-by-tool inventory — MCP execution, negative case,
  annotation, `confirm`, frozen payload — and it is **generated, not written**:
  `python -m tests.inventario_tools` derives it from the running server and a
  test fails when the published file drifts. 114 tools are rejected at schema
  validation before their body runs, 10 answer a structured `ok: false` with an
  error code when there is no project, 8 have no failure mode at all and are
  executed anyway to keep that claim honest, and 2 are declared with a reason
  because they probe the real environment. What it does not prove is written
  into the document itself.

- **The DLL downloader stopped carrying its own promotion.**
  `scripts/fetch_libs.py` had `_promover_directorio` and
  `_recuperar_interrupcion` — a fixed-name `.previous`, its own recovery, its
  own compensation. It worked; the problem was that it was the **third** one.
  Three ways to promote are three different ways to end up half-done, and only
  one of them has containment tests, a versioned journal and quarantine. It now
  publishes through `lifecycle/promotion.py` **under the lifecycle lock of its
  root** — which is what was really missing: the script runs on its own, as the
  README says and as the installer invokes it, and two processes could promote
  onto the same destination at once. A promotion failure also stops surfacing as
  a Python traceback in all three downloaders: it exits 1 with `FALLO:`, which
  is what the person installing actually reads.

- **`pip install` now leaves you a command that actually exists.** The
  health check already told you what was missing and named the exact command to
  fix it — and that command was `python scripts/fetch_libs.py`, while
  **`scripts/` does not ship in the wheel**. A perfect diagnosis followed by an
  impossible instruction. Worse, a test *required* that shape and a second one
  checked the file existed in the checkout; both passed, both encoded the
  defect. The three downloaders now live in `horizun_pbi_mcp/completado/` and
  ship as **`horizun-pbi-completar`**, which downloads the Analysis Services
  DLLs and the PBIR schemas verified by SHA-256, treats the Microsoft validator
  as optional (INSTALL-002), and answers `--check` without downloading
  anything. The DLL manifest moved with the code that reads it and is declared
  as package data; the default install target is now where the server actually
  reads (`settings.libs_dir`) instead of `<checkout>/libs`. `scripts/fetch_*.py`
  remain as one-line wrappers for the plugin installer and CI. Verified on a
  clean pip install of **both wheel and sdist**: the executable is in the venv,
  `--check` exits 1 and names what is missing.

- **Build once**: `scripts/release_build.py` produces the wheel and sdist in a
  single build, runs `twine check --strict`, emits `SHA256SUMS` and a
  reproducible CycloneDX SBOM, and freezes the installer asset;
  `scripts/release_verify.py` is the gate every consumer crosses before use.

### Fixed

- **A failed upgrade cost you the 134 tools, with the previous runtime still
  intact on disk.** The launcher only ever looked at the current version: if its
  status was not `ready`, it served the bootstrap MCP with its two tools —
  install and check status — while N−1 sat there, whole and bootable. The
  fallback existed on disk and not in the code. There is now a state file at the
  data root with three independent facts (`activo`, `last_known_good`,
  `ultimo_intento`), because *the upgrade failed* and *N−1 is still serving* are
  true at the same time and used to overwrite each other. The acceptance test
  drives the real launcher over stdio and counts what a client actually receives.
- **The fallback could hand you two MCP servers on one connection.** The
  launcher ran the active runtime with the client's stdio inherited and, if it
  exited non-zero within 20 seconds, started N−1 on that same channel — on the
  grounds that it "had not written anything yet". That was never measured and
  could not be: the child writes straight to the client's stdout, so the
  launcher sees none of it. A runtime that answers `initialize` and dies two
  seconds later left the client with two `serverInfo` values and two answers for
  the same `id`, which no MCP client can detect. The timing threshold is gone:
  the handshake now happens in a separate process with its own pipes, and only a
  runtime that has already proven it speaks MCP is given the client's stdio.
  Once handed over, nothing else is started on that connection.
- **`state` stayed `ready` after the runtime was corrupted.** Serving N−1 was
  reflected in a different field, so the one a client reads to know whether this
  works kept saying yes about a runtime that no longer starts. `state` is now the
  *operational* state and reads `degraded`; the installer's own last result moved
  to `estado_instalacion` rather than being overwritten. Structural damage
  (missing interpreter or entry points) is derived on read; deeper damage
  (missing package, a server that dies mid-handshake) is found by the preflight
  and recorded under the lifecycle lock.
- **Two processes could publish schemas or the validator at the same time.**
  Both publishers called the recovery routine whose own docstring requires
  holding the lifecycle lock, and neither held it — inside `install()` the data
  root's lock covered them, but both are scripts meant to be run on their own.
  Each now takes the lock of its own component root before recovering,
  preparing, promoting or cleaning. The per-publication backup is also collected
  once published, instead of accumulating one more copy on every update — which
  for schemas meant they travelled from version to version, since seeding copies
  the whole folder.
- **The upgrade recovery trusted absolute paths written in a file.**
  `promotion.recuperar()` read `staging`, `destino` and `anterior` straight out
  of `.promotion.json`. That file lives in the data directory, so whoever can
  write it decides which folder an unattended installer renames — demonstrated
  by moving a staging directory to a sibling of the data root. The journal now
  stores only names of direct children, validated both lexically (no `..`,
  separators, absolute paths, UNC, alternate streams) and after resolution (no
  junctions or symlinks). Recovery, seeding, promotion and cleanup all moved
  inside the lifecycle lock; they used to run before it was acquired.
- **`ready` accepted any hundred tools whose name started with `pbi_`**, with
  the contract at 134. It also ignored `serverInfo` entirely and never compared
  the version the server announced with the one just prepared. Three different
  broken runtimes passed. The healthcheck now checks the packaged contract:
  exact server name, matching version, well-formed `tools/list`, and not one of
  the 134 missing. Extra tools are still fine — adding one must not turn into a
  failed install.
- **Schemas and the PBIR validator were published on top of the live
  directory** — the schemas file by file, the validator with
  `npm install --prefix <destino>`. Either one, interrupted, left old and new
  mixed. Both now stage in a sibling directory, verify, and publish with a
  rename through the same lifecycle used for the runtime.
- **The one-paste block resolved two things through the session's command
  discovery: the hash and the interpreter.** The hash used `Get-FileHash`. To be
  precise about what that was and was not — it was *not* an integrity hole: with
  `$ErrorActionPreference = 'Stop'` an unresolvable command throws, so the block
  aborted; there was never a path that executed an unverified installer. It was
  an environmental dependency in the one step you cannot skip, able to turn a
  working install into a failed one depending on the pasting session. It now
  uses `[Security.Cryptography.SHA256]`, a type the runtime resolves, and
  refuses outright if the published hash is not 64 lowercase hex. The
  interpreter did have a real consequence: `& powershell` resolves aliases and
  functions *before* the PATH, so a hijacked name would have run another program
  with the verified script as its argument. It now launches the absolute path
  from `$PSHOME`, checked to be a file first.
- **The one-paste block ended with the same sentence whether or not the
  installer ran.** «No se ejecutó nada que no coincidiera con el hash
  publicado» is literally true and misleading in what a person reads: if the
  installer downloaded, verified, ran and then failed halfway, the message still
  sounded like nothing had happened. It now tracks the phase — download,
  verification, execution — and says which one it stopped in, or states plainly
  that the installer did run and failed.
- **`py -3` was downloading and installing a Python runtime during a
  *diagnostic* probe.** On modern Windows `py` is the Python Install Manager:
  asking it for an interpreter it does not have makes it fetch one. With a clean
  `LOCALAPPDATA` a single probe left `pythoncore-3.14-64-3.14.7.zip` in the
  cache — on the empty PC, which is the one case where a dry run matters. Dry
  run now resolves Python by looking at disk, as `launch.cmd` already did.
- **The packaging tests turned every failure into a skip**, and ran in a venv
  built with `--system-site-packages` and installed with `--no-deps` — so the
  dependencies they blessed came from the developer's environment. Measured: a
  package declaring an unsatisfiable `mcp>=99,<100` was **fully green**, and one
  that could not be built at all came out **amber**. Both are red now.
- **The published artifact was not the tested one.** CI built and tested on
  Windows; the publish workflow **rebuilt** on Ubuntu with a different Python
  and different action versions, and published that. Publishing now consumes
  exactly the verified artifact and never rebuilds.
- **Publishing did not depend on a green CI.** The two publish workflows fired
  on the same tag with no `needs`, running *in parallel* with CI, and their
  `workflow_dispatch` published at the press of a button, from any branch. There
  is now a single gated release DAG; manual publication requires typing the
  exact tag, and only from a tag.

### Removed

- `.github/workflows/publish-pypi.yml` and `.github/workflows/publish-mcp.yml`,
  folded into the gated `release.yml`.

---

## [1.5.5] — 2026-08-14

The install stops showing up on screen, and stops happening over and over. No
tool changes (134, contract untouched).

### Fixed

- **A `pip` console popped up on every start.** The installer runs
  `DETACHED_PROCESS`, that is, with **no console of its own**. On Windows, when
  a process without a console starts a console application, the system creates
  a **visible** console for the child unless *that* `CreateProcess` asks
  otherwise — the parent's `CREATE_NO_WINDOW` is not inherited. So every `pip`,
  every `npm` and every download opened a window in the user's face. Every
  subprocess of the install now goes through a single helper
  (`flags_sin_ventana()`), and a test walks the AST of the bootstrap scripts so
  the next download script added can't forget it. Redirecting stdout never
  helped: the console is assigned regardless.
- **The runtime rebuilt itself whenever the client renamed its data folder.**
  The install root hung off `CLAUDE_PLUGIN_DATA`, whose *name* the client
  chooses: on one machine it went from `horizun-pbi-mcp-horizun` to
  `horizun-pbi-mcp-inline` in under 15 hours, and the whole runtime — venv,
  pip, Analysis Services DLLs, 23 PBIR schemas, 586 npm files of the validator
  — was built again from scratch, leaving the previous ~1 GB folder behind
  forever. The runtime now lives at a **stable** root
  (`%LOCALAPPDATA%\HorizunPbiMcp\plugin\<version>`), and only the explicit
  `HORIZUN_PBI_PLUGIN_DATA` override can move it.
- **An orphaned `install.lock` froze the plugin at `installing` forever.**
  Killing the installer half-way (closing the PC, for instance) left a lock
  nobody owned: the next installer gave up on sight, and the launcher only ever
  retried on `not_installed` or a version change. The lock now records its PID
  and is stolen when that process is gone — which also unfreezes machines
  already stuck by 1.5.4. On Windows the liveness check uses `OpenProcess`,
  never `os.kill(pid, 0)`: there, signal 0 doesn't ask, it **terminates**.

### Changed

- **Upgrading no longer re-downloads what's already verified.** A new version
  seeds its runtime from the previous one (or from the pre-1.5.5 layout) before
  installing: pip upgrades the package inside the existing venv and the
  hash-verified downloads are skipped.
- **User data is no longer versioned.** `outputs/` and `backups/` sit at the
  stable root and survive every upgrade. Only rebuildable content lives under
  the version folder, which is what makes deleting orphans safe.
- **Orphaned data folders are cleaned up after a successful install** — caches
  of other versions, leftovers of the old layout and client folders left behind
  by a previous name, including the empty ones. Anything the user generated in
  those folders is moved to the stable root first, never deleted, and the
  removals are listed in `pbi_install_status`.

---

## [1.5.4] — 2026-08-13

The empty-PC path, verified instead of assumed. No tool changes (134, contract
untouched).

### Fixed

- **`--scope user` could stop the install dead on a clean machine.** When a
  package doesn't publish an installer *tagged* as user-scope, winget answers
  `No applicable installer found` (0x8A150044) and gives up — even though its
  default installer would have installed into the user profile anyway. Whether
  a given package carries that tag is a fact in **someone else's** manifest,
  free to change without notice. Each install is now attempted **both ways**:
  with `--scope user` first, then without. Still no elevation anywhere: if
  something genuinely required administrator, winget fails and it is reported
  as pending, with the exact package id to hand to IT.

### Changed

- **README leads with one command for everyone.** The install no longer asks
  the reader to classify their own machine first: the same PowerShell line
  works on a fully-equipped PC and on an empty one, with the in-chat prompt
  offered afterwards as a convenience.
- The installer's closing message says that **the first launch will ask you to
  sign in to Claude** — normal, not a failed install — and how long the runtime
  takes.

## [1.5.3] — 2026-08-12

Install trust and clarity. No tool changes (134, contract untouched).

### Fixed

- **1.5.2 shipped mojibake.** Its version bump was done with PowerShell 5.1,
  where `Get-Content -Raw` reads using the ANSI codepage and `WriteAllText`
  writes UTF-8. Accented text in **both** `plugin.json` descriptions and in the
  install messages of `plugin_bootstrap.py` — text the user reads on screen and
  in the plugin marketplace — was published corrupted. No test caught it
  because every test checks *content*, never *encoding*. There is now a guard
  over the published manifests, installer text and front documentation, and it
  was verified against the actual file 1.5.2 shipped.
- **The one-paste installer could not install Claude Code without npm.** It
  depended on Node, whose MSI is usually per-machine and fails without
  administrator — exactly the empty PC the script exists to rescue. It now uses
  Anthropic's official installer first (`irm https://claude.ai/install.ps1`),
  keeps npm as a fallback, and adds `~\.local\bin` to the running process's
  PATH so the very next step finds `claude` without reopening the terminal.
- **The installer claimed success it had not checked.** It printed "plugin
  registered" unconditionally, so a dead install said goodbye in green. It now
  re-reads `claude plugin list` and reports what it actually finds.
- **False pending on the execution policy.** `Set-ExecutionPolicy` can write
  the setting *and still throw* when a more specific scope overrides it, so a
  correct install ended in yellow. The verdict now comes from re-reading the
  effective policy, not from the exception.

### Changed

- **README rewritten around installing and trusting it.** The install is two
  labelled paths (you have Claude / you have nothing), one paste each, with
  measured timings; and a new section states, point by point and checkable in
  this repository, that it needs no administrator, sends nothing anywhere,
  reaches the network only for pinned SHA-256-verified downloads, backs up
  before writing, and blocks a write it cannot prove is safe. The guided prompt
  now leads in English, with the Spanish version folded.

## [1.5.2] — 2026-08-12

Same day, second pass on the same funnel. 1.5.1 taught the launcher to reject
the Microsoft Store alias; this one teaches it that **rejecting a bad name is
not the same as picking a working interpreter**. No tool changes (134,
contract untouched).

### Fixed

- **`launch.cmd` chose an interpreter for existing, not for running.** Three
  states reproduced in a terminal, each of which passed the old presence test
  without serving:
  - an **orphaned `py.exe`** (Python uninstalled, launcher left behind): it was
    picked, died with `Python 3.x not found` and exit 103 without naming any
    remedy, and never tried the real `python` that WAS on the PATH;
  - a **Python below the 3.10 floor** declared in `pyproject.toml`: starts
    fine, then fails much later and worse, inside the server;
  - a **`.bat`/`.cmd` candidate** (pyenv-win shims, corporate wrappers):
    invoked without `call`, a batch file takes the control and never returns
    it, leaving the plugin mute inside the very file written to prevent that.

  Now a candidate is accepted only if it RUNS and meets the floor, every
  invocation (including the final launch) goes through `call`, and when none
  works the message distinguishes "no Python" from "Python too old", because
  the remedy differs.

### Added

- **Last-resort interpreter lookup** in the folders winget installs into
  (`%LOCALAPPDATA%\Programs\Python`, `%LOCALAPPDATA%\Python`, `%ProgramFiles%`),
  for the field case "I just installed it and this console doesn't see it" —
  so nobody is asked to close and reopen windows.
- **Guided install prompt in the README**, with its eight rules (plan before
  action, announce every step with an ETA, refresh PATH, drive
  `pbi_install_status` to `ready`, end any failure in a diagnosis plus the fix
  command) and, folded away for maintainers, the field-failure table each rule
  answers to.
- Four tests in `tests/test_plugin_distribution.py` that execute the launcher
  for real against controlled shims, plus a guard tying its version floor to
  `pyproject.toml`. Verified by mutation: dropping the `call` fails three,
  lowering the floor fails one.

## [1.5.1] — 2026-08-12

Installation, measured in the field the same morning: a 2h11m training session
with six users spent most of its time installing. Everything below attacks
that funnel; no tool changes (134, contract untouched).

### Added

- **One-paste installer** — `scripts/instalar.ps1`, run as
  `irm .../scripts/instalar.ps1 | iex` in a NORMAL PowerShell window. Installs
  every prerequisite at **user level** (real Python dodging the Store alias,
  Git, optional Node, execution policy, Claude Code via npm when available)
  and registers the plugin. Idempotent: fix what it marks pending and paste
  the same command again. When IT blocks winget, its output IS the ticket to
  hand over (exact user-scope package ids). ASCII-only on purpose: PS 5.1
  reads UTF-8-without-BOM with the OEM codepage and mangles accents.
- `docs/INSTALL.md` opens with the one-paste path plus a field-symptom table
  (dead plugin with no error, "Git is required", mid-install network death,
  "running scripts is disabled", "not recognized" after installing).

### Fixed

- **The Microsoft Store `python` alias killed the plugin before it could say
  anything.** The manifests declared `"command": "python"`; on machines where
  that resolves to the WindowsApps shim, the launcher — and with it
  `pbi_install_status`, the one piece that self-diagnoses — never ran. Both
  plugin manifests now launch through `scripts/launch.cmd`, which resolves a
  REAL interpreter (`py -3` first, then any PATH python that is not the
  WindowsApps shim) and, when none exists, explains the remedy via stderr.
  Unix users of the plugin path: register via `make_mcp_config.py` (the
  generic stdio path is unchanged).
- **A transient network failure no longer costs the whole runtime install.**
  The bootstrap's four download steps (PyPI, NuGet DLLs, PBIR schemas, npm
  validator) retry 3x with increasing backoff — the team has a MEASURED
  IPv6 DNS race against nuget.org/developer.microsoft.com — and the failure
  status now says that relaunching resumes from the same step
  (hash-verified downloads are never repeated).

## [1.5.0] — 2026-08-12

Torre Aurora field-report closure (134 tools, one additive). Everything below
came from two real build sessions on 2026-08-11, triaged against the current
code first — roughly half the report was already implemented; this closes the
other half. Three behaviors are deliberately STRICTER than in 1.4.0 (unknown
spec options, merge content collisions, session-restored project); see
`Changed` — automation that relied on the old lax behavior will notice.

### Added

- **`pbi_set_color_from_field`** — Power BI's "Field value" conditional
  formatting: a DAX measure returns the color and the visual applies it
  as-is. The which-form-works-in-which-visual matrix lives in the server
  (validated against the official catalog), not in anyone's memory. Hand-writing
  it had two known failure modes: one variant crashed the page render, another
  painted cards but not table cells.
- **`pbi_set_conditional_format`**: `target_column` separates the gradient's
  INPUT from the column being painted (paint `semaforo` with
  `[Puntaje promedio]`), validated against the visual's projections instead of
  silently writing a rule nobody renders; `min_value`/`mid_value`/`max_value`
  numeric anchors for the gradient stops.
- **`pbi_validate_desktop_render`**: `page` parameter (captures THAT page, not
  whatever was left active) and `fit_to_page` (default on) which forces the
  official `displayOption: FitToPage` so the capture shows the whole canvas,
  not the top third at the saved zoom. Both edit the view before opening and
  restore the files byte-for-byte afterwards.
- **`pbi_set_visual_title`**: `show=false` hides a title WITHOUT deleting its
  text or format (the empty-text workaround kept the title band occupying
  height).
- **`pbi_apply_theme`**: `patch` merges partial changes over the report's
  current theme (deep-merge for dicts, whole-list replacement) instead of
  resending the full theme.
- Spec `format.header` toggles the slicer's field header (`header.show` in the
  official catalog) — a polished dropdown no longer needs a hand patch.
- `layout_internal_void` lint: a 4-column matrix stretched to 1248px leaves a
  black hole inside its own box; only a human ever saw it. The estimate is
  deliberately generous and only fires when the box nearly doubles the
  estimated content width.

### Changed

- **Unknown spec options are now rejected loudly** with the valid list and a
  hint (`style: "dropdown"` → `format.mode`; `min_value` → conditional-format
  parameter). An accepted-but-unmaterialized option was the report's worst
  bug class: `ok: true` and a visual that ignores what was asked.
- **`pbi_apply_page_spec` merge pairs by id first** (the deterministic id from
  the same seed, or the spec author's `id`), then by signature. Two textboxes
  share a signature (no title, no fields): the spec's subtitle used to pair
  with the EXISTING title and silently replace its text. A signature match
  whose content differs is now a `page_conflict` telling the author how to
  disambiguate; `replace` mode keeps its declare-the-whole-page semantics.
- **`pbi_normalize_page_layout` executes the z-order autofix** the analyzer
  had been advertising as `auto_fix_available: true` with no tool ever running
  it: duplicated (or mixed missing) z values are reassigned as a unique
  sequence preserving the current stacking order.
- **A project restored from `session.json` is no longer reactivated
  silently.** The previous session's project becomes a candidate: the first
  tool that needs one fails with the restored path and asks for explicit
  confirmation via `pbi_open_pbip_project` (it once ran the validator against
  the previous day's `C:\Demos TorreAurora` without a word). `pbi_session_info`
  shows the pending candidate.
- `pbir_schema` now validates gradient stop anchors (`value` must be a literal
  with the `D` suffix): one stray `expr` inside a stop used to leave the heat
  map rendering nothing with every validator green.

---

## [1.4.0] — 2026-08-06

Five additive tools (133 total), zero breaking changes.

### Added

- **`pbi_export_report_content`** — exports the report CONTENT, not its
  metadata: the table behind each visual, or a query the client declares
  (`rows`/`values`/`filters`/`top_n`). Selection by `pages`, `visuals` or
  `queries`, output to `.xlsx`/`.pdf` under `outputs/content/`.
  Each visual's query is reconstructed from its fields and its PBIR filters,
  and every sheet declares which filters were applied and — the part that
  matters — which could not be. Visuals with no tabular query (text boxes,
  images, shapes) are listed with the reason instead of exported empty.
  Needs the live model, so it opens Desktop when there is none, and it
  **refuses to export a model that is open but unprocessed**: a freshly
  opened `.pbip` answers every query with zero rows and would produce a blank
  workbook without a single error. `dry_run` returns the DAX without touching
  the engine.

- **`pbi_export_excel`** — verified `.xlsx` with summary, model metadata,
  relationships, report pages/visuals, audit findings and optional read-only
  DAX rows. Prevents formula injection, does not overwrite an existing export
  and reopens the workbook before publishing it.
- **`pbi_generate_pdf_report`** — executive, technical and audit PDF reports
  with optional PNG/JPEG dashboard captures. Reopens every PDF with `pypdf`
  and reports Poppler render verification when available.
- **`pbi_sharepoint_list_folder`** — SharePoint Online folder discovery through
  Microsoft Graph v1.0, with pagination, recursion and explicit limits.
- **`pbi_sharepoint_download_folder`** — filtered, staged, all-or-nothing
  download to `outputs/sharepoint/`, with byte limits, SHA-256 and post-write
  verification.

### Added

- **Two audit rules for visuals Power BI refuses to draw.** A visual in error
  shows a banner instead of content, and nothing catches it: the PBIR schema
  accepts the JSON, the official CLI passes, and the synthesized DAX even
  returns rows — because the fault is in the *field configuration*, not the
  query. Both were found by looking at a real report, and both now fail the
  audit as `error`:
  - `report_scatter_axis_not_aggregated` — a scatter with a field in Details
    and non-aggregated X/Y renders nothing ("remove the values to show X and
    Y pairs").
  - `report_slicer_below_height_floor` — a slicer below its height floor is
    clipped in dropdown mode and shows a single scrollbarred item in list
    mode. The floor depends on the header: **76px** with it (header 28 +
    selector 32 + padding 8/8) and **48px** without (selector 32 + padding
    8/8). Both numbers are measured against the official CLI, not derived:
    feeding it the same report at varying heights, 74 fails and 76 passes
    with a header, 47 fails and 48 passes without one. A first version used
    76 unconditionally and reported nine healthy slicers — the ones that hide
    their header — as broken.

### Fixed

- **A visual with columns from two tables exported a cartesian product.**
  `SUMMARIZECOLUMNS` only applies auto-exists within one table: grouping by
  columns of two related tables with no measure crosses them. Measured
  against the engine — 20 risks by 20 mitigation measures returned 400 rows
  where the visual shows 20. The query now carries an auxiliary
  `CALCULATE(COUNTROWS(<fact table>))`, resolved from the model's
  relationships, and that column is stripped before the file is written. When
  no single fact table covers every column, the visual is declared
  non-exportable rather than exported wrong.
- **`reuse_open` never reused a `.pbip` session.** Detection relied on the
  open file descriptors, and Desktop keeps none on a `.pbip` project folder
  (verified with `open_files()`: zero files). Every `pbi_open_in_desktop` on a
  project therefore launched ANOTHER window of the same report, and
  `reuse_open=false` could not fail closed either. It now falls back to
  matching the main window title, reusing only when exactly one window
  matches.
- **`pbi_generate_pdf_report` named no object in its audit table.** Every
  finding carries `object` — the measure, column, visual or page it is about —
  and the PDF dropped it, printing seven identical `measure_possibly_unused`
  rows. It now has an `Objeto` column resolving page ids to their visible name.
  The Excel export already carried the column.
- **The same table printed raw JSON.** No audit engine emits `message`, so the
  `message or summary or evidence` fallback always landed on the evidence dict
  and rendered `{"visual_count": 13, "threshold": 12}` in a document a person
  reads. Evidence is now flattened to `visual_count: 13; threshold: 12`. Found
  by rendering the PDF and looking at it; the existing tests only counted pages.

### Security

- SharePoint uses MSAL client credentials from environment variables only.
  Secrets and access tokens never appear in tool arguments or responses.
- Graph pagination is restricted to `https://graph.microsoft.com`; site URLs
  must be SharePoint Online HTTPS URLs; remote path components are validated
  before touching disk.
- The two SharePoint tools are the only ones announcing
  `openWorldHint=true`; local export tools are classified as file-emitting.

## [1.3.0] — 2026-08-05

One new tool (128 total), zero breaking changes. Two defects found writing a
real 5D dashboard, two more found reviewing the fix for the first two, and all
of them the same shape: **the server knew something and acted as if it
didn't.** Every fix verified by mutation.

### Added

- **Custom visuals in page writing** — third-party visuals by GUID, with the
  role contract **discovered** from
  `<Report>/CustomVisuals/<GUID>/resources/<GUID>.pbiviz.json`, never
  hardcoded. Validated as strictly as a native; native contracts deliberately
  not applied to third parties. `catalog describe <GUID>` does not work for
  them, so inherited container chrome is judged against a native catalog and
  sanitized (real templates carry `dropShadow.preset='Outer'`, invalid per the
  official catalog and tolerated by Desktop).
- **`pbi_reflow_pages`** — rescales already-written pages to another system's
  canvas and recomputes the decorative text colour baked in at composition
  time. Does not recompose. `dry_run=true` by default.

### Fixed

- **The two server states could diverge silently.** `active_model` and
  `active_pbip` were never cross-checked and can point at different clients'
  files while every response looks normal. Now crossed at
  `assert_escritura_pbir`. Blocks on confirmed divergence, **never on
  `unknown`**.
- **Live measures were ephemeral and said so in a footnote.** `mode='live'`
  now returns `persisted: false` plus a warning stating the consequence, and
  the envelope escalates to `WARNING`.
- **Changing the canvas left orphans off-screen.** `merge` warns before the
  damage with the count, the ids and both ways out; applying a design system
  reports how many pages it leaves on another canvas.
- **Idempotency could authorise the same mutation twice**, four ways:
  the reservation was not atomic (now a per-`request_id` lock across threads
  and processes, plus `O_CREAT|O_EXCL`); a stale `in_flight` was reclaimed on
  age alone (no automatic reclaim — `request_outcome_unknown` with
  `safe_to_retry=false`, non-reusable `attempt_id` and compare-and-set on
  close); `safe_to_retry` was stored and never consulted; and the TTL deleted
  uncertain state, `in_flight` included.
- **Corrupt JSON was overwritten.** Idempotency records and `session.json`
  both fail closed now and are preserved byte for byte, with a recovery path.
  Nothing is renamed, moved or deleted automatically. An `OSError` while
  reading is no longer treated as absence.

### Changed

- `pbi_session_info` gains `persisted_session` (additive).
- `descartar_en_vuelo()` — explicit recovery for an uncertain outcome. Service
  function, deliberately not an MCP tool in this release.

---

## [1.2.0] — 2026-08-04

The four phases of the product vision, shipped. Six new tools (127 total);
zero breaking changes. They are not four loose features — they chain:

    port keys -> brief critical_fields -> diagnostics escalate to error

### Added

- **Intent brief** (`pbi_define_brief`, `pbi_get_brief`) — what the dashboard
  is FOR, as a versioned artifact next to the `.pbip`. **The answers belong to
  the human**: an empty `purpose` errors with an instruction to ASK, and
  `pbi_get_brief` on a project without one returns the questions to ask rather
  than an empty form. Consumed by `pbi_start_here` (shows the declared
  purpose; its absence is the project's first gap), `pbi_propose_dashboard`
  (attaches purpose, key questions and non-goals as the yardstick — no fake
  keyword matching) and `pbi_list_design_systems` (recommends from `delivery`,
  which is physical legibility, not aesthetics).

- **Content-level data diagnostics** (`pbi_diagnose_data`) — what breaks
  dashboards and no metadata sees: orphan keys falling into the relationship's
  (Blank) so totals quietly come up short (blank keys counted too), duplicated
  grain on the one side that multiplies everything on join, calendar gaps
  detected by key TYPE rather than by name, and the brief's `critical_fields`
  thresholds. **Severity is the owner's call**: what they declared critical
  escalates to `error` citing their own *why*; a critical field that no longer
  exists is a finding, not silence. Every finding carries the DAX that proves
  it and sample culprits.

- **External sources** (`pbi_add_table_from_source`) — SQL Server, PostgreSQL,
  OData, Web JSON. The M is the easy part; **credentials and privacy levels
  live in Desktop's UI and are not stored in the `.pbip`**, so the warning
  ships in every response: the query is written, the first refresh needs a
  human, and this server cannot verify the connection. Columns are declared by
  the caller — without credentials there is no schema to read, and columns are
  never invented. `Value.NativeQuery` carries `EnableFolding=true`; `web_json`
  pins culture to `en-US` because JSON writes numbers culture-free and using
  the system's is the `10527.52` → ten million bug.

- **The ecosystem port as a DATA CONTRACT** (`pbi_define_port_contract`,
  `pbi_check_contract`) — deliberately not an API bus between Revit,
  Navisworks and Project: four chained desktop apps is fragile in a way that
  doesn't get fixed. Each tool emits a normalized dataset with a shared key;
  the contract is validated against incoming files (structure only — and it
  says so: uniqueness and orphans need the full data, which is
  `pbi_diagnose_data`) and against the live model, which returns
  `suggested_critical_fields` so the port keys reach the brief without being
  typed twice.

---

## [1.1.1] — 2026-08-04

Closes the field-report queue completely (items 6, 8 and 9 were the last
three) and removes a real client's name from the repository — current state
AND full history, which was rewritten for the purpose. Two new tools
(121 total); zero breaking changes.

### Added

- **`theme_json` and `fonts` on `pbi_apply_theme`**: a complete caller-supplied
  theme written as-is (corporate typography no longer requires overwriting the
  generated file by hand), and `fonts: {title|body|callout}` mapped to the
  real `textClasses` (`body` → `label`). Replacing a theme whose on-disk
  content differs (hand-edited) now warns and points at the transaction
  backup — it used to be silently destroyed on re-apply.
- **`pbi_rename_measure`**: renames the TMDL header, the unqualified `[old]`
  DAX references in other measures, and the report's `visual.json`s, all in
  ONE transaction, then verifies by re-reading. Replacement runs only inside
  measure blocks (in a calculated column, unqualified brackets mean a COLUMN
  of its own table) and only on unqualified refs (`Tabla[old]` can be a
  homonymous column of another table); anything left — bookmarks, filters,
  qualified refs — comes back in `warnings` with its location, never
  silently. Renaming onto an existing measure or a column of the same table
  is refused up front: that exact collision passes the write and kills
  Desktop at open.
- **`pbi_close_desktop`**: the missing exit of the edit-open-look-edit cycle.
  Closes ONLY the instance serving that file, verifies process identity
  (name + start time, never bare PID), re-checks the file is no longer open
  (`verified_closed`) and requires `confirm=true` — unsaved changes die with
  the window, and in a `.pbip` that includes the session's refreshed data.

### Changed

- **Client names are gone from the repository**, current state and rewritten
  history, with a tracked-files guard (`tests/test_sin_datos_de_empresa.py`)
  that also watches for any trace of the team's internal knowledge base. The
  guard builds the forbidden literal split so it never publishes what it
  polices — which is how the name leaked the first time.
- `docs/BACKLOG.md` brought current: the `pbi_apply_plan` question is
  **decided** (the `plan_token` IS the explicit approval — a client cannot
  hold a valid one by accident, and it dies on state drift; `confirm` stays
  as documented redundancy), and R15 records `mode='auto'` as its mitigation.

---

## [1.1.0] — 2026-08-03

Everything here came out of real use, not a roadmap: five gaps hit while
building a construction-budget dashboard end to end (PR #5), and fourteen
limitations written down during a second full session — each one cost real
time before being worked around by hand. The common thread of the fixes: the
server used to KNOW the answer and not say it (a note, a hint, an error
count, a mode that always failed). One new tool (`pbi_open_and_refresh`,
118 → 119) and no breaking changes: the frozen contract holds.

### Added

- **`options` in `pbi_create_visual`**: the tool never exposed the parameter
  even though `visual_factory.build_visual` already supported it for
  shape/card visuals; now `pbi_apply_page_spec` and `pbi_create_visual` share
  the same capability.
- **Color frame for any visual** (`background_color`, `border_color`,
  `border_radius`, `background_transparency` inside `options`): it previously
  only existed to turn the frame off (`show=False`) on composition elements;
  there was no way to ask for a colored background/border on a card, a chart
  or a table without hand-writing `visualContainerObjects`. Verified against
  real shapes captured from Power BI Desktop
  (`tests/fixtures/synthetic/format_objects_corpus.json`), not just against
  the schema.
- **`references` in `pbi_validate_pbip_project`**: cross-checks every
  `Measure`/`Column` that a `visual.json` or its `filterConfig` cites against
  the real TMDL. The official validator certifies the JSON's SHAPE; it had no
  way to know whether a mistyped measure name actually exists. A report could
  pass with 0 errors and open in Desktop with a silently blank card.
- **`pbi_set_visual_filter`**: filters an ALREADY-WRITTEN visual without
  hand-editing `filterConfig`. `pbip.filter_builder` (filter alias, typed
  values, stable name) had existed for a while with no tool exposing it for
  an existing visual, only for new specs.
- **`pbi_add_table_from_file` reads HTML disguised as `.xls`**: a common ERP
  export pattern (`Excel.Workbook` fails outright on these files). The
  `.xls` extension is no longer taken at face value: the real file signature
  is sniffed (OLE2 is rejected with a clear message; ZIP is read as
  `.xlsx`; anything else is profiled as an HTML table). Repeats a `colspan`
  cell's value across every column it spans, detects the declared encoding
  (`<meta charset>`), only promotes headers if the table actually uses
  `<th>`, and fixes column names by position in the M query
  (`Table.FromRows` + `Table.ToRows`) instead of trusting how `Web.Page`
  names them at refresh time — that naming isn't predictable from Python
  when there is no `<th>`.

- **Risk class declared to the client (`annotations`)**: the 118 tools reached
  the client with no metadata at all, so `pbi_list_measures` and
  `pbi_delete_page` were indistinguishable — an MCP client could neither
  auto-allow a read nor warn differently before a deletion. Each tool now
  declares its class in `tools/risk.py` (51 strictly read-only, 9 that emit a
  file into `outputs/`, 2 that open Power BI Desktop, 47 reversible writes, 8
  destructive and 1 irreversible), translated into `readOnlyHint`,
  `destructiveHint`, `idempotentHint` and `openWorldHint`.

  The table is written by hand **on purpose**, because erring on the easy side
  — marking as read-only something that writes — is exactly the mistake a
  client would turn into "this can run without asking".
  `tests/test_tool_annotations.py` contrasts every entry against AST evidence
  from the code: a tool that calls `guard_mutation` cannot be declared a read,
  a `read_only` one cannot reach `atomic_write_text`, a destructive one must
  take `confirm`, and an unclassified tool breaks the suite instead of
  silently defaulting. `annotations_for()` fails closed: unknown means
  destructive.

- **`detail` and `tables` in `pbi_list_tables` and `pbi_list_measures`**: both
  returned the entire inventory always, with no way to ask for less. Measured
  on a real seven-table project, `pbi_list_tables` came to ~28,000 characters
  (~7,000 tokens) in a single response; a forty-table corporate model eats a
  large part of the context window before any work happens.
  `detail='summary'` replaces the column list with its count (−98% in that
  project) and drops the DAX expression from measures. `tables=[...]` narrows
  to specific tables and **fails with the real names in view** when one
  doesn't exist, instead of returning an empty list that reads as "the model
  is empty". The default stays `full`: the contract is frozen and nobody's
  existing call changes behavior.

- **Header-row autodetection and `skip_rows` in `pbi_add_table_from_file`**:
  the most common ERP export pattern — row 1 holds the report title and six
  empty cells, the real header sits in row 2 — used to die with "6 unnamed
  column(s) in the header row" and force hand-writing the M partition and the
  TMDL. Now the first plausible header row (no gaps, no duplicate names,
  spanning the table's full width) is detected among the first ten, the choice
  is announced in `warnings` with the parameter to override it, and — the part
  that matters — **the generated M query skips the same rows**, otherwise the
  TMDL would describe one set of columns and the refresh would load another.
  Two defects found by the tests, not by reasoning: the CSV delimiter was
  sniffed only on row 1 (a title row has no separators, so it always fell back
  to comma), and a single-cell title row passed the heuristic — hence the
  full-width requirement.

- **`format` block per visual** (spec and `pbi_create_visual`): slicer `mode`
  (Dropdown/List/Between…), `dataLabels`, `legend`, `legendPosition` — the
  things that used to require hand-writing `objects` inside each
  `visual.json`. The vocabulary is short on purpose: every entry is checked
  against the official catalog, the written paths are declared to the format
  oracle (it only checks what is declared — undeclared paths pass unseen,
  which is how invalid `objects` slipped in historically), and an unknown key
  **fails** instead of being ignored: a format that is asked for and not
  applied leaves the report different from what was designed, silently.
  Plus **incremental z-order**: every visual came out with `z: 0` and then
  `pbi_detect_layout_issues` flagged `layout_z_order_duplicated` — the tool
  generated the very problem its own auditor reported. Hand-set `z` values
  are respected.

- **`mode='auto'` on the six dual tools**: the historical default `live`
  requires Desktop open, writing the model requires it closed, and `both` is
  blocked — so in the build-from-scratch flow the default always failed.
  `auto` resolves against the real state inside the same precondition that
  blocks `both` (i.e., before any effect): live session → `live`; project on
  disk and Desktop closed → `pbip`; both possible → `live`; neither → an
  error that names the exact tool to run. The default itself is untouched:
  changing a default breaks the frozen contract.

- **`rows_by_table` in `pbi_refresh_model`**: a refresh can finish "ok" and
  have loaded ZERO rows (credentials returning an empty set, a date filter
  reaching nothing, a source that changed schema). Row counts are read back
  from the model after the refresh; zero-row tables are additionally warned.
  If counting fails, the response says so — inventing a zero would be worse
  than not counting, because zero is precisely the alarm signal. And the new
  **`pbi_open_and_refresh`** collapses the real working sequence (a freshly
  opened `.pbip` has no data) into one call; on refresh failure the window is
  deliberately left open, because closing it would destroy exactly the
  context needed to see why.

### Changed

- **Everything now lives under a single package, `horizun_pbi_mcp`**: the wheel
  used to install **ten** top-level names into `site-packages` — `config`,
  `server`, `services`, `tools`, `utils`, `pbip`, `powerbi`, `reporting`,
  `logging_config` and `branding`. Four of those are among the most common
  names in Python: in any environment where another package — or the user's own
  script — did `import config`, one of the two won and the other broke, in
  whichever direction that day. Published on PyPI that stops being our problem
  and becomes the problem of whoever installs it, and it can't be fixed later
  without breaking everyone who already has it.

  `test_el_wheel_solo_ocupa_un_nombre_de_primer_nivel` checks it against the
  **built wheel**, not against `pyproject.toml`: what matters is what lands in
  `site-packages`. It immediately earned its keep by catching a stale `build/`
  directory that was poisoning the wheel with both layouts at once.

  The command doesn't change (`horizun-pbi-mcp`). Launching from a clone is now
  `python -m horizun_pbi_mcp.server` with `PYTHONPATH=<repo>/src`: running the
  file directly puts *its own* directory on `sys.path`, not `src/`, so
  `import horizun_pbi_mcp` doesn't resolve on a clean clone. On a developer
  machine it appears to work anyway, because an editable install leaves a
  `.pth` pointing at `src/` — exactly the class of failure that only shows up
  on someone else's machine. `scripts/make_mcp_config.py` now emits the form
  that works in both.

- **`outputs/` and `backups/` no longer default inside the library tree**: they
  were resolved relative to the *repository* root, computed from where
  `config.py` sat. That works from a clone; installed with `pip`, `config.py`
  lives in `site-packages/horizun_pbi_mcp/`, so that "root" was the virtualenv's
  library tree — the user's Power BI project backups landed in
  `<venv>/Lib/site-packages/backups` and vanished on the next reinstall. A backup
  that deletes itself is not a backup.

  `data_root()` now tells the two cases apart: from a clone the paths are
  **exactly what they were** (nobody has to migrate anything); installed, it
  uses the OS user-data directory (`%LOCALAPPDATA%`, or `XDG_DATA_HOME` /
  `~/.local/share`). Environment variables still win over both.

### Fixed

- **The server died at startup in an environment with no user profile**: found
  while making the change above. `Path.home()` *raises* `RuntimeError` when
  there is no `USERPROFILE` or `HOME` — which is what an MCP server launched as
  a service, or by a client that scrubs the environment, actually gets. The
  exception killed the process before the first protocol message: the worst way
  to fail, with no visible trace for whoever configured it. Caught by the
  packaging test that launches the installed server with a deliberately emptied
  environment; `data_root()` now never raises.

- **Intermittent test in `test_hide_columns_bulk.py`**: the live-mode tests
  install a fake model on port 1 and rely on the identity certified by
  `set_active_model()` — which **expires after one second** by design
  (`Session._MODEL_VERIFICATION_TTL_SECONDS`, so the server never trusts
  indefinitely that Desktop is still alive). If more than a second passed
  between that line and the operation — which happens in a loaded full-suite
  run and never when running the file on its own — port 1 was re-verified,
  nobody was listening, and `StaleSessionError` came out. A failure that
  appeared and vanished depending on how busy the machine was.

  Fixed by removing the clock from the equation, not by raising the TTL:
  the fixture certifies the session identity explicitly, which is what those
  tests assume anyway. Freshness itself is still exercised, untouched, in
  `tests/test_session_freshness.py`. Verified by reproducing the failure with a
  1.3 s delay and confirming the same case passes afterwards.

- **The contract didn't compare `annotations`**: `contract_utils` recorded
  them in the golden snapshot but `diff_snapshots` never looked at them, so a
  tool's risk class could change — or disappear — without the frozen contract
  saying a word. Now it's compared with the criterion that matters: becoming
  *less* cautious (gaining `readOnlyHint`, losing `destructiveHint`, losing
  the annotations entirely) is a **break**; becoming more cautious is a
  compatible change.

- **The official validator reported errors without saying which**: after two
  hand-edited `visual.json` files, every response carried
  `{"errors": 8, "preexisting_diagnostics": 8}` and never listed a single
  one — no rule, no file, no JSON path, no message. Eight unreadable errors
  are eight uncorrectable errors. Two distinct holes: `to_envelope()` didn't
  include `diagnostics` at all, and the before/after comparison returned
  preexisting findings as a bare count (they don't block, so nobody ever
  listed them — they were exactly those eight). The parser also used to throw
  away the CLI's human message, which is usually the only thing that says
  what's wrong; it is now kept, redacted like everything from an external
  process, and deliberately excluded from the comparison key so a CLI update
  doesn't make old defects look new.

- **Three claims the server made that weren't true**: the refresh note said
  "save in Desktop to persist" — false for `.pbip`, which stores definition,
  not data (the note now depends on the actual project format, and when the
  format is unknown it explains the difference instead of asserting either);
  theme presets defined data-label color and size but never turned them on
  (`"show": true` was missing, so charts shipped without numbers); and
  `pbi_page_building_blocks` described a spec shape the validator rejects —
  it now returns `example_spec`, a minimal spec the test runs through
  `validate_schema()` rather than trusting the prose.

- **`textbox` and `image` were announced but unusable**: the error said
  "needs 'text'" without saying WHERE, so `fields` and the visual root were
  tried and failed identically — a page title had to be faked with cards. The
  errors now name `options.text` / `options.resource` and carry a complete
  example in `details`. The dead `MODE_NOTE` constant (a second source of
  truth about `mode` that no client ever read) was removed instead of left to
  drift.

---

## [1.0.1] — 2026-08-02

### Fixed

- The official oracle now also checks visuals that only contain
  `visualContainerObjects`; it no longer falls back to the partial snapshot in that case.
- Empty format expressions (`expr: {}`) are rejected before writing.
- Downgrading to an earlier PBIR schema is limited to versions the
  manifest expressly identifies as not published by Microsoft.
- An already-open PBIP session can be reused without first validating a
  different or incomplete copy of the model saved on disk.

---

---

## [1.0.0] — 2026-08-02

First stable release of the official repository. **117 tools, 1542 tests
passed and 3 skipped due to documented external conditions.**

### Includes

- Pre-validation of TMDL/TOM before opening Power BI Desktop, which blocks
  name collisions and avoids the generic Frown for invalid projects.
- Structural oracle for the managed properties of `objects`,
  role and type validation against the official catalog, and safe capture of
  Desktop's exact window.
- Atomic transactions, backups, journals, rollback, and an MCP contract
  compatible with the original 34 tools.

### Published limits

- Full visual equivalence of `objects` still requires rendered
  inspection for combinations not covered by the oracle.
- `mode="both"` remains blocked due to the incompatibility between open
  Desktop and safe PBIP writing; the two unpublished Microsoft schemas
  remain an upstream limitation.

---

## [1.0.0-rc.11] — 2026-08-01

**116 tools, 1262 tests.**

Three defects found running the five-stage release checklist for the
**first time** on the published tag, plus a second pass of "open and look."

### Fixed

- **`requirements.txt` had diverged from `pyproject.toml` on the six
  dependencies.** The README offers `pip install -r requirements.txt` as
  the first option, and that file said `mcp>=1.10` **with no cap**: a clean
  install pulled in `mcp` 2.0.0 —where `mcp.server.fastmcp` no longer
  exists— and **the server didn't even get to import**. `jsonschema` and
  `referencing` were missing entirely, so every PBIR write would have
  failed with `schema_unavailable`.

  None of the 1255 tests saw it, because they all run on the development
  environment, which was already fine.

- **`doctor.py` checked three of the six dependencies.** An install missing
  `jsonschema` reported "Python dependencies: OK" and then every write
  failed. A diagnostic that doesn't look at what matters is worse than not
  having one.

- **`pbi_create_measure` let through measures that prevent the project from
  opening.** A measure can't be named the same as a column in its table, and
  its name is unique across the **entire** model, not per table. The TMDL
  parser swallows both cases; the engine rejects them on load. Confirmed by
  opening it: Power BI leaves an **"Untitled" window with an empty model**
  and says it can't create the measure. The lint had known both rules all
  along; the writer just wasn't consulting them.

### Documentation

- The checklist declared an **obsolete** exception —`filters`/`interactions`
  rejected, when they've worked since rc.9— and **didn't declare the one
  that does exist**: no schema validates a visual's `objects` block
  (`additionalProperties: {}`), so the only detector is opening it and looking.
- **`docs/BACKLOG.md`** — what remains open, with evidence and how it's
  checked. Eight points, ordered by what hurts the most.

---

## [1.0.0-rc.10] — 2026-08-01

**116 tools, 1255 tests.**

Three defects that **can't be seen either by opening the file or by
validating it**: only by looking at the screen. All three passed Microsoft's
official validator with zero errors.

### Fixed

- **Conditional formatting painted nothing.** The rule was written as
  `{"solid": <expression>}`, missing the `color` level that every other
  PBIR color has. The official schema declares that part as
  `additionalProperties: {}` —accepting literally anything— so
  Microsoft's CLI gave it the green light and Power BI simply didn't
  color anything. Discovered by opening the report and seeing an
  uncolored table.

- **Coloring a second measure erased the first.** Any block with that
  property was replaced, without checking which field it pointed to: in a
  matrix with several metrics, only the last one ended up colored. Now each
  rule is scoped to its field with `selector.metadata` —which the schema
  describes as *"defines the scope to a specific field"*—, and only the
  block for the same field gets replaced. The known workaround, making the
  metrics dynamic on rows, is no longer needed.

- **A page title could end up invisible.** A report allows **one**
  theme, but `pbi_compose_page` was embedding the page system's own color.
  Composing with `report` on a report themed with `room` wrote the
  title in `#0B0B0B` on a `#1A1A19` background: 1.02:1 contrast. Color now
  comes from whatever theme the report has applied; geometry still comes
  from the page. There's a WCAG contrast test that catches this without
  opening anything.

- **The KPI number was the smallest text on the page.** In `room`
  —a 1920×1080 canvas meant to be read from four meters away— the KPI
  came out at the default size. Each system now declares its own KPI size
  (44pt in `room`, 28pt elsewhere) and the category label is turned off,
  since it repeated the card's title and came out bigger than the actual
  data.

### Verified by opening it

A project was generated with real data, opened in Power BI Desktop, and
all four pages were reviewed. That's what found the four defects above: the
suite was green and so was the official validator.

---

## [1.0.0-rc.9] — 2026-08-01

**116 tools, 1233 tests.**

Six defects no validator of our own could see, and the two gaps left
between having the pieces and knowing how to use them.

### Added

- **Design layer** (`pbi_list_design_systems`, `pbi_apply_design_system`,
  `pbi_compose_page`). There were two halves that didn't talk to each other:
  the theme knew about color and typography and nothing about where each
  thing goes; the layout engine placed things with `ceil(sqrt(n))` without
  knowing the background color. Between the two there was no grid, no
  constant margins, no title band. The result showed: correct pages with no
  design sense.

  A **design system** owns both halves: which theme it draws color from
  —from the ones already verified against color blindness, not a new
  palette—, what 12-column grid everything is placed on, what height each
  band has, and what size each text level is. Three systems, each solving a
  different scenario: `room` (1920×1080, read from four meters away),
  `report` (1280×720, exported to PDF) and `focus` (the saturated color
  reserved for the traffic-light indicator).

  `pbi_compose_page` translates intent —"a title, four indicators, one
  hero chart and two supporting ones"— into a placed page. The
  composition is rigid on purpose: consistency between pages comes from
  none of them being able to invent its own order. And if something doesn't
  fit **it says so with the math done**, instead of shrinking it until it's
  unreadable.

- **`pbi_start_here`** — an entry point for 116 tools. A hundred and
  sixteen well-named tools are still a hundred and sixteen tools: the
  catalog was complete and the path wasn't there. This one looks at the real
  state —whether there's a project, whether it has a model or just a
  report, whether it's empty, whether Power BI Desktop has it open and is
  blocking TMDL writes— and answers with three or four concrete steps, each
  with **why** it matters right now. A step with no reason is an order, and
  an order can't be skipped with judgment.

  It counts visuals, not just pages: a freshly created project comes with
  one empty page, and telling someone who has nothing yet "you already have
  one" is the kind of answer that makes you distrust the rest.

- **`tests/test_generadores_abren.py`** — the test that was missing, and
  the one that found everything above. It builds a `.pbip` with the
  **real** generators (skeleton, tables from file, measures, theme, all nine
  visual types with data, filters, interactions and bookmarks) and asks the
  two real oracles whether it opens.

  It was verified by mutation: reverting each fix, the test fails and
  **names the culprit line** (`ROLE_MAP['cardVisual']['values'] = 'Values'`).

  The ones needing the DLLs and Node are marked `abre` and skip themselves;
  the role contract, the interaction-type one —anchored to the cached
  official schema— and the round trip need nothing and run in CI.

### Fixed

- **The tool catalog lied about its own size.** It advertised 101 with 112
  registered, and its block table added up to a third number. Now the
  counts come from the constants the suite verifies, and there's a test
  that keeps it in sync.

And six defects of the same lineage: the server wrote something, showed it
to a validator **of its own**, and that validator said yes. None of the
1169 tests saw them, because the correct shape was defined by the same code
being tested. They were found by asking the only two judges that aren't
ours: `TmdlSerializer` (the code Power BI uses to read the model) and the
official CLI `@microsoft/powerbi-report-authoring-cli`.

- **A visual's fields were silently dropped if the role didn't match case.**
  The role was looked up with `fields.get(role)`, exact. Writing
  `{"Values": [...]}` —which is the name that appears **in the
  `visual.json` itself** and what `pbi_list_visuals` returns— didn't match
  the `values` key, and the visual was written with no data at all. No
  error. The report opens and paints a blank card, which is worse than not
  opening: nobody goes looking for a failure that never happened. Now the
  role is recognized however it's written.

- **A misspelled role next to a good one disappeared without even a
  warning.** `{"category": [...], "valeus": [...]}` produced a chart with
  an axis and no bars. Now a role that visual type doesn't have is
  **rejected**, with the list of valid ones.

- **`cardVisual` declared the role `Values`; PBIR requires `Data`.** The
  type was advertised as supported and **always** produced an invalid
  report (`PBIR_ROLE_UNKNOWN` plus `PBIR_ROLE_REQUIRED_MISSING`). The full
  role map was checked one by one against the official validator instead of
  being guessed.

- **The server's own reader and writer didn't understand each other.**
  `pbi_list_visuals` returns roles with the PBIR name (`Category`, `Y`) and
  each field as an object; the generator expected logical roles and
  strings. Reading a page to make a similar one —the most natural flow
  there is— failed, and if someone extracted the `ref` by hand, the visual
  came out empty. Both forms are now accepted.

- **`interactions` was declared, validated, and useless.** It references
  visuals by id, and ids are generated by the compiler: whoever writes the
  spec can't know them. Every generator in the repository passed it `[]`,
  which is why nobody found the next defect. Now each visual can be
  targeted by its position, by a spec-level `id`, or by its title.

- **Two of the three interaction types didn't exist in PBIR.**
  `INTERACCIONES` said `("NoFilter", "Filter", "Highlight")`. The official
  `page/2.1.0` schema says `Default`, `DataFilter`, `HighlightFilter` and
  `NoFilter`. `Filter` and `Highlight` produced a page the schema rejects,
  and `Default` wasn't offered. The old names still work as aliases.

- **A `live` test crashed instead of skipping.** The `skipif` condition is
  evaluated at collection time and the body looked up the instance again:
  if Power BI Desktop closed between the two —in a four-minute suite, it
  happens— a bare `StopIteration` came out.

---

## [1.0.0-rc.8] — 2026-08-01

**112 tools, 1169 tests.**

Three defects only visible by **opening** the report. None is detected by a
schema validator: the JSON is correct in all three cases.

### Fixed

- **The skeleton generated reports Power BI refused to open.** A report
  needs a *resolved* base theme, and that's four things that go together or
  not at all: the `themeCollection` declaration, **`reportVersionAtImport`
  inside it**, the `resourcePackages` entry, and the file on disk. All were
  missing. Power BI says so literally —"The required property
  'reportVersionAtImport' was not included"— but only on open.

  The base theme is now **generated by the MCP** (`HorizunBase`) instead of
  copying Microsoft's: vendoring `CY26SU05.json` in an Apache-2.0
  repository isn't ours to do. Neutral palette on purpose; the actual
  brand identity is applied with `pbi_apply_theme`.

- **`title` was being printed on the canvas.** In a spec, `title`
  identifies the visual; on a composition element it's not a label anyone
  wants to see. It came out as "Title" over a cover page's own title, and
  would have come out as "Acme Logo" over a logo. Decorative elements
  now only show it with `show_title: true`.

- **And the reverse: asking for a title on a card didn't show it.** The
  text was written but not `show`, and the default for a card is *hidden*.
  A label was requested, nothing failed, and there was no label on screen.

### Added

- **Automatic minimum height for text.** Below the floor the font size
  requires, Power BI adds a scrollbar and cuts off the text. The official
  validator's formula is applied —`max(18, ceil(pt × 25/16)) + padding`—,
  corrected upward, and **flagged**: whoever composes a page shouldn't need
  to know the formula.
- **Card formatting from the spec**: `value_font_size`, `bold_value`,
  `value_color` and `show_category_label`, so the number carries more
  weight than its label and the same text isn't repeated above and below.
  With no options, nothing is touched: no formatting is invented that
  nobody asked for.

---

## [1.0.0-rc.7] — 2026-08-01

**112 tools, 1157 tests.** Fixes a `pbi_create_pbip_project` that generated
projects Power BI Desktop wouldn't open.

### Fixed

**The skeleton was missing `.platform` and `definition/version.json`.**
Without them the TMDL parses, the internal validator says everything's
fine, and Desktop opens an "Untitled" window with an empty model: it
neither loads nor explains why. It surfaced by opening the freshly created
project, not in the tests — the model was correct; what was missing was on
the report side, which `pbi_validate_tmdl` doesn't look at.

Fixed at the root, not just by adding the two files: **the generator now
runs the report it writes through Microsoft's official validator** and
aborts if there are errors. Generating a project that won't open is worse
than not generating it. If the CLI isn't installed, it says so
(`report_validation.checked: false`) instead of assuming it's fine.

Each artifact carries its own `logicalId`: two artifacts can't share an
identity.

### Verified end to end

From two file paths to a model that opens, with no hand-written TMDL:
`PB5-ERP_COSTOS_REALES.csv` (449 rows, sum **$1,031,062.23**, matching to
the cent an independent calculation) and `PB5-EDI-CRONOGRAMA.xlsx` (20
columns, dates included). Valid TMDL, report **`passed` with zero
diagnostics**, and opened in Desktop.

---

## [1.0.0-rc.6] — 2026-08-01

**112 tools, 1155 tests**, contract frozen (everything new is additive).

This release comes out of a real case: building two dashboards and breaking
the project six times in a row, discovering by hand what the MCP should have
said. The thread tying all of this together is no longer using Power BI
Desktop as the error detector — it arrives at the end, once already
delivered.

### Fixed — a table that got created and didn't exist

**`pbi_create_calculated_table` wrote the table's file but didn't declare it
in `model.tmdl`.** Without the `ref table <name>` line, the table is on disk
and **isn't part of the model**: the `.tmdl` looks perfect, the project opens
without complaint, and anything using it —a measure, a visual— shows up
broken without saying why.

It was detected while writing the end-to-end test from the previous point,
not by using the tool: exactly the kind of failure that doesn't manifest
until someone opens the report and sees an empty page.

Fixed in three places, because one alone isn't enough:

- `pbi_create_calculated_table` and `pbi_add_table_from_file` now declare
  the table when creating it, in the same operation.
- The validator gains two rules: **`tmdl_table_not_referenced`** (there's a
  file and no declaration) and **`tmdl_ref_table_missing`** (there's a
  declaration and no file). Both are errors, not warnings.
- The `sample_pbip` fixture didn't declare its table, so it didn't
  represent a real `.pbip` and let exactly this failure slip through. Now
  it does.

### Added

- **`pbi_create_pbip_project`**: creates an empty but valid `.pbip` project
  and leaves it active. It's what was missing to build a dashboard **from
  file paths alone**: create the project, load the data into it, and
  compose the pages without opening Power BI Desktop until the end. It
  writes the minimum Power BI accepts, with the report↔model reference in a
  **relative** path —an absolute one would tie the project to the machine
  where it was created— and with one page, since a report with none won't
  open.

  It doesn't declare `sourceQueryCulture` on purpose: culture is fixed per
  query, which is the only thing that doesn't force an assumption about how
  each source writes decimals.

- **`pbi_add_table_from_file`**: loads a file into the model following the
  same steps a person would in Power Query —open, promote headers, change
  types, load— and with the step names Power BI uses in Spanish
  (`Origen`, `Encabezados promovidos`, `Tipo cambiado`), so the query can
  still be opened and edited in the editor without looking out of place.
  Accepts `.csv`, `.txt`, `.tsv`, `.xlsx`, `.xlsm` and `.json` **with no new
  dependencies**: an `.xlsx` is read as what it is, a zip with XML inside.

  Three decisions that avoid, by construction, the failures of writing M
  by hand:

  - **Culture is inferred from the file**, looking at how it writes
    decimals, and always emitted explicitly. Against the real CSV that
    motivated all of this, it gets it right the first time (`.` → `en-US`);
    doing it by hand cost a failed refresh and a contrast against the
    source to figure it out.
  - **Excel dates are detected by their format**, not their value. Excel
    stores `45715` and, separately, a `numFmt` saying it's a date; without
    checking it, a date gets declared as an integer and the load blows up.
  - **What gets written is validated before it's committed.** If the
    generated TMDL didn't pass `pbi_validate_tmdl`, it aborts. Automating
    the mistake would be worse than making it by hand.

  On the real 20-column schedule it gets all 20 right, including two that
  look like dates and aren't because they mix in text (`NOD`): it leaves
  them as text instead of forcing them.

- **`pbi_validate_tmdl`**: checks whether a TMDL model will open, without
  opening Power BI Desktop. Two layers: a static lint in pure Python —works
  without the Analysis Services DLLs— and, if available, a parse with
  `TmdlSerializer`, **the same serializer Power BI uses to open the
  project**. Each finding carries a rule, severity, file and line.
- **`pbi_open_in_desktop`**: opens a `.pbix` or `.pbip`, waits for the local
  engine to serve the model, identifies which instance corresponds to it —
  the port is dynamic— and leaves it as the active model. Reuses the
  session if the file was already open and never closes a user's window.
  Closes the work loop: it's now possible to check that a project **really
  opens** without asking anyone.

### Fixed

- **`pbi_validate_pbip_project` said `valid: true` for projects Power BI
  Desktop refused to open.** It only checked that the files existed; it
  never looked inside the TMDL. In a real session it returned `valid: true`
  five times in a row while Desktop aborted loading, so Desktop ended up
  being the only available error detector: expensive and late. It now
  incorporates real validation and adds the `tmdl` block to the response.
  It only invalidates when it **could** check and it came out wrong: if it
  couldn't be inspected, it says so.

### The five traps now detected

They came out of breaking a real project five times in a row:

1. **A table's property after its children.** TMDL requires an object's
   properties to come before its measures and columns. Inserting measures
   right below `table X` orphans whatever came after. Power BI aborts with
   "invalid indentation detected."
2. **A `///` comment above a relationship.** It gets serialized as
   `description`, and `SingleColumnRelationship` has no such property.
3. **A measure with the same name as a column in its table.** The parser
   accepts it; the engine rejects it when creating the database. Only
   visible on open.
4. **A duplicate measure name across tables.** In a tabular model the
   measure name is global, not per table.
5. **`Table.TransformColumnTypes` with no explicit culture** over a text
   source, with a non-invariant `sourceQueryCulture`. It's the most
   dangerous one because **it produces no error at all**: a CSV with a
   decimal point gets read as a thousands separator and the totals come out
   inflated. The report opens, renders, and lies.

Warning 5 is only emitted when the source delivers text (`Csv.Document`,
`Json.Document`…). Excel and databases return already-typed values: there,
culture changes nothing, and warning about it would be noise.

### Found by running the validator over the team's 23 projects

Three classes of project the validator handled poorly, and one that was
already broken:

- **Report-only `.pbip`** (live connection to a published dataset, or
  converted with `include_model=false`). It's legitimate and has no TMDL to
  validate. It used to come out as a broken path; now it's explained for
  what it is (`tmdl_report_only_project`), which isn't the same as a
  failure.
- **Models in `model.bim` format** (TMSL/JSON): the default format of a
  `.pbip` without the TMDL preview, i.e. **most of them**. They used to go
  unevaluated. Now they're normalized to the same shape and the semantic
  checks apply, since those don't depend on the format. The structural ones
  don't apply: there's no indentation to break in a JSON.
- **`create_calculated_table` silently lost the column type.** It only read
  `data_type`; with `dataType` —which is how the property is named in TMDL
  and in the tool's JSON schema— it fell back to the default `string`. A
  numeric column got written as text and aggregations quietly stopped
  working. Now both spellings are accepted and **an unknown key is
  rejected** instead of degrading the type: a typo can't cost a table.

Sweep result: 23 of 23 projects evaluated, **zero errors**, a single
repeated warning (`tmdl_transform_without_culture` in `PowerBIMTemplate`,
which reads from `Json.Document` under `sourceQueryCulture: es-CO`).

### What still can't be checked statically

Documented in the response itself (`limitations`), not hidden: a blank or
duplicate in the "one" side column of a relationship depends on the data,
not the TMDL, and only shows up on refresh. That's what `pbi_refresh_model`
is for.

---

## [1.0.0-rc.5] — 2026-07-31

**108 tools, 1097 tests**, contract frozen. Integrates three fixes that came
out of background tasks and strengthens the visual-type contract.

### Fixed

- **`TYPE_MAP` is now DERIVED in lowercase** (`{real.lower(): real}`) instead
  of hand-written. It was previously fixed by lowering the keys one by one,
  which left the defect one slip away: adding a camelCase key was enough to
  re-advertise a type that gets rejected. Now it's impossible by
  construction.
- **Less was advertised than what's accepted**: the factory's error message,
  the validator's hint, and `pbi_page_building_blocks` only listed the real
  `visualType` values, hiding the convenient aliases (`matrix`, `barChart`,
  `button`). All three now draw from `SUPPORTED`, and there are tests that
  check they can't drift apart.
- **The `live` DAX test never actually ran**: it imported names that no
  longer exist inside an `except Exception: return False`, so the
  ImportError was read as "no Desktop open" and it came out skipped even
  with a model loaded. The import now happens at module level: renaming
  something breaks collection instead of disguising itself as a skip.
- **The in-flight idempotency test was flaky**: it coordinated by clock
  (a 0.15s `sleep` against a 1s wait) and under the full suite's load that
  margin wasn't always met. Now the two threads rendezvous via events, with
  two barriers, and the result no longer depends on how long anything
  takes.

---

## [1.0.0-rc.4] — 2026-07-31

**108 tools, 1008 tests** (2 skipped), contract frozen.

### Added

- **Composition elements**: `textbox`, `shape`, `image`, `actionButton` and
  `pageNavigator`. Until now the server only knew how to create data
  visuals, so it couldn't build a cover page or a navigation menu. They
  carry no query: their content is defined in `options` (text, fill, shape,
  target page), and asking them for fields is an explicit error instead of
  an empty visual. The structures were extracted from real reports, not
  from documentation.
- **Visual identity**: `pbi_list_themes` and `pbi_apply_theme`, with three
  palettes verified with the `dataviz` skill's validator (luminosity band,
  chroma, separation under protanopia/deuteranopia/tritanopia, and
  contrast). Status colors are fixed across all three themes: the
  traffic-light means the same wherever it's painted, and a status color is
  never reused as a series. Applying a theme writes the JSON, declares it in
  `themeCollection`, and registers it in `resourcePackages`: without all
  three, Desktop silently ignores it.
- The HTML preview now draws composition elements **with their actual
  look** (color, text, buttons) instead of as wireframe boxes, so a cover
  page can be judged without opening Power BI Desktop.

### Fixed

- **`TYPE_MAP` declared keys in camelCase and the lookup lowercased them**:
  `cardVisual`, `tableEx` and `pivotTable` were advertised as supported and
  rejected when used, with an error message that listed them as valid. Now
  there's a test that walks every advertised type.
- **The layout detector treated composition elements as charts**: a normal
  cover page produced about twenty false warnings —a background *must* be
  below everything, and a button isn't "too small to show data"— and among
  them the real warning got lost. Now overlap and minimum size only apply
  to data visuals; Z-order is still checked on all of them.

### Added (conversion)

- **`.pbix` → `.pbip` conversion**, single file or batch folder:
  `pbi_convert_pbix_to_pbip`, `pbi_inspect_pbix` and `pbi_list_convertible_pbix`.
  - **Report**: if the `.pbix` already stores PBIR (recent Desktop versions
    do), it's copied byte for byte; if it carries the legacy `Report/Layout`,
    it's translated. The translation resolves table aliases to entity
    names, merges `projections` with `prototypeQuery.Select`, converts
    numeric enums to strings, and turns `OrderBy` into `sortDefinition`. The
    equivalences were derived by comparing a real report saved by Desktop in
    both formats.
  - **Model**: the `DataModel` stream is an ABF backup compressed with
    XPress9 and can't be read without the engine, so the `.pbix` is opened
    in Power BI Desktop and serialized to TMDL with the official
    `TmdlSerializer`. The session is reused if the report is already open,
    and only what the tool opened gets closed. The original `.pbix` is
    never modified.
  - The conversion reports what has **no** equivalent (`dropped`) instead of
    silently losing it: today, legacy-format bookmarks.
  - Verified on 72 real legacy reports: 6705 valid documents against the
    official schemas, and projects Power BI Desktop opens.

### Fixed

- The TMDL serializer runs on .NET Framework, which rejects paths of 260
  characters or more even if Windows allows them. Now it serializes to a
  short temp path and moves it to the destination with Python.
- Power BI Desktop also won't open a `.pbip` with long paths
  (`PBIProjectUtils.EnsureNotLong`). The conversion checks this **before**
  writing and aborts stating how much is over, instead of leaving a project
  that won't open.
- Instance discovery considered an engine ready before it had actually
  loaded the model: Desktop creates the database before populating it, and
  there was a window of several seconds during which the TMDL would have
  come out with no tables.

### Unblocked — schemas Microsoft doesn't publish

Power BI writes schema versions before publishing them: `visualContainer`
2.10.0 and 2.11.0 return **404**. That was blocking **every** write on any
report saved with a recent Desktop version, which is nearly all of them.

It's now checked against the previous version of the same family, and only
what a later version could have **added** is forgiven (a new property, a
new enum value). A wrong type or a missing required field still blocks.
Measured on **275 real files** declaring 2.10 or 2.11: in all of them, the
only discrepancy against 2.7.0 was the `$schema` string's own version. The
approach doesn't cross major versions, and with no earlier version cached,
the block stays in place. The only unpublished schema left with no
fallback is `bookmarks/` (plural), which some reports declare for the
bookmark index; the ones this server writes —`bookmark/2.1.0` and
`bookmarksMetadata/1.0.0`— are published, so creating bookmarks is checked
in full.

### Added — missing authoring

- **Conditional formatting** (`pbi_set_conditional_format`): a two- or
  three-stop gradient on background, text or bars. This is what turns a
  matrix of numbers into a heat map. With a wildcard selector, otherwise the
  color would only paint the first row.
- **Filters and interactions**: previously rejected because we didn't know
  how to serialize them. The catch with the filter is that it has two
  halves with different rules —`field` references the table by name and
  the internal query by alias—, and writing the name in both produces a
  filter Power BI silently ignores.
- **Semantic model beyond measures**: `pbi_create_calculated_column`,
  `pbi_create_relationship` and `pbi_create_hierarchy`.
- **Resources**: `pbi_add_image_resource` and `pbi_list_report_resources`.
  Copying an image without declaring it leaves it invisible to Power BI, and
  declaring it without copying it leaves the visual empty: both cases are
  silent when the report opens.
- **`pbi_propose_dashboard`**: classifies the model —which column is a
  status, which one a date, which ones form a comparable family— and
  returns complete designs with their reasoning and an applicable spec,
  instead of waiting for instructions.
- **`pbi_profile_data`**: profiles the VALUES, not the structure. Detects
  percentages outside 0-100, empty or single-value columns. On a real model
  it found in seconds a `pct_codificado` valued at −800.
- **Bookmarks**: `pbi_create_bookmark`, `pbi_list_bookmarks` and
  `pbi_delete_bookmark`. Both the file AND the index get written, because
  without the index Power BI won't show it even if the file exists. Inside
  a bookmark, the filter uses the key `expression`, not `field` as in
  `filterConfig`: they're similar structures with different names, and
  using the wrong one produces a bookmark that restores nothing.
- **`pbi_set_storage_mode`**: import / directQuery / dual. Returns the
  previous mode and how many partitions changed, because it's a change that
  must be undoable knowing exactly what was touched, and it warns that
  DirectQuery requires foldable queries and disables calculated columns.
- **`pbi_create_calculated_table`**: infers the columns by EXECUTING the DAX
  against the open model, because TMDL requires them declared and they
  can't be guessed by reading the expression.

### Fixed — precedence and dialects

- **Field validation was looking at the wrong model**: it preferred the
  live model over the project's TMDL, so having another `.pbix` open in
  Desktop was enough to make freshly written measures look nonexistent.
- **Two incompatible spec dialects**: validation used
  `{schema_version, page}` and applying used `{page_name}`. A spec that
  passed validation bounced when creating it, with an error that didn't
  even mention there were two formats. Now `pbi_create_page_from_spec`
  accepts both.

---

## [1.0.0-rc.3] — 2026-07-31

**90 tools, 859 tests** (2 skipped), contract frozen.

### Added

- Distribution as a local **Codex** and **Claude Code** plugin, with native
  manifests, an install skill, and automatic setup of the isolated runtime.
- MCP-driven install bootstrapping: no need to download, register or run a
  dedicated binary. Python is still needed to access Power BI Desktop and
  local files.

### Changed

- Project license to **Apache License 2.0**, with a consistent `NOTICE` and
  package metadata. Microsoft binaries are still not redistributed.
- Declared version: `1.0.0-rc.3` visible, `1.0.0rc3` in PEP 440.

---

## [1.0.0-rc.2] — 2026-07-31

Replaces `1.0.0-rc.1`, whose CI matrix was red. **90 tools, 854 tests** (2 skipped), contract frozen.

### Fixed

- **The contract check depended on the Python version.** `test_contract_matches_golden` failed on 3.10 and passed on 3.13, reporting the 90 tools as having a "modified description" with nothing about the product having changed.

  Python 3.13 changed how docstrings are stored ([gh-81283](https://github.com/python/cpython/issues/81283)): from that version on, the compiler strips their indentation. The tools' descriptions **are** their docstrings, and the golden was generated with 3.14, so on 3.10 there was exactly that extra indentation left over (`pbi_list_tables` 130 → 138 bytes).

  The contract now normalizes with `inspect.cleandoc` before freezing and comparing. The golden doesn't change a single byte: what changes is that 3.10 now produces the same thing. `requires-python = ">=3.10"` is kept — the product does support 3.10; the defect was in how the contract was frozen.

- Workflow actions bumped to `checkout@v7`, `setup-python@v7`, `setup-node@v7` and `upload-artifact@v7`: the previous ones run on a Node runtime the runner flags as deprecated.

### Changed

- Declared version: `1.0.0-rc.2` visible, `1.0.0rc2` in PEP 440.

---

## [1.0.0-rc.1] — 2026-07-31

First public candidate. 90 tools, contract frozen.

> **Replaced by `1.0.0-rc.2`**: it was published with a red CI matrix (`test (3.10)` failed and `build` was skipped). The tag and its evidence are kept.

### Added

- **Real page updates** (C2–C4). `apply_page_spec` on an existing page did nothing and reported success. It now dispatches by explicit outcome —`create`, `update`, `no_change`, `conflict`—, keeps the page's id and each equivalent visual's id, and offers `sync_mode` (`merge` by default, `replace` optional).
- **Safe duplication** (E4). `duplicate_page` copied visuals with new ids without remapping anything: interactions, groups and drillthrough kept pointing at the original page. Now the full `old_id → new_id` map is built, and an id that can't be remapped **blocks** with `unsupported_page_structure`.
- **Recovery from journal** (`pbi_recover_from_journal`) with five states, byte-for-byte verification and parent directory recreation.
- **Backup retention** (`pbi_purge_backups`), which closes **R5**. Dry-run by default, validated root, only recognizable journals, symlinks not followed, and the most recent one plus all pending ones are always kept.
- **Microsoft's official validator** (E3.2) as a second layer: `@microsoft/powerbi-report-authoring-cli@0.1.4`, offline, with pre/post diagnostic comparison.
- **Representative PBIR fixture** (`tests/fixtures/rich.py`): interactions, bookmarks, drillthrough, custom visual, broken reference, CRLF and an unpublished schema. Synthetic and anonymized.
- `docs/DUAL_MODE.md`, `docs/VALIDATION.md`, `docs/RELEASE_CHECKLIST.md`, `CONTRIBUTING.md`.

### Fixed

- **Workflow atomicity** (D). `repair_broken_references` opened one transaction per visual **and caught the exception to keep going**; `normalize_report`, one per page. And `__exit__` called `commit()` unprotected: if the commit failed, the exception escaped **without rolling back**.
- **Log rotation** (N). `RotatingFileHandler` was spewing a traceback to stderr in the middle of `doctor` and the contract check, which exited with code 0 anyway.
- **Directory cleanup after commit**: between the write and the cleanup, the report was left invalid. Moved inside the transaction; rollback recreates the parents.
- **`_pages_metadata` was propagating a `pages.json` without `$schema`** instead of guaranteeing it.
- **Unpinned DLLs** (J3). `latest_stable()` swallowed the latest version with no hash, and extracted onto `libs/`: a partial failure left a mix of two versions.

### Known limitations

- `visualContainer/2.10.0` and `bookmarks/2.0.0` **are not published** by Microsoft (404). Neither the internal validator nor the official CLI can check them; writes on files declaring them are blocked. **G10 remains a documented exception.**
- `mode="both"` **blocked**; R15 open.
- `filters` and `interactions` in the page spec are **rejected** with `unsupported_feature`.

---

## [1.0.0] — 2026-07-30 (internal, not published)

Hardening before publication: plan contract, idempotency, API honesty,
secret redaction and packaging.

### Fixed — Plans and idempotency

- **Single, versioned plan contract** (`services/plan_contract.py`). `pbi_apply_page_spec(dry_run=True)` produced a plan `pbi_apply_plan` didn't know how to apply: no `affected_files` (`KeyError: 'files'`) and with an *argument* fingerprint in the field meant for the *state* fingerprint. The applier now dispatches by `operation`, and the envelope describes the exact bytes that will be written. An envelope with an unknown version is rejected with `plan_version_unsupported`.
- **Real idempotency** (`services/idempotency.py`). It was documented but not implemented: nobody called `comprobar_request`/`guardar_resultado` and `guard()` made up a `request_id` on every call. There are now four states (`in_flight`, `succeeded`, `failed`, `compensated`), a persistent record with atomic writes, and an optional `request_id` on the 34 tools that mutate.

### Fixed — API honesty

- `filters` and `interactions` in the page spec were accepted and **silently dropped**. Now they're rejected with `unsupported_feature` stating the exact JSON path. Serialization is still pending.
- `pbi_replace_visual_field` wrote any reference without checking it, and kept the old field's node type (a measure could end up in a `Column` node). Now it validates against the model and returns `field_not_found`.
- The PBIR *capability check* was informational and nobody looked at it; it also declared a report **without** a version as supported. Now it blocks with `pbir_version_unsupported` (fail-closed).
- DAX export said "complete result" when it was already truncated by rows and by bytes.

### Fixed — Security and robustness

- `ConnectionFailedError` returned the entire connection string, and `DaxQueryError` 2000 characters of the query. `services/redaction.py` leaves the destination, the length and a short prefix.
- `max_rows`, `max_bytes` and `timeout_seconds` weren't validated: zero, negative and disproportionate values reached the engine.
- The audit score measured the report's size, not its quality (the real PB4 scored 0). Normalized by applicable rules, objects evaluated, severity and a per-rule cap.

### Fixed — Quality and packaging

- Three assertions that could never fail (two `or True` and one empty test under an unconditional *skip*).
- `LICENSE` was initially published as MIT; since RC3 the project uses Apache-2.0. `mcp` stays pinned to `>=1.28.1,<2` with a compatibility test, because the server depends on the private attribute `_mcp_server.version`.
- The **sdist** is also tested: build and install in a clean environment.

---

## [1.0.0] — 2026-07-30

First complete version. 88 tools, contract frozen.

### Added — Platform (Macro-phase A)

- **Uniform, additive response envelope**: `status`, `request_id`, `operation`, `duration_ms`, `warnings`, `side_effects`. Keeps `ok` and every previous field.
- States: `success`, `warning`, `planned`, `error`, `conflict`, `rollback_incomplete`.
- **JSON logging to stderr** with redaction: only the shape of DAX, rows, expressions and paths is logged, never the content.
- **Idempotency** via `request_id`; reusing it with different arguments is `request_id_conflict`.
- **Plans with `plan_token`** that capture the state; if the project changes, the plan is rejected (`plan_token_stale`).
- Tools: `pbi_health_check`, `pbi_capabilities`, `pbi_session_info`, `pbi_list_pending_journals`, `pbi_inspect_journal`, `pbi_plan_change`, `pbi_apply_plan`.

### Added — Semantic model (Macro-phase B)

- **Exploration that works the same live and over TMDL**: summary, search (also inside the DAX), direct, transitive and reverse dependencies.
- Reference extraction with a lexical scanner: a reference written inside a string or a comment doesn't count.
- **Model audit** with 13 rules, each with a stable identifier, evidence and `auto_fix_available`.
- **DAX with real limits**: `max_bytes`, `timeout_seconds`, `export`, per-column types, and statistics that distinguish row-based from size-based truncation.
- Tools: `pbi_model_summary`, `pbi_search_model`, `pbi_get_object`, `pbi_measure_dependencies`, `pbi_column_dependencies`, `pbi_list_hierarchies`, `pbi_list_roles`, `pbi_list_perspectives`, `pbi_list_partitions`, `pbi_audit_model`, `pbi_list_audit_rules`.

### Added — PBIR authoring (Macro-phase C)

- **Full visual CRUD**: duplicate (keeping fields, format and filters), delete, title, Z-order, replace field, copy format.
- **Page CRUD**: duplicate with all its visuals, delete while updating order and the active page, rename, reorder.
- **Deterministic layout engine**: detects overlaps, off-canvas placement, minimum sizes, margins, spacing and Z-order; aligns, distributes and normalizes.
- Tools: 16, from `pbi_get_visual` to `pbi_normalize_page_layout`.

### Added — Declarative spec (Macro-phase D)

- **Versioned Schema 1.0**, with errors carrying a **JSON path** (`$.visuals[2].fields.values[0]`).
- Resolution against the model: a nonexistent or **ambiguous** reference is rejected.
- **Deterministic IDs** with a seed.
- Full flow: building blocks → spec → validate → preview → diff → plan → apply → verify → rollback.
- 6 presets: `executive`, `financial`, `sales`, `operations`, `evm`, `detail`.

### Added — Comprehensive audit (Macro-phase E)

- `pbi_audit_project` combines model, report and layout, with a score **per domain** and an executive summary.
- Output in JSON, Markdown and HTML (with verified escaping).
- **Selectable autofixes**: `plan_fixes` requires explicit rules. There's no "fix everything."

### Added — Workflows (Macro-phase F)

- 8 outcome-oriented workflows, composing internal services (never decorated tools, verified via AST).
- Each one walks through analysis → plan → preview → apply → verification → report, with `dry_run` by default.

### Security (Phase 1A and derivatives)

- **Paths bounded** to the project, with real Windows semantics: UNC, `\\?\`, `\\.\`, `C:relative`, NTFS ADS, reserved names, junctions and anti-TOCTOU revalidation.
- **Read-only DAX**, fail-closed: only `EVALUATE`, `DEFINE…EVALUATE` and `$SYSTEM` DMVs. No escape hatch.
- **Strict Power BI Desktop policy**: `open` and `unknown` block PBIR writes.
- **Compensated transaction** with a journal, sha256 fingerprints verified three times, and a rollback that **doesn't overwrite external changes**.
- **Backups** with a validated destination (never inside the `.pbip`), hash-based identification and a verifiable manifest.
- **Sessions**: stale ones and ones that reused the port are detected.

### Changed

- `mode="both"` **disabled** on the 6 dual tools: `live` needs Desktop open and `pbip` needs it closed. It used to apply `live` and fail on `pbip`, leaving a partial state.
- `pbi_run_dax` accepts `max_bytes`, `timeout_seconds` and `export` (optional).

### Fixed

- Rollback left empty, orphaned page directories.
- A failed `os.replace` left a `.tmp` inside the `.pbip`.
- `pbi_hide_columns` called another decorated tool: errors turned into data and the batch reported `ok:true` with failures buried inside.
- A raw .NET exception from `SaveChanges` escaped without compensating the disk.
- Packaging: `services*` and `reporting` were missing from `pyproject.toml`.
- `doctor.py` had the tool count hardcoded.

---

## [0.1.0] — 2026-07-07

Initial version: 34 tools, live layer (ADOMD/TOM) and disk layer (TMDL/PBIR).
