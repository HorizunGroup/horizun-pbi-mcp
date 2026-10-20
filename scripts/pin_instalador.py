"""Recalcula los pines del instalador A PARTIR DEL ARCHIVO FIRMADO.

El instalador `scripts/instalar.ps1` es el unico binario con firma de este
repositorio, y su SHA-256 esta FIJADO en cuatro sitios del repo:

* `scripts/downloads_manifest.json` (`sha256`, `actual_bytes`, `url`);
* `scripts/one_paste.ps1` (`$sha`, `$url`): la fuente canonica del bloque de un
  pegado, que verifica ese hash antes de ejecutar nada;
* `docs/INSTALL.md` y `skills/horizun-pbi-setup/SKILL.md`, copias del bloque que
  `sync_one_paste.py` mantiene.

Firmar el .ps1 DESPUES de construir la release cambia su hash y rompe el
one-paste: es lo que paso con la v2.1.2 (el asset publicado se re-firmo a
posteriori y el bloque quedo apuntando al hash de antes de firmar). Por eso el
diseno es: **firmar en el arbol, antes de etiquetar, y calcular los pines sobre
el archivo firmado**. Este script es el segundo paso de ese diseno:

    1. firmar scripts/instalar.ps1 con signtool (RELEASING.md)
    2. python scripts/pin_instalador.py          # escribe los pines
    3. python scripts/pin_instalador.py --check  # comprueba que cuadran

Se niega a pinear un instalador sin firmar: pinear el hash de un archivo que
luego se firma es justo el error que este paso existe para evitar.

Uso:
    python scripts/pin_instalador.py            # reescribe manifest, bloque y docs
    python scripts/pin_instalador.py --check    # solo informa; 1 si algo diverge
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_verify as rv                                      # noqa: E402
import sync_one_paste as sop                                     # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "scripts" / "instalar.ps1"
MANIFEST = RAIZ / "scripts" / "downloads_manifest.json"
ONE_PASTE = RAIZ / "scripts" / "one_paste.ps1"

_BLOQUE = re.compile(r'("instalar\.ps1"\s*:\s*\{)(.*?)(\n    \},?\n)', re.S)


def _escribir(ruta: Path, texto: str, original: str) -> None:
    if texto != original:
        # newline='' para no convertir LF en CRLF en Windows.
        ruta.write_text(texto, encoding="utf-8", newline="")


def calcular() -> dict:
    """sha256 y tamano del instalador FIRMADO. Para si no esta firmado."""
    crudo = INSTALADOR.read_bytes()
    rv.exigir_firmado(crudo, "scripts/instalar.ps1")
    return {"sha256": hashlib.sha256(crudo).hexdigest(), "bytes": len(crudo)}


def _pines_del_manifest(texto: str) -> dict:
    entrada = json.loads(texto)["downloads"]["instalar.ps1"]
    return {"sha256": entrada["sha256"], "bytes": entrada["actual_bytes"],
            "url": entrada["url"]}


def _reescribir_manifest(texto: str, pines: dict) -> str:
    m = _BLOQUE.search(texto)
    if not m:
        raise SystemExit("[pin_instalador] no encuentro el bloque instalar.ps1 "
                         "en downloads_manifest.json")
    cuerpo = m.group(2)
    cuerpo, n1 = re.subn(r'("sha256"\s*:\s*")[0-9a-f]{64}(")',
                         rf'\g<1>{pines["sha256"]}\g<2>', cuerpo)
    cuerpo, n2 = re.subn(r'("actual_bytes"\s*:\s*)\d+',
                         rf'\g<1>{pines["bytes"]}', cuerpo)
    if (n1, n2) != (1, 1):
        raise SystemExit("[pin_instalador] el manifest no tiene exactamente un "
                         "sha256 y un actual_bytes para instalar.ps1")
    return texto[:m.start(2)] + cuerpo + texto[m.end(2):]


def _reescribir_one_paste(texto: str, pines: dict, url: str) -> str:
    texto, n_sha = re.subn(r"^\$sha = '[^']*'$", f"$sha = '{pines['sha256']}'",
                           texto, flags=re.M)
    texto, n_url = re.subn(r"^\$url = '[^']*'$", f"$url = '{url}'",
                           texto, flags=re.M)
    if (n_sha, n_url) != (1, 1):
        raise SystemExit("[pin_instalador] one_paste.ps1 no tiene exactamente "
                         "una linea $sha y una $url")
    return texto


def pinear(*, solo_comprobar: bool) -> int:
    pines = calcular()
    manifest_txt = MANIFEST.read_text(encoding="utf-8")
    url = _pines_del_manifest(manifest_txt)["url"]
    paste_txt = ONE_PASTE.read_text(encoding="ascii")

    nuevo_manifest = _reescribir_manifest(manifest_txt, pines)
    nuevo_paste = _reescribir_one_paste(paste_txt, pines, url)

    divergen = []
    if nuevo_manifest != manifest_txt:
        divergen.append("scripts/downloads_manifest.json")
    if nuevo_paste != paste_txt:
        divergen.append("scripts/one_paste.ps1")

    if not solo_comprobar:
        _escribir(MANIFEST, nuevo_manifest, manifest_txt)
        _escribir(ONE_PASTE, nuevo_paste, paste_txt)

    # Las copias del bloque en la documentacion, desde el canonico ya pineado.
    canonico = "\n".join(
        nuevo_paste.splitlines()[
            next(n for n, l in enumerate(nuevo_paste.splitlines())
                 if l.strip() and not l.startswith("#")):]).rstrip("\n")
    for rel in sop.DOCUMENTOS:
        ruta = RAIZ / rel
        texto = ruta.read_text(encoding="utf-8")
        if sop.HUELLA not in texto:
            raise SystemExit(f"[pin_instalador] {rel} ya no lleva el bloque de "
                             "un pegado")
        nuevo, cambiados = sop.sincronizar(texto, canonico)
        if cambiados:
            divergen.append(rel)
            if not solo_comprobar:
                ruta.write_text(nuevo, encoding="utf-8", newline="")

    print(f"[pin_instalador] instalador firmado: {pines['bytes']} bytes, "
          f"sha256 {pines['sha256']}")
    if solo_comprobar:
        if divergen:
            print("[pin_instalador] PINES DESFASADOS respecto al archivo firmado:",
                  file=sys.stderr)
            for d in divergen:
                print(f"  - {d}", file=sys.stderr)
            print("Ejecuta: python scripts/pin_instalador.py", file=sys.stderr)
            return 1
        print("[pin_instalador] OK: manifest, bloque de un pegado, INSTALL.md y "
              "la skill apuntan a los bytes firmados.")
        return 0
    print("[pin_instalador] pines actualizados en: "
          + (", ".join(divergen) if divergen else "(nada que cambiar)"))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="no escribe; devuelve 1 si algun pin no es el del "
                         "archivo firmado")
    args = ap.parse_args(argv)
    return pinear(solo_comprobar=args.check)


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
