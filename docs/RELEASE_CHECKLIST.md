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

The release candidate version is **`v2.0.2`**. The exceptions below are documented
product limits, not hidden bugs or skipped criteria.

> **`v2.0.0` is burned, and no longer exists.** It was created during a **failed
> publication attempt**: the release run built and tested correctly,
> `publicar-pypi` failed with `invalid-publisher`, `publicar-mcp` was skipped,
> and nothing was ever published to GitHub Releases, PyPI or the MCP Registry.
> The default rule — a public tag may already have been fetched, so it is not
> moved, reused or deleted — is recorded here rather than quietly dropped; it
> was **revoked deliberately for that one tag** and it was deleted from the
> remote on 2026-08-16, on the grounds that it lived roughly eighteen hours,
> published nothing, and pointed at a commit (`1f0405b`) still reachable from
> `main`. The pipeline fix shipped as `v2.0.1`. The full account is in the
> `CHANGELOG` entry for `2.0.1`.

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

### Publishing: what actually happens on the tag

Since the release pipeline was rebuilt (RELEASE-001/002), pushing `v*` starts
**one** workflow, `release.yml`, and there is no other path to PyPI or to the
MCP Registry — the two standalone publish workflows are gone.

    tag v*  ->  build  ->  test (windows x 3.10/3.13)  ->  publicar-pypi
                                                              -> publicar-mcp
                                                                 -> publicar-github-release

The last link was **missing until v2.0.1** (RELEASE-004): nothing created a
GitHub Release, while the one-paste block in this repository downloads
`horizun-pbi-mcp-instalar.ps1` from `releases/download/v<version>/`. The
installation path offered to users pointed at an asset no job ever created.

What that means in practice, and what to check before tagging:

1. **The artifact is built once**, on `windows-latest`, and nothing rebuilds it
   afterwards. Every consumer downloads the same artifact and runs
   `scripts/release_verify.py` before touching it.
2. **The test job installs the built wheel**, not the checkout, and runs the
   suite with `-o pythonpath=` so `src/` cannot shadow it.
3. **The tag must match all nine places that declare the version.**
   `release_verify.py --expect-version` enforces it and stops the publish job
   otherwise. Check it locally first:

   ```bash
   python scripts/release_build.py --outdir artefactos && python scripts/release_verify.py --dir artefactos --expect-version 2.0.2
   ```

4. **The release asset `horizun-pbi-mcp-instalar.ps1` must be the frozen bytes**
   of `scripts/instalar.ps1`. The size and SHA-256 are **not repeated here on
   purpose** — the one canonical copy lives in
   `scripts/downloads_manifest.json`, and a second copy in prose is a number
   that goes stale silently. (It did: this checklist claimed 21 016 bytes and a
   `33fa1058…` digest long after the installer had changed.) The build emits
   the asset already verified against the manifest, and `release_publish.py`
   re-reads it **from the published release** and compares both the digest and
   the download URL.
5. **The release publishes exactly what `SHA256SUMS` covers**: wheel, sdist,
   installer, CycloneDX SBOM, release notes and migration notes — plus
   `SHA256SUMS` itself, which is the only one that cannot sign itself. The list
   is derived from the signed manifest, never from a glob.
6. **The post-publication check is part of the job, not a manual step.**
   `publicar-github-release` downloads every asset back, compares each digest
   with the signed one, and fails if the installer's SHA-256 or its
   `browser_download_url` is not exactly what the manifest declares. Flip
   `status: pending_remote_release` to `published` in
   `scripts/downloads_manifest.json` only **after** that job has finished
   green, and record the run id.

The `pypi`, `mcp-registry` and `github-release` environments need required
reviewers configured on GitHub; that, and the other remote controls, are listed
in [`../SECURITY.md`](../SECURITY.md#pending-remote-controls). `github-release`
is new in v2.0.1 and GitHub creates it on first use with **no** protection
rules — declaring it in the workflow is what makes a human gate *possible*, not
what configures it.

PyPI additionally needs its **trusted publisher** configured before `v2.0.1` is
tagged: that is what failed on the `v2.0.0` attempt, with `invalid-publisher`.
The exact claims observed in that run and the manual procedure are in
[`audits/PYPI_TRUSTED_PUBLISHER.md`](audits/PYPI_TRUSTED_PUBLISHER.md).

For this release, an isolated reproducible environment plus the CI matrix
on clean GitHub machines is accepted; a second physical machine isn't needed:

1. export the versioned tree and create a virtual environment with no repository packages;
2. install following the README, no shortcuts;
3. `pytest`, `doctor` and contract check green **there**;
4. register the MCP with a client and check the handshake;
5. the CI matrix **fully** green, with no jobs skipped for a dependency.
