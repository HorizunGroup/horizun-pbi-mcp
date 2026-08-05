"""Cambiar de sistema de diseno sin recomponerlo todo a mano.

Dos hallazgos ALTO de una sesion real, que resultan ser el mismo:

- Se compuso con `sala` (1920x1080), se cambio a `informe` (1280x720) y se
  recompuso con `merge`. Los visuales de la composicion anterior se
  CONSERVARON fuera del lienzo: basura invisible que si viaja al render y a la
  publicacion, y que hay que descubrir con `pbi_detect_layout_issues`.
- La documentacion avisa «eligelo ANTES de la primera pagina», pero la gente
  cambia de opinion —en esa sesion se paso de oscuro a claro a mitad—, y
  cuando pasa la herramienta no ayudaba: a recomponer cada pagina a mano.

Y un detalle que no estaba dicho en ninguna parte: el color del texto de los
elementos decorativos SI sale del tema, pero se cuece AL COMPONER y queda
literal en el `visual.json`. Cambiar de sistema despues no reescribe lo ya
escrito, asi que los titulos compuestos en oscuro (#FFFFFF) quedan blancos
sobre blanco al pasar a claro. Invisibles, y sin que falle nada.

Lo que hace este modulo es lo unico honesto que se puede hacer con una pagina
ya escrita: **reescalarla** al lienzo nuevo y **recalcular** los colores que se
cocieron con el tema viejo. No es recomponer -no se sabe que intencion tenia
cada visual- pero deja la pagina utilizable en vez de rota.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError

log = get_logger("reflow")

#: Tipos cuyo color de texto se cocio del tema al componer.
DECORATIVOS_CON_COLOR = ("textbox", "shape")


def _canvas_de_pagina(pagina: Dict[str, Any]) -> Tuple[float, float]:
    return (float(pagina.get("width") or 0), float(pagina.get("height") or 0))


def paginas_con_otro_lienzo(active, canvas_destino: Dict[str, Any]
                            ) -> List[Dict[str, Any]]:
    """Paginas cuyo lienzo NO es el del sistema que se va a aplicar."""
    from horizun_pbi_mcp.pbip import pbir_reader

    ancho = float(canvas_destino.get("width") or 0)
    alto = float(canvas_destino.get("height") or 0)
    distintas = []
    for p in pbir_reader.list_pages(active, strict=False):
        pw, ph = _canvas_de_pagina(p)
        if pw and ph and (pw != ancho or ph != alto):
            distintas.append({"page": p["name"],
                              "display_name": p.get("display_name"),
                              "canvas": {"width": pw, "height": ph}})
    return distintas


def _reescalar(pos: Dict[str, Any], fx: float, fy: float,
               canvas: Dict[str, float]) -> Dict[str, float]:
    """Posicion proporcional al lienzo nuevo, acotada a sus limites."""
    ancho = max(1.0, round(float(pos.get("width") or 0) * fx))
    alto = max(1.0, round(float(pos.get("height") or 0) * fy))
    # El tamano se acota ANTES que el origen: un visual mas ancho que el
    # lienzo nuevo no puede caber, y encogerlo es preferible a dejarlo fuera.
    ancho = min(ancho, canvas["width"])
    alto = min(alto, canvas["height"])
    x = min(max(0.0, round(float(pos.get("x") or 0) * fx)), canvas["width"] - ancho)
    y = min(max(0.0, round(float(pos.get("y") or 0) * fy)), canvas["height"] - alto)
    nueva = {"x": float(x), "y": float(y),
             "width": float(ancho), "height": float(alto)}
    if pos.get("z") is not None:
        nueva["z"] = pos["z"]
    if pos.get("tabOrder") is not None:
        nueva["tabOrder"] = pos["tabOrder"]
    return nueva


def _color_literal(valor: Any) -> Optional[str]:
    """Extrae '#RRGGBB' de la forma anidada que usa PBIR, si la tiene."""
    try:
        crudo = valor["solid"]["color"]["expr"]["Literal"]["Value"]
    except (KeyError, TypeError):
        return None
    return str(crudo).strip("'") or None


def planificar(active, system: str,
               paginas: Optional[List[str]] = None) -> Dict[str, Any]:
    """Que cambiaria el reflujo, sin escribir nada."""
    from horizun_pbi_mcp.pbip import pbir_reader
    from horizun_pbi_mcp.services import design

    if system not in design.SISTEMAS:
        raise ValidationError(
            f"Sistema '{system}' no existe. Disponibles: "
            f"{sorted(design.SISTEMAS)}.")
    t = design.tokens(system)
    destino = {"width": float(t["canvas"]["width"]),
               "height": float(t["canvas"]["height"])}
    colores = design.tinta(system, design.paleta_del_informe(active))

    objetivo = {str(p).casefold() for p in paginas} if paginas else None
    plan: List[Dict[str, Any]] = []
    for p in pbir_reader.list_pages(active, strict=False):
        if objetivo and p["name"].casefold() not in objetivo and \
                str(p.get("display_name") or "").casefold() not in objetivo:
            continue
        pw, ph = _canvas_de_pagina(p)
        if not pw or not ph:
            continue
        fx = destino["width"] / pw
        fy = destino["height"] / ph

        visuales = []
        for v in pbir_reader.list_visuals(active, p["name"], strict=False):
            pos = v.get("position") or {}
            nueva = _reescalar(pos, fx, fy, destino)
            fuera = (float(pos.get("x") or 0) + float(pos.get("width") or 0)
                     > destino["width"]
                     or float(pos.get("y") or 0) + float(pos.get("height") or 0)
                     > destino["height"])
            entrada = {"visual_id": v["id"], "type": v.get("type"),
                       "was_out_of_bounds": bool(fuera),
                       "from": {k: pos.get(k) for k in ("x", "y", "width", "height")},
                       "to": {k: nueva[k] for k in ("x", "y", "width", "height")}}
            # El color cocido del tema viejo: si no es el del sistema nuevo,
            # se recalcula. Es lo que deja los titulos blancos sobre blanco.
            if v.get("type") in DECORATIVOS_CON_COLOR:
                entrada["recolor"] = colores.get("strong")
            visuales.append(entrada)

        if visuales or (pw, ph) != (destino["width"], destino["height"]):
            plan.append({
                "page": p["name"], "display_name": p.get("display_name"),
                "canvas_from": {"width": pw, "height": ph},
                "canvas_to": dict(destino),
                "scale": {"x": round(fx, 4), "y": round(fy, 4)},
                "visuals": visuales,
                "out_of_bounds_before": sum(
                    1 for x in visuales if x["was_out_of_bounds"]),
            })

    return {"system": system, "canvas": destino, "pages": plan,
            "page_count": len(plan),
            "visual_count": sum(len(p["visuals"]) for p in plan),
            "out_of_bounds_total": sum(p["out_of_bounds_before"] for p in plan)}


def _recolorear(data: Dict[str, Any], color: str) -> bool:
    """Reescribe el color de texto de un decorativo. True si cambio algo.

    Solo toca lo que el compositor escribio: el `color` del textbox y el
    `text_color` de una forma. No se inventa formato donde no lo habia.
    """
    from horizun_pbi_mcp.pbip.visual_factory import _lit

    vis = data.get("visual") or {}
    objetos = vis.get("objects")
    if not isinstance(objetos, dict):
        return False
    cambio = False
    for grupo, propiedad in (("general", "fontColor"),
                             ("text", "color"),
                             ("shape", "textColor")):
        for bloque in objetos.get(grupo) or []:
            props = bloque.get("properties") if isinstance(bloque, dict) else None
            if isinstance(props, dict) and propiedad in props:
                props[propiedad] = _lit(color)
                cambio = True
    # El textbox escribe el color dentro de su `paragraphs`, no en objects.
    for bloque in objetos.get("general") or []:
        parrafos = (bloque.get("properties") or {}).get("paragraphs")
        for parrafo in (parrafos or []) if isinstance(parrafos, list) else []:
            for run in (parrafo.get("textRuns") or []):
                estilo = run.get("textStyle")
                if isinstance(estilo, dict) and "color" in estilo:
                    estilo["color"] = color
                    cambio = True
    return cambio


def aplicar(active, system: str, paginas: Optional[List[str]] = None,
            dry_run: bool = True) -> Dict[str, Any]:
    """Reescala las paginas al lienzo del sistema y recalcula sus colores."""
    from horizun_pbi_mcp.pbip import pbir_writer
    from horizun_pbi_mcp.services import pbir_edit
    from horizun_pbi_mcp.services import txn as txn_service

    plan = planificar(active, system, paginas)
    if dry_run:
        return {**plan, "dry_run": True, "applied": False}
    if not plan["pages"]:
        return {**plan, "applied": False,
                "reason": "No hay paginas que reflujar."}

    # Se compila TODO antes de escribir nada: una transaccion para el lote
    # entero, no una por pagina. Si falla la tercera, las dos primeras no
    # pueden quedar reflujadas y el resto no.
    planificados: List[Dict[str, Any]] = []
    recoloreados = 0
    for pagina in plan["pages"]:
        updates = [{"visual_id": v["visual_id"], **v["to"]}
                   for v in pagina["visuals"]]
        if not updates:
            continue
        for p in pbir_writer.plan_visuals_bulk(active, pagina["page"], updates):
            entrada = next(v for v in pagina["visuals"]
                           if v["visual_id"] == p["id"])
            if entrada.get("recolor") and _recolorear(p["data"],
                                                      entrada["recolor"]):
                recoloreados += 1
            planificados.append(p)

    pbir_edit.assert_escritura_pbir(active, "Reflujar las paginas")
    cm = txn_service.project_transaction(
        active, [p["path"] for p in planificados], tool="pbi_reflow_pages")
    with cm as t:
        for p in planificados:
            t.write_json(p["path"], p["data"])

    log.info("Reflujo aplicado: %d visual(es) en %d pagina(s), %d recoloreado(s).",
             len(planificados), len(plan["pages"]), recoloreados)
    return {**plan, "applied": True, "dry_run": False,
            "visuals_moved": len(planificados),
            "visuals_recolored": recoloreados,
            "transaction": cm.result,
            "note": ("El reflujo reescala y recolorea; NO recompone. Si una "
                     "pagina necesita otra estructura, recomponla con "
                     "pbi_compose_page.")}
