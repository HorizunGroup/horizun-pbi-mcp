# Security guide

What this server protects, how, and what it **cannot** promise.

---

## 1. Threat model

This MCP receives instructions from an LLM and writes to the user's files. The real threats aren't a remote attacker, but:

| # | Threat | Mitigation |
|---|---|---|
| T1 | A page or visual id with path syntax writes outside the project | `services/paths.py` |
| T2 | A change made by another process gets overwritten | sha256 fingerprints verified three times |
| T3 | Power BI Desktop overwrites what we write | Strict policy |
| T4 | A multi-file operation leaves the project half-done | Compensated transaction with journal |
| T5 | Non-read-only DAX gets run | Fail-closed classifier |
| T6 | A secret ends up in the log | Redaction in `services/telemetry.py` |
| T7 | A backup inside the `.pbip` corrupts it | Validated destination |
| T8 | Operating against a session that's no longer the same one | Session fingerprint |
| T9 | A forged Graph pagination URL receives an access token | HTTPS host/port validation before following every `nextLink` |
| T10 | A remote SharePoint name escapes the download directory or leaves a partial batch | Component validation, containment, staging and atomic directory publish |

---

## 2. Paths: identifiers, not paths

A page or visual id is an **identifier**. It's rejected, before touching disk: separators, `..`, absolute paths, drive syntax (`C:\x` and `C:x`), UNC, `\\?\`, `\\.\`, NTFS ADS (`file.json:stream`), reserved names (`CON`, `NUL`, `AUX`, `COM1`…), empty components and ones with a trailing dot or space.

Containment resolves junctions and compares with `normcase` (NTFS is case-insensitive) and **re-validates right before writing**: a link can change between validation and write.

> `Path('base') / 'C:/other'` returns `C:/other`. That's why each component must be validated before joining.

---

## 3. DAX: fail-closed

Lexical scanner first (comments, strings, quoted and bracketed identifiers), classification after. Only three shapes are allowed:

```
EVALUATE ...
DEFINE ... EVALUATE ...
SELECT ... FROM $SYSTEM....
```

Everything else is rejected, including the ambiguous: XMLA, DDL, `;`, `DEFINE` without `EVALUATE`, `SELECT` whose `FROM` isn't `$SYSTEM.`, concatenated tokens and unclosed delimiters.

Since literals are neutralized first, `EVALUATE ROW("DROP TABLE", 1)` **is still read-only**.

**No escape hatch.** There's no environment variable that relaxes it; there's a test that verifies it.

---

## 4. Power BI Desktop policy

| State | PBIR write |
|---|---|
| Verified `closed` | Allowed |
| `open` | **Blocked** |
| `unknown` | **Blocked** |

**Read-only** signals only: processes, command line and open files. A real file is never mutated to probe whether it's locked.

**Honest limit:** this doesn't prevent Desktop from overwriting the report *afterward*. It only avoids *us* writing when there are signs it's open. The error message says so.

---

## 5. Writes: compensated transaction

```
PLAN → fingerprint of each target
SNAPSHOT → copy to the journal + manifest
PRE-CHECK → re-verify right before replacing
WRITE → tmp → flush → fsync → validate → os.replace → clean up
POST → re-read and compare
COMMIT | ROLLBACK
```

**There's no file-system multi-file atomicity.** Between the first and last `os.replace` there's a window. What's guaranteed: it's short, the journal allows going back, and **success is never reported if the rollback wasn't clean**.

The rollback **doesn't overwrite external changes**: if someone touched the file after our write, it's marked `rollback_conflict` and the journal is kept.

---

## 6. `mode="both"`: disabled

`live` needs Desktop **open**; `pbip` needs it **closed**. There's no system state in which both are safe in one call. It used to apply `live` and fail on `pbip`, leaving a deterministic partial state.

Now it's rejected **before any effect**. No bypass.

---

## 7. Logs, repository and external boundary

The log only records the **shape**: `<15 chars>`, `<2 items>`. Never the content of `query`, `dax`, `expression`, `rows`, `spec`, `html`, `password`, `token`… Paths are shortened to two segments and `Password=…` patterns are masked.

Never enter the repository: real `.pbix`, `.pbip`, `.Report/`, `.SemanticModel/`, `libs/`, `outputs/`, `backups/`, `.env`, `.mcp.json` or credentials. Versioned fixtures are 100% invented.

---

### SharePoint external boundary

The two SharePoint tools are the only open-world operations. They authenticate
app-only with MSAL, reading tenant, client id and client secret from the process
environment. Secrets are never MCP parameters and tokens are never returned.
The bearer token is sent only to `https://graph.microsoft.com:443`; the
temporary download URL receives no bearer header. Site/library/folder
identifiers necessarily go to Microsoft Graph, and selected files travel only
from SharePoint to the configured local `outputs/sharepoint/` directory. No
local model, report, Excel or PDF content is uploaded.

Use `Sites.Selected` with an explicit grant to only the required sites whenever
the tenant permits it. Broader permissions such as `Sites.Read.All` expand the
blast radius and are a deliberate administrator decision.

## 8. What this server does **not** do

| Doesn't do | Why |
|---|---|
| Interactive/delegated Microsoft login | SharePoint uses an application identity; no user-token flow |
| Upload, move or delete in SharePoint | The connector is intentionally inbound/read-only remotely |
| Publish to the Power BI Service | Local only |
| Write via arbitrary XMLA | There's no safe way to bound it |
| Send local Power BI content to Graph | SharePoint support only lists and downloads remote files |
| Resume journals on startup | Could be worse than leaving it alone; see `RECOVERY.md` |
| Guess the target of a broken reference | Requires an explicit `mapping` |
| "Fix everything" | Autofixes are chosen by rule and object |

---

## 9. If you find a security bug

Reproduce it with a synthetic fixture and a `tmp_path`. **Never use a real project** to demonstrate a write failure: a test's "outside" must live inside pytest's `tmp_path` (`synthetic.outside_marker_dir()`).
