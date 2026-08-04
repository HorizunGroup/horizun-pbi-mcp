"""Propuestas de tablero: mirar el modelo y sugerir, no esperar instrucciones.

`pbi_page_building_blocks` entrega el INVENTARIO (que tablas y campos hay). Eso
deja el diseño entero en manos de quien pregunta, y el resultado suele ser un
tablero correcto y anodino.

Aqui se da un paso mas: se clasifica lo que hay —que columna es un estado, cual
una fecha, cuales forman una familia de metricas comparables— y se devuelven
varias propuestas COMPLETAS y distintas entre si, cada una con el motivo por el
que se propone y un spec listo para aplicar.

La regla que gobierna todo esto: no inventar campos. Cada propuesta se arma con
lo que el modelo tiene de verdad, y si algo no da para una pagina, se dice en
vez de rellenarla con visuales de adorno.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("proposals")

#: Valores que delatan una columna de estado (semaforo).
_ESTADOS = {
    "apto", "no apto", "cerca", "ok", "ko", "si", "no", "yes", "no",
    "verde", "amarillo", "rojo", "green", "amber", "red", "pass", "fail",
    "cumple", "no cumple", "aprobado", "rechazado", "pendiente", "activo",
}
_RE_ESTADO_NOMBRE = re.compile(
    r"semaforo|estado|status|resultado|veredicto|apto|cumple", re.I)
#: Los tipos se comparan en minusculas: el lector TMDL los escribe como el
#: formato ('int64', 'string') y el lector en vivo como TOM ('Int64', 'String').
#: Compararlos tal cual dejaba TODAS las columnas sin clasificar segun de donde
#: se hubiera leido el modelo.
_NUMERICOS = {"int64", "double", "decimal"}
_TEXTO = {"string"}
_FECHA = {"datetime"}
#: Por encima de esto una columna no sirve para agrupar en un grafico.
_MAX_CATEGORIAS = 25


def _familias_de_columnas(columnas: List[str]) -> Dict[str, List[str]]:
    """Columnas que comparten prefijo y por tanto se comparan entre si.

    `m_configuracion`, `m_familias`, `m_vistas`... son diez metricas de la
    misma naturaleza guardadas una por columna. Detectarlo importa porque esa
    forma pide una matriz, y sin dinamizarlas hay que crear una medida por
    columna.
    """
    grupos: Dict[str, List[str]] = {}
    for c in columnas:
        for sep in ("_", " "):
            if sep in c:
                prefijo = c.split(sep)[0]
                if len(prefijo) <= 12:
                    grupos.setdefault(prefijo.lower(), []).append(c)
                break
    return {k: v for k, v in grupos.items() if len(v) >= 4}


def clasificar(model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Reparte columnas y medidas por el PAPEL que pueden desempeñar."""
    fechas: List[Tuple[str, str]] = []
    estados: List[Tuple[str, str]] = []
    categorias: List[Tuple[str, str]] = []
    numericas: List[Tuple[str, str]] = []
    familias: Dict[str, Dict[str, Any]] = {}

    for tabla in model_data.get("tables", []) or []:
        nombre = tabla.get("name") or ""
        if nombre.startswith(("LocalDateTable_", "DateTableTemplate_")):
            continue
        visibles = [c for c in tabla.get("columns", [])
                    if not c.get("is_hidden") and c.get("column_type") != "RowNumber"]
        nombres = [c["name"] for c in visibles]
        for c in visibles:
            tipo = str(c.get("data_type") or "").lower()
            par = (nombre, c["name"])
            # La fecha se decide por TIPO, no por el nombre: 'dias_pendiente'
            # contiene "dia" y es un entero, y proponer una linea temporal
            # sobre el seria un disparate presentado con seguridad.
            if tipo in _FECHA:
                fechas.append(par)
            elif tipo == "boolean" or _RE_ESTADO_NOMBRE.search(c["name"]):
                estados.append(par)
            elif tipo in _NUMERICOS:
                numericas.append(par)
            elif tipo in _TEXTO:
                categorias.append(par)
        detectadas = _familias_de_columnas(nombres)
        if detectadas:
            prefijo, miembros = max(detectadas.items(), key=lambda kv: len(kv[1]))
            familias[nombre] = {"prefix": prefijo, "columns": sorted(miembros)}

    medidas = [(m.get("table"), m.get("name")) for m in model_data.get("measures", [])
               if m.get("name")]
    return {"dates": fechas, "status": estados, "categories": categorias,
            "numeric": numericas, "measures": medidas, "families": familias}


def _ref(par: Tuple[str, str]) -> str:
    return f"{par[0]}[{par[1]}]"


def _card(ref: str, titulo: str, x: int, y: int = 20) -> Dict[str, Any]:
    return {"type": "card", "title": titulo, "fields": {"values": [ref]},
            "position": {"x": x, "y": y, "width": 290, "height": 100}}


def _proponer_estado(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not c["status"] or not c["measures"]:
        return None
    estado = c["status"][0]
    kpis = c["measures"][:4]
    categoria = c["categories"][0] if c["categories"] else None
    visuales = [_card(f"{t}[{m}]", m, 20 + 300 * i) for i, (t, m) in enumerate(kpis)]
    if categoria:
        visuales.append({
            "type": "barchart", "title": f"Por {categoria[1]}",
            "fields": {"category": [_ref(categoria)],
                       "values": [f"{kpis[0][0]}[{kpis[0][1]}]"]},
            "position": {"x": 20, "y": 140, "width": 590, "height": 285}})
    visuales.append({
        "type": "columnchart", "title": f"Reparto por {estado[1]}",
        "fields": {"category": [_ref(estado)],
                   "values": [f"{kpis[0][0]}[{kpis[0][1]}]"]},
        "position": {"x": 620, "y": 140, "width": 290, "height": 285}})
    visuales.append({"type": "slicer", "title": estado[1],
                     "fields": {"values": [_ref(estado)]},
                     "position": {"x": 920, "y": 140, "width": 290, "height": 285}})
    return {
        "page": "Estado general",
        "why": (f"El modelo tiene una columna de estado ('{estado[1]}') y "
                f"{len(c['measures'])} medidas: da para una portada de semaforo "
                "con KPIs arriba y el reparto debajo."),
        "spec": {"schema_version": "1.0",
                 "page": {"name": "Estado general", "width": 1280, "height": 720},
                 "visuals": visuales},
    }


def _proponer_familia(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not c["families"] or not c["categories"]:
        return None
    tabla, info = next(iter(c["families"].items()))
    categoria = next((p for p in c["categories"] if p[0] == tabla), c["categories"][0])
    medidas_familia = [f"{t}[{m}]" for t, m in c["measures"]
                       if any(col.lower() in str(m).lower() for col in info["columns"])]
    return {
        "page": "Comparativa por familia",
        "why": (f"'{tabla}' guarda {len(info['columns'])} columnas con el prefijo "
                f"'{info['prefix']}': son metricas comparables entre si, y esa "
                "forma pide una matriz. Ojo: al estar en columnas y no en filas, "
                "hace falta una medida por columna (o dinamizarlas con una tabla "
                "calculada)."),
        "needs": ([] if medidas_familia else
                  [f"Una medida por cada columna de '{info['prefix']}' "
                   f"({len(info['columns'])} en total), o una tabla calculada "
                   "que las dinamice."]),
        "spec": {"schema_version": "1.0",
                 "page": {"name": "Comparativa por familia",
                          "width": 1280, "height": 720},
                 "visuals": [{
                     "type": "matrix", "title": f"{categoria[1]} x {info['prefix']}",
                     "fields": {"rows": [_ref(categoria)],
                                "values": medidas_familia[:10]},
                     "position": {"x": 20, "y": 20, "width": 1190, "height": 400}}]}
        if medidas_familia else None,
    }


def _proponer_detalle(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if len(c["categories"]) < 2:
        return None
    tabla = c["categories"][0][0]
    columnas = [_ref(p) for p in c["categories"] if p[0] == tabla][:6]
    numericas = [_ref(p) for p in c["numeric"] if p[0] == tabla][:3]
    if len(columnas) < 2:
        return None
    return {
        "page": "Detalle",
        "why": (f"'{tabla}' tiene varias columnas descriptivas: sirve como lista "
                "de trabajo, que es lo que se acaba pidiendo para actuar sobre "
                "los casos concretos."),
        "spec": {"schema_version": "1.0",
                 "page": {"name": "Detalle", "width": 1280, "height": 720},
                 "visuals": [{
                     "type": "table", "title": f"Detalle de {tabla}",
                     "fields": {"values": columnas + numericas},
                     "position": {"x": 20, "y": 20, "width": 1190, "height": 660}}]},
    }


def _proponer_tendencia(c: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not c["dates"] or not c["measures"]:
        return None
    fecha = c["dates"][0]
    tabla, medida = c["measures"][0]
    return {
        "page": "Evolucion",
        "why": (f"Hay una columna de fecha ('{fecha[1]}'): permite ver la "
                "evolucion. Comprueba antes cuanto historico hay de verdad: con "
                "dos dias de datos una linea no dice nada."),
        "spec": {"schema_version": "1.0",
                 "page": {"name": "Evolucion", "width": 1280, "height": 720},
                 "visuals": [{
                     "type": "linechart", "title": f"{medida} en el tiempo",
                     "fields": {"category": [_ref(fecha)],
                                "values": [f"{tabla}[{medida}]"]},
                     "position": {"x": 20, "y": 20, "width": 1190, "height": 400}}]},
    }


def propose(model_data: Optional[Dict[str, Any]],
            theme_presets: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Propuestas de paginas y de tema, deducidas de lo que el modelo tiene."""
    if not model_data:
        return {"proposals": [], "reason": "No hay modelo cargado que analizar."}

    clasificacion = clasificar(model_data)
    candidatas = [_proponer_estado(clasificacion), _proponer_familia(clasificacion),
                  _proponer_detalle(clasificacion), _proponer_tendencia(clasificacion)]
    propuestas = [p for p in candidatas if p and p.get("spec")]

    faltantes: List[str] = []
    if not clasificacion["measures"]:
        faltantes.append(
            "El modelo no tiene NINGUNA medida: sin ellas los visuales caen en "
            "sumas implicitas y muestran numeros sin sentido (sumar 33 puntajes "
            "en vez de promediarlos). Crea las medidas antes de construir.")
    for p in candidatas:
        if p:
            faltantes.extend(p.get("needs") or [])

    log.info("Propuestas generadas: %s", len(propuestas))
    return {
        "proposals": propuestas,
        "detected": {k: len(v) for k, v in clasificacion.items() if k != "families"},
        "families": clasificacion["families"],
        "blockers": faltantes,
        "themes": theme_presets or [],
        "how_to_apply": ("Elige una propuesta y pasa su 'spec' a "
                         "pbi_validate_page_spec y luego a pbi_apply_page_spec. "
                         "El tema se aplica aparte con pbi_apply_theme."),
    }
