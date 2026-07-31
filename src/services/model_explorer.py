"""Exploracion del modelo semantico: busqueda, objetos y dependencias.

Trabaja sobre el diccionario NORMALIZADO del modelo, venga de la capa en vivo
(`powerbi.model_reader.read_model`) o de los archivos TMDL
(`pbip.tmdl_reader.read_semantic_model`). Esa es la razon de que viva en
`services/` y no en una de las dos capas: la misma exploracion sirve con Power
BI Desktop abierto y sin el.

Las dependencias se extraen reutilizando el escaner lexico de `dax_guard`: se
neutralizan primero cadenas y comentarios, asi que un `"Ventas[Monto]"` escrito
dentro de un literal no cuenta como referencia. Es un analisis LEXICO, no una
resolucion semantica del motor: detecta referencias escritas, no las que se
construyen dinamicamente con funciones.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from services import dax_guard

#: 'Tabla'[Columna] o Tabla[Columna]
_REF_CUALIFICADA = re.compile(
    r"(?:'(?P<q>[^']+)'|(?P<b>[^\W\d]\w*))\s*\[(?P<campo>[^\]]+)\]", re.UNICODE)
#: [Medida] suelta, sin tabla delante
_REF_SIMPLE = re.compile(r"(?<![\w'\]])\[(?P<campo>[^\]]+)\]", re.UNICODE)


def _sin_literales(expresion: str) -> str:
    """Neutraliza comentarios y cadenas, pero CONSERVA los corchetes.

    `dax_guard.scan` sustituye tambien los `[...]`, que aqui son justo lo que
    interesa. Por eso se hace una pasada propia que solo quita comentarios y
    cadenas dobles.
    """
    salida: List[str] = []
    i, n = 0, len(expresion)
    while i < n:
        ch = expresion[i]
        nxt = expresion[i + 1] if i + 1 < n else ""
        if (ch == "/" and nxt == "/") or (ch == "-" and nxt == "-"):
            while i < n and expresion[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            fin = expresion.find("*/", i + 2)
            i = n if fin == -1 else fin + 2
            salida.append(" ")
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if expresion[j] == '"':
                    if j + 1 < n and expresion[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            salida.append(" ")
            i = j + 1
            continue
        salida.append(ch)
        i += 1
    return "".join(salida)


def extract_references(expresion: Optional[str]) -> Dict[str, List[str]]:
    """Referencias escritas en una expresion DAX.

    Devuelve `columns` (`Tabla[Campo]`) y `unqualified` (`[Campo]`, que puede
    ser una medida o una columna de la misma tabla: sin el modelo no se sabe).
    """
    if not expresion:
        return {"columns": [], "unqualified": []}
    texto = _sin_literales(expresion)

    columnas: List[str] = []
    ocupados: List[Tuple[int, int]] = []
    for m in _REF_CUALIFICADA.finditer(texto):
        tabla = m.group("q") or m.group("b")
        columnas.append(f"{tabla}[{m.group('campo').strip()}]")
        ocupados.append(m.span())

    sueltas: List[str] = []
    for m in _REF_SIMPLE.finditer(texto):
        if any(ini <= m.start() < fin for ini, fin in ocupados):
            continue
        sueltas.append(m.group("campo").strip())

    return {"columns": sorted(set(columnas)), "unqualified": sorted(set(sueltas))}


# ------------------------------------------------------------------ indices ---
def build_index(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Indices de busqueda del modelo, calculados una vez."""
    medidas = {m["name"]: m for m in model_data.get("measures", [])}
    columnas: Dict[str, Dict[str, Any]] = {}
    for t in model_data.get("tables", []):
        for c in t.get("columns", []):
            columnas[f"{t['name']}[{c['name']}]"] = {**c, "table": t["name"]}
    return {
        "measures": medidas,
        "columns": columnas,
        "tables": {t["name"]: t for t in model_data.get("tables", [])},
    }


def resolve_reference(ref: str, indice: Dict[str, Any],
                      tabla_contexto: Optional[str] = None) -> Dict[str, Any]:
    """Determina si una referencia existe y a que apunta."""
    if "[" in ref:
        if ref in indice["columns"]:
            return {"ref": ref, "kind": "column", "exists": True}
        campo = ref.split("[", 1)[1].rstrip("]")
        if campo in indice["measures"]:
            return {"ref": ref, "kind": "measure", "exists": True,
                    "note": "medida referenciada con tabla delante"}
        return {"ref": ref, "kind": "unknown", "exists": False}
    if ref in indice["measures"]:
        return {"ref": ref, "kind": "measure", "exists": True}
    if tabla_contexto and f"{tabla_contexto}[{ref}]" in indice["columns"]:
        return {"ref": f"{tabla_contexto}[{ref}]", "kind": "column", "exists": True}
    for clave in indice["columns"]:
        if clave.endswith(f"[{ref}]"):
            return {"ref": clave, "kind": "column", "exists": True,
                    "note": "resuelta por nombre de columna unico"}
    return {"ref": ref, "kind": "unknown", "exists": False}


# ------------------------------------------------------------- dependencias ---
def measure_dependencies(model_data: Dict[str, Any], nombre: str, *,
                         profundidad: int = 3) -> Dict[str, Any]:
    """Dependencias directas y transitivas de una medida, y quien la usa."""
    indice = build_index(model_data)
    if nombre not in indice["measures"]:
        from powerbi.errors import MeasureNotFoundError

        raise MeasureNotFoundError(
            f"La medida '{nombre}' no existe en el modelo.",
            details={"available": sorted(indice["measures"])[:50]})

    def directas(medida: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        refs = extract_references(medida.get("expression"))
        resueltas = [resolve_reference(r, indice, medida.get("table"))
                     for r in refs["columns"] + refs["unqualified"]]
        return {
            "measures": [r for r in resueltas if r["kind"] == "measure"],
            "columns": [r for r in resueltas if r["kind"] == "column"],
            "broken": [r for r in resueltas if not r["exists"]],
        }

    raiz = directas(indice["measures"][nombre])

    # Cierre transitivo sobre medidas.
    vistas: Set[str] = {nombre}
    pendientes = [(m["ref"], 1) for m in raiz["measures"]]
    transitivas: List[Dict[str, Any]] = []
    while pendientes:
        ref, nivel = pendientes.pop()
        clave = ref.split("[", 1)[1].rstrip("]") if "[" in ref else ref
        if clave in vistas or nivel > profundidad:
            continue
        vistas.add(clave)
        transitivas.append({"measure": clave, "depth": nivel})
        hija = indice["measures"].get(clave)
        if hija:
            for m in directas(hija)["measures"]:
                pendientes.append((m["ref"], nivel + 1))

    # Quien depende de ESTA medida.
    usada_por = []
    for otra, datos in indice["measures"].items():
        if otra == nombre:
            continue
        refs = extract_references(datos.get("expression"))
        if nombre in refs["unqualified"] or any(
                r.endswith(f"[{nombre}]") for r in refs["columns"]):
            usada_por.append({"measure": otra, "table": datos.get("table")})

    return {
        "measure": nombre,
        "table": indice["measures"][nombre].get("table"),
        "depends_on": raiz,
        "transitive_measures": sorted(transitivas, key=lambda x: (x["depth"], x["measure"])),
        "used_by": sorted(usada_por, key=lambda x: x["measure"]),
        "is_leaf": not raiz["measures"],
        "is_unused": not usada_por,
        "broken_references": raiz["broken"],
        "note": ("Analisis lexico de las expresiones: detecta referencias "
                 "escritas, no las construidas dinamicamente."),
    }


def column_dependencies(model_data: Dict[str, Any], tabla: str,
                        columna: str) -> Dict[str, Any]:
    """Que medidas, columnas calculadas, relaciones y jerarquias la usan."""
    indice = build_index(model_data)
    clave = f"{tabla}[{columna}]"
    if clave not in indice["columns"]:
        from powerbi.errors import TableNotFoundError

        raise TableNotFoundError(
            f"La columna '{clave}' no existe en el modelo.",
            details={"table": tabla, "column": columna})

    def usa(expresion: Optional[str]) -> bool:
        refs = extract_references(expresion)
        return clave in refs["columns"] or columna in refs["unqualified"]

    medidas = [{"measure": m["name"], "table": m.get("table")}
               for m in model_data.get("measures", []) if usa(m.get("expression"))]
    calculadas = [
        {"column": f"{t['name']}[{c['name']}]"}
        for t in model_data.get("tables", []) for c in t.get("columns", [])
        if c.get("expression") and usa(c["expression"])
        and f"{t['name']}[{c['name']}]" != clave]
    relaciones = [
        r for r in model_data.get("relationships", [])
        if (r.get("from_table") == tabla and r.get("from_column") == columna)
        or (r.get("to_table") == tabla and r.get("to_column") == columna)]
    jerarquias = [
        {"hierarchy": h["name"], "table": h.get("table")}
        for h in model_data.get("hierarchies", [])
        if any(l.get("column") == columna for l in h.get("levels", []))]

    return {
        "column": clave,
        "properties": indice["columns"][clave],
        "used_by_measures": medidas,
        "used_by_calculated_columns": calculadas,
        "used_by_relationships": relaciones,
        "used_by_hierarchies": jerarquias,
        "is_unused": not (medidas or calculadas or relaciones or jerarquias),
    }


# ---------------------------------------------------------------- busqueda ----
def search(model_data: Dict[str, Any], termino: str, *,
           kinds: Optional[Iterable[str]] = None,
           limit: int = 50) -> Dict[str, Any]:
    """Busca por nombre (y en el DAX de las medidas), sin distinguir mayusculas."""
    q = (termino or "").strip().lower()
    if not q:
        from powerbi.errors import ValidationError

        raise ValidationError("El termino de busqueda esta vacio.")
    tipos = set(kinds) if kinds else {"table", "column", "measure", "hierarchy", "role"}
    hits: List[Dict[str, Any]] = []

    if "table" in tipos:
        for t in model_data.get("tables", []):
            if q in t["name"].lower():
                hits.append({"kind": "table", "name": t["name"],
                             "is_hidden": t.get("is_hidden")})
    if "column" in tipos:
        for t in model_data.get("tables", []):
            for c in t.get("columns", []):
                if q in c["name"].lower():
                    hits.append({"kind": "column", "name": f"{t['name']}[{c['name']}]",
                                 "table": t["name"], "data_type": c.get("data_type"),
                                 "is_hidden": c.get("is_hidden")})
    if "measure" in tipos:
        for m in model_data.get("measures", []):
            en_nombre = q in m["name"].lower()
            en_dax = q in (m.get("expression") or "").lower()
            if en_nombre or en_dax:
                hits.append({"kind": "measure", "name": m["name"],
                             "table": m.get("table"),
                             "matched_in": "name" if en_nombre else "expression"})
    if "hierarchy" in tipos:
        for h in model_data.get("hierarchies", []):
            if q in h["name"].lower():
                hits.append({"kind": "hierarchy", "name": h["name"],
                             "table": h.get("table")})
    if "role" in tipos:
        for r in model_data.get("roles", []):
            if q in (r.get("name") or "").lower():
                hits.append({"kind": "role", "name": r["name"]})

    return {"term": termino, "total": len(hits), "truncated": len(hits) > limit,
            "results": hits[:limit]}


def get_object(model_data: Dict[str, Any], kind: str, name: str) -> Dict[str, Any]:
    """Devuelve un objeto del modelo con todo su detalle."""
    from powerbi.errors import ValidationError

    indice = build_index(model_data)
    k = (kind or "").lower()
    if k == "table":
        t = indice["tables"].get(name)
        if not t:
            raise ValidationError(f"No existe la tabla '{name}'.",
                                  details={"available": sorted(indice["tables"])[:50]})
        return {"kind": "table", "object": t}
    if k == "measure":
        m = indice["measures"].get(name)
        if not m:
            raise ValidationError(f"No existe la medida '{name}'.",
                                  details={"available": sorted(indice["measures"])[:50]})
        return {"kind": "measure", "object": m,
                "references": extract_references(m.get("expression"))}
    if k == "column":
        c = indice["columns"].get(name)
        if not c:
            raise ValidationError(
                f"No existe la columna '{name}'. Usa la forma 'Tabla[Columna]'.",
                details={"available": sorted(indice["columns"])[:50]})
        return {"kind": "column", "object": c}
    raise ValidationError(f"kind invalido: '{kind}'. Usa table|column|measure.")


def summary(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Resumen compacto del modelo, pensado para que lo lea un LLM."""
    tablas = model_data.get("tables", [])
    medidas = model_data.get("measures", [])
    relaciones = model_data.get("relationships", [])
    indice = build_index(model_data)

    columnas_totales = sum(len(t.get("columns", [])) for t in tablas)
    calculadas = [f"{t['name']}[{c['name']}]" for t in tablas
                  for c in t.get("columns", [])
                  if c.get("column_type") == "Calculated"]
    ocultas = sum(1 for t in tablas for c in t.get("columns", []) if c.get("is_hidden"))

    rotas = []
    for m in medidas:
        refs = extract_references(m.get("expression"))
        for r in refs["columns"] + refs["unqualified"]:
            if not resolve_reference(r, indice, m.get("table"))["exists"]:
                rotas.append({"measure": m["name"], "reference": r})

    conectadas = {r.get("from_table") for r in relaciones} | \
                 {r.get("to_table") for r in relaciones}
    desconectadas = [t["name"] for t in tablas if t["name"] not in conectadas]

    return {
        "model": model_data.get("model", {}),
        "source": model_data.get("source", "live"),
        "counts": {
            "tables": len(tablas), "columns": columnas_totales,
            "measures": len(medidas), "relationships": len(relaciones),
            "hierarchies": len(model_data.get("hierarchies", [])),
            "roles": len(model_data.get("roles", [])),
            "calculated_columns": len(calculadas),
            "hidden_columns": ocultas,
        },
        "tables": [{"name": t["name"], "columns": len(t.get("columns", [])),
                    "measures": t.get("measure_count", 0),
                    "is_hidden": t.get("is_hidden"),
                    "is_date_table": t.get("is_date_table")} for t in tablas],
        "measures_by_table": {
            t["name"]: [m["name"] for m in medidas if m.get("table") == t["name"]]
            for t in tablas},
        "calculated_columns": calculadas,
        "disconnected_tables": desconectadas,
        "broken_references": rotas,
        "bidirectional_relationships": [
            f"{r.get('from_table')} <-> {r.get('to_table')}" for r in relaciones
            if str(r.get("cross_filtering")) in ("BothDirections", "bothDirections")],
    }
