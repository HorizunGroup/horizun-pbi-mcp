# Horizun PBI MCP v2.2.0

A smaller catalog for small local models, and the fixes that make the projects
it writes open in Power BI Desktop. Contract against `v2.1.3`: 140 tools, the
original 34 frozen, **0 breaking changes and 3 compatible ones**.

## Local profile for small models

Set `HORIZUN_PBI_MCP_PROFILE=local` and the server registers 22 tools with
short descriptions and compact replies: about 4 500 tokens of catalog instead
of about 47 000. It covers the whole file workflow: create or open a PBIP,
import a file, tables, relationships, measures, calculated tables and columns,
pages, validation, and opening, refreshing and closing Desktop.
`pbi_find_tool` (in every profile) finds the tools the profile hides. Without
the variable nothing changes.

Measured on 18 typical tasks: Claude Haiku solves 18/18 on `local`, the same
as on the full catalog, with 85 % fewer input tokens. qwen3:8b solves 14/18.

## Fixed

- **Calculated tables broke the project.** `pbi_create_calculated_table`
  wrote `sourceColumn: semana` without brackets, and Desktop rejected every
  relationship to that column ("invalid column ID"): the project did not open.
  It now writes `[semana]`. `pbi_validate_tmdl` flags the old form, and
  `pbi_open_in_desktop` stops such a project in about 2 s, before launching
  Desktop.
- **`pbi_open_in_desktop` waited for the whole timeout.** When Desktop shows
  "Problems were found" it now fails at once with `desktop_load_failed` and
  the dialog text, instead of a generic `desktop_timeout`. Reading the text
  needs the `export` extra.
- **Tables loaded from a relative path did not refresh.** The path is now
  written absolute in the M.
- **Calculated tables without `columns`** got an error that pointed to a
  `mode` parameter this tool does not have; it now asks for `columns`.
- Measures with references the engine cannot evaluate are refused before
  writing in `pbip` mode; TOTAL and blank rows are left out of imported files
  (`keep_summary_rows=true` keeps them); `pbi_create_page_from_spec` fills the
  empty first page instead of answering `ok` without doing anything.

Full list in `CHANGELOG.md`.

## Upgrading

Nothing to do. No tool was renamed, no parameter changed type or default, no
response shape moved. If you installed with the one-paste block, use the
`v2.2.0` block in `docs/INSTALL.md`.
