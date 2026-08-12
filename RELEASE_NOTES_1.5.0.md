# Horizun PBI MCP v1.5.0

One new tool (134 total) and a batch of fixes with a single origin: **two real
build sessions** (a five-page, 42-visual report built end-to-end through the
server on 2026-08-11), triaged point by point against the code before touching
anything — about half the field report turned out to be already implemented.
What this release closes is the other half, and its theme is the mirror image
of 1.4.0's: last time the server produced artifacts that looked right and
weren't; this time it **accepted requests that looked accepted and weren't**.
An option that validates and materializes nothing is the worst class of bug
this product can have, because `ok: true` is the one answer nobody
double-checks.

## Added

- **`pbi_set_color_from_field`** — Power BI's "Field value" conditional
  formatting: a DAX measure returns `'#D03B3B'` (or a color name) and the
  visual applies it as-is. Hand-writing this had two known failure modes — one
  variant crashed the page render, another painted cards but not table cells.
  The which-form-works-in-which-visual matrix now lives in the server,
  validated against the official catalog, and a field-value rule and a
  gradient on the same field replace each other instead of coexisting hidden.

- **`pbi_set_conditional_format`** learns the two halves it was missing:
  `target_column` separates the gradient's INPUT from the column being painted
  (paint `semaforo` with `[Puntaje promedio]`), validated against the visual's
  projections — before, that request silently wrote a rule no one rendered and
  reported success. And `min_value` / `mid_value` / `max_value` anchor the
  gradient stops to data values, with ordering validated and the anchors
  checked by the PBIR barrier (`pbir_schema` now validates stop contents: one
  stray `expr` inside a stop used to leave the heat map rendering nothing with
  every validator green).

- **`pbi_validate_desktop_render`** accepts `page` (capture THAT page, not
  whatever was left active) and `fit_to_page` (default on), which writes the
  official `displayOption: FitToPage` so the capture shows the whole canvas
  instead of the top third at the saved zoom. Both adjust the view before
  opening and restore the files **byte-for-byte** afterwards — it is a view
  for the photo, not an edit of the report. The two layout defects of the
  field sessions were caught by the human's screen, not the tool; this is what
  closes that gap for remote verification.

- **`pbi_set_visual_title`**: `show=false` hides a title without deleting its
  text or format (the empty-text workaround kept the title band occupying
  height). **`pbi_apply_theme`**: `patch` merges partial changes over the
  report's current theme instead of resending it whole. Spec `format.header`
  toggles the slicer's field header. `layout_internal_void` flags a 4-column
  matrix stretched to 1248px — valid JSON, clean layout, giant hole inside its
  own box.

## Changed — deliberately stricter

Three behaviors are LOUDER than in 1.4.0. If your automation relied on the old
lax behavior, it will notice — that is the point:

- **Unknown spec options are rejected**, with the valid list and a hint
  (`style: "dropdown"` → it's `format.mode`; `min_value` in options → it's a
  `pbi_set_conditional_format` parameter). They used to be silently ignored.
- **`pbi_apply_page_spec` merge pairs by id first** (the deterministic id from
  the same seed, or the spec author's `id`), then by signature — and a
  signature match whose content differs is now a `page_conflict`. Two
  textboxes share a signature (no title, no fields): the spec's subtitle used
  to pair with the EXISTING title and silently replace its text. `replace`
  mode keeps its declare-the-whole-page semantics.
- **A project restored from `session.json` is not reactivated silently.** The
  first tool that needs one fails with the restored path and asks for explicit
  confirmation via `pbi_open_pbip_project` — it once ran the validator against
  the previous day's project without a word. `pbi_session_info` shows the
  pending candidate.

Also: **`pbi_normalize_page_layout` now executes the z-order autofix** the
analyzer had been advertising as `auto_fix_available: true` with no tool ever
running it — duplicated or mixed-missing z values are reassigned as a unique
sequence preserving the current stacking order.

## Verification

- 2168 tests passed, 1 skipped with its condition documented — final run on
  the tagged tree, packaging stages included (clean-environment install and
  stdio handshake reporting 1.5.0).
- Contract check exit 0: no breaking change against the frozen golden;
  134 tools, the 34-baseline contract untouched.
- `doctor.py` exit 0 without traceback; the three dependency fetchers verify
  by hash and exit 0.

## Known limits

Unchanged from 1.4.0 and documented in
[`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md): three PBIR schema
versions unpublished upstream, `both` mode blocked by mutually exclusive
preconditions, and full visual equivalence of the `objects` block still
partial.

Capture by `page` needs a `.pbip` (a compiled `.pbix` cannot be prepared
without editing it) and a session the tool opens itself: with a reused open
session, `page` fails with the remedy and `fit_to_page` degrades to a warning.
