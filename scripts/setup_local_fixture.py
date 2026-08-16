"""Prepara el fixture LOCAL de compatibilidad (copia de un proyecto real).

Copia un `.pbip` real y sus carpetas `.Report` / `.SemanticModel` a
`tests/fixtures/local/`, que esta IGNORADA por git, y deja la copia en
solo lectura.

Garantias:
  - Nunca escribe en el proyecto de origen (solo lee).
  - Se niega a copiar si git NO esta ignorando el destino.
  - Marca todos los archivos copiados como solo lectura.
  - No imprime contenido de los archivos, solo conteos y rutas.

Uso:
    python scripts/setup_local_fixture.py --source "C:/ruta/MiInforme.pbip"
    python scripts/setup_local_fixture.py --status
    python scripts/setup_local_fixture.py --remove
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIR = PROJECT_ROOT / "tests" / "fixtures" / "local"


def _fail(msg: str) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _git_ignores(path: Path) -> bool:
    """True si git ignora `path`. Si no hay repo git, devuelve False (inseguro)."""
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    try:
        res = subprocess.run(
            ["git", "check-ignore", "-q", rel],
            cwd=PROJECT_ROOT, capture_output=True, timeout=15,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _make_read_only(root: Path) -> int:
    n = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                p.chmod(p.stat().st_mode & ~stat.S_IWRITE)
                n += 1
            except OSError:
                pass
    return n


def _make_writable(root: Path) -> None:
    for p in root.rglob("*"):
        if p.is_file():
            try:
                p.chmod(p.stat().st_mode | stat.S_IWRITE)
            except OSError:
                pass


def cmd_status() -> int:
    print(f"Destino del fixture local : {LOCAL_DIR}")
    print(f"git lo ignora             : {'SI' if _git_ignores(LOCAL_DIR) else 'NO'}")
    if not LOCAL_DIR.exists():
        print("Estado                    : no preparado (las pruebas locales se omitiran)")
        return 0
    pbips = sorted(LOCAL_DIR.glob("*.pbip"))
    files = sum(1 for p in LOCAL_DIR.rglob("*") if p.is_file())
    writable = sum(1 for p in LOCAL_DIR.rglob("*")
                   if p.is_file() and os.access(p, os.W_OK))
    print(f"Estado                    : preparado")
    print(f"  .pbip encontrados       : {[p.name for p in pbips]}")
    print(f"  archivos                : {files}")
    print(f"  archivos escribibles    : {writable} (deberia ser 0)")
    return 0


def cmd_remove() -> int:
    if not LOCAL_DIR.exists():
        print("No hay fixture local que eliminar.")
        return 0
    _make_writable(LOCAL_DIR)
    shutil.rmtree(LOCAL_DIR)
    print(f"Fixture local eliminado: {LOCAL_DIR}")
    return 0


def cmd_setup(source: str) -> int:
    src = Path(source).expanduser().resolve()
    if src.is_dir():
        matches = sorted(src.glob("*.pbip"))
        if not matches:
            _fail(f"No hay ningun .pbip en la carpeta {src}")
        src = matches[0]
    if src.suffix.lower() != ".pbip" or not src.exists():
        _fail(f"La ruta no es un .pbip existente: {src}")

    # Guardarrail 1: el destino DEBE estar ignorado por git.
    if not _git_ignores(LOCAL_DIR):
        _fail(
            f"git NO esta ignorando {LOCAL_DIR}.\n"
            "  Copiar aqui un proyecto real podria versionarlo. Revisa .gitignore\n"
            "  (debe contener 'tests/fixtures/local/') y vuelve a intentar."
        )

    # Guardarrail 2: origen y destino no pueden solaparse.
    project_dir = src.parent
    try:
        LOCAL_DIR.resolve().relative_to(project_dir)
        _fail("El destino esta dentro del proyecto de origen; abortado.")
    except ValueError:
        pass

    if LOCAL_DIR.exists():
        _make_writable(LOCAL_DIR)
        shutil.rmtree(LOCAL_DIR)
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)

    shutil.copy2(src, LOCAL_DIR / src.name)
    copied_dirs = []
    for pattern in ("*.Report", "*.SemanticModel"):
        for d in sorted(project_dir.glob(pattern)):
            if d.is_dir():
                shutil.copytree(d, LOCAL_DIR / d.name)
                copied_dirs.append(d.name)

    if not copied_dirs:
        print("AVISO: no se encontraron carpetas .Report / .SemanticModel junto al .pbip.")

    n = _make_read_only(LOCAL_DIR)
    total = sum(1 for p in LOCAL_DIR.rglob("*") if p.is_file())

    print("Fixture local preparado.")
    print(f"  origen (intacto)   : {src}")
    print(f"  destino (ignorado) : {LOCAL_DIR}")
    print(f"  carpetas copiadas  : {copied_dirs}")
    print(f"  archivos           : {total} ({n} marcados solo lectura)")
    print("  git lo ignora      : SI (verificado antes de copiar)")
    print("\nLas pruebas marcadas 'local_fixture' ahora se ejecutaran:")
    print("  python -m pytest -m local_fixture -q")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--source", help="Ruta al .pbip real (o a su carpeta).")
    g.add_argument("--status", action="store_true", help="Muestra el estado del fixture local.")
    g.add_argument("--remove", action="store_true", help="Elimina el fixture local.")
    args = ap.parse_args()

    if args.status:
        return cmd_status()
    if args.remove:
        return cmd_remove()
    return cmd_setup(args.source)


if __name__ == "__main__":
    raise SystemExit(main())
