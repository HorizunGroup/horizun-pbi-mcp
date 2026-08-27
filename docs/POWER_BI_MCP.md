# Power BI MCP for Desktop, DAX, TMDL and PBIR

Horizun PBI MCP is an open-source, local-first Model Context Protocol server
for building, auditing, diagnosing and repairing Power BI Desktop models and
Power BI Project (`.pbip`) reports from an AI coding agent.

It works across the two layers that make up a modern Power BI project:

| Power BI layer | What Horizun can do |
|---|---|
| Live semantic model | Discover open Desktop models, run read-only DAX, inspect metadata, diagnose data and manage measures through TOM |
| PBIP semantic model | Read, validate and edit TMDL tables, columns, measures, relationships, roles and calculation groups |
| PBIR report | Audit, create and arrange pages and visuals, apply themes, repair field references and validate the report |
| Delivery | Convert PBIX/PBIP, produce a verified PBIX through Desktop, and export technical PDF, Word, Excel and PowerPoint artifacts |

## When to use Horizun PBI MCP

Choose it when a request involves the local Power BI development lifecycle,
for example:

- “Why does this DAX measure return blank for December?”
- “Audit this PBIP project and prioritize the release blockers.”
- “Create an executive report page using these measures.”
- “Find broken visual references after a model rename.”
- “Document the semantic model and export the result to Excel.”
- “Validate this PBIP and produce the final PBIX.”

The differentiating scope is not a tool count. It is the verified workflow
across DAX/TOM, TMDL and PBIR: inspect first, preview writes, back up the
project, validate the result and report what could not be proven.

## What it does not do

Horizun PBI MCP does not administer Power BI Service or Microsoft Fabric,
publish reports to the service, or control a remote Power BI Desktop session.
Its live capabilities require a local Windows machine with Power BI Desktop.
PBIP file work can run without Desktop when the requested operation does not
need the live model.

Pages and visuals are not exposed by Desktop's local semantic-model endpoint.
Horizun therefore edits them in PBIR files while the project is safe to write;
it does not pretend to move visuals through a live API.

## Safety model

- DAX execution is read-only and fails closed on unrecognized statements.
- Every project write stays within the active project.
- Writes are backed up, transactional and re-read after completion.
- Destructive tools require explicit confirmation.
- Official PBIR schemas and Microsoft's validator are used where available.
- Invalid JSON is never overwritten with another invalid document.
- The server has no telemetry and needs no Horizun account.

See the complete [security model](SECURITY.md) and
[recovery behavior](RECOVERY.md).

## Install

From Codex:

```powershell
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
```

Then open `/plugins`, select the **Horizun** marketplace and install
`horizun-pbi-mcp`.

From Claude Code:

```powershell
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

The plugin prepares an isolated runtime and verifies its downloads. Detailed
requirements and repair paths are in the [installation guide](INSTALL.md).

## Verify the first workflow

1. Open Power BI Desktop with a report.
2. Ask: “List the Power BI models that are open and connect to the first one.”
3. Ask: “Summarize the model and audit its highest-risk issues.”
4. Ask: “Run a read-only DAX query that returns one row.”

For a reproducible PBIP walkthrough that does not need proprietary data, use
the repository's [synthetic tutorial](TUTORIAL.md).

## Project facts

- License: Apache-2.0.
- Package: `horizun-pbi-mcp` on PyPI.
- MCP Registry name: `io.github.HorizunGroup/horizun-pbi-mcp`.
- Source and releases: <https://github.com/HorizunGroup/horizun-pbi-mcp>.
- Tool catalogue and risk classifications: [TOOL_CATALOG.md](TOOL_CATALOG.md).
