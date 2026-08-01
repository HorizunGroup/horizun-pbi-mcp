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
#: Que hacer con los vacios. 'asZero' los pinta como cero; 'specificColor'
#: exige un color aparte; 'none' los deja sin pintar.
ESTRATEGIAS_NULOS = ("asZero", "none", "specificColor")


class ConditionalFormatError(PowerBIMCPError):
    code = "conditional_format_error"


def _color(valor: str) -> Dict[str, Any]:
    return {"color": {"Literal": {"Value": "'" + str(valor).replace("'", "''") + "'"}}}


def _validar_color(valor: str, etiqueta: str) -> str:
    if not (isinstance(valor, str) and valor.startswith("#") and len(valor) in (4, 7)):
        raise ConditionalFormatError(
            f"{etiqueta} debe ser un color #RRGGBB; se recibio {valor!r}.")
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


def _selector_todas_las_filas() -> Dict[str, Any]:
    """Aplica la regla a todas las filas, no solo a la primera."""
    return {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}


def apply_to_visual(visual: Dict[str, Any], field: Dict[str, Any],
                    min_color: str, max_color: str, *,
                    target: str = "background",
                    mid_color: Optional[str] = None,
                    null_strategy: str = "asZero") -> Dict[str, Any]:
    """Añade la regla al visual (se modifica en el sitio) y describe el cambio.

    Si ya habia una regla en el mismo destino se sustituye: dos degradados
    sobre la misma propiedad no se suman, se pisan, y dejar las dos escritas
    solo haria impredecible cual gana.
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

    regla = build_fill_rule(field, min_color, max_color, mid_color, null_strategy)
    objetos = nodo.setdefault("objects", {})
    bloques: List[Dict[str, Any]] = objetos.setdefault(grupo, [])

    selector = _selector_todas_las_filas()
    reemplazado = False
    for bloque in bloques:
        props = bloque.get("properties") or {}
        if propiedad in props:
            props[propiedad] = {"solid": regla}
            bloque["selector"] = selector
            reemplazado = True
            break
    if not reemplazado:
        bloques.append({"properties": {propiedad: {"solid": regla}},
                        "selector": selector})

    log.info("Formato condicional en %s.%s (%s)", grupo, propiedad,
             "sustituido" if reemplazado else "anadido")
    return {"target": clave, "object_group": grupo, "property": propiedad,
            "replaced": reemplazado,
            "gradient": "linearGradient3" if mid_color else "linearGradient2",
            "colors": {"min": min_color, "mid": mid_color, "max": max_color},
            "null_strategy": null_strategy}
