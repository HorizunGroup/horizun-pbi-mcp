"""Formato condicional: color que sale del dato, no de una eleccion fija.

Es lo que convierte una matriz de numeros en un mapa de calor. En PBIR no es
una propiedad aparte: es una EXPRESION (`FillRule`) puesta donde iria un color
literal, con dos piezas —de que medida se lee el valor (`Input`) y como se
traduce a color (`linearGradient2`/`3`)—.

Donde se pone depende de lo que se quiera pintar:

- fondo de celda en tabla/matriz -> `objects.values[].properties.backColor`
- color del texto                -> `objects.values[].properties.fontColor`
- barras y puntos de un grafico  -> `objects.dataPoint[].properties.fill`

La estructura se copio de informes reales; el `selector` con
`dataViewWildcard` es lo que hace que la regla se aplique a todas las filas y
no solo a una.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("conditional_format")

#: Donde vive cada destino: (grupo de objects, propiedad).
DESTINOS = {
    "background": ("values", "backColor"),
    "font": ("values", "fontColor"),
    "bars": ("dataPoint", "fill"),
    "datapoint": ("dataPoint", "fill"),
}

# `objects` no tiene un esquema de propiedades estricto. Por eso no basta con
# escribir un grupo bien formado: si el grupo no existe para ESE visual,
# Desktop lo ignora. Esta tabla sale del catalogo oficial del CLI de Microsoft
# (`catalog describe <visualType>`), campo `formattingObjects`.
_TIPOS_POR_GRUPO = {
    "values": frozenset({"table", "tableEx", "matrix", "pivotTable"}),
    "dataPoint": frozenset({
        "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart",
        "barChart", "clusteredBarChart", "stackedBarChart",
        "hundredPercentStackedBarChart", "columnChart",
        "clusteredColumnChart", "stackedColumnChart",
        "hundredPercentStackedColumnChart", "lineChart", "pieChart",
        "donutChart", "scatterChart", "treemap", "funnel", "gauge",
        "ribbonChart", "lineClusteredColumnComboChart",
        "lineStackedColumnComboChart",
    }),
}
#: Que hacer con los vacios. 'asZero' los pinta como cero; 'specificColor'
#: exige un color aparte; 'none' los deja sin pintar.
ESTRATEGIAS_NULOS = ("asZero", "none", "specificColor")
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{3}(?:[0-9A-Fa-f]{3})?$")


class ConditionalFormatError(PowerBIMCPError):
    code = "conditional_format_error"


def _color(valor: str) -> Dict[str, Any]:
    return {"color": {"Literal": {"Value": "'" + str(valor).replace("'", "''") + "'"}}}


def _validar_color(valor: str, etiqueta: str) -> str:
    if not (isinstance(valor, str) and _HEX_COLOR.fullmatch(valor)):
        raise ConditionalFormatError(
            f"{etiqueta} debe ser un color hexadecimal #RGB o #RRGGBB; "
            f"se recibio {valor!r}.")
    return valor


def build_fill_rule(field: Dict[str, Any], min_color: str, max_color: str,
                    mid_color: Optional[str] = None,
                    null_strategy: str = "asZero") -> Dict[str, Any]:
    """Expresion de color degradado a partir de un campo del modelo.

    `field` es el nodo de campo tal cual lo escribe el resto del servidor
    (`{"Measure": {...}}` o `{"Column": {...}}`), para que la referencia sea la
    misma en la consulta del visual y en la regla de color.
    """
    _validar_color(min_color, "min_color")
    _validar_color(max_color, "max_color")
    if null_strategy not in ESTRATEGIAS_NULOS:
        raise ConditionalFormatError(
            f"Estrategia de nulos no soportada: '{null_strategy}'. "
            f"Usa una de {list(ESTRATEGIAS_NULOS)}.")

    if mid_color:
        _validar_color(mid_color, "mid_color")
        gradiente = {"linearGradient3": {
            "min": _color(min_color), "mid": _color(mid_color),
            "max": _color(max_color),
            "nullColoringStrategy": {"strategy": {"Literal": {
                "Value": f"'{null_strategy}'"}}}}}
    else:
        gradiente = {"linearGradient2": {
            "min": _color(min_color), "max": _color(max_color),
            "nullColoringStrategy": {"strategy": {"Literal": {
                "Value": f"'{null_strategy}'"}}}}}

    return {"expr": {"FillRule": {"Input": copy.deepcopy(field),
                                  "FillRule": gradiente}}}


def query_ref(field: Dict[str, Any]) -> Optional[str]:
    """`Tabla.Campo` a partir del nodo de campo. None si no se reconoce.

    Es la misma forma que escribe `visual_factory` en `queryRef`, y tiene que
    serlo: es lo que empareja la regla de color con su columna del visual.
    """
    for clase in ("Measure", "Column", "Aggregation", "HierarchyLevel"):
        nodo = field.get(clase)
        if not isinstance(nodo, dict):
            continue
        propiedad = nodo.get("Property")
        if not propiedad:
            continue
        entidad = ((nodo.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
        return f"{entidad}.{propiedad}" if entidad else str(propiedad)
    return None


def _propiedad_de_color(regla: Dict[str, Any]) -> Dict[str, Any]:
    """Envuelve la regla como lo que es: el COLOR de un relleno solido.

    La forma es `{"solid": {"color": <expresion>}}`, la misma con la que
    `visual_factory` escribe un color literal. Antes se escribia
    `{"solid": <expresion>}`, sin el nivel `color`, y ahi no lo veia nadie: el
    esquema oficial declara esta parte como `additionalProperties: {}` —acepta
    cualquier cosa— asi que el validador de Microsoft daba el visto bueno y
    Power BI simplemente no pintaba nada. Se descubrio abriendo el informe y
    mirando una tabla sin colorear.
    """
    return {"solid": {"color": regla}}


def _selector(referencia: Optional[str]) -> Dict[str, Any]:
    """A que celdas se aplica la regla.

    `dataViewWildcard` la extiende a todas las filas —sin el solo pinta la
    primera—, y `metadata` la acota A UN CAMPO. Eso segundo es lo que permite
    que una matriz tenga un degradado distinto por medida: el esquema oficial
    lo describe como "defines the scope to a specific field".
    """
    selector: Dict[str, Any] = {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}
    if referencia:
        selector["metadata"] = referencia
    return selector


def apply_to_visual(visual: Dict[str, Any], field: Dict[str, Any],
                    min_color: str, max_color: str, *,
                    target: str = "background",
                    mid_color: Optional[str] = None,
                    null_strategy: str = "asZero") -> Dict[str, Any]:
    """Añade la regla al visual (se modifica en el sitio) y describe el cambio.

    Se sustituye la regla del MISMO campo, y solo esa. Antes se reemplazaba
    cualquier bloque que tuviera esa propiedad, sin mirar a que campo apuntaba:
    colorear una segunda medida borraba el degradado de la primera, y en una
    matriz de varias metricas acababa pintada solo la ultima. El rodeo conocido
    era dinamizar las metricas a filas para tener una sola medida; ya no hace
    falta.

    Dos degradados sobre la misma propiedad no se pisan mientras cada uno este
    acotado a su campo con `selector.metadata`.
    """
    clave = str(target).strip().lower()
    if clave not in DESTINOS:
        raise ConditionalFormatError(
            f"Destino no soportado: '{target}'. Usa uno de "
            f"{sorted(set(DESTINOS))} (background y font para tablas y "
            "matrices; bars para barras y columnas).")
    grupo, propiedad = DESTINOS[clave]

    nodo = visual.get("visual")
    if not isinstance(nodo, dict):
        raise ConditionalFormatError("El visual no tiene nodo 'visual'.")

    tipo = nodo.get("visualType")
    permitidos = _TIPOS_POR_GRUPO[grupo]
    if tipo not in permitidos:
        raise ConditionalFormatError(
            f"El destino '{clave}' escribe el grupo '{grupo}', que no existe "
            f"para el visual '{tipo}'. Tipos compatibles: {sorted(permitidos)}.",
            details={"target": clave, "object_group": grupo,
                     "visual_type": tipo, "compatible_visual_types":
                     sorted(permitidos)})

    regla = build_fill_rule(field, min_color, max_color, mid_color, null_strategy)
    objetos = nodo.setdefault("objects", {})
    bloques: List[Dict[str, Any]] = objetos.setdefault(grupo, [])

    referencia = query_ref(field)
    selector = _selector(referencia)
    reemplazado = False
    for bloque in bloques:
        props = bloque.get("properties") or {}
        if propiedad not in props:
            continue
        # El campo al que apunta el bloque es lo que decide si esto es la misma
        # regla o una distinta. Sin esta comparacion, colorear una medida
        # borraba el degradado de la anterior.
        if (bloque.get("selector") or {}).get("metadata") != referencia:
            continue
        props[propiedad] = _propiedad_de_color(regla)
        bloque["selector"] = selector
        reemplazado = True
        break
    if not reemplazado:
        bloques.append({"properties": {propiedad: _propiedad_de_color(regla)},
                        "selector": selector})

    log.info("Formato condicional en %s.%s sobre %s (%s)", grupo, propiedad,
             referencia or "campo sin referencia",
             "sustituido" if reemplazado else "anadido")
    return {"target": clave, "object_group": grupo, "property": propiedad,
            "field_ref": referencia, "replaced": reemplazado,
            "rules_on_property": sum(
                1 for b in bloques if propiedad in (b.get("properties") or {})),
            "gradient": "linearGradient3" if mid_color else "linearGradient2",
            "colors": {"min": min_color, "mid": mid_color, "max": max_color},
            "null_strategy": null_strategy}
