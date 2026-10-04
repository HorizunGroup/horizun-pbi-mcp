# Horizun PBI MCP v2.1.2

A patch release built from two runs in real use, not from review: the
«Comité de obra» exercise and a course demo recorded on camera. In both the
agent ended up editing files by hand or reporting a result that was not true;
this release removes those cases. The MCP contract is untouched — still 139
tools, the original 34 frozen, **0 breaking changes and 3 compatible ones**
against `v2.1.1`.

## A model that will not open no longer says `success`

`pbi_create_pbip_project` wrote `database.tmdl` with the project name unquoted.
A project called `Tablero Mirador` got `database Tablero Mirador`, which the
official serializer rejects, so Power BI Desktop would not open it. The name is
now quoted when TMDL needs it, every TMDL identifier goes through the same
quoting rule, and the model is validated in staging before the project is
published.

The static lint that let that file through now reports
`tmdl_database_name_unquoted`, checked against what `TmdlSerializer` actually
rejects. And any tool whose `model_validation` comes back `valid: false` now
answers `status: warning` with a message saying the model will not open,
instead of `success` with the failure buried in the payload.

## Code columns load as text

A `codigo` column with `1.01`, `2.03`, `2.10` loaded as a number, so `2.10`
became `2.1` and stopped matching the other tables. Columns whose header says
they are a code (`codigo`, `cod`, `code`, `clave`, `item`, `sku`, `ref`…), and
all-integer columns with leading zeros, now load as text, each reported in
`warnings`. `pbi_add_table_from_file(text_columns=[...])` forces text on any
column the header does not give away. Codes also no longer vote in the decimal
separator detection, which could load values multiplied by a hundred.

## Render validation tells the truth about the canvas

`pbi_validate_desktop_render` reported `data_loaded: true` over blank visuals:
it counted rows in the engine and captured before the window repainted. It now
reads the canvas through UI Automation — Power BI's own banners, `(Blank)`
values and the titles of the page's visuals — waits for a healthy canvas, and
says `data_loaded: false` when it never gets one. Only visible elements count.

The capture also kept only the top-left corner of the window at 150 % display
scaling; it now switches the DPI context of its own thread. "Fit to page" works
on a window this server opened itself, and the zoom is no longer reported as
verified from an announcement that repeats the same percentage.

## A default format instead of the bare template

Visuals built from the minimal template now leave with a base format: a 12 pt
semibold title in the theme's ink, a frame with background and rounded border,
and the theme's colours (or the «claro» preset's when the theme does not define
them). What the theme already governs is not overridden, and `options` always
wins. A card with a title turns off the category label that repeats it
(`show_category_label=true` keeps it).

## Smaller fixes

- `pbi_create_measure` accepts an integer `format_string` such as `0` instead of
  rejecting it, and `pbi_apply_plan` no longer drops it silently; a decimal is
  rejected with a useful message.
- A page spec's `displayName` is used for the tab, and
  `pbi_create_page_from_spec` passes each visual's `options` to the factory.

## Upgrading

Nothing to do. No tool was renamed, no parameter changed, no response shape
moved. The plugin prepares a runtime for the new version on first start; the
previous one is kept as the last known good.
