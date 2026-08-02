"""Cruza lo que un informe PBIR REFERENCIA contra el modelo TMDL real.

El validador oficial de Microsoft (`services.report_validator`) certifica que
cada visual.json tiene la FORMA correcta; no sabe que hay dentro del .pbip ni
si "Presupuesto Toal" (con la o y la a invertidas) existe de verdad. Un
informe puede pasar esa validacion con 0 errores y abrir en Desktop con una
tarjeta muda, porque Desktop resuelve el campo roto en silencio a nada: no
hay excepcion, no hay marca visible, solo un numero que nunca aparece.

Esto cierra ese hueco leyendo el TMDL de verdad (`tmdl_reader`) y comparando
cada `Measure`/`Column` que un visual.json o su `filterConfig` referencian.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

from config import ActivePbip
from pbip.pbir_reader import pages_dir
from utils.json_utils import read_json


def _measure_names(model_data: Dict[str, Any]) -> Set[str]:
    return {m["name"] for m in model_data.get("measures", []) if m.get("name")}


def _column_index(model_data: Dict[str, Any]) -> Dict[str, Set[str]]:
    return {t["name"]: {c["name"] for c in t.get("columns", []) if c.get("name")}
            for t in model_data.get("tables", []) if t.get("name")}


def _walk_field_refs(node: Any) -> List[Tuple[str, str, str]]:
    """(kind, entity, property) de cada nodo Measure/Column dentro de `node`.

    Solo se cuenta un nodo cuando trae `Expression.SourceRef.Entity`, el
    nombre REAL de la tabla. La mitad interna de un filtro referencia la
    tabla por ALIAS (`SourceRef.Source`), no por nombre, y esa forma se
    ignora aqui a proposito: comparar un alias de una letra contra nombres de
    tabla produciria falsos positivos en cada filtro que el informe tenga.
    """
    hallados: List[Tuple[str, str, str]] = []
    if isinstance(node, dict):
        for kind in ("Measure", "Column"):
            sub = node.get(kind)
            if isinstance(sub, dict):
                entidad = ((sub.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
                propiedad = sub.get("Property")
                if entidad and propiedad:
                    hallados.append((kind, entidad, propiedad))
        for value in node.values():
            hallados.extend(_walk_field_refs(value))
    elif isinstance(node, list):
        for item in node:
            hallados.extend(_walk_field_refs(item))
    return hallados


def check_report_references(active: ActivePbip,
                            model_data: Dict[str, Any]) -> Dict[str, Any]:
    """Referencias rotas: medida inexistente, tabla inexistente o columna
    inexistente en una tabla que si existe. Recorre TODO el visual.json (la
    consulta Y el `filterConfig`), asi que un filtro que apunta a una columna
    borrada se detecta igual que una tarjeta que apunta a una medida borrada.
    """
    medidas_conocidas = _measure_names(model_data)
    columnas_por_tabla = _column_index(model_data)
    tablas_conocidas = set(columnas_por_tabla)

    rotas: List[Dict[str, Any]] = []
    ilegibles: List[Dict[str, str]] = []
    revisados = 0

    for archivo in sorted(pages_dir(active).glob("*/visuals/*/visual.json")):
        try:
            documento = read_json(archivo)
        except Exception as exc:  # noqa: BLE001
            ilegibles.append({"file": str(archivo),
                              "error": f"{type(exc).__name__}: {exc}"})
            continue
        revisados += 1
        pagina = archivo.parent.parent.parent.name
        visual_id = documento.get("name")

        for kind, entidad, propiedad in _walk_field_refs(documento):
            if kind == "Measure":
                if propiedad not in medidas_conocidas:
                    rotas.append({
                        "file": str(archivo), "page": pagina, "visual_id": visual_id,
                        "kind": "measure", "table": entidad, "property": propiedad,
                        "reason": "medida_inexistente"})
            elif entidad not in tablas_conocidas:
                rotas.append({
                    "file": str(archivo), "page": pagina, "visual_id": visual_id,
                    "kind": "column", "table": entidad, "property": propiedad,
                    "reason": "tabla_inexistente"})
            elif propiedad not in columnas_por_tabla[entidad]:
                rotas.append({
                    "file": str(archivo), "page": pagina, "visual_id": visual_id,
                    "kind": "column", "table": entidad, "property": propiedad,
                    "reason": "columna_inexistente"})

    return {"checked": True, "visuals_checked": revisados,
            "broken_references": rotas, "unreadable_files": ilegibles,
            "valid": not rotas and not ilegibles}
