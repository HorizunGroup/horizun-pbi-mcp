"""Genera `scripts/requirements.lock` — INSTALL-009, el lado del determinismo.

    python scripts/generar_lock.py            # regenera el lock
    python scripts/generar_lock.py --check    # falla si el lock esta desfasado

El problema: `install()` ejecuta `pip install <PLUGIN_ROOT>`, que **resuelve las
dependencias de cero cada vez**. Dos instalaciones separadas por una semana
producen entornos distintos sin que nadie lo pida ni lo note, y cuando una falla
y la otra no, no hay forma de saber en que se diferencian. Un producto que se
instala solo, en la maquina de otro, no puede depender de lo que hubiera en PyPI
esa tarde.

Lo que se congela: version EXACTA y SHA-256 de cada dependencia transitiva. El
propio paquete no va en el lock -no tiene hash publicado, es la fuente local- y
se instala aparte con `--no-deps`.

**Limite honesto, y hay que decirlo antes de que alguien se confie:** el lock se
resuelve con UN interprete. `pip install --require-hashes` exige que TODO lo que
vaya a instalar este listado, asi que un lock hecho en 3.14 puede no cubrir las
ruedas que 3.10 necesitaria. Por eso el instalador lo usa como camino
PREFERENTE y no como unico: si el lock no cubre el entorno, cae al resolutor
normal y **lo dice en el estado**, en vez de fallar la instalacion entera por
una garantia que no aplica.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

RAIZ = Path(__file__).resolve().parent.parent
LOCK = RAIZ / "scripts" / "requirements.lock"


def resolver() -> Dict[str, Any]:
    """Pregunta a pip que instalaria, sin instalar nada."""
    with tempfile.TemporaryDirectory(prefix="hz_lock_") as tmp:
        destino = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--ignore-installed", "--report", str(destino), str(RAIZ)],
            check=True, capture_output=True, timeout=1800)
        return json.loads(destino.read_text(encoding="utf-8"))


def lineas_del_lock(reporte: Dict[str, Any]) -> List[str]:
    entradas = []
    for paquete in reporte.get("install", []):
        meta = paquete["metadata"]
        nombre, version = meta["name"], meta["version"]
        hashes = ((paquete.get("download_info") or {})
                  .get("archive_info", {}).get("hashes", {}))
        sha = hashes.get("sha256")
        if not sha:
            # Es el propio paquete: la fuente local no tiene hash publicado y se
            # instala aparte con --no-deps. No se inventa un hash para que la
            # linea "quede bonita".
            continue
        entradas.append(f"{nombre}=={version} --hash=sha256:{sha}")
    return sorted(entradas)


def escribir(entradas: List[str]) -> Path:
    cabecera = [
        "# GENERADO por scripts/generar_lock.py. No editar a mano.",
        "#",
        "# INSTALL-009: sin esto, `pip install <repo>` resuelve de cero en cada",
        "# instalacion y dos maquinas -o la misma en dos semanas- acaban con",
        "# entornos distintos que nadie pidio.",
        "#",
        f"# Resuelto con Python {sys.version_info.major}.{sys.version_info.minor} "
        f"en {sys.platform}.",
        "# El paquete propio NO figura aqui: se instala aparte con --no-deps.",
        "",
    ]
    LOCK.write_text("\n".join(cabecera + entradas) + "\n", encoding="utf-8",
                    newline="")
    return LOCK


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true",
                   help="no escribe; sale 1 si el lock no coincide con el resuelto")
    args = p.parse_args()

    entradas = lineas_del_lock(resolver())
    if not args.check:
        print(f"Lock regenerado: {escribir(entradas)}")
        print(f"  {len(entradas)} dependencia(s) fijadas con SHA-256")
        return 0

    if not LOCK.exists():
        print("No existe el lock. Generalo con: python scripts/generar_lock.py",
              file=sys.stderr)
        return 1
    actuales = [l for l in LOCK.read_text(encoding="utf-8").splitlines()
                if l and not l.startswith("#")]
    if actuales == entradas:
        print(f"El lock esta al dia ({len(entradas)} dependencias).")
        return 0
    faltan = sorted(set(entradas) - set(actuales))
    sobran = sorted(set(actuales) - set(entradas))
    for l in faltan:
        print(f"  [+] {l}")
    for l in sobran:
        print(f"  [-] {l}")
    # Que esto salga 1 no significa que el lock este roto: casi siempre
    # significa que PyPI se movio, que es exactamente lo que el lock existe
    # para que no pase a espaldas de nadie. Adoptarlo es una DECISION -se
    # regenera y se corre la suite contra el conjunto nuevo-, no un tramite.
    print("\nEl lock fija lo que se probo; arriba esta lo que PyPI ofrece hoy.",
          file=sys.stderr)
    print("Para adoptarlo: python scripts/generar_lock.py, y pasa la suite.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
