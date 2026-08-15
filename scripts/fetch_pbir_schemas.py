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
sys.path.insert(0, str(RAIZ_REPO / "src"))

# El ciclo de vida COMPARTIDO. Publicar esquemas es el mismo problema que
# promover un runtime -preparar aparte, verificar, renombrar, poder
# recuperarse- y resolverlo por segunda vez habria significado tener tambien
# dos formas distintas de quedarse a medias.
from horizun_pbi_mcp.lifecycle import locking as cerrojos  # noqa: E402
from horizun_pbi_mcp.lifecycle import promotion  # noqa: E402
# Ruta bajo el paquete unico. La antigua (src/services/...) sobrevivio al
# reempaquetado porque ningun test ejecuta este script: fallaba en el bootstrap
# del plugin, en el paso de esquemas del CI y en la instruccion del README —
# los tres sitios que SI lo ejecutan. Peor: con --update habria RESUCITADO el
# arbol viejo, escribiendo el manifiesto donde el servidor ya no lee.
MANIFIESTO = (RAIZ_REPO / "src" / "horizun_pbi_mcp" / "services" / "schemas"
              / "pbir_manifest.json")

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
    from horizun_pbi_mcp.services.pbir_schema import cache_dir as resolver

    return resolver()


def _verificar_preparado(staging: Path, manifiesto: Dict) -> None:
    """Relee del DISCO todo lo preparado, antes de publicarlo.

    Comprobar el hash de lo que se acaba de descargar demuestra que la descarga
    llego entera; no demuestra que se haya ESCRITO entera. Un disco lleno, un
    antivirus que se lleva un archivo a mitad o un corte de corriente dejan un
    fichero corto sin que `write_bytes` se queje de nada. Releer cuesta unos
    milisegundos y es la diferencia entre publicar un esquema truncado -que
    despues hara fallar validaciones con un mensaje que no menciona la
    instalacion- y no publicarlo.
    """
    for entrada in manifiesto["documents"]:
        ruta = staging / entrada["file"]
        if not ruta.is_file():
            raise SchemaFetchError(
                f"falta {entrada['file']} en lo preparado: no se publica nada")
        crudo = ruta.read_bytes()
        if len(crudo) != entrada["bytes"]:
            raise SchemaFetchError(
                f"{entrada['file']} se escribio con {len(crudo)} bytes y el "
                f"manifiesto dice {entrada['bytes']}: no se publica nada")
        if hashlib.sha256(crudo).hexdigest() != entrada["sha256"]:
            raise SchemaFetchError(
                f"{entrada['file']} no cuadra de hash tras escribirlo: no se "
                "publica nada")
    try:
        json.loads((staging / "_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SchemaFetchError(
            f"el manifiesto preparado no se puede releer: {exc}") from exc


def instalar(manifiesto: Dict, destino: Path) -> Dict:
    """Prepara aparte, verifica entero y publica con un `rename` (INSTALL-006).

    Antes esto descargaba a un temporal y luego copiaba archivo por archivo
    ENCIMA del destino vivo. Dos defectos en esa forma. El primero: si la copia
    se cortaba a la mitad -por lo que sea- quedaba una mezcla de esquemas
    viejos y nuevos, que es un estado que nadie ha probado nunca y que no se
    distingue a simple vista de uno bueno. El segundo: los archivos que dejaban
    de estar en el manifiesto se quedaban ahi para siempre, porque copiar no
    borra.

    Ahora se prepara en un directorio HERMANO del destino -mismo volumen, para
    que publicar sea un `rename` y no una copia-, se relee entero, y solo
    entonces se publica. La publicacion usa el MISMO ciclo de vida que la
    promocion del runtime: journal, `.previous-` y recuperacion. No hacia falta
    una segunda forma de promover, y tener dos habria significado dos formas de
    recuperarse a medias.
    """
    destino = Path(destino)
    raiz = destino.parent
    raiz.mkdir(parents=True, exist_ok=True)

    # El cerrojo de ESTA raiz, y antes de nada. `promotion.recuperar()` dice en
    # su docstring que quien llama debe tenerlo, y este script no lo tenia: al
    # ejecutarse desde `install()` quedaba cubierto por el cerrojo de la raiz de
    # datos, pero es un script que se lanza tambien por su cuenta -asi lo
    # documenta el README y asi lo invoca el instalador- y ahi no habia ninguno.
    # Dos procesos podian leer y escribir el mismo journal y promover sobre el
    # mismo destino.
    #
    # Orden entre cerrojos, que hay que decirlo para que nadie lo invierta: el
    # de la raiz de DATOS se toma siempre primero (lo toma `install()`), y este
    # despues. Son raices distintas y la jerarquia es fija, asi que no hay ciclo.
    with cerrojos.CerrojoDeCicloDeVida(raiz, etiqueta="schemas") as cerrojo:
        if not cerrojo.adquirido:
            raise SchemaFetchError(
                f"Hay otra publicacion de esquemas en curso sobre {raiz}. No se "
                "toca nada: el destino anterior sigue intacto.")

        # Si una publicacion anterior se corto entre los dos renombrados, se
        # resuelve ANTES de preparar otra. Sin esto, la siguiente instalacion
        # trabajaria sobre un estado que no sabe describir.
        recuperado = promotion.recuperar(raiz)

        esperado = {e["url"]: e for e in manifiesto["documents"]}
        staging = promotion.crear_staging(raiz, destino.name)
        try:
            for url, entrada in esperado.items():
                datos = descargar(url)
                real = hashlib.sha256(datos).hexdigest()
                if real != entrada["sha256"]:
                    raise SchemaFetchError(
                        f"HASH DISTINTO para {url}\n"
                        f"  esperado: {entrada['sha256']}\n"
                        f"  obtenido: {real}\n"
                        "No se instala nada. Si el cambio es legitimo, revisa "
                        "el contenido y regenera el manifiesto con --update.")
                (staging / entrada["file"]).write_bytes(datos)

            (staging / "_manifest.json").write_text(
                json.dumps(manifiesto, indent=2, ensure_ascii=False),
                encoding="utf-8")
            _verificar_preparado(staging, manifiesto)
            promotion.promover(raiz, staging, destino)
        except BaseException:
            # Tambien en KeyboardInterrupt: dejar el staging seria dejar basura
            # con un prefijo que la limpieza del ciclo de vida reconoce, pero no
            # hay motivo para esperar a que alguien pase por ahi.
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # Publicado y verificado: el apartado ya no sirve. Solo los de ESTE
        # destino, no todos los de la raiz: ahi puede vivir el N−1 del runtime.
        recogidos = promotion.limpiar_apartados_de(raiz, destino.name)

    return {"installed": len(esperado), "dir": str(destino),
            "recuperacion_previa": recuperado.get("accion"),
            "respaldos_recogidos": len(recogidos)}


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
    except (SchemaFetchError, promotion.PromocionError) as exc:
        print(f"FALLO: {exc}", file=sys.stderr)
        return 1
    print(f"{r['installed']} esquema(s) verificados e instalados en {r['dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
