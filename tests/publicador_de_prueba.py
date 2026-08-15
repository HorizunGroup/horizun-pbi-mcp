"""Publica esquemas o el validador desde un proceso APARTE.

Las carreras entre dos publicaciones no se pueden reproducir con hilos: lo que
se está comprobando es un cerrojo **interproceso**, y un `threading.Lock` no
tiene nada que ver con eso. Así que hace falta un ejecutable que haga la
publicación de verdad, y este es.

    python tests/publicador_de_prueba.py schemas   <destino> <marca>
    python tests/publicador_de_prueba.py validator <destino> <marca>

`marca` es el contenido que escribe este publicador. Dos publicadores con
marcas distintas dejan un destino que se puede auditar: o es entero de uno, o
es entero del otro, o hay mezcla — y la mezcla es lo que no puede pasar.

Variables de entorno:

  PAUSA_EN_PROMOCION  segundos de parada justo DESPUÉS de apartar el destino y
                      ANTES de poner el staging, que es el único hueco no
                      atómico de la promoción. Reproduce el corte.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

ARCHIVOS = ("a.json", "b.json", "c.json")
RELATIVA = Path("node_modules/@microsoft/powerbi-report-authoring-cli/dist/cli.js")


def _pausar_en_el_hueco(promotion, segundos: float) -> None:
    """Para el proceso entre los dos renombrados de la promoción."""
    original = promotion.os.rename
    cuenta = {"n": 0}

    def lento(origen, destino):
        original(origen, destino)
        cuenta["n"] += 1
        if cuenta["n"] == 1:
            time.sleep(segundos)

    promotion.os.rename = lento


def _manifiesto(marca: str) -> dict:
    documentos = {n: f'{{"marca":"{marca}","archivo":"{n}"}}'.encode()
                  for n in ARCHIVOS}
    return {
        "manifest_version": 1,
        "documents": [
            {"url": f"https://developer.microsoft.com/json-schemas/fabric/{n}",
             "file": n, "sha256": hashlib.sha256(d).hexdigest(),
             "bytes": len(d), "root": True}
            for n, d in documentos.items()],
    }, documentos


def _publicar_schemas(destino: Path, marca: str, pausa: float) -> int:
    from horizun_pbi_mcp.completado import esquemas as fps

    if pausa:
        _pausar_en_el_hueco(fps.promotion, pausa)
    manifiesto, documentos = _manifiesto(marca)
    fps.descargar = lambda url: documentos[url.split("/")[-1]]
    try:
        fps.instalar(manifiesto, destino)
    except Exception as exc:                      # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def _publicar_validator(destino: Path, marca: str, pausa: float) -> int:
    from horizun_pbi_mcp.completado import validador as frv

    if pausa:
        _pausar_en_el_hueco(frv.promotion, pausa)

    def _correr(args, cwd=None, timeout=900):
        texto = " ".join(str(a) for a in args)
        if "pack" in texto:
            (Path(cwd) / "p.tgz").write_bytes(b"tarball")
            return subprocess.CompletedProcess(args, 0, "", "")
        if "install" in texto:
            prefijo = Path(args[args.index("--prefix") + 1])
            cli = prefijo / RELATIVA
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_text(f"// {marca}\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "v20.0.0", "")

    frv.comprobar_node = lambda: 20
    frv.shutil.which = lambda n: f"/falso/{n}"
    frv.verificar_tarball = lambda ruta: None
    frv._correr = _correr
    frv._verificar_preparado = lambda staging: ((staging / RELATIVA), frv.VERSION)
    try:
        frv.instalar(destino)
    except Exception as exc:                      # noqa: BLE001
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    componente, destino, marca = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    pausa = float(os.environ.get("PAUSA_EN_PROMOCION", "0"))
    if componente == "schemas":
        return _publicar_schemas(destino, marca, pausa)
    if componente == "validator":
        return _publicar_validator(destino, marca, pausa)
    raise SystemExit(f"componente desconocido: {componente}")


if __name__ == "__main__":
    raise SystemExit(main())
