# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Semantic versioning. **The contract of the original 34 tools is never broken.**

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

## [Unreleased]

Five gaps found while using the server on a real case (building a
construction-budget dashboard from scratch, with data from an external ERP),
not invented ones: each one cost real session time before being worked
around by hand, and this delivery keeps the next case from paying the same
price.

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
  would have come out as "Prodesa Logo" over a logo. Decorative elements
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
