# Tutorial: from installation to a dashboard

A full walkthrough using the repository's **synthetic fixture**. You don't need Power BI Desktop for steps 1–7.

---

## 1. Install

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/fetch_libs.py
```

```bash
python scripts/doctor.py
```

Must end with `RESULTADO: instalacion operativa` and exit code **0**.

---

## 2. Register with your client

```bash
python scripts/make_mcp_config.py --client all
```

Prints the snippet for Claude Code, Claude Desktop, Codex and generic stdio, with already-resolved absolute paths. Detail in [`INSTALL.md`](INSTALL.md).

---

## 3. Get your bearings

> Check the server's status and tell me what I can do right now.

`pbi_health_check` and `pbi_capabilities`. The second one says what's available **and what isn't, with the reason** — including that `mode="both"` is disabled and why.

---

## 4. Open the project

> Open the project `tests/fixtures/synthetic/minimal/Demo.pbip` and give me a model summary.

`pbi_open_pbip_project` → `pbi_model_summary`. You'll see 2 tables, 6 columns, 2 measures, 1 relationship.

---

## 5. Understand before touching

> What does the measure `Ratio Pct` depend on, and who uses it?

```
depends_on.measures : ['TotalAmount']
used_by             : []
is_unused           : True
```

> What breaks if I hide `Fact[Amount]`?

`pbi_column_dependencies` → `TotalAmount` uses it.

---

## 6. Audit

> Audit the whole project and generate the report in HTML.

`pbi_audit_project(formats=["html"])`. Returns an overall score and per-domain scores, an executive summary and prioritized findings with evidence. The HTML ends up in `outputs/`.

---

## 7. Build a dashboard

> Build an executive page with `TotalAmount` and `Ratio Pct`, by `Calendar[Year]`. Show me the preview before applying.

```
pbi_build_executive_page(measures=[...], category="Calendar[Year]")   # dry_run by default
```

Returns the `analysis → plan → preview` stages. Review the preview's HTML: **the positions are final**.

> Apply it.

```
pbi_build_executive_page(..., dry_run=false)
```

Adds `apply` and `verification`. Check `valid: true` and `broken_references: []`.

---

## 8. Iterate safely

> Duplicate the `TotalAmount` card and title it "Accumulated amount".

`pbi_duplicate_visual` keeps fields and format; only regenerates the id.

> Are there layout issues on that page?

`pbi_detect_layout_issues` — overlaps with exact area, off-canvas, sizes, Z order.

> Normalize it.

`pbi_normalize_page_layout(dry_run=true)` first: it fixes **only** what's wrong.

---

## 9. Undo

> Show me this project's journals.

`pbi_list_pending_journals(only_pending=false)` → one per logical operation.

> Inspect the last one.

`pbi_inspect_journal` tells you, per file, whether it still matches the original and whether there's a backup. To restore, see [`RECOVERY.md`](RECOVERY.md).

---

## 10. Prepare the delivery

> Is it ready to deliver?

`pbi_prepare_delivery` returns a checklist with blockers and the available fix plan.

> Apply only the fixes for missing titles.

```
pbi_plan_audit_fixes(rules=["report_visual_without_title"])
pbi_apply_audit_fixes(actions=[...], confirm=true)
```

There's no "fix everything": rules must be named.

> Generate the technical documentation.

`pbi_generate_technical_documentation` → Markdown with the model, dependencies, page-by-page report and audit.

---

## 11. With Power BI Desktop open

Only the **live** layer:

> List the open models, select the only one, and run `EVALUATE ROW("ok", 1)`.

Remember:

- `mode="live"` doesn't persist until you save with **Ctrl+S**.
- With Desktop open, **PBIR writes are blocked** on purpose.
- `pbi_compare_live_to_pbip` tells you if there are unsaved in-memory changes.

---

## Errors you'll see, and what they mean

| Error | What to do |
|---|---|
| `project_open_in_desktop` | Close Desktop. It's intentional |
| `dual_mode_not_safely_available` | Choose `live` or `pbip` |
| `dax_not_read_only` | Only `EVALUATE`, `DEFINE…EVALUATE` and DMVs |
| `plan_token_stale` | The project changed: regenerate the plan |
| `page_spec_invalid` | Check `details.errors`: they carry the exact JSON path |
