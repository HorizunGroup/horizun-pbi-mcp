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

Ahora hay un lock por combinacion soportada, cada uno resuelto con su **propio
interprete real**, y el instalador elige por coincidencia exacta. Si no hay lock
para su combinacion no se inventa uno parecido: cae al resolutor y lo dice. Ese
fallback es honesto, pero no reproducible.

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
from pathlib import Path

# `tomllib` es de 3.11 para arriba, y este script tiene que correr en **3.10**:
# es la version minima que los classifiers prometen, y es justo la que verifica
# su propio lock en CI. Importarlo a secas hacia que el verificador de la matriz
# no arrancara en el interprete mas antiguo que dice cubrir -lo delato CI, no
# esta maquina-.
try:                                        # pragma: no cover - depende de la version
    import tomllib
except ModuleNotFoundError:                 # pragma: no cover
    try:
        import tomli as tomllib             # type: ignore[no-redef]
    except ModuleNotFoundError as exc:      # pragma: no cover
        raise SystemExit(
            "Este script lee pyproject.toml y necesita un lector de TOML. En "
            "Python 3.10 no viene en la biblioteca estandar: instala `tomli` "
            "(`python -m pip install tomli`) o usa Python >= 3.11."
        ) from exc
from typing import Any, Dict, Iterable, List, Tuple

RAIZ = Path(__file__).resolve().parent.parent
LOCKS = RAIZ / "scripts" / "locks"
PYPROJECT = RAIZ / "pyproject.toml"

#: Las combinaciones para las que HAY un lock fiel. Cada archivo se genera con
#: su propio interprete y solo entra aqui despues de instalar correctamente con
#: `--require-hashes` bajo esa misma version.
#:
#: La version anterior generaba los cinco desde un solo interprete con
#: `pip --python-version`, y **eso no produce un lock fiel**: pip evalua los
#: marcadores de entorno contra el interprete que CORRE, no contra el de
#: destino. Resolver `anyio>=4` para 3.10 desde 3.14 devuelve `anyio` e `idna`
#: y se deja `exceptiongroup`, que anyio solo pide en `python_version < "3.11"`.
#: Los cinco locks salian con las mismas 43 entradas; lo unico que cambiaba
#: eran hashes de ruedas.
#:
#: El resultado no era «un lock incompleto»: era uno que **no instala**.
#: `pip install --require-hashes` se niega en cuanto aparece una dependencia
#: sin fijar, y lo dijo CI: *«In --require-hashes mode, all requirements must
#: have their versions pinned with ==. These do not: exceptiongroup>=1.0.2»*.
#:
#: Asi que un lock se genera **en su propio interprete** o no se genera. Los
#: cinco declarados se construyen y se prueban por separado; la prueba de
#: instalacion real es la evidencia, no esta lista.
MATRIZ: Tuple[Tuple[str, str], ...] = (
    ("3.10", "win_amd64"),
    ("3.11", "win_amd64"),
    ("3.12", "win_amd64"),
    ("3.13", "win_amd64"),
    ("3.14", "win_amd64"),
)

#: Classifiers todavia sin lock fiel. Vacia solo porque cada entrada de MATRIZ
#: fue generada y probada con su interprete exacto. Los tests exigen que
#: classifiers == MATRIZ | PENDIENTES.
PENDIENTES: Tuple[Tuple[str, str], ...] = ()


def version_en_curso() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


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
    """Le pregunta a pip que instalaria, **con este mismo interprete**.

    Se niega a resolver para otra version a proposito. `pip --python-version`
    cambia las etiquetas de rueda compatibles pero **evalua los marcadores de
    entorno contra el interprete que corre**, asi que un lock hecho asi omite
    las dependencias condicionales del destino y despues `--require-hashes` se
    niega a instalarlo. Un lock que no instala es peor que no tenerlo: parece
    una garantia.
    """
    if version != version_en_curso():
        raise SystemExit(
            f"Este script genera el lock de SU PROPIO interprete ({version_en_curso()}), "
            f"y se le pidio {version}. Resolver para otra version con "
            "`--python-version` produce un lock que no instala: pip evalua los "
            "marcadores contra el interprete que corre. Ejecutalo con Python "
            f"{version}.")
    with tempfile.TemporaryDirectory(prefix="hz_lock_") as tmp:
        destino = Path(tmp) / "report.json"
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--dry-run", "--quiet",
             "--ignore-installed", "--report", str(destino),
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

    mio = version_en_curso()
    entradas = lineas_del_lock(resolver(mio, "win_amd64"))
    ruta = escribir(mio, "win_amd64", entradas)
    print(f"{ruta.name}: {len(entradas)} dependencia(s) fijadas")
    faltan = [f"py{v}" for v, _ in PENDIENTES if v != mio]
    if faltan:
        print(f"  siguen sin lock: {', '.join(faltan)} — ejecuta este script "
              "con cada uno de esos interpretes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
