"""Construccion de visuales PBIR (visual.json).

Estrategia (Fase 8):
1. Si existe un visual del mismo tipo en el informe, se CLONA su estructura
   (conserva el andamiaje de formato/tema) y se le inyectan los campos/titulo.
2. Si no existe plantilla, se crea una minima valida con un mapa de roles por
   tipo, y se AVISA que debe validarse en Power BI Desktop.
"""
from __future__ import annotations

import copy
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
HTML_CONTENT_TYPE = "htmlContent443BE3AD55E043BF878BED274D3A6865"

# Nombre amigable -> visualType real de PBIR
TYPE_MAP = {
    "card": "card",
    "cardVisual": "cardVisual",
    "table": "tableEx",
    "tableEx": "tableEx",
    "matrix": "pivotTable",
    "pivotTable": "pivotTable",
    "slicer": "slicer",
    "barchart": "clusteredBarChart",
    "clusteredbarchart": "clusteredBarChart",
    "columnchart": "clusteredColumnChart",
    "clusteredcolumnchart": "clusteredColumnChart",
    "linechart": "lineChart",
    "piechart": "pieChart",
    "htmlcontent": HTML_CONTENT_TYPE,
    HTML_CONTENT_TYPE.lower(): HTML_CONTENT_TYPE,
}

# Roles logicos -> clave de queryState por tipo real
ROLE_MAP = {
    "card": {"values": "Values"},
    "cardVisual": {"values": "Values"},
    "tableEx": {"values": "Values"},
    "pivotTable": {"rows": "Rows", "columns": "Columns", "values": "Values"},
    "slicer": {"values": "Values", "category": "Values"},
    "clusteredBarChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "clusteredColumnChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "lineChart": {"category": "Category", "values": "Y", "legend": "Series"},
    "pieChart": {"category": "Category", "values": "Y", "legend": "Series"},
    # HTML Content: una sola medida (que devuelve HTML/SVG) en el rol 'content'.
    HTML_CONTENT_TYPE: {"values": "content", "content": "content"},
}

SUPPORTED = sorted(set(TYPE_MAP))


def resolve_type(visual_type: str) -> str:
    key = str(visual_type).strip().lower()
    if key not in TYPE_MAP:
        raise VisualFactoryError(
            f"Tipo de visual no soportado: '{visual_type}'. "
            f"Soportados: {sorted(set(TYPE_MAP.values()))}.")
    return TYPE_MAP[key]


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


def _build_query(actual_type: str, fields: Dict[str, Any],
                 measure_index: Optional[Dict[str, str]],
                 warnings: List[str]) -> Dict[str, Any]:
    role_map = ROLE_MAP.get(actual_type, {"values": "Values"})
    query_state: Dict[str, Any] = {}
    for logical_role, role_key in role_map.items():
        refs = fields.get(logical_role)
        if not refs:
            continue
        if isinstance(refs, str):
            refs = [refs]
        projections = []
        for ref in refs:
            kind = _infer_kind(ref, measure_index)
            projections.append(_field_node(ref, kind, measure_index, warnings))
        # si el rol ya tenia proyecciones (p.ej. slicer values+category), extiende
        if role_key in query_state:
            query_state[role_key]["projections"].extend(projections)
        else:
            query_state[role_key] = {"projections": projections}
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
    else:
        vco["title"] = [{"properties": {"text": {"expr": {"Literal": {"Value": value}}}}}]


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
) -> Dict[str, Any]:
    """Devuelve (visual_dict, meta) donde meta incluye warnings y origen."""
    actual_type = resolve_type(visual_type)
    warnings: List[str] = []
    query = _build_query(actual_type, fields or {}, measure_index, warnings)

    pos = {
        "x": float(position["x"]),
        "y": float(position["y"]),
        "z": float(position.get("z", 0)),
        "width": float(position["width"]),
        "height": float(position["height"]),
        "tabOrder": float(position.get("z", 0)),
    }

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
        origin = f"clonado de {template}"
    else:
        vis = {
            "visualType": actual_type,
            "query": query,
            "drillFilterOtherVisuals": True,
        }
        if title is not None:
            _set_title(vis, title)
        data = {"$schema": SCHEMA_VISUAL, "position": pos, "visual": vis}
        origin = "plantilla minima (validar en Power BI Desktop)"
        warnings.append(
            "No habia un visual de este tipo para clonar; se genero una plantilla "
            "minima. Verifica el resultado en Power BI Desktop.")

    return {"visual": data, "actual_type": actual_type, "origin": origin, "warnings": warnings}
