# Release checklist

What gets checked before tagging a release. Every line is a command, not an intention.

---

## 1. Tree and contract

```bash
git status --short
```

Empty. Nothing unversioned that should be.

```bash
python -m tests.contract_utils
```

Exits with **0**. If it exits 1, it says what changed and **whether it breaks compatibility**. An approved compatible change is frozen with `--write`; an incompatible one isn't published.

## 2. Full suite

```bash
python -m pytest -q
```

**Without excluding `packaging`.** Skips must be environmental only and say how to run them.

## 3. Diagnostics

```bash
python scripts/doctor.py
```

Exits with **0** and **no traceback**. An exit 0 with a printed traceback doesn't count: `doctor` and the contract check share the server's logger, and a rotation failure used to dirty stderr.

## 4. Packaging

Wheel and sdist are built and installed in a clean environment, and the server starts **from the installed package, outside the repository**:

```bash
python -m pytest -m packaging -q
```

It also checks that the wheel:

- includes `services/`, `reporting`, `branding` and the schema manifest;
- does **not** include DLLs, fixtures, `outputs/`, `backups/` or third-party schemas;
- responds to the stdio handshake with `serverInfo.name = horizun-pbi-mcp` and the product version.

## 5. Verified dependencies

```bash
python scripts/fetch_libs.py --check
python scripts/fetch_pbir_schemas.py
python scripts/fetch_report_validator.py --check
```

All three verify the **hash before installing** and fail closed. None uses `latest` or `npx`.

## 6. No real data or personal paths

```bash
git status --short --ignored
```

`libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`, `.env` and `.mcp.json` **ignored**.

And on the versioned tree, no matches for: user paths, real project names, credentials, tokens.

## 7. Validation on a real `.pbip`

On a **copy** outside OneDrive, never on the original:

1. full fingerprint of the original **before**;
2. read-only smoke test with Desktop open;
3. close Desktop **without saving**;
4. PBIR operations on the closed copy: audit, dry-run, apply, page update, duplication, deletion, workflows;
5. injected failure → **byte-for-byte rollback**;
6. recovery from journal;
7. open the copy in Desktop: it must load with no new error;
8. guard: with the project open, the write is **blocked**; with **another** project open, there's **no** false block;
9. remove only our own leftovers;
10. fingerprint of the original **after**: identical.

## 8. Consistent documentation

- Tool and test counts taken from the **final run**, not estimated.
- Limitations described as they are: unpublished upstream schemas, `both` blocked, and full visual equivalence of `objects` still partial.
- No example with personal paths.

---

## Accepted exceptions — do not block the stable release

| Exception | Why |
|---|---|
| `visualContainer/2.10.0`, `2.11.0` and `bookmarks/2.0.0` unpublished | 404 at the official source; Microsoft's own CLI doesn't validate them either |
| **G10** partially closed | Direct consequence of the above |
| **R15** open, `both` blocked | Mutually exclusive preconditions ([`DUAL_MODE.md`](DUAL_MODE.md)) |
| **Full visual equivalence of `objects` partially closed** | The managed paths are checked against the official `effective-properties` and an anonymized corpus of shapes Desktop exported. This proves structure, properties, types and enums; it doesn't yet demonstrate the semantic pixel-level result for every possible combination |
| Pre-existing errors in the user's report | Never fixed automatically |
| Three skipped tests | Require Desktop open or a model precondition |

---

## Tagging

The release candidate version is **`v1.5.1`**. The exceptions below are documented
product limits, not hidden bugs or skipped criteria.

The version declared in `branding.VERSION` / `pyproject.toml` must match the tag **before** tagging. Installing from a tag and getting a package that reports a different version is exactly what these checks exist to prevent.

> **The clean install is not a formality.** The first time these five stages
> were run, on `v1.0.0-rc.11`, two defects appeared
> that none of the 1255 tests saw —because they all ran on the
> development environment, which was already fine—:
>
> - `requirements.txt` had diverged from `pyproject.toml` on **six**
>   dependencies. With no cap, `mcp>=1.10` installed 2.0.0 —where
>   `mcp.server.fastmcp` no longer exists— and the server didn't even get to
>   import. `jsonschema` and `referencing` were missing entirely.
> - `doctor.py` checked three of the six dependencies, so an incomplete
>   install reported "Python dependencies: OK".
>
> Both were covered with tests in `tests/test_packaging.py`, but the
> lesson is the stage, not the fix: **what only runs on the developer's own
> machine only works there.**

For this release, an isolated reproducible environment plus the CI matrix
on clean GitHub machines is accepted; a second physical machine isn't needed:

1. export the versioned tree and create a virtual environment with no repository packages;
2. install following the README, no shortcuts;
3. `pytest`, `doctor` and contract check green **there**;
4. register the MCP with a client and check the handshake;
5. the CI matrix **fully** green, with no jobs skipped for a dependency.
