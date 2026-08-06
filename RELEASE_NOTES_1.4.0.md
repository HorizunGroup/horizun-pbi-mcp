# Horizun PBI MCP v1.4.0

Five new tools (133 total), zero breaking changes — and a theme that runs
through every fix in this release: **the server produced something that
looked right and nobody could see it was wrong.** A workbook the schema
accepts, a PDF that opens, a query that returns rows, a slicer whose colours
are written down. All valid. All silently not doing what they claimed.

Every defect below was found by opening the artifact and looking at it, and
every one is verified by mutation: revert the fix and a named test fails.

## Added

- **`pbi_export_report_content`** — exports the report CONTENT, not its
  metadata: the table behind each visual, or a query the client declares
  (`rows` / `values` / `filters` / `top_n`). Selection by `pages`, `visuals`
  or `queries`; output to `.xlsx` and `.pdf`.

  Each visual's query is reconstructed from its fields and its PBIR filters.
  Every sheet declares which filters were applied and — the part that matters
  — which could **not** be translated, because a number that silently ignores
  a filter is worse than no export. Visuals with no tabular query (text
  boxes, images, shapes) are listed with the reason instead of exported
  empty, and an unrecognised aggregation makes the visual decline rather than
  guess.

  It needs the live model, so it opens Desktop when there is none — and it
  **refuses to export a model that is open but unprocessed**. A freshly
  opened `.pbip` answers every query with zero rows: the workbook would come
  out blank without a single error.

- **`pbi_export_excel`**, **`pbi_generate_pdf_report`** — verified document
  exports: `.xlsx` with model, report and audit; executive, technical and
  audit PDFs with optional dashboard captures. Both reopen what they wrote
  before reporting success.

- **`pbi_sharepoint_list_folder`**, **`pbi_sharepoint_download_folder`** —
  read-only SharePoint Online ingestion through Microsoft Graph, app-only
  MSAL, all-or-nothing staged download with SHA-256 verification.

- **Two audit rules for visuals Power BI refuses to draw.** A visual in error
  shows a banner instead of content, and nothing catches it: the PBIR schema
  accepts the JSON, the official CLI passes, and the synthesized DAX even
  returns rows — the fault is in the *field configuration*, not the query.
  `report_scatter_axis_not_aggregated` (a field in Details with
  non-aggregated X/Y) and `report_slicer_below_height_floor` (a slicer under
  its height floor).

## Fixed

- **A visual with columns from two tables exported a cartesian product.**
  `SUMMARIZECOLUMNS` only applies auto-exists within one table. Measured
  against the engine: 20 risks by 20 mitigation measures returned **400 rows
  where the visual shows 20**. The query now carries an auxiliary
  `CALCULATE(COUNTROWS(<fact table>))` resolved from the model's
  relationships, stripped again before the file is written. With no single
  fact table covering every column, the visual is declared non-exportable.

- **`pbi_generate_pdf_report` named no object in its audit table**, and
  printed raw JSON. Every finding carries the measure, column, visual or page
  it is about; the PDF dropped it and printed seven identical
  `measure_possibly_unused` rows with `{"referenced_by_measures": 0}` as the
  description. There is now an `Objeto` column, page ids resolve to their
  visible name, and evidence reads as `visual_count: 13; threshold: 12`.

- **`reuse_open` never reused a `.pbip` session.** Detection relied on open
  file descriptors and Desktop keeps none on a project folder — verified with
  `open_files()`: zero files. Every `pbi_open_in_desktop` on a project
  launched ANOTHER window of the same report. It now falls back to the main
  window title, reusing only when exactly one window matches.

## Measured, not derived

The slicer height floor shipped wrong first. The rule used 76px
unconditionally (header 28 + selector 32 + padding 8/8) and reported nine
healthy slicers as broken — the ones that hide their header. The floor is
**76px with a visible header and 48px without**, and both numbers now come
from feeding the official CLI the same report at varying heights: 74 fails
and 76 passes with a header, 47 fails and 48 passes without one.

The lesson is the method, not the constant: when the oracle and I disagree,
the one who has to explain themselves is me.

## Known limits

Unchanged from 1.3.0 and documented in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md): three PBIR schema
versions unpublished upstream, `both` mode blocked by mutually exclusive
preconditions, and full visual equivalence of the `objects` block still
partial.

Content export needs Power BI Desktop: the data lives in the engine, and the
engine only exists as a child of Desktop. There is no XMLA path to the
Service in this version.
