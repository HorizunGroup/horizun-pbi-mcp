# Horizun PBI MCP v1.0.0

First stable release of the official Horizun PBI MCP repository.

- 117 MCP tools and a contract compatible with the original 34 tools.
- 1542 tests passed; 3 skipped solely due to documented external
  conditions.
- TMDL/TOM validation before opening Power BI Desktop, avoiding the Frown for
  projects with name collisions.
- Preflight for empty models: returns an actionable diagnosis instead of
  waiting for a Desktop timeout with no engine served.
- PBIR validation against official schemas and CLI when published,
  structural oracle for `objects`, atomic transactions, journals,
  backups and rollback.
- Distribution for Codex and Claude via an isolated runtime; no Microsoft
  DLLs or third-party schemas are redistributed.

## Known limits

- Full visual equivalence of the `objects` block requires rendered
  inspection for combinations not covered by the oracle.
- `mode="both"` is blocked by design: open Desktop and safe PBIP writing
  are incompatible preconditions.
- Two PBIR schemas Microsoft has not yet published remain an upstream
  limitation and are explicitly blocked.
