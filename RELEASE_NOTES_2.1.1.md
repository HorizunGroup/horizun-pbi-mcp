# Horizun PBI MCP v2.1.1

A patch release with one purpose: **make the Claude Desktop installer real**.
The `.mcpb` bundle existed in the tree since `2.1.0` but had never been through
a release, and the two paths it depends on turned out to be broken in ways the
test suite could not see. The MCP contract is untouched — still 139 tools, still
the original 34 frozen.

## The one-click installer for Claude Desktop actually ships now

`v2.1.0` was published before the bundle existed, so its release carries no
`.mcpb` and the download links in the README pointed at a 404. This release is
the first that carries `horizun-pbi-mcp-2.1.1.mcpb`, signed in `SHA256SUMS`
like every other asset and rebuilt from committed history alone.

Verified by installing it from the interface of **Claude Desktop 1.46388.3**:
it registers as `local.mcpb.horizungroup.horizun-pbi-mcp`, so
`manifest_version: 0.4` and `server.type: "uv"` are accepted as declared; the
client shows its usual *not verified by Anthropic* notice for anything outside
its own directory and lets the install continue; the server starts **without
restarting the client**, exposes the 139 tools, and answers real calls.

## The first run was broken, and always had been

Two defects, both only reachable through the path a real client uses. A manual
install never touched either, which is why they survived.

**The install failed at promotion, every time.** Promoting a runtime renames a
directory, and the launcher hands the detached installer a log file *inside*
that same directory as its stdout. On Windows, renaming a directory that holds
an open file fails with `ERROR_ACCESS_DENIED` — so the installer was renaming
the folder containing its own output. The log now lives in the data root, where
the lock already lived for exactly this reason. Measured: two first runs from
the bundle failed before the fix and reach `ready` with the 139 tools after it.

**The bootstrap ignored the client's protocol version.** It answered every
`initialize` with its own `2025-11-25`. The specification requires echoing a
version the client asked for when it is supported, and the installed server
already does that through the SDK — so the two halves of the same extension
disagreed precisely at startup, which is the moment the bootstrap exists for.

Two consequences of moving the log were fixed with it: the orphan sweep that
runs after every successful install was deleting the live log — the very file
`pbi_install_status` points at — and a single log shared by all versions needed
a size bound, so it now rotates at 2 MiB keeping one previous round.

## Promotion tolerates a transient Windows lock

Independently of the above: renaming a directory can also fail because an
antivirus is scanning the freshly written runtime. The two renames now retry
briefly and only on the Windows sharing codes. Nothing else changes — once the
waits run out the same error is raised and the previous runtime is left intact,
and an error that is *not* a lock still fails on the first attempt.

## Tests that could not fail, and now can

The handshake test asked for the same protocol constant the launcher had
hard-coded, so it could never have caught the negotiation defect. The release
tests checked the wheel, the sdist, the installer and the SBOM against
`SHA256SUMS` but not the bundle — and `release_verify` only objects to files
that are *extra*, so removing the step that builds the `.mcpb` left every test
green while the release silently lost its one-click installer.

Both are covered now, along with three mutations that must stop the pipeline:
a `.mcpb` altered after signing, one present but unsigned, and one signed and
missing. There are also checks that the manifest's `entry_point` exists inside
the ZIP, and that the builder packs the **committed** blob rather than a dirty
working tree — where real PBIX files, outputs or credentials could be sitting.

## Documentation

The README now says up front what the project had only implied in a metadata
line: it needs **no licence, no API key and no paid plan**, because it runs as a
local process — and that is also why a paid remote connector is not an upgrade
path but a different architecture that could not reach your Power BI at all.
It also explains when to pick Claude Desktop and when to pick Claude Code.

`docs/INSTALL.md` gains the requirements, the first-run sequence, how to confirm
the tools appeared, and how to read `pbi_install_status` when something fails —
including that a successful install leaves its log empty, so an empty log is not
a symptom.

## Upgrading

Nothing to do. No tool was renamed, no parameter changed, no response shape
moved. If you already have a prepared runtime it is reused: its directory is
deliberately independent of which client installed it.
