# Security policy

How to report a vulnerability in Horizun PBI MCP, what we cover, and what
happens after you send it.

This file is the **reporting policy**. The **threat model** — what the server
protects, how, and what it explicitly does *not* promise — lives in
[`docs/SECURITY.md`](docs/SECURITY.md), and it is worth reading before
reporting: some behaviours that look like flaws are documented, deliberate
limits.

---

## Reporting a vulnerability

**Do not open a public issue for a security problem.** A public issue tells
everyone how to exploit it before anyone can fix it.

Use, in this order of preference:

1. **GitHub Private vulnerability reporting** — the *Report a vulnerability*
   button under the repository's **Security** tab. This is the preferred
   channel: it is private, it threads, and it produces a CVE if one is
   warranted.
2. **Email** — `security@horizunhub.com`, with `horizun-pbi-mcp` in the
   subject. Use this if you cannot access GitHub or the button is not visible
   to you.

> **Status of channel 1:** private vulnerability reporting is a **repository
> setting on GitHub** and is not verifiable from this file. See
> [pending remote controls](#pending-remote-controls) below. If the button is
> not there, use the email address; the report is just as valid.

### What helps

- The version (`pbi_health_check`, or `pyproject.toml`) and how it was
  installed (plugin, one-paste installer, `pip`).
- What an attacker gains, not just what misbehaves.
- The smallest reproduction you can manage. A synthetic `.pbip` is ideal — and
  **please do not send real reports, models or client data**; we do not want
  them and cannot store them.
- Whether you want credit, and under what name.

### What to expect

| Stage | Target |
|---|---|
| Acknowledgement of receipt | **3 business days** |
| First assessment (severity, whether it is in scope) | **10 business days** |
| Fix or documented mitigation for critical/high | **30 days** from assessment |
| Fix or documented mitigation for medium/low | next feature release |
| Public disclosure | coordinated, **after** a fix ships, or 90 days from the report, whichever comes first |

These are targets from a small team, not a contractual SLA. If a deadline is
going to slip, you will be told before it slips rather than after.

We will not take legal action over good-faith research that respects these
rules: no accessing data that is not yours, no denial of service, no social
engineering, and no testing against machines you do not own.

---

## Supported versions

Security fixes land on the **latest minor**, and are published as a new patch
release. There is no long-term-support branch.

| Version | Supported |
|---|---|
| `2.1.x` | ✅ Yes |
| `2.0.x` and older | ❌ No — upgrade |

Upgrading is `claude plugin install horizun-pbi-mcp@horizun` again, or the
one-paste installer, which is idempotent.

---

## Scope

**In scope:**

- The MCP server and its tools (`src/horizun_pbi_mcp/**`).
- The installer and bootstrap (`scripts/instalar.ps1`,
  `scripts/one_paste.ps1`, `scripts/plugin_bootstrap.py`) and anything they
  download.
- The supply chain of this repository: pinned downloads, release workflow,
  published artifacts.
- Path traversal, unverified downloads, secret leakage into logs, and writes
  that escape the active project. These are the failure classes this project
  takes most seriously; see T1–T10 in [`docs/SECURITY.md`](docs/SECURITY.md).

**Out of scope:**

- Power BI Desktop, the Analysis Services engine, and the official Microsoft
  PBIR validator. Report those to Microsoft.
- Anything that requires an attacker to already have code execution as your
  user. This server runs with your privileges by design and does not attempt
  to defend against a compromised local account.
- The documented limits in [`docs/SECURITY.md`](docs/SECURITY.md) — e.g. that
  `mode="both"` is blocked rather than coordinated, or that a `pip`-only
  install is not a complete install (`INSTALL-005`). Those are tracked as
  open findings in
  [`docs/MATRIZ_REMEDIACION.md`](docs/MATRIZ_REMEDIACION.md), not hidden.

---

## What this repository does to protect the chain

Each of these is checkable here, not a promise:

- **Nothing executable is downloaded from a moving reference.** Every download
  has a pinned version, a byte cap and a SHA-256 verified *before* it is
  extracted or run — `scripts/downloads_manifest.json`, enforced by
  `tests/test_supply_chain.py`.
- **The published one-paste verifies before it executes.** It downloads a
  pinned release asset, checks the hash and runs it with `&`, never
  `Invoke-Expression`. `tests/test_one_paste.py` proves every failure path
  executes nothing, against a local HTTP server.
- **The installer can be audited without being run:**
  `powershell -NoProfile -File scripts/instalar.ps1 -DryRun`.
- **One build, published as-is.** `scripts/release_build.py` builds once and
  emits `SHA256SUMS` and a CycloneDX SBOM; every consuming job re-verifies via
  `scripts/release_verify.py` before use, and the publish jobs never rebuild.
- **All GitHub Actions are pinned by full commit SHA**, with the human version
  in a trailing comment. A tag is movable; a SHA is not.
- **CodeQL** runs on pushes, pull requests and weekly
  (`.github/workflows/codeql.yml`).
- **Dependabot** watches Actions and Python dependencies
  (`.github/dependabot.yml`).

---

## Pending remote controls

Honesty about the gap: the items below are **GitHub repository settings**. They
cannot be configured from a file in this repository, and this document does not
claim they are active. They are tracked as **RELEASE-003**, which stays
*partially closed* until each one has a saved `gh api` output proving it.

| Control | Why it matters | How to verify |
|---|---|---|
| Branch protection on `main` | Stops a direct push bypassing review and CI | `gh api repos/:owner/:repo/branches/main/protection` |
| Required reviews and required status checks | A red CI must be able to block a merge | same call, `required_status_checks` |
| Secret scanning | Catches a credential committed by accident | `gh api repos/:owner/:repo` → `security_and_analysis` |
| Push protection | Blocks the credential *before* it lands | same |
| Dependabot **security updates** | The config file schedules updates; enabling security updates is a setting | same |
| Private vulnerability reporting | Channel 1 of this document | `gh api repos/:owner/:repo/private-vulnerability-reporting` |
| Protected `pypi`, `mcp-registry` and `github-release` environments with required reviewers | The human gate before anything is published | `gh api repos/:owner/:repo/environments` |

Until those are configured and evidenced, treat this section — not the section
above it — as the current state of the remote.

**Read on 2026-08-15, so the gap is a measurement and not a guess:** `main` is
**not protected** and has no rulesets, Dependabot security updates are
**disabled**, secret scanning and push protection are **disabled**, and private
vulnerability reporting is **disabled**. CodeQL is the one that *is* running —
green on `main`/`1f0405b`, run 31913970370.

The commands to change each of those, with the exact check names read from the
real check-runs of `1f0405b`, plus a verification and a rollback for each, are
written out and **deliberately not executed** in
[`docs/PLAN_SEGURIDAD_GITHUB.md`](docs/PLAN_SEGURIDAD_GITHUB.md). A written plan
is not evidence: these close when someone runs them and saves the JSON.
