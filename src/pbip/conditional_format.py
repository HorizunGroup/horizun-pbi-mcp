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
# La compatibilidad es por PROPIEDAD, no solo por grupo. El catalogo oficial
# enumera ``values`` para ``matrix`` y ``table``, pero esos visuales clasicos no
# tienen ``values.fontColor`` (usan propiedades distintas para filas pares e
# impares). Aceptarlos producia un JSON que el esquema abierto toleraba y que
# Desktop ignoraba. La tabla contiene solo visualTypes observados en el
# catalogo 0.1.4 y/o en visual.json exportados por Desktop.
_TIPOS_POR_DESTINO = {
    "background": frozenset({"table", "tableEx", "matrix", "pivotTable"}),
    "font": frozenset({"tableEx", "pivotTable"}),
    "bars": frozenset({
        "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart",
        "barChart", "clusteredBarChart", "hundredPercentStackedBarChart",
        "columnChart", "clusteredColumnChart",
        "hundredPercentStackedColumnChart", "lineChart", "pieChart", "donutChart",
        "scatterChart", "treemap", "funnel", "gauge", "ribbonChart",
        "lineClusteredColumnComboChart", "lineStackedColumnComboChart",
    }),
    "datapoint": frozenset({
        "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart",
        "barChart", "clusteredBarChart", "hundredPercentStackedBarChart",
        "columnChart", "clusteredColumnChart",
        "hundredPercentStackedColumnChart", "lineChart", "pieChart", "donutChart",
        "scatterChart", "treemap", "funnel", "gauge", "ribbonChart",
        "lineClusteredColumnComboChart", "lineStackedColumnComboChart",
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


def _luminancia(color: str) -> float:
    """Luminancia relativa WCAG de un color ya validado."""
    _validar_color(color, "color")
    hexa = color[1:]
    if len(hexa) == 3:
        hexa = "".join(c * 2 for c in hexa)
    canales = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lineales = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                for c in canales]
    return 0.2126 * lineales[0] + 0.7152 * lineales[1] + 0.0722 * lineales[2]


def contrast_ratio(a: str, b: str) -> float:
    """Relacion de contraste WCAG entre dos colores hexadecimales."""
    la, lb = _luminancia(a), _luminancia(b)
    claro, oscuro = max(la, lb), min(la, lb)
    return (claro + 0.05) / (oscuro + 0.05)


def contrast_warnings(min_color: str, max_color: str, *, target: str,
                      theme_data: Optional[Dict[str, Any]]) -> List[str]:
    """Advierte extremos que pueden desaparecer sobre el tema activo."""
    if not isinstance(theme_data, dict):
        return []
    clave = str(target).strip().lower()
    # Si se pinta el fondo, el numero conserva la tinta del tema. Si se pinta
    # la fuente o una marca, el contraste relevante es contra la superficie.
    referencia = (theme_data.get("foreground") if clave == "background"
                  else theme_data.get("background"))
    if not (isinstance(referencia, str) and _HEX_COLOR.fullmatch(referencia)):
        return []
    avisos = []
    for extremo, color in (("min", min_color), ("max", max_color)):
        razon = contrast_ratio(color, referencia)
        if razon < 3.0:
            avisos.append(
                f"Contraste bajo en el extremo {extremo}: {color} frente a "
                f"{referencia} del tema ({razon:.2f}:1, minimo recomendado 3:1).")
    return avisos


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
    # Una columna agregada no tiene ``Property`` en el propio Aggregation: la
    # expresion interior es otro nodo Column. Antes devolviamos None y el
    # selector quedaba sin alcance aun cuando la proyeccion tenia queryRef.
    agregacion = field.get("Aggregation")
    if isinstance(agregacion, dict):
        expresion = agregacion.get("Expression")
        if isinstance(expresion, dict):
            return query_ref(expresion)

    for clase in ("Measure", "Column", "HierarchyLevel"):
        nodo = field.get(clase)
        if not isinstance(nodo, dict):
            continue
        propiedad = nodo.get("Property")
        if not propiedad:
            continue
        entidad = ((nodo.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
        return f"{entidad}.{propiedad}" if entidad else str(propiedad)
    return None


def _field_identity(field: Any) -> Optional[tuple[str, str]]:
    """Identidad (tabla, propiedad) de un nodo de campo PBIR.

    Conserva por separado la estructura original: esta funcion solo sirve
    para encontrar la proyeccion solicitada. Una vez encontrada se reutiliza
    su nodo completo, incluida ``Aggregation.Function``.
    """
    if not isinstance(field, dict):
        return None
    agregacion = field.get("Aggregation")
    if isinstance(agregacion, dict):
        return _field_identity(agregacion.get("Expression"))
    for clase in ("Measure", "Column", "HierarchyLevel"):
        nodo = field.get(clase)
        if not isinstance(nodo, dict):
            continue
        propiedad = nodo.get("Property")
        if not isinstance(propiedad, str) or not propiedad:
            continue
        entidad = ((nodo.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
        return str(entidad or ""), propiedad
    return None


def _field_signature(field: Any) -> Any:
    """Firma semantica que distingue Sum(columna) de Avg(columna)."""
    if not isinstance(field, dict):
        return None
    agregacion = field.get("Aggregation")
    if isinstance(agregacion, dict):
        return ("Aggregation", agregacion.get("Function"),
                _field_signature(agregacion.get("Expression")))
    for clase in ("Measure", "Column", "HierarchyLevel"):
        nodo = field.get(clase)
        identidad = _field_identity({clase: nodo}) if isinstance(nodo, dict) else None
        if identidad is not None:
            return (clase, *identidad)
    return None


def _fill_rule_input(value: Any) -> Optional[Dict[str, Any]]:
    try:
        entrada = value["solid"]["color"]["expr"]["FillRule"]["Input"]
    except (KeyError, TypeError):
        return None
    return entrada if isinstance(entrada, dict) else None


def resolve_projection(visual: Dict[str, Any], field_ref: str) -> Dict[str, Any]:
    """Encuentra el campo *ya usado* por el visual y devuelve su proyeccion.

    Un FillRule no debe reconstruir el campo desde una cadena. En particular,
    Desktop representa una columna numerica agregada como
    ``Aggregation(Expression=Column, Function=N)``. Convertirla otra vez en
    ``Column`` cambia la semantica del color. Tambien se reutiliza el Input de
    una regla existente cuando Desktop colorea por un campo que no se muestra.
    """
    if not isinstance(field_ref, str) or not field_ref.strip():
        raise ConditionalFormatError("La referencia del campo esta vacia.")
    texto = field_ref.strip()
    if "[" in texto and texto.endswith("]"):
        tabla_pedida = texto[:texto.index("[")].strip()
        propiedad_pedida = texto[texto.index("[") + 1:-1].strip()
    else:
        tabla_pedida = ""
        propiedad_pedida = texto.strip("[] ")
    if not propiedad_pedida:
        raise ConditionalFormatError(
            f"Referencia de campo invalida: {field_ref!r}.")

    nodo_visual = visual.get("visual")
    estados = (((nodo_visual or {}).get("query") or {}).get("queryState") or {})
    candidatos: List[Dict[str, Any]] = []
    for estado in estados.values():
        for proyeccion in (estado or {}).get("projections", []) or []:
            if not isinstance(proyeccion, dict):
                continue
            identidad = _field_identity(proyeccion.get("field"))
            if identidad is None:
                continue
            tabla, propiedad = identidad
            if propiedad.casefold() != propiedad_pedida.casefold():
                continue
            if tabla_pedida and tabla.casefold() != tabla_pedida.casefold():
                continue
            candidatos.append(proyeccion)

    # Desktop permite basar un color en un campo que no se muestra en el
    # visual. En ese caso no hay proyeccion, pero al editar una regla existente
    # su FillRule.Input conserva la estructura autoritativa (incluida la
    # agregacion). Reutilizarla es seguro; adivinarla desde el nombre, no.
    for bloques in ((nodo_visual or {}).get("objects") or {}).values():
        for bloque in bloques if isinstance(bloques, list) else []:
            for valor in (bloque.get("properties") or {}).values():
                entrada = _fill_rule_input(valor)
                if entrada is None:
                    continue
                identidad = _field_identity(entrada)
                if identidad is None:
                    continue
                tabla, propiedad = identidad
                if propiedad.casefold() != propiedad_pedida.casefold():
                    continue
                if tabla_pedida and tabla.casefold() != tabla_pedida.casefold():
                    continue
                candidatos.append({"field": entrada})

    if not candidatos:
        raise ConditionalFormatError(
            f"El campo {field_ref!r} no esta en la consulta ni en una regla "
            "existente del visual; no se puede deducir con seguridad su "
            "agregacion para el formato condicional.",
            details={"field": field_ref, "rule": "format_field_not_projected"})
    if len(candidatos) > 1:
        firmas = {_field_signature(c.get("field")) for c in candidatos}
        if len(firmas) > 1:
            raise ConditionalFormatError(
                f"El campo {field_ref!r} aparece en varias proyecciones; usa "
                "una referencia que identifique de forma unica la regla.",
                details={"field": field_ref,
                         "rule": "format_field_projection_ambiguous",
                         "matches": len(candidatos)})
    # Si la misma estructura aparece en queryState y en el FillRule existente,
    # se prefiere la proyeccion: conserva el queryRef exacto que Desktop usa
    # para selectors de tablas.
    elegido = next((c for c in candidatos if c.get("queryRef")), candidatos[0])
    return copy.deepcopy(elegido)


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
                    null_strategy: str = "asZero",
                    projection_query_ref: Optional[str] = None) -> Dict[str, Any]:
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
    permitidos = _TIPOS_POR_DESTINO[clave]
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

    referencia = projection_query_ref or query_ref(field)
    # Los visual.json exportados por Desktop acotan ``values`` a la columna
    # con selector.metadata, lo que permite una regla por medida. Para
    # ``dataPoint.fill`` escriben solo el wildcard de datos: añadir metadata
    # cambia el alcance y puede dejar barras/sectores sin pintar.
    selector = _selector(referencia if grupo == "values" else None)
    reemplazado = False
    for bloque in bloques:
        props = bloque.get("properties") or {}
        if propiedad not in props:
            continue
        # El campo al que apunta el bloque es lo que decide si esto es la misma
        # regla o una distinta. Sin esta comparacion, colorear una medida
        # borraba el degradado de la anterior.
        if grupo == "values":
            if (bloque.get("selector") or {}).get("metadata") != referencia:
                continue
        else:
            existente = _fill_rule_input(props.get(propiedad))
            if _field_signature(existente) != _field_signature(field):
                continue
        props[propiedad] = _propiedad_de_color(regla)
        bloque["selector"] = selector
        reemplazado = True
        break
    if not reemplazado:
        bloques.append({"properties": {propiedad: _propiedad_de_color(regla)},
                        "selector": selector})

    # El JSON Schema no sabe si ``values.backColor`` o ``dataPoint.fill``
    # existen para este visual. El catalogo oficial si; se consulta antes de
    # que el documento llegue a una transaccion. Si el runtime no tiene el
    # oraculo, la barrera estructural local sigue actuando y el resultado lo
    # comprobaran los validadores de informe disponibles.
    from services import format_oracle
    format_oracle.assert_managed_paths(
        visual, [("objects", grupo, propiedad)])

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
