"""Sintaxis que el Python minimo declarado no entiende.

`pyproject.toml` dice `requires-python = ">=3.10"`, pero la suite corre con el
interprete que haya en la maquina. Escribiendo en 3.14 se cuela sin ruido
sintaxis de 3.12: **comillas del mismo tipo anidadas dentro de un f-string**
(PEP 701). En 3.10 eso no es un fallo en tiempo de ejecucion sino un
`SyntaxError` **al importar**, asi que no rompe un test: rompe la recoleccion
entera y el servidor no arranca.

Paso de verdad, y solo lo vio CI en la matriz 3.10:

    f"({', '.join(sorted({f'{d["canvas"]["width"]:.0f}' for d in x}))})"

`ast.parse(..., feature_version=(3, 10))` NO sirve para esto —lo acepta, el
tokenizador no esta versionado—, asi que hay que mirar los tokens. Con
`FSTRING_START`/`FSTRING_END` (3.12+) se sabe exactamente donde empieza y
acaba cada f-string y con que comilla, y cualquier STRING de dentro que use
esa misma comilla es sintaxis que 3.10 no compila.

Cubre esa trampa, que es la que hemos pisado. No es un backport del parser.
"""
from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
FUENTES = sorted((RAIZ / "src").rglob("*.py")) + sorted((RAIZ / "scripts").rglob("*.py"))


def minimo_declarado() -> tuple:
    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'requires-python\s*=\s*"[><=~^ ]*(\d+)\.(\d+)', texto)
    assert m, "pyproject.toml no declara requires-python"
    return (int(m.group(1)), int(m.group(2)))


def comillas_anidadas(fuente: str):
    """Posiciones donde un f-string reusa por dentro una comilla que lo cierra.

    La regla de antes de 3.12 es literal: el texto de un f-string no puede
    contener su propio delimitador. Y vale para CUALQUIERA de los f-strings
    que envuelven, no solo el mas interno —el caso que se colo tenia el
    `"canvas"` dentro de un `f'...'` que a su vez estaba dentro de un
    `f"..."`—. Un `f\"\"\"` solo choca con tres comillas seguidas, no con una.
    """
    tokens = tokenize.generate_tokens(io.StringIO(fuente).readline)
    pila: list = []
    hallazgos = []
    for tok in tokens:
        if tok.type == getattr(tokenize, "FSTRING_START", -1):
            pila.append(tok.string.lstrip("fFrRbB"))       # el delimitador
        elif tok.type == getattr(tokenize, "FSTRING_END", -1):
            if pila:
                pila.pop()
        elif pila and tok.type in (tokenize.STRING,
                                   getattr(tokenize, "FSTRING_MIDDLE", -1)):
            texto = tok.string
            if tok.type != tokenize.STRING:
                # En la parte LITERAL, una comilla escapada siempre fue legal
                # —`f"... ROW(\"n\", ...)"` es DAX de toda la vida—. Lo que
                # 3.10 no admite es la comilla desnuda.
                texto = texto.replace('\\"', "").replace("\\'", "")
            if any(delim in texto for delim in pila):
                hallazgos.append((tok.start[0], tok.string))
    return hallazgos


@pytest.mark.skipif(not hasattr(tokenize, "FSTRING_START"),
                    reason="el interprete no distingue los tokens de f-string "
                           "(<3.12); ahi el propio parser ya rechaza PEP 701")
@pytest.mark.parametrize("ruta", FUENTES, ids=lambda p: p.name)
def test_ninguna_fuente_usa_comillas_anidadas_en_f_strings(ruta):
    minimo = minimo_declarado()
    if minimo >= (3, 12):
        pytest.skip(f"el minimo declarado ya es {minimo}: PEP 701 es legal")

    hallazgos = comillas_anidadas(ruta.read_text(encoding="utf-8"))
    assert not hallazgos, (
        f"{ruta.relative_to(RAIZ)} usa comillas anidadas dentro de un f-string "
        f"(PEP 701, Python 3.12+) y el minimo declarado es "
        f"{minimo[0]}.{minimo[1]}. En 3.10 esto es un SyntaxError AL IMPORTAR: "
        f"el servidor no arranca. Saca la expresion a una variable antes. "
        f"Lineas: {[l for l, _ in hallazgos]}")


def test_el_detector_encuentra_el_caso_que_se_nos_colo():
    """El detector tiene que acusar la linea real que rompio CI."""
    c = '"'
    malo = ("x = f" + c + "({', '.join(sorted({f'{d[" + c + "canvas" + c
            + "][" + c + "width" + c + "]:.0f}' for d in y}))})" + c + "\n")

    hallazgos = comillas_anidadas(malo)
    assert hallazgos, "el detector no vio el caso que rompio la matriz 3.10"


def test_el_detector_no_acusa_lo_que_si_es_legal():
    """Comillas del OTRO tipo por dentro son validas desde siempre."""
    assert comillas_anidadas("""x = f"{d['canvas']['width']:.0f}"\n""") == []
    assert comillas_anidadas('''x = f'{d["canvas"]}'\n''') == []
    assert comillas_anidadas('x = "una cadena normal"\n') == []
    assert comillas_anidadas('x = f"{a}{b}" + "otra"\n') == []
