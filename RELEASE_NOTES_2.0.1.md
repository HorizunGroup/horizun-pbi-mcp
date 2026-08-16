# Horizun PBI MCP v2.0.1

**The first public release of the 2.x line.** Same 134 tools as the 2.0.0 that
was prepared and never shipped; the three contract changes from CONTRACT-003
land here, and the migration guide is
[`docs/MIGRACION_1x_A_2.0.md`](docs/MIGRACION_1x_A_2.0.md).

If you are coming from 1.x, read that guide: three calls change and nothing
else. If you are coming from nothing, the one-paste installer in the README is
the whole procedure.

## What 2.0.1 fixes: the release nobody published

The pipeline built the artifact once, ran the suite against those exact bytes,
published to PyPI and published to the MCP Registry — and stopped there. It
**never created a GitHub Release**.

Meanwhile the one-paste block offered in `README.md`, `docs/INSTALL.md` and the
setup skill downloads `horizun-pbi-mcp-instalar.ps1` from
`releases/download/v<version>/…`. So the installation path handed to users
pointed at an asset that no job in the pipeline ever created. Configuring PyPI
and re-running would have published the package and the registry entry, and the
one-paste would still have been a 404.

It was a defect of **omission**, which is the kind no review of the YAML
catches: every guard in the suite asked whether the jobs that existed did
something wrong, and none asked whether a job was missing.

### What the new job does, and what it refuses to do

`publicar-github-release` runs last — after `build`, `test`, `publicar-pypi` and
`publicar-mcp`. Publishing the release before PyPI would be worse than not
publishing it: people download the installer and the `pip install` inside finds
nothing.

- **It publishes exactly the signed bytes.** The asset list is derived from
  `SHA256SUMS`, not from a glob: wheel, sdist, installer, CycloneDX SBOM,
  release notes and migration notes — plus `SHA256SUMS` itself, the only file
  that cannot sign itself. The notes are now copied into the artifact and signed
  with everything else; published from the checkout they would have been the
  only bytes of the release nobody verified.
- **It never replaces an asset.** If a file already exists under that tag with
  different bytes, the job stops. Someone may have downloaded it already, and
  rewriting it under the same name and the same tag is exactly how two people
  "install the same version" and get different things.
- **It is idempotent.** A rerun over a complete release re-verifies every asset
  by downloading it and finishes green **without writing anything**.
- **It checks after uploading.** That no `POST` returned an error says nothing
  about what is on the other side. Every asset is downloaded back and its
  SHA-256 compared with the signed one, and the installer additionally has to
  match both the digest **and** the exact `browser_download_url` published in
  `scripts/downloads_manifest.json` — the URL people paste.
- **It is the only job in the workflow with `contents: write`**, and it has no
  OIDC token: it writes here and authenticates to nobody outside.

## The `v2.0.0` tag

The tag `v2.0.0` was created during a **failed publication attempt**. The run
built and tested correctly, `publicar-pypi` failed with `invalid-publisher`,
`publicar-mcp` was skipped, and nothing reached GitHub Releases, PyPI or the MCP
Registry. The correction ships as `v2.0.1`.

**The tag was deleted from the remote on 2026-08-16.** This file, as published
with the release, said instead that it was immutable and would never be deleted
— the rule that a public tag may already have been fetched by third parties, so
it is not moved or removed. That rule is a sound default and it is recorded
here rather than quietly dropped; it was revoked deliberately for this one tag,
on these grounds: it existed for roughly eighteen hours, nothing was ever
published under it, and the commit it pointed at (`1f0405b`) is still reachable
from `main`. Nobody can find different bytes under a name they already fetched;
the most anyone loses is a dangling reference.

The released asset keeps the original wording, since a published release is not
rewritten. The `CHANGELOG` entry for `2.0.1` carries the same account.

## Verification

- Full local suite green, plus `doctor.py`, the frozen MCP contract
  (`python -m tests.contract_utils`), the tool inventory, the one-paste
  synchronization check, `python -m build` and `twine check`.
- The workflow guards run against a **mutated** copy of `release.yml`: removing
  `publicar-pypi` or `publicar-mcp` from the new job's `needs`, deleting the job
  entirely, granting `contents: write` to `build`, adding an OIDC token, sneaking
  a `gh release create` into the build job, downloading the artifact twice, and
  replacing the publish step with a glob — each one turns exactly one guard red,
  and no other.
- The publication logic is exercised end to end against a stand-in GitHub API,
  including every path that must **stop**: an asset that already exists with
  other bytes, a half-finished upload, an extra unsigned asset, a draft release,
  edited notes, and an installer whose published URL is not the one in the
  manifest.
