"""La consulta que hay DETRAS de un visual, y la que el cliente declara.

El .pbip no guarda datos: guarda que campos pide cada visual y en que rol.
Para exportar el CONTENIDO hay que reconstruir la consulta y correrla contra
el motor. Este modulo hace solo esa reconstruccion; ejecutar y escribir el
archivo es de `content_export`.

Dos reglas gobiernan todo lo de aqui:

1. **Antes que adivinar, se declina.** Si una proyeccion usa una agregacion
   que no se reconoce, o una jerarquia, el visual sale marcado como no
   exportable con el motivo. Un numero equivocado en un Excel no se nota:
   se firma.
2. **Los filtros van DENTRO de `SUMMARIZECOLUMNS`.** DAX prohibe usar
   `SUMMARIZECOLUMNS` en un contexto modificado por `CALCULATE`, asi que
   envolverlo en `CALCULATETABLE` falla en el motor. Van como argumentos de
   filtro, que es la forma que el motor acepta.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError

log = get_logger("content_query")

#: Codigo numerico de `Aggregation.Function` -> nombre. Solo los que se han
#: visto escritos por Desktop y cuyo equivalente en DAX es inequivoco.
_FUNCION_A_NOMBRE = {0: "sum", 1: "avg", 2: "min", 3: "max", 5: "countnonnull"}

#: Nombre de agregacion -> funcion DAX. `CountNonNull` es `COUNTA` y no
#: `COUNT`: `COUNT` solo cuenta numeros, y la columna puede ser de texto.
_AGREGACION_A_DAX = {
    "sum": "SUM", "avg": "AVERAGE", "average": "AVERAGE",
    "min": "MIN", "max": "MAX", "countnonnull": "COUNTA",
}

#: `Sum(Tabla.Columna)` dentro de `queryRef`: es el nombre que escribio
#: Desktop, y vale mas que el codigo numerico cuando los dos estan.
_QUERYREF_AGREGADO = re.compile(r"^([A-Za-z]+)\(")


def _partes(ref: str) -> Tuple[str, str]:
    """`Tabla[Columna]` -> `('Tabla', 'Columna')`."""
    texto = str(ref or "").strip()
    if "[" not in texto or not texto.endswith("]"):
        raise ValidationError(
            f"Referencia de campo invalida: {ref!r}. Se espera 'Tabla[Columna]'.",
            details={"field": ref})
    tabla = texto[:texto.index("[")].strip()
    columna = texto[texto.index("[") + 1:-1]
    if not tabla or not columna:
        raise ValidationError(f"Referencia de campo incompleta: {ref!r}.",
                              details={"field": ref})
    return tabla, columna


def columna_dax(ref: str) -> str:
    """`Tabla[Columna]` -> `'Tabla'[Columna]`, con las comillas escapadas."""
    tabla, columna = _partes(ref)
    return "'" + tabla.replace("'", "''") + "'[" + columna.replace("]", "]]") + "]"


def medida_dax(nombre: str) -> str:
    """Nombre de medida -> `[Medida]`. Acepta `Tabla[Medida]` y la desnuda."""
    texto = str(nombre or "").strip()
    if "[" in texto and texto.endswith("]"):
        texto = texto[texto.index("[") + 1:-1]
    if not texto:
        raise ValidationError("Nombre de medida vacio.")
    return "[" + texto.replace("]", "]]") + "]"


def literal_dax(valor: Any) -> str:
    """Valor de filtro -> literal DAX."""
    if valor is None:
        return "BLANK()"
    if isinstance(valor, bool):
        return "TRUE()" if valor else "FALSE()"
    if isinstance(valor, (int, float)):
        return repr(valor)
    return '"' + str(valor).replace('"', '""') + '"'


def _alias(texto: str, usados: set) -> str:
    """Nombre de columna de salida, unico y sin comillas que rompan el DAX."""
    base = str(texto or "Valor").replace('"', "'").strip() or "Valor"
    candidato, n = base, 2
    while candidato.lower() in usados:
        candidato, n = f"{base} {n}", n + 1
    usados.add(candidato.lower())
    return candidato


def filtro_dax(filtro: Dict[str, Any]) -> str:
    """Filtro categorico traducido -> tabla de filtro para SUMMARIZECOLUMNS."""
    referencia = filtro.get("field")
    valores = list(filtro.get("values") or [])
    if not referencia or not valores:
        raise ValidationError(
            "Un filtro necesita 'field' y al menos un valor.",
            details={"filter": filtro})
    columna = columna_dax(referencia)
    lista = ", ".join(literal_dax(v) for v in valores)
    condicion = f"{columna} IN {{{lista}}}"
    if filtro.get("exclude"):
        condicion = f"NOT ({condicion})"
    # ALL() sobre la columna, no sobre la tabla: acotar la columna sin arrasar
    # con el resto del contexto es justo lo que hace el panel de filtros.
    return f"FILTER(ALL({columna}), {condicion})"


def construir_dax(dimensiones: Sequence[str], medidas: Sequence[Dict[str, str]],
                  filtros: Sequence[Dict[str, Any]] = (),
                  *, top_n: Optional[int] = None,
                  order_desc: bool = True) -> str:
    """`EVALUATE SUMMARIZECOLUMNS(...)` con filtros, orden y tope.

    `medidas`: [{'alias': 'Total', 'expr': '[Total]'}]. El alias es el titulo
    de la columna en el archivo exportado.
    """
    if not dimensiones and not medidas:
        raise ValidationError(
            "Una consulta necesita al menos una dimension o una medida.")
    partes: List[str] = [columna_dax(d) for d in dimensiones]
    partes += [filtro_dax(f) for f in filtros]
    for m in medidas:
        partes.append(f'"{m["alias"]}", {m["expr"]}')
    consulta = "SUMMARIZECOLUMNS(\n    " + ",\n    ".join(partes) + "\n)"

    if top_n:
        # La medida de existencia no sirve como criterio: vale 1 en todas las
        # filas y el corte quedaria al azar.
        reales = [m for m in medidas if not m.get("aux")]
        if not reales:
            raise ValidationError(
                "`top_n` necesita una medida por la cual ordenar el corte.")
        criterio = f'[{reales[0]["alias"]}]'
        consulta = (f"TOPN({int(top_n)}, {consulta}, {criterio}, "
                    f"{'DESC' if order_desc else 'ASC'})")

    orden = ""
    if dimensiones:
        orden = "\nORDER BY " + ", ".join(columna_dax(d) for d in dimensiones)
    elif top_n:
        orden = f"\nORDER BY {criterio} " + ("DESC" if order_desc else "ASC")
    return f"EVALUATE\n{consulta}{orden}"


#: Alias de la medida auxiliar que fuerza la relacion. Se quita del archivo
#: antes de entregarlo: es andamiaje, no un dato del cliente.
ALIAS_EXISTENCIA = "__existe"


def tabla_de_hechos(tablas: Sequence[str],
                    relaciones: Sequence[Dict[str, Any]]) -> Optional[str]:
    """La tabla del lado MUCHOS de la que cuelgan todas las demas.

    En una relacion de Power BI el lado `from` es el de muchos. Si desde una
    de las tablas implicadas se llega a todas las otras siguiendo aristas
    muchos -> uno, esa es la que define que combinaciones existen.
    """
    involucradas = [t for t in dict.fromkeys(tablas) if t]
    if len(involucradas) < 2:
        return involucradas[0] if involucradas else None

    aristas: Dict[str, set] = {}
    for r in relaciones or []:
        if not r.get("is_active", True):
            continue
        origen, destino = r.get("from_table"), r.get("to_table")
        if origen and destino:
            aristas.setdefault(origen, set()).add(destino)

    def alcanzables(inicio: str) -> set:
        vistas, pila = {inicio}, [inicio]
        while pila:
            for siguiente in aristas.get(pila.pop(), ()):
                if siguiente not in vistas:
                    vistas.add(siguiente)
                    pila.append(siguiente)
        return vistas

    candidatas = [t for t in involucradas
                  if set(involucradas).issubset(alcanzables(t))]
    # Si hay dos, el modelo tiene un ciclo o relaciones bidireccionales: no se
    # elige una por sorteo, se declina mas arriba.
    return candidatas[0] if len(candidatas) == 1 else None


def _tablas_de(referencias: Sequence[str]) -> List[str]:
    return [_partes(r)[0] for r in referencias]


def _medida_de_existencia(tabla: str) -> Dict[str, str]:
    """Fuerza a `SUMMARIZECOLUMNS` a respetar la relacion.

    Sin una medida que apunte al lado de muchos, `SUMMARIZECOLUMNS` CRUZA las
    columnas de tablas distintas: 20 riesgos por 20 medidas dan 400 filas
    donde el visual muestra 20. Comprobado contra el motor.
    """
    return {"alias": ALIAS_EXISTENCIA,
            "expr": f"CALCULATE(COUNTROWS({_tabla_dax(tabla)}))", "aux": True}


def _tabla_dax(tabla: str) -> str:
    return "'" + str(tabla).replace("'", "''") + "'"


def _resolver_cruce(dimensiones: Sequence[str], medidas: List[Dict[str, str]],
                    relaciones: Sequence[Dict[str, Any]]
                    ) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Anade la medida de existencia si las columnas cruzan tablas."""
    tablas = list(dict.fromkeys(_tablas_de(dimensiones)))
    if len(tablas) < 2:
        return medidas, None
    hechos = tabla_de_hechos(tablas, relaciones)
    if not hechos:
        return medidas, (
            "Las columnas vienen de las tablas " + ", ".join(tablas) +
            ", y no hay una sola tabla de hechos de la que cuelguen todas. "
            "Sin eso el resultado seria un producto cartesiano, con mas filas "
            "de las que muestra el visual.")
    return list(medidas) + [_medida_de_existencia(hechos)], None


def _agregacion_de(proyeccion: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """Nombre de la agregacion de una proyeccion, y el motivo si no se sabe."""
    referencia = str(proyeccion.get("queryRef") or "")
    coincidencia = _QUERYREF_AGREGADO.match(referencia)
    nombre_ref = coincidencia.group(1).lower() if coincidencia else None
    nombre_codigo = _FUNCION_A_NOMBRE.get(proyeccion.get("aggregation"))

    # El nombre que escribio Desktop manda; el codigo solo confirma. Si los
    # dos estan y no coinciden, no se elige: se declina.
    if nombre_ref and nombre_codigo and nombre_ref != nombre_codigo:
        return None, (f"La proyeccion dice '{nombre_ref}' en queryRef y "
                      f"'{nombre_codigo}' en el codigo de agregacion.")
    nombre = nombre_ref or nombre_codigo
    if not nombre:
        return None, (f"Agregacion desconocida (codigo "
                      f"{proyeccion.get('aggregation')!r}).")
    if nombre not in _AGREGACION_A_DAX:
        return None, f"Agregacion '{nombre}' sin equivalente exacto en DAX."
    return nombre, None


def plan_de_visual(visual: Dict[str, Any],
                   filtros: Sequence[Dict[str, Any]] = (),
                   relaciones: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Convierte un visual leido del PBIR en un plan de consulta.

    Devuelve siempre un plan; si no es exportable, `exportable` es False y
    `reason` dice por que. Nunca devuelve una consulta a medias.
    """
    plan: Dict[str, Any] = {
        "visual_id": visual.get("id"), "visual_type": visual.get("type"),
        "title": visual.get("title") or visual.get("id"),
        "dimensions": [], "measures": [], "filters": list(filtros),
        "exportable": False, "reason": None, "dax": None,
    }
    roles = visual.get("fields") or {}
    if not roles:
        plan["reason"] = ("El visual no consulta datos (no tiene campos): "
                          "cuadro de texto, imagen o forma.")
        return plan

    usados: set = set()
    dimensiones: List[str] = []
    medidas: List[Dict[str, str]] = []
    for rol, proyecciones in roles.items():
        for proyeccion in proyecciones or []:
            tipo = proyeccion.get("kind")
            referencia = proyeccion.get("ref")
            if tipo == "measure" and referencia:
                medidas.append({"alias": _alias(
                    proyeccion.get("nativeQueryRef") or
                    _partes_medida(referencia), usados),
                    "expr": medida_dax(referencia), "role": rol})
            elif tipo == "column" and referencia and proyeccion.get("aggregation") is not None:
                nombre, motivo = _agregacion_de(proyeccion)
                if not nombre:
                    plan["reason"] = f"{motivo} Campo: {referencia}."
                    return plan
                medidas.append({"alias": _alias(
                    proyeccion.get("nativeQueryRef") or
                    f"{nombre.upper()} de {referencia}", usados),
                    "expr": f"{_AGREGACION_A_DAX[nombre]}({columna_dax(referencia)})",
                    "role": rol})
            elif tipo == "column" and referencia:
                if referencia not in dimensiones:
                    dimensiones.append(referencia)
            else:
                plan["reason"] = (
                    f"El campo de rol '{rol}' es de tipo '{tipo}' y no se "
                    "sabe llevar a una consulta tabular (jerarquias y "
                    "expresiones a medida quedan fuera).")
                return plan

    if not dimensiones and not medidas:
        plan["reason"] = "El visual no proyecta ni columnas ni medidas."
        return plan

    medidas, motivo = _resolver_cruce(dimensiones, medidas, relaciones)
    if motivo:
        plan["reason"] = motivo
        return plan

    plan["dimensions"] = dimensiones
    plan["measures"] = medidas
    plan["dax"] = construir_dax(dimensiones, medidas, filtros)
    plan["exportable"] = True
    return plan


def _partes_medida(referencia: str) -> str:
    texto = str(referencia)
    return texto[texto.index("[") + 1:-1] if "[" in texto and texto.endswith("]") else texto


def plan_declarado(spec: Dict[str, Any],
                   relaciones: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Plan a partir de lo que el cliente pide, sin referirse a un visual.

    `spec`: {name, rows: ['Tabla[Columna]'], values: ['Medida'],
             filters: [{field, values, exclude?}], top_n?, order_desc?}
    """
    if not isinstance(spec, dict):
        raise ValidationError("Cada consulta declarada debe ser un objeto.",
                              details={"query": spec})
    filas = [str(r) for r in (spec.get("rows") or []) if str(r).strip()]
    usados: set = set()
    medidas = [{"alias": _alias(_partes_medida(v), usados),
                "expr": medida_dax(v)}
               for v in (spec.get("values") or []) if str(v).strip()]
    if not filas and not medidas:
        raise ValidationError(
            "Una consulta declarada necesita 'rows' o 'values'.",
            details={"query": spec})

    filtros = []
    for f in spec.get("filters") or []:
        if not isinstance(f, dict) or not f.get("field"):
            raise ValidationError(
                "Cada filtro necesita 'field' y 'values'.", details={"filter": f})
        filtros.append({"field": f["field"], "values": list(f.get("values") or []),
                        "exclude": bool(f.get("exclude")), "state": "applied",
                        "scope": "declarado"})

    medidas, motivo = _resolver_cruce(filas, medidas, relaciones)
    if motivo:
        raise ValidationError(motivo, details={"query": spec.get("name")})

    nombre = str(spec.get("name") or "").strip() or "Consulta"
    return {"visual_id": None, "visual_type": "declarado", "title": nombre,
            "dimensions": filas, "measures": medidas, "filters": filtros,
            "exportable": True, "reason": None,
            "dax": construir_dax(filas, medidas, filtros,
                                 top_n=spec.get("top_n"),
                                 order_desc=bool(spec.get("order_desc", True)))}
