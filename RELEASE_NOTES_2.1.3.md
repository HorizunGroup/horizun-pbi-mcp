# Horizun PBI MCP v2.1.3

Binarios firmados con el certificado de HORIZUN GROUP S.A.S. (Certum). Sin
cambios de codigo funcional.

Signed-binaries release: the one-paste installer (`horizun-pbi-mcp-instalar.ps1`)
now carries an Authenticode signature from the HORIZUN GROUP S.A.S. certificate
(Certum, timestamped), and the SHA-256 that the one-paste block verifies is the
hash of the **signed** file. The MCP itself is untouched: still 139 tools, the
original 34 frozen, **0 breaking changes and 0 compatible ones** against
`v2.1.2`.

## Why a patch release

The `v2.1.2` installer was signed after the release was built, so the file
people download (45 966 bytes) is not the one the one-paste block pins (31 600
bytes). Published releases are immutable and cannot be re-uploaded, so the fix is
a new version in which the installer is signed **before** tagging and every pin
is computed from the signed file.

## What changed

- `scripts/instalar.ps1` ships signed; its only functional line change is the
  marketplace `ref`, now `v2.1.3`.
- The pinned SHA-256 and size (manifest, one-paste block, `docs/INSTALL.md`, the
  setup skill) are those of the signed file.
- Release tooling: the pipeline requires the signed installer, creates the
  GitHub Release as a draft, and a dedicated job verifies the signature with the
  Certum key before publishing it (see `RELEASING.md`).

## Upgrading

Nothing to do. No tool was renamed, no parameter changed, no response shape
moved. If you installed with the one-paste block, use the `v2.1.3` block in
`docs/INSTALL.md`.
