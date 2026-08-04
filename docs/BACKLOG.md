# Backlog

What remains open, why it matters and how to check it. Ordered by what
hurts the most.

Updated on 2026-08-04 for **v1.2.0** (127 tools). Two things closed since
1.0.1: the fourteen-item field report from the first real end-to-end session,
and **the four phases of the product vision** — intent brief, content-level
data diagnostics, external sources, and the ecosystem port as a data
contract. They chain: port keys → brief `critical_fields` → diagnostics
escalate those findings to `error` citing the owner's own reason.

The list below isn't a brainstorm: it's what we know is missing, with
evidence.

---

## 1. Full equivalence of the `objects` block

**Status:** partially closed. The structure Horizun generates already has an
oracle; full visual equivalence remains a limitation in
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

The official `formattingObjectDefinitions` schema declares
`DataViewObjectPropertyDefinitions` as `additionalProperties: {}`. It accepts
literally anything. That's where conditional formatting, font sizes and
colors of each visual live.

**Measured consequence:** conditional formatting was written incorrectly
throughout the project's entire life — the `color` level was missing — and it
passed Microsoft's official validator with zero errors. It was only
noticed by opening the report and seeing an uncolored table.

**Current barriers:** `services.pbir_schema.validar_objetos_visual()` checks,
before every write, the grammar of the wrappers the server produces: format
groups, `solid.color`, expressions, and `FillRule` gradients. With this, a
shape like `solid: {expr: ...}` gets blocked and the transaction reverted,
even though the official schema accepts it. The regression runs against the
previous commit and fails there.

`services.format_oracle` adds the missing piece for the paths the MCP
manages: it queries `formatting effective-properties` from the official CLI
and compares group, property, type and enum by `visualType`. A minimal
snapshot of those same paths keeps the barrier alive without Node; a live
test requires the snapshot to remain an exact subset of the installed
official catalog.

The synthetic corpus `tests/fixtures/synthetic/format_objects_corpus.json`
keeps only structural shapes that Desktop actually exported. It was built
from temporary copies of 125 PBIX files and replaces every literal with a type
token. It contains no paths, per-origin counts, hashes, GUIDs, IDs, URLs,
text, page/table/field names, model values or custom types. The extractor
fails closed on any key not in its allowlist.

This evidence found and closed defects the schema accepted: `cardVisual`
used `value.color` instead of `value.fontColor`; shapes and icons wrote
invented enums; `table`/`matrix` accepted properties Desktop ignores; and
`FillRule` entries lost `Aggregation.Function` and used the wrong selector.

The factory also queries the official catalog to require roles,
cardinalities and field class (`Grouping`, `Measure` or
`GroupingOrMeasure`) before writing. This closed another route by which
Desktop accepted the file but left an empty or semantically incorrect
visual. Roles live outside `objects` and are validated separately.

**Additional closure:** when the official CLI is installed, every PBIR write
re-compares **all** properties present in `objects` and
`visualContainerObjects` of the visual against `effective-properties`,
including those inherited from a template. An unknown property or group
blocks the transaction with `format_oracle`. Without the CLI, the offline
structural barrier is kept and full equivalence is not feigned. The
structural check alone also doesn't prove a visual renders as expected: that
belongs to the rendered check in the next point.

---

## 2. Automatic interpretation of the visual check

**Status:** partially closed. Capture is already automatic and safe; deciding
whether the result is visually correct still requires inspection.

The **WCAG contrast** check (`tests/test_design_y_guia.py`) closed a real
class: a title in `#0B0B0B` on a `#1A1A19` background.

The `pbi_validate_desktop_render` tool opens the `.pbix`/`.pbip`, correlates
the exact window by PID and creation time, and renders it with `PrintWindow`
without depending on focus. It fails closed on ambiguous windows or a
recycled PID, writes the PNG atomically to `outputs/desktop_captures` and
only closes Desktop if that same call opened it. It was tested against a
real Power BI Desktop.

What still requires eyes: whether the number fits, whether the table
renders, whether the legend doesn't cover the bar and whether the axis isn't
cut off. The capture proves the window rendered; it doesn't semantically
interpret its pixels.

**Current procedure**, so it doesn't need reinventing every time:

```python
result = pbi_validate_desktop_render(
    path=r"C:\path\Project.pbip", timeout=300, capture_timeout=30)
```

If the freshly generated project has no materialized data, it still needs a
refresh. The tool captures the visible page; walking through all pages and
classifying composition defects is still pending.

During this review a real case appeared that used to end in a Desktop Frown:
a construction-site model had the measures `Ejecutado` and `Programado` with
the same name as their columns. The TMDL parser accepted it, but Power BI
rejected the model on load. The launcher now runs the lint/TOM check before
opening the window and returns `desktop_preflight_failed` with the two rules
and their evidence; it doesn't leave a hung `Untitled` process. The
regression is in `tests/test_desktop_preflight.py`.

The same barrier now detects empty semantic models before Desktop's timeout
(`tmdl_empty_model`). In the sweep of nine local projects, five opened and
captured correctly, three were rejected before launching Desktop for being
empty, and the project with collisions was rejected with its two findings.
No orphaned process was left behind.

**What's still needed:** deterministic navigation through every page and an
image/layout oracle that can emit concrete diagnoses, without confusing a
legitimate data or theme difference with a real defect.

---

## 3. Visual type coverage

**Status:** closed on 2026-08-01.

The nine types with original data are present, plus the ten that were
missing: `gauge`, `kpi`, `donutChart`, `areaChart`, `scatterChart`,
`treemap`, `funnel`, `waterfallChart`, `multiRowCard` and `ribbonChart`.
`waterfall` is accepted as an alias, but written as `waterfallChart`, which
is the real name in the official catalog. Plus the composition ones.

**How to add one without repeating the `cardVisual` mistake:** roles are
**not deduced**. A visual is written for every (type, candidate role) pair,
the official CLI is run over the whole report, and whatever returns
`PBIR_ROLE_UNKNOWN` is read. The authoritative table comes out in one pass.
`tests/test_generadores_abren.py` has the sweep wired up.

---

## 4. Known roles not offered

**Status:** closed on 2026-08-01.

`ROLE_MAP` now exposes `tooltips`, `Y2` and `Rows`, plus the roles specific
to the ten new types. The names were checked with `catalog describe` from
the official CLI; the `abre` test generates every type/role pair and
requires zero `PBIR_ROLE_UNKNOWN` over the full report.

---

## 5. The minimum color of a gradient on a dark theme

**Status:** closed on 2026-08-01.

`pbi_set_conditional_format` takes the caller's colors. With `#FFFFFF`
as the minimum on a dark theme, low values end up **white on white**: the
cell gets painted and the number disappears.

The operation keeps the caller's explicit colors, but now reads the active
theme and warns if either end falls below 3:1. When painting the background
it compares against the theme's ink; when painting font or marks, against
the surface. So `#FFFFFF` on white text in the dark theme no longer passes
silently.

---

## 6. R15 — `both` blocked

**Status:** open by design. Analyzed in [`DUAL_MODE.md`](DUAL_MODE.md).

`live` requires Power BI Desktop **open**; `pbip` requires it **closed**.
They are mutually exclusive preconditions, so `both` used to produce a
deterministic partial state. It was blocked instead of faking atomicity.

It's not debt: it's a decision with its reasoning written down. Reopening it
requires turning it into a **guided workflow**, not an operation.

**Mitigation since v1.1.0:** `mode='auto'` resolves live/pbip from the REAL
state (verified session, not a stored field) before any effect, so the
build-from-scratch flow no longer trips over the `live` default. `both`
itself stays blocked; `auto` chooses one destination, never two.

---

## 7. G10 — three unpublished PBIR schema versions

**Status:** open **upstream**, not ours.

`visualContainer/2.10.0`, `visualContainer/2.11.0` and `bookmarks/2.0.0`
return 404 at Microsoft's official source. Their own CLI doesn't validate
them either: it emits `PBIR_SCHEMA_UNREACHABLE` and skips them.

There's nothing to do on our side until Microsoft publishes them.

---

## 8. `pbi_apply_plan` and contractual confirmation

**Status:** decided on 2026-08-04. The `plan_token` IS the explicit approval.

Reasoning: a client cannot hold a valid token by accident. It must call
`pbi_plan_change`, receive a token bound to a fingerprint of the state the
plan was computed on, and pass it back deliberately — and the token dies the
moment the state drifts (`plan_token_stale`). That is a stronger consent
mechanism than a boolean a client can hardcode. Requiring `confirm=true` on
top would be a second signature for the same act; flipping its default to
`false` would break the frozen contract to add no safety.

`confirm=true` stays as the historical default, documented as redundant with
the token. Nothing to do; the item is closed.

---

## 9. What we learned and must not lose

Three rules that were expensive to learn. They're in the code as comments,
but it's worth having them together:

1. **A green suite proves nothing if the same code being tested defines
   the correct shape.** You have to ask `TmdlSerializer` and the official
   CLI. `tests/test_generadores_abren.py`.

2. **A test that passes but wouldn't have caught the bug is worthless.**
   Verify by mutation: revert the fix and check that it fails, and that the
   message names the culprit line.

3. **What only runs on the developer's own machine only works there.**
   The clean install found two defects no test caught, because they all
   ran on an environment that was already fine. The suite keeps growing (1,700+
   tests), but the external oracles remain mandatory.
