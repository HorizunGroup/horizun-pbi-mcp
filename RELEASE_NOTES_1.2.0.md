# Horizun PBI MCP v1.2.0

The four phases of the product vision, shipped. Six new tools (127 total),
zero breaking changes.

They are not four loose features — they chain into one system:

```
port keys → brief critical_fields → diagnostics escalate to error
```

`pbi_check_contract` returns the port's keys; they go into `pbi_define_brief`;
from then on an orphan key on `HRZ_COD_PRES` stops being a generic warning and
comes back as an **error citing the owner's own reason**.

## Added

- **Intent brief** — `pbi_define_brief`, `pbi_get_brief`. What the dashboard is
  FOR, versioned next to the `.pbip`. The answers belong to the human: an empty
  purpose errors with an instruction to ask, and a project without a brief gets
  the questions, not an empty form. Read by `pbi_start_here`,
  `pbi_propose_dashboard` and `pbi_list_design_systems`.
- **Content-level diagnostics** — `pbi_diagnose_data`. Orphan keys, duplicated
  grain, calendar gaps and the brief's thresholds, each with the DAX that
  proves it and sample culprits. Severity is the owner's call.
- **External sources** — `pbi_add_table_from_source` (SQL Server, PostgreSQL,
  OData, Web JSON), with the credentials truth up front in every response.
- **Ecosystem port** — `pbi_define_port_contract`, `pbi_check_contract`. A data
  contract with a shared key, not an API bus between desktop apps.

## The discipline behind it

Each phase had a chance to fake capability, and each one refuses in writing:

- A diagnostic check that blows up lands in `skipped` — with skips the result
  is never `clean`.
- The server cannot verify an external connection, and says so every time.
- The contract file check states what it did **not** check.

## Evidence

- 127 tools; MCP contract: additive only, golden updated.
- 1793 tests passed, 3 skipped with their condition documented.
- Every behavioral change mutation-verified: reverting it fails a named test.
- `scripts/doctor.py` exit 0; CI green on Windows, Python 3.10 and 3.13.
