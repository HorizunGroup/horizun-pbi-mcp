## Summary

Describe the problem and the outcome of this change.

## Validation

- [ ] `python -m pytest -q`
- [ ] `python scripts/doctor.py`
- [ ] `python -m tests.contract_utils`
- [ ] No real `.pbix`, `.pbip`, credentials, DLLs, outputs or backups are included
- [ ] User-facing behavior and documentation are updated when applicable

## Contract and risk

- [ ] No MCP tool, parameter, default or response shape was changed incompatibly
- [ ] New tools are classified in `src/horizun_pbi_mcp/tools/risk.py`
- [ ] Mutations create a backup, validate after writing and roll back on failure

## Evidence

Link the issue and include the smallest useful logs, screenshots or test output.
Remove project data, queries, credentials and customer names before posting.
