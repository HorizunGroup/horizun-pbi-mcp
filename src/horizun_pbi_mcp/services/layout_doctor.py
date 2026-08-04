"""Diagnostico y correccion de layout PBIR. Determinista.

Detecta colisiones, elementos fuera del lienzo, tamanos anomalos, margenes,
separaciones inconsistentes y orden Z incoherente. Cada hallazgo lleva su
`rule` estable y su evidencia geometrica.

La correccion es determinista: la misma entrada produce siempre la misma
salida. Nada depende del orden de iteracion de un diccionario ni de un
identificador aleatorio.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.powerbi.errors import ValidationError

#: Tamano minimo razonable de un visual, en pixeles de lienzo.
MIN_ANCHO, MIN_ALTO = 80, 60
#: Margen minimo respecto al borde del lienzo.
MARGEN = 8
#: Cuantos visuales por pagina se consideran saturacion.
SATURACION = 12
# Desktop serializa geometria con decimales binarios: un visual pegado al
# borde puede acabar en 1000.2 sobre un lienzo de 1000 y se muestra entero.
# La misma tolerancia de medio pixel que usamos para solapes evita auditar eso
# como un error real y proponer una reescritura innecesaria.
EPSILON_GEOMETRIA = 0.5


def _caja(v: Dict[str, Any]) -> Tuple[float, float, float, float]:
    p = v.get("position", {}) or {}
    x = float(p.get("x", 0) or 0)
    y = float(p.get("y", 0) or 0)
    return x, y, x + float(p.get("width", 0) or 0), y + float(p.get("height", 0) or 0)


def _solapan(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Dict[str, float]]:
    ax1, ay1, ax2, ay2 = _caja(a)
    bx1, by1, bx2, by2 = _caja(b)
    ancho = min(ax2, bx2) - max(ax1, bx1)
    alto = min(ay2, by2) - max(ay1, by1)
    if ancho > 0.5 and alto > 0.5:
        return {"overlap_width": round(ancho, 1), "overlap_height": round(alto, 1),
                "overlap_area": round(ancho * alto, 1)}
    return None


def _hallazgo(rule: str, severity: str, objeto: Dict[str, Any],
              evidencia: Dict[str, Any], recomendacion: str,
              auto_fix: bool) -> Dict[str, Any]:
    return {"rule": rule, "severity": severity, "domain": "layout",
            "object": objeto, "evidence": evidencia,
            "recommendation": recomendacion, "auto_fix_available": auto_fix}


def detect_issues(visuals: List[Dict[str, Any]],
                  canvas: Dict[str, Any]) -> Dict[str, Any]:
    """Analiza la geometria de una pagina. Solo lectura."""
    W = float(canvas.get("width", 1280) or 1280)
    H = float(canvas.get("height", 720) or 720)
    hallazgos: List[Dict[str, Any]] = []

    ordenados = sorted(visuals, key=lambda v: (v.get("id") or ""))

    # Los elementos de composicion (fondos, bandas, titulos, botones) SE
    # SUPERPONEN a proposito: un rectangulo de fondo esta debajo de todo por
    # definicion, y un boton no muestra datos, asi que "demasiado pequeno para
    # leerse" no le aplica. Sin esta distincion una portada normal generaba
    # veinte avisos falsos y enterraba los de verdad.
    from horizun_pbi_mcp.pbip.visual_factory import DECORATIVOS

    def _es_composicion(v: Dict[str, Any]) -> bool:
        return str(v.get("type") or "") in DECORATIVOS

    graficos = [v for v in ordenados if not _es_composicion(v)]

    # --- colisiones (solo entre visuales de datos) ------------------------
    for i, a in enumerate(graficos):
        for b in graficos[i + 1:]:
            solape = _solapan(a, b)
            if solape:
                hallazgos.append(_hallazgo(
                    "layout_overlap", "warning",
                    {"kind": "visual_pair", "visuals": [a["id"], b["id"]]},
                    solape,
                    "Dos visuales se pisan. Reacomoda la pagina o ajusta sus "
                    "tamanos: en Power BI el de mayor z tapa al otro.", True))

    # --- fuera del lienzo y tamanos ---------------------------------------
    for v in ordenados:
        x1, y1, x2, y2 = _caja(v)
        fuera = {}
        if x1 < -EPSILON_GEOMETRIA:
            fuera["left"] = round(x1, 1)
        if y1 < -EPSILON_GEOMETRIA:
            fuera["top"] = round(y1, 1)
        if x2 > W + EPSILON_GEOMETRIA:
            fuera["right"] = round(x2 - W, 1)
        if y2 > H + EPSILON_GEOMETRIA:
            fuera["bottom"] = round(y2 - H, 1)
        if fuera:
            hallazgos.append(_hallazgo(
                "layout_out_of_canvas", "error",
                {"kind": "visual", "id": v["id"], "type": v.get("type")},
                {"canvas": {"width": W, "height": H}, "overflow": fuera},
                "El visual se sale del lienzo: parte no se vera al publicar. "
                "Muevelo o redimensionalo.", True))

        ancho, alto = x2 - x1, y2 - y1
        # El minimo legible aplica a los visuales que muestran DATOS. Un boton
        # o una banda de color no tienen ejes ni etiquetas que apretar.
        if not _es_composicion(v) and (ancho < MIN_ANCHO or alto < MIN_ALTO):
            hallazgos.append(_hallazgo(
                "layout_visual_too_small", "warning",
                {"kind": "visual", "id": v["id"], "type": v.get("type")},
                {"width": round(ancho, 1), "height": round(alto, 1),
                 "min_width": MIN_ANCHO, "min_height": MIN_ALTO},
                "Un visual tan pequeno no muestra sus datos de forma legible.",
                True))

        if x1 < MARGEN or y1 < MARGEN:
            hallazgos.append(_hallazgo(
                "layout_margin_too_small", "info",
                {"kind": "visual", "id": v["id"]},
                {"x": round(x1, 1), "y": round(y1, 1), "min_margin": MARGEN},
                f"Deja al menos {MARGEN}px de margen con el borde del lienzo.",
                True))

    # --- orden Z ----------------------------------------------------------
    zetas: Dict[float, List[str]] = {}
    for v in ordenados:
        z = (v.get("position") or {}).get("z")
        if z is not None:
            zetas.setdefault(float(z), []).append(v["id"])
    for z, ids in sorted(zetas.items()):
        if len(ids) > 1:
            hallazgos.append(_hallazgo(
                "layout_z_order_duplicated", "info",
                {"kind": "visual_group", "visuals": sorted(ids)},
                {"z": z, "count": len(ids)},
                "Varios visuales comparten el mismo orden Z: cual queda encima "
                "es indefinido. Asigna z distintos.", True))
    sin_z = [v["id"] for v in ordenados if (v.get("position") or {}).get("z") is None]
    if sin_z and zetas:
        hallazgos.append(_hallazgo(
            "layout_z_order_missing", "info",
            {"kind": "visual_group", "visuals": sorted(sin_z)},
            {"count": len(sin_z)},
            "Estos visuales no declaran z mientras otros si: el apilado queda "
            "a merced del orden de lectura.", True))

    # --- saturacion -------------------------------------------------------
    if len(ordenados) > SATURACION:
        hallazgos.append(_hallazgo(
            "layout_page_crowded", "info", {"kind": "page"},
            {"visual_count": len(ordenados), "threshold": SATURACION},
            "Muchos visuales en una pagina cuestan de leer y de renderizar. "
            "Reparte en varias paginas.", False))
    if not ordenados:
        hallazgos.append(_hallazgo(
            "layout_page_empty", "warning", {"kind": "page"}, {"visual_count": 0},
            "La pagina no tiene visuales.", False))

    # --- separaciones inconsistentes --------------------------------------
    gaps = _gaps_horizontales(ordenados)
    if len(set(gaps)) > 2 and len(gaps) >= 2:
        hallazgos.append(_hallazgo(
            "layout_inconsistent_gaps", "info", {"kind": "page"},
            {"observed_gaps": sorted(set(gaps))[:8]},
            "Las separaciones entre visuales no son uniformes. Normaliza el "
            "layout para que se vea ordenado.", True))

    por_severidad: Dict[str, int] = {}
    for h in hallazgos:
        por_severidad[h["severity"]] = por_severidad.get(h["severity"], 0) + 1
    return {
        "canvas": {"width": W, "height": H},
        "visual_count": len(ordenados),
        "issue_count": len(hallazgos),
        "by_severity": por_severidad,
        "issues": hallazgos,
        "clean": not hallazgos,
    }


def _gaps_horizontales(visuals: List[Dict[str, Any]]) -> List[int]:
    """Separaciones entre visuales que comparten banda vertical."""
    gaps: List[int] = []
    por_fila: Dict[int, List[Dict[str, Any]]] = {}
    for v in visuals:
        y = int(round(float((v.get("position") or {}).get("y", 0) or 0) / 20) * 20)
        por_fila.setdefault(y, []).append(v)
    for fila in por_fila.values():
        fila = sorted(fila, key=lambda v: float((v.get("position") or {}).get("x", 0) or 0))
        for a, b in zip(fila, fila[1:]):
            _, _, ax2, _ = _caja(a)
            bx1, _, _, _ = _caja(b)
            hueco = int(round(bx1 - ax2))
            if hueco >= 0:
                gaps.append(hueco)
    return gaps


# ------------------------------------------------------------- correcciones ---
def align(visuals: List[Dict[str, Any]], ids: List[str], edge: str,
          canvas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Alinea visuales por un borde. Devuelve las posiciones nuevas."""
    validos = {"left", "right", "top", "bottom", "center_h", "center_v"}
    if edge not in validos:
        raise ValidationError(f"edge invalido: '{edge}'. Usa {sorted(validos)}.")
    seleccion = [v for v in visuals if v["id"] in ids]
    if len(seleccion) < 2:
        raise ValidationError("Se necesitan al menos dos visuales para alinear.")

    cajas = {v["id"]: _caja(v) for v in seleccion}
    izq = min(c[0] for c in cajas.values())
    arr = min(c[1] for c in cajas.values())
    der = max(c[2] for c in cajas.values())
    aba = max(c[3] for c in cajas.values())

    salida = []
    for v in sorted(seleccion, key=lambda x: x["id"]):
        x1, y1, x2, y2 = cajas[v["id"]]
        w, h = x2 - x1, y2 - y1
        nx, ny = x1, y1
        if edge == "left":
            nx = izq
        elif edge == "right":
            nx = der - w
        elif edge == "top":
            ny = arr
        elif edge == "bottom":
            ny = aba - h
        elif edge == "center_h":
            nx = (izq + der) / 2 - w / 2
        elif edge == "center_v":
            ny = (arr + aba) / 2 - h / 2
        salida.append({"visual_id": v["id"], "x": round(nx), "y": round(ny),
                       "width": round(w), "height": round(h)})
    return salida


def distribute(visuals: List[Dict[str, Any]], ids: List[str],
               axis: str) -> List[Dict[str, Any]]:
    """Reparte visuales con separacion uniforme en un eje."""
    if axis not in ("horizontal", "vertical"):
        raise ValidationError("axis debe ser 'horizontal' o 'vertical'.")
    seleccion = [v for v in visuals if v["id"] in ids]
    if len(seleccion) < 3:
        raise ValidationError(
            "Se necesitan al menos tres visuales para distribuir (con dos, "
            "la separacion ya es la que hay).")

    cajas = {v["id"]: _caja(v) for v in seleccion}
    clave = 0 if axis == "horizontal" else 1
    ordenados = sorted(seleccion, key=lambda v: (cajas[v["id"]][clave], v["id"]))

    primero, ultimo = cajas[ordenados[0]["id"]], cajas[ordenados[-1]["id"]]
    if axis == "horizontal":
        inicio, fin = primero[0], ultimo[2]
        ocupado = sum(cajas[v["id"]][2] - cajas[v["id"]][0] for v in ordenados)
    else:
        inicio, fin = primero[1], ultimo[3]
        ocupado = sum(cajas[v["id"]][3] - cajas[v["id"]][1] for v in ordenados)
    hueco = max(0.0, (fin - inicio - ocupado) / (len(ordenados) - 1))

    salida, cursor = [], inicio
    for v in ordenados:
        x1, y1, x2, y2 = cajas[v["id"]]
        w, h = x2 - x1, y2 - y1
        if axis == "horizontal":
            salida.append({"visual_id": v["id"], "x": round(cursor), "y": round(y1),
                           "width": round(w), "height": round(h)})
            cursor += w + hueco
        else:
            salida.append({"visual_id": v["id"], "x": round(x1), "y": round(cursor),
                           "width": round(w), "height": round(h)})
            cursor += h + hueco
    return salida


def normalize(visuals: List[Dict[str, Any]],
              canvas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Corrige lo corregible sin reacomodar la pagina entera.

    Mete dentro del lienzo lo que se sale, sube al minimo lo demasiado pequeno
    y respeta el margen. No mueve lo que ya esta bien: es una correccion
    conservadora, no un reacomodo.
    """
    W = float(canvas.get("width", 1280) or 1280)
    H = float(canvas.get("height", 720) or 720)
    salida = []
    for v in sorted(visuals, key=lambda x: (x.get("id") or "")):
        x1, y1, x2, y2 = _caja(v)
        w = max(MIN_ANCHO, x2 - x1)
        h = max(MIN_ALTO, y2 - y1)
        w = min(w, W - 2 * MARGEN)
        h = min(h, H - 2 * MARGEN)
        x = min(max(x1, MARGEN), W - w - MARGEN)
        y = min(max(y1, MARGEN), H - h - MARGEN)
        nueva = {"visual_id": v["id"], "x": round(x), "y": round(y),
                 "width": round(w), "height": round(h)}
        if (round(x), round(y), round(w), round(h)) != (
                round(x1), round(y1), round(x2 - x1), round(y2 - y1)):
            salida.append(nueva)
    return salida
