"""Genera y verifica los locks de dependencias — INSTALL-009 / G4.6.

    python scripts/generar_lock.py              # regenera TODA la matriz
    python scripts/generar_lock.py --check      # integridad, OFFLINE y determinista
    python scripts/generar_lock.py --updates    # consulta a PyPI: solo informa

El problema: `install()` ejecutaba `pip install <PLUGIN_ROOT>`, que **resuelve
las dependencias de cero cada vez**. Dos instalaciones separadas por una semana
producen entornos distintos sin que nadie lo pida ni lo note, y cuando una falla
y la otra no, no hay forma de saber en que se diferencian.

## Por que una MATRIZ y no un lock

La primera version tenia un solo lock, resuelto con el interprete de quien lo
generase, y su cabecera lo decia: «Python 3.14 en win32». Pero `pyproject`
admite `>=3.10` y CI corre 3.10 y 3.13. En esos dos, `pip install
--require-hashes` falla —el lock no lista las ruedas que necesitan— y el
instalador cae al resolutor **sin hashes**: justo en las versiones que mas gente
usa se perdia la garantia que INSTALL-009 venia a dar, y el estado lo decia en
una linea que nadie lee.

Ahora hay un lock por combinacion soportada, resuelto con `--python-version` y
`--platform`, y el instalador **elige por coincidencia exacta**. Si no hay lock
para su combinacion no se inventa uno parecido: cae al resolutor y lo dice, que
es honesto, pero entonces esa instalacion no es reproducible y G4.6 no esta
cumplido para ella.

## Por que `--check` no habla con PyPI

Antes `--check` comparaba el lock contra lo que pip resolveria HOY. Sale 1 en
cuanto alguien publica una version nueva —paso en la misma sesion en que se
genero: `charset-normalizer` 3.5.0 -> 3.5.1 en dos horas—, y eso no significa
que el lock este roto: significa que PyPI se movio. Un check que grita por algo
que no es un fallo acaba desactivado.

Son dos preguntas distintas y ahora son dos comandos:

* `--check` responde **«¿el lock que probamos sigue siendo coherente?»**. No
  toca la red: formato, hashes bien formados, sin duplicados, cabecera acorde al
  nombre del archivo, y todas las dependencias declaradas en `pyproject`
  presentes. Es determinista y sirve en CI.
* `--updates` responde **«¿PyPI ofrece algo nuevo?»**. Necesita red y solo
  informa. Adoptar lo nuevo es una decision: se regenera y se corre la suite.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

RAIZ = Path(__file__).resolve().parent.parent
LOCKS = RAIZ / "scripts" / "locks"
PYPROJECT = RAIZ / "pyproject.toml"

#: Las combinaciones que el producto declara soportar, **todas**.
#:
#: La plataforma es una sola y no por comodidad: `pyproject` declara
#: `Operating System :: Microsoft :: Windows` y nada mas. Exigir un runner
#: Linux para cerrar G4.6 era pedir evidencia de un entorno que el producto no
#: promete; lo que si promete son cinco versiones de Python, y hasta ahora la
#: matriz cubria tres. Ese era el hueco de verdad.
#:
#: `test_la_matriz_cubre_lo_que_pyproject_declara_soportar` lo ata a los
#: classifiers: anadir un `Programming Language :: Python :: 3.x` sin su lock
#: pone la suite en rojo.
MATRIZ: Tuple[Tuple[str, str], ...] = (
    ("3.10", "win_amd64"),
    ("3.11", "win_amd64"),
    ("3.12", "win_amd64"),
    ("3.13", "win_amd64"),
    ("3.14", "win_amd64"),
)

LINEA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*"
                   r"==[A-Za-z0-9][A-Za-z0-9.\-+!]*"
                   r" --hash=sha256:[0-9a-f]{64}$")


def nombre_de(version: str, plataforma: str) -> str:
    return f"requirements-py{version.replace('.', '')}-{plataforma}.lock"


def ruta_de(version: str, plataforma: str) -> Path:
    return LOCKS / nombre_de(version, plataforma)


def dependencias_declaradas() -> List[str]:
    """Lo que `pyproject` pide, que es lo que hay que fijar.

    Se resuelven las DEPENDENCIAS y no el paquete local a proposito: resolver
    `.` para otro interprete exigiria construirlo para ese interprete, y el
    paquete propio se instala aparte con `--no-deps` porque es la fuente local
    y no tiene hash publicado.
    """
    datos = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    return list(datos["project"]["dependencies"])


def normalizar(nombre: str) -> str:
    return nombre.lower().replace("_", "-")


def resolver(version: str, plataforma: str) -> Dict[str, Any]:
    """Le pregunta a pip que instalaria PARA ESA combinacion, sin instalar.

    `--only-binary=:all:` es obligatorio con `--python-version`: pip no puede
    construir un sdist para un interprete que no esta corriendo. Tambien es lo
    correcto para un lock —una rueda tiene hash estable; una construccion
    local, no—.
    """
    with tempfile.TemporaryDirectory(prefix="hz_lock_") as tmp:
        destino = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--ignore-installed", "--report", str(destino),
             "--python-version", version, "--platform", plataforma,
             "--only-binary=:all:", *dependencias_declaradas()],
            check=True, capture_output=True, timeout=1800)
        return json.loads(destino.read_text(encoding="utf-8"))


def lineas_del_lock(reporte: Dict[str, Any]) -> List[str]:
    entradas = []
    for paquete in reporte.get("install", []):
        meta = paquete["metadata"]
        hashes = ((paquete.get("download_info") or {})
                  .get("archive_info", {}).get("hashes", {}))
        sha = hashes.get("sha256")
        if not sha:
            # Sin hash no hay garantia que fijar. Inventarse uno para que la
            # linea "quede bonita" seria falsificarla.
            continue
        entradas.append(f"{meta['name']}=={meta['version']} --hash=sha256:{sha}")
    return sorted(entradas)


def cabecera(version: str, plataforma: str, cuantas: int) -> List[str]:
    return [
        "# GENERADO por scripts/generar_lock.py. No editar a mano.",
        "#",
        "# INSTALL-009: sin esto, `pip install <repo>` resuelve de cero en cada",
        "# instalacion y dos maquinas -o la misma en dos semanas- acaban con",
        "# entornos distintos que nadie pidio.",
        "#",
        f"# python-version: {version}",
        f"# platform: {plataforma}",
        f"# dependencias: {cuantas}",
        "#",
        "# El paquete propio NO figura aqui: es la fuente local, no tiene hash",
        "# publicado, y se instala aparte con --no-deps.",
        "",
    ]


def escribir(version: str, plataforma: str, entradas: List[str]) -> Path:
    LOCKS.mkdir(parents=True, exist_ok=True)
    ruta = ruta_de(version, plataforma)
    ruta.write_text("\n".join(cabecera(version, plataforma, len(entradas))
                              + entradas) + "\n", encoding="utf-8", newline="")
    return ruta


def entradas_de(ruta: Path) -> List[str]:
    return [l for l in ruta.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def metadatos_de(ruta: Path) -> Dict[str, str]:
    datos = {}
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^# (python-version|platform|dependencias): (.+)$", linea)
        if m:
            datos[m.group(1)] = m.group(2).strip()
    return datos


# ----------------------------------------------------- integridad (offline) --

def problemas_de(ruta: Path, version: str, plataforma: str) -> List[str]:
    """Todo lo que se puede comprobar SIN red. Devuelve la lista de fallos."""
    fallos: List[str] = []
    if not ruta.is_file():
        return [f"no existe {ruta.name}"]

    entradas = entradas_de(ruta)
    if not entradas:
        fallos.append(f"{ruta.name}: no fija ninguna dependencia")
    for linea in entradas:
        if not LINEA.match(linea):
            fallos.append(f"{ruta.name}: linea sin version+hash: {linea[:60]}")

    nombres = [normalizar(l.split("==", 1)[0]) for l in entradas]
    repetidos = sorted({n for n in nombres if nombres.count(n) > 1})
    if repetidos:
        fallos.append(f"{ruta.name}: fijadas dos veces: {repetidos}")
    if "horizun-pbi-mcp" in nombres:
        fallos.append(f"{ruta.name}: el paquete propio no puede figurar en el lock")

    meta = metadatos_de(ruta)
    if meta.get("python-version") != version:
        fallos.append(f"{ruta.name}: la cabecera dice python-version "
                      f"{meta.get('python-version')!r} y el nombre dice {version!r}")
    if meta.get("platform") != plataforma:
        fallos.append(f"{ruta.name}: la cabecera dice platform "
                      f"{meta.get('platform')!r} y el nombre dice {plataforma!r}")
    if meta.get("dependencias") != str(len(entradas)):
        fallos.append(f"{ruta.name}: la cabecera declara "
                      f"{meta.get('dependencias')} dependencias y hay {len(entradas)}")

    # Toda dependencia DIRECTA de pyproject tiene que estar fijada. Es lo que
    # convierte un lock viejo -al que se le añadio una dependencia despues- en
    # un fallo visible en vez de en un `--require-hashes` que revienta durante
    # la instalacion de otro.
    declaradas = {normalizar(re.split(r"[<>=!~\[]", d, maxsplit=1)[0].strip())
                  for d in dependencias_declaradas()}
    faltan = sorted(declaradas - set(nombres))
    if faltan:
        fallos.append(f"{ruta.name}: dependencias declaradas y sin fijar: {faltan}")
    return fallos


def comprobar(matriz: Iterable[Tuple[str, str]] = MATRIZ) -> List[str]:
    fallos: List[str] = []
    for version, plataforma in matriz:
        fallos.extend(problemas_de(ruta_de(version, plataforma), version, plataforma))
    return fallos


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true",
                   help="integridad de los locks; OFFLINE y determinista")
    p.add_argument("--updates", action="store_true",
                   help="consulta a PyPI si hay versiones nuevas; solo informa")
    args = p.parse_args(argv)

    if args.check:
        fallos = comprobar()
        if not fallos:
            total = sum(len(entradas_de(ruta_de(v, pl))) for v, pl in MATRIZ)
            print(f"Los {len(MATRIZ)} locks estan integros "
                  f"({total} dependencias fijadas en total).")
            return 0
        for f in fallos:
            print(f"  [!] {f}")
        print("\nRegenera con: python scripts/generar_lock.py", file=sys.stderr)
        return 1

    if args.updates:
        # La pregunta MOVIL, separada a proposito: que PyPI se mueva no rompe
        # nada. Adoptarlo es una decision que se toma corriendo la suite.
        hubo = False
        for version, plataforma in MATRIZ:
            actuales = set(entradas_de(ruta_de(version, plataforma)))
            nuevas = set(lineas_del_lock(resolver(version, plataforma)))
            if actuales == nuevas:
                print(f"py{version}/{plataforma}: al dia.")
                continue
            hubo = True
            print(f"py{version}/{plataforma}:")
            for l in sorted(nuevas - actuales):
                print(f"  [+] {l}")
            for l in sorted(actuales - nuevas):
                print(f"  [-] {l}")
        if hubo:
            print("\nEl lock fija lo que se probo; arriba esta lo que PyPI "
                  "ofrece hoy.", file=sys.stderr)
            print("Para adoptarlo: python scripts/generar_lock.py, y pasa la "
                  "suite entera.", file=sys.stderr)
        return 0

    for version, plataforma in MATRIZ:
        entradas = lineas_del_lock(resolver(version, plataforma))
        ruta = escribir(version, plataforma, entradas)
        print(f"{ruta.name}: {len(entradas)} dependencia(s) fijadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
