"""RELEASE-001 — construir UNA vez, y que todo lo demas consuma esos bytes.

El defecto que esto corrige: `ci.yml` probaba en Windows y `publish-pypi.yml`
**volvia a construir** en Ubuntu y publicaba *eso*. Los bytes que la suite
aprobo y los bytes que llegaron a PyPI eran artefactos distintos, construidos en
maquinas distintas, y nada comparaba sus digests. La suite en verde no decia
nada sobre lo publicado; solo lo parecia.

La logica vive AQUI y no en YAML a proposito. Un workflow no se puede ejecutar
en una maquina de desarrollo, asi que un pipeline escrito en pasos `run:` solo
se prueba publicando. Como script, el flujo completo -construir, verificar,
firmar con hashes, generar el SBOM, congelar el asset del instalador- se corre
en local, se le miran las manos y se comprueba con pruebas normales.

Salida (todo bajo --outdir):

    dist/                  el wheel y el sdist, y NADA mas: es lo que sube a
                           PyPI tal cual, sin colar metadatos en el paquete
    meta/SHA256SUMS        digest de cada archivo publicable
    meta/sbom.cdx.json     CycloneDX del entorno resuelto del wheel
    meta/horizun-pbi-mcp-instalar.ps1
                           copia byte a byte de scripts/instalar.ps1, con su
                           SHA-256 comprobado contra downloads_manifest.json
    meta/horizun-pbi-mcp-<version>.mcpb
                           bundle local de Claude Desktop, reproducible y
                           construido solo desde el arbol Git confirmado
    meta/RELEASE_NOTES_<version>.md
    meta/MIGRACION_1x_A_2.0.md
                           las notas que acompanan a la release, COPIADAS AQUI
                           y firmadas en SHA256SUMS. Publicarlas desde el
                           checkout las dejaria fuera de la cadena de digests:
                           serian los unicos bytes de la release que nadie
                           verifico
    meta/build.json        resumen legible de la construccion

Uso:
    python scripts/release_build.py --outdir artefactos
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import build_mcpb

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "scripts" / "downloads_manifest.json"
INSTALADOR = REPO / "scripts" / "instalar.ps1"

#: Nombre del asset en la release. Debe coincidir con el del manifest.
NOMBRE_ASSET = "horizun-pbi-mcp-instalar.ps1"
NOMBRE_MCPB = "horizun-pbi-mcp-{version}.mcpb"

#: Las notas que viajan CON la release. `{version}` se sustituye por la que
#: declara pyproject. Se copian al artefacto en vez de publicarse desde el
#: checkout a proposito: lo que no esta en SHA256SUMS no lo verifica nadie, y
#: unas notas de migracion son exactamente el archivo que alguien lee para
#: decidir como adaptar su codigo.
NOTAS = ("RELEASE_NOTES_{version}.md", "docs/MIGRACION_1x_A_2.0.md")

#: Herramientas de construccion, acotadas. Un salto mayor de cualquiera de
#: ellas puede cambiar el artefacto resultante sin que nadie lo pida.
HERRAMIENTAS = ["build>=1.2,<2", "twine>=5,<7", "cyclonedx-bom>=7.3,<8"]


def _correr(args, *, que: str, cwd=None, timeout=3600) -> subprocess.CompletedProcess:
    res = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                         text=True, timeout=timeout, encoding="utf-8",
                         errors="replace")
    if res.returncode != 0:
        raise SystemExit(
            f"[release_build] {que} fallo con codigo {res.returncode}\n"
            f"  comando: {' '.join(str(a) for a in args)}\n"
            f"--- stdout ---\n{(res.stdout or '')[-4000:]}\n"
            f"--- stderr ---\n{(res.stderr or '')[-4000:]}")
    return res


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def _scripts(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _python_de(venv: Path) -> Path:
    return _scripts(venv) / ("python.exe" if os.name == "nt" else "python")


def _crear_venv(destino: Path, que: str) -> Path:
    """Venv LIMPIO: sin --system-site-packages, o el SBOM mentiria."""
    _correr([sys.executable, "-m", "venv", destino], que=f"crear el venv de {que}")
    py = _python_de(destino)
    _correr([py, "-m", "pip", "install", "--upgrade", "-q", "pip"],
            que=f"actualizar pip en el venv de {que}")
    return py


def _version_declarada() -> str:
    import re
    texto = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)


def verificar_version_del_bundle(resumen: dict, version: str, nombre: str) -> None:
    """El bundle sale de HEAD; su NOMBRE, del arbol de trabajo. Que coincidan.

    Son dos fuentes distintas, y con la version subida pero sin confirmar
    divergen EN SILENCIO: `build_mcpb` lee `pyproject.toml` de HEAD -por diseño,
    para que un arbol sucio no pueda colar nada- mientras el nombre del asset se
    formatea con la version del disco. El resultado seria publicar
    `...-2.1.1.mcpb` con un manifest que dice 2.1.0.

    Paso preparando la 2.1.1 y no lo detuvo nadie: solo se vio leyendo el JSON
    de salida a mano. Aqui para la construccion.
    """
    declarada = resumen.get("version")
    if declarada != version:
        raise SystemExit(
            f"[release_build] {nombre} declara la version {declarada} y la "
            f"release es {version}. El bundle se construye desde HEAD y el "
            "nombre del asset sale del arbol de trabajo: confirma el cambio de "
            "version antes de construir la release.")


def construir(outdir: Path) -> dict:
    dist = outdir / "dist"
    meta = outdir / "meta"
    trabajo = outdir / ".trabajo"
    for d in (dist, meta, trabajo):
        d.mkdir(parents=True, exist_ok=True)

    version = _version_declarada()

    # 1. UNA construccion. `python -m build` saca el sdist y despues compila el
    #    wheel A PARTIR del sdist: si algo falta en el tarball, el wheel que
    #    sale de el lo pierde tambien, y se ve aqui.
    py_build = _crear_venv(trabajo / "venv_build", "construccion")
    _correr([py_build, "-m", "pip", "install", "-q", *HERRAMIENTAS],
            que="instalar las herramientas de construccion")
    _correr([py_build, "-m", "build", "--outdir", dist, REPO], cwd=REPO,
            que="construir wheel y sdist")

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit(
            f"[release_build] se esperaba un wheel y un sdist; hay "
            f"{[w.name for w in wheels]} y {[s.name for s in sdists]}")
    sobrantes = [p.name for p in dist.iterdir()
                 if p not in (wheels[0], sdists[0])]
    if sobrantes:
        raise SystemExit(
            f"[release_build] dist/ lleva archivos que no se publican: {sobrantes}")

    # 2. twine check antes de que nadie mas los toque.
    _correr([py_build, "-m", "twine", "check", "--strict", dist / "*"],
            que="twine check --strict")

    # 3. El SBOM describe el entorno que resuelve ESTE wheel, no el del
    #    desarrollador. Por eso se instala de verdad, con dependencias.
    venv_sbom = trabajo / "venv_sbom"
    py_sbom = _crear_venv(venv_sbom, "SBOM")
    _correr([py_sbom, "-m", "pip", "install", "-q", wheels[0]],
            que="instalar el wheel con sus dependencias resueltas")
    sbom = meta / "sbom.cdx.json"
    _correr([_scripts(trabajo / "venv_build") / "cyclonedx-py", "environment",
             py_sbom, "--of", "JSON", "--sv", "1.6", "-o", sbom,
             "--pyproject", REPO / "pyproject.toml",
             # Sin esto el SBOM lleva marca de tiempo y un serialNumber
             # aleatorio: dos construcciones de los mismos bytes darian
             # documentos distintos y el digest dejaria de significar nada.
             "--output-reproducible"],
            que="generar el SBOM CycloneDX")
    componentes = json.loads(sbom.read_text(encoding="utf-8")).get("components", [])
    if len(componentes) < 5:
        raise SystemExit(
            f"[release_build] el SBOM solo lista {len(componentes)} componentes; "
            "algo fallo en la resolucion")

    # 4. El asset del instalador: copia byte a byte y hash comprobado contra el
    #    manifest. Aqui es donde se garantiza que lo que se publica bajo ese
    #    nombre es exactamente lo que las pruebas y el one-paste esperan.
    entrada = json.loads(MANIFEST.read_text(encoding="utf-8"))["downloads"]["instalar.ps1"]
    asset = meta / NOMBRE_ASSET
    shutil.copyfile(INSTALADOR, asset)
    real = _sha256(asset)
    if real != entrada["sha256"]:
        raise SystemExit(
            f"[release_build] el instalador no coincide con el manifest.\n"
            f"  manifest: {entrada['sha256']}\n  real:     {real}\n"
            "  Si instalar.ps1 cambio, recalcula el hash y actualizalo en "
            "downloads_manifest.json y en scripts/one_paste.ps1.")
    if asset.stat().st_size != entrada["actual_bytes"]:
        raise SystemExit("[release_build] el tamano del instalador no coincide "
                         "con el manifest")
    if entrada["name"] != NOMBRE_ASSET:
        raise SystemExit(f"[release_build] el manifest llama al asset "
                         f"{entrada['name']!r} y aqui se publica {NOMBRE_ASSET!r}")

    # 5. Claude Desktop: un bundle local instalable con doble clic. Se
    # construye desde HEAD, no desde el arbol de trabajo, para que una maquina
    # con outputs, backups o proyectos reales sin versionar no pueda colarlos
    # en el asset por accidente.
    mcpb = meta / NOMBRE_MCPB.format(version=version)
    resumen_mcpb = build_mcpb.build(mcpb, repo=REPO, ref="HEAD")
    verificar_version_del_bundle(resumen_mcpb, version, mcpb.name)

    # 6. Las notas. Se copian con el nombre con el que se publican y entran en
    #    SHA256SUMS como cualquier otro publicable. Si falta una, se para aqui:
    #    una release sin notas de migracion en una MAYOR es la que obliga a
    #    leerse el diff para saber que se rompio.
    notas = []
    for plantilla in NOTAS:
        origen = REPO / plantilla.format(version=version)
        if not origen.is_file():
            raise SystemExit(
                f"[release_build] falta {origen.relative_to(REPO).as_posix()}, "
                "que se publica como asset de la release")
        destino = meta / origen.name
        shutil.copyfile(origen, destino)
        notas.append(destino)

    # 7. SHA256SUMS sobre TODO lo publicable. Es lo que cada job consumidor
    #    comprueba antes de usar nada.
    publicables = [wheels[0], sdists[0], asset, mcpb, sbom, *notas]
    lineas = []
    for p in publicables:
        rel = p.relative_to(outdir).as_posix()
        lineas.append(f"{_sha256(p)}  {rel}")
    (meta / "SHA256SUMS").write_text("\n".join(lineas) + "\n",
                                     encoding="ascii", newline="\n")

    resumen = {
        "version": version,
        "wheel": wheels[0].name,
        "sdist": sdists[0].name,
        "asset_instalador": NOMBRE_ASSET,
        "asset_claude_desktop": resumen_mcpb,
        "notas": [n.name for n in notas],
        "sbom_componentes": len(componentes),
        "digests": {p.relative_to(outdir).as_posix(): _sha256(p)
                    for p in publicables},
    }
    (meta / "build.json").write_text(json.dumps(resumen, indent=2) + "\n",
                                     encoding="utf-8", newline="\n")
    return resumen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--outdir", required=True, type=Path)
    args = ap.parse_args(argv)

    resumen = construir(args.outdir.resolve())
    print(json.dumps(resumen, indent=2))
    print("\n[release_build] OK: una sola construccion, verificada y con SBOM.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
