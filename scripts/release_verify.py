"""RELEASE-001/002 — nadie usa un artefacto que no haya verificado antes.

Todo job que consume el artefacto -probarlo, instalarlo, publicarlo- llama aqui
primero. Si `SHA256SUMS` no cuadra, o falta un archivo, o sobra uno que nadie
declaro, el job se para antes de tocar nada.

Con `--expect-version` comprueba ademas que el tag y **todos** los manifiestos
declaran el mismo numero. Es la guarda que impide publicar 2.0.0 desde un arbol
que dice 1.5.4 en tres archivos y 2.0.0 en otros dos.

Uso:
    python scripts/release_verify.py --dir artefactos
    python scripts/release_verify.py --dir artefactos --expect-version 2.0.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Los sitios que declaran la version del producto. Si uno se queda atras, el
#: marketplace instala una cosa, PyPI otra y el registro MCP una tercera.
SITIOS_DE_VERSION = {
    "pyproject.toml": lambda t: re.search(r'^version = "([^"]+)"', t, re.M).group(1),
    "src/horizun_pbi_mcp/branding.py": lambda t: re.search(r'^VERSION\s*=\s*"([^"]+)"', t, re.M).group(1),
    ".mcp/server.json": lambda t: json.loads(t)["version"],
    ".claude-plugin/plugin.json": lambda t: json.loads(t)["version"],
    ".codex-plugin/plugin.json": lambda t: json.loads(t)["version"],
    ".claude-plugin/marketplace.json": lambda t: _ref(t),
    ".agents/plugins/marketplace.json": lambda t: _ref(t),
    "scripts/downloads_manifest.json": lambda t: json.loads(t)["downloads"]["instalar.ps1"]["version"],
}


def _ref(texto: str) -> str:
    refs = set(re.findall(r'"ref"\s*:\s*"v([^"]+)"', texto))
    if len(refs) != 1:
        raise SystemExit(f"[release_verify] el marketplace declara refs {refs}")
    return refs.pop()


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_digests(raiz: Path) -> list:
    sumas = raiz / "meta" / "SHA256SUMS"
    if not sumas.exists():
        raise SystemExit(f"[release_verify] no hay {sumas}: el artefacto no "
                         "viene de scripts/release_build.py")

    declarados, problemas = {}, []
    for linea in sumas.read_text(encoding="ascii").splitlines():
        if not linea.strip():
            continue
        digest, _, rel = linea.partition("  ")
        declarados[rel] = digest

    for rel, esperado in declarados.items():
        p = raiz / rel
        if not p.is_file():
            problemas.append(f"declarado y ausente: {rel}")
            continue
        real = _sha256(p)
        if real != esperado:
            problemas.append(f"digest distinto en {rel}: "
                             f"esperado {esperado}, real {real}")

    # Un archivo publicable que NADIE declaro es tan grave como uno cambiado:
    # es exactamente por donde entraria algo que la suite no vio.
    for p in sorted((raiz / "dist").glob("*")):
        rel = p.relative_to(raiz).as_posix()
        if rel not in declarados:
            problemas.append(f"en dist/ y sin declarar en SHA256SUMS: {rel}")

    if problemas:
        raise SystemExit("[release_verify] artefacto NO verificado:\n  - "
                         + "\n  - ".join(problemas))
    return sorted(declarados)


def verificar_instalador(raiz: Path) -> str:
    """El asset de la release tiene que ser el archivo congelado, byte a byte."""
    entrada = json.loads(
        (REPO / "scripts" / "downloads_manifest.json").read_text(encoding="utf-8")
    )["downloads"]["instalar.ps1"]
    asset = raiz / "meta" / entrada["name"]
    if not asset.exists():
        raise SystemExit(f"[release_verify] falta el asset {entrada['name']}")

    real = _sha256(asset)
    if real != entrada["sha256"]:
        raise SystemExit(
            f"[release_verify] el asset del instalador no es el congelado.\n"
            f"  manifest: {entrada['sha256']}\n  real:     {real}")
    crudo = asset.read_bytes()
    if len(crudo) != entrada["actual_bytes"]:
        raise SystemExit("[release_verify] el asset no tiene el tamano declarado")
    if b"\r" in crudo:
        raise SystemExit("[release_verify] el asset llego con CRLF: el hash "
                         "publicado dejaria de coincidir con lo descargado")
    return real


def verificar_version(esperada: str) -> dict:
    esperada = esperada.lstrip("v")
    leidas, discrepan = {}, []
    for rel, extraer in SITIOS_DE_VERSION.items():
        texto = (REPO / rel).read_text(encoding="utf-8")
        leidas[rel] = extraer(texto)
        if leidas[rel] != esperada:
            discrepan.append(f"{rel} dice {leidas[rel]}")
    if discrepan:
        raise SystemExit(
            f"[release_verify] se publica {esperada} y no todos lo declaran:\n  - "
            + "\n  - ".join(discrepan))
    return leidas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--expect-version", default=None)
    args = ap.parse_args(argv)

    raiz = args.dir.resolve()
    archivos = verificar_digests(raiz)
    digest_instalador = verificar_instalador(raiz)
    print("[release_verify] digests OK:")
    for a in archivos:
        print(f"  - {a}")
    print(f"[release_verify] asset del instalador OK: {digest_instalador}")

    if args.expect_version:
        leidas = verificar_version(args.expect_version)
        print(f"[release_verify] version {args.expect_version} declarada en "
              f"los {len(leidas)} sitios.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
