"""Descarga verificada de los esquemas oficiales del PBIR (Fase E3.1).

Por que no van en el repositorio
--------------------------------
Los esquemas se publican en `developer.microsoft.com/json-schemas/...` y **no
declaran licencia ni permiso de redistribucion** —ni en el documento, ni junto
a el—. Sin una autorizacion explicita, copiarlos a este repositorio seria
redistribuir software de terceros por nuestra cuenta. Se descargan aqui, con
URL y hash fijados, igual que se hace con las DLL de Analysis Services.

Que hace
--------
1. Parte de los cinco esquemas raiz que el PBIR declara en sus `$schema`.
2. Sigue los `$ref` **transitivos** (son rutas relativas: `../../foo/1.2.3/
   schema.json#/definitions/Bar`) hasta cerrar el grafo completo.
3. Verifica el SHA-256 de cada documento ANTES de instalarlo.
4. Instala de forma atomica en `<cache>/pbir-schemas/` y escribe un manifiesto.

Fallo cerrado: si un hash no coincide, no se instala nada y el manifiesto
anterior queda intacto.

    python scripts/fetch_pbir_schemas.py            # instala/verifica
    python scripts/fetch_pbir_schemas.py --update   # recalcula el manifiesto
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Set, Tuple

RAIZ_REPO = Path(__file__).resolve().parent.parent
MANIFIESTO = RAIZ_REPO / "src" / "services" / "schemas" / "pbir_manifest.json"

BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/"

#: Todos los `$schema` que aparecen en informes PBIR reales, no solo los que
#: este servidor escribe. Validar en LECTURA un informe ajeno exige conocer sus
#: versiones: el PB4 de referencia declara visualContainer 2.10.0 en 239
#: archivos, bookmarks, versionMetadata y report 3.3.0, ninguno de los cuales
#: estaba cubierto por las cinco raices iniciales.
RAICES = [
    # los que ESCRIBE este servidor
    BASE + "definition/visualContainer/2.7.0/schema.json",
    BASE + "definition/page/2.1.0/schema.json",
    BASE + "definition/pagesMetadata/1.1.0/schema.json",
    BASE + "definition/report/2.0.0/schema.json",
    BASE + "definitionProperties/2.0.0/schema.json",
    # los que aparecen en informes reales y hay que saber LEER
    BASE + "definition/visualContainer/2.10.0/schema.json",
    BASE + "definition/pagesMetadata/1.0.0/schema.json",
    BASE + "definition/report/3.2.0/schema.json",
    BASE + "definition/report/3.3.0/schema.json",
    BASE + "definition/bookmark/2.0.0/schema.json",
    BASE + "definition/bookmark/2.1.0/schema.json",
    BASE + "definition/bookmarks/2.0.0/schema.json",
    BASE + "definition/bookmarksMetadata/1.0.0/schema.json",
    BASE + "definition/versionMetadata/1.0.0/schema.json",
    BASE + "localSettings/1.0.0/schema.json",
]

#: Solo se descarga de aqui. Un $ref que salga de este prefijo se rechaza.
PREFIJO_PERMITIDO = "https://developer.microsoft.com/json-schemas/fabric/"

TIEMPO_LIMITE = 30


class SchemaFetchError(RuntimeError):
    pass


def nombre_local(url: str) -> str:
    """URL -> nombre de archivo plano y estable."""
    resto = url[len(PREFIJO_PERMITIDO):] if url.startswith(PREFIJO_PERMITIDO) else url
    return resto.replace("/", "__").replace("#", "_")


def descargar(url: str) -> bytes:
    if not url.startswith(PREFIJO_PERMITIDO):
        raise SchemaFetchError(
            f"Referencia fuera del origen permitido: {url}\n"
            f"Solo se descarga de {PREFIJO_PERMITIDO}")
    req = urllib.request.Request(url, headers={"User-Agent": "horizun-pbi-mcp"})
    try:
        with urllib.request.urlopen(req, timeout=TIEMPO_LIMITE) as r:
            return r.read()
    except urllib.error.URLError as exc:
        raise SchemaFetchError(f"No se pudo descargar {url}: {exc}") from exc


def refs_de(doc, base_url: str) -> Set[str]:
    """`$ref` externos de un documento, resueltos contra su propia URL."""
    salida: Set[str] = set()

    def caminar(nodo):
        if isinstance(nodo, dict):
            for clave, valor in nodo.items():
                if clave == "$ref" and isinstance(valor, str):
                    if valor.startswith("#"):
                        continue                       # interno
                    destino = urllib.parse.urljoin(base_url, valor.split("#", 1)[0])
                    salida.add(destino)
                else:
                    caminar(valor)
        elif isinstance(nodo, list):
            for x in nodo:
                caminar(x)

    caminar(doc)
    return salida


def cerrar_grafo() -> Tuple[Dict[str, bytes], List[Dict[str, str]]]:
    """Descarga las raices y todo su cierre transitivo de `$ref`.

    Algunas URLs que Power BI escribe en `$schema` NO estan publicadas —el PB4
    de referencia declara `bookmarks/2.0.0`, que devuelve 404—. No es un fallo
    del descargador: se anota y se sigue, para que el manifiesto documente que
    ese esquema no se puede obtener y el validador lo diga con claridad en vez
    de fingir que lo comprueba.
    """
    pendientes: List[str] = list(RAICES)
    vistos: Dict[str, bytes] = {}
    ausentes: List[Dict[str, str]] = []
    while pendientes:
        url = pendientes.pop()
        if url in vistos or any(a["url"] == url for a in ausentes):
            continue
        try:
            datos = descargar(url)
        except SchemaFetchError as exc:
            if "404" not in str(exc):
                raise
            ausentes.append({"url": url, "reason": "404 en el origen oficial"})
            continue
        vistos[url] = datos
        try:
            doc = json.loads(datos)
        except ValueError as exc:
            raise SchemaFetchError(f"{url} no es JSON valido: {exc}") from exc
        for ref in refs_de(doc, url):
            if ref not in vistos:
                pendientes.append(ref)
    return vistos, ausentes


def construir_manifiesto() -> Dict:
    from datetime import date

    documentos, ausentes = cerrar_grafo()
    entradas = []
    for url in sorted(documentos):
        datos = documentos[url]
        entradas.append({
            "url": url,
            "file": nombre_local(url),
            "sha256": hashlib.sha256(datos).hexdigest(),
            "bytes": len(datos),
            "root": url in RAICES,
        })
    return {
        "manifest_version": 1,
        "retrieved": date.today().isoformat(),
        "source": PREFIJO_PERMITIDO,
        "redistribution": (
            "NO VERIFICADA. Los esquemas no declaran licencia ni permiso de "
            "redistribucion, asi que NO se copian a este repositorio: se "
            "descargan con este script y quedan en la cache local del usuario."),
        "roots": RAICES,
        "unavailable_upstream": ausentes,
        "documents": entradas,
    }


def cache_dir() -> Path:
    """La MISMA resolucion que services.pbir_schema, para no instalar donde el
    servidor no va a buscar."""
    sys.path.insert(0, str(RAIZ_REPO / "src"))
    from services.pbir_schema import cache_dir as resolver

    return resolver()


def instalar(manifiesto: Dict, destino: Path) -> Dict:
    """Descarga, VERIFICA y luego instala. Si un hash no cuadra, no toca nada."""
    esperado = {e["url"]: e for e in manifiesto["documents"]}
    tmp = Path(tempfile.mkdtemp(prefix="pbir_schemas_"))
    try:
        descargados = {}
        for url, entrada in esperado.items():
            datos = descargar(url)
            real = hashlib.sha256(datos).hexdigest()
            if real != entrada["sha256"]:
                raise SchemaFetchError(
                    f"HASH DISTINTO para {url}\n"
                    f"  esperado: {entrada['sha256']}\n"
                    f"  obtenido: {real}\n"
                    "No se instala nada. Si el cambio es legitimo, revisa el "
                    "contenido y regenera el manifiesto con --update.")
            (tmp / entrada["file"]).write_bytes(datos)
            descargados[url] = entrada["file"]

        destino.mkdir(parents=True, exist_ok=True)
        for archivo in tmp.iterdir():
            shutil.copy2(archivo, destino / archivo.name)
        (destino / "_manifest.json").write_text(
            json.dumps(manifiesto, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"installed": len(descargados), "dir": str(destino)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--update", action="store_true",
                   help="recalcula el manifiesto desde el origen (hashes nuevos)")
    p.add_argument("--dest", default=None, help="directorio de la cache")
    args = p.parse_args()

    if args.update:
        m = construir_manifiesto()
        MANIFIESTO.parent.mkdir(parents=True, exist_ok=True)
        MANIFIESTO.write_text(json.dumps(m, indent=2, ensure_ascii=False),
                              encoding="utf-8")
        print(f"Manifiesto actualizado: {MANIFIESTO}")
        print(f"  {len(m['documents'])} documento(s), "
              f"{sum(1 for d in m['documents'] if d['root'])} raiz/raices")
        for a in m["unavailable_upstream"]:
            print(f"  NO PUBLICADO: {a['url'].split('/report/')[-1]} ({a['reason']})")
        return 0

    if not MANIFIESTO.exists():
        print(f"No existe el manifiesto {MANIFIESTO}. Ejecuta --update primero.",
              file=sys.stderr)
        return 2
    m = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
    destino = Path(args.dest) if args.dest else cache_dir()
    try:
        r = instalar(m, destino)
    except SchemaFetchError as exc:
        print(f"FALLO: {exc}", file=sys.stderr)
        return 1
    print(f"{r['installed']} esquema(s) verificados e instalados en {r['dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
