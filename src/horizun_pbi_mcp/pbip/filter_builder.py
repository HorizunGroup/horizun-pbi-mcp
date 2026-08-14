"""Filtros de informe/pagina/visual e interacciones entre visuales.

Un filtro PBIR tiene dos mitades que es facil confundir:

- `field`, la columna o medida sobre la que se filtra, en la forma de campo
  normal (`SourceRef.Entity`);
- `filter`, una consulta semantica COMPLETA con su propio `From` y su `Where`,
  donde las tablas se referencian por ALIAS (`SourceRef.Source`), no por
  nombre. Escribir ahi el nombre de la tabla es el error tipico y Power BI lo
  rechaza sin explicar por que.

Las interacciones son mucho mas simples: una lista en la pagina que dice, por
cada par de visuales, si el primero filtra al segundo.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("filter_builder")

#: Como interactua un visual con otro cuando se selecciona algo en el.
#:
#: Son los cuatro valores de `VisualInteractionFilterType` en el esquema
#: oficial de `page/2.1.0`, ni uno mas. Antes aqui ponia
#: `("NoFilter", "Filter", "Highlight")`: dos de los tres NO existen en PBIR y
#: producian una pagina que el esquema rechaza. Nadie lo habia notado porque no
#: habia forma de escribir una interaccion desde un page spec —los ids de los
#: visuales los genera el compilador— y todos los generadores del repositorio
#: pasaban una lista vacia.
INTERACCIONES = ("Default", "DataFilter", "HighlightFilter", "NoFilter")

#: Los nombres antiguos y los naturales, hacia el valor que PBIR entiende.
_ALIAS_INTERACCION = {
    "filter": "DataFilter", "datafilter": "DataFilter",
    "highlight": "HighlightFilter", "highlightfilter": "HighlightFilter",
    "none": "NoFilter", "nofilter": "NoFilter", "off": "NoFilter",
    "default": "Default", "auto": "Default",
}
#: Tipos de filtro que este servidor sabe CONSTRUIR. Otros se pueden pasar
#: tal cual con `raw`, pero no se generan a partir de valores.
TIPOS = ("Categorical", "Advanced", "TopN", "Range", "RelativeDate", "Passthrough")

#: `ComparisonKind` del PBIR, con los nombres y los simbolos que un humano
#: escribiria. Es la mitad que faltaba para filtrar por una MEDIDA, que es como
#: se encadenan dos slicers de dimension cuando varias tablas de hechos cuelgan
#: de las mismas dimensiones y una relacion bidireccional crearia ambiguedad.
COMPARACIONES = {
    "equal": 0, "=": 0, "==": 0, "eq": 0,
    "greaterthan": 1, ">": 1, "gt": 1,
    "greaterthanorequal": 2, ">=": 2, "gte": 2, "ge": 2,
    "lessthan": 3, "<": 3, "lt": 3,
    "lessthanorequal": 4, "<=": 4, "lte": 4, "le": 4,
}
#: Se expresa como `Not(Equal)`: el PBIR no tiene un ComparisonKind propio.
_DISTINTO = {"notequal", "!=", "<>", "ne", "neq"}


class FilterBuildError(PowerBIMCPError):
    code = "filter_build_error"


def _alias(tabla: str) -> str:
    """Alias corto y estable para una tabla dentro de la consulta del filtro."""
    limpio = "".join(c for c in str(tabla) if c.isalnum()) or "t"
    return limpio[0].lower()


def _literal(valor: Any) -> Dict[str, Any]:
    if valor is None:
        return {"Literal": {"Value": "null"}}
    if isinstance(valor, bool):
        return {"Literal": {"Value": "true" if valor else "false"}}
    if isinstance(valor, int):
        return {"Literal": {"Value": f"{valor}L"}}
    if isinstance(valor, float):
        return {"Literal": {"Value": f"{valor}D"}}
    return {"Literal": {"Value": "'" + str(valor).replace("'", "''") + "'"}}


def _campo_por_alias(tabla: str, columna: str) -> Dict[str, Any]:
    return {"Column": {"Expression": {"SourceRef": {"Source": _alias(tabla)}},
                       "Property": columna}}


def _medida_por_alias(tabla: str, medida: str) -> Dict[str, Any]:
    return {"Measure": {"Expression": {"SourceRef": {"Source": _alias(tabla)}},
                        "Property": medida}}


def build_measure_comparison(table: str, measure: str, condition: str,
                             value: Any) -> Dict[str, Any]:
    """Filtro de nivel visual sobre una MEDIDA: "[Medida] > 0".

    Es el caso canonico para encadenar slicers de dimension (al filtrar por
    proyecto, que el slicer de responsable muestre solo a los suyos). Con
    varias tablas de hechos colgando de las mismas dimensiones no se puede
    resolver con relaciones bidireccionales -crean ambiguedad-, asi que la via
    correcta es esta.
    """
    clave = str(condition or "").strip().casefold()
    negar = clave in _DISTINTO
    kind = 0 if negar else COMPARACIONES.get(clave)
    if kind is None:
        raise FilterBuildError(
            f"Comparacion no soportada: '{condition}'. Usa una de "
            f"{sorted(set(COMPARACIONES) | _DISTINTO)}.",
            details={"valid": sorted(set(COMPARACIONES) | _DISTINTO)})
    condicion: Dict[str, Any] = {"Comparison": {
        "ComparisonKind": kind,
        "Left": _medida_por_alias(table, measure),
        "Right": _literal(value)}}
    if negar:
        condicion = {"Not": {"Expression": condicion}}
    return {
        "Version": 2,
        "From": [{"Name": _alias(table), "Entity": table, "Type": 0}],
        "Where": [{"Condition": condicion}],
    }


def _nombre_estable(contenido: Dict[str, Any]) -> str:
    semilla = json.dumps(contenido, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(semilla.encode("utf-8")).hexdigest()[:20]


def build_categorical(table: str, column: str, values: List[Any], *,
                      exclude: bool = False) -> Dict[str, Any]:
    """Filtro de lista: "la columna esta (o no esta) en estos valores".

    Es el filtro mas comun y el que Power BI crea al marcar casillas.
    """
    if not values:
        raise FilterBuildError(
            "Un filtro categorico necesita al menos un valor. Para 'no vacio' "
            "usa exclude=True con values=[None].")
    campo = _campo_por_alias(table, column)
    condicion: Dict[str, Any] = {
        "In": {"Expressions": [campo],
               "Values": [[_literal(v)] for v in values]}}
    if exclude:
        condicion = {"Not": {"Expression": condicion}}
    return {
        "Version": 2,
        "From": [{"Name": _alias(table), "Entity": table, "Type": 0}],
        "Where": [{"Condition": condicion}],
    }


def _partir_referencia(referencia: Any, clave: str) -> tuple:
    texto = str(referencia or "").strip()
    if "[" not in texto or not texto.endswith("]"):
        raise FilterBuildError(
            f"'{clave}' debe tener la forma 'Tabla[{'Medida' if clave == 'measure' else 'Columna'}]'; "
            f"se recibio {referencia!r}.")
    tabla = texto[:texto.index("[")].strip()
    nombre = texto[texto.index("[") + 1:-1]
    if not tabla or not nombre:
        raise FilterBuildError(f"Referencia de campo incompleta: {texto!r}.")
    return tabla, nombre


def build_filter(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Un elemento de `filterConfig.filters` a partir de una descripcion simple.

    `spec`: {field: 'Tabla[Columna]', type?, values?, exclude?, raw?,
             hidden?, locked?, display_name?}
    o, para filtrar por una MEDIDA:
    `{measure: 'Tabla[Medida]', condition: 'GreaterThan', value: 0}`.

    Con `raw` se pasa una consulta semantica ya construida, sin interpretarla:
    la valvula de escape para lo que este constructor no cubre.
    """
    if spec.get("measure"):
        if spec.get("field"):
            raise FilterBuildError(
                "Un filtro es de columna ('field') o de medida ('measure'), no "
                "de las dos cosas.")
        return _build_measure_filter(spec)

    tabla, columna = _partir_referencia(spec.get("field"), "field")

    tipo = spec.get("type") or ("Categorical" if spec.get("values") else "Advanced")
    if tipo not in TIPOS:
        raise FilterBuildError(
            f"Tipo de filtro no soportado: '{tipo}'. Usa uno de {list(TIPOS)}.")

    salida: Dict[str, Any] = {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": tabla}},
                             "Property": columna}},
        "type": tipo,
    }
    if spec.get("raw") is not None:
        salida["filter"] = spec["raw"]
    elif spec.get("values"):
        salida["filter"] = build_categorical(tabla, columna, list(spec["values"]),
                                             exclude=bool(spec.get("exclude")))
    # Sin `filter` el elemento describe un filtro presente pero sin seleccion:
    # es lo que Power BI escribe cuando el campo esta en el panel sin acotar.

    return _decorar(salida, spec)


def _build_measure_filter(spec: Dict[str, Any]) -> Dict[str, Any]:
    """La mitad `Advanced` de un filtro de medida, con su `field` de medida.

    El `field` va por NOMBRE de tabla (`SourceRef.Entity`) y la consulta de
    dentro por ALIAS (`SourceRef.Source`), igual que en los de columna: es la
    confusion que hace que Power BI ignore el filtro sin decir nada.
    """
    tabla, medida = _partir_referencia(spec.get("measure"), "measure")
    tipo = spec.get("type") or "Advanced"
    if tipo not in TIPOS:
        raise FilterBuildError(
            f"Tipo de filtro no soportado: '{tipo}'. Usa uno de {list(TIPOS)}.")

    salida: Dict[str, Any] = {
        "field": {"Measure": {"Expression": {"SourceRef": {"Entity": tabla}},
                              "Property": medida}},
        "type": tipo,
    }
    if spec.get("raw") is not None:
        salida["filter"] = spec["raw"]
    elif spec.get("condition"):
        salida["filter"] = build_measure_comparison(
            tabla, medida, spec["condition"], spec.get("value"))
    elif "value" in spec:
        raise FilterBuildError(
            "Un filtro de medida con 'value' necesita 'condition' (por ejemplo "
            "'GreaterThan'); si no, no se sabe que comparar.")
    # `howCreated: User` es lo que Desktop escribe para un filtro que el usuario
    # anade con un campo que el visual no proyecta -justo este caso-.
    salida["howCreated"] = spec.get("how_created") or "User"
    return _decorar(salida, spec)


def _decorar(salida: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Metadatos comunes del contenedor de filtro y su nombre estable."""
    if spec.get("display_name"):
        salida["displayName"] = spec["display_name"]
    if spec.get("hidden"):
        salida["isHiddenInViewMode"] = True
    if spec.get("locked"):
        salida["isLockedInViewMode"] = True
    salida["name"] = spec.get("name") or _nombre_estable(salida)
    return salida


def build_filter_config(specs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """`filterConfig` completo para un informe, una pagina o un visual."""
    filtros = [build_filter(s) for s in specs or [] if isinstance(s, dict)]
    return {"filters": filtros} if filtros else None


def build_interactions(specs: List[Dict[str, Any]],
                       visuales_validos: Optional[List[str]] = None
                       ) -> Optional[List[Dict[str, Any]]]:
    """`visualInteractions` de una pagina.

    Cada entrada dice que le hace `source` a `target` al seleccionar en el.
    Se comprueba que ambos existan: una interaccion que apunta a un visual
    inexistente no falla al abrir, simplemente no hace nada, y encontrarlo
    despues es muy caro.
    """
    salida = []
    conocidos = set(visuales_validos or [])
    for s in specs or []:
        if not isinstance(s, dict):
            continue
        origen, destino = s.get("source"), s.get("target")
        tipo = s.get("type", "NoFilter")
        # `in (None, "")` y no `not origen`: un id que se evalue como falso
        # —el indice 0 antes de resolverse, por ejemplo— es una referencia
        # legitima, y rechazarla aqui es muy dificil de ver desde fuera.
        if origen in (None, "") or destino in (None, ""):
            raise FilterBuildError(
                "Una interaccion necesita 'source' y 'target'. Desde un page "
                "spec puedes senalar cada visual por su posicion, su 'id' o su "
                "titulo; en una pagina existente, por su id.")
        # PBIR escribe el tipo en PascalCase; se acepta como venga y se emite
        # como toca. Que 'filter' y 'Filter' sean cosas distintas es una trampa
        # sin ninguna ventaja.
        canonico = _ALIAS_INTERACCION.get(str(tipo).strip().casefold())
        if canonico is None:
            raise FilterBuildError(
                f"Interaccion no soportada: '{tipo}'. Usa una de "
                f"{list(INTERACCIONES)}.",
                details={"valid": list(INTERACCIONES),
                         "aliases": sorted(_ALIAS_INTERACCION)})
        tipo = canonico
        if conocidos:
            faltan = [v for v in (origen, destino) if v not in conocidos]
            if faltan:
                raise FilterBuildError(
                    f"La interaccion apunta a visuales que no existen en la "
                    f"pagina: {faltan}.", details={"known": sorted(conocidos)})
        salida.append({"source": origen, "target": destino, "type": tipo})
    return salida or None
