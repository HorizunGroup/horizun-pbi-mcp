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

from horizun_pbi_mcp.powerbi.errors import TableNotFoundError, ValidationError
from horizun_pbi_mcp.services import dax_guard

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
def _indexar_ci(destino: Dict[str, List[str]], clave: str, canonico: str) -> None:
    """Anota `canonico` bajo la clave normalizada, sin perder homonimos."""
    destino.setdefault(str(clave).casefold(), []).append(canonico)


def _unico(candidatos: Optional[List[str]]) -> Optional[str]:
    """El unico candidato, o None si hay cero o mas de uno."""
    return candidatos[0] if candidatos and len(candidatos) == 1 else None


def build_index(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Indices de busqueda del modelo, calculados una vez.

    DAX y TOM NO distinguen mayusculas de minusculas en los identificadores:
    `Cronograma[Fecha]` y `CRONOGRAMA[FECHA]` son el MISMO objeto para el
    motor. Comparar por igualdad exacta convertia esa diferencia de escritura
    en una `measure_broken_reference` inventada.

    Por eso, junto a los indices canonicos —los que devuelven el nombre tal
    como lo declara el modelo— se calculan indices NORMALIZADOS con
    `casefold()`. El valor de cada entrada normalizada es una LISTA: si dos
    objetos colisionan al normalizar, la ambiguedad se conserva para poder
    rechazarla en vez de elegir uno en silencio.
    """
    medidas = {m["name"]: m for m in model_data.get("measures", [])}
    columnas: Dict[str, Dict[str, Any]] = {}
    tablas = {t["name"]: t for t in model_data.get("tables", [])}
    for t in model_data.get("tables", []):
        for c in t.get("columns", []):
            columnas[f"{t['name']}[{c['name']}]"] = {**c, "table": t["name"]}

    medidas_ci: Dict[str, List[str]] = {}
    columnas_ci: Dict[str, List[str]] = {}
    tablas_ci: Dict[str, List[str]] = {}
    campos_ci: Dict[str, List[str]] = {}
    for nombre in medidas:
        _indexar_ci(medidas_ci, nombre, nombre)
    for nombre in tablas:
        _indexar_ci(tablas_ci, nombre, nombre)
    for clave, datos in columnas.items():
        _indexar_ci(columnas_ci, clave, clave)
        _indexar_ci(campos_ci, datos["name"], clave)

    return {
        "measures": medidas,
        "columns": columnas,
        "tables": tablas,
        # Normalizados: casefold -> [nombres canonicos]
        "measures_ci": medidas_ci,
        "columns_ci": columnas_ci,
        "tables_ci": tablas_ci,
        "columns_by_field_ci": campos_ci,
    }


def _ci(indice: Dict[str, Any], clave: str) -> Dict[str, List[str]]:
    """Indice normalizado, tolerando un indice construido a mano en pruebas."""
    valor = indice.get(clave)
    return valor if isinstance(valor, dict) else {}


def canonical_table(indice: Dict[str, Any], nombre: str) -> Optional[str]:
    """Nombre canonico de una tabla escrita con cualquier capitalizacion."""
    return _unico(_ci(indice, "tables_ci").get(str(nombre).casefold()))


def canonical_measure(indice: Dict[str, Any], nombre: str) -> Optional[str]:
    """Nombre canonico de una medida escrita con cualquier capitalizacion."""
    return _unico(_ci(indice, "measures_ci").get(str(nombre).casefold()))


def canonical_column(indice: Dict[str, Any], tabla: str,
                     columna: str) -> Optional[str]:
    """`Tabla[Columna]` canonico a partir de una escritura cualquiera."""
    return _unico(_ci(indice, "columns_ci").get(
        f"{tabla}[{columna}]".casefold()))


def _ambigua(ref: str, candidatos: List[str], motivo: str) -> Dict[str, Any]:
    """Una coincidencia ambigua NO se resuelve: se declara como tal.

    `exists` es False a proposito: quien llama no puede tratarla como
    resuelta. `kind='ambiguous'` la distingue de una referencia inexistente,
    que es un problema distinto y con otra correccion.
    """
    return {"ref": ref, "kind": "ambiguous", "exists": False,
            "reason": motivo, "candidates": sorted(candidatos),
            "note": (f"'{ref}' coincide con {len(candidatos)} objetos del "
                     "modelo al ignorar mayusculas y minusculas; no se elige "
                     "uno. Cualifica la referencia.")}


def resolve_reference(ref: str, indice: Dict[str, Any],
                      tabla_contexto: Optional[str] = None) -> Dict[str, Any]:
    """Determina si una referencia existe y a que apunta.

    La comparacion ignora mayusculas y minusculas, como el motor, y lo que se
    devuelve en `ref` es SIEMPRE el nombre canonico del modelo, no el que
    escribio quien redacto el DAX.
    """
    columnas_ci = _ci(indice, "columns_ci")
    medidas_ci = _ci(indice, "measures_ci")
    campos_ci = _ci(indice, "columns_by_field_ci")

    if "[" in ref:
        tabla, _, resto = ref.partition("[")
        campo = resto.rstrip("]")
        candidatas = columnas_ci.get(f"{tabla}[{campo}]".casefold(), [])
        if len(candidatas) > 1:
            return _ambigua(ref, candidatas, "columna_ambigua")
        if candidatas:
            return {"ref": candidatas[0], "kind": "column", "exists": True}
        medidas = medidas_ci.get(campo.casefold(), [])
        if len(medidas) > 1:
            return _ambigua(ref, medidas, "medida_ambigua")
        if medidas:
            tabla_canon = canonical_table(indice, tabla) or tabla
            return {"ref": f"{tabla_canon}[{medidas[0]}]", "kind": "measure",
                    "exists": True, "measure": medidas[0],
                    "note": "medida referenciada con tabla delante"}
        conocida = bool(_ci(indice, "tables_ci").get(tabla.casefold()))
        return {"ref": ref, "kind": "unknown", "exists": False,
                "reason": "columna_inexistente" if conocida
                          else "tabla_inexistente"}

    medidas = medidas_ci.get(ref.casefold(), [])
    if len(medidas) > 1:
        return _ambigua(ref, medidas, "medida_ambigua")
    if medidas:
        return {"ref": medidas[0], "kind": "measure", "exists": True,
                "measure": medidas[0]}
    if tabla_contexto:
        en_contexto = canonical_column(indice, tabla_contexto, ref)
        if en_contexto:
            return {"ref": en_contexto, "kind": "column", "exists": True}
    candidatas = campos_ci.get(ref.casefold(), [])
    if len(candidatas) > 1:
        return _ambigua(ref, candidatas, "columna_ambigua")
    if candidatas:
        return {"ref": candidatas[0], "kind": "column", "exists": True,
                "note": "resuelta por nombre de columna unico"}
    return {"ref": ref, "kind": "unknown", "exists": False,
            "reason": "objeto_inexistente"}


# ------------------------------------------------------------- dependencias ---
def measure_dependencies(model_data: Dict[str, Any], nombre: str, *,
                         profundidad: int = 3) -> Dict[str, Any]:
    """Dependencias directas y transitivas de una medida, y quien la usa."""
    indice = build_index(model_data)
    canonico = canonical_measure(indice, nombre)
    if canonico is None:
        from horizun_pbi_mcp.powerbi.errors import MeasureNotFoundError

        raise MeasureNotFoundError(
            f"La medida '{nombre}' no existe en el modelo.",
            details={"available": sorted(indice["measures"])[:50]})
    nombre = canonico

    def directas(medida: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        refs = extract_references(medida.get("expression"))
        resueltas = [resolve_reference(r, indice, medida.get("table"))
                     for r in refs["columns"] + refs["unqualified"]]
        return {
            "measures": [r for r in resueltas if r["kind"] == "measure"],
            "columns": [r for r in resueltas if r["kind"] == "column"],
            # Una referencia AMBIGUA no esta rota: existe mas de una vez. Se
            # separa para no acusar de inexistente lo que sobra, no falta.
            "broken": [r for r in resueltas if r["kind"] == "unknown"],
            "ambiguous": [r for r in resueltas if r["kind"] == "ambiguous"],
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
    objetivo = nombre.casefold()
    for otra, datos in indice["measures"].items():
        if otra == nombre:
            continue
        refs = extract_references(datos.get("expression"))
        if objetivo in {r.casefold() for r in refs["unqualified"]} or any(
                r.casefold().endswith(f"[{objetivo}]") for r in refs["columns"]):
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
        "ambiguous_references": raiz["ambiguous"],
        "note": ("Analisis lexico de las expresiones: detecta referencias "
                 "escritas, no las construidas dinamicamente. La comparacion "
                 "ignora mayusculas y minusculas, como el motor, pero NO se "
                 "comprobo nada contra el motor."),
    }


#: Clases de dependencia que este modulo sabe comprobar sobre el MODELO. Los
#: visuales del informe NO estan aqui: viven en el PBIR, no en el modelo, y
#: quien los necesite tiene que comprobarlos aparte y decirlo.
CLASES_DE_USO = ("measures", "calculated_columns", "relationships",
                 "hierarchies")


def column_usage_index(model_data: Dict[str, Any], *,
                       indice: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Uso de CADA columna del modelo, en una sola pasada.

    Existe para que un consumidor que necesita el uso de muchas columnas
    -perfilar el modelo, por ejemplo- no recorra el modelo entero una vez por
    columna. Es el mismo calculo que `column_dependencies` hace para una sola,
    con el mismo resolvedor: NO es una segunda implementacion con sus propias
    reglas, que es como se acaba con dos verdades distintas.

    Cubre exclusivamente `CLASES_DE_USO`. Que una columna no aparezca aqui
    significa "ninguna EXPRESION del modelo la nombra", no "no se usa".
    """
    idx = indice or build_index(model_data)
    uso: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
        clave: {c: [] for c in CLASES_DE_USO} for clave in idx["columns"]}

    def _columnas_citadas(expresion: Optional[str],
                          contexto: Optional[str]) -> Set[str]:
        refs = extract_references(expresion)
        encontradas: Set[str] = set()
        for r in refs["columns"] + refs["unqualified"]:
            resuelta = resolve_reference(r, idx, contexto)
            if resuelta["kind"] == "column":
                encontradas.add(resuelta["ref"])
        return encontradas

    for m in model_data.get("measures", []):
        for clave in _columnas_citadas(m.get("expression"), m.get("table")):
            uso[clave]["measures"].append(
                {"measure": m["name"], "table": m.get("table")})

    for t in model_data.get("tables", []):
        for c in t.get("columns", []):
            if not c.get("expression"):
                continue
            propia = f"{t['name']}[{c['name']}]"
            for clave in _columnas_citadas(c["expression"], t["name"]):
                if clave == propia:
                    continue
                uso[clave]["calculated_columns"].append({"column": propia})

    for r in model_data.get("relationships", []):
        for lado in ("from", "to"):
            clave = canonical_column(idx, r.get(f"{lado}_table") or "",
                                     r.get(f"{lado}_column") or "")
            if clave and r not in uso[clave]["relationships"]:
                uso[clave]["relationships"].append(r)

    for h in model_data.get("hierarchies", []):
        tabla_h = h.get("table")
        for nivel in h.get("levels", []) or []:
            nombre = nivel.get("column")
            if not nombre:
                continue
            if tabla_h:
                claves = [canonical_column(idx, tabla_h, nombre)]
            else:
                # El lector TMDL no dice de que tabla es la jerarquia. Sin ese
                # dato no se puede acotar, y omitirla en silencio seria peor.
                claves = _ci(idx, "columns_by_field_ci").get(
                    str(nombre).casefold(), [])
            for clave in claves:
                if clave:
                    uso[clave]["hierarchies"].append(
                        {"hierarchy": h.get("name"), "table": tabla_h})
    return uso


def column_dependencies(model_data: Dict[str, Any], tabla: str,
                        columna: str) -> Dict[str, Any]:
    """Que medidas, columnas calculadas, relaciones y jerarquias la usan."""
    indice = build_index(model_data)
    clave = canonical_column(indice, tabla, columna)
    if clave is None:
        from horizun_pbi_mcp.powerbi.errors import TableNotFoundError

        raise TableNotFoundError(
            f"La columna '{tabla}[{columna}]' no existe en el modelo.",
            details={"table": tabla, "column": columna})
    # A partir de aqui se trabaja con los nombres CANONICOS del modelo, no con
    # los que escribio quien llamo.
    tabla = indice["columns"][clave]["table"]
    columna = indice["columns"][clave]["name"]
    clave_cf = clave.casefold()
    columna_cf = columna.casefold()

    def usa(expresion: Optional[str]) -> bool:
        refs = extract_references(expresion)
        return (clave_cf in {r.casefold() for r in refs["columns"]}
                or columna_cf in {r.casefold() for r in refs["unqualified"]})

    def _igual(a: Any, b: str) -> bool:
        return str(a or "").casefold() == b

    medidas = [{"measure": m["name"], "table": m.get("table")}
               for m in model_data.get("measures", []) if usa(m.get("expression"))]
    calculadas = [
        {"column": f"{t['name']}[{c['name']}]"}
        for t in model_data.get("tables", []) for c in t.get("columns", [])
        if c.get("expression") and usa(c["expression"])
        and f"{t['name']}[{c['name']}]".casefold() != clave_cf]
    relaciones = [
        r for r in model_data.get("relationships", [])
        if (_igual(r.get("from_table"), tabla.casefold())
            and _igual(r.get("from_column"), columna_cf))
        or (_igual(r.get("to_table"), tabla.casefold())
            and _igual(r.get("to_column"), columna_cf))]
    jerarquias = [
        {"hierarchy": h["name"], "table": h.get("table")}
        for h in model_data.get("hierarchies", [])
        if any(_igual(l.get("column"), columna_cf) for l in h.get("levels", []))]

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
        from horizun_pbi_mcp.powerbi.errors import ValidationError

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
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    indice = build_index(model_data)
    k = (kind or "").lower()
    if k == "table":
        t = indice["tables"].get(canonical_table(indice, name) or name)
        if not t:
            raise ValidationError(f"No existe la tabla '{name}'.",
                                  details={"available": sorted(indice["tables"])[:50]})
        return {"kind": "table", "object": t}
    if k == "measure":
        m = indice["measures"].get(canonical_measure(indice, name) or name)
        if not m:
            raise ValidationError(f"No existe la medida '{name}'.",
                                  details={"available": sorted(indice["measures"])[:50]})
        return {"kind": "measure", "object": m,
                "references": extract_references(m.get("expression"))}
    if k == "column":
        canonica = _unico(_ci(indice, "columns_ci").get(str(name).casefold()))
        c = indice["columns"].get(canonica or name)
        if not c:
            raise ValidationError(
                f"No existe la columna '{name}'. Usa la forma 'Tabla[Columna]'.",
                details={"available": sorted(indice["columns"])[:50]})
        return {"kind": "column", "object": c}
    raise ValidationError(f"kind invalido: '{kind}'. Usa table|column|measure.")


#: Detalle admitido por las vistas de inventario.
DETALLE_COMPLETO = "full"
DETALLE_RESUMEN = "summary"
DETALLES = (DETALLE_COMPLETO, DETALLE_RESUMEN)


def _validar_detalle(detail: str) -> str:
    if detail not in DETALLES:
        raise ValidationError(
            f"detail='{detail}' no existe. Usa {' o '.join(DETALLES)}.")
    return detail


def _filtrar_tablas(nombres_pedidos: Optional[Iterable[str]],
                    disponibles: Iterable[str]) -> Optional[Set[str]]:
    """Resuelve el filtro `tables`, fallando con los nombres reales a la vista.

    Sin esto, pedir una tabla mal escrita devolvia una lista vacia y parecia que
    el modelo no tenia nada. Un inventario vacio y un nombre equivocado tienen
    que distinguirse.
    """
    if nombres_pedidos is None:
        return None
    disponibles = list(disponibles)
    indice = {n.casefold(): n for n in disponibles}
    resueltos, faltan = set(), []
    for pedido in nombres_pedidos:
        real = indice.get(str(pedido).casefold())
        if real is None:
            faltan.append(str(pedido))
        else:
            resueltos.add(real)
    if faltan:
        raise TableNotFoundError(
            f"No existe(n) en el modelo: {faltan}.",
            details={"available_tables": sorted(disponibles)})
    return resueltos


def tables_view(model_data: Dict[str, Any], *,
                tables: Optional[Iterable[str]] = None,
                detail: str = DETALLE_COMPLETO) -> Dict[str, Any]:
    """Inventario de tablas, acotable por nombre y por nivel de detalle.

    El motivo es de coste, no de estetica: con `detail='full'` un modelo de
    siete tablas ocupa ~28.000 caracteres, y uno corporativo de cuarenta se come
    una parte grande de la ventana de contexto en UNA llamada. `summary`
    conserva la forma de la respuesta y sustituye la lista de columnas por su
    recuento, que es lo que hace falta para decidir a que tabla entrar.

    `count` sigue siendo el numero de tablas DEVUELTAS, como siempre. Cuando el
    filtro recorta, `total_tables` dice cuantas hay en el modelo, para que no
    parezca que el resto desaparecio.
    """
    _validar_detalle(detail)
    todas = model_data.get("tables", [])
    elegidas = _filtrar_tablas(tables, (t["name"] for t in todas))
    salida = [t for t in todas if elegidas is None or t["name"] in elegidas]

    if detail == DETALLE_RESUMEN:
        salida = [{"name": t["name"],
                   "is_hidden": t.get("is_hidden"),
                   "column_count": t.get("column_count", len(t.get("columns", []))),
                   "measure_count": t.get("measure_count", len(t.get("measures", [])))}
                  for t in salida]

    resultado: Dict[str, Any] = {"count": len(salida), "tables": salida,
                                 "detail": detail}
    if elegidas is not None:
        resultado["total_tables"] = len(todas)
    return resultado


def measures_view(model_data: Dict[str, Any], *,
                  tables: Optional[Iterable[str]] = None,
                  detail: str = DETALLE_COMPLETO) -> Dict[str, Any]:
    """Inventario de medidas, acotable por tabla y por nivel de detalle.

    En `summary` se omite la expresion DAX, que es el grueso del peso y casi
    nunca lo que se necesita para orientarse. Para leer el DAX de una medida
    concreta esta `pbi_get_object`, y para buscar dentro del DAX,
    `pbi_search_model`.
    """
    _validar_detalle(detail)
    todas = model_data.get("measures", [])
    if tables is not None:
        # El universo de nombres validos son las TABLAS del modelo, no las que
        # resulten tener medidas: pedir una tabla real y vacia no es un error.
        elegidas = _filtrar_tablas(tables,
                                   (t["name"] for t in model_data.get("tables", [])))
        salida = [m for m in todas if m.get("table") in elegidas]
    else:
        salida = list(todas)

    if detail == DETALLE_RESUMEN:
        salida = [{"name": m["name"], "table": m.get("table"),
                   "format_string": m.get("format_string"),
                   "display_folder": m.get("display_folder"),
                   "description": m.get("description")}
                  for m in salida]

    resultado: Dict[str, Any] = {"count": len(salida), "measures": salida,
                                 "detail": detail}
    if tables is not None:
        resultado["total_measures"] = len(todas)
    return resultado


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
    ambiguas = []
    for m in medidas:
        refs = extract_references(m.get("expression"))
        for r in refs["columns"] + refs["unqualified"]:
            resuelta = resolve_reference(r, indice, m.get("table"))
            if resuelta["kind"] == "ambiguous":
                ambiguas.append({"measure": m["name"], "reference": r,
                                 "candidates": resuelta["candidates"]})
            elif not resuelta["exists"]:
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
        # Ni rota ni resuelta: coincide con varios objetos al ignorar
        # mayusculas y minusculas. Se declara, no se elige.
        "ambiguous_references": ambiguas,
        "reference_check": {
            "method": "lexico",
            "case_insensitive": True,
            "engine_verified": False,
            "note": ("Las referencias se comparan como lo hace el motor "
                     "-sin distinguir mayusculas- pero SOLO sobre el texto de "
                     "las expresiones: nada se evaluo contra el motor."),
        },
        "bidirectional_relationships": [
            f"{r.get('from_table')} <-> {r.get('to_table')}" for r in relaciones
            if str(r.get("cross_filtering")) in ("BothDirections", "bothDirections")],
    }
