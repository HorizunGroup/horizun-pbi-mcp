"""Generacion de hojas (paginas) de informe a partir del contenido existente.

Flujo pensado para lenguaje natural:
1. `building_blocks()` entrega el material: modelo (tablas/medidas/columnas),
   catalogo de visuales existentes (por tipo, reutilizables como plantilla),
   canvas y paginas. Con eso, Claude razona la instruccion del usuario.
2. Claude arma un `spec` (que visuales, campos, posiciones).
3. `render_html()` muestra la maqueta en HTML para revisarla ANTES de escribir.
4. `create_page_from_spec()` materializa la pagina como PBIR real (clonando
   plantillas existentes para respetar el estilo).
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import ValidationError
from pbip import layout_engine, pbir_reader, pbir_writer, visual_factory

log = get_logger("page_builder")

# Paleta "Control Room" (coincide con el estandar del usuario) para la maqueta HTML.
_TYPE_COLORS = {
    "card": "#2EC4B6", "cardVisual": "#2EC4B6",
    "clusteredColumnChart": "#4C9AFF", "clusteredBarChart": "#4C9AFF",
    "lineChart": "#F4A259", "pieChart": "#B980F0",
    "tableEx": "#8AA0B4", "pivotTable": "#8AA0B4", "slicer": "#21BF73",
}


def _detect_canvas(active: ActivePbip) -> Dict[str, int]:
    try:
        pages = pbir_reader.list_pages(active)
        widths = [p.get("width") for p in pages if p.get("width")]
        heights = [p.get("height") for p in pages if p.get("height")]
        if widths and heights:
            return {"width": max(widths), "height": max(heights)}
    except Exception:  # noqa: BLE001
        pass
    return {"width": 1280, "height": 720}


def building_blocks(active: ActivePbip, model_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Material para diseniar una hoja: modelo + catalogo de visuales + canvas."""
    tables = []
    measures = []
    if model_data:
        for t in model_data.get("tables", []):
            tables.append({
                "name": t["name"],
                "is_hidden": t.get("is_hidden"),
                "columns": [{"name": c["name"], "data_type": c.get("data_type"),
                             "is_hidden": c.get("is_hidden")}
                            for c in t.get("columns", [])],
                "measures": [m["name"] for m in t.get("measures", [])]
                if t.get("measures") else [],
            })
        measures = [{"name": m["name"], "table": m.get("table")}
                    for m in model_data.get("measures", [])]

    catalog: Dict[str, Any] = {}
    pages_info = []
    avisos: List[str] = []
    try:
        for page in pbir_reader.list_pages(active):
            pages_info.append({"id": page["name"], "name": page.get("display_name"),
                               "visuals": page.get("visual_count")})
            for v in pbir_reader.list_visuals(active, page["name"]):
                vt = v.get("type")
                entry = catalog.setdefault(vt, {"count": 0, "example": None})
                entry["count"] += 1
                if entry["example"] is None and (v.get("measures") or v.get("columns")):
                    entry["example"] = {"title": v.get("title"),
                                        "measures": v.get("measures"),
                                        "columns": v.get("columns"),
                                        "page": page.get("display_name")}
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer el catalogo de visuales: %s", exc)
        avisos.append(
            "No se pudo leer completo el catalogo de visuales: "
            f"{type(exc).__name__}: {exc}")

    return {
        "canvas": _detect_canvas(active),
        "model": {"tables": tables, "measures": measures},
        "visual_catalog": catalog,
        "existing_pages": pages_info,
        "supported_visual_types": visual_factory.SUPPORTED,
        "warnings": avisos,
        "hint": "Arma un spec con page_name, canvas y visuals[{type,title,fields,position}]. "
                "Omite position para auto-acomodar con 'layout'.",
    }


def _resolve_positions(visuals: List[Dict[str, Any]], canvas: Dict[str, int],
                       layout: Optional[str], spacing: int) -> List[Dict[str, Any]]:
    """Devuelve posiciones finales: respeta las dadas, calcula las que falten."""
    have_all = all(v.get("position") for v in visuals)
    if have_all and not layout:
        return [dict(v["position"]) for v in visuals]
    # auto-acomodo (grid por defecto) para los que no traen posicion o si se pide layout
    items = [{"visual_id": str(idx), "type": v.get("type")}
             for idx, v in enumerate(visuals)]
    computed = layout_engine.compute_layout(items, layout or "grid", canvas, spacing)
    by_id = {c["visual_id"]: c for c in computed}
    out = []
    for idx, v in enumerate(visuals):
        if v.get("position") and not layout:
            out.append(dict(v["position"]))
        else:
            c = by_id[str(idx)]
            out.append({"x": c["x"], "y": c["y"], "width": c["width"], "height": c["height"]})
    return out


def create_page_from_spec(active: ActivePbip, spec: Dict[str, Any],
                          measure_index: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Crea una pagina PBIR completa a partir de un spec.

    spec = {
      "page_name": str,
      "canvas": {"width","height"}   (opcional),
      "layout": "grid|dashboard|executive_summary"  (opcional, auto-acomodo),
      "spacing": int (opcional),
      "visuals": [ {"type","title","fields":{rol:refs},"position":{x,y,width,height}?} ]
    }
    """
    # --- 1. Validar el spec completo ANTES de tocar nada -------------------
    if not spec.get("page_name"):
        raise ValidationError("El spec necesita 'page_name'.")
    visuals = spec.get("visuals") or []
    if not visuals:
        raise ValidationError("El spec necesita al menos un visual en 'visuals'.")
    for idx, v in enumerate(visuals):
        if not isinstance(v, dict) or not v.get("type"):
            raise ValidationError(
                f"El visual en la posicion {idx} no declara 'type'.")
    canvas = spec.get("canvas") or _detect_canvas(active)
    layout = spec.get("layout")
    spacing = int(spec.get("spacing", 16))

    # --- 2. Resolver posiciones FINALES ------------------------------------
    positions = _resolve_positions(visuals, canvas, layout, spacing)

    # --- 3. Construir TODOS los visuales en memoria ------------------------
    # Si `build_visual` falla en el visual N, la excepcion sale de aqui y no se
    # ha escrito ni un byte: no queda una pagina vacia ni a medias.
    planificados = []
    warnings = []
    for v, pos in zip(visuals, positions):
        built = visual_factory.build_visual(
            active, v["type"], v.get("fields", {}), pos, v.get("title"), measure_index)
        planificados.append({
            "visual": built["visual"],
            "meta": {"type": built["actual_type"], "title": v.get("title"),
                     "origin": built["origin"]},
        })
        warnings.extend(built["warnings"])

    # --- 4. Una sola transaccion: pagina + metadatos + N visuales ----------
    result = pbir_writer.create_page_with_visuals(
        active, spec["page_name"], int(canvas["width"]), int(canvas["height"]),
        planificados, tool="pbi_create_page_from_spec")

    return {"page_id": result["page_id"], "page_name": spec["page_name"],
            "canvas": canvas, "created": result.get("created", True),
            "visuals_created": result["visuals_created"], "warnings": warnings,
            "backup": result.get("backup"), "transaction": result.get("transaction")}


# --------------------------------------------------------------- HTML maqueta ---
def _caja_decorativa(vtype: str, opciones: Dict[str, Any], estilo_pos: str,
                     z: float) -> Optional[str]:
    """Dibuja los elementos de composicion con su ASPECTO, no como alambre.

    Un rectangulo de relleno negro y un cuadro de texto blanco son cajas
    identicas en un wireframe, y ahi se pierde justo lo que hay que revisar:
    si la portada se ve. Estos se pintan con su color y su texto para poder
    juzgar la composicion sin abrir Power BI Desktop.
    """
    o = opciones or {}
    base = (f'style="{estilo_pos}z-index:{int(z)};box-sizing:border-box;'
            f'position:absolute;overflow:hidden;')
    if vtype == "shape":
        relleno = o.get("fill") or "#3A3A46"
        texto = html.escape(str(o.get("text") or ""))
        cuerpo = (f'<span style="color:{o.get("text_color") or "#FFF"};'
                  f'font-size:{float(o.get("font_size") or 14):.0f}px;'
                  f'font-weight:600">{texto}</span>') if texto else ""
        return (f'<div {base}background:{relleno};display:flex;'
                f'align-items:center;justify-content:center;">{cuerpo}</div>')
    if vtype == "textbox":
        return (f'<div {base}display:flex;align-items:center;'
                f'justify-content:{"center" if o.get("align") == "center" else "flex-start"};'
                f'color:{o.get("color") or "#E6E6EF"};'
                f'font-size:{float(o.get("font_size") or 12):.0f}px;'
                f'font-weight:{"700" if o.get("bold") else "400"};line-height:1.2">'
                f'{html.escape(str(o.get("text") or ""))}</div>')
    if vtype == "actionButton":
        return (f'<div {base}background:{o.get("fill") or "#2A78D6"};'
                f'border-radius:6px;display:flex;align-items:center;'
                f'justify-content:center;color:{o.get("text_color") or "#FFF"};'
                f'font-size:{float(o.get("font_size") or 12):.0f}px;font-weight:600">'
                f'{html.escape(str(o.get("text") or o.get("icon") or "boton"))}</div>')
    if vtype == "image":
        return (f'<div {base}background:#2B2B3D;border:1.5px dashed #6C7A99;'
                f'border-radius:6px;display:flex;align-items:center;'
                f'justify-content:center;color:#8AA0B4;font-size:11px">'
                f'{html.escape(str(o.get("resource") or "imagen"))}</div>')
    if vtype == "pageNavigator":
        return (f'<div {base}background:#252535;border:1.5px solid #2EC4B6;'
                f'border-radius:6px;display:flex;align-items:center;gap:6px;'
                f'padding:0 10px;color:#2EC4B6;font-size:11px">'
                f'&#9664; navegador de paginas &#9654;</div>')
    return None


def _visual_box_html(v: Dict[str, Any], canvas: Dict[str, int]) -> str:
    pos = v.get("position", {})
    x = float(pos.get("x", 0)); y = float(pos.get("y", 0))
    w = float(pos.get("width", 100)); h = float(pos.get("height", 60))
    cw = float(canvas.get("width", 1280)); ch = float(canvas.get("height", 720))
    left = 100 * x / cw; top = 100 * y / ch
    width = 100 * w / cw; height = 100 * h / ch
    vtype = v.get("type") or "?"

    decorativo = _caja_decorativa(
        vtype, v.get("options") or {},
        f"left:{left:.2f}%;top:{top:.2f}%;width:{width:.2f}%;height:{height:.2f}%;",
        float(pos.get("z", 0)))
    if decorativo is not None:
        return decorativo

    color = _TYPE_COLORS.get(vtype, "#8AA0B4")
    title = html.escape(str(v.get("title") or ""))
    fields = (v.get("measures") or []) + (v.get("columns") or [])
    # spec-style fields
    if not fields and isinstance(v.get("fields"), dict):
        for refs in v["fields"].values():
            fields += refs if isinstance(refs, list) else [refs]
    fields_html = "".join(
        f'<span class="chip">{html.escape(str(f))}</span>' for f in fields[:6])
    return (
        f'<div class="viz" style="left:{left:.2f}%;top:{top:.2f}%;'
        f'width:{width:.2f}%;height:{height:.2f}%;border-color:{color}">'
        f'<div class="viz-badge" style="background:{color}">{html.escape(vtype)}</div>'
        f'<div class="viz-title">{title}</div>'
        f'<div class="viz-fields">{fields_html}</div>'
        f'</div>'
    )


def render_html(title: str, canvas: Dict[str, int], visuals: List[Dict[str, Any]],
                standalone: bool = True) -> str:
    """Renderiza una maqueta HTML (wireframe) de una pagina."""
    cw = int(canvas.get("width", 1280)); ch = int(canvas.get("height", 720))
    boxes = "".join(_visual_box_html(v, canvas) for v in visuals)
    aspect = (ch / cw) * 100
    body = f"""
<div class="pb-wrap">
  <div class="pb-head">
    <span class="pb-title">{html.escape(title)}</span>
    <span class="pb-meta">{cw}&times;{ch} &middot; {len(visuals)} visuales</span>
  </div>
  <div class="pb-canvas" style="padding-bottom:{aspect:.3f}%">
    {boxes}
  </div>
</div>
"""
    style = """
<style>
  .pb-wrap{font-family:'Segoe UI',system-ui,sans-serif;max-width:1100px;margin:0 auto;color:#E6E6EF}
  .pb-head{display:flex;justify-content:space-between;align-items:baseline;margin:0 0 8px}
  .pb-title{font-size:18px;font-weight:600;color:#2EC4B6}
  .pb-meta{font-size:12px;color:#8AA0B4}
  .pb-canvas{position:relative;width:100%;height:0;background:#1A1A26;
    border:1px solid #2b2b3d;border-radius:8px;overflow:hidden}
  .viz{position:absolute;box-sizing:border-box;background:#252535;border:1.5px solid;
    border-radius:6px;padding:6px 8px;overflow:hidden;display:flex;flex-direction:column;gap:4px}
  .viz-badge{align-self:flex-start;font-size:9px;font-weight:600;color:#0d0d14;
    padding:1px 6px;border-radius:10px;text-transform:lowercase}
  .viz-title{font-size:12px;font-weight:600;color:#E6E6EF;line-height:1.2}
  .viz-fields{display:flex;flex-wrap:wrap;gap:3px;margin-top:auto}
  .chip{font-size:9px;color:#B7C0CC;background:#1f1f2c;border:1px solid #33334a;
    border-radius:4px;padding:1px 5px}
  @media (prefers-color-scheme: light){
    .pb-wrap{color:#1a1a26} .pb-meta{color:#5a6472}
  }
</style>
"""
    if standalone:
        return (f"<!doctype html><html><head><meta charset='utf-8'>"
                f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{html.escape(title)}</title>{style}</head>"
                f"<body style='background:#12121a;margin:0;padding:24px'>{body}</body></html>")
    return style + body


def page_to_html(active: ActivePbip, page: str, standalone: bool = True) -> str:
    """Maqueta HTML de una pagina EXISTENTE."""
    visuals = pbir_reader.list_visuals(active, page, strict=True)
    page_dir = pbir_reader.resolve_page_dir(active, page)
    from utils.json_utils import read_json
    pj = page_dir / "page.json"
    canvas = {"width": 1280, "height": 720}
    title = page
    if pj.exists():
        d = read_json(pj)
        canvas = {"width": d.get("width", 1280), "height": d.get("height", 720)}
        title = d.get("displayName") or page
    return render_html(title, canvas, visuals, standalone)


def spec_to_html(active: ActivePbip, spec: Dict[str, Any], standalone: bool = True) -> str:
    """Maqueta HTML de un spec PROPUESTO (antes de crearlo)."""
    canvas = spec.get("canvas") or _detect_canvas(active)
    visuals = spec.get("visuals") or []
    positions = _resolve_positions(visuals, canvas, spec.get("layout"),
                                   int(spec.get("spacing", 16)))
    enriched = []
    for v, pos in zip(visuals, positions):
        vv = dict(v)
        vv["position"] = pos
        enriched.append(vv)
    return render_html(spec.get("page_name", "Propuesta"), canvas, enriched, standalone)
