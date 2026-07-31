"""Descarga VERIFICADA de las DLL de ADOMD.NET y TOM (Fase J3).

Que hacia antes
---------------
`latest_stable()` preguntaba a NuGet cual era la ultima version y se la
tragaba, sin hash y sin comprobar nada. Dos instalaciones del mismo commit
podian acabar con DLL distintas, y una version nueva podia romper el servidor
sin que nada lo avisara. Ademas extraia directamente sobre `libs/`: un fallo a
medias dejaba una mezcla de dos versiones, que es peor que no tener ninguna.

Que hace ahora
--------------
1. Version EXACTA por paquete, con su URL determinista.
2. SHA-256 del `.nupkg` verificado ANTES de abrirlo.
3. Se comprueba que el paquete traiga las DLL esperadas.
4. Extraccion a un directorio temporal, e instalacion atomica: `libs/` solo se
   toca cuando todo lo anterior ha salido bien.
5. Idempotente: si ya estan y coinciden, no descarga nada.
6. Fallo cerrado en todos los pasos.

`libs/` sigue fuera de Git: son binarios de Microsoft y no nos corresponde
redistribuirlos.

    python scripts/fetch_libs.py            # instala/verifica
    python scripts/fetch_libs.py --check    # solo informa
    python scripts/fetch_libs.py --update   # recalcula hashes de las versiones fijadas
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LIBS = PROJECT_ROOT / "libs"
MANIFIESTO = PROJECT_ROOT / "scripts" / "libs_manifest.json"

#: Solo se descarga de aqui.
ORIGEN = "https://www.nuget.org/api/v2/package/"

#: Version fijada. Es la que se probó y con la que funciona el servidor; no se
#: sube sin ejecutar la suite contra ella.
VERSION_FIJADA = "19.84.1"

PAQUETES = [
    {"id": "Microsoft.AnalysisServices.AdomdClient.retail.amd64",
     "version": VERSION_FIJADA,
     "purpose": "ADOMD.NET (consultas DAX)",
     "license": "Microsoft — ver terminos del paquete NuGet. NO se redistribuye."},
    {"id": "Microsoft.AnalysisServices.retail.amd64",
     "version": VERSION_FIJADA,
     "purpose": "AMO + TOM (lectura y escritura del modelo)",
     "license": "Microsoft — ver terminos del paquete NuGet. NO se redistribuye."},
]

PREFERRED_TFMS = ("/lib/net45/", "/lib/net472/", "/lib/netstandard2.0/")

#: Sin estas el servidor no arranca la capa en vivo.
DLLS_OBLIGATORIAS = [
    "Microsoft.AnalysisServices.AdomdClient.dll",
    "Microsoft.AnalysisServices.Core.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
    "Microsoft.AnalysisServices.dll",
]

TIEMPO_LIMITE = 300
#: Un .nupkg de estos ronda los 10 MB; 200 es un tope generoso y finito.
MAX_BYTES = 200 * 1024 * 1024


class LibsError(RuntimeError):
    pass


def url_de(paquete: Dict[str, str]) -> str:
    return f"{ORIGEN}{paquete['id']}/{paquete['version']}"


def descargar(url: str) -> bytes:
    if not url.startswith(ORIGEN):
        raise LibsError(f"Origen no permitido: {url}")
    try:
        with urllib.request.urlopen(url, timeout=TIEMPO_LIMITE) as r:
            datos = r.read(MAX_BYTES + 1)
    except OSError as exc:
        # URLError es OSError, pero urlopen tambien lanza OSError a secas
        # (socket cerrado, DNS, timeout del sistema). Capturar solo URLError
        # dejaba escapar la mitad de los cortes de red.
        raise LibsError(f"No se pudo descargar {url}: {exc}") from exc
    if len(datos) > MAX_BYTES:
        raise LibsError(f"El paquete supera {MAX_BYTES} bytes: {url}")
    if len(datos) < 1024:
        raise LibsError(
            f"Descarga truncada o vacia ({len(datos)} bytes): {url}")
    return datos


def leer_manifiesto() -> Dict[str, Any]:
    if not MANIFIESTO.exists():
        raise LibsError(
            f"No existe {MANIFIESTO}. Ejecuta --update para generarlo.")
    return json.loads(MANIFIESTO.read_text(encoding="utf-8"))


def dlls_del_paquete(datos: bytes, pid: str) -> Dict[str, bytes]:
    """DLL del TFM preferido. Rechaza un paquete sin ninguno compatible."""
    try:
        zf = zipfile.ZipFile(__import__("io").BytesIO(datos))
    except zipfile.BadZipFile as exc:
        raise LibsError(f"{pid}: el paquete no es un zip valido "
                        "(descarga corrupta?)") from exc

    nombres = [n for n in zf.namelist() if n.lower().endswith(".dll")]
    if not nombres:
        raise LibsError(f"{pid}: el paquete no contiene ninguna DLL. "
                        "No es el paquete que esperabamos.")

    elegido = None
    for tfm in PREFERRED_TFMS:
        if any(tfm in ("/" + n.lower().replace("\\", "/")) for n in nombres):
            elegido = tfm
            break
    if elegido is None:
        raise LibsError(f"{pid}: no hay ningun TFM compatible "
                        f"{PREFERRED_TFMS} en el paquete.")

    salida: Dict[str, bytes] = {}
    for n in nombres:
        if elegido in ("/" + n.lower().replace("\\", "/")):
            salida[Path(n).name] = zf.read(n)
    return salida


def estado() -> Dict[str, Any]:
    """Que hay instalado y si coincide con el manifiesto. No lanza."""
    try:
        m = leer_manifiesto()
    except LibsError as exc:
        return {"ready": False, "reason": str(exc), "missing": [], "mismatch": []}

    faltan, distintos = [], []
    for nombre, esperado in m["files"].items():
        f = LIBS / nombre
        if not f.exists():
            faltan.append(nombre)
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != esperado["sha256"]:
            distintos.append(nombre)

    obligatorias_ok = all((LIBS / d).exists() for d in DLLS_OBLIGATORIAS)
    return {
        "ready": not faltan and not distintos and obligatorias_ok,
        "dir": str(LIBS), "expected": len(m["files"]),
        "missing": faltan, "mismatch": distintos,
        "pinned_version": VERSION_FIJADA,
        "required_present": obligatorias_ok,
        "reason": ("" if not faltan and not distintos and obligatorias_ok else
                   f"{len(faltan)} ausente(s), {len(distintos)} con hash "
                   "distinto"),
    }


def construir_manifiesto() -> Dict[str, Any]:
    """Descarga las versiones fijadas y anota sus hashes."""
    from datetime import date

    paquetes, archivos = [], {}
    for paquete in PAQUETES:
        url = url_de(paquete)
        datos = descargar(url)
        paquetes.append({**paquete, "url": url,
                         "sha256": hashlib.sha256(datos).hexdigest(),
                         "bytes": len(datos)})
        for nombre, contenido in dlls_del_paquete(datos, paquete["id"]).items():
            archivos[nombre] = {
                "sha256": hashlib.sha256(contenido).hexdigest(),
                "bytes": len(contenido),
                "package": paquete["id"],
            }

    faltan = [d for d in DLLS_OBLIGATORIAS if d not in archivos]
    if faltan:
        raise LibsError(
            f"Las versiones fijadas NO traen {faltan}. No se genera un "
            "manifiesto que dejaria el servidor sin arrancar.")

    return {
        "manifest_version": 1,
        "generated": date.today().isoformat(),
        "source": ORIGEN,
        "redistribution": ("NO. Son binarios de Microsoft: se descargan aqui y "
                           "libs/ queda fuera del control de versiones."),
        "required_dlls": DLLS_OBLIGATORIAS,
        "packages": paquetes,
        "files": dict(sorted(archivos.items())),
    }


def instalar() -> Dict[str, Any]:
    """Descarga, verifica y solo entonces toca `libs/`."""
    m = leer_manifiesto()
    esperado_paquete = {p["id"]: p for p in m["packages"]}

    tmp = Path(tempfile.mkdtemp(prefix="hz_libs_"))
    try:
        extraidas: Dict[str, bytes] = {}
        for paquete in PAQUETES:
            ref = esperado_paquete.get(paquete["id"])
            if ref is None:
                raise LibsError(
                    f"{paquete['id']} no esta en el manifiesto. Ejecuta --update.")
            if ref["version"] != paquete["version"]:
                raise LibsError(
                    f"{paquete['id']}: el manifiesto fija {ref['version']} y el "
                    f"script pide {paquete['version']}.")

            datos = descargar(url_de(paquete))
            real = hashlib.sha256(datos).hexdigest()
            if real != ref["sha256"]:
                raise LibsError(
                    f"HASH DISTINTO en {paquete['id']} {paquete['version']}\n"
                    f"  esperado: {ref['sha256']}\n"
                    f"  obtenido: {real}\n"
                    "No se instala nada. Si el cambio es legitimo, revisa el "
                    "contenido y regenera el manifiesto con --update.")
            extraidas.update(dlls_del_paquete(datos, paquete["id"]))

        # Cada DLL, contra su hash, antes de que ninguna llegue a libs/.
        for nombre, ref in m["files"].items():
            if nombre not in extraidas:
                raise LibsError(
                    f"El paquete ya no trae {nombre}, que el manifiesto espera.")
            real = hashlib.sha256(extraidas[nombre]).hexdigest()
            if real != ref["sha256"]:
                raise LibsError(
                    f"HASH DISTINTO en {nombre}\n  esperado: {ref['sha256']}\n"
                    f"  obtenido: {real}\nNo se instala nada.")

        faltan = [d for d in DLLS_OBLIGATORIAS if d not in extraidas]
        if faltan:
            raise LibsError(f"Faltan DLL obligatorias: {faltan}. No se instala.")

        for nombre, contenido in extraidas.items():
            (tmp / nombre).write_bytes(contenido)

        LIBS.mkdir(parents=True, exist_ok=True)
        for f in tmp.iterdir():
            shutil.copy2(f, LIBS / f.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    final = estado()
    if not final["ready"]:
        raise LibsError(f"Tras instalar, la verificacion falla: {final['reason']}")
    return {"installed": len(m["files"]), "dir": str(LIBS),
            "version": VERSION_FIJADA}


def main() -> int:
    global LIBS
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true", help="solo informar")
    p.add_argument("--dest", default=None, help="directorio de instalacion")
    p.add_argument("--update", action="store_true",
                   help="recalcular el manifiesto desde las versiones fijadas")
    args = p.parse_args()

    if args.dest:
        LIBS = Path(args.dest).expanduser().resolve()

    if args.update:
        try:
            m = construir_manifiesto()
        except LibsError as exc:
            print(f"FALLO: {exc}", file=sys.stderr)
            return 1
        MANIFIESTO.write_text(json.dumps(m, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        print(f"Manifiesto actualizado: {MANIFIESTO}")
        print(f"  {len(m['packages'])} paquete(s), {len(m['files'])} DLL, "
              f"version {VERSION_FIJADA}")
        return 0

    if args.check:
        e = estado()
        print(json.dumps(e, indent=2, ensure_ascii=False))
        return 0 if e["ready"] else 1

    e = estado()
    if e["ready"]:
        print(f"Las {e['expected']} DLL ya estan instaladas y verificadas "
              f"(version {VERSION_FIJADA}). Nada que hacer.")
        return 0

    try:
        r = instalar()
    except LibsError as exc:
        print(f"FALLO: {exc}", file=sys.stderr)
        return 1
    print(f"{r['installed']} DLL verificadas e instaladas en {r['dir']} "
          f"(version {r['version']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
