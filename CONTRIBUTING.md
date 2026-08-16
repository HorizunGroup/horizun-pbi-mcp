# Contributing to Horizun PBI MCP

This server writes to other people's Power BI projects. A failure here doesn't produce an error: it **corrupts a report**, and the user finds out when they open it, far removed from the operation that caused it. The rules below exist because of that.

The full operating rules are in [`AGENTS.md`](AGENTS.md) and take priority.

---

## Before touching anything

```bash
python -m pytest -q
python -m ruff check src scripts tests
python -m mypy
python scripts/doctor.py
python -m tests.contract_utils
```

All five green. CI also measures the full suite with coverage and rejects totals
below 85%; the current measured baseline is 86%.

## The MCP contract is untouchable

The tools are frozen in `tests/golden/tools_v1.json`.

**Without explicit approval, forbidden:** removing or renaming a tool, removing a parameter, adding a **required** one, changing a type or a default value, changing the response shape.

**Allowed:** adding tools, adding **optional parameters with a default**, adding fields to the response dict, improving descriptions.

After a deliberate, approved change:

```bash
python -m tests.contract_utils --write
```

Never say "the contract didn't change" if you regenerated the golden. Report **breaks (0)** and **compatible (N)** separately.

## Invariants

1. **stdout is the JSON-RPC channel.** All logging goes to stderr or a file. A debug `print()` breaks the client connection.
2. **Never overwrite JSON that doesn't parse.** If it can't be read, abort.
3. **Every write to the user's project:** backup before, re-read after, rollback on failure.
4. **No write path leaves the active project.**
5. **No fields are invented.** If it doesn't exist, report it; don't guess.
6. **Destructive ones require `confirm=true`.**
7. **Prefer cloning a real template** over hand-building visual JSON.
8. **Fail-closed.** When in doubt, block. An `unsupported_feature` is better than a blind write.

## One transaction per logical operation

Forbidden:

- transaction inside a `for`;
- **calling in a loop a function that opens its own transaction** (the case the lexical check doesn't see);
- catching an exception to keep going after a failed mutation;
- returning `ok:true` with failed sub-operations;
- a decorated tool calling another decorated tool.

The correct pattern: **compile all changes in memory** → compute affected files → **one** transaction → validate → commit → verify.

```bash
python -m pytest tests/test_workflow_atomicity.py -q
```

Includes two static checks that fail if anyone reintroduces the pattern.

## Tests

A test that can't fail is worse than none: it gives false confidence.

**Forbidden:** `or True`, asserts on constants, mocks that verify their own value, overly broad `except`, tests without asserts, unmotivated skips.

**Every defect fix needs a regression test that fails against the previous commit and passes with the fix.** Check it:

```bash
git worktree add --detach /tmp/regression <previous-commit>
cp tests/test_new_thing.py /tmp/regression/tests/
cd /tmp/regression && python -m pytest tests/test_new_thing.py
```

If it passes there, the test isn't testing anything.

**Set up the precondition.** The `minimal` fixture has no interactions or references: a duplication test on it passes without checking anything. Use `tests/fixtures/rich.py` or build the scenario.

**Path traversal:** the "outside" is created **inside pytest's `tmp_path`** (`synthetic.outside_marker_dir()`). Never a real machine path.

## Real data: never enters git

| Never version | Do version |
|---|---|
| Real `.pbix`, `.pbip`, `.Report/`, `.SemanticModel/` | `tests/fixtures/synthetic/**`, `tests/fixtures/rich.py` |
| `libs/` (Microsoft DLLs) | `scripts/fetch_libs.py` + `libs_manifest.json` |
| `schemas_cache/`, `validator_cache/` | manifests with URLs and hashes |
| `outputs/`, `backups/`, `*.log` | `*.example.*` templates |
| `.env`, `.mcp.json` | `.env.example` |

Synthetic fixtures **contain no** commercial names, data, paths or GUIDs from any real project. There are tests that verify this.

## Dependencies

**Exact** version and **hash verified before installing**, across all three chains: Analysis Services DLLs, PBIR schemas and Microsoft's official CLI.

Never `latest`, never `npx -y`, never download during a normal operation. Fail closed if the hash doesn't match.

## Commits

- No remote, no `push`, no publishing packages.
- **One commit per phase**, thematic and reversible.
- Don't mix functional fixes with documentation or cleanup.
- The message explains **what was wrong**, not just what was changed.
- `a304e33` is the baseline: **it is not rewritten**.

## Style

- Code comments: **why**, not what. If the code says what it does, the comment is redundant.
- Names and messages in Spanish, like the rest of the repository.
- Error messages say **what happened, where, and what to do**. They never include values from the user's report nor personal paths.
