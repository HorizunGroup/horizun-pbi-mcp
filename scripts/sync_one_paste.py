"""Propaga `scripts/one_paste.ps1` a los documentos que lo ofrecen.

El bloque de un pegado vive en TRES sitios: el canonico y las dos copias que
lee una persona. Mantenerlas a mano garantiza que un dia diverjan, y la que se
quede atras sera precisamente la que alguien copie -nadie pega desde
`scripts/`-. `tests/test_one_paste.py` prohibe la divergencia; este script es
la forma de repararla sin editar cuatro veces lo mismo.

    python scripts/sync_one_paste.py            # reescribe las copias
    python scripts/sync_one_paste.py --check    # solo informa (para el CI)

Un bloque se reconoce por el `User-Agent` del propio bloque, que no aparece en
ningun otro sitio del repositorio. Reconocerlo por el lenguaje de la valla o
por la primera linea acabaria pisando cualquier otro ejemplo de PowerShell.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CANONICO = RAIZ / "scripts" / "one_paste.ps1"

#: Los mismos que vigila tests/test_one_paste.py. Los historicos no se tocan.
DOCUMENTOS = ("docs/INSTALL.md", "skills/horizun-pbi-setup/SKILL.md")

#: Marca inequivoca del bloque: solo aparece dentro de el.
HUELLA = "horizun-pbi-mcp-one-paste"

_VALLA = re.compile(r"(^```powershell\n)(.*?)(^```)", re.M | re.S)


def snippet_canonico() -> str:
    """El cuerpo ejecutable: el archivo canonico sin su cabecera de comentarios."""
    lineas = CANONICO.read_text(encoding="ascii").splitlines()
    i = next(n for n, l in enumerate(lineas) if l.strip() and not l.startswith("#"))
    return "\n".join(lineas[i:]).rstrip("\n")


def sincronizar(texto: str, canonico: str) -> tuple[str, int]:
    """Devuelve (texto nuevo, cuantos bloques se reescribieron)."""
    cambiados = 0

    def _reemplazar(m: re.Match) -> str:
        nonlocal cambiados
        cuerpo = m.group(2)
        if HUELLA not in cuerpo:
            return m.group(0)
        nuevo = canonico + "\n"
        if cuerpo != nuevo:
            cambiados += 1
        return m.group(1) + nuevo + m.group(3)

    return _VALLA.sub(_reemplazar, texto), cambiados


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--check", action="store_true",
                   help="no escribe; devuelve 1 si algun documento diverge")
    args = p.parse_args()

    canonico = snippet_canonico()
    divergentes: list[str] = []
    for rel in DOCUMENTOS:
        ruta = RAIZ / rel
        texto = ruta.read_text(encoding="utf-8")
        nuevo, cambiados = sincronizar(texto, canonico)
        if HUELLA not in texto:
            print(f"AVISO: {rel} ya no lleva el bloque de un pegado.", file=sys.stderr)
            divergentes.append(rel)
            continue
        if not cambiados:
            print(f"  {rel}: al dia")
            continue
        divergentes.append(rel)
        if args.check:
            print(f"  {rel}: DIVERGE en {cambiados} bloque(s)")
            continue
        # Se escribe con newline='' para no convertir los LF en CRLF en Windows:
        # el bloque tiene que llegar al portapapeles tal cual se publico.
        ruta.write_text(nuevo, encoding="utf-8", newline="")
        print(f"  {rel}: {cambiados} bloque(s) reescrito(s)")

    if args.check and divergentes:
        print("Ejecuta: python scripts/sync_one_paste.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
