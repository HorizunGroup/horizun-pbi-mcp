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

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import ValidationError, VisualFactoryError
from pbip.pbir_reader import list_pages, pages_dir
from pbip.pbir_writer import SCHEMA_VISUAL
from utils.json_utils import read_json

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
    "clusteredBarChart",
    "clusteredColumnChart",
    "lineChart",
    "pieChart",
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
    "barChart": "clusteredBarChart",
    "columnChart": "clusteredColumnChart",
    "htmlContent": HTML_CONTENT_TYPE,
    "text": "textbox",
    "rectangle": "shape",
    "navigation": "pageNavigator",
    "button": "actionButton",
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
    "cardVisual": {"values": "Data"},
    # Una tabla no distingue dimension de medida: todo va a `Values`, en el
    # orden en que se pide. Se acepta `category` como en el segmentador porque
    # el destino no es ambiguo —y descartarlo, que es lo que se hacia antes,
    # borraba una columna que alguien habia pedido sin decirlo.
    "tableEx": {"values": "Values", "category": "Values"},
    "pivotTable": {"rows": "Rows", "columns": "Columns", "values": "Values"},
    "slicer": {"values": "Values", "category": "Values"},
    "clusteredBarChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "clusteredColumnChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "lineChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "pieChart": {"category": "Category", "values": "Y", "legend": "Series"},
    # HTML Content: una sola medida (que devuelve HTML/SVG) en el rol 'content'.
    HTML_CONTENT_TYPE: {"values": "content", "content": "content"},
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
        normalizado.setdefault(clave, []).extend(
            normalizar_referencia(r) for r in lista)
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
        for ref in refs:
            kind = _infer_kind(ref, measure_index)
            projections.append(_field_node(ref, kind, measure_index, warnings))
        query_state[clave] = {"projections": projections}

    if not query_state:
        warnings.append("El visual no recibio campos; quedara vacio.")
    return {"queryState": query_state}


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


# ------------------------------------------------------------------ decorativos --
#: Formas admitidas por el visual `shape` de Power BI.
FORMAS = ("rectangle", "roundedRectangle", "oval", "line", "arrow", "triangle",
          "pentagon", "hexagon", "heart")
#: Iconos de `actionButton`. 'blank' es el boton de texto sin icono.
ICONOS_BOTON = ("blank", "back", "bookmark", "drillDown", "drillUp", "info",
                "question", "reset", "resetFilters", "chevronRight", "chevronLeft")


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


def _aplicar_opciones_de_tarjeta(vis: Dict[str, Any],
                                 opciones: Dict[str, Any]) -> None:
    """Formato del numero y de la etiqueta de una tarjeta.

    Con un titulo propio descriptivo, la etiqueta de categoria repite el mismo
    texto y ademas suele salir mas grande que el dato. Nada de esto se toca si
    no se pide: no se inventa formato que nadie encargo.
    """
    objetos = vis.setdefault("objects", {})
    if opciones.get("show_category_label") is False:
        objetos["categoryLabels"] = [{"properties": {"show": _lit(False)}}]

    props: Dict[str, Any] = {}
    if opciones.get("value_font_size") is not None:
        # El tamano va como numero crudo con sufijo D, sin comillas: `_lit`
        # entrecomillaria la cadena y Power BI lo leeria como texto.
        props["fontSize"] = {"expr": {"Literal": {
            "Value": f"{opciones['value_font_size']}D"}}}
    if opciones.get("bold_value"):
        props["bold"] = _lit(True)
    if opciones.get("value_color"):
        props["color"] = {"solid": {"color": _lit(opciones["value_color"])}}
    if props:
        objetos["labels"] = [{"properties": props}]
    if not objetos:
        vis.pop("objects", None)


def _build_shape(opciones: Dict[str, Any]) -> Dict[str, Any]:
    forma = opciones.get("shape", "rectangle")
    if forma not in FORMAS:
        raise VisualFactoryError(
            f"Forma no soportada: '{forma}'. Usa una de {list(FORMAS)}.")
    objetos: Dict[str, Any] = {
        "shape": _props(tileShape=forma),
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

    objetos: Dict[str, Any] = {"icon": [{"properties": {"shapeType": _lit(icono)},
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

    pos = {
        "x": float(position["x"]),
        "y": float(position["y"]),
        "z": float(position.get("z", 0)),
        "width": float(position["width"]),
        "height": float(position["height"]),
        "tabOrder": float(position.get("z", 0)),
    }

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
        # "Titulo" sobre el titulo de una portada y "Logo Acme" sobre un
        # logo. Se muestra solo si se pide a proposito.
        if title is not None and opciones.get("show_title"):
            vis.setdefault("visualContainerObjects", {})["title"] = [
                {"properties": {"show": _lit(True), "text": _lit(title)}}]
        if actual_type == "textbox":
            _ajustar_alto_de_texto(pos, opciones, warnings)
        return {"visual": {"$schema": SCHEMA_VISUAL, "position": pos, "visual": vis},
                "actual_type": actual_type,
                "origin": "elemento de composicion",
                "warnings": warnings}

    query = _build_query(actual_type, fields or {}, measure_index, warnings)
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
        if actual_type in ("card", "cardVisual"):
            _aplicar_opciones_de_tarjeta(vis, options or {})
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
            _aplicar_opciones_de_tarjeta(vis, options or {})
        data = {"$schema": SCHEMA_VISUAL, "position": pos, "visual": vis}
        origin = "plantilla minima (validar en Power BI Desktop)"
        warnings.append(
            "No habia un visual de este tipo para clonar; se genero una plantilla "
            "minima. Verifica el resultado en Power BI Desktop.")

    return {"visual": data, "actual_type": actual_type, "origin": origin, "warnings": warnings}
