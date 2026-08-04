"""Construccion de visuales PBIR (visual.json).

Estrategia (Fase 8):
1. Si existe un visual del mismo tipo en el informe, se CLONA su estructura
   (conserva el andamiaje de formato/tema) y se le inyectan los campos/titulo.
2. Si no existe plantilla, se crea una minima valida con un mapa de roles por
   tipo, y se AVISA que debe validarse en Power BI Desktop.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError, VisualFactoryError
from horizun_pbi_mcp.pbip.pbir_reader import list_pages, pages_dir
from horizun_pbi_mcp.pbip.pbir_writer import SCHEMA_VISUAL
from horizun_pbi_mcp.utils.json_utils import read_json

log = get_logger("visual_factory")

# GUID del custom visual "HTML Content" (renderiza HTML/SVG desde una medida DAX).
HTML_CONTENT_TYPE = "htmlContent443BE3AD55E043BF878BED274D3A6855"

# Nombre amigable -> visualType real de PBIR
# visualTypes reales de PBIR que sabemos construir. Se escriben con las
# mayusculas de PBIR porque asi se serializan a visual.json.
REAL_TYPES = (
    "card",
    "cardVisual",
    "tableEx",
    "pivotTable",
    "slicer",
    "barChart",
    "columnChart",
    "clusteredBarChart",
    "clusteredColumnChart",
    "lineChart",
    "pieChart",
    "gauge",
    "kpi",
    "donutChart",
    "areaChart",
    "scatterChart",
    "treemap",
    "funnel",
    "waterfallChart",
    "multiRowCard",
    "ribbonChart",
    HTML_CONTENT_TYPE,
    # Elementos de composicion: no consultan datos, decoran y navegan.
    "textbox",
    "shape",
    "image",
    "pageNavigator",
    "actionButton",
)

# Nombre amigable -> visualType real de PBIR. Se escriben como se le muestran al
# usuario; la busqueda no distingue mayusculas.
ALIASES = {
    "table": "tableEx",
    "matrix": "pivotTable",
    "htmlContent": HTML_CONTENT_TYPE,
    "text": "textbox",
    "rectangle": "shape",
    "navigation": "pageNavigator",
    "button": "actionButton",
    "donut": "donutChart",
    # El nombre del visual en el catalogo oficial incluye el sufijo Chart.
    "waterfall": "waterfallChart",
}

# `resolve_type` busca en minusculas, asi que las CLAVES se DERIVAN en minusculas:
# una clave escrita en camelCase aqui seria inalcanzable —y se anunciaria como
# soportada mientras se rechaza al usarla, que es el defecto que esto corrige—.
# Cada tipo real es ademas alias de si mismo, de modo que todo lo que se anuncia
# se puede escribir tal cual en el spec.
TYPE_MAP = {real.lower(): real for real in REAL_TYPES}
TYPE_MAP.update({alias.lower(): real for alias, real in ALIASES.items()})

#: Visuales que NO llevan consulta: su contenido vive entero en `objects`.
#: Pedirles campos es un error del que llama, no algo que se ignore en silencio.
DECORATIVOS = frozenset({"textbox", "shape", "image", "pageNavigator", "actionButton"})

# Roles logicos -> clave de queryState por tipo real.
#
# Las claves de la derecha son las que PBIR exige de verdad, y no se deducen:
# se comprobaron una a una contra el validador oficial de Microsoft escribiendo
# un visual por cada (tipo, rol) y leyendo que devolvia PBIR_ROLE_UNKNOWN. De
# ahi salio que `cardVisual` no usa `Values` como los demas sino `Data`: con
# `Values` el informe entero queda invalido, y el tipo estaba anunciado como
# soportado. `tests/test_generadores_abren.py` mantiene esa comprobacion viva.
ROLE_MAP = {
    "card": {"values": "Values"},
    "cardVisual": {"values": "Data", "rows": "Rows",
                   "tooltips": "Tooltips"},
    # Una tabla no distingue dimension de medida: todo va a `Values`, en el
    # orden en que se pide. Se acepta `category` como en el segmentador porque
    # el destino no es ambiguo —y descartarlo, que es lo que se hacia antes,
    # borraba una columna que alguien habia pedido sin decirlo.
    "tableEx": {"values": "Values", "category": "Values"},
    "pivotTable": {"rows": "Rows", "columns": "Columns", "values": "Values"},
    "slicer": {"values": "Values", "category": "Values"},
    "barChart": {"category": "Category", "values": "Y", "legend": "Series",
                 "rows": "Rows", "tooltips": "Tooltips"},
    "columnChart": {"category": "Category", "values": "Y", "legend": "Series",
                    "rows": "Rows", "tooltips": "Tooltips"},
    "clusteredBarChart": {"category": "Category", "values": "Y",
                          "legend": "Series", "rows": "Rows",
                          "tooltips": "Tooltips"},
    "clusteredColumnChart": {"category": "Category", "values": "Y",
                             "legend": "Series", "rows": "Rows",
                             "tooltips": "Tooltips"},
    "lineChart": {"category": "Category", "values": "Y", "legend": "Series",
                  "y2": "Y2", "rows": "Rows", "tooltips": "Tooltips"},
    "pieChart": {"category": "Category", "values": "Y", "legend": "Series",
                 "details": "Series", "tooltips": "Tooltips"},
    "gauge": {"values": "Y", "min": "MinValue", "max": "MaxValue",
              "target": "TargetValue", "tooltips": "Tooltips"},
    "kpi": {"values": "Indicator", "trend": "TrendLine", "goal": "Goal"},
    "donutChart": {"category": "Category", "values": "Y",
                   "legend": "Series", "details": "Series",
                   "tooltips": "Tooltips"},
    "areaChart": {"category": "Category", "values": "Y", "legend": "Series",
                  "y2": "Y2", "rows": "Rows", "tooltips": "Tooltips"},
    "scatterChart": {"category": "Category", "legend": "Series", "x": "X",
                     "y": "Y", "size": "Size", "play": "Play",
                     "tooltips": "Tooltips"},
    "treemap": {"category": "Group", "group": "Group", "details": "Details",
                "values": "Values", "tooltips": "Tooltips"},
    "funnel": {"category": "Category", "values": "Y",
               "tooltips": "Tooltips"},
    "waterfallChart": {"category": "Category", "values": "Y",
                       "breakdown": "Breakdown", "tooltips": "Tooltips"},
    "multiRowCard": {"values": "Values"},
    "ribbonChart": {"category": "Category", "values": "Y",
                    "legend": "Series", "rows": "Rows",
                    "tooltips": "Tooltips"},
    # HTML Content: una sola medida (que devuelve HTML/SVG) en el rol 'content'.
    HTML_CONTENT_TYPE: {"values": "content", "content": "content"},
}

# Contrato del catalogo oficial (`catalog describe <visualType>`). Un rol
# conocido no basta: omitir uno obligatorio o superar su cardinalidad genera
# PBIR_ROLE_REQUIRED_MISSING/PBIR_ROLE_CARDINALITY_EXCEEDED y deja el informe
# sin abrir. Las claves son las de queryState, no los alias de la API.
REQUIRED_ROLES = {
    "card": ("Values",),
    "cardVisual": ("Data",),
    "tableEx": ("Values",),
    "pivotTable": ("Values",),
    "slicer": ("Values",),
    "barChart": ("Category", "Y"),
    "columnChart": ("Category", "Y"),
    "clusteredBarChart": ("Category", "Y"),
    "clusteredColumnChart": ("Category", "Y"),
    "lineChart": ("Category", "Y"),
    "pieChart": ("Category", "Y"),
    "gauge": ("Y",),
    "kpi": ("Indicator",),
    "donutChart": ("Category", "Y"),
    "areaChart": ("Category", "Y"),
    "scatterChart": ("X", "Y"),
    "treemap": ("Values",),
    "funnel": ("Category", "Y"),
    "waterfallChart": ("Category", "Y"),
    "multiRowCard": ("Values",),
    "ribbonChart": ("Category", "Y"),
    HTML_CONTENT_TYPE: ("content",),
}

MAX_PER_ROLE = {
    "card": {"Values": 1},
    "slicer": {"Values": 1},
    "barChart": {"Series": 1},
    "columnChart": {"Series": 1},
    "clusteredBarChart": {"Series": 1},
    "clusteredColumnChart": {"Series": 1},
    "lineChart": {"Series": 1},
    "pieChart": {"Category": 1, "Series": 1},
    "gauge": {"Y": 1, "MinValue": 1, "MaxValue": 1, "TargetValue": 1},
    "kpi": {"Indicator": 1, "TrendLine": 1, "Goal": 2},
    "donutChart": {"Category": 1, "Series": 1},
    "areaChart": {"Series": 1},
    "scatterChart": {"Category": 1, "Series": 1, "Y": 1,
                     "Size": 1, "Play": 1},
    "treemap": {"Group": 1, "Details": 1},
    "funnel": {"Category": 1},
    "waterfallChart": {"Category": 1, "Breakdown": 1, "Y": 1},
    "ribbonChart": {"Series": 1},
    HTML_CONTENT_TYPE: {"content": 1},
}

ROLE_KINDS = {
    "card": {"Values": "Measure"},
    "cardVisual": {"Data": "Measure", "Rows": "Grouping",
                   "Tooltips": "Measure"},
    "tableEx": {"Values": "GroupingOrMeasure"},
    "pivotTable": {"Rows": "Grouping", "Columns": "Grouping",
                   "Values": "Measure"},
    "slicer": {"Values": "Grouping"},
    "barChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                 "Rows": "Grouping", "Tooltips": "Measure"},
    "columnChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                    "Rows": "Grouping", "Tooltips": "Measure"},
    "clusteredBarChart": {"Category": "Grouping", "Series": "Grouping",
                          "Y": "Measure", "Rows": "Grouping",
                          "Tooltips": "Measure"},
    "clusteredColumnChart": {"Category": "Grouping", "Series": "Grouping",
                             "Y": "Measure", "Rows": "Grouping",
                             "Tooltips": "Measure"},
    "lineChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                  "Y2": "Measure", "Rows": "Grouping", "Tooltips": "Measure"},
    "pieChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                 "Tooltips": "Measure"},
    "gauge": {"Y": "Measure", "MinValue": "Measure", "MaxValue": "Measure",
              "TargetValue": "Measure", "Tooltips": "Measure"},
    "kpi": {"Indicator": "Measure", "TrendLine": "Grouping", "Goal": "Measure"},
    "donutChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                   "Tooltips": "Measure"},
    "areaChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                  "Y2": "Measure", "Rows": "Grouping", "Tooltips": "Measure"},
    "scatterChart": {"Category": "Grouping", "Series": "Grouping",
                     "X": "GroupingOrMeasure", "Y": "GroupingOrMeasure",
                     "Size": "Measure", "Play": "Grouping", "Tooltips": "Measure"},
    "treemap": {"Group": "Grouping", "Details": "Grouping", "Values": "Measure",
                "Tooltips": "Measure"},
    "funnel": {"Category": "Grouping", "Y": "Measure", "Tooltips": "Measure"},
    "waterfallChart": {"Category": "Grouping", "Breakdown": "Grouping",
                       "Y": "Measure", "Tooltips": "Measure"},
    "multiRowCard": {"Values": "GroupingOrMeasure"},
    "ribbonChart": {"Category": "Grouping", "Series": "Grouping", "Y": "Measure",
                    "Rows": "Grouping", "Tooltips": "Measure"},
    HTML_CONTENT_TYPE: {"content": "Measure"},
}

#: Otros nombres con los que la gente llama al mismo rol. Se aceptan solo si el
#: tipo tiene ese rol logico: en un `card` no hay leyenda que valga.
#:
#: Aqui solo entra lo que significa lo MISMO, no lo que se le parece. `details`
#: es un rol propio en la interfaz de Power BI, asi que mandarlo a `category`
#: seria colocar un campo donde nadie lo pidio —el mismo defecto que este mapa
#: existe para cerrar—. Un rol que no este aqui se rechaza y se dice.
_SINONIMOS_DE_ROL = {
    "value": "values", "measure": "values", "measures": "values",
    "axis": "category", "categories": "category",
    "row": "rows", "column": "columns", "series": "legend",
}


def roles_de(actual_type: str) -> Dict[str, str]:
    """Nombre de rol aceptado (en minusculas) -> clave de queryState.

    Se admiten TRES formas del mismo rol, porque las tres circulan de verdad:
    el rol logico (`values`), el nombre PBIR (`Y`, `Data`, `Category`) —que es
    justo lo que devuelve `pbir_reader` al leer una pagina y lo que se ve en el
    propio visual.json— y un puñado de sinonimos naturales (`measure`, `axis`).
    """
    role_map = ROLE_MAP.get(actual_type, {"values": "Values"})
    alias: Dict[str, str] = {}
    for logico, clave in role_map.items():
        alias[logico.lower()] = clave
    for clave in role_map.values():
        alias.setdefault(clave.lower(), clave)
    for sinonimo, logico in _SINONIMOS_DE_ROL.items():
        if logico in role_map:
            alias.setdefault(sinonimo, role_map[logico])
    return alias


# Lo que sale aqui lo acepta `resolve_type`, y al reves: misma fuente.
SUPPORTED = sorted(REAL_TYPES + tuple(ALIASES), key=str.lower)


def resolve_type(visual_type: str) -> str:
    key = str(visual_type).strip().lower()
    if key not in TYPE_MAP:
        raise VisualFactoryError(
            f"Tipo de visual no soportado: '{visual_type}'. "
            f"Soportados: {SUPPORTED}.")
    return TYPE_MAP[key]


def normalizar_referencia(ref: Any) -> str:
    """Deja cualquier forma de referencia de campo en `Tabla[Campo]`.

    `pbir_reader.read_visual_file` devuelve cada campo como un diccionario
    (`{"kind", "entity", "property", "ref"}`) y el generador espera una cadena.
    Sin esto, leer una pagina y reutilizar sus campos —el flujo mas natural que
    hay, "hazme otra parecida a esta"— no funcionaba: el lector y el escritor
    del mismo servidor no se entendian.
    """
    if isinstance(ref, str):
        return ref.strip()
    if isinstance(ref, dict):
        texto = ref.get("ref")
        if isinstance(texto, str) and texto.strip():
            return texto.strip()
        entidad, propiedad = ref.get("entity"), ref.get("property")
        if propiedad:
            return f"{entidad}[{propiedad}]" if entidad else f"[{propiedad}]"
    raise VisualFactoryError(
        f"Referencia de campo invalida: {ref!r}. Usa 'Tabla[Campo]', "
        "'[Medida]', o el objeto que devuelve pbi_list_visuals.",
        details={"reference": repr(ref)})


def _parse_ref(ref: str) -> Dict[str, Optional[str]]:
    r = ref.strip()
    if "[" in r and r.endswith("]"):
        field = r[r.index("[") + 1:-1]
        table = r[:r.index("[")].strip() or None
        return {"table": table, "field": field}
    return {"table": None, "field": r}


def _infer_kind(ref: str, measure_index: Optional[Dict[str, str]]) -> str:
    parsed = _parse_ref(ref)
    if ref.strip().startswith("["):
        return "measure"
    if measure_index and parsed["field"] in measure_index:
        return "measure"
    return "column"


def _field_node(ref: str, kind: str, measure_index: Optional[Dict[str, str]],
                warnings: List[str]) -> Dict[str, Any]:
    parsed = _parse_ref(ref)
    table = parsed["table"]
    field = parsed["field"]
    # Rechaza referencias vacias o malformadas (p.ej. 'Tabla[]', '[', 'Tabla[').
    if not field or not field.strip() or "[" in field or "]" in field:
        raise VisualFactoryError(
            f"Referencia de campo invalida: '{ref}'. Usa 'Tabla[Campo]' o '[Medida]'.")
    if table is None and measure_index and field in measure_index:
        table = measure_index[field]
    if table is None:
        warnings.append(
            f"No se pudo inferir la tabla de '{ref}'; Power BI podria no vincularlo. "
            "Usa la forma 'Tabla[Campo]'.")
    tom_kind = "Measure" if kind == "measure" else "Column"
    query_ref = f"{table}.{field}" if table else field
    return {
        "field": {
            tom_kind: {
                "Expression": {"SourceRef": {"Entity": table or ""}},
                "Property": field,
            }
        },
        "queryRef": query_ref,
        "nativeQueryRef": field,
    }


def _normalizar_roles(actual_type: str,
                      fields: Dict[str, Any]) -> Dict[str, List[Any]]:
    """Lo que pide quien llama -> claves de queryState. Acusa lo que no existe.

    Se recorren los roles PEDIDOS, no los conocidos. La version anterior hacia
    lo contrario (`fields.get(rol)` por cada rol del mapa) y eso tenia dos
    consecuencias mudas: `{"Values": [...]}` no casaba con la clave `values` y
    el visual salia SIN datos, y un rol mal escrito junto a uno bueno se perdia
    sin ni siquiera un aviso. En los dos casos el informe abre y pinta un
    visual vacio, que es peor que no abrir: nadie va a buscar un error.
    """
    alias = roles_de(actual_type)
    normalizado: Dict[str, List[Any]] = {}
    for rol_pedido, refs in (fields or {}).items():
        clave = alias.get(str(rol_pedido).strip().lower())
        if clave is None:
            raise VisualFactoryError(
                f"El visual '{actual_type}' no tiene un rol '{rol_pedido}'. "
                f"Roles validos para este tipo: {sorted(set(alias))}.",
                details={"visual_type": actual_type, "role": rol_pedido,
                         "valid_roles": sorted(set(alias))})
        if not refs:
            continue
        if isinstance(refs, (str, dict)):
            lista = [refs]
        elif isinstance(refs, (list, tuple)):
            lista = list(refs)
        else:
            # `list(5)` seria un TypeError crudo en mitad del generador; el que
            # llama merece un error del servidor que diga que se esperaba.
            raise VisualFactoryError(
                f"El rol '{rol_pedido}' espera un campo o una lista de campos; "
                f"se recibio {type(refs).__name__}.",
                details={"visual_type": actual_type, "role": rol_pedido})
        normalizados: List[Any] = []
        for referencia in lista:
            texto = normalizar_referencia(referencia)
            if isinstance(referencia, dict):
                # El lector entrega metadatos que no caben en `Tabla[Campo]`.
                # Se conservan para que reconstruir una pagina no cambie una
                # agregacion ni sus queryRef/nativeQueryRef originales.
                campo = {"ref": texto}
                for metadato in ("kind", "aggregation", "queryRef",
                                 "nativeQueryRef"):
                    if metadato in referencia:
                        campo[metadato] = referencia[metadato]
                normalizados.append(campo)
            else:
                normalizados.append(texto)
        normalizado.setdefault(clave, []).extend(normalizados)
    return normalizado


def _build_query(actual_type: str, fields: Dict[str, Any],
                 measure_index: Optional[Dict[str, str]],
                 warnings: List[str]) -> Dict[str, Any]:
    role_map = ROLE_MAP.get(actual_type, {"values": "Values"})
    normalizado = _normalizar_roles(actual_type, fields)

    # Se emite en el orden del mapa, no en el que llegaron los roles: dos specs
    # equivalentes tienen que producir el MISMO visual.json byte a byte, o el
    # diff de `page_update` vera cambios donde no los hay.
    orden: List[str] = []
    for clave in role_map.values():
        if clave not in orden:
            orden.append(clave)
    for clave in normalizado:
        if clave not in orden:                              # pragma: no cover
            orden.append(clave)

    query_state: Dict[str, Any] = {}
    for clave in orden:
        refs = normalizado.get(clave)
        if not refs:
            continue
        projections = []
        for entrada in refs:
            metadatos = entrada if isinstance(entrada, dict) else {}
            ref = normalizar_referencia(entrada)
            kind_leido = metadatos.get("kind")
            kind = (kind_leido if kind_leido in ("measure", "column")
                    else _infer_kind(ref, measure_index))
            proyeccion = _field_node(ref, kind, measure_index, warnings)

            if "aggregation" in metadatos:
                proyeccion["field"] = {"Aggregation": {
                    "Expression": proyeccion["field"],
                    "Function": metadatos["aggregation"],
                }}
            for clave_ref in ("queryRef", "nativeQueryRef"):
                valor = metadatos.get(clave_ref)
                if isinstance(valor, str) and valor:
                    proyeccion[clave_ref] = valor
            projections.append(proyeccion)
        query_state[clave] = {"projections": projections}

    if not query_state:
        warnings.append("El visual no recibio campos; quedara vacio.")
    return {"queryState": query_state}


def _validate_role_contract(actual_type: str, query: Dict[str, Any]) -> None:
    """Exige obligatoriedad y cardinalidad antes de buscar una plantilla."""
    estados = query.get("queryState") or {}
    faltantes = [rol for rol in REQUIRED_ROLES.get(actual_type, ())
                 if not (estados.get(rol) or {}).get("projections")]
    if faltantes:
        raise VisualFactoryError(
            f"El visual '{actual_type}' requiere los roles {faltantes}; no se "
            "escribira un visual incompleto.",
            details={"visual_type": actual_type, "missing_roles": faltantes,
                     "required_roles": list(REQUIRED_ROLES.get(actual_type, ())),
                     "received_roles": sorted(estados)})

    excedidos = []
    for rol, maximo in MAX_PER_ROLE.get(actual_type, {}).items():
        cantidad = len((estados.get(rol) or {}).get("projections") or [])
        if cantidad > maximo:
            excedidos.append({"role": rol, "count": cantidad, "max": maximo})
    if excedidos:
        raise VisualFactoryError(
            f"El visual '{actual_type}' supera la cardinalidad oficial de "
            f"estos roles: {excedidos}.",
            details={"visual_type": actual_type,
                     "cardinality_exceeded": excedidos})

    incompatibles = []
    for rol, estado in estados.items():
        esperado = ROLE_KINDS.get(actual_type, {}).get(rol)
        if esperado in (None, "GroupingOrMeasure"):
            continue
        for indice, proyeccion in enumerate(estado.get("projections") or []):
            campo = proyeccion.get("field") if isinstance(proyeccion, dict) else None
            claves = set(campo) if isinstance(campo, dict) else set()
            recibido = ("Measure" if claves & {"Measure", "Aggregation"}
                        else "Grouping" if claves & {"Column", "HierarchyLevel"}
                        else "Unknown")
            if recibido != esperado:
                incompatibles.append({"role": rol, "index": indice,
                                      "expected": esperado, "received": recibido,
                                      "query_ref": proyeccion.get("queryRef")
                                      if isinstance(proyeccion, dict) else None})
    if incompatibles:
        raise VisualFactoryError(
            f"El visual '{actual_type}' recibio campos del tipo equivocado en "
            f"estos roles: {incompatibles}.",
            details={"visual_type": actual_type,
                     "role_kind_mismatch": incompatibles})


def _title_object(title: str) -> Dict[str, Any]:
    value = "'" + str(title).replace("'", "''") + "'"
    return {"title": [{"properties": {"text": {"expr": {"Literal": {"Value": value}}}}}]}


def _set_title(vis: Dict[str, Any], title: str) -> None:
    """Fija el texto del titulo PRESERVANDO el formato (color/fuente) de la plantilla.

    Si el visual clonado ya tiene un objeto title (con su estilo), solo se cambia el
    texto; asi el titulo hereda el estilo del visual plantilla en vez de resetearse.
    """
    value = "'" + str(title).replace("'", "''") + "'"
    vco = vis.setdefault("visualContainerObjects", {})
    tarr = vco.get("title")
    if isinstance(tarr, list) and tarr and isinstance(tarr[0], dict):
        props = tarr[0].setdefault("properties", {})
        props["text"] = {"expr": {"Literal": {"Value": value}}}
        # Sin `show` explicito, el defecto de una tarjeta es OCULTO: se pedia
        # un titulo, no fallaba nada, y en pantalla no habia titulo. Un rotulo
        # que se pide y no aparece es peor que no poder pedirlo.
        props.setdefault("show", {"expr": {"Literal": {"Value": "true"}}})
    else:
        vco["title"] = [{"properties": {
            "text": {"expr": {"Literal": {"Value": value}}},
            "show": {"expr": {"Literal": {"Value": "true"}}}}}]


def _quitar_titulo_heredado(vis: Dict[str, Any]) -> None:
    """`title=None` significa sin titulo, no "usa el de la plantilla"."""
    vco = vis.get("visualContainerObjects")
    if not isinstance(vco, dict):
        return
    vco.pop("title", None)
    if not vco:
        vis.pop("visualContainerObjects", None)


def _quitar_selectores_de_campos_obsoletos(
        vis: Dict[str, Any], query: Dict[str, Any], warnings: List[str]) -> None:
    """Descarta formato acotado a campos que ya no estan en la consulta.

    `selector.metadata` es un queryRef. Clonarlo junto con una consulta nueva
    deja reglas de formato condicional apuntando al campo de la plantilla; el
    esquema no lo denuncia porque el bloque `objects` es abierto.
    """
    referencias = {
        proyeccion.get("queryRef")
        for estado in (query.get("queryState") or {}).values()
        for proyeccion in (estado.get("projections") or [])
        if isinstance(proyeccion, dict) and proyeccion.get("queryRef")
    }
    objetos = vis.get("objects")
    if not isinstance(objetos, dict):
        return

    eliminados = 0
    for grupo, bloques in list(objetos.items()):
        if not isinstance(bloques, list):
            continue
        vigentes = []
        for bloque in bloques:
            selector = bloque.get("selector") if isinstance(bloque, dict) else None
            metadata = selector.get("metadata") if isinstance(selector, dict) else None
            if metadata is not None and (
                    not isinstance(metadata, str) or metadata not in referencias):
                eliminados += 1
                continue
            vigentes.append(bloque)
        if vigentes:
            objetos[grupo] = vigentes
        else:
            objetos.pop(grupo, None)
    if not objetos:
        vis.pop("objects", None)
    if eliminados:
        warnings.append(
            f"Se descartaron {eliminados} bloque(s) de formato de la plantilla "
            "porque apuntaban a campos que ya no estan en la consulta.")


def _normalizar_posicion(position: Dict[str, float]) -> Dict[str, float]:
    """Convierte y valida geometria antes de leer una plantilla o escribir."""
    try:
        pos = {
            "x": float(position["x"]),
            "y": float(position["y"]),
            "z": float(position.get("z", 0)),
            "width": float(position["width"]),
            "height": float(position["height"]),
            "tabOrder": float(position.get("z", 0)),
        }
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
        raise VisualFactoryError(
            "La posicion exige x, y, width y height numericos.",
            details={"position": repr(position)}) from exc

    no_finitas = [clave for clave in ("x", "y", "z", "width", "height")
                  if not math.isfinite(pos[clave])]
    if no_finitas:
        raise VisualFactoryError(
            f"La posicion contiene dimensiones no finitas: {no_finitas}.",
            details={"position": repr(position), "invalid": no_finitas,
                     "rule": "finite_position"})
    no_positivas = [clave for clave in ("width", "height") if pos[clave] <= 0]
    if no_positivas:
        raise VisualFactoryError(
            f"Las dimensiones width y height deben ser mayores que cero; "
            f"no cumplen: {no_positivas}.",
            details={"position": repr(position), "invalid": no_positivas,
                     "rule": "positive_dimensions"})
    return pos


# ------------------------------------------------------------------ decorativos --
#: Alias publicos -> enum exacto del catalogo oficial. La API conserva nombres
#: amistosos ya publicados, pero el visual.json nunca escribe un enum inventado.
FORMAS = ("rectangle", "roundedRectangle", "oval", "line", "arrow", "triangle",
          "pentagon", "hexagon", "heart")
_FORMA_PBIR = {
    "rectangle": "rectangle", "roundedRectangle": "rectangleRounded",
    "oval": "oval", "line": "line", "arrow": "arrow",
    "triangle": "triangleIsoc", "pentagon": "pentagon",
    "hexagon": "hexagon", "heart": "heart",
}
#: `shapeType` exactos, mas alias historicos que se traducen antes de escribir.
ICONOS_BOTON = ("blank", "back", "bookmark", "drillDown", "drillUp", "info",
                "question", "reset", "resetFilters", "chevronRight", "chevronLeft")
_ICONO_PBIR = {
    "blank": "blank", "back": "back", "bookmark": "bookmarks",
    "drillDown": "rightArrow", "drillUp": "leftArrow",
    "info": "information", "question": "help", "reset": "reset",
    "resetFilters": "clearAllSlicers", "chevronRight": "rightArrow",
    "chevronLeft": "leftArrow",
}


def _lit(valor: Any) -> Dict[str, Any]:
    """Envuelve un valor en la forma `expr.Literal` que usa PBIR.

    Las cadenas van entre comillas simples DENTRO del literal, los numeros con
    su sufijo de tipo ('D' decimal, 'L' entero) y los booleanos sin comillas.
    Es la gramatica del motor, no una convencion nuestra.
    """
    if isinstance(valor, bool):
        texto = "true" if valor else "false"
    elif isinstance(valor, int):
        texto = f"{valor}L"
    elif isinstance(valor, float):
        texto = f"{valor}D"
    else:
        texto = "'" + str(valor).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": texto}}}


def _props(**kwargs: Any) -> List[Dict[str, Any]]:
    """Bloque `objects` de una sola entrada con sus propiedades literales."""
    return [{"properties": {k: _lit(v) for k, v in kwargs.items()
                            if v is not None}}]


def _sin_marco() -> Dict[str, Any]:
    """Titulo, fondo y borde apagados: lo normal en un elemento de composicion."""
    return {
        "title": _props(show=False),
        "background": _props(show=False),
        "border": _props(show=False),
    }


def _build_textbox(opciones: Dict[str, Any]) -> Dict[str, Any]:
    texto = opciones.get("text")
    if texto is None:
        raise VisualFactoryError(
            "Un 'textbox' necesita 'text'. Sin texto no hay nada que escribir.")
    estilo: Dict[str, Any] = {}
    if opciones.get("font_size") is not None:
        estilo["fontSize"] = f"{opciones['font_size']}pt"
    if opciones.get("color"):
        estilo["color"] = opciones["color"]
    if opciones.get("bold"):
        estilo["fontWeight"] = "bold"
    if opciones.get("font"):
        estilo["fontFamily"] = opciones["font"]

    parrafo: Dict[str, Any] = {"textRuns": [{"value": str(texto),
                                             **({"textStyle": estilo} if estilo else {})}]}
    if opciones.get("align"):
        parrafo["horizontalTextAlignment"] = opciones["align"]
    return {"general": [{"properties": {"paragraphs": [parrafo]}}]}


#: Relleno que Power BI deja por defecto arriba y abajo de un texto.
_PADDING_TEXTO = 8


def piso_de_texto(font_size: float, padding: int = _PADDING_TEXTO) -> int:
    """Altura minima de un textbox para que no salga barra de scroll.

    Es la cuenta del validador oficial de Microsoft. Por debajo de ella el
    texto se corta, que es un fallo que ningun validador de esquema ve porque
    el JSON es perfectamente valido.
    """
    return max(18, math.ceil(float(font_size) * 25 / 16)) + padding * 2


def _ajustar_alto_de_texto(pos: Dict[str, float], opciones: Dict[str, Any],
                           warnings: List[str]) -> None:
    """Sube la altura al piso si se quedo corta, y lo dice.

    Se corrige en vez de solo avisar: quien compone una pagina no tiene por que
    saber la formula, y un texto cortado no es lo que nadie queria.
    """
    tamano = opciones.get("font_size")
    if tamano is None:
        return
    minimo = piso_de_texto(tamano)
    if pos["height"] < minimo:
        warnings.append(
            f"La altura {pos['height']:.0f}px se queda corta para un texto de "
            f"{tamano}pt y saldria cortado con barra de scroll; se sube a "
            f"{minimo}px.")
        pos["height"] = float(minimo)


def _aplicar_opciones_de_tarjeta(vis: Dict[str, Any], actual_type: str,
                                 opciones: Dict[str, Any]) -> None:
    """Formato del numero y de la etiqueta de una tarjeta.

    Con un titulo propio descriptivo, la etiqueta de categoria repite el mismo
    texto y ademas suele salir mas grande que el dato. Nada de esto se toca si
    no se pide: no se inventa formato que nadie encargo.
    """
    objetos = vis.setdefault("objects", {})

    # Son dos visuales distintos aunque ambos se llamen "tarjeta" en la UI.
    # El catalogo oficial del CLI 0.1.4 enumera `label` y `value` para
    # `cardVisual`; el `card` clasico usa `categoryLabels` y `labels`. El mismo
    # par nuevo aparece en los visual.json exportados por Desktop. Mezclarlos
    # produce JSON valido de esquema que Desktop simplemente no aplica.
    if actual_type == "cardVisual":
        etiqueta, valor = "label", "value"
        propiedad_color = "fontColor"
        objetos.pop("categoryLabels", None)
        objetos.pop("labels", None)
    else:
        etiqueta, valor = "categoryLabels", "labels"
        propiedad_color = "color"

    if opciones.get("show_category_label") is False:
        objetos[etiqueta] = [{"properties": {"show": _lit(False)}}]

    props: Dict[str, Any] = {}
    if opciones.get("value_font_size") is not None:
        # El tamano va como numero crudo con sufijo D, sin comillas: `_lit`
        # entrecomillaria la cadena y Power BI lo leeria como texto.
        props["fontSize"] = {"expr": {"Literal": {
            "Value": f"{opciones['value_font_size']}D"}}}
    if opciones.get("bold_value"):
        props["bold"] = _lit(True)
    if opciones.get("value_color"):
        props[propiedad_color] = {
            "solid": {"color": _lit(opciones["value_color"])}}
    if props:
        objetos[valor] = [{"properties": props}]
    if not objetos:
        vis.pop("objects", None)


def _aplicar_estilo_contenedor(vis: Dict[str, Any], opciones: Dict[str, Any]) -> None:
    """Fondo y borde del MARCO: aplica a cualquier tipo de visual.

    Es el panel General > Efectos de Power BI Desktop (Fondo / Borde), no el
    relleno propio de una forma (`_build_shape`) ni el color de un valor de
    tarjeta (`_aplicar_opciones_de_tarjeta`). Antes no habia forma de pedir
    esto por 'options': `_sin_marco()` solo sabia APAGARLO (`show=False`) en
    los elementos de composicion, y encenderlo con un color exigia escribir
    `visualContainerObjects` a mano fuera de esta fabrica.

    `background_color` / `border_color`: hex ('#RRGGBB'). Sin ellos, no se
    toca nada (no se inventa un marco que nadie pidio).
    """
    color_fondo = opciones.get("background_color")
    color_borde = opciones.get("border_color")
    if not color_fondo and not color_borde:
        return

    contenedor = vis.setdefault("visualContainerObjects", {})

    if color_fondo:
        contenedor["background"] = [{"properties": {
            "show": _lit(True),
            "color": {"solid": {"color": _lit(color_fondo)}},
            "transparency": _lit(float(opciones.get("background_transparency", 0))),
        }}]

    if color_borde:
        props: Dict[str, Any] = {
            "show": _lit(True),
            "color": {"solid": {"color": _lit(color_borde)}},
        }
        if opciones.get("border_radius") is not None:
            props["radius"] = _lit(float(opciones["border_radius"]))
        contenedor["border"] = [{"properties": props}]


def _build_shape(opciones: Dict[str, Any]) -> Dict[str, Any]:
    forma = opciones.get("shape", "rectangle")
    if forma not in FORMAS:
        raise VisualFactoryError(
            f"Forma no soportada: '{forma}'. Usa una de {list(FORMAS)}.")
    objetos: Dict[str, Any] = {
        "shape": _props(tileShape=_FORMA_PBIR[forma]),
        "rotation": _props(shapeAngle=int(opciones.get("angle", 0))),
    }
    relleno = opciones.get("fill")
    if relleno:
        objetos["fill"] = [{
            "properties": {
                "fillColor": {"solid": {"color": _lit(relleno)}},
                "transparency": _lit(float(opciones.get("transparency", 0))),
            },
            "selector": {"id": "default"},
        }]
    if opciones.get("text"):
        objetos["text"] = [
            {"properties": {"show": _lit(True)}},
            {"properties": {k: v for k, v in {
                "text": _lit(opciones["text"]),
                "fontSize": _lit(float(opciones["font_size"]))
                if opciones.get("font_size") is not None else None,
                "fontColor": {"solid": {"color": _lit(opciones["text_color"])}}
                if opciones.get("text_color") else None,
            }.items() if v is not None},
             "selector": {"id": "default"}},
        ]
    return objetos


def _build_image(opciones: Dict[str, Any]) -> Dict[str, Any]:
    recurso = opciones.get("resource")
    if not recurso:
        raise VisualFactoryError(
            "Un 'image' necesita 'resource': el ItemName del recurso ya "
            "registrado en RegisteredResources. Registralo antes con "
            "pbi_add_image_resource.")
    return {"image": [{"properties": {"sourceFile": {"image": {
        "name": _lit(opciones.get("name") or recurso),
        "url": {"expr": {"ResourcePackageItem": {
            "PackageName": "RegisteredResources",
            "PackageType": 1,
            "ItemName": recurso}}},
        "scaling": _lit(opciones.get("scaling", "Fit")),
    }}}}]}


def _build_page_navigator(opciones: Dict[str, Any]) -> Dict[str, Any]:
    return {"pages": [
        {"properties": {"showHiddenPages": _lit(bool(opciones.get("show_hidden", False)))}},
        {"properties": {"showPage": _lit(bool(opciones.get("show_current", False)))}},
    ]}


def _build_action_button(opciones: Dict[str, Any]) -> Dict[str, Any]:
    """Boton con accion. Devuelve (objects, visualContainerObjects)."""
    accion = str(opciones.get("action", "page")).lower()
    icono = opciones.get("icon", "back" if accion == "back" else "blank")
    if icono not in ICONOS_BOTON:
        raise VisualFactoryError(
            f"Icono de boton no soportado: '{icono}'. Usa uno de {list(ICONOS_BOTON)}.")

    objetos: Dict[str, Any] = {"icon": [{"properties": {
        "shapeType": _lit(_ICONO_PBIR[icono])},
                                         "selector": {"id": "default"}}]}
    if opciones.get("text"):
        objetos["text"] = [
            {"properties": {"show": _lit(True)}},
            {"properties": {k: v for k, v in {
                "text": _lit(opciones["text"]),
                "fontSize": _lit(float(opciones["font_size"]))
                if opciones.get("font_size") is not None else None,
                "fontColor": {"solid": {"color": _lit(opciones["text_color"])}}
                if opciones.get("text_color") else None,
            }.items() if v is not None},
             "selector": {"id": "default"}},
        ]
    if opciones.get("fill"):
        objetos["fill"] = [{
            "properties": {"fillColor": {"solid": {"color": _lit(opciones["fill"])}},
                           "transparency": _lit(float(opciones.get("transparency", 0)))},
            "selector": {"id": "default"}}]

    if accion == "back":
        enlace = {"show": _lit(True), "type": _lit("Back")}
    elif accion in ("page", "pagenavigation"):
        destino = opciones.get("target_page")
        if not destino:
            raise VisualFactoryError(
                "Un boton con action='page' necesita 'target_page' con el "
                "NOMBRE INTERNO de la pagina destino (el id, no el titulo).")
        enlace = {"show": _lit(True), "type": _lit("PageNavigation"),
                  "navigationSection": _lit(destino)}
    elif accion == "bookmark":
        marcador = opciones.get("bookmark")
        if not marcador:
            raise VisualFactoryError(
                "Un boton con action='bookmark' necesita 'bookmark'.")
        enlace = {"show": _lit(True), "type": _lit("Bookmark"),
                  "bookmark": _lit(marcador)}
    else:
        raise VisualFactoryError(
            f"Accion de boton no soportada: '{accion}'. Usa page | back | bookmark.")
    return objetos, {"visualLink": [{"properties": enlace}]}


def _build_decorativo(actual_type: str,
                      opciones: Dict[str, Any]) -> Dict[str, Any]:
    """Construye el visual completo de un elemento de composicion."""
    contenedor = _sin_marco()
    if actual_type == "textbox":
        objetos = _build_textbox(opciones)
    elif actual_type == "shape":
        objetos = _build_shape(opciones)
    elif actual_type == "image":
        objetos = _build_image(opciones)
    elif actual_type == "pageNavigator":
        objetos = _build_page_navigator(opciones)
        contenedor = {}          # el navegador dibuja su propio marco
    elif actual_type == "actionButton":
        objetos, extra = _build_action_button(opciones)
        contenedor.update(extra)
    else:                                                # pragma: no cover
        raise VisualFactoryError(f"Tipo decorativo desconocido: {actual_type}")

    vis: Dict[str, Any] = {"visualType": actual_type, "objects": objetos,
                           "drillFilterOtherVisuals": True}
    if contenedor:
        vis["visualContainerObjects"] = contenedor
    return vis


#: Formatos comunes traducidos a PBIR. Clave amable -> (scope, grupo,
#: propiedad, valores admitidos o None si es libre).
#:
#: Existe porque poner un slicer en desplegable obligaba a escribir a mano
#:
#:     "objects": {"data": [{"properties": {"mode": {"expr": {"Literal":
#:         {"Value": "'Dropdown'"}}}}}]}
#:
#: dentro de cada visual.json. Funciona -Desktop lo respeta- pero es la clase
#: de edicion a mano que despues aparece como errores de esquema que nadie
#: sabe de donde salieron. Aqui la escribe el servidor, con la gramatica de
#: `_lit()`, y pasa por el oraculo oficial como todo lo demas.
#:
#: El vocabulario es corto A PROPOSITO: cada entrada esta comprobada contra el
#: catalogo oficial. Es preferible rechazar una clave que no se conoce a
#: escribir una forma plausible que Desktop ignore en silencio.
_FORMATOS_COMUNES: Dict[str, tuple] = {
    "mode": ("objects", "data", "mode",
             ("Basic", "Dropdown", "Between", "Before", "After", "List")),
    "dataLabels": ("objects", "labels", "show", None),
    "legend": ("objects", "legend", "show", None),
    "legendPosition": ("objects", "legend", "position",
                       ("Top", "Bottom", "Left", "Right", "TopCenter",
                        "BottomCenter", "LeftCenter", "RightCenter")),
}


#: Vocabulario publico: lo consulta el validador del spec para poder
#: rechazar una clave desconocida ANTES de construir nada.
FORMATOS_COMUNES = _FORMATOS_COMUNES


def _aplicar_formato(vis: Dict[str, Any], formato: Dict[str, Any]
                     ) -> List[tuple[str, str, str]]:
    """Traduce el bloque `format` a `objects`. Devuelve las rutas escritas.

    Falla ante una clave desconocida en vez de ignorarla: un formato que se
    pide y no se aplica es peor que un error, porque el informe sale distinto
    de lo que se penso y nadie sabe por que.
    """
    rutas: List[tuple[str, str, str]] = []
    for clave, valor in (formato or {}).items():
        entrada = _FORMATOS_COMUNES.get(clave)
        if entrada is None:
            raise VisualFactoryError(
                f"'{clave}' no es un formato conocido. Admitidos: "
                f"{sorted(_FORMATOS_COMUNES)}.",
                details={"unsupported": clave,
                         "supported": sorted(_FORMATOS_COMUNES)})
        scope, grupo, propiedad, admitidos = entrada
        if admitidos and str(valor) not in admitidos:
            raise VisualFactoryError(
                f"'{clave}={valor}' no existe. Valores: {list(admitidos)}.",
                details={"property": clave, "value": valor,
                         "allowed": list(admitidos)})
        destino = vis.setdefault(scope, {})
        bloques = destino.setdefault(grupo, [{}])
        if not bloques:
            bloques.append({})
        propiedades = bloques[0].setdefault("properties", {})
        propiedades[propiedad] = _lit(valor)
        rutas.append((scope, grupo, propiedad))
    return rutas


def _rutas_formato_generadas(vis: Dict[str, Any], *, completas: bool,
                             actual_type: str, title: Optional[str],
                             opciones: Dict[str, Any]) -> List[tuple[str, str, str]]:
    """Rutas que acabamos de escribir y que el oraculo debe reconocer."""
    if completas:
        return [
            (scope, grupo, propiedad)
            for scope in ("objects", "visualContainerObjects")
            for grupo, bloques in (vis.get(scope) or {}).items()
            for bloque in bloques if isinstance(bloque, dict)
            for propiedad in (bloque.get("properties") or {})
        ]

    rutas: List[tuple[str, str, str]] = []
    if title is not None:
        rutas.extend([("visualContainerObjects", "title", "text"),
                      ("visualContainerObjects", "title", "show")])
    if opciones.get("background_color"):
        rutas.extend([("visualContainerObjects", "background", "show"),
                      ("visualContainerObjects", "background", "color"),
                      ("visualContainerObjects", "background", "transparency")])
    if opciones.get("border_color"):
        rutas.extend([("visualContainerObjects", "border", "show"),
                      ("visualContainerObjects", "border", "color")])
        if opciones.get("border_radius") is not None:
            rutas.append(("visualContainerObjects", "border", "radius"))
    if actual_type in ("card", "cardVisual"):
        etiqueta = "label" if actual_type == "cardVisual" else "categoryLabels"
        valor = "value" if actual_type == "cardVisual" else "labels"
        if opciones.get("show_category_label") is False:
            rutas.append(("objects", etiqueta, "show"))
        if opciones.get("value_font_size") is not None:
            rutas.append(("objects", valor, "fontSize"))
        if opciones.get("bold_value"):
            rutas.append(("objects", valor, "bold"))
        if opciones.get("value_color"):
            propiedad = "fontColor" if actual_type == "cardVisual" else "color"
            rutas.append(("objects", valor, propiedad))
    return rutas


def _comprobar_formato_generado(documento: Dict[str, Any], *, completas: bool,
                                actual_type: str, title: Optional[str],
                                opciones: Dict[str, Any],
                                rutas_extra: Optional[List[tuple]] = None) -> None:
    """Falla pronto si el catalogo oficial no reconoce lo que generamos.

    `rutas_extra` son las que escribio el bloque `format`. Van explicitas
    porque el oraculo solo comprueba lo que se le declara: una ruta escrita y
    no declarada pasaria sin mirar, que es como se colaron en su dia los
    `objects` invalidos.
    """
    from horizun_pbi_mcp.services import format_oracle

    rutas = _rutas_formato_generadas(
        documento.get("visual") or {}, completas=completas,
        actual_type=actual_type, title=title, opciones=opciones)
    rutas = list(rutas) + list(rutas_extra or [])
    if rutas:
        format_oracle.assert_managed_paths(documento, rutas)


def find_template(active: ActivePbip, actual_type: str) -> Optional[Path]:
    """Busca un visual existente del mismo tipo para usar como plantilla."""
    pdir = pages_dir(active)
    for page in list_pages(active):
        vdir = pdir / page["name"] / "visuals"
        if not vdir.exists():
            continue
        for vf in sorted(vdir.glob("*/visual.json")):
            try:
                data = read_json(vf)
            except ValidationError:
                continue  # visual.json corrupto: se ignora como plantilla
            if data.get("visual", {}).get("visualType") == actual_type:
                return vf
    return None


def build_visual(
    active: ActivePbip,
    visual_type: str,
    fields: Dict[str, Any],
    position: Dict[str, float],
    title: Optional[str] = None,
    measure_index: Optional[Dict[str, str]] = None,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Devuelve (visual_dict, meta) donde meta incluye warnings y origen."""
    actual_type = resolve_type(visual_type)
    warnings: List[str] = []

    pos = _normalizar_posicion(position)

    if actual_type in DECORATIVOS:
        # No consultan datos: no se clonan plantillas (arrastrarian el texto o
        # el relleno de otro visual) y se construyen enteros desde `options`.
        if fields:
            raise VisualFactoryError(
                f"Un visual '{actual_type}' no lleva campos: su contenido se "
                "define en 'options' (text, fill, shape, target_page...).")
        opciones = options or {}
        vis = _build_decorativo(actual_type, opciones)
        # En un elemento de composicion `title` es el NOMBRE del visual dentro
        # del spec, no una etiqueta para el lienzo: encenderlo imprimia
        # "Titulo" sobre el titulo de una portada y "Logo Prodesa" sobre un
        # logo. Se muestra solo si se pide a proposito.
        if title is not None and opciones.get("show_title"):
            vis.setdefault("visualContainerObjects", {})["title"] = [
                {"properties": {"show": _lit(True), "text": _lit(title)}}]
        _aplicar_estilo_contenedor(vis, opciones)
        if actual_type == "textbox":
            _ajustar_alto_de_texto(pos, opciones, warnings)
        documento = {"$schema": SCHEMA_VISUAL, "position": pos, "visual": vis}
        _comprobar_formato_generado(
            documento, completas=True, actual_type=actual_type,
            title=title if opciones.get("show_title") else None,
            opciones=opciones)
        return {"visual": documento,
                "actual_type": actual_type,
                "origin": "elemento de composicion",
                "warnings": warnings}

    query = _build_query(actual_type, fields or {}, measure_index, warnings)
    _validate_role_contract(actual_type, query)
    template = find_template(active, actual_type)
    if template is not None:
        data = copy.deepcopy(read_json(template))
        data["$schema"] = data.get("$schema", SCHEMA_VISUAL)
        data.pop("name", None)  # el writer asigna id nuevo
        data.pop("filterConfig", None)
        data["position"] = pos
        vis = data.setdefault("visual", {})
        vis["visualType"] = actual_type
        vis["query"] = query
        vis.setdefault("drillFilterOtherVisuals", True)
        if title is not None:
            _set_title(vis, title)  # preserva el estilo del titulo de la plantilla
        else:
            _quitar_titulo_heredado(vis)
        _quitar_selectores_de_campos_obsoletos(vis, query, warnings)
        if actual_type in ("card", "cardVisual"):
            _aplicar_opciones_de_tarjeta(vis, actual_type, options or {})
        _aplicar_estilo_contenedor(vis, options or {})
        rutas_formato = _aplicar_formato(vis, (options or {}).get("format") or {})
        origin = f"clonado de {template}"
    else:
        vis = {
            "visualType": actual_type,
            "query": query,
            "drillFilterOtherVisuals": True,
        }
        if title is not None:
            _set_title(vis, title)
        if actual_type in ("card", "cardVisual"):
            _aplicar_opciones_de_tarjeta(vis, actual_type, options or {})
        _aplicar_estilo_contenedor(vis, options or {})
        rutas_formato = _aplicar_formato(vis, (options or {}).get("format") or {})
        data = {"$schema": SCHEMA_VISUAL, "position": pos, "visual": vis}
        origin = "plantilla minima (validar en Power BI Desktop)"
        warnings.append(
            "No habia un visual de este tipo para clonar; se genero una plantilla "
            "minima. Verifica el resultado en Power BI Desktop.")

    _comprobar_formato_generado(
        data, completas=False, actual_type=actual_type, title=title,
        opciones=options or {}, rutas_extra=rutas_formato)
    return {"visual": data, "actual_type": actual_type, "origin": origin, "warnings": warnings}
