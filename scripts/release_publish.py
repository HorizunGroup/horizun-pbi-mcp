"""RELEASE-004 — la GitHub Release, al final del DAG y con los bytes firmados.

El defecto que esto corrige es de omision, que es la clase que no se ve en
ninguna revision del YAML: `release.yml` construia, probaba, publicaba en PyPI
y publicaba en el MCP Registry, y **no creaba ninguna GitHub Release**. El
bloque de un pegado que ofrecen el README, `docs/INSTALL.md` y la skill
descarga `horizun-pbi-mcp-instalar.ps1` de
`releases/download/v<version>/...` — un asset que ningun job creaba nunca. El
intento de v2.0.0 lo dejo a la vista: el pipeline habria terminado en verde
-si PyPI hubiera estado configurado- con el one-paste apuntando a un 404.

Lo que se exige aqui, y por que cada cosa:

* **La release se crea la ultima.** `needs: [build, test, publicar-pypi,
  publicar-mcp]`. Una release publicada mientras PyPI falla es peor que ninguna:
  la gente la descarga y el `pip install` de dentro no encuentra el paquete.
* **Se publican exactamente los bytes verificados.** La lista de assets no se
  escribe a mano: sale de `SHA256SUMS`, o sea, de lo que se firmo en la unica
  construccion. Lo que no esta firmado no se publica, y lo que se firmo se
  publica entero.
* **Nada se reemplaza en silencio.** Si un asset ya existe con OTROS bytes, el
  script para. No borra y vuelve a subir: alguien pudo descargar ese archivo, y
  reescribirlo bajo el mismo nombre y el mismo tag es la forma exacta de que
  dos personas «instalen la misma version» y tengan cosas distintas.
* **Es idempotente.** Un rerun sobre una release ya completa la re-verifica
  descargando cada asset y termina en verde sin escribir nada.
* **Se comprueba DESPUES de subir.** Que el `PUT` no diera error no dice que lo
  que hay al otro lado sea lo que mandamos. Se descarga cada asset de vuelta y
  se compara su SHA-256, y del instalador se comprueba ademas que su
  `browser_download_url` es **exactamente** la URL que publica
  `scripts/downloads_manifest.json`: es la que la gente pega.

La logica vive en este script y no en pasos `run:` por la misma razon que
`release_build.py`: un workflow no se puede ejecutar en una maquina de
desarrollo, asi que un pipeline escrito en YAML solo se prueba publicando.
Aqui el flujo entero se prueba en local contra un cliente falso
(`tests/test_release_github.py`), incluidos los caminos que deben PARAR.

Uso:
    python scripts/release_publish.py --dir artefactos --tag v2.0.1 \
        --repo HorizunGroup/horizun-pbi-mcp
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import release_verify as rv                                      # noqa: E402

REPO = rv.REPO
API = "https://api.github.com"
SUBIDAS = "https://uploads.github.com"

#: Ni una release en borrador ni una prerelease: el one-paste apunta a la URL
#: de descarga, y en un borrador esa URL no existe para nadie de fuera.
BORRADOR = False
PRERELEASE = False


def _sha256_bytes(crudo: bytes) -> str:
    return hashlib.sha256(crudo).hexdigest()


def _sha256(ruta: Path) -> str:
    return _sha256_bytes(ruta.read_bytes())


def _normalizar(texto: str) -> str:
    """GitHub devuelve el cuerpo con CRLF aunque se envie con LF.

    Comparar los bytes tal cual haria fallar todo rerun con una diferencia que
    no existe. Se comparan los renglones, que es lo que de verdad se publico.
    """
    return texto.replace("\r\n", "\n").replace("\r", "\n").strip()


# ------------------------------------------------------------- el cliente ---
class ClienteGitHub:
    """Lo minimo de la API de releases, con `urllib` y sin dependencias.

    Se aisla en una clase porque asi las pruebas pueden inyectar uno falso y
    ejercitar el flujo completo -incluidos los caminos que deben parar- sin
    tocar la red ni crear releases de verdad.
    """

    def __init__(self, repo: str, token: str, *, api: str = API,
                 subidas: str = SUBIDAS, timeout: int = 120) -> None:
        self.repo = repo
        self.token = token
        self.api = api.rstrip("/")
        self.subidas = subidas.rstrip("/")
        self.timeout = timeout

    def _peticion(self, metodo: str, url: str, *, datos: bytes | None = None,
                  tipo: str | None = None,
                  aceptar: str = "application/vnd.github+json"):
        pedido = urllib.request.Request(url, data=datos, method=metodo)
        pedido.add_header("Authorization", f"Bearer {self.token}")
        pedido.add_header("Accept", aceptar)
        pedido.add_header("X-GitHub-Api-Version", "2022-11-28")
        pedido.add_header("User-Agent", "horizun-pbi-mcp-release")
        if tipo:
            pedido.add_header("Content-Type", tipo)
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _json(self, metodo: str, url: str, cuerpo: dict | None = None) -> dict:
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        codigo, crudo = self._peticion(
            metodo, url, datos=datos,
            tipo="application/json" if datos else None)
        if codigo >= 400:
            raise SystemExit(
                f"[release_publish] {metodo} {url} devolvio {codigo}: "
                f"{crudo[:800].decode('utf-8', 'replace')}")
        return json.loads(crudo) if crudo else {}

    def release_por_tag(self, tag: str) -> dict | None:
        url = f"{self.api}/repos/{self.repo}/releases/tags/{tag}"
        codigo, crudo = self._peticion("GET", url)
        if codigo == 404:
            return None
        if codigo >= 400:
            raise SystemExit(
                f"[release_publish] no se pudo consultar la release {tag}: "
                f"{codigo} {crudo[:800].decode('utf-8', 'replace')}")
        return json.loads(crudo)

    def crear_release(self, tag: str, nombre: str, cuerpo: str) -> dict:
        return self._json(
            "POST", f"{self.api}/repos/{self.repo}/releases",
            {"tag_name": tag, "name": nombre, "body": cuerpo,
             "draft": BORRADOR, "prerelease": PRERELEASE,
             # El tag ya existe y es inmutable: que la API no invente uno si
             # algun dia se llamara a esto con un tag que no se ha empujado.
             "make_latest": "true"})

    def assets(self, release: dict) -> list[dict]:
        url = (f"{self.api}/repos/{self.repo}/releases/"
               f"{release['id']}/assets?per_page=100")
        datos = self._json("GET", url)
        return list(datos) if isinstance(datos, list) else datos

    def subir_asset(self, release: dict, nombre: str, contenido: bytes) -> dict:
        plantilla = str(release.get("upload_url") or "")
        base = plantilla.split("{", 1)[0] or (
            f"{self.subidas}/repos/{self.repo}/releases/{release['id']}/assets")
        codigo, crudo = self._peticion(
            "POST", f"{base}?name={nombre}", datos=contenido,
            tipo="application/octet-stream")
        if codigo >= 400:
            raise SystemExit(
                f"[release_publish] no se pudo subir {nombre}: {codigo} "
                f"{crudo[:800].decode('utf-8', 'replace')}")
        return json.loads(crudo) if crudo else {}

    def descargar_asset(self, asset: dict) -> bytes:
        url = f"{self.api}/repos/{self.repo}/releases/assets/{asset['id']}"
        codigo, crudo = self._peticion(
            "GET", url, aceptar="application/octet-stream")
        if codigo >= 400:
            raise SystemExit(
                f"[release_publish] no se pudo releer {asset.get('name')}: "
                f"{codigo}")
        return crudo


# --------------------------------------------------------------- el flujo ---
def _entrada_instalador() -> dict:
    return json.loads(
        (REPO / "scripts" / "downloads_manifest.json").read_text(encoding="utf-8")
    )["downloads"]["instalar.ps1"]


def cuerpo_de_la_release(raiz: Path, tag: str) -> str:
    """Las notas de la version, tomadas del artefacto FIRMADO.

    No del checkout: el cuerpo de la release es lo primero que lee una persona
    y tiene que salir de la misma cadena de digests que todo lo demas.
    """
    version = tag.lstrip("v")
    nombre = f"RELEASE_NOTES_{version}.md"
    ruta = raiz / "meta" / nombre
    if not ruta.is_file():
        raise SystemExit(
            f"[release_publish] el artefacto no trae {nombre}; lo genera "
            "scripts/release_build.py y va firmado en SHA256SUMS")
    return ruta.read_text(encoding="utf-8")


def publicar(raiz: Path, tag: str, cliente) -> dict:
    """Crea o re-verifica la release de `tag`. Nunca reemplaza nada."""
    # 1. Antes de mirar el remoto: el artefacto local tiene que ser el que se
    #    construyo, y el arbol tiene que declarar la version del tag.
    rv.verificar_digests(raiz)
    rv.verificar_instalador(raiz)
    rv.verificar_version(tag)

    assets = rv.assets_publicables(raiz)
    esperados = {nombre: _sha256(ruta) for nombre, ruta in assets.items()}
    cuerpo = cuerpo_de_la_release(raiz, tag)

    # 2. La release. Si ya existe, no se toca: se comprueba que es la misma.
    release = cliente.release_por_tag(tag)
    creada = False
    if release is None:
        release = cliente.crear_release(tag, tag, cuerpo)
        creada = True
    else:
        if release.get("draft"):
            raise SystemExit(
                f"[release_publish] la release {tag} existe como BORRADOR. Un "
                "borrador no sirve el asset a nadie de fuera y el one-paste "
                "seguiria roto. Resuelvelo a mano antes de volver a correr esto")
        if release.get("prerelease"):
            raise SystemExit(
                f"[release_publish] la release {tag} existe como PRERELEASE")
        if _normalizar(str(release.get("body") or "")) != _normalizar(cuerpo):
            raise SystemExit(
                f"[release_publish] la release {tag} ya existe con OTRAS notas. "
                "No se reescribe: o alguien la edito a mano, o este artefacto "
                "no es el de esa release")

    # 3. Los assets, uno a uno. Subir el que falta; verificar el que esta;
    #    PARAR ante el que esta y es otro.
    acciones: dict[str, str] = {}
    presentes = {a["name"]: a for a in cliente.assets(release)}
    for nombre in sorted(assets):
        actual = presentes.get(nombre)
        if actual is None:
            cliente.subir_asset(release, nombre, assets[nombre].read_bytes())
            acciones[nombre] = "subido"
            continue
        estado = str(actual.get("state") or "uploaded")
        if estado != "uploaded":
            raise SystemExit(
                f"[release_publish] el asset {nombre} esta en estado {estado!r}: "
                "una subida a medias. No se pisa; borralo a mano y repite")
        real = _sha256_bytes(cliente.descargar_asset(actual))
        if real != esperados[nombre]:
            raise SystemExit(
                f"[release_publish] {nombre} ya existe en {tag} con OTROS bytes.\n"
                f"  firmado: {esperados[nombre]}\n  remoto:  {real}\n"
                "  No se reemplaza: alguien pudo haberlo descargado ya. Un tag "
                "publicado es inmutable; si hay que corregir, se corrige en una "
                "version nueva")
        acciones[nombre] = "ya estaba, identico"

    # 4. DESPUES de subir. Que ningun `POST` diera error no dice que lo que hay
    #    al otro lado sea lo que mandamos.
    finales = {a["name"]: a for a in cliente.assets(release)}
    faltan = sorted(set(assets) - set(finales))
    if faltan:
        raise SystemExit(
            f"[release_publish] tras subir, la release {tag} no tiene {faltan}")
    sobran = sorted(set(finales) - set(assets))
    if sobran:
        raise SystemExit(
            f"[release_publish] la release {tag} lleva assets que nadie firmo: "
            f"{sobran}. Un archivo publicable fuera de SHA256SUMS es por donde "
            "entra lo que la suite no vio")

    for nombre, asset in sorted(finales.items()):
        real = _sha256_bytes(cliente.descargar_asset(asset))
        if real != esperados[nombre]:
            raise SystemExit(
                f"[release_publish] {nombre} quedo publicado con otro digest.\n"
                f"  firmado: {esperados[nombre]}\n  remoto:  {real}")

    # 5. Y el que de verdad importa: el instalador que la gente pega. Su hash y
    #    su URL tienen que ser EXACTAMENTE los del manifest, que es de donde
    #    salen el README, docs/INSTALL.md, la skill y el bloque de un pegado.
    entrada = _entrada_instalador()
    instalador = finales.get(entrada["name"])
    if instalador is None:
        raise SystemExit(
            f"[release_publish] la release no publica {entrada['name']}: el "
            "one-paste apuntaria a un 404, que es el defecto que este job existe "
            "para cerrar")
    crudo = cliente.descargar_asset(instalador)
    real = _sha256_bytes(crudo)
    if real != entrada["sha256"]:
        raise SystemExit(
            f"[release_publish] el instalador publicado NO es el del manifest.\n"
            f"  manifest: {entrada['sha256']}\n  remoto:   {real}")
    if len(crudo) != entrada["actual_bytes"]:
        raise SystemExit(
            "[release_publish] el instalador publicado no tiene el tamano "
            f"declarado: {len(crudo)} vs {entrada['actual_bytes']}")
    url = str(instalador.get("browser_download_url") or "")
    if url != entrada["url"]:
        raise SystemExit(
            "[release_publish] la URL publicada no es la que pega la gente.\n"
            f"  manifest: {entrada['url']}\n  release:  {url}")

    return {
        "tag": tag,
        "release_creada": creada,
        "url": release.get("html_url"),
        "draft": bool(release.get("draft")),
        "assets": acciones,
        "instalador": {"sha256": real, "url": url},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--repo", required=True,
                    help="owner/nombre, como github.repository")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("[release_publish] falta GITHUB_TOKEN en el entorno")

    resumen = publicar(args.dir.resolve(), args.tag,
                       ClienteGitHub(args.repo, token))
    print(json.dumps(resumen, indent=2, ensure_ascii=False))
    print("\n[release_publish] OK: la release publica exactamente los bytes "
          "firmados, y el instalador remoto coincide con el manifest.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
