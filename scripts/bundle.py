"""Bundle offline — INSTALL-009 / G4.7, la mitad local.

    python scripts/bundle.py construir --salida <dir>     # lo arma
    python scripts/bundle.py verificar <bundle.zip>       # comprueba, no extrae
    python scripts/bundle.py instalar  <bundle.zip> --destino <dir>

## Qué problema resuelve

Una instalación normal descarga de **cuatro** sitios: PyPI, nuget.org,
developer.microsoft.com y registry.npmjs.org. En una máquina sin salida directa
—que es la mitad de las máquinas corporativas— no hay nada que hacer: el
instalador falla en el primer paso y no hay alternativa documentada.

El bundle es esa alternativa: **un archivo que ya lleva las cuatro cosas**,
verificado por SHA-256, que se instala sin tocar la red.

## Formato

Un ZIP con esta estructura y **nada más** —lo que sobre se rechaza, porque un
bundle que admite archivos extra admite un archivo extra malicioso—:

    bundle.json          manifiesto: version, hashes, tamanos, limites
    wheelhouse/          ruedas de las dependencias + el paquete propio
    libs/                DLL de Analysis Services
    schemas/pbir/        esquemas PBIR oficiales
    validator/           tarball del CLI de Microsoft

`bundle.json` lleva el SHA-256 y el tamaño de **cada** archivo, y su propio
hash va aparte, en `bundle.json.sha256`: un manifiesto que se verifica a sí
mismo no verifica nada.

## Lo que NO hace, y por qué

**No firma.** Firmar exige una clave, y una clave exige dónde guardarla y quién
la rota; eso es una decisión de operación, no de código. Lo que hay —hash por
archivo, manifiesto hasheado aparte, verificación completa antes de extraer— es
lo que se puede sostener hoy sin inventarse una ceremonia que nadie mantendría.

**No extrae y luego comprueba.** Se verifica **entero, antes** de escribir un
solo byte en el destino. Un bundle manipulado no llega a tocar el disco: es la
diferencia entre rechazar un archivo y tener que limpiar una instalación a
medias.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from horizun_pbi_mcp.lifecycle import locking as cerrojos    # noqa: E402
from horizun_pbi_mcp.lifecycle import promotion              # noqa: E402

VERSION_BUNDLE = 1
MANIFIESTO = "bundle.json"
HASH_DEL_MANIFIESTO = "bundle.json.sha256"

#: Las cuatro piezas, y el orden es el de la instalacion.
COMPONENTES = ("wheelhouse", "libs", "schemas", "validator")

#: Techo del archivo entero. No es un capricho: un ZIP sin limite es una bomba
#: de descompresion, y aqui se conoce el tamano esperado -unos 300 MB con las
#: DLL y el validador-. Se rechaza ANTES de abrirlo.
LIMITE_BUNDLE = 1_500 * 1024 * 1024

#: Techo por archivo extraido, contra el zip-bomb de un solo miembro.
LIMITE_MIEMBRO = 400 * 1024 * 1024


class BundleError(RuntimeError):
    pass


def _sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for trozo in iter(lambda: f.read(1024 * 1024), b""):
            h.update(trozo)
    return h.hexdigest()


# ------------------------------------------------------------- construccion --

def _wheelhouse(destino: Path, lock: Path) -> None:
    """Las ruedas del lock, mas el paquete propio construido aqui."""
    destino.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "download", "--require-hashes",
         "-r", str(lock), "-d", str(destino)],
        capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        raise BundleError(f"no se pudieron descargar las ruedas del lock:\n"
                          f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    r = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(destino),
         str(RAIZ)], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise BundleError(f"no se pudo construir el paquete propio:\n"
                          f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}")


def manifiesto_de(raiz: Path, version_producto: str, lock: str) -> Dict[str, Any]:
    """Recorre lo preparado y anota tamano y hash de cada archivo."""
    archivos: Dict[str, Dict[str, Any]] = {}
    total = 0
    for componente in COMPONENTES:
        base = raiz / componente
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(raiz).as_posix()
            tam = f.stat().st_size
            if tam > LIMITE_MIEMBRO:
                raise BundleError(
                    f"{rel} pesa {tam} bytes y el limite por archivo es "
                    f"{LIMITE_MIEMBRO}")
            archivos[rel] = {"sha256": _sha256_de(f), "bytes": tam}
            total += tam
    if not archivos:
        raise BundleError("no hay nada que empaquetar")
    return {
        "bundle_version": VERSION_BUNDLE,
        "producto": "horizun-pbi-mcp",
        "version": version_producto,
        "lock": lock,
        "componentes": sorted({r.split("/", 1)[0] for r in archivos}),
        "total_bytes": total,
        "limite_bytes": LIMITE_BUNDLE,
        "archivos": dict(sorted(archivos.items())),
    }


def empaquetar(preparado: Path, salida: Path, manifiesto: Dict[str, Any]) -> Path:
    """Escribe el ZIP con el manifiesto y su hash aparte."""
    crudo = json.dumps(manifiesto, indent=2, ensure_ascii=False).encode("utf-8")
    salida.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFIESTO, crudo)
        # El hash del manifiesto va FUERA del manifiesto: uno que se verifica a
        # si mismo no verifica nada.
        z.writestr(HASH_DEL_MANIFIESTO, _sha256(crudo))
        for rel in manifiesto["archivos"]:
            z.write(preparado / rel, rel)
    return salida


# ------------------------------------------------------------- verificacion --

def verificar(bundle: Path) -> Dict[str, Any]:
    """Comprueba el bundle ENTERO sin extraer nada al destino.

    Orden: tamano del archivo, manifiesto contra su hash, lista de miembros
    contra la del manifiesto, y hash de cada miembro leyendolo del ZIP. Un
    bundle manipulado no llega a tocar el disco.
    """
    if not bundle.is_file():
        raise BundleError(f"no existe {bundle}")
    tam = bundle.stat().st_size
    if tam > LIMITE_BUNDLE:
        raise BundleError(
            f"el bundle pesa {tam} bytes y el limite es {LIMITE_BUNDLE}: no se "
            "abre")

    with zipfile.ZipFile(bundle) as z:
        nombres = set(z.namelist())
        for obligatorio in (MANIFIESTO, HASH_DEL_MANIFIESTO):
            if obligatorio not in nombres:
                raise BundleError(f"al bundle le falta {obligatorio}")

        crudo = z.read(MANIFIESTO)
        esperado = z.read(HASH_DEL_MANIFIESTO).decode("utf-8").strip()
        if _sha256(crudo) != esperado:
            raise BundleError(
                "el manifiesto no cuadra con su propio hash: el bundle esta "
                "manipulado o corrupto")
        try:
            manifiesto = json.loads(crudo)
        except ValueError as exc:
            raise BundleError(f"el manifiesto no es JSON: {exc}") from exc
        if manifiesto.get("bundle_version") != VERSION_BUNDLE:
            raise BundleError(
                f"bundle_version {manifiesto.get('bundle_version')!r}: este "
                f"instalador entiende la {VERSION_BUNDLE}")

        declarados = set(manifiesto["archivos"])
        sobran = nombres - declarados - {MANIFIESTO, HASH_DEL_MANIFIESTO}
        if sobran:
            # Un bundle que admite archivos extra admite un archivo extra
            # malicioso, y el manifiesto no diria nada de el.
            raise BundleError(f"el bundle trae archivos sin declarar: "
                              f"{sorted(sobran)[:5]}")
        faltan = declarados - nombres
        if faltan:
            raise BundleError(f"el manifiesto declara archivos que no estan: "
                              f"{sorted(faltan)[:5]}")

        total = 0
        for rel, ref in manifiesto["archivos"].items():
            info = z.getinfo(rel)
            if info.file_size != ref["bytes"]:
                raise BundleError(
                    f"{rel}: el manifiesto dice {ref['bytes']} bytes y el "
                    f"archivo tiene {info.file_size}")
            if info.file_size > LIMITE_MIEMBRO:
                raise BundleError(f"{rel} supera el limite por archivo")
            total += info.file_size
            if _sha256(z.read(rel)) != ref["sha256"]:
                raise BundleError(
                    f"{rel}: HASH DISTINTO. No se instala nada.")
        if total != manifiesto["total_bytes"]:
            raise BundleError(
                f"el manifiesto declara {manifiesto['total_bytes']} bytes en "
                f"total y suman {total}")
    return manifiesto


# -------------------------------------------------------------- instalacion --

def instalar(bundle: Path, destino: Path) -> Dict[str, Any]:
    """Verifica, extrae a un staging y promueve. Sin red en ningun paso.

    La promocion es la del ciclo de vida compartido -journal, `.previous-`,
    recuperacion-, la misma que usan el runtime, los esquemas y el validador.
    No hacia falta una cuarta.
    """
    manifiesto = verificar(bundle)          # ANTES de escribir un solo byte
    destino = Path(destino).resolve()
    raiz = destino.parent
    raiz.mkdir(parents=True, exist_ok=True)

    with cerrojos.CerrojoDeCicloDeVida(raiz, etiqueta="bundle") as cerrojo:
        if not cerrojo.adquirido:
            raise BundleError(
                f"hay otra instalacion en curso sobre {raiz}. No se toca nada.")
        recuperado = promotion.recuperar(raiz)
        staging = promotion.crear_staging(raiz, destino.name)
        try:
            with zipfile.ZipFile(bundle) as z:
                for rel in manifiesto["archivos"]:
                    salida = promotion.bajo_root(staging, rel.split("/", 1)[0],
                                                 que="componente")
                    del salida            # solo para validar el primer segmento
                    ruta = staging / rel
                    ruta.parent.mkdir(parents=True, exist_ok=True)
                    ruta.write_bytes(z.read(rel))
            # Se relee del DISCO: que el ZIP cuadre demuestra que el archivo
            # llego entero, no que se haya ESCRITO entero.
            for rel, ref in manifiesto["archivos"].items():
                if _sha256_de(staging / rel) != ref["sha256"]:
                    raise BundleError(
                        f"{rel} no cuadra tras escribirlo: no se publica nada")
            promotion.promover(raiz, staging, destino)
        except BaseException:
            promotion.descartar_staging(staging)
            raise
        recogidos = promotion.limpiar_apartados_de(raiz, destino.name)

    return {"destino": str(destino), "archivos": len(manifiesto["archivos"]),
            "version": manifiesto["version"],
            "recuperacion_previa": recuperado.get("accion"),
            "respaldos_recogidos": len(recogidos)}


# ---------------------------------------------------------------------- CLI --

def _construir(args) -> int:
    from horizun_pbi_mcp.completado import esquemas, libs, validador

    del esquemas, libs, validador          # se invocan por CLI, no por import
    lock = Path(args.lock) if args.lock else None
    if lock is None:
        v = f"{sys.version_info.major}.{sys.version_info.minor}"
        lock = RAIZ / "scripts" / "locks" / f"requirements-py{v.replace('.', '')}-win_amd64.lock"
    if not lock.is_file():
        print(f"FALLO: no existe el lock {lock}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="hz_bundle_") as tmp:
        preparado = Path(tmp)
        if "wheelhouse" in args.componentes:
            _wheelhouse(preparado / "wheelhouse", lock)
        for componente, modulo in (("libs", "libs"), ("schemas", "esquemas"),
                                   ("validator", "validador")):
            if componente not in args.componentes:
                continue
            sub = preparado / componente
            sub.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                [sys.executable, "-m", f"horizun_pbi_mcp.completado.{modulo}",
                 "--dest", str(sub if componente != "schemas" else sub / "pbir")],
                capture_output=True, text=True, timeout=3600)
            if r.returncode != 0:
                print(f"FALLO al preparar {componente}:\n{r.stdout[-1500:]}\n"
                      f"{r.stderr[-1500:]}", file=sys.stderr)
                return 1

        from horizun_pbi_mcp import branding

        manifiesto = manifiesto_de(preparado, branding.VERSION, lock.name)
        salida = Path(args.salida) / f"horizun-pbi-mcp-{branding.VERSION}-bundle.zip"
        empaquetar(preparado, salida, manifiesto)

    print(f"Bundle construido: {salida}")
    print(f"  {len(manifiesto['archivos'])} archivo(s), "
          f"{manifiesto['total_bytes'] / 1024 / 1024:.1f} MiB")
    print(f"  componentes: {', '.join(manifiesto['componentes'])}")
    return 0


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="accion", required=True)

    c = sub.add_parser("construir", help="arma el bundle (necesita red)")
    c.add_argument("--salida", default=".", help="directorio donde dejarlo")
    c.add_argument("--lock", default=None, help="lock a usar (por defecto, el de este interprete)")
    c.add_argument("--componentes", nargs="+", default=list(COMPONENTES),
                   choices=list(COMPONENTES))

    v = sub.add_parser("verificar", help="comprueba el bundle sin extraerlo")
    v.add_argument("bundle")

    i = sub.add_parser("instalar", help="instala desde el bundle, sin red")
    i.add_argument("bundle")
    i.add_argument("--destino", required=True)

    args = p.parse_args(argv)
    try:
        if args.accion == "construir":
            return _construir(args)
        if args.accion == "verificar":
            m = verificar(Path(args.bundle))
            print(f"Bundle integro: {len(m['archivos'])} archivo(s), "
                  f"{m['total_bytes'] / 1024 / 1024:.1f} MiB, version {m['version']}")
            return 0
        r = instalar(Path(args.bundle), Path(args.destino))
        print(f"Instalado desde el bundle en {r['destino']}: "
              f"{r['archivos']} archivo(s), version {r['version']}")
        return 0
    except (BundleError, promotion.PromocionError) as exc:
        print(f"FALLO: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
