"""Referencias de una expresion DAX contra el modelo TMDL, sin abrir Desktop.

El caso que lo motiva (duelo ronda 2, un modelo de 4B): trece medidas escritas
en modo `pbip` que el escritor acepto y `pbi_validate_tmdl` dio por validas,
pero que ninguna podia evaluarse:

- ``SUMX(Presupuesto Edificio Mirador, ...)``: tabla con espacios sin comillas.
- ``VAC = EAC - BAC``: medidas citadas sin corchetes, que DAX lee como tablas.

Solo se acusa lo que se puede afirmar mirando el texto y el modelo; no se
evalua nada ni se juzga si la formula tiene sentido de negocio. Las cadenas,
los comentarios, los nombres entre comillas y entre corchetes se leen con sus
reglas de escape, asi que ``"TOTAL [x]"`` dentro de una cadena no cuenta.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

#: Palabras que DAX acepta sueltas, sin parentesis ni comillas.
_PALABRAS = {
    "var", "return", "true", "false", "in", "not", "and", "or", "asc", "desc",
    "define", "measure", "evaluate", "order", "by", "start", "at", "table",
    "column", "integer", "string", "double", "boolean", "currency",
    "datetime", "int64", "decimal", "boolean", "skip", "dense", "rel",
    "abs", "relative", "absolute", "keepfilters", "blank",
}

#: Caracteres que se cuelan al copiar de un chat o de Word y que DAX no
#: reconoce como operadores.
_TIPOGRAFICOS = {
    "−": "-", "–": "-", "—": "-", "×": "*", "÷": "/",
    "“": '"', "”": '"', "‘": "'", "’": "'",
}

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _cerrar(texto: str, i: int, cierre: str) -> int:
    """Indice del cierre de un literal cuyo escape es el cierre duplicado."""
    n = len(texto)
    while i < n:
        if texto[i] == cierre:
            if i + 1 < n and texto[i + 1] == cierre:
                i += 2
                continue
            return i
        i += 1
    return n


def tokens(texto: str) -> List[Tuple[str, str, int, int]]:
    """(tipo, valor, inicio, fin). Tipos: string, table, bracket, ident, num, op."""
    salida: List[Tuple[str, str, int, int]] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c.isspace():
            i += 1
        elif texto.startswith("//", i) or texto.startswith("--", i):
            fin = texto.find("\n", i)
            i = n if fin < 0 else fin
        elif texto.startswith("/*", i):
            fin = texto.find("*/", i + 2)
            i = n if fin < 0 else fin + 2
        elif c in "\"'[":
            cierre = "]" if c == "[" else c
            fin = _cerrar(texto, i + 1, cierre)
            valor = texto[i + 1:fin].replace(cierre * 2, cierre)
            tipo = {"\"": "string", "'": "table", "[": "bracket"}[c]
            salida.append((tipo, valor, i, min(fin + 1, n)))
            i = fin + 1
        elif c.isdigit() or (c == "." and i + 1 < n and texto[i + 1].isdigit()):
            m = re.match(r"[0-9.]+(?:[eE][+-]?[0-9]+)?", texto[i:])
            salida.append(("num", m.group(0), i, i + m.end()))
            i += m.end()
        else:
            m = _IDENT.match(texto, i)
            if m:
                salida.append(("ident", m.group(0), i, m.end()))
                i = m.end()
            else:
                salida.append(("op", c, i, i + 1))
                i += 1
    return salida


def indice_desde_tablas(tablas: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Indice sin distinguir mayusculas, como compara el motor."""
    por_tabla: Dict[str, Dict[str, Any]] = {}
    medidas: Dict[str, str] = {}
    columnas: Set[str] = set()
    for tabla in tablas:
        cols = {c["name"].casefold() for c in tabla.get("columns") or []}
        meds = {m["name"].casefold() for m in tabla.get("measures") or []}
        por_tabla[tabla["name"].casefold()] = {
            "name": tabla["name"], "columns": cols, "measures": meds}
        columnas |= cols
        for m in tabla.get("measures") or []:
            medidas[m["name"].casefold()] = m["name"]
    return {"tables": por_tabla, "measures": medidas, "columns": columnas}


def indice_desde_definicion(definition: Path) -> Dict[str, Any]:
    from horizun_pbi_mcp.pbip import tmdl_reader

    tablas = []
    carpeta = Path(definition) / "tables"
    for archivo in sorted(carpeta.glob("*.tmdl")) if carpeta.is_dir() else []:
        tablas.append(tmdl_reader.parse_table_file(archivo))
    return indice_desde_tablas(tablas)


def _necesita_comillas(nombre: str) -> bool:
    return not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nombre)


def _citar_tabla(nombre: str) -> str:
    return "'" + nombre.replace("'", "''") + "'"


def _bare_table_arguments(toks: List[Tuple[str, str, int, int]]) -> Set[int]:
    """Only whole, bare arguments in known table slots are provably tables.

    Do not guess signatures of arbitrary calls or classify scalar enums.
    Track nested calls/constructors so their commas cannot change the slot.
    """
    first_table = {"countrows", "sumx", "averagex", "minx", "maxx", "countx",
                   "countax", "filter", "selectcolumns", "addcolumns",
                   "summarize", "groupby", "calculatetable", "all",
                   "allexcept", "allselected", "distinct", "values"}
    found: Set[int] = set()
    stack: List[Tuple[str, int, int]] = []
    for k, (kind, value, _, _) in enumerate(toks):
        if kind != "op":
            continue
        if value in ("(", "{"):
            name = toks[k - 1][1].casefold() if value == "(" and k and toks[k - 1][0] == "ident" else ""
            stack.append((name, 0, k + 1))
        elif value in (",", ";", ")", "}") and stack:
            name, arg, start = stack[-1]
            if name in first_table and arg == 0 and k == start + 1 \
                    and toks[start][0] == "ident":
                found.add(start)
            if value in (",", ";"):
                stack[-1] = (name, arg + 1, k + 1)
            else:
                stack.pop()
    return found


def revisar(expresion: str, indice: Dict[str, Any], *,
            medidas_extra: Iterable[str] = ()) -> List[Dict[str, str]]:
    """Problemas que impedirian evaluar la expresion. Lista vacia si ninguno.

    `medidas_extra`: nombres que cuentan como medidas aunque aun no esten en
    el TMDL (la que se esta creando, por ejemplo).
    """
    problemas: List[Dict[str, str]] = []
    vistos: Set[Tuple[str, str]] = set()

    def acusa(regla: str, referencia: str, mensaje: str) -> None:
        if (regla, referencia.casefold()) not in vistos:
            vistos.add((regla, referencia.casefold()))
            problemas.append({"rule": regla, "reference": referencia,
                              "message": mensaje})

    toks = tokens(expresion)
    table_arguments = _bare_table_arguments(toks)
    tablas = indice["tables"]
    medidas = dict(indice["measures"])
    for extra in medidas_extra:
        medidas.setdefault(extra.casefold(), extra)
    literales = {v.casefold() for t, v, _, _ in toks if t == "string"}

    # Texto con cadenas, comentarios y nombres citados en blanco: lo que queda
    # es lo que DAX lee como identificadores sueltos.
    limpio = list(expresion)
    for tipo, _, ini, fin in toks:
        if tipo in ("string", "table", "bracket"):
            limpio[ini:fin] = " " * (fin - ini)
    # Las cadenas ya estan en blanco, asi que un '//' dentro de una no llega aqui.
    limpio_txt = re.sub(r"//[^\n]*|--[^\n]*|/\*.*?\*/",
                        lambda m: " " * len(m.group(0)), "".join(limpio),
                        flags=re.S)

    for ch, ok in _TIPOGRAFICOS.items():
        if ch in limpio_txt:
            acusa("dax_typographic_character", ch,
                  f"El caracter '{ch}' (U+{ord(ch):04X}) no es un operador DAX; "
                  f"usa '{ok}'.")

    # 1. Tablas con espacios sin comillas.
    tapados: List[Tuple[int, int]] = []
    for clave, info in tablas.items():
        nombre = info["name"]
        if not _necesita_comillas(nombre):
            continue
        patron = r"(?<![\w])" + r"\s+".join(
            re.escape(p) for p in nombre.split()) + r"(?![\w])"
        for m in re.finditer(patron, limpio_txt, re.I):
            tapados.append((m.start(), m.end()))
            acusa("dax_unquoted_table_name", nombre,
                  f"La tabla '{nombre}' lleva espacios o simbolos: en DAX va "
                  f"entre comillas simples, {_citar_tabla(nombre)}.")

    def tapado(pos: int) -> bool:
        return any(a <= pos < b for a, b in tapados)

    variables = {toks[k + 1][1].casefold() for k in range(len(toks) - 1)
                 if toks[k][0] == "ident" and toks[k][1].casefold() == "var"
                 and toks[k + 1][0] == "ident"}

    # Offline we cannot resolve the row schema of table expressions. Defer
    # unqualified references in that context; explicit model references below
    # remain checkable. A VAR may hold a table, including a table constructor.
    virtual_context = bool(variables) or any(t == "op" and v == "{" for t, v, _, _ in toks) or any(
        t == "ident" and v.casefold() in {
            "generateseries", "calendar", "calendarauto", "selectcolumns",
            "addcolumns", "summarize", "summarizecolumns", "datatable",
            "row", "groupby", "generate", "generateall", "union",
            "crossjoin", "intersect", "except", "naturalinnerjoin",
            "naturalleftouterjoin"}
        and k + 1 < len(toks) and toks[k + 1][1] == "("
        for k, (t, v, _, _) in enumerate(toks))

    for k, (tipo, valor, ini, _fin) in enumerate(toks):
        siguiente = toks[k + 1] if k + 1 < len(toks) else None
        previo = toks[k - 1] if k > 0 else None
        if tapado(ini):
            continue
        # 2. Tabla[Columna]
        if tipo in ("table", "ident") and siguiente and siguiente[0] == "bracket":
            if tipo == "ident" and valor.casefold() in variables:
                continue
            info = tablas.get(valor.casefold())
            if info is None:
                acusa("dax_unknown_table", valor,
                      f"La tabla '{valor}' no existe en el modelo.")
            elif siguiente[1].casefold() not in info["columns"] | info["measures"]:
                acusa("dax_unknown_column", f"{valor}[{siguiente[1]}]",
                      f"'{info['name']}' no tiene la columna ni la medida "
                      f"[{siguiente[1]}].")
            continue
        # 3. [Nombre] sin tabla
        if tipo == "bracket":
            if previo and previo[0] in ("table", "ident") and not tapado(previo[2]):
                continue
            if previo and tapado(previo[2]):
                continue
            clave = valor.casefold()
            if clave in medidas or clave in indice["columns"] or clave in literales \
                    or virtual_context:
                continue
            acusa("dax_unknown_reference", f"[{valor}]",
                  f"[{valor}] no es una medida ni una columna del modelo.")
            continue
        # 4. Medida citada sin corchetes: DAX la lee como una tabla.
        if tipo == "ident":
            clave = valor.casefold()
            llamada = siguiente is not None and siguiente[0] == "op" \
                and siguiente[1] == "("
            if llamada or clave in _PALABRAS or clave in variables \
                    or clave in tablas:
                continue
            if clave in medidas:
                acusa("dax_measure_without_brackets", valor,
                      f"'{valor}' es una medida: en DAX se cita entre "
                      f"corchetes, [{medidas[clave]}]. Sin ellos se lee como "
                      "una tabla.")
            elif clave in indice["columns"]:
                acusa("dax_column_without_table", valor,
                      f"'{valor}' es una columna: citala como "
                      f"Tabla[{valor}].")
            elif k in table_arguments:
                acusa("dax_unknown_table", valor,
                      f"La tabla '{valor}' no existe en el modelo.")
    # Tambien una tabla citada con comillas que no existe.
    for k, (tipo, valor, ini, _f) in enumerate(toks):
        if tipo == "table" and valor.casefold() not in tablas:
            acusa("dax_unknown_table", valor,
                  f"La tabla '{valor}' no existe en el modelo.")
    return problemas


def resumen(problemas: List[Dict[str, str]]) -> str:
    return " ".join(p["message"] for p in problemas)
