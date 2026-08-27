---
name: powerbi-project-workflows
description: Build, audit, diagnose, repair, validate, document, and deliver local Power BI Desktop models and PBIP projects with Horizun PBI MCP. Use for DAX, semantic models, TMDL, PBIR, report pages, visual layout, PBIX/PBIP conversion, and pre-delivery checks; not for Power BI Service or Fabric cloud administration.
---

# Power BI project workflows

Turn a plain-language Power BI request into a verified result with the
`pbi_*` tools. Prefer the high-level workflow tool that matches the requested
outcome; use lower-level tools only when the workflow needs diagnosis or a
precise edit.

## Establish the target

- If readiness or the active target is unclear, call `pbi_health_check` and
  `pbi_capabilities` before doing work.
- For an open Desktop model, use `pbi_list_desktop_models`, then
  `pbi_select_model`. Never guess which model the user means when several are
  open.
- For a file or folder, use `pbi_prepare_project` with the exact path. It
  resolves `.pbip`, converts a `.pbix` when requested, and rejects ambiguous
  folders instead of choosing a project silently.
- Keep the live semantic-model layer separate from the PBIP file layer.
  Desktop exposes DAX/TOM data and model operations; pages and visuals are
  authored in PBIR files while the project is safe to write.

## Route by outcome

- **Audit or explain:** start with `pbi_model_summary` and
  `pbi_audit_project` using `compact=true`. Use `pbi_audit_model`,
  `pbi_audit_report_only`, or `pbi_diagnose_data` only when the user needs that
  narrower evidence. Data diagnosis requires a live, loaded Desktop model.
- **Diagnose DAX:** inspect the measure and its dependencies, reproduce the
  symptom with read-only `pbi_run_dax`, and use `pbi_validate_measures` before
  creating or updating a measure. Do not treat a syntactically valid measure
  as semantically correct without a representative query.
- **Build a page or dashboard:** prefer `pbi_build_dashboard`,
  `pbi_build_executive_page`, or `pbi_generate_report_page`. Run the preview or
  `dry_run=true` path first, verify referenced measures and fields, then apply
  only when the user's request authorizes the write.
- **Repair quality findings:** audit first, select concrete rules, call
  `pbi_plan_audit_fixes`, show the proposed actions, and pass only those
  actions to `pbi_apply_audit_fixes`. Never invent an "apply everything"
  operation.
- **Prepare delivery:** call `pbi_prepare_delivery` in dry-run mode, resolve
  blockers, validate the project, and generate the requested documentation or
  exports. Use `pbi_finalize_delivery` only when the user asked for the final
  deliverable and the target path and overwrite behavior are unambiguous.

## Safety boundaries

- The server is local-first. Do not claim that it administers Power BI Service
  or Fabric, publishes to the service, or can control a remote Desktop session.
- Do not use `mode="both"`; live and PBIP writes cannot be made safely in the
  same call.
- Preserve dry-run defaults. A request to inspect, diagnose, audit, review, or
  explain does not authorize applying changes.
- Never close Desktop, overwrite a deliverable, delete model objects, or run a
  destructive action without the explicit confirmation required by that tool.
- Do not bypass project-state checks, backups, transactions, schema
  validation, or post-write verification. If validation cannot prove the
  result, report that limitation instead of declaring success.

## Report the result

Lead with what was accomplished. Include the target model or project, the
evidence used, changes made, validation outcome, generated artifact paths, and
any remaining warnings. Distinguish "not checked" from "passed" and a preview
from an applied change.
