"""Instala el validador PBIR oficial de Microsoft (Fase E3.2).

    python scripts/fetch_report_validator.py

Version EXACTA y hash fijado. Ninguna operacion normal ejecuta `npx -y` ni
descarga `@latest`: eso convertiria cada escritura en una descarga de codigo
sin verificar, ejecutado sobre el proyecto del usuario.

El paquete es MIT (Microsoft Corporation), asi que redistribuirlo estaria
permitido; aun asi NO se empaqueta en el wheel: arrastraria un arbol de
dependencias npm cuyas licencias habria que auditar una a una, y el servidor
Python funciona sin el. Lo que queda bloqueado sin CLI son las escrituras que
necesiten su cobertura.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ_REPO / "src"))
# El propio directorio de scripts: al ejecutar el fichero ya esta en sys.path,
# pero no cuando lo carga una prueba por ruta.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from plugin_bootstrap import flags_sin_ventana  # noqa: E402

PAQUETE = "@microsoft/powerbi-report-authoring-cli"
VERSION = "0.1.4"

#: Verificados el 2026-07-31 contra el registro de npm.
SHA1_TARBALL = "e43058268d04fdd4a41063231dd29b905ba5b70b"
INTEGRITY = ("sha512-SibT9RCS7dQdqYGhU0/r1yixYZgxRsjlpKSF+a/wGczN0YRG6M9nR"
             "oFBe2d4hptwwUeqvnkjNxpo7NTUxXNIDQ==")
NODE_MINIMO = 20


class InstalacionFallida(RuntimeError):
    pass


def _correr(args, cwd=None, timeout=900):
    # `capture_output` redirige los handles, pero NO evita que Windows le
    # asigne una consola visible al hijo cuando el padre no tiene ninguna:
    # `node` y `npm` estrenaban ventana en cada instalacion del validador.
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          shell=False, timeout=timeout, **flags_sin_ventana())


def comprobar_node() -> int:
    node = shutil.which("node")
    if not node:
        raise InstalacionFallida(
            "Node no esta instalado. El validador oficial de Microsoft lo "
            f"necesita (engines.node >= {NODE_MINIMO}).")
    r = _correr([node, "--version"], timeout=60)
    try:
        mayor = int(r.stdout.strip().lstrip("v").split(".")[0])
    except (ValueError, IndexError) as exc:
        raise InstalacionFallida(
            f"No se pudo interpretar la version de Node: {r.stdout!r}") from exc
    if mayor < NODE_MINIMO:
        raise InstalacionFallida(
            f"Node {mayor} es anterior al minimo que exige el paquete "
            f"({NODE_MINIMO}).")
    return mayor


def verificar_tarball(ruta: Path) -> None:
    datos = ruta.read_bytes()
    sha1 = hashlib.sha1(datos).hexdigest()
    integridad = "sha512-" + base64.b64encode(
        hashlib.sha512(datos).digest()).decode()

    if sha1 != SHA1_TARBALL or integridad != INTEGRITY:
        raise InstalacionFallida(
            "El paquete descargado NO coincide con el fijado.\n"
            f"  sha1 esperado : {SHA1_TARBALL}\n"
            f"  sha1 obtenido : {sha1}\n"
            f"  integrity esperada: {INTEGRITY[:40]}...\n"
            f"  integrity obtenida: {integridad[:40]}...\n"
            "No se instala nada.")


def instalar(destino: Path) -> dict:
    comprobar_node()
    npm = shutil.which("npm")
    if not npm:
        raise InstalacionFallida("npm no esta en el PATH.")

    tmp = Path(tempfile.mkdtemp(prefix="hz_validator_"))
    try:
        r = _correr([npm, "pack", f"{PAQUETE}@{VERSION}"], cwd=str(tmp))
        if r.returncode != 0:
            raise InstalacionFallida(f"`npm pack` fallo: {r.stderr[-400:]}")
        tarballs = list(tmp.glob("*.tgz"))
        if not tarballs:
            raise InstalacionFallida("`npm pack` no dejo ningun .tgz")

        verificar_tarball(tarballs[0])          # ANTES de instalar nada

        destino.mkdir(parents=True, exist_ok=True)
        r = _correr([npm, "install", "--prefix", str(destino), "--no-audit",
                     "--no-fund", "--ignore-scripts", str(tarballs[0])],
                    cwd=str(tmp))
        if r.returncode != 0:
            raise InstalacionFallida(f"`npm install` fallo: {r.stderr[-400:]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    from horizun_pbi_mcp.services import report_validator as rv

    cli = rv.localizar()
    if cli is None:
        raise InstalacionFallida(
            f"El paquete se instalo pero no aparece el CLI bajo {destino}.")
    version = rv._version_cli(cli)             # noqa: SLF001
    if version != VERSION:
        raise InstalacionFallida(
            f"El CLI instalado reporta {version!r} y se esperaba {VERSION!r}.")
    return {"cli": str(cli), "version": version, "dir": str(destino)}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dest", default=None, help="directorio de instalacion")
    p.add_argument("--check", action="store_true",
                   help="solo informar del estado, sin instalar")
    args = p.parse_args()

    from horizun_pbi_mcp.services import report_validator as rv

    if args.check:
        print(json.dumps(rv.estado(), indent=2, ensure_ascii=False))
        return 0 if rv.estado()["available"] else 1

    destino = Path(args.dest) if args.dest else rv.cli_dir()
    try:
        r = instalar(destino)
    except InstalacionFallida as exc:
        print(f"FALLO: {exc}", file=sys.stderr)
        return 1
    print(f"Validador oficial {r['version']} instalado en {r['dir']}")
    print(f"  {r['cli']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
