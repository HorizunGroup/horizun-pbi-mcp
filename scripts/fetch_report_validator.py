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
import os
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

# El ciclo de vida COMPARTIDO: la misma promocion que publica el runtime.
from horizun_pbi_mcp.lifecycle import promotion  # noqa: E402

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


def _cli_bajo(carpeta: Path) -> Path | None:
    """El `cli.js` dentro de `carpeta`, con la MISMA forma que busca el servidor.

    Se apunta `cli_dir()` a la carpeta y se pregunta a `report_validator`, en
    vez de rehacer la ruta aqui: el dia que el paquete npm mueva su `dist/`,
    una ruta duplicada en el instalador diria que todo fue bien mientras el
    servidor no encuentra nada.
    """
    from horizun_pbi_mcp.services import report_validator as rv

    previo_dir = os.environ.get("HORIZUN_PBI_MCP_REPORT_VALIDATOR_DIR")
    previo_cli = os.environ.pop("HORIZUN_PBI_MCP_REPORT_VALIDATOR", None)
    os.environ["HORIZUN_PBI_MCP_REPORT_VALIDATOR_DIR"] = str(carpeta)
    try:
        return rv.localizar()
    finally:
        if previo_dir is None:
            os.environ.pop("HORIZUN_PBI_MCP_REPORT_VALIDATOR_DIR", None)
        else:
            os.environ["HORIZUN_PBI_MCP_REPORT_VALIDATOR_DIR"] = previo_dir
        if previo_cli is not None:
            os.environ["HORIZUN_PBI_MCP_REPORT_VALIDATOR"] = previo_cli


def _verificar_preparado(staging: Path) -> tuple[Path, str]:
    """Comprueba el CLI DENTRO del staging, antes de publicarlo."""
    from horizun_pbi_mcp.services import report_validator as rv

    cli = _cli_bajo(staging)
    if cli is None:
        raise InstalacionFallida(
            f"npm termino sin error pero no aparece el CLI bajo {staging}. "
            "No se publica nada.")
    version = rv._version_cli(cli)             # noqa: SLF001
    if version != VERSION:
        raise InstalacionFallida(
            f"El CLI preparado reporta {version!r} y se esperaba {VERSION!r}. "
            "No se publica nada.")
    return cli, version


def instalar(destino: Path) -> dict:
    """Prepara aparte, verifica y publica con un `rename` (INSTALL-006).

    Antes esto ejecutaba `npm install --prefix <destino>` sobre el directorio
    VIVO. `npm install` no es atomico: escribe cientos de archivos y, si algo
    lo interrumpe -red, disco, un Ctrl-C-, deja el validador anterior mezclado
    con medio validador nuevo. Un CLI a medias es peor que ninguno, porque
    existe y arranca.

    Ahora npm escribe en un directorio hermano, se comprueba que el CLI
    preparado esta y dice la version que toca, y solo entonces se publica con
    el ciclo de vida compartido -journal, `.previous-`, recuperacion-.
    """
    comprobar_node()
    npm = shutil.which("npm")
    if not npm:
        raise InstalacionFallida("npm no esta en el PATH.")

    destino = Path(destino)
    raiz = destino.parent
    raiz.mkdir(parents=True, exist_ok=True)
    promotion.recuperar(raiz)

    tmp = Path(tempfile.mkdtemp(prefix="hz_validator_"))
    staging = promotion.crear_staging(raiz, destino.name)
    try:
        r = _correr([npm, "pack", f"{PAQUETE}@{VERSION}"], cwd=str(tmp))
        if r.returncode != 0:
            raise InstalacionFallida(f"`npm pack` fallo: {r.stderr[-400:]}")
        tarballs = list(tmp.glob("*.tgz"))
        if not tarballs:
            raise InstalacionFallida("`npm pack` no dejo ningun .tgz")

        verificar_tarball(tarballs[0])          # ANTES de instalar nada

        r = _correr([npm, "install", "--prefix", str(staging), "--no-audit",
                     "--no-fund", "--ignore-scripts", str(tarballs[0])],
                    cwd=str(tmp))
        if r.returncode != 0:
            raise InstalacionFallida(f"`npm install` fallo: {r.stderr[-400:]}")

        cli, version = _verificar_preparado(staging)
        relativa = cli.relative_to(staging.resolve())
        promotion.promover(raiz, staging, destino)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return {"cli": str(destino / relativa), "version": version,
            "dir": str(destino)}


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
