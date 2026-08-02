"""Auditoria del modelo semantico con reglas de identificador ESTABLE.

Cada regla tiene un `rule` que no cambia entre versiones: un cliente puede
silenciar `measure_without_format` sin miedo a que el identificador se renombre.

Ninguna heuristica se presenta como certeza. Cada hallazgo lleva su `evidence`
—los datos concretos que la dispararon— para que quien lo lea pueda juzgar, y
`auto_fix_available` dice si existe una correccion mecanica y segura.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from services import model_explorer, scoring

INFO, WARNING, ERROR = "info", "warning", "error"

_ID = re.compile(r"(?i)(^|[ _])(id|c[oó]digo|code|key|llave|clave)([ _0-9]*)$")
_ID_CAMEL = re.compile(r".+(ID|Id|Key|KEY)$")
_CALENDARIO = re.compile(r"(?i)calendar|fecha|date|dim[_ ]?date")
_DAX_LARGO = 1500


@dataclass
class Regla:
    rule: str
    severity: str
    dominio: str
    descripcion: str
    fn: Callable[[Dict[str, Any], Dict[str, Any]], List[Dict[str, Any]]]
    auto_fix: bool = False


_REGLAS: List[Regla] = []
_POR_ID: Dict[str, Regla] = {}


def regla(rule: str, severity: str, dominio: str, descripcion: str,
          auto_fix: bool = False):
    def deco(fn):
        r = Regla(rule, severity, dominio, descripcion, fn, auto_fix)
        _REGLAS.append(r)
        _POR_ID[rule] = r
        return fn
    return deco


def _hallazgo(rule_id: str, objeto: Dict[str, Any], evidencia: Dict[str, Any],
              recomendacion: str) -> Dict[str, Any]:
    """Construye un hallazgo buscando la regla por su ID.

    Se busca por ID y no por indice: una regla nueva insertada en medio no puede
    hacer que otra reporte la severidad equivocada.
    """
    r = _POR_ID[rule_id]
    return {"rule": r.rule, "severity": r.severity, "domain": r.dominio,
            "object": objeto, "evidence": evidencia,
            "recommendation": recomendacion,
            "auto_fix_available": r.auto_fix}


# ------------------------------------------------------------------ reglas ---
@regla("relationship_bidirectional", WARNING, "relationships",
       "Relacion con filtro cruzado bidireccional.")
def _bidireccional(md, idx):
    out = []
    for r in md.get("relationships", []):
        if str(r.get("cross_filtering")) in ("BothDirections", "bothDirections"):
            out.append(_hallazgo(
                "relationship_bidirectional", {"kind": "relationship", "name": r.get("name"),
                             "from": r.get("from_table"), "to": r.get("to_table")},
                {"cross_filtering": r.get("cross_filtering")},
                "Las bidireccionales pueden crear ambiguedad y afectar el "
                "rendimiento. Usa 'single' salvo que la necesites de verdad."))
    return out


@regla("table_disconnected", WARNING, "relationships",
       "Tabla que no participa en ninguna relacion.")
def _desconectada(md, idx):
    conectadas = {r.get("from_table") for r in md.get("relationships", [])} | \
                 {r.get("to_table") for r in md.get("relationships", [])}
    out = []
    for t in md.get("tables", []):
        if t["name"] in conectadas or t.get("is_date_table"):
            continue
        if t.get("measure_count", 0) > 0:
            continue          # tabla de solo medidas: patron legitimo
        out.append(_hallazgo(
            "table_disconnected", {"kind": "table", "name": t["name"]},
            {"columns": len(t.get("columns", [])),
             "measures": t.get("measure_count", 0)},
            "Una tabla sin relaciones no puede filtrar ni ser filtrada. "
            "Relacionala, conviertela en tabla de medidas o eliminala."))
    return out


@regla("measure_without_description", INFO, "measures",
       "Medida sin descripcion.", auto_fix=False)
def _sin_descripcion(md, idx):
    return [_hallazgo("measure_without_description",
                      {"kind": "measure", "name": m["name"], "table": m.get("table")},
                      {"has_description": False},
                      "Documenta que calcula y en que unidades. Es lo que lee "
                      "un usuario en el panel de campos.")
            for m in md.get("measures", []) if not m.get("description")]


@regla("measure_without_format", WARNING, "measures",
       "Medida sin formato definido.", auto_fix=True)
def _sin_formato(md, idx):
    return [_hallazgo("measure_without_format",
                      {"kind": "measure", "name": m["name"], "table": m.get("table")},
                      {"format_string": None},
                      "Sin formato, Power BI muestra el numero crudo. Define "
                      "formatString (p.ej. '#,0.00' o '0.0%').")
            for m in md.get("measures", []) if not m.get("format_string")]


@regla("measure_possibly_unused", INFO, "measures",
       "Medida a la que no hace referencia ninguna otra.")
def _no_usada(md, idx):
    out = []
    for m in md.get("measures", []):
        usada = False
        for otra in md.get("measures", []):
            if otra["name"] == m["name"]:
                continue
            refs = model_explorer.extract_references(otra.get("expression"))
            if m["name"] in refs["unqualified"] or \
                    any(r.endswith(f"[{m['name']}]") for r in refs["columns"]):
                usada = True
                break
        if not usada:
            out.append(_hallazgo(
                "measure_possibly_unused", {"kind": "measure", "name": m["name"],
                             "table": m.get("table")},
                {"referenced_by_measures": 0},
                "Ninguna otra medida la usa. Puede estar bien (medida final de "
                "un visual) o ser un resto. Comprueba si algun informe la usa "
                "antes de borrarla."))
    return out


@regla("measure_broken_reference", ERROR, "measures",
       "La expresion referencia un objeto que no existe.")
def _referencia_rota(md, idx):
    out = []
    for m in md.get("measures", []):
        refs = model_explorer.extract_references(m.get("expression"))
        for r in refs["columns"] + refs["unqualified"]:
            if not model_explorer.resolve_reference(r, idx, m.get("table"))["exists"]:
                out.append(_hallazgo(
                    "measure_broken_reference", {"kind": "measure", "name": m["name"],
                                 "table": m.get("table")},
                    {"missing_reference": r},
                    f"'{r}' no existe en el modelo. La medida fallara al "
                    "evaluarse. Corrige la referencia o crea el objeto."))
    return out


@regla("measure_dax_too_long", INFO, "dax",
       "Expresion DAX muy larga.")
def _dax_largo(md, idx):
    out = []
    for m in md.get("measures", []):
        expr = m.get("expression") or ""
        if len(expr) > _DAX_LARGO:
            out.append(_hallazgo(
                "measure_dax_too_long", {"kind": "measure", "name": m["name"],
                             "table": m.get("table")},
                {"length": len(expr), "threshold": _DAX_LARGO},
                "Divide en medidas intermedias o usa VAR. Facilita depurarla "
                "y suele mejorar el rendimiento."))
    return out


@regla("column_id_visible", INFO, "columns",
       "Columna que parece un identificador y esta visible.", auto_fix=True)
def _id_visible(md, idx):
    out = []
    for t in md.get("tables", []):
        for c in t.get("columns", []):
            nombre = c["name"]
            if c.get("is_hidden"):
                continue
            if _ID.search(nombre) or _ID_CAMEL.match(nombre):
                out.append(_hallazgo(
                    "column_id_visible", {"kind": "column", "name": f"{t['name']}[{nombre}]",
                                 "table": t["name"]},
                    {"is_hidden": False, "matched_pattern": "id-like"},
                    "Las claves tecnicas suelen ocultarse: no aportan al usuario "
                    "y ensucian el panel de campos."))
    return out


@regla("column_calculated", INFO, "columns",
       "Columna calculada: evalua si puede resolverse antes.")
def _calculada(md, idx):
    out = []
    for t in md.get("tables", []):
        for c in t.get("columns", []):
            if c.get("column_type") == "Calculated":
                out.append(_hallazgo(
                    "column_calculated", {"kind": "column", "name": f"{t['name']}[{c['name']}]",
                                 "table": t["name"]},
                    {"expression_length": len(c.get("expression") or "")},
                    "Una columna calculada ocupa memoria y se materializa al "
                    "refrescar. Valora hacerlo en Power Query o con una medida."))
    return out


@regla("model_no_date_table", WARNING, "model",
       "No se detecta una tabla de calendario.")
def _sin_calendario(md, idx):
    for t in md.get("tables", []):
        if t.get("is_date_table") or _CALENDARIO.search(t["name"]):
            return []
    return [_hallazgo("model_no_date_table", {"kind": "model"},
                      {"tables_checked": len(md.get("tables", []))},
                      "Sin tabla de fechas marcada, la inteligencia de tiempo "
                      "(TOTALYTD, SAMEPERIODLASTYEAR...) puede fallar.")]


@regla("model_no_rls", INFO, "security",
       "El modelo no define ningun rol de seguridad.")
def _sin_rls(md, idx):
    if md.get("roles"):
        return []
    return [_hallazgo("model_no_rls", {"kind": "model"}, {"roles": 0},
                      "Si el informe se compartira con perfiles distintos, "
                      "define roles RLS. Si es de uso propio, ignoralo.")]


@regla("naming_inconsistent", INFO, "naming",
       "Nombre con espacios sobrantes o dobles.")
def _naming(md, idx):
    out = []
    objetos = [("measure", m["name"], m.get("table")) for m in md.get("measures", [])]
    objetos += [("table", t["name"], None) for t in md.get("tables", [])]
    objetos += [("column", f"{t['name']}[{c['name']}]", t["name"])
                for t in md.get("tables", []) for c in t.get("columns", [])]
    for kind, nombre, tabla in objetos:
        crudo = nombre.split("[")[-1].rstrip("]")
        if crudo != crudo.strip() or "  " in crudo:
            out.append(_hallazgo(
                "naming_inconsistent", {"kind": kind, "name": nombre, "table": tabla},
                {"raw_name": repr(crudo)},
                "Quita los espacios sobrantes: se ven en el panel de campos y "
                "obligan a citar el nombre en DAX."))
    return out


@regla("column_suspicious_type", INFO, "columns",
       "Columna con nombre de fecha pero tipo no temporal.")
def _tipo_sospechoso(md, idx):
    out = []
    for t in md.get("tables", []):
        for c in t.get("columns", []):
            tipo = str(c.get("data_type") or "").lower()
            if _CALENDARIO.search(c["name"]) and tipo and "date" not in tipo \
                    and "time" not in tipo:
                out.append(_hallazgo(
                    "column_suspicious_type", {"kind": "column",
                                  "name": f"{t['name']}[{c['name']}]",
                                  "table": t["name"]},
                    {"data_type": c.get("data_type")},
                    "El nombre sugiere una fecha pero el tipo no lo es. Si "
                    "guarda fechas como texto, la inteligencia de tiempo fallara."))
    return out


# ------------------------------------------------------------------ motor ----
def reglas_disponibles() -> List[Dict[str, Any]]:
    return [{"rule": r.rule, "severity": r.severity, "domain": r.dominio,
             "description": r.descripcion, "auto_fix_available": r.auto_fix}
            for r in _REGLAS]


def audit(model_data: Dict[str, Any], *,
          rules: Optional[List[str]] = None,
          min_severity: str = INFO) -> Dict[str, Any]:
    """Ejecuta las reglas y devuelve los hallazgos agrupados."""
    orden = {INFO: 0, WARNING: 1, ERROR: 2}
    minimo = orden.get(min_severity, 0)
    seleccion = [r for r in _REGLAS
                 if (rules is None or r.rule in rules)
                 and orden[r.severity] >= minimo]

    idx = model_explorer.build_index(model_data)
    hallazgos: List[Dict[str, Any]] = []
    errores_regla: List[Dict[str, Any]] = []
    for r in seleccion:
        try:
            hallazgos.extend(r.fn(model_data, idx))
        except Exception as exc:  # noqa: BLE001 - una regla no puede tumbar la auditoria
            errores_regla.append({"rule": r.rule, "error": str(exc)})

    por_severidad: Dict[str, int] = {}
    por_dominio: Dict[str, int] = {}
    for h in hallazgos:
        por_severidad[h["severity"]] = por_severidad.get(h["severity"], 0) + 1
        por_dominio[h["domain"]] = por_dominio.get(h["domain"], 0) + 1

    # Puntaje normalizado: cumplimiento por regla, no recuento de hallazgos.
    # Ver services/scoring.py para por que el recuento medía el tamano.
    puntuacion = scoring.compute(
        hallazgos,
        [{"rule": r.rule, "severity": r.severity} for r in seleccion],
        scoring.contar_objetos_modelo(model_data))

    return {
        "score": puntuacion["score"],
        "score_detail": puntuacion,
        "finding_count": len(hallazgos),
        "by_severity": por_severidad,
        "by_domain": por_dominio,
        "findings": sorted(hallazgos,
                           key=lambda h: (-orden[h["severity"]], h["rule"])),
        "rules_run": [r.rule for r in seleccion],
        "rule_errors": errores_regla,
        "warnings": ([f"{len(errores_regla)} regla(s) no se pudieron ejecutar; "
                      "el resultado de la auditoria es parcial."]
                     if errores_regla else []),
        "auto_fixable": sorted({h["rule"] for h in hallazgos
                                if h["auto_fix_available"]}),
    }
