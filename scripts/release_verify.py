"""RELEASE-001/002 — nadie usa un artefacto que no haya verificado antes.

Todo job que consume el artefacto -probarlo, instalarlo, publicarlo- llama aqui
primero. Si `SHA256SUMS` no cuadra, o falta un archivo, o sobra uno que nadie
declaro, el job se para antes de tocar nada.

Con `--expect-version` comprueba ademas que el tag y **todos** los manifiestos
declaran el mismo numero. Es la guarda que impide publicar 2.0.0 desde un arbol
que dice 1.5.4 en tres archivos y 2.0.0 en otros dos.

Uso:
    python scripts/release_verify.py --dir artefactos
    python scripts/release_verify.py --dir artefactos --expect-version 2.0.0
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Lo unico que puede vivir en `meta/` sin estar firmado en SHA256SUMS. El
#: propio archivo de sumas no se puede declarar a si mismo, y `build.json` es
#: un resumen legible que NO se publica. Cualquier otra cosa ahi es un archivo
#: publicable que nadie firmo.
META_NO_FIRMADOS = {"meta/SHA256SUMS", "meta/build.json"}

#: Los sitios que declaran la version del producto. Si uno se queda atras, el
#: marketplace instala una cosa, PyPI otra y el registro MCP una tercera.
SITIOS_DE_VERSION = {
    "pyproject.toml": lambda t: re.search(r'^version = "([^"]+)"', t, re.M).group(1),
    "src/horizun_pbi_mcp/branding.py": lambda t: re.search(r'^VERSION\s*=\s*"([^"]+)"', t, re.M).group(1),
    ".mcp/server.json": lambda t: json.loads(t)["version"],
    ".claude-plugin/plugin.json": lambda t: json.loads(t)["version"],
    ".codex-plugin/plugin.json": lambda t: json.loads(t)["version"],
    ".claude-plugin/marketplace.json": lambda t: _ref(t),
    ".agents/plugins/marketplace.json": lambda t: _ref(t),
    "packaging/claude-desktop/manifest.json": lambda t: json.loads(t)["version"],
    "scripts/downloads_manifest.json": lambda t: json.loads(t)["downloads"]["instalar.ps1"]["version"],
    # El launcher del plugin decide con EL SUYO si
    # el runtime instalado sirve o hay que reinstalar. Quedandose atras, un
    # plugin recien actualizado seguiria dando por buena la version anterior.
    # Se colaba porque solo lo miraba una prueba de empaquetado, no el gate.
    "scripts/plugin_bootstrap.py": lambda t: re.search(r'^VERSION = "([^"]+)"', t, re.M).group(1),
}


# --------------------------------------------- el instalador y su firma ---
#: Marcas del bloque Authenticode que anade `signtool` / `Set-AuthenticodeSignature`
#: al FINAL de un .ps1. Lo que hay antes es el cuerpo que se firmo.
MARCA_FIRMA_INICIO = b"# SIG # Begin signature block"
MARCA_FIRMA_FIN = b"# SIG # End signature block"

#: Huella del certificado Certum de HORIZUN GROUP S.A.S. con el que se firma.
HUELLA_CERTUM = "D19F95C700EFEB78F03B6A2F9F6FBF87BF9BF8FA"

AYUDA_FIRMA = (
    "El instalador scripts/instalar.ps1 se firma EN EL ARBOL, antes de etiquetar, "
    "y sus pines se recalculan DESPUES de firmar. Procedimiento exacto: "
    "RELEASING.md, seccion 'Firma del instalador'.")


class InstaladorInvalido(ValueError):
    """El .ps1 no tiene la forma que exige el pipeline."""


def analizar_instalador(crudo: bytes) -> dict:
    """Separa cuerpo y bloque de firma, y valida la FORMA de cada parte.

    Contrato (el mismo que declara `downloads_manifest.json`):

    * sin BOM;
    * el CUERPO -todo lo anterior al bloque de firma- es ASCII puro y con LF:
      PowerShell 5.1 lee un .ps1 sin BOM con la codepage OEM, y un CR suelto
      cambiaria el hash que se publica;
    * el BLOQUE (`# SIG # Begin signature block` ... `# SIG # End signature
      block`) es ASCII, va al final del archivo y cada linea empieza por `# `.
      El bloque lo escribe signtool y trae CRLF; signtool ademas intercala un
      CRLF entre el cuerpo y el bloque. Son los UNICOS CR que se admiten.

    Devuelve `{"firmado": bool, "cuerpo": bytes, "bloque": bytes | None}`.
    Un archivo SIN firmar es valido en cuanto a forma (`firmado: False`); quien
    exige la firma llama a `exigir_firmado`.
    """
    if crudo.startswith(b"\xef\xbb\xbf"):
        raise InstaladorInvalido("el instalador lleva BOM")
    cuenta = crudo.count(MARCA_FIRMA_INICIO)
    if cuenta > 1:
        raise InstaladorInvalido("el instalador tiene mas de un bloque de firma")
    if cuenta == 0:
        cuerpo, bloque = crudo, None
    else:
        i = crudo.index(MARCA_FIRMA_INICIO)
        if i != 0 and crudo[i - 1:i] != b"\n":
            raise InstaladorInvalido(
                "la marca de firma no abre una linea del instalador")
        cuerpo, bloque = crudo[:i], crudo[i:]
        lineas = bloque.replace(b"\r\n", b"\n").split(b"\n")
        while lineas and lineas[-1] == b"":
            lineas.pop()
        if lineas[-1] != MARCA_FIRMA_FIN:
            raise InstaladorInvalido(
                "el bloque de firma no termina en '# SIG # End signature block': "
                "hay bytes detras de la firma o la firma esta cortada")
        malas = [l[:40] for l in lineas if not l.startswith(b"# ")]
        if malas:
            raise InstaladorInvalido(
                f"lineas del bloque de firma sin el prefijo '# ': {malas[:3]}")
        if any(b > 127 for b in bloque):
            raise InstaladorInvalido("el bloque de firma no es ASCII")
    # signtool intercala un unico CRLF entre el cuerpo y el bloque de firma
    # (una linea en blanco, que ya forma parte de lo firmado). Es el unico CR
    # que admite el cuerpo; cualquier otro cambiaria el hash al descargar.
    sin_marco = (cuerpo[:-2] if bloque is not None and cuerpo.endswith(b"\r\n")
                 else cuerpo)
    if b"\r" in sin_marco:
        raise InstaladorInvalido(
            "el CUERPO del instalador tiene CR (CRLF): solo el bloque de firma "
            "(y el CRLF que signtool intercala justo antes) admite CRLF; el "
            "cuerpo es LF puro")
    if any(b > 127 for b in cuerpo):
        raise InstaladorInvalido("el cuerpo del instalador no es ASCII")
    return {"firmado": bloque is not None, "cuerpo": cuerpo, "bloque": bloque}


def exigir_firmado(crudo: bytes, nombre: str = "scripts/instalar.ps1") -> dict:
    """Falla de forma EXPLICITA si el instalador no lleva el bloque de firma."""
    try:
        info = analizar_instalador(crudo)
    except InstaladorInvalido as exc:
        raise SystemExit(f"[release_verify] {nombre} no es valido: {exc}")
    if not info["firmado"]:
        raise SystemExit(
            f"[release_verify] {nombre} NO ESTA FIRMADO (no hay '# SIG # Begin "
            "signature block'). Un instalador sin firma Authenticode no se "
            f"construye ni se publica. {AYUDA_FIRMA}")
    return info


def verificar_authenticode(ruta: Path) -> dict:
    """Pregunta a Windows si la firma es valida y es la de HORIZUN GROUP.

    `Status = Valid` quiere decir que el hash del cuerpo coincide con la firma
    y que la cadena llega a una raiz de confianza; ademas se exige la huella de
    la empresa y el sello de tiempo, que es lo que mantiene valida la firma
    cuando el certificado caduque. Solo Windows: en otro sistema no hay
    `Get-AuthenticodeSignature` y se para en vez de dar la firma por buena.
    """
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if not ps:
        raise SystemExit("[release_verify] no hay PowerShell para comprobar la "
                         "firma Authenticode; esta comprobacion es de Windows")
    orden = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:HORIZUN_VERIFICAR_RUTA; "
        "[pscustomobject]@{ status = [string]$s.Status; "
        "thumbprint = [string]$s.SignerCertificate.Thumbprint; "
        "subject = [string]$s.SignerCertificate.Subject; "
        "issuer = [string]$s.SignerCertificate.Issuer; "
        "timestamp = [string]$s.TimeStamperCertificate.Subject } "
        "| ConvertTo-Json -Compress")
    # La ruta viaja por el entorno: con `-Command` los argumentos sobrantes se
    # pegan al texto del comando y no llegan a `$args`.
    entorno = dict(os.environ, HORIZUN_VERIFICAR_RUTA=str(ruta))
    res = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", orden],
                         capture_output=True, text=True, timeout=120,
                         encoding="utf-8", errors="replace", env=entorno)
    if res.returncode != 0 or not res.stdout.strip():
        raise SystemExit("[release_verify] Get-AuthenticodeSignature fallo: "
                         + (res.stderr or res.stdout)[-600:])
    datos = json.loads(res.stdout.strip().splitlines()[-1])
    problemas = []
    if datos["status"] != "Valid":
        problemas.append(f"estado de la firma: {datos['status']} (se exige Valid)")
    if datos["thumbprint"].upper() != HUELLA_CERTUM:
        problemas.append(f"firmado por la huella {datos['thumbprint'] or '(ninguna)'}, "
                         f"no por {HUELLA_CERTUM}")
    if "Certum" not in datos["issuer"]:
        problemas.append(f"el emisor no es Certum: {datos['issuer']}")
    if "Certum" not in datos["timestamp"]:
        problemas.append("sin sello de tiempo de Certum")
    if problemas:
        raise SystemExit(
            f"[release_verify] la firma Authenticode de {ruta.name} NO es valida:"
            "\n  - " + "\n  - ".join(problemas))
    return datos


def _ref(texto: str) -> str:
    refs = set(re.findall(r'"ref"\s*:\s*"v([^"]+)"', texto))
    if len(refs) != 1:
        raise SystemExit(f"[release_verify] el marketplace declara refs {refs}")
    return refs.pop()


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar_digests(raiz: Path) -> list:
    sumas = raiz / "meta" / "SHA256SUMS"
    if not sumas.exists():
        raise SystemExit(f"[release_verify] no hay {sumas}: el artefacto no "
                         "viene de scripts/release_build.py")

    declarados, problemas = {}, []
    for linea in sumas.read_text(encoding="ascii").splitlines():
        if not linea.strip():
            continue
        digest, _, rel = linea.partition("  ")
        declarados[rel] = digest

    for rel, esperado in declarados.items():
        p = raiz / rel
        if not p.is_file():
            problemas.append(f"declarado y ausente: {rel}")
            continue
        real = _sha256(p)
        if real != esperado:
            problemas.append(f"digest distinto en {rel}: "
                             f"esperado {esperado}, real {real}")

    # Un archivo publicable que NADIE declaro es tan grave como uno cambiado:
    # es exactamente por donde entraria algo que la suite no vio. Se mira
    # `dist/` -lo que sube a PyPI- y tambien `meta/` -lo que sube como asset de
    # la GitHub Release-, porque los dos son destinos publicos.
    for carpeta in ("dist", "meta"):
        for p in sorted((raiz / carpeta).glob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(raiz).as_posix()
            if rel not in declarados and rel not in META_NO_FIRMADOS:
                problemas.append(
                    f"en {carpeta}/ y sin declarar en SHA256SUMS: {rel}")

    if problemas:
        raise SystemExit("[release_verify] artefacto NO verificado:\n  - "
                         + "\n  - ".join(problemas))
    return sorted(declarados)


def assets_publicables(raiz: Path) -> dict:
    """`{nombre del asset: ruta}` — la lista EXACTA que sube a la release.

    Se deriva de `SHA256SUMS` en vez de escribirse a mano: asi «lo que se
    publica» y «lo que esta firmado» no son dos listas que puedan divergir,
    sino la misma. `SHA256SUMS` se publica ademas de firmar, porque quien
    descargue un asset suelto necesita con que compararlo; es el unico que no
    puede declararse a si mismo.
    """
    sumas = raiz / "meta" / "SHA256SUMS"
    salida: dict[str, Path] = {}
    for linea in sumas.read_text(encoding="ascii").splitlines():
        if not linea.strip():
            continue
        _, _, rel = linea.partition("  ")
        ruta = raiz / rel
        nombre = ruta.name
        if nombre in salida:
            raise SystemExit(
                f"[release_verify] dos publicables se llamarian igual como "
                f"asset: {nombre}. Una release no admite nombres repetidos y "
                "uno taparia al otro")
        salida[nombre] = ruta
    salida[sumas.name] = sumas
    return salida


def verificar_instalador(raiz: Path) -> str:
    """El asset de la release tiene que ser el archivo congelado, byte a byte.

    «Congelado» es el archivo FIRMADO: el hash del manifest es el del `.ps1`
    con su bloque de firma. Un instalador sin firmar se rechaza aqui, antes de
    mirar el hash, para que el mensaje diga la causa y no solo «no coincide».
    """
    entrada = json.loads(
        (REPO / "scripts" / "downloads_manifest.json").read_text(encoding="utf-8")
    )["downloads"]["instalar.ps1"]
    asset = raiz / "meta" / entrada["name"]
    if not asset.exists():
        raise SystemExit(f"[release_verify] falta el asset {entrada['name']}")

    crudo = asset.read_bytes()
    exigir_firmado(crudo, entrada["name"])

    real = _sha256(asset)
    if real != entrada["sha256"]:
        raise SystemExit(
            f"[release_verify] el asset del instalador no es el congelado.\n"
            f"  manifest: {entrada['sha256']}\n  real:     {real}\n"
            "  Si el instalador se firmo o cambio, recalcula los pines: "
            "python scripts/pin_instalador.py")
    if len(crudo) != entrada["actual_bytes"]:
        raise SystemExit("[release_verify] el asset no tiene el tamano declarado")
    return real


def verificar_version(esperada: str) -> dict:
    esperada = esperada.lstrip("v")
    leidas, discrepan = {}, []
    for rel, extraer in SITIOS_DE_VERSION.items():
        texto = (REPO / rel).read_text(encoding="utf-8")
        leidas[rel] = extraer(texto)
        if leidas[rel] != esperada:
            discrepan.append(f"{rel} dice {leidas[rel]}")
    if discrepan:
        raise SystemExit(
            f"[release_verify] se publica {esperada} y no todos lo declaran:\n  - "
            + "\n  - ".join(discrepan))
    return leidas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--expect-version", default=None)
    ap.add_argument("--authenticode", action="store_true",
                    help="ademas, Windows comprueba la firma del instalador "
                         "(estado Valid, huella y sello de Certum)")
    args = ap.parse_args(argv)

    raiz = args.dir.resolve()
    archivos = verificar_digests(raiz)
    digest_instalador = verificar_instalador(raiz)
    print("[release_verify] digests OK:")
    for a in archivos:
        print(f"  - {a}")
    print(f"[release_verify] asset del instalador OK (firmado): {digest_instalador}")
    if args.authenticode:
        nombre = json.loads((REPO / "scripts" / "downloads_manifest.json").read_text(
            encoding="utf-8"))["downloads"]["instalar.ps1"]["name"]
        firma = verificar_authenticode(raiz / "meta" / nombre)
        print(f"[release_verify] firma Authenticode Valid: {firma['subject']}")

    if args.expect_version:
        leidas = verificar_version(args.expect_version)
        print(f"[release_verify] version {args.expect_version} declarada en "
              f"los {len(leidas)} sitios.")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
