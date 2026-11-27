# Horizun PBI MCP v2.2.1

Fixes from an end-to-end audit: 118 MCP calls over stdio against a real Power
BI Desktop, from an empty project to an exported `.pbix`, plus a clean plugin
install from zero. Contract against `v2.2.0`: 140 tools, **no change** (one
new response field).

## Fixed

- **A misspelled argument no longer runs the tool with defaults.** Unknown
  keys used to be dropped silently, so `pbi_align_visuals(alignment="top")`
  (the parameter is `edge`) aligned left, stacked one chart on another and
  answered `ok: true`. Write tools now refuse with `unknown_arguments` before
  doing anything and suggest the right parameter. Read-only tools still run and
  report `ignored_arguments`.
- **Card values were 9 pt under the bundled themes.** They are now 28 pt.
- **Page presets drew the same chart twice**, and `[Measure]` references in the
  page generators became `[[Measure]]` or were reported missing.
- **Reads with Desktop closed** now say to repeat the call with
  `source='pbip'` instead of asking to open Desktop.
- **Plugin install:** `launch.cmd` failed with Git for Windows first on `PATH`;
  a failed install step left `install.log` empty; the `venv` step is retried
  once.

Full list in `CHANGELOG.md`.

## Upgrading

Nothing to do. If you get `Connection closed` right after updating the plugin,
remove any manual `horizun-pbi-mcp` MCP entry that points at an old versioned
cache: the plugin registers its own server (`docs/INSTALL.md`, section 6). If
you installed with the one-paste block, use the `v2.2.1` block in
`docs/INSTALL.md`.
