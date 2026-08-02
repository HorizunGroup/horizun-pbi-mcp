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
import contextlib
import hashlib
import json
import shutil
import socket
import sys
import tempfile
import time
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

#: nuget.org resuelve via una cadena CNAME larga (Azure Traffic Manager) sin
#: registro AAAA. `getaddrinfo` con AF_UNSPEC pide IPv4 e IPv6 a la vez, y la
#: rama IPv6 (que no tiene a donde ir) a veces tira toda la consulta aunque un
#: segundo despues el mismo lookup funcione. Forzar IPv4 quita esa carrera; los
#: reintentos cubren el resto de cortes transitorios.
INTENTOS_DESCARGA = 4
ESPERA_ENTRE_INTENTOS = 0.3


class LibsError(RuntimeError):
    pass


def url_de(paquete: Dict[str, str]) -> str:
    return f"{ORIGEN}{paquete['id']}/{paquete['version']}"


@contextlib.contextmanager
def _resolver_solo_ipv4():
    """Fuerza IPv4 en `getaddrinfo` solo mientras dura la descarga.

    Se restaura en el `finally`: no dejamos el proceso entero forzado a IPv4
    despues de esta llamada.
    """
    original = socket.getaddrinfo

    def solo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return original(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = solo_ipv4
    try:
        yield
    finally:
        socket.getaddrinfo = original


def descargar(url: str) -> bytes:
    if not url.startswith(ORIGEN):
        raise LibsError(f"Origen no permitido: {url}")
    ultimo_error: Exception | None = None
    for intento in range(1, INTENTOS_DESCARGA + 1):
        try:
            with _resolver_solo_ipv4():
                with urllib.request.urlopen(url, timeout=TIEMPO_LIMITE) as r:
                    datos = r.read(MAX_BYTES + 1)
            break
        except OSError as exc:
            # URLError es OSError, pero urlopen tambien lanza OSError a secas
            # (socket cerrado, DNS, timeout del sistema). Capturar solo
            # URLError dejaba escapar la mitad de los cortes de red.
            ultimo_error = exc
            if intento < INTENTOS_DESCARGA:
                time.sleep(ESPERA_ENTRE_INTENTOS)
    else:
        raise LibsError(
            f"No se pudo descargar {url} tras {INTENTOS_DESCARGA} intentos "
            f"(forzando IPv4): {ultimo_error}") from ultimo_error
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


def _estado_directorio(directorio: Path, m: Dict[str, Any]) -> Dict[str, Any]:
    """Comprueba un directorio concreto contra un manifiesto ya leido."""
    faltan, distintos = [], []
    for nombre, esperado in m["files"].items():
        f = directorio / nombre
        if not f.exists():
            faltan.append(nombre)
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != esperado["sha256"]:
            distintos.append(nombre)

    obligatorias_ok = all((directorio / d).exists() for d in DLLS_OBLIGATORIAS)
    return {
        "ready": not faltan and not distintos and obligatorias_ok,
        "dir": str(directorio), "expected": len(m["files"]),
        "missing": faltan, "mismatch": distintos,
        "pinned_version": VERSION_FIJADA,
        "required_present": obligatorias_ok,
        "reason": ("" if not faltan and not distintos and obligatorias_ok else
                   f"{len(faltan)} ausente(s), {len(distintos)} con hash "
                   "distinto"),
    }


def estado() -> Dict[str, Any]:
    """Que hay instalado y si coincide con el manifiesto. No lanza."""
    try:
        m = leer_manifiesto()
        return _estado_directorio(LIBS, m)
    except (LibsError, OSError) as exc:
        return {"ready": False, "reason": str(exc), "missing": [], "mismatch": []}


def _existe(path: Path) -> bool:
    """Incluye enlaces simbolicos rotos, que ``Path.exists`` oculta."""
    return path.exists() or path.is_symlink()


def _eliminar_directorio(path: Path) -> None:
    """Elimina solo el artefacto indicado, sin seguir enlaces simbolicos."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _ruta_anterior() -> Path:
    return LIBS.with_name(f".{LIBS.name}.previous")


def _recuperar_interrupcion(m: Dict[str, Any]) -> None:
    """Resuelve el estado dejado por un corte entre los dos renombres.

    El respaldo tiene nombre fijo para que una ejecucion nueva pueda encontrarlo.
    Solo se restaura si coincide por completo con el manifiesto.
    """
    anterior = _ruta_anterior()
    if not _existe(anterior):
        return
    if anterior.is_symlink() or not anterior.is_dir():
        raise LibsError(f"Respaldo de instalacion inseguro: {anterior}")

    if not _existe(LIBS):
        if not _estado_directorio(anterior, m)["ready"]:
            raise LibsError(
                f"Hay un respaldo incompleto en {anterior}; no se restaura.")
        try:
            anterior.replace(LIBS)
        except OSError as exc:
            raise LibsError(
                f"No se pudo restaurar la instalacion anterior: {exc}") from exc
        return

    if LIBS.is_symlink() or not LIBS.is_dir():
        raise LibsError(f"Destino de instalacion inseguro: {LIBS}")
    if _estado_directorio(LIBS, m)["ready"]:
        _eliminar_directorio(anterior)
        return

    if not _estado_directorio(anterior, m)["ready"]:
        raise LibsError(
            "La instalacion y su respaldo estan incompletos; no se sobrescribe "
            "ninguno automaticamente.")

    fallida = LIBS.with_name(f".{LIBS.name}.interrupted")
    if _existe(fallida):
        raise LibsError(f"Ya existe un artefacto de recuperacion: {fallida}")
    try:
        LIBS.replace(fallida)
        anterior.replace(LIBS)
    except OSError as exc:
        if not _existe(LIBS) and _existe(fallida):
            try:
                fallida.replace(LIBS)
            except OSError:
                pass
        raise LibsError(f"No se pudo recuperar la instalacion: {exc}") from exc
    _eliminar_directorio(fallida)


def _promover_directorio(staging: Path, m: Dict[str, Any]) -> None:
    """Sustituye ``libs/`` por un staging completo y compensa cualquier fallo."""
    anterior = _ruta_anterior()
    if _existe(anterior):
        raise LibsError(f"Respaldo pendiente sin recuperar: {anterior}")
    if _existe(LIBS) and (LIBS.is_symlink() or not LIBS.is_dir()):
        raise LibsError(f"Destino de instalacion inseguro: {LIBS}")

    habia_anterior = _existe(LIBS)
    promovido = False
    fallida = staging.with_name(staging.name + ".failed")
    try:
        if habia_anterior:
            LIBS.replace(anterior)
        staging.replace(LIBS)
        promovido = True
        final = _estado_directorio(LIBS, m)
        if not final["ready"]:
            raise LibsError(
                f"Tras instalar, la verificacion falla: {final['reason']}")
    except Exception as exc:  # noqa: BLE001 - frontera de compensacion
        rollback_error = None
        try:
            if promovido and _existe(LIBS):
                LIBS.replace(fallida)
            if habia_anterior and _existe(anterior) and not _existe(LIBS):
                anterior.replace(LIBS)
            if _existe(fallida):
                _eliminar_directorio(fallida)
        except Exception as rollback_exc:  # noqa: BLE001
            rollback_error = rollback_exc

        if rollback_error is not None:
            raise LibsError(
                "Fallo al instalar y al restaurar. El respaldo se conserva en "
                f"{anterior}: {rollback_error}") from exc
        if isinstance(exc, LibsError):
            raise
        raise LibsError(
            f"No se pudo promover la instalacion; se restauro la anterior: {exc}"
        ) from exc

    if _existe(anterior):
        _eliminar_directorio(anterior)


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
    LIBS.parent.mkdir(parents=True, exist_ok=True)
    _recuperar_interrupcion(m)
    esperado_paquete = {p["id"]: p for p in m["packages"]}

    # Mismo volumen que el destino: el renombre final es una operacion atomica
    # del sistema de archivos y no puede degradarse a una copia parcial.
    tmp = Path(tempfile.mkdtemp(
        prefix=f".{LIBS.name}.stage_", dir=str(LIBS.parent)))
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

        staged = _estado_directorio(tmp, m)
        if not staged["ready"]:
            raise LibsError(
                f"El staging no supera la verificacion: {staged['reason']}")
        _promover_directorio(tmp, m)
    finally:
        if _existe(tmp):
            _eliminar_directorio(tmp)

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
