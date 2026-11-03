"""Orden y escala numerica opcionales de un visual del page spec."""
from __future__ import annotations

import copy
from typing import Any, Dict, List

from horizun_pbi_mcp.pbip import visual_factory
from horizun_pbi_mcp.powerbi.errors import ValidationError

CHARTS = ("barChart", "columnChart", "clusteredBarChart", "clusteredColumnChart")
DISPLAY_TYPES = (*CHARTS, "lineChart", "card", "cardVisual")
UNITS = {"none": 1, "auto": 0, "thousands": 1000, "millions": 1000000}


def validate(spec: Dict[str, Any]) -> List[Dict[str, str]]:
    """Errores relativos al visual; tambien sirve al constructor no versionado."""
    errors = []
    actual_type = visual_factory.TYPE_MAP.get(str(spec.get("type")).lower())
    if "sort" in spec:
        sort = spec["sort"]
        if actual_type not in CHARTS:
            errors.append({"path": "sort", "message":
                           "'sort' solo se admite en graficos de barras o columnas."})
        if not isinstance(sort, dict):
            errors.append({"path": "sort", "message": "'sort' debe ser un objeto."})
        else:
            field = sort.get("field")
            if (not isinstance(field, str) or "[" not in field
                    or not field.endswith("]") or not field.split("[", 1)[1][:-1]):
                errors.append({"path": "sort.field", "message":
                               "Usa 'Tabla[Columna]' o '[Medida]' en sort.field."})
            if sort.get("direction") not in ("desc", "asc"):
                errors.append({"path": "sort.direction", "message":
                               "sort.direction debe ser 'desc' o 'asc'."})
    for key in ("display_units", "decimals"):
        if key in spec and actual_type not in DISPLAY_TYPES:
            errors.append({"path": key, "message":
                           f"'{key}' no se admite en este tipo de visual."})
    if "display_units" in spec:
        units = spec["display_units"]
        if not isinstance(units, str) or units not in UNITS:
            errors.append({"path": "display_units", "message":
                           f"display_units debe ser uno de {list(UNITS)}."})
    if "decimals" in spec:
        decimals = spec["decimals"]
        if type(decimals) is not int or not 0 <= decimals <= 15:
            errors.append({"path": "decimals", "message":
                           "decimals debe ser un entero entre 0 y 15."})
    return errors


def apply(document: Dict[str, Any], spec: Dict[str, Any]) -> None:
    """Modifica solo claves pedidas, sobre la plantilla ya clonada en memoria.

    El orden usa la expresion proyectada, como `_convertir_sort` del conversor
    PBIX: no reconstruye columnas ni pierde agregaciones. Las rutas de formato
    son las del corpus sintetico exportado por Desktop (labels/value/valueAxis).
    """
    errors = validate(spec)
    if errors:
        raise ValidationError(errors[0]["message"], details={"errors": errors})
    visual = document["visual"]
    if "sort" in spec:
        query = visual["query"]
        available: Dict[str, Any] = {}
        for role in query["queryState"].values():
            for projection in role.get("projections", []):
                expression = projection["field"]
                node = expression.get("Aggregation", {}).get("Expression", expression)
                for kind in ("Column", "Measure"):
                    if kind not in node:
                        continue
                    field = node[kind]
                    table = field["Expression"]["SourceRef"]["Entity"]
                    name = field["Property"]
                    available[f"{table}[{name}]"] = expression
                    if kind == "Measure":
                        available[f"[{name}]"] = expression
        requested = spec["sort"]["field"].strip()
        if requested not in available:
            raise ValidationError(
                f"El campo de orden '{requested}' no existe entre los campos del "
                f"visual. Campos disponibles: {sorted(available)}.",
                details={"field": requested, "available_fields": sorted(available)})
        query["sortDefinition"] = {"sort": [{
            "field": copy.deepcopy(available[requested]),
            "direction": "Descending" if spec["sort"]["direction"] == "desc"
            else "Ascending"}]}
    properties = {}
    if "display_units" in spec:
        # La escala usa un literal decimal; 1 = sin abreviar, 0 = automatico.
        units = UNITS[spec["display_units"]]
        properties["labelDisplayUnits"] = {"expr": {"Literal": {"Value": f"{units}D"}}}
    if "decimals" in spec:
        properties["labelPrecision"] = visual_factory._lit(spec["decimals"])
    if properties:
        actual_type = visual["visualType"]
        groups = (("labels",) if actual_type == "card" else
                  ("value",) if actual_type == "cardVisual" else
                  ("labels", "valueAxis"))
        for group in groups:
            entries = visual.setdefault("objects", {}).setdefault(group, [])
            # Conserva estilo y selectores de la plantilla; explicita la escala
            # tambien en las entradas por serie para que no gane la heredada.
            if not entries:
                entries.append({"properties": {}})
            for entry in entries:
                entry.setdefault("properties", {}).update(copy.deepcopy(properties))
