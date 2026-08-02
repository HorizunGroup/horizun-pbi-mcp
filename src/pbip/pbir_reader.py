"""Lectura de informes PBIR (.Report/definition).

Estructura real (formato de reporte mejorado / PBIR):
    definition/pages/pages.json                 -> orden y pagina activa
    definition/pages/<pageId>/page.json         -> name, displayName, width, height
    definition/pages/<pageId>/visuals/<id>/visual.json  -> un visual por JSON
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PathSecurityError, PbirNotEnabledError, ValidationError
from services import paths as safe_paths
from utils.json_utils import read_json

log = get_logger("pbir_reader")


def report_definition_dir(active: ActivePbip) -> Path:
    if not active.has_pbir or not active.report_dir:
        raise PbirNotEnabledError(
            "El proyecto activo no tiene informe en formato PBIR. Guarda el informe "
            "como PBIP con el formato de reporte mejorado (PBIR) activado.")
    return Path(active.report_dir) / "definition"


def pages_dir(active: ActivePbip) -> Path:
    return report_definition_dir(active) / "pages"


def _literal_value(node: Any) -> Optional[str]:
    """Extrae expr.Literal.Value y le quita comillas TMDL/DAX externas."""
    try:
        val = node["expr"]["Literal"]["Value"]
    except (KeyError, TypeError):
        return None
    if isinstance(val, str) and len(val) >= 2 and val[0] == "'" and val[-1] == "'":
        return val[1:-1].replace("''", "'")
    return val


def _parse_field(field: Dict[str, Any]) -> Dict[str, Any]:
    for kind in ("Measure", "Column"):
        if kind in field:
            f = field[kind]
            entity = (f.get("Expression", {}).get("SourceRef", {}).get("Entity"))
            prop = f.get("Property")
            ref = f"{entity}[{prop}]" if entity else prop
            return {"kind": kind.lower(), "entity": entity, "property": prop, "ref": ref}
    if "Aggregation" in field:
        inner = field["Aggregation"].get("Expression", {})
        base = _parse_field(inner) if inner else {"kind": "unknown"}
        base = dict(base)
        base["aggregation"] = field["Aggregation"].get("Function")
        return base
    if "HierarchyLevel" in field:
        return {"kind": "hierarchylevel", "raw": field["HierarchyLevel"]}
    return {"kind": "unknown", "raw": field}


def _extract_fields(visual_node: Dict[str, Any]) -> Dict[str, Any]:
    query = visual_node.get("query", {})
    query_state = query.get("queryState", {})
    by_role: Dict[str, List[Dict[str, Any]]] = {}
    measures: List[str] = []
    columns: List[str] = []
    for role, spec in query_state.items():
        refs = []
        for proj in spec.get("projections", []):
            field = proj.get("field")
            if not field:
                continue
            parsed = _parse_field(field)
            # Son parte de la identidad de la proyeccion, especialmente para
            # agregaciones (`Sum(Tabla.Columna)`). Sin conservarlos, el viaje
            # reader -> factory podia reconstruir el campo pero no la misma
            # consulta que Desktop habia escrito.
            for clave in ("queryRef", "nativeQueryRef"):
                if clave in proj:
                    parsed[clave] = proj[clave]
            refs.append(parsed)
            if parsed["kind"] == "measure" and parsed.get("ref"):
                measures.append(parsed["ref"])
            elif parsed["kind"] == "column" and parsed.get("ref"):
                columns.append(parsed["ref"])
        if refs:
            by_role[role] = refs
    return {"by_role": by_role, "measures": measures, "columns": columns}


def _visual_title(visual_node: Dict[str, Any]) -> Optional[str]:
    try:
        title_objs = visual_node["visualContainerObjects"]["title"]
        props = title_objs[0]["properties"]["text"]
        return _literal_value(props)
    except (KeyError, IndexError, TypeError):
        return None


def read_visual_file(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    visual_node = data.get("visual", {})
    fields = _extract_fields(visual_node)
    return {
        "id": data.get("name"),
        "type": visual_node.get("visualType"),
        "position": data.get("position", {}),
        "title": _visual_title(visual_node),
        "fields": fields["by_role"],
        "measures": fields["measures"],
        "columns": fields["columns"],
        "file": str(path),
    }


def list_pages(active: ActivePbip) -> List[Dict[str, Any]]:
    pdir = pages_dir(active)
    order: List[str] = []
    active_page = None
    pages_json = pdir / "pages.json"
    if pages_json.exists():
        meta = read_json(pages_json)
        order = meta.get("pageOrder", [])
        active_page = meta.get("activePageName")

    page_dirs = {d.name: d for d in pdir.iterdir() if d.is_dir()}
    ordered_ids = [pid for pid in order if pid in page_dirs]
    ordered_ids += [pid for pid in page_dirs if pid not in ordered_ids]

    pages = []
    for pid in ordered_ids:
        pjson = page_dirs[pid] / "page.json"
        info: Dict[str, Any] = {"name": pid}
        if pjson.exists():
            try:
                d = read_json(pjson)
                info["display_name"] = d.get("displayName")
                info["width"] = d.get("width")
                info["height"] = d.get("height")
            except ValidationError:
                info["display_name"] = None
        vdir = page_dirs[pid] / "visuals"
        info["visual_count"] = sum(1 for _ in vdir.glob("*/visual.json")) if vdir.exists() else 0
        info["is_active"] = (pid == active_page)
        pages.append(info)
    return pages


def resolve_page_dir(active: ActivePbip, page: str) -> Path:
    """Resuelve una pagina por id de carpeta o por nombre visible.

    `page` viene del cliente MCP, asi que se trata como dato hostil: primero se
    rechaza cualquier sintaxis de ruta (`..`, separadores, unidad, UNC, ADS) y
    solo despues se combina con el root. Sin esto, `page="../../../../X"` o una
    ruta absoluta escribian fuera del proyecto.
    """
    pdir = pages_dir(active)
    safe_paths.assert_not_path_syntax(page, kind="pagina")

    # por id de carpeta (solo si ademas es un identificador valido)
    try:
        cand = safe_paths.safe_join(pdir, page, kind="id de pagina")
    except PathSecurityError:
        cand = None                      # puede seguir siendo un displayName
    if cand is not None and cand.is_dir():
        return cand

    # por displayName (case-insensitive)
    for d in pdir.iterdir():
        if not d.is_dir():
            continue
        pjson = d / "page.json"
        if pjson.exists():
            try:
                data = read_json(pjson)
            except ValidationError:
                continue
            if str(data.get("displayName", "")).lower() == str(page).lower():
                return d
    raise ValidationError(
        f"No se encontro la pagina '{page}'.",
        details={"available": [p["name"] for p in list_pages(active)]})


def list_visuals(active: ActivePbip, page: str, *,
                 strict: bool = False) -> List[Dict[str, Any]]:
    """Lista visuales; con ``strict`` no oculta archivos que no pudo leer.

    El modo tolerante mantiene el listado exploratorio existente. Los
    verificadores posteriores a una escritura deben usar ``strict=True``: si
    omiten un visual corrupto, pueden declarar válida una página incompleta.
    """
    page_dir = resolve_page_dir(active, page)
    vdir = page_dir / "visuals"
    visuals = []
    errores = []
    if vdir.exists():
        for vf in sorted(vdir.glob("*/visual.json")):
            try:
                visuals.append(read_visual_file(vf))
            except Exception as exc:  # noqa: BLE001 - un visual corrupto no debe tumbar el listado
                log.warning("Visual ilegible %s: %s", vf, exc)
                errores.append({"visual_id": vf.parent.name,
                                "file": str(vf),
                                "error": f"{type(exc).__name__}: {exc}"})
    if strict and errores:
        raise ValidationError(
            f"No se pudieron leer {len(errores)} visual(es) de la pagina "
            f"'{page}'; no se puede declarar valida una pagina incompleta.",
            details={"page": page, "unreadable_visuals": errores,
                     "readable_count": len(visuals)})
    return visuals
