# Horizun PBI MCP v1.0.1

First corrective update to the stable release. Keeps the 117-tool
contract intact and closes four validation gaps detected after
publishing `v1.0.0`.

## Fixed

- The official oracle now also reviews visuals that only contain
  `visualContainerObjects`.
- Empty format expressions (`expr: {}`) are rejected before writing.
- Downgrading to an earlier PBIR schema version is only allowed for
  URLs the manifest expressly identifies as not published by
  Microsoft.
- An already-open PBIP session is reused before validating the state saved
  on disk, avoiding blocking a valid model that Desktop already serves in memory.

## Evidence

- 117 tools; MCP contract unchanged.
- 1547 tests passed and 3 skipped due to documented external preconditions.
- CI green on Windows with Python 3.10 and 3.13.
- Wheel and sdist built and verified with `twine check`.
- `scripts/doctor.py` operational with DLLs, PBIR schemas and the official CLI present.
