# Publishing: how it was done and how to repeat it

> **Historical document (v1.0.0 era).** The live release procedure is
> [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md); the version numbers and
> commands below describe the ORIGINAL publication and are not updated.

This document describes the official publication: the historical repository
is kept as a private legacy, and the public version is exported with fresh
history from a verified tree.

Public repository: **https://github.com/HorizunGroup/horizun-pbi-mcp**

---

## The problem that had to be solved

The versioned tree was clean. The **history wasn't**: two blobs from the two
oldest commits contained personal paths (`C:/Users/<user>/...`) as an example
value in `.env.example` and `examples/mcp-config.example.json`. They're no
longer in the current tree, but they were still reachable from history.

And `AGENTS.md` states that **`a304e33` is not rewritten**.

## The decision

**The development repository is neither filtered nor rewritten.** A sanitized
copy is exported to another directory and that one is published, with
**fresh single-commit history**.

The development commits stay only in the local repository, which **has no
remote** and won't get one.

---

## Procedure

### 1. Export the versioned tree

```bash
mkdir C:\tmp\horizun-publish
```

```bash
git archive --format=tar HEAD | tar -x -C C:\tmp\horizun-publish
```

`git archive` exports exactly what `git ls-files` lists, and nothing more.
`libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`,
`build/`, `.mcp.json` and `.env` are left out **by construction**, not
because someone remembered to exclude them.

### 2. Verify the copy before touching anything external

Compare the inventory against `git ls-files`, and scan for personal paths,
credentials and real data. Then, a full installation **from the copy**:

```bash
python -m pip install -e .
python scripts/fetch_libs.py
python scripts/fetch_pbir_schemas.py
python scripts/fetch_report_validator.py
python -m pytest -q
python scripts/doctor.py
python -m tests.contract_utils
```

The last three green, and the stdio handshake against the installed package.

### 3. Fresh history

```bash
git init -b main
git add -A
git commit -m "Horizun PBI MCP v1.0.0"
```

**Before tagging**, check that `branding.VERSION` and `pyproject.toml`
declare that same version. Tagging a commit that declares another one
produces a package that lies about what it is — it happened with `rc.2` and
had to be fixed.

```bash
git tag -a v1.0.0 -m "Horizun PBI MCP v1.0.0"
```

### 4. Publish

```bash
gh repo create horizun-pbi-mcp --public --source=. --remote=origin
git push -u origin main
git push origin v1.0.0
gh release create v1.0.0 --notes-file docs/releases/RELEASE_NOTES_1.0.0.md --verify-tag
```

### 5. Wait for CI

**Not declared done until the matrix is fully green.** The gates run on
clean GitHub machines; local validation uses a virtual environment with no
repository packages as reproducible evidence.

```bash
gh run list --repo HorizunGroup/horizun-pbi-mcp
gh run view <RUN_ID> --repo HorizunGroup/horizun-pbi-mcp
```

---

## Subsequent updates

The sanitized copy keeps its `.git` and its remote. To publish a change:

```bash
cd C:\tmp\horizun-publish
git fetch origin && git reset --hard origin/main
```

The tree is re-exported from the development repository, synced onto the
copy, and **a new commit** is made on `main`. **An already-published commit
is never rewritten.**

---

## What was published

| Directory | Files |
|---|---|
| `src/` | 73 |
| `tests/` | 61 |
| `docs/` | 13 |
| root | 12 |
| `scripts/` | 7 |
| `examples/` | 4 |
| `.github/` | 1 |
| **Total** | **171** |

The three external dependencies —Analysis Services DLL, PBIR schemas and
Microsoft's official CLI— **are not redistributed**: they're installed with
their own scripts, with a pinned version and verified hash.

## Repository configuration

| Setting | Value |
|---|---|
| Main branch | `main` |
| CI | `windows-latest`, Python 3.10 and 3.13 |
| Releases | stable `v1.0.0`, with CI on clean machines and isolated local validation |

**Pending manual configuration** (needs permissions the CLI token doesn't
have): branch protection on `main` — require PR, require green CI, prohibit
force-push. Done in *Settings → Branches → Add rule*.
