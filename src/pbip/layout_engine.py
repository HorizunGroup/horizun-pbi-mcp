"""Motor de layout: calcula posiciones (x, y, width, height) para acomodar visuales.

Es geometria pura; no toca archivos. La tool pbi_arrange_visuals aplica el
resultado con pbir_writer.update_visual_position.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from powerbi.errors import ValidationError

CARD_TYPES = {"card", "cardVisual", "gauge", "kpi", "multiRowCard"}


def _grid_positions(items: List[Dict[str, Any]], x0: float, y0: float,
                    w: float, h: float, spacing: float) -> List[Dict[str, Any]]:
    n = len(items)
    if n == 0:
        return []
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = max(1, math.ceil(n / cols))
    cell_w = (w - (cols - 1) * spacing) / cols
    cell_h = (h - (rows - 1) * spacing) / rows
    out = []
    for idx, item in enumerate(items):
        r, c = divmod(idx, cols)
        out.append({
            "visual_id": item["visual_id"],
            "x": round(x0 + c * (cell_w + spacing)),
            "y": round(y0 + r * (cell_h + spacing)),
            "width": round(cell_w),
            "height": round(cell_h),
        })
    return out


def _row_positions(items: List[Dict[str, Any]], x0: float, y0: float,
                   w: float, h: float, spacing: float) -> List[Dict[str, Any]]:
    n = len(items)
    if n == 0:
        return []
    cell_w = (w - (n - 1) * spacing) / n
    return [{
        "visual_id": item["visual_id"],
        "x": round(x0 + i * (cell_w + spacing)),
        "y": round(y0),
        "width": round(cell_w),
        "height": round(h),
    } for i, item in enumerate(items)]


def compute_layout(
    items: List[Dict[str, Any]],
    layout: str = "grid",
    canvas: Optional[Dict[str, float]] = None,
    spacing: float = 16,
    custom: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[Dict[str, Any]]:
    """items: [{'visual_id', 'type'(opcional)}]. Devuelve posiciones nuevas."""
    canvas = canvas or {"width": 1280, "height": 720}
    W = float(canvas.get("width", 1280))
    H = float(canvas.get("height", 720))
    m = float(spacing)
    layout = (layout or "grid").lower()

    if not items:
        return []

    if layout == "custom":
        if not custom:
            raise ValidationError("El layout 'custom' requiere posiciones explicitas.")
        out = []
        for item in items:
            vid = item["visual_id"]
            if vid in custom:
                pos = custom[vid]
                out.append({"visual_id": vid, "x": float(pos["x"]), "y": float(pos["y"]),
                            "width": float(pos["width"]), "height": float(pos["height"])})
        return out

    if layout == "grid":
        return _grid_positions(items, m, m, W - 2 * m, H - 2 * m, m)

    if layout in ("dashboard", "executive_summary"):
        cards = [i for i in items if (i.get("type") in CARD_TYPES)]
        charts = [i for i in items if (i.get("type") not in CARD_TYPES)]
        # Si no hay tipos, usa los primeros como tarjetas.
        if not cards and not any("type" in i for i in items):
            k = min(4, len(items))
            cards, charts = items[:k], items[k:]

        positions: List[Dict[str, Any]] = []
        strip_h = 130.0
        y_cursor = m
        if cards:
            positions += _row_positions(cards, m, y_cursor, W - 2 * m, strip_h, m)
            y_cursor += strip_h + m

        region_h = H - y_cursor - m
        region_w = W - 2 * m
        if not charts:
            return positions

        if layout == "executive_summary" and len(charts) >= 1:
            hero = charts[0]
            rest = charts[1:]
            if rest:
                hero_w = region_w * 2 / 3 - m / 2
                positions.append({"visual_id": hero["visual_id"], "x": round(m),
                                  "y": round(y_cursor), "width": round(hero_w),
                                  "height": round(region_h)})
                side_x = m + hero_w + m
                positions += _grid_positions(rest, side_x, y_cursor,
                                             region_w - hero_w - m, region_h, m)
            else:
                positions.append({"visual_id": hero["visual_id"], "x": round(m),
                                  "y": round(y_cursor), "width": round(region_w),
                                  "height": round(region_h)})
            return positions

        # dashboard: rejilla de graficos debajo de las tarjetas
        positions += _grid_positions(charts, m, y_cursor, region_w, region_h, m)
        return positions

    raise ValidationError(
        f"Layout no soportado: '{layout}'. Usa grid|dashboard|executive_summary|custom.")
