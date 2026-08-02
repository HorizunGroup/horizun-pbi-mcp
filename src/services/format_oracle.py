"""Oraculo estructural para ``visual.objects``.

Los JSON Schema oficiales dejan el contenido de los objetos de formato
abierto. El catalogo del CLI oficial, en cambio, conoce para cada visualType
los grupos, propiedades, tipos y enums que Desktop entiende. Este modulo
consulta ese catalogo fijado por :mod:`services.report_validator`, lo cachea y
compara SOLO las rutas que Horizun acaba de generar. No examina ni bloquea
propiedades ajenas heredadas de una plantilla, por lo que una version nueva de
Desktop puede conservar extensiones que nuestro catalogo aun no conozca.
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from powerbi.errors import PowerBIMCPError


class FormatOracleMismatch(PowerBIMCPError):
    code = "format_oracle_mismatch"


_cache: Dict[str, Optional[Dict[str, Any]]] = {}
_lock = threading.Lock()
_MAX_CATALOG_BYTES = 8 * 1024 * 1024

# Snapshot MINIMO de las rutas que este servidor escribe. No contiene datos de
# informes ni pretende copiar el catalogo entero. Se genero con el CLI oficial
# 0.1.4 y una prueba viva exige que siga siendo subconjunto exacto del
# effective-properties instalado. Sirve de barrera offline y evita arrancar
# Node solo para comprobar VCO compartidos como title.show/title.text.
_SHARED_VCO = {
    "title": {"show": {"type": "bool"}, "text": {"type": "text"}},
    "background": {"show": {"type": "bool"}, "color": {"type": "fill"},
                   "transparency": {"type": "numeric"}},
    "border": {"show": {"type": "bool"}, "color": {"type": "fill"},
               "radius": {"type": "numeric"}},
    "visualLink": {
        "show": {"type": "bool"},
        "type": {"type": "enum", "values": [
            "Back", "Bookmark", "Drillthrough", "PageNavigation", "Qna",
            "WebUrl", "ApplyAllSlicers", "ClearAllSlicers", "DataFunction"]},
        "navigationSection": {"type": "unknown"},
        "bookmark": {"type": "unknown"},
    },
}
_SHAPE_ENUM = [
    "arrow", "arrowChevron", "arrowPentagon", "heart", "hexagon", "line",
    "octagon", "oval", "parallelogram", "pentagon", "pill", "rectangle",
    "rectangleRounded", "rectangleRoundedByPixel", "tabCutCorner",
    "tabCutTopCorners", "tabCutTopCornersByPixel", "tabRoundCorner",
    "tabRoundTopCorners", "speechbubbleRectangle", "trapezoid",
    "triangleIsoc", "triangleRight",
]
_ICON_ENUM = [
    "blank", "leftArrow", "rightArrow", "back", "reset", "help",
    "information", "qna", "bookmarks", "applyAllSlicers",
    "clearAllSlicers", "custom", "spinner",
]
_DATA_POINT_TYPES = frozenset({
    "areaChart", "stackedAreaChart", "hundredPercentStackedAreaChart",
    "barChart", "clusteredBarChart", "hundredPercentStackedBarChart",
    "columnChart", "clusteredColumnChart",
    "hundredPercentStackedColumnChart", "lineChart", "pieChart", "donutChart",
    "scatterChart", "treemap", "funnel", "gauge", "ribbonChart",
    "lineClusteredColumnComboChart", "lineStackedColumnComboChart",
})


def _snapshot_catalog(visual_type: str) -> Optional[Dict[str, Any]]:
    objects: Dict[str, Any] = {}
    if visual_type == "textbox":
        objects = {"general": {"paragraphs": {"type": "unknown"}}}
    elif visual_type == "shape":
        objects = {
            "shape": {"tileShape": {"type": "enum", "values": _SHAPE_ENUM}},
            "rotation": {"shapeAngle": {"type": "integer"}},
            "fill": {"fillColor": {"type": "fill"},
                     "transparency": {"type": "numeric"}},
            "text": {"show": {"type": "bool"}, "text": {"type": "text"},
                     "fontSize": {"type": "formatting"},
                     "fontColor": {"type": "fill"}},
        }
    elif visual_type == "image":
        objects = {"image": {"sourceFile": {
            "type": "image", "subProperties": {
                "name": {"type": "expr", "required": True},
                "url": {"type": "expr", "required": True},
                "scaling": {"type": "expr", "required": False}}}}}
    elif visual_type == "pageNavigator":
        objects = {"pages": {"showHiddenPages": {"type": "bool"},
                              "showPage": {"type": "bool"}}}
    elif visual_type == "actionButton":
        objects = {
            "icon": {"shapeType": {"type": "enum", "values": _ICON_ENUM}},
            "text": {"show": {"type": "bool"}, "text": {"type": "text"},
                     "fontSize": {"type": "formatting"},
                     "fontColor": {"type": "fill"}},
            "fill": {"fillColor": {"type": "fill"},
                     "transparency": {"type": "numeric"}},
        }
    elif visual_type == "card":
        objects = {"categoryLabels": {"show": {"type": "bool"}},
                   "labels": {"fontSize": {"type": "formatting"},
                              "bold": {"type": "bool"},
                              "color": {"type": "fill"}}}
    elif visual_type == "cardVisual":
        objects = {"label": {"show": {"type": "bool"}},
                   "value": {"fontSize": {"type": "formatting"},
                             "bold": {"type": "bool"},
                             "fontColor": {"type": "fill"}}}
    elif visual_type in {"table", "tableEx", "matrix", "pivotTable"}:
        props: Dict[str, Any] = {"backColor": {"type": "fill"}}
        if visual_type in {"tableEx", "pivotTable"}:
            props["fontColor"] = {"type": "fill"}
        objects = {"values": props}
    elif visual_type in _DATA_POINT_TYPES:
        objects = {"dataPoint": {"fill": {"type": "fill"}}}
    # Aunque el tipo no tenga objects administrados, todos los visuales pueden
    # recibir los VCO compartidos que generamos (por ejemplo un titulo).
    return {"catalogVersion": "powerbi-report-authoring-cli@0.1.4-managed",
            "visualObjects": objects,
            "visualContainerObjects": _SHARED_VCO}


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _remember(visual_type: str,
              value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    with _lock:
        _cache[visual_type] = value
    return value


def _load_catalog(visual_type: str) -> Optional[Dict[str, Any]]:
    """Devuelve effective-properties del CLI fijado, o None si no esta."""
    with _lock:
        if visual_type in _cache:
            return _cache[visual_type]

    from services import report_validator

    estado = report_validator.estado()
    node = report_validator._node()  # ruta absoluta resuelta por el backend
    cli = estado.get("cli_path")
    if not estado.get("available") or not node or not cli:
        return _remember(visual_type, None)
    try:
        proc = subprocess.run(
            [node, str(cli), "formatting", "effective-properties", visual_type],
            capture_output=True, text=False, shell=False, timeout=20,
            cwd=str(Path(cli).parent))
    except (OSError, subprocess.SubprocessError):
        return _remember(visual_type, None)
    if proc.returncode or len(proc.stdout or b"") > _MAX_CATALOG_BYTES:
        return _remember(visual_type, None)
    try:
        envelope = json.loads((proc.stdout or b"").decode("utf-8"))
        data = envelope.get("data")
    except (UnicodeDecodeError, ValueError, AttributeError):
        return _remember(visual_type, None)
    if not isinstance(data, dict):
        return _remember(visual_type, None)
    return _remember(visual_type, data)


def _literal(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    expr = value.get("expr")
    if not isinstance(expr, dict):
        return None
    literal = expr.get("Literal")
    raw = literal.get("Value") if isinstance(literal, dict) else None
    return raw if isinstance(raw, str) else None


def _unquote(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1].replace("''", "'")
    return raw


def _matches_kind(value: Any, definition: Dict[str, Any]) -> bool:
    kind = definition.get("type")
    if kind in ("unknown", "formatting", "expr"):
        return True
    if kind == "fill":
        if not isinstance(value, dict) or not isinstance(value.get("solid"), dict):
            return False
        solid = value["solid"]
        # Desktop usa ``solid.color.expr`` para un color literal y
        # ``solid.expr.FillRule`` para formato condicional. Ambos son formas
        # oficiales del mismo tipo ``fill``.
        color = solid.get("color")
        if isinstance(color, dict) and "expr" in color:
            return True
        expr = solid.get("expr")
        return isinstance(expr, dict) and isinstance(expr.get("FillRule"), dict)
    if kind == "image":
        image = value.get("image") if isinstance(value, dict) else None
        requeridas = {k for k, v in (definition.get("subProperties") or {}).items()
                      if v.get("required")}
        return isinstance(image, dict) and requeridas <= set(image)
    raw = _literal(value)
    if raw is None:
        return False
    limpio = _unquote(raw)
    if kind == "bool":
        return raw in ("true", "false")
    if kind == "integer":
        return raw.endswith("L") and raw[:-1].lstrip("-").isdigit()
    if kind == "numeric":
        try:
            float(raw[:-1] if raw.endswith(("D", "L")) else raw)
            return raw.endswith(("D", "L"))
        except ValueError:
            return False
    if kind == "text":
        return len(raw) >= 2 and raw[0] == raw[-1] == "'"
    if kind == "enum":
        return limpio in {str(v) for v in definition.get("values", [])}
    return True


PathSpec = Tuple[str, str, str]


def compare_managed_paths(document: Dict[str, Any], paths: Iterable[PathSpec],
                          *, catalog: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """Compara ``(scope, group, property)`` contra el catalogo oficial.

    ``scope`` es ``objects`` o ``visualContainerObjects``. Si el CLI no esta
    instalado, devuelve ``available=False``; la barrera gramatical local sigue
    actuando, pero no se finge equivalencia con un oraculo ausente.
    """
    visual = document.get("visual") if isinstance(document, dict) else None
    visual_type = visual.get("visualType") if isinstance(visual, dict) else None
    if not isinstance(visual_type, str) or not visual_type:
        return {"available": False, "reason": "visualType ausente", "errors": []}
    paths = list(paths)
    source = "injected"
    if catalog is None:
        # Los VCO son compartidos entre tipos; el snapshot fijado evita un
        # proceso Node por cada clase de grafico que solo recibe un titulo.
        if paths and all(scope == "visualContainerObjects"
                         for scope, _group, _prop in paths):
            catalog = _snapshot_catalog(visual_type)
            source = "managed_snapshot"
        else:
            catalog = _load_catalog(visual_type)
            source = "official_cli"
            if catalog is None:
                catalog = _snapshot_catalog(visual_type)
                source = "managed_snapshot"
    if catalog is None:
        return {"available": False, "reason": "catalogo oficial no disponible",
                "visual_type": visual_type, "errors": []}

    errores: List[Dict[str, str]] = []
    scope_keys = {"objects": "visualObjects",
                  "visualContainerObjects": "visualContainerObjects"}
    for scope, group, prop in paths:
        definiciones = catalog.get(scope_keys.get(scope, "")) or {}
        grupo_def = definiciones.get(group)
        ruta = f"$.visual.{scope}.{group}[*].properties.{prop}"
        if not isinstance(grupo_def, dict):
            errores.append({"path": ruta, "rule": "format_group_unknown"})
            continue
        prop_def = grupo_def.get(prop)
        if not isinstance(prop_def, dict):
            errores.append({"path": ruta, "rule": "format_property_unknown"})
            continue
        bloques = ((visual.get(scope) or {}).get(group) or [])
        valores = [(b.get("properties") or {}).get(prop) for b in bloques
                   if isinstance(b, dict) and prop in (b.get("properties") or {})]
        if not valores:
            errores.append({"path": ruta, "rule": "format_property_missing"})
            continue
        if any(not _matches_kind(valor, prop_def) for valor in valores):
            errores.append({"path": ruta, "rule": "format_property_shape"})
    return {"available": True, "visual_type": visual_type, "source": source,
            "catalog_version": catalog.get("catalogVersion"), "errors": errores}


def assert_managed_paths(document: Dict[str, Any], paths: Iterable[PathSpec],
                         *, catalog: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    result = compare_managed_paths(document, paths, catalog=catalog)
    if result["errors"]:
        raise FormatOracleMismatch(
            "El formato generado no equivale a una estructura que el "
            "catalogo oficial reconoce para ese visual.",
            details={"visual_type": result.get("visual_type"),
                     "errors": result["errors"], "rule": "format_oracle"})
    return result


def all_object_paths(document: Dict[str, Any]) -> List[PathSpec]:
    """Enumera todas las propiedades presentes en ``objects`` de un visual.

    El esquema PBIR deja esas claves abiertas. Esta enumeración permite hacer
    una comprobación de equivalencia completa cuando el catálogo oficial está
    instalado, sin inventar propiedades ni inspeccionar valores del informe.
    """
    visual = document.get("visual") if isinstance(document, dict) else None
    if not isinstance(visual, dict):
        return []
    rutas: List[PathSpec] = []
    for scope in ("objects", "visualContainerObjects"):
        grupos = visual.get(scope)
        if not isinstance(grupos, dict):
            continue
        for grupo, bloques in grupos.items():
            if not isinstance(bloques, list):
                continue
            for bloque in bloques:
                if not isinstance(bloque, dict):
                    continue
                propiedades = bloque.get("properties")
                if not isinstance(propiedades, dict):
                    continue
                rutas.extend((scope, str(grupo), str(propiedad))
                             for propiedad in propiedades)
    return rutas


def compare_all_managed_objects(document: Dict[str, Any]) -> Dict[str, Any]:
    """Compara todas las rutas del visual contra el catálogo de Desktop.

    Solo se considera evidencia de equivalencia cuando procede del CLI oficial
    (`source=official_cli`). El snapshot offline sigue siendo una barrera para
    las rutas administradas, pero no pretende describir cada propiedad que un
    informe real puede conservar.
    """
    rutas = all_object_paths(document)
    if not rutas:
        return {"available": False, "source": "none", "errors": []}
    resultado = compare_managed_paths(document, rutas)
    if resultado.get("source") != "official_cli":
        resultado = dict(resultado)
        resultado["errors"] = []
        resultado["equivalence_checked"] = False
    else:
        resultado["equivalence_checked"] = True
    return resultado
