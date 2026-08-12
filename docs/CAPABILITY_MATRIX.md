# Capability and coexistence matrix

_Generated in Phase 0. Inspection date: 2026-07-30._

## How to read this matrix

Every statement carries its **verification level**. They are not mixed.

| Level | Meaning |
|---|---|
| **Tested** | Run on this machine and the result observed |
| **Observed** | Read from the installed artifact (code, bundle, manifest) without running it |
| **Declared** | Stated in its documentation; not checked |
| **Pending** | Couldn't be verified without violating a Phase 0 constraint |

---

## Servers inspected

| Server | Version | Status | Highest level reached |
|---|---|---|---|
| **Horizun PBI MCP** (this repo) | 1.5.0 | Present, starts | **Tested** — stdio handshake, 134 tools, live DAX, PBIR fixture, verified Excel/PDF exports, Desktop capture by PID and simulated Graph folder traversal/download |
| **powerbi-report-mcp** | 0.9.6 | Present and built at `..\PowerBI MCP\powerbi-report-mcp\dist\index.js` | **Observed** — 57 tool names extracted from the bundle and the README. **Not run** |
| **@microsoft/powerbi-modeling-mcp** | 0.5.0-beta.11 | Downloaded to a temp folder, extracted and read. **Not run** | **Declared** — README + CHANGELOG + `index.js`. Execution **halted** due to a stop condition (§2.1) |

### Provenance of the Microsoft package (inspection on 2026-07-30)

| Field | Value |
|---|---|
| Package | `@microsoft/powerbi-modeling-mcp` |
| Pinned version | `0.5.0-beta.11` (**not** `@latest`) |
| Origin | `https://registry.npmjs.org/@microsoft/powerbi-modeling-mcp/-/powerbi-modeling-mcp-0.5.0-beta.11.tgz` |
| SHA-1 | `fe7552d74cd3093a6935a11f7365c5eeffaa8ea1` — **verified** against the download |
| Integrity | `sha512-a5aO6glpBFIlaHHe+8LRunNPExJqsbnskRHDW5y7Vb7Jac85KqMUUEvxRuL2IkwJDDng0FEhfZNUbqw3ehmQIw==` |
| License | **Microsoft Software License Terms (PREVIEW)** — proprietary, not OSS |
| Repository | `github.com/microsoft/powerbi-modeling-mcp` |
| Method | `npm pack` to a temp directory. **No global install. No `npx -y`. No MCP configuration touched** |
| Channel status | Only `0.5.0-beta.*` versions exist. **No stable release** |

---

## 2.1 Why execution was halted

The authorization said: *"If the package requires authentication or a connection with external effects, stop."* **Three** stop conditions are met, found by reading the package before running it:

**1. Telemetry to Microsoft from mere use.** README §Data Collection:

> "The software may collect information about you and your use of the software and send it to Microsoft. […] **Your use of the software operates as your consent to these practices.**"

Starting it is a connection with external effects and consents to sending data. That's not a decision an agent should make.

**2. License acceptance by use.** LICENSE, header: *"BY USING THE SOFTWARE, YOU ACCEPT THESE TERMS."* It also restricts use: *"You may not use the software in a live operating environment unless Microsoft permits you to do so under another agreement."* This machine has Power BI Desktop open with a real model.

**3. Runtime self-install of 48 MB.** The 34 KB package is just a launcher. `index.js:96` runs `npm install @microsoft/powerbi-modeling-mcp-win32-x64@<version>` if the platform package isn't present:

```js
execFileSync('npm', ['install', `${platformPackageName}@${version}`], { ... })
```

| Platform package | Data |
|---|---|
| Name | `@microsoft/powerbi-modeling-mcp-win32-x64` |
| Size | **50,425,117 bytes (~48 MB)**, 7 files |
| SHA-1 | `296f8168c4982760b1b8ba0b381f0cdbbbfa3501` |

It's containable (pre-downloading the pinned platform package to a temporary `node_modules`), but **it doesn't change anything**: points 1 and 2 are enough to stop.

**Lifting the block requires the project owner to decide**, with knowledge of the telemetry and license terms, whether to authorize running it. Only then would it move from *Declared* to *Observed*/*Tested*.

---

## 2.2 DECLARED capability of @microsoft/powerbi-modeling-mcp 0.5.0-beta.11

Extracted from README and CHANGELOG. **None of this is observed or tested.**

| Declared area | Evidence |
|---|---|
| Tools by domain | `database_operations`, `table_operations`, `column_operations`, `dax_operations`, measure operations, user hierarchies |
| Connection | Power BI Desktop, **Fabric workspace** and **PBIP/TMDL** folders. Prompts `/ConnectToPowerBIDesktop`, `/ConnectToFabric`, `/ConnectToPowerBIProject` |
| Modeling | create/update tables, columns, measures, relationships; `IsKey`; `sortByColumn`; Expression Context; Direct Lake |
| DAX | execute and validate, execution metrics, **impersonation with roles and UPN** |
| Serialization | `ExportTMDL`, `ExportTMSL`, `DeployToFabric` |
| Refresh | `RefreshWithXMLA`, `RefreshWithAPI`, `CheckStatusOfRefreshWithAPI`, `CancelRefreshWithAPI` |
| Batches | native batch operations across all tools, with transactional support (declared) |
| Best practices | evaluation and implementation of modeling best practices |
| Authentication | Entra ID via Azure Identity SDK; `AzureCLI`, `DefaultAzureCredential`, `managedidentity`, service principal modes |
| Transport | stdio and **optional HTTP**, with its own warning: *"no MCP-level auth in HTTP mode"* |

### Two signals worth not overlooking

1. **`0.5.0-beta.11`: "Skip write-operation confirmation prompts by default. Provide `--require-confirmation` flag."** Write operations **don't ask for confirmation by default**. It's the opposite policy of this project (`confirm=true` mandatory on destructive ones).
2. **Beta-only channel**, with recent breaking changes (`Rename Refresh to RefreshWithXMLA (breaking change)` in beta.3). Building a dependency on it means assuming its instability.

---

## 2.3 Impact on the live-model reorientation

The approved reorientation targets: the live layer, ADOMD/TOM, the `live|pbip|both` bridge, model auditing. **That's exactly the territory Microsoft's own server declares**: it claims to cover semantic modeling over Desktop *and* PBIP/TMDL, DAX, refresh and best practices.

That said, and keeping the level discipline, what's declared **proves nothing** about: the safety of its writes, behavior with Desktop open, rollback, real telemetry, or whether the `live↔pbip` bridge we offer even exists there. The same criterion requested for `powerbi-report-mcp` applies: **provisional until contracts and behavior are tested**.

What remains, as far as the evidence reaches, exclusive to this project:

| Capability | Status vs. Microsoft (declared) |
|---|---|
| HTML/SVG inside Power BI via DAX measure + `data_category="ImageUrl"` | Not present in their documentation |
| Creating visuals and PBIR pages | Not present: their declared scope is the **model**, not the report |
| HTML preview of a sheet before writing it | Not present |
| Working **without telemetry and without a proprietary license** | Structural difference, not a functional one |
| Explicit confirmation policy on destructive writes | Opposite of their declared default |

---

## 1. Horizun PBI MCP vs. @microsoft/powerbi-modeling-mcp

| Capability | Horizun PBI MCP | Microsoft MCP 0.5.0-beta.11 | Strategy |
|---|---|---|---|
| Live DAX | ✅ **Tested** (`EVALUATE ROW` in 2 ms, port 58770) | 📄 **Declared** (`dax_operations`, with metrics and impersonation) | Maintain compatibility |
| Measures / TOM | ✅ **Tested** | 📄 **Declared** (TOM 19.114.1.3) | Maintain; don't prioritize duplication |
| Model reading (TMDL) | ✅ **Tested** | 📄 **Declared** (`ExportTMDL`, PBIP folder connection) | Maintain |
| PBIR (report) | ✅ **Tested** | ❌ **Not declared** — their scope is the model | **Still ours** |
| Visuals / pages | ✅ **Tested** | ❌ **Not declared** | **Still ours** |
| HTML/SVG per measure | ✅ **Tested** | ❌ **Not declared** | **Still ours** |
| Comprehensive audit | 🟡 Partial (7 rules) | 📄 **Declared** (best practices) | Compare before expanding |
| Refresh | ✅ Local | 📄 **Declared** (XMLA + async API, Fabric) | They go further here |
| Power BI Service / Fabric | ❌ No | 📄 **Declared** (workspaces, DeployToFabric, Entra ID) | Not competing here |
| Confirmation on writes | ✅ `confirm=true` mandatory on destructive ones | 📄 **Declared: off by default** (`--require-confirmation` to enable it) | Policy difference, in our favor |
| License / telemetry | Apache-2.0, no telemetry | Proprietary PREVIEW, telemetry on use | Structural difference |

**No row in the Microsoft column goes beyond *Declared*.** It's read from their documentation, not executed. A package's name — and its README — are not evidence of behavior.

---

## 2. Horizun PBI MCP vs. powerbi-report-mcp 0.9.6 — relevant finding

This server **is already built on this machine** and covers exactly the domain the earlier audit identified as Horizun PBI MCP's differentiator: PBIR.

**57 tools observed** in its bundle, grouped:

| Area | Tools observed (sample) |
|---|---|
| Pages | `pbir_create_page`, `pbir_delete_page`, `pbir_duplicate_page`, `pbir_rename_page`, `pbir_reorder_pages`, `pbir_set_active_page`, `pbir_set_page_visibility`, `pbir_update_page_size` |
| Visuals | `pbir_add_visual`, `pbir_get_visual`, `pbir_delete_visual`, `pbir_duplicate_visual`, `pbir_move_visual`, `pbir_change_visual_type`, `pbir_format_visual`, `pbir_set_visual_title`, `pbir_set_visual_sort`, `pbir_set_visual_interaction`, `pbir_update_visual_bindings` |
| Layout | `pbir_auto_layout`, `pbir_layout_grid`, `pbir_validate_wireframe` |
| Themes | `pbir_apply_theme`, `pbir_get_report_theme`, `pbir_set_report_theme`, `pbir_diff_report_theme`, `pbir_audit_theme_compliance`, `pbir_lookup_theme_property` |
| Filters | `pbir_add_page_filter`, `pbir_list_filters`, `pbir_remove_filter`, `pbir_clear_filters`, `pbir_set_filter_pane` |
| Bookmarks | `pbir_add_bookmark`, `pbir_list_bookmarks`, `pbir_rename_bookmark`, `pbir_delete_bookmark` |
| Batches | `pbir_bulk_bind`, `pbir_bulk_delete_visuals`, `pbir_bulk_update_format` |
| Conditional formatting | `pbir_set_conditional_format`, `pbir_set_datapoint_colors`, `pbir_set_page_background` |

### Real overlap

| Capability | Horizun PBI MCP | powerbi-report-mcp | Verdict |
|---|---|---|---|
| List pages/visuals | ✅ 3 tools | ✅ observed | **Duplicated** |
| Create/move visual | ✅ 2 tools | ✅ observed | **Duplicated** |
| Delete/duplicate visual and page | ❌ | ✅ observed | **They're ahead** |
| Themes, bookmarks, filters | ❌ | ✅ observed | **Only theirs** |
| Batch operations | ❌ | ✅ observed | **Only theirs** |
| Conditional formatting | ❌ | ✅ observed | **Only theirs** |
| LIVE layer (ADOMD/TOM) | ✅ **Tested** | ❌ not observed | **Only ours** |
| DAX measures (create/edit) | ✅ **Tested** | ❌ not observed (has `pbir_manage_extension_measures`, which is something else) | **Only ours** |
| Local refresh | ✅ | ❌ not observed | **Only ours** |
| Model documentation | ✅ **Tested** | ❌ not observed | **Only ours** |
| HTML/SVG via DAX measure | ✅ **Tested** | ❌ not observed | **Only ours** |
| Declarative page generation + HTML preview | ✅ **Tested** | 🟡 `pbir_validate_wireframe` suggests something similar | **To review** |
| Dual live+pbip mode | ✅ **Tested** | ❌ | **Only ours** |

### What this means for the plan

The earlier audit's premise — "PBIR is the main differentiator" — **is weakened**: there's a local server, more mature in that specific domain, already built.

What remains genuinely unique to Horizun PBI MCP:

1. **The LIVE layer** (ADOMD.NET + TOM against `msmdsrv.exe`). Querying real data, creating measures, refreshing.
2. **The live↔disk bridge** (`mode: live|pbip|both`), which neither of the other two does.
3. **HTML/SVG inside Power BI** via DAX measure + `data_category="ImageUrl"`.
4. **Semantic model documentation and auditing.**

This doesn't close the door on phases 2–3, but **it changes their justification**: they'd stop being "the competitive edge" and become "the minimum for the live layer to be usable end to end." It's a product decision, not a technical one, and belongs to the project owner.

---

## 3. Coexistence strategy

All three servers can be registered at once: the prefixes don't clash (`pbi_*` vs `pbir_*` vs Microsoft's).

**Real coexistence risk:** two servers writing to the same `.pbip` without coordination. Neither one knows about the other's locks. Mitigation in Phase 1: file lock + `expected_state` + detecting external modification between read and write.

**On reusing their code:** nothing has been copied or integrated. `powerbi-report-mcp` ships its own `LICENSE`; any reuse would require reviewing it first. The recommended path is **registering both servers**, not merging them.

---

## 4. To complete this matrix

- [x] ~~Download and inspect `@microsoft/powerbi-modeling-mcp`~~ → done: pinned version, integrity verified, read. Level **Declared**.
- [ ] **Owner decision:** authorize starting Microsoft's beta, knowing that (a) using it consents to telemetry to Microsoft, (b) using it accepts its PREVIEW license terms, (c) it self-installs 48 MB. Only then does it move to *Observed*/*Tested*.
- [ ] Run `powerbi-report-mcp` with `tools/list` against a synthetic fixture → moves from *Observed* to *Tested*. **No known blockers**: it's a local build already present, with no telemetry or proprietary license detected.
- [ ] Compare contracts and behavior before duplicating any new PBIR capability.

## 5. Code reuse: none

Not a single line has been copied or integrated from either.

- `@microsoft/powerbi-modeling-mcp`: **proprietary license** (Microsoft Software License Terms, PREVIEW). Expressly prohibits use in a production environment without another agreement. Incorporating its code is not an option.
- `powerbi-report-mcp`: ships its own `LICENSE`; it would need reviewing before any reuse.

The correct path remains **registering the servers separately**, not merging them.
