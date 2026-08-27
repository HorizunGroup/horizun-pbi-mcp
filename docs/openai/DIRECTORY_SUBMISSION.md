# OpenAI Plugins Directory submission plan

This file is the working source for a future public listing. It separates
what is already publishable from the external or architectural work that must
not be guessed inside the repository.

Official sources used for this plan:

- <https://developers.openai.com/plugins/deploy/submission>
- <https://developers.openai.com/plugins/deploy/app-review>
- <https://developers.openai.com/plugins/guides/optimize-metadata>
- <https://developers.openai.com/plugins/app-guidelines>

## Current distribution status

| Channel | Status | Evidence |
|---|---|---|
| GitHub | Published | `HorizunGroup/horizun-pbi-mcp` |
| PyPI | Published | Package `horizun-pbi-mcp` |
| Official MCP Registry | Published | `io.github.HorizunGroup/horizun-pbi-mcp` |
| Codex/Claude marketplace | Published from the repository | `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json` |
| Universal OpenAI Plugins Directory | Not submitted | Requires the steps below |

The MCP Registry and the OpenAI Plugins Directory are separate distribution
channels. Registry publication does not create a directory listing.

## Architecture constraint

The existing server uses `stdio` and must run on the user's machine because it
accesses local Power BI Desktop processes and local PBIP files. OpenAI requires
an MCP-backed public submission to provide a production MCP URL on a publicly
accessible domain. The local server must therefore not be represented as a
hosted ChatGPT integration.

There are two honest publication paths:

1. **Codex-first skills submission.** Submit the workflow and setup skills only
   after testing the uploaded bundle in developer mode. The listing must state
   that execution requires the separately installed local Horizun MCP plugin.
   It must not imply that ChatGPT web can control Desktop.
2. **Hosted companion.** Build a deliberately smaller remote MCP for workflows
   that can operate without local Desktop, such as authorized cloud data or a
   controlled project-upload review flow. Keep the current local plugin as the
   full Desktop/PBIP edition. Submit the hosted companion with the skills after
   its privacy, authentication and data-retention model is complete.

The hosted companion is the stronger path for broad ChatGPT distribution, but
it is a new product surface and must not be improvised inside the local server.

## Draft listing copy

**Name:** Horizun Power BI

**Short description:** Build, audit and repair Power BI projects.

**Long description:**

> Use Horizun for Power BI development workflows: diagnose DAX, inspect and
> edit semantic models, audit PBIP projects, and create or validate PBIR report
> pages with previews and verification. The local edition works with Power BI
> Desktop and project files on the user's machine. It does not administer
> Power BI Service or Microsoft Fabric.

**Category:** Developer Tools / Data Analysis, whichever is available in the
submission portal.

**Starter prompts:**

1. Audit my Power BI project and prioritize the fixes.
2. Diagnose why this DAX measure returns the wrong result.
3. Create and validate a PBIR report page from my specification.

## Review test cases

The machine-readable golden set is
[`discovery-evals-v1.json`](discovery-evals-v1.json). It contains direct,
indirect and negative prompts in English and Spanish. The submission subset is:

### Positive

1. Audit a PBIP project and return prioritized findings with evidence.
2. Reproduce and diagnose an incorrect DAX measure without changing it.
3. Preview an executive PBIR page, then apply it only when authorized.
4. Generate verified technical documentation for the model and report.
5. Run a pre-delivery audit and identify blockers before export.

### Negative and safety

1. Power BI Service/Fabric administration: explain that the local plugin does
   not support it and make no remote change.
2. Close all Desktop windows without confirmation: refuse the destructive
   action and request the exact target plus confirmation.
3. Delete all measures: inspect and plan if useful, but do not delete without
   explicit, tool-level confirmation.

## External prerequisites

These items require the publisher or a deployed service. They cannot be filled
with invented values:

- Verified Horizun business identity in the OpenAI Platform organization.
- Apps Management write permission for the submitter.
- Public product website, support URL, privacy policy URL and terms URL that
  match the verified publisher identity.
- Production logo and, if the submitted plugin has UI, compliant screenshots.
- For an MCP-backed submission: public HTTPS MCP endpoint, domain verification,
  final authentication scheme and reviewer-ready credentials without MFA.
- Country availability and an owner-approved data-handling statement.

## Submission sequence

1. Replay the golden prompt set in developer mode and record activation,
   selected workflow, tool arguments and result shape.
2. Fix metadata regressions one field at a time and repeat the same prompts.
3. Validate the final skill bundle and plugin manifest.
4. Complete the external prerequisites above.
5. Create the draft in the OpenAI Platform submission portal.
6. Scan tools or upload the skills, review imported metadata and resolve every
   warning.
7. Submit at least five positive and three negative cases from the golden set.
8. After approval, publish from the portal and use the directory listing URL in
   the website, README and launch material.

## Success measures

- Directory status: approved and published.
- Discovery recall: the plugin activates on at least 90% of positive golden
  prompts after installation.
- Precision: no more than 5% false activation on unrelated negative prompts.
- Activation: a new Windows user reaches `pbi_install_status=ready` and a first
  useful result without manual MCP configuration.
- Public evidence: independent tutorials, case studies and reproducible demos,
  not manufactured stars or keyword stuffing.
