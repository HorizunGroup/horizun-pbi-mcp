"""Build the one-click Claude Desktop MCP Bundle from committed sources.

The bundle contains a dependency-free UV bootstrap at its root and the
installable project under ``payload/``. Claude Desktop supplies UV/Python;
the existing plugin launcher then prepares the same verified, versioned
runtime used by the ChatGPT/Codex and Claude Code plugins.

Only files selected from a Git tree are packed. This is intentional: a dirty
working tree can contain real PBIX/PBIP data, outputs or backups, and none of
those may enter a release artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path, PurePosixPath


REPO = Path(__file__).resolve().parent.parent
MANIFEST_PATH = "packaging/claude-desktop/manifest.json"
EXACT_PAYLOAD = {
    "README.md",
    "pyproject.toml",
    "scripts/fetch_libs.py",
    "scripts/fetch_pbir_schemas.py",
    "scripts/fetch_report_validator.py",
    "scripts/plugin_bootstrap.py",
    "scripts/plugin_launcher.py",
}
PAYLOAD_PREFIXES = ("scripts/locks/", "src/horizun_pbi_mcp/")
FORBIDDEN_PARTS = {
    ".git", ".claude", "backups", "outputs", "tests", "fixtures",
    "__pycache__", ".pytest_cache", ".venv", "venv",
}
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _git(repo: Path, *args: str) -> bytes:
    process = subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False)
    if process.returncode:
        raise SystemExit(
            f"[build_mcpb] git {' '.join(args)} fallo:\n"
            f"{process.stderr.decode('utf-8', 'replace')[-2000:]}")
    return process.stdout


def _tree_files(repo: Path, ref: str) -> list[str]:
    raw = _git(repo, "ls-tree", "-r", "--name-only", "-z", ref)
    return [name.decode("utf-8") for name in raw.split(b"\0") if name]


def _blob(repo: Path, ref: str, relative: str) -> bytes:
    return _git(repo, "show", f"{ref}:{relative}")


def _version(pyproject: bytes) -> str:
    import re
    match = re.search(rb'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    if not match:
        raise SystemExit("[build_mcpb] pyproject.toml no declara version")
    return match.group(1).decode("ascii")


def _safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise SystemExit(f"[build_mcpb] ruta no segura en el bundle: {name}")
    if FORBIDDEN_PARTS.intersection(part.casefold() for part in path.parts):
        raise SystemExit(f"[build_mcpb] contenido privado/prohibido: {name}")
    if any(part.casefold().startswith(".env") for part in path.parts):
        raise SystemExit(f"[build_mcpb] archivo de entorno prohibido: {name}")
    if ".bak-" in name.casefold():
        raise SystemExit(f"[build_mcpb] backup prohibido: {name}")


def _write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    _safe_member(name)
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    zf.writestr(info, data, compresslevel=9)


def build(output: Path, *, repo: Path = REPO, ref: str = "HEAD") -> dict:
    files = _tree_files(repo, ref)
    selected = sorted(
        path for path in files
        if path in EXACT_PAYLOAD or path.startswith(PAYLOAD_PREFIXES))
    missing = sorted(EXACT_PAYLOAD - set(selected))
    if missing:
        raise SystemExit(f"[build_mcpb] faltan archivos requeridos: {missing}")

    manifest_raw = _blob(repo, ref, MANIFEST_PATH)
    manifest = json.loads(manifest_raw.decode("utf-8"))
    version = _version(_blob(repo, ref, "pyproject.toml"))
    if manifest.get("version") != version:
        raise SystemExit(
            f"[build_mcpb] manifest dice {manifest.get('version')} y "
            f"pyproject dice {version}")
    if manifest.get("manifest_version") != "0.4":
        raise SystemExit("[build_mcpb] el manifest MCPB debe usar la spec 0.4")
    server = manifest.get("server") or {}
    if server.get("type") != "uv":
        raise SystemExit("[build_mcpb] Claude Desktop debe aportar UV/Python")
    if server.get("entry_point") != "payload/scripts/plugin_launcher.py":
        raise SystemExit("[build_mcpb] entry_point inesperado")

    bootstrap_project = (
        "[project]\n"
        "name = \"horizun-pbi-mcp-bootstrap\"\n"
        f"version = \"{version}\"\n"
        "requires-python = \">=3.10,<3.15\"\n"
        "dependencies = []\n"
    ).encode("ascii")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as zf:
        _write(zf, "manifest.json", manifest_raw)
        _write(zf, "pyproject.toml", bootstrap_project)
        for relative in selected:
            _write(zf, f"payload/{relative}", _blob(repo, ref, relative))

    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as zf:
        names = zf.namelist()
        if len(names) != len(set(names)):
            raise SystemExit("[build_mcpb] el bundle contiene rutas duplicadas")
        for name in names:
            _safe_member(name)
        if zf.testzip() is not None:
            raise SystemExit("[build_mcpb] el ZIP no supera su CRC")

    return {
        "name": output.name,
        "version": version,
        "sha256": digest,
        "bytes": output.stat().st_size,
        "files": len(selected) + 2,
        "source_ref": ref,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args(argv)
    result = build(args.output.resolve(), ref=args.ref)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
