# PBIR validation: two layers, a format oracle, and their limits

Before writing a report file, Horizun PBI MCP validates it in **two independent layers**. Neither replaces the other, and neither promises more than it checks.

For the properties the server itself adds to `visual.objects`, a
third, specific barrier also compares the structure against the format
catalog and against shapes Power BI Desktop actually exported.

---

## Layer 1 — internal schema validator

`services/pbir_schema.py`. Validates **each document separately** against the **official** JSON Schema the file itself declares in `$schema`.

- The schemas are **exact official copies** from `developer.microsoft.com`, with their transitive `$ref` closure (22 documents: `semanticQuery`, `filterConfiguration`, `formattingObjectDefinitions`, `visualConfiguration`…).
- Each one has its **SHA-256 pinned** in `src/services/schemas/pbir_manifest.json`.
- Validation is done by **`jsonschema`**, with the draft each document declares (draft-07).
- References are resolved against a `referencing.Registry` built **only** from the manifest: allowlist, **zero network access**, zero resolution of arbitrary URLs.

**Not redistributed.** They don't declare a license or permission, so they're installed separately:

```bash
python scripts/fetch_pbir_schemas.py
```

**Without that cache, every PBIR write fails with `schema_unavailable`.** It doesn't degrade to "just check it's JSON".

### What it CANNOT see

Standalone documents. It sees nothing that depends on looking at the whole report: whether a format object exists for that visual type, whether a column occupies a role that only accepts measures, whether a theme's name matches the one `report.json` references.

---

## Layer 2 — Microsoft's official validator

`services/report_validator.py`. Runs the official CLI over the **entire** `.Report`, after writing the batch and **before committing**.

```bash
python scripts/fetch_report_validator.py     # requires Node >= 20
```

Package: `@microsoft/powerbi-report-authoring-cli@0.1.4` (MIT, Microsoft Corporation), exact version, tarball verified by SHA-1 and SHA-512 integrity before installing. **No normal operation runs `npx` or downloads `@latest`.**

It finds what layer 1 can't. On a real reference report: **44 errors and 12 warnings**.

| Diagnostic | What it detects |
|---|---|
| `PBIR_FORMATTING_OBJECT_UNKNOWN` | A format object that doesn't exist for that visual type |
| `PBIR_ROLE_KIND_MISMATCH` | A column in a role that only accepts measures |
| `PBIR_THEME_FILE_NAME_MISMATCH` | The theme declares a different name than what `report.json` references |
| `PBIR_VISUAL_TYPE_UNKNOWN` | Unrecognized custom visual |
| `PBIR_VISUAL_DIR_WITHOUT_JSON` | Visual folder without its `visual.json` |

It's always invoked with **`--no-schema`**: by default the CLI downloads schemas over the network, and a mutation can't depend on that. Measured: in that mode it keeps the 44 semantic errors and only loses the unreachable-schema warning, which layer 1 already covers.

**The CLI's exit code is 0 even when it fails.** It reports the diagnostic count, not the exit code.

---

## Oracle for managed format paths

`services/format_oracle.py` queries `formatting effective-properties` from the
pinned CLI and validates the visual's full `(scope, group, property)` paths,
including those inherited from a template, with their value type and enums. A
minimal snapshot enables the same offline barrier for the managed paths, and
a live test checks it doesn't drift from the official catalog.

The synthetic fixture `format_objects_corpus.json` adds independent evidence
from visuals exported by Desktop: it keeps only structural keys and type
tokens. It contains no data, identifiers, names, paths or counts from the
source reports.

Without the official CLI, full equivalence isn't feigned; the local
structural barrier is kept. The oracle also doesn't claim that a valid
structure produces a visually good composition. To check that Desktop
renders the file there's `pbi_validate_desktop_render`; the aesthetic/semantic
evaluation of the capture remains a separate layer.

Before opening a `.pbip`, `desktop_launcher` also runs the TMDL validator.
If it finds static or `TmdlSerializer` errors, it returns
`desktop_preflight_failed` with the findings and doesn't launch Desktop. This
prevents an old project from ending up in an `Untitled` window with a
generic Frown; for example, it detects a measure that collides with a column
in the same table. It also blocks a semantic model with no tables
(`tmdl_empty_model`), which would otherwise just produce a timeout waiting
for an engine that never gets to serve.

---

## Pre-existing diagnostics

A report can carry its own defects. The reference one carries 44. Attributing them to our operation would be false; ignoring the new ones, dangerous.

The **baseline is taken before writing**. Afterward, **normalized** diagnostics are compared: code, severity, relative file and JSON path. **Never the human message** — it carries absolute paths and variable text.

| Situation | Result |
|---|---|
| New error | **Blocks** and reverts |
| More errors than before | **Blocks** |
| Same error, in a different file or path | **Blocks** |
| Identical pre-existing errors | Not attributed to the operation |
| New warning | Doesn't block |
| A pre-existing error gets resolved | Doesn't block |

Pre-existing ones are **never fixed automatically**.

---

## Backend selection

| Layer 1 | Layer 2 | Behavior |
|---|---|---|
| available | available | Both. `validation_level = official_schema+report` |
| available | absent | Schema only. `validation_level = official_schema` |
| not available | — | **Blocks** with `schema_unavailable` |

In no case does it fall back to "just parseable JSON".

---

## The known limit: schemas Microsoft doesn't publish

Power BI Desktop writes `visualContainer/2.10.0` in recent reports. **That URL returns 404** at the official source. Same with `bookmarks/2.0.0`.

**The official CLI can't validate them either**: it downloads them from the same URL and emits `PBIR_SCHEMA_UNREACHABLE`, skipping schema validation for those files.

It's an **upstream** incompatibility, not this server's.

**Practical consequence:** writes on files declaring those schemas are blocked with `schema_unavailable` and `rule=no_publicado_upstream`.

Blocking was chosen over guessing. Validating 2.10.0 against 2.7.0 would give false negatives —`additionalProperties: false` would reject legitimate new properties— and false positives on whatever 2.10.0 may have relaxed.

Measured on a real 443-document report:

| | |
|---|---|
| Validate | **176** |
| Blocked for unpublished schema | **240** |
| Out of scope (`CustomVisuals/`, `StaticResources/`) | 25 |
| Genuinely non-compliant | 2 |

**G10 remains a documented release exception.**

---

## Error codes

| Code | Meaning |
|---|---|
| `invalid_json` | The content doesn't parse |
| `schema_unsupported` | The `$schema` isn't in the manifest, or a known PBIR type doesn't declare it |
| `schema_unavailable` | The schemas aren't installed, the hash doesn't match, or Microsoft doesn't publish that schema |
| `schema_validation_failed` | It parses, the schema is known, and it doesn't comply |
| `report_validation_failed` | The official validator found **new** errors |
| `validator_unavailable` | The official validator is needed and isn't present |

Errors state the **file and JSON path** (`$.position.width`) and **never the values**: those are report data.

---

## Check status

```bash
python scripts/doctor.py
```

```bash
python scripts/fetch_pbir_schemas.py --update      # recomputes the manifest
python scripts/fetch_report_validator.py --check   # official CLI status
```
