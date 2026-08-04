"""Workflows orientados a resultados.

Cada uno recorre siempre las mismas seis etapas:

    analisis -> plan -> preview -> apply -> verificacion -> reporte

Componen SERVICIOS internos, nunca tools decoradas: `guard()` convertiria los
errores en datos y el workflow seguiria adelante creyendo que todo fue bien.

Todos admiten `dry_run`, que se detiene tras el preview y devuelve el plan.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip import pbir_reader, tmdl_reader
from horizun_pbi_mcp.services import (layout_doctor, model_explorer, page_spec, pbir_edit,
                      report_audit)
from horizun_pbi_mcp.services import txn as txn_service

log = get_logger("workflows")


def _etapa(nombre: str, resultado: Any, nota: str = "") -> Dict[str, Any]:
    return {"stage": nombre, "result": resultado, "note": nota}


def _informe(nombre: str, etapas: List[Dict[str, Any]], *,
             applied: bool, resumen: str,
             warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"workflow": nombre, "stages": etapas, "applied": applied,
            "summary": resumen, "warnings": warnings or []}


# =============================================================== construir ====
def _construir_pagina(active: ActivePbip, model_data: Optional[Dict[str, Any]],
                      *, nombre: str, preset: str, measures: List[str],
                      category: Optional[str], seed: str,
                      dry_run: bool, workflow: str) -> Dict[str, Any]:
    """Base comun de los workflows que generan una pagina."""
    etapas: List[Dict[str, Any]] = []

    # 1. Analisis: que hay en el modelo y que se puede usar.
    if not model_data:
        raise ValidationError(
            "Se necesita el modelo para construir una pagina: abre el .pbip o "
            "selecciona un modelo en vivo.")
    resumen_modelo = model_explorer.summary(model_data)
    disponibles = [m["name"] for m in model_data.get("measures", [])]
    faltan = [m for m in measures if m not in disponibles]
    if faltan:
        raise ValidationError(
            f"Estas medidas no existen en el modelo: {faltan}",
            details={"available": disponibles[:50]})
    etapas.append(_etapa("analisis", {
        "measures_available": len(disponibles), "measures_requested": measures,
        "tables": resumen_modelo["counts"]["tables"]}))

    # 2. Plan: el spec.
    definicion = page_spec.PRESETS[preset]
    visuals: List[Dict[str, Any]] = []
    avisos: List[str] = []
    for bloque in definicion["blocks"]:
        for i in range(bloque["count"]):
            if bloque["role"] == "kpi":
                if i >= len(measures):
                    avisos.append(
                        f"El preset '{preset}' admite {bloque['count']} KPIs y se "
                        f"dieron {len(measures)} medidas.")
                    break
                visuals.append({"type": bloque["type"], "title": measures[i],
                                "fields": {"values": [f"[{measures[i]}]"]}})
            else:
                if not category:
                    avisos.append(f"El bloque '{bloque['role']}' necesita una "
                                  "categoria; se omite.")
                    break
                if i > 0:
                    break
                etiqueta = category.split("[")[-1].rstrip("]")
                visuals.append({
                    "type": bloque["type"],
                    "title": f"{measures[0]} por {etiqueta}",
                    "fields": {"category": category,
                               "values": [f"[{measures[0]}]"]}})
    if not visuals:
        raise ValidationError(
            "No se pudo componer ningun visual: revisa las medidas y la categoria.")

    spec = {"schema_version": page_spec.SCHEMA_VERSION,
            "page": {"name": nombre, "width": 1280, "height": 720},
            "layout": {"preset": preset, "gap": 16},
            "visuals": visuals, "filters": [], "interactions": []}
    etapas.append(_etapa("plan", {"spec": spec, "visual_count": len(visuals)}))

    # 3. Preview.
    compilado = page_spec.compile_spec(active, spec, model_data, seed=seed)
    etapas.append(_etapa("preview", {
        "positions": compilado["positions"],
        "layout_clean": compilado["layout_issues"]["clean"],
        "html": page_spec.preview(active, compilado)[:0] or "generado"},
        "El preview usa las posiciones finales."))

    if dry_run:
        return _informe(workflow, etapas, applied=False,
                        resumen=f"Plan listo: {len(visuals)} visuales en "
                                f"'{nombre}'. Nada escrito (dry_run).",
                        warnings=avisos + compilado["warnings"])

    # 4. Apply.
    resultado = page_spec.apply_spec(active, compilado)
    etapas.append(_etapa("apply", {"page_id": resultado["page_id"],
                                   "visuals_created": len(resultado["visuals_created"]),
                                   "journal": resultado.get("backup")}))

    # 5. Verificacion.
    verificacion = page_spec.validate_generated_page(
        active, resultado["page_id"], model_data)
    etapas.append(_etapa("verificacion", verificacion))

    return _informe(
        workflow, etapas, applied=True,
        resumen=(f"Pagina '{nombre}' creada con {len(visuals)} visuales. "
                 f"Valida: {verificacion['valid']}."),
        warnings=avisos + compilado["warnings"] + verificacion.get("warnings", []))


def build_dashboard(active, model_data, *, name: str, measures: List[str],
                    category: Optional[str] = None, preset: str = "executive",
                    seed: str = "", dry_run: bool = False) -> Dict[str, Any]:
    if preset not in page_spec.PRESETS:
        raise ValidationError(f"Preset desconocido: '{preset}'.",
                              details={"available": sorted(page_spec.PRESETS)})
    return _construir_pagina(active, model_data, nombre=name, preset=preset,
                             measures=measures, category=category, seed=seed,
                             dry_run=dry_run, workflow="build_dashboard")


def build_executive_page(active, model_data, *, name: str = "Resumen ejecutivo",
                         measures: List[str], category: Optional[str] = None,
                         seed: str = "", dry_run: bool = False) -> Dict[str, Any]:
    return _construir_pagina(active, model_data, nombre=name, preset="executive",
                             measures=measures, category=category, seed=seed,
                             dry_run=dry_run, workflow="build_executive_page")


def build_evm_page(active, model_data, *, name: str = "EVM",
                   measures: List[str], category: Optional[str] = None,
                   seed: str = "", dry_run: bool = False) -> Dict[str, Any]:
    """Pagina EVM. Espera medidas del tipo PV, EV, AC, CPI, SPI."""
    esperadas = ("PV", "EV", "AC", "CPI", "SPI")
    faltan = [e for e in esperadas
              if not any(e.lower() in m.lower() for m in measures)]
    informe = _construir_pagina(active, model_data, nombre=name, preset="evm",
                                measures=measures, category=category, seed=seed,
                                dry_run=dry_run, workflow="build_evm_page")
    if faltan:
        informe["warnings"].append(
            f"No se reconocieron medidas para {faltan}. Una pagina EVM completa "
            "suele necesitar PV, EV, AC, CPI y SPI.")
    return informe


# ================================================================ reparar =====
def repair_broken_references(active, model_data, *, mapping: Optional[Dict[str, str]] = None,
                             dry_run: bool = True) -> Dict[str, Any]:
    """Detecta referencias rotas en los visuales y las sustituye segun `mapping`.

    Sin `mapping` solo diagnostica: adivinar a que campo queria apuntar un
    visual roto es exactamente el tipo de decision que no debe tomarse sola.
    """
    etapas: List[Dict[str, Any]] = []
    if not model_data:
        raise ValidationError("Se necesita el modelo para saber que esta roto.")

    indice = model_explorer.build_index(model_data)
    rotas: List[Dict[str, Any]] = []
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["name"], strict=True):
            for ref in list(v.get("measures", [])) + list(v.get("columns", [])):
                if not model_explorer.resolve_reference(ref, indice)["exists"]:
                    rotas.append({"page": p["name"], "visual_id": v["id"],
                                  "reference": ref, "type": v.get("type")})
    etapas.append(_etapa("analisis", {"broken_count": len(rotas), "broken": rotas}))

    if not rotas:
        return _informe("repair_broken_references", etapas, applied=False,
                        resumen="No hay referencias rotas.")

    if not mapping:
        return _informe(
            "repair_broken_references", etapas, applied=False,
            resumen=(f"{len(rotas)} referencia(s) rota(s). Pasa `mapping` con "
                     "{referencia_rota: referencia_nueva} para repararlas."),
            warnings=["No se adivina el destino de una referencia rota."])

    acciones = [{"page": r["page"], "visual_id": r["visual_id"],
                 "old_ref": r["reference"], "new_ref": mapping[r["reference"]]}
                for r in rotas if r["reference"] in mapping]
    sin_mapear = [r["reference"] for r in rotas if r["reference"] not in mapping]
    etapas.append(_etapa("plan", {"actions": acciones,
                                  "unmapped": sorted(set(sin_mapear))}))

    for a in acciones:
        destino = a["new_ref"].strip("[]")
        if not model_explorer.resolve_reference(destino, indice)["exists"]:
            raise ValidationError(
                f"El destino '{a['new_ref']}' tampoco existe en el modelo.",
                details={"mapping": mapping})
    etapas.append(_etapa("preview", {"validated_targets": len(acciones)}))

    if dry_run:
        return _informe("repair_broken_references", etapas, applied=False,
                        resumen=f"{len(acciones)} reparacion(es) planificadas.",
                        warnings=([f"{len(sin_mapear)} sin mapear"] if sin_mapear else []))

    # Se compila TODO antes de escribir nada. Antes esto era un bucle con una
    # transaccion por visual y un `except` que seguia adelante: si fallaba el
    # quinto, los cuatro anteriores quedaban confirmados y la tool devolvia
    # ok:true con una lista de fallidos. Ahora un solo fallo aborta el lote.
    planes = [pbir_edit.plan_replace_visual_field(
        active, a["page"], a["visual_id"], a["old_ref"], a["new_ref"], model_data)
        for a in acciones]

    aplicadas = [{**a, "replacements": p["count"]}
                 for a, p in zip(acciones, planes)]
    destinos = [p["path"] for p in planes]

    pbir_edit.assert_escritura_pbir(active, "Reparar referencias rotas")
    cm = txn_service.project_transaction(
        active, destinos, tool="pbi_repair_broken_references")
    with cm as t:
        for p in planes:
            t.write_json(p["path"], p["data"])
    etapas.append(_etapa("apply", {"applied": len(aplicadas), "failed": 0,
                                   "transaction": cm.result["journal"]}))

    quedan = [r for r in rotas if r["reference"] in sin_mapear]
    etapas.append(_etapa("verificacion", {"remaining_broken": len(quedan)}))
    return _informe(
        "repair_broken_references", etapas, applied=True,
        resumen=f"{len(aplicadas)} reparada(s), {len(quedan)} pendiente(s).",
        # Ya no hay "fallidas": un fallo aborta el lote entero y esta funcion
        # no llega a devolver. Lo pendiente es solo lo que no se supo mapear.
        warnings=([f"{len(sin_mapear)} sin mapear"] if sin_mapear else []))


# ============================================================== normalizar ====
def normalize_report(active, model_data, *, dry_run: bool = True) -> Dict[str, Any]:
    """Normaliza la geometria de TODAS las paginas del informe."""
    etapas: List[Dict[str, Any]] = []
    paginas = pbir_reader.list_pages(active)
    antes = report_audit.audit_project(active, model_data)
    etapas.append(_etapa("analisis", {
        "pages": len(paginas), "score_before": antes["score"],
        "layout_findings": antes["by_domain"].get("layout", {}).get("findings", 0)}))

    plan = []
    for p in paginas:
        visuales = pbir_reader.list_visuals(active, p["name"], strict=True)
        canvas = {"width": p.get("width"), "height": p.get("height")}
        nuevas = layout_doctor.normalize(visuales, canvas)
        if nuevas:
            plan.append({"page": p["name"], "moves": len(nuevas),
                         "positions": nuevas})
    etapas.append(_etapa("plan", {"pages_to_change": len(plan),
                                  "total_moves": sum(x["moves"] for x in plan)}))
    etapas.append(_etapa("preview", {"detail": [
        {"page": x["page"], "moves": x["moves"]} for x in plan]}))

    if dry_run or not plan:
        return _informe("normalize_report", etapas, applied=False,
                        resumen=(f"{sum(x['moves'] for x in plan)} visual(es) se "
                                 f"moverian en {len(plan)} pagina(s)."
                                 if plan else "El layout ya cumple."))

    from horizun_pbi_mcp.pbip import pbir_writer

    # Se compilan las posiciones de TODAS las paginas y se escriben en una sola
    # transaccion. Antes habia una por pagina: atomico dentro de cada una, pero
    # si fallaba la tercera, las dos primeras quedaban reacomodadas y el
    # informe a medio normalizar.
    planificados = []
    for x in plan:
        planificados.extend(
            pbir_writer.plan_visuals_bulk(active, x["page"], x["positions"]))

    pbir_edit.assert_escritura_pbir(active, "Normalizar el informe")
    cm = txn_service.project_transaction(
        active, [p["path"] for p in planificados], tool="pbi_normalize_report")
    with cm as t:
        for p in planificados:
            t.write_json(p["path"], p["data"])

    movidos = len(planificados)
    etapas.append(_etapa("apply", {"moved": movidos,
                                   "pages": len(plan),
                                   "transaction": cm.result["journal"]}))

    despues = report_audit.audit_project(active, model_data)
    etapas.append(_etapa("verificacion", {
        "score_after": despues["score"],
        "improved": despues["score"] >= antes["score"]}))
    return _informe(
        "normalize_report", etapas, applied=True,
        resumen=(f"{movidos} visual(es) reacomodados. Puntaje "
                 f"{antes['score']} -> {despues['score']}."))


# ================================================================ comparar ====
def compare_live_to_pbip(session) -> Dict[str, Any]:
    """Compara el modelo EN VIVO con el TMDL del disco."""
    from horizun_pbi_mcp.powerbi import model_reader

    active = session.require_active_pbip()
    etapas: List[Dict[str, Any]] = []

    disco = tmdl_reader.read_semantic_model(active)
    try:
        vivo = model_reader.read_model(session)
    except Exception as exc:  # noqa: BLE001
        return _informe(
            "compare_live_to_pbip",
            [_etapa("analisis", {"error": str(exc)})],
            applied=False,
            resumen="No se pudo leer el modelo en vivo: necesita Power BI "
                    "Desktop abierto y el modelo seleccionado.",
            warnings=["Comparacion no realizada."])

    def indexar(md):
        return ({t["name"] for t in md.get("tables", [])},
                {m["name"]: (m.get("expression") or "").strip()
                 for m in md.get("measures", [])})

    tablas_v, medidas_v = indexar(vivo)
    tablas_d, medidas_d = indexar(disco)

    diferencias = {
        "tables_only_live": sorted(tablas_v - tablas_d),
        "tables_only_pbip": sorted(tablas_d - tablas_v),
        "measures_only_live": sorted(set(medidas_v) - set(medidas_d)),
        "measures_only_pbip": sorted(set(medidas_d) - set(medidas_v)),
        "measures_with_different_dax": sorted(
            n for n in set(medidas_v) & set(medidas_d)
            if medidas_v[n] != medidas_d[n]),
    }
    total = sum(len(v) for v in diferencias.values())
    etapas.append(_etapa("analisis", {"live": {"tables": len(tablas_v),
                                               "measures": len(medidas_v)},
                                      "pbip": {"tables": len(tablas_d),
                                               "measures": len(medidas_d)}}))
    etapas.append(_etapa("verificacion", diferencias))
    return _informe(
        "compare_live_to_pbip", etapas, applied=False,
        resumen=("Modelo en vivo y TMDL coinciden." if total == 0 else
                 f"{total} diferencia(s) entre el modelo en vivo y el disco."),
        warnings=(["Recuerda que los cambios 'live' no se persisten hasta que "
                   "guardas en Power BI Desktop (Ctrl+S)."] if total else []))


# =========================================================== documentacion ====
def generate_technical_documentation(active, model_data) -> str:
    """Documentacion tecnica en Markdown: modelo + informe + auditoria."""
    lineas: List[str] = ["# Documentacion tecnica", ""]

    if model_data:
        s = model_explorer.summary(model_data)
        lineas += ["## Modelo semantico", "",
                   f"- **Modelo:** {s['model'].get('name')}",
                   f"- **Origen:** {s['source']}", ""]
        lineas += ["| Metrica | Valor |", "|---|---|"]
        for k, v in s["counts"].items():
            lineas.append(f"| {k} | {v} |")
        lineas += ["", "### Tablas", "",
                   "| Tabla | Columnas | Medidas | Oculta |", "|---|---|---|---|"]
        for t in s["tables"]:
            lineas.append(f"| {t['name']} | {t['columns']} | {t['measures']} | "
                          f"{'si' if t['is_hidden'] else 'no'} |")
        if s["disconnected_tables"]:
            lineas += ["", f"> Tablas sin relaciones: {s['disconnected_tables']}"]
        if s["broken_references"]:
            lineas += ["", "### Referencias rotas", ""]
            for r in s["broken_references"]:
                lineas.append(f"- `{r['measure']}` -> `{r['reference']}`")

        lineas += ["", "### Medidas", ""]
        for m in model_data.get("measures", []):
            lineas += [f"#### {m.get('table')}[{m['name']}]"]
            if m.get("description"):
                lineas.append(f"> {m['description']}")
            if m.get("format_string"):
                lineas.append(f"- Formato: `{m['format_string']}`")
            deps = model_explorer.extract_references(m.get("expression"))
            if deps["columns"] or deps["unqualified"]:
                lineas.append(f"- Depende de: {deps['columns'] + deps['unqualified']}")
            lineas += ["", "```dax", (m.get("expression") or "").strip(), "```", ""]

    lineas += ["## Informe", ""]
    paginas = pbir_reader.list_pages(active)
    lineas += ["| Pagina | Visuales | Lienzo |", "|---|---|---|"]
    for p in paginas:
        lineas.append(f"| {p.get('display_name')} | {p.get('visual_count')} | "
                      f"{p.get('width')}x{p.get('height')} |")
    for p in paginas:
        lineas += ["", f"### {p.get('display_name')}", "",
                   "| Visual | Tipo | Campos |", "|---|---|---|"]
        for v in pbir_reader.list_visuals(active, p["name"], strict=True):
            campos = ", ".join((v.get("measures") or []) + (v.get("columns") or []))
            lineas.append(f"| {v.get('title') or '(sin titulo)'} | "
                          f"{v.get('type')} | {campos or '-'} |")

    auditoria = report_audit.audit_project(active, model_data)
    lineas += ["", "## Auditoria", "",
               f"**Puntaje: {auditoria['score']}/100**", "",
               auditoria["executive_summary"], "",
               "| Dominio | Puntaje | Hallazgos |", "|---|---|---|"]
    for dom, d in sorted(auditoria["by_domain"].items()):
        lineas.append(f"| {dom} | {d['score']} | {d['findings']} |")
    return "\n".join(lineas)


# ================================================================= entrega ====
def prepare_delivery(active, model_data, *, dry_run: bool = True) -> Dict[str, Any]:
    """Checklist de pre-entrega: audita, lista bloqueantes y propone plan."""
    etapas: List[Dict[str, Any]] = []
    auditoria = report_audit.audit_project(active, model_data)
    etapas.append(_etapa("analisis", {
        "score": auditoria["score"], "findings": auditoria["finding_count"],
        "by_domain": {k: v["score"] for k, v in auditoria["by_domain"].items()}}))

    bloqueantes = [h for h in auditoria["findings"] if h["severity"] == "error"]
    avisos = [h for h in auditoria["findings"] if h["severity"] == "warning"]
    checklist = [
        {"check": "sin errores", "ok": not bloqueantes,
         "detail": f"{len(bloqueantes)} error(es)"},
        {"check": "sin avisos", "ok": not avisos,
         "detail": f"{len(avisos)} aviso(s)"},
        {"check": "paginas con contenido",
         "ok": all(p.get("visual_count", 0) > 0
                   for p in pbir_reader.list_pages(active)),
         "detail": "toda pagina debe tener visuales"},
        {"check": "sin referencias rotas",
         "ok": not any(h["rule"].endswith("broken_reference")
                       or h["rule"].endswith("broken_field_reference")
                       for h in auditoria["findings"]),
         "detail": "los visuales apuntan a campos que existen"},
    ]
    etapas.append(_etapa("plan", {"checklist": checklist,
                                  "auto_fixable": auditoria["auto_fixable"]}))

    plan_fixes = None
    if auditoria["auto_fixable"]:
        arreglables = [r for r in auditoria["auto_fixable"]
                       if r in report_audit.AUTOFIXES]
        if arreglables:
            plan_fixes = report_audit.plan_fixes(active, auditoria, arreglables)
    etapas.append(_etapa("preview", {
        "fix_actions": plan_fixes["action_count"] if plan_fixes else 0}))

    listo = all(c["ok"] for c in checklist)
    if dry_run or not plan_fixes:
        return _informe(
            "prepare_delivery", etapas, applied=False,
            resumen=("Listo para entregar." if listo else
                     f"NO listo: {len(bloqueantes)} error(es), {len(avisos)} aviso(s)."),
            warnings=[h["rule"] for h in bloqueantes])

    aplicado = report_audit.apply_fixes(active, plan_fixes["actions"])
    etapas.append(_etapa("apply", aplicado))
    despues = report_audit.audit_project(active, model_data)
    etapas.append(_etapa("verificacion", {"score_after": despues["score"]}))
    return _informe(
        "prepare_delivery", etapas, applied=True,
        resumen=(f"{aplicado['applied']} correccion(es). Puntaje "
                 f"{auditoria['score']} -> {despues['score']}."))


# ---------------------------------------------------------------------------
# Renombrar una medida SIN romper el informe
# ---------------------------------------------------------------------------

def _bloques_de_medida(lines: List[str]) -> List[Dict[str, Any]]:
    """Rangos [inicio, fin) de cada bloque `measure` de un archivo TMDL.

    Se delimitan aqui y no con un regex global porque el reemplazo de
    referencias DAX solo es seguro DENTRO de una medida: en la expresion de una
    columna calculada, `[x]` sin calificar es una COLUMNA de su propia tabla, y
    reescribirlo corromperia un calculo que no tiene nada que ver.
    """
    from horizun_pbi_mcp.pbip.tmdl_reader import _first_token, _indent, _unquote

    bloques: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        linea = lines[i]
        if _indent(linea) == 1 and _first_token(linea) == "measure":
            cabecera = linea.strip()[len("measure"):].strip()
            nombre = _unquote(cabecera.split("=", 1)[0].strip())
            j = i + 1
            while j < len(lines):
                lj = lines[j]
                if lj.strip() and _indent(lj) <= 1:
                    break
                j += 1
            bloques.append({"name": nombre, "start": i, "end": j})
            i = j
        else:
            i += 1
    return bloques


def _reemplazar_ref_dax(texto: str, viejo: str, nuevo: str):
    """Sustituye `[viejo]` SIN calificar por `[nuevo]`. `(texto, cuantas)`.

    Solo la forma sin calificar: `Tabla[viejo]` o `'Tabla'[viejo]` puede ser
    una COLUMNA homonima de otra tabla, y adivinar ahi es corromper. Las
    calificadas que referencien de verdad a la medida las recoge el barrido
    final y salen como aviso con su ubicacion, nunca en silencio.
    """
    import re

    patron = re.compile(r"(?<![\w'\]])\[" + re.escape(viejo) + r"\]",
                        re.IGNORECASE)
    return patron.subn(f"[{nuevo}]", texto)


def _barrido_de_restos(active, table: str, old_name: str) -> List[str]:
    """Rutas del informe donde AUN se referencia la medida vieja.

    Cubre lo que el plan de visuales no toca: bookmarks, filterConfig de
    paginas o del informe, y cualquier JSON del arbol `.Report`. Encontrar
    algo aqui no es fallo del renombrado: es la lista exacta de lo que hay
    que revisar a mano, dicha en voz alta.
    """
    from pathlib import Path as _Path

    from horizun_pbi_mcp.utils.json_utils import read_json

    if not active.report_dir:
        return []

    def _tiene_ref(nodo) -> bool:
        if isinstance(nodo, dict):
            medida = nodo.get("Measure")
            if isinstance(medida, dict):
                prop = str(medida.get("Property") or "")
                ent = (medida.get("Expression", {}) or {}).get(
                    "SourceRef", {}) or {}
                entidad = str(ent.get("Entity") or "")
                if (prop.casefold() == old_name.casefold()
                        and (not entidad or entidad.casefold() == table.casefold())):
                    return True
            return any(_tiene_ref(v) for v in nodo.values())
        if isinstance(nodo, list):
            return any(_tiene_ref(v) for v in nodo)
        return False

    base = _Path(active.report_dir) / "definition"
    restos: List[str] = []
    for ruta in sorted(base.rglob("*.json")):
        try:
            datos = read_json(ruta)
        except Exception:                                # noqa: BLE001
            continue
        if _tiene_ref(datos):
            restos.append(str(ruta.relative_to(base)).replace("\\", "/"))
    return restos


def rename_measure(active, model_data, *, table: str, old_name: str,
                   new_name: str, dry_run: bool = True) -> Dict[str, Any]:
    """Renombra una medida actualizando a la vez modelo e informe.

    El caso que cierra: renombrar 8 medidas a nombres presentables obligaba a
    reescribir el TMDL completo y re-aplicar la pagina entera, porque
    cualquier referencia vieja quedaba rota EN SILENCIO -el visual abre y sale
    vacio-. Aqui todo se compila primero y se escribe en UNA transaccion:
    cabecera TMDL, expresiones DAX de otras medidas y visual.json del informe.
    """
    import copy as _copy

    from horizun_pbi_mcp.pbip.tmdl_reader import find_table_file
    from horizun_pbi_mcp.pbip.tmdl_writer import _render
    from horizun_pbi_mcp.utils.validation import tmdl_quote_name, validate_object_name

    etapas: List[Dict[str, Any]] = []
    if not model_data:
        raise ValidationError("Se necesita el modelo para renombrar con referencias.")

    nuevo = validate_object_name(new_name, "medida")
    viejo = str(old_name).strip()
    if viejo == nuevo:
        raise ValidationError("El nombre nuevo es identico al actual.")

    # -- validaciones contra el modelo (la leccion del preflight: una colision
    #    de nombres no falla al escribir; falla al ABRIR Desktop) --------------
    medidas = model_data.get("measures") or []
    la_medida = None
    for m in medidas:
        if (str(m.get("table", "")).casefold() == table.casefold()
                and str(m.get("name", "")).casefold() == viejo.casefold()):
            la_medida = m
            break
    if la_medida is None:
        disponibles = sorted(str(m.get("name")) for m in medidas
                             if str(m.get("table", "")).casefold() == table.casefold())
        raise ValidationError(
            f"La medida '{viejo}' no existe en la tabla '{table}'.",
            details={"available": disponibles})
    for m in medidas:
        if (m is not la_medida
                and str(m.get("name", "")).casefold() == nuevo.casefold()):
            raise ValidationError(
                f"Ya existe una medida '{m.get('name')}' en la tabla "
                f"'{m.get('table')}': los nombres de medida son unicos en "
                "todo el modelo.")
    for t in model_data.get("tables") or []:
        if str(t.get("name", "")).casefold() != table.casefold():
            continue
        for c in t.get("columns") or []:
            if str(c.get("name", "")).casefold() == nuevo.casefold():
                raise ValidationError(
                    f"La tabla '{table}' ya tiene una COLUMNA '{c.get('name')}'. "
                    "Power BI acepta escribirlo y despues rechaza el modelo al "
                    "abrirlo: es la colision exacta que detecta el preflight.")

    # -- plan TMDL: cabecera + referencias DAX en bloques de medida -----------
    planes_tmdl: List[Dict[str, Any]] = []
    total_refs_dax = 0
    for t in model_data.get("tables") or []:
        nombre_tabla = str(t.get("name"))
        try:
            ruta = find_table_file(active, nombre_tabla)
        except Exception:                                # noqa: BLE001
            continue
        lineas = ruta.read_text(encoding="utf-8-sig").splitlines()
        cambiado = False
        refs_aqui = 0
        for bloque in _bloques_de_medida(lineas):
            ini, fin = bloque["start"], bloque["end"]
            if (nombre_tabla.casefold() == table.casefold()
                    and bloque["name"].casefold() == viejo.casefold()):
                cab = lineas[ini]
                _, _, resto = cab.partition("=")
                lineas[ini] = f"\tmeasure {tmdl_quote_name(nuevo)} ={resto}"
                cambiado = True
            for k in range(ini, fin):
                if k == ini:
                    izq, sep, der = lineas[k].partition("=")
                    if not sep:
                        continue
                    der2, n = _reemplazar_ref_dax(der, viejo, nuevo)
                    if n:
                        lineas[k] = izq + sep + der2
                        refs_aqui += n
                        cambiado = True
                else:
                    linea2, n = _reemplazar_ref_dax(lineas[k], viejo, nuevo)
                    if n:
                        lineas[k] = linea2
                        refs_aqui += n
                        cambiado = True
        if cambiado:
            planes_tmdl.append({"path": ruta, "text": _render(lineas),
                                "table": nombre_tabla,
                                "dax_refs": refs_aqui})
            total_refs_dax += refs_aqui
    etapas.append(_etapa("plan_modelo", {
        "files": [{"table": p["table"], "dax_refs": p["dax_refs"]}
                  for p in planes_tmdl],
        "dax_references": total_refs_dax}))

    # -- plan de visuales: contra el modelo YA renombrado en memoria ----------
    modelo_renombrado = _copy.deepcopy(model_data)
    for m in modelo_renombrado.get("measures") or []:
        if (str(m.get("table", "")).casefold() == table.casefold()
                and str(m.get("name", "")).casefold() == viejo.casefold()):
            m["name"] = nuevo
    ref_vieja = f"{table}[{viejo}]"
    ref_nueva = f"{table}[{nuevo}]"
    acciones: List[Dict[str, Any]] = []
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["name"], strict=True):
            if any(str(r).casefold() == ref_vieja.casefold()
                   for r in v.get("measures", [])):
                acciones.append({"page": p["name"], "visual_id": v["id"]})
    planes_visual = [pbir_edit.plan_replace_visual_field(
        active, a["page"], a["visual_id"], ref_vieja, ref_nueva,
        modelo_renombrado) for a in acciones]
    etapas.append(_etapa("plan_informe", {"visuals": acciones}))

    if dry_run:
        return _informe(
            "rename_measure", etapas, applied=False,
            resumen=(f"'{viejo}' -> '{nuevo}': {len(planes_tmdl)} archivo(s) "
                     f"TMDL ({total_refs_dax} referencia(s) DAX) y "
                     f"{len(acciones)} visual(es). dry_run."))

    # -- aplicar: UNA transaccion para modelo e informe -----------------------
    destinos = [p["path"] for p in planes_tmdl] + [p["path"] for p in planes_visual]
    pbir_edit.assert_escritura_pbir(active, "Renombrar una medida")
    cm = txn_service.project_transaction(active, destinos,
                                         tool="pbi_rename_measure")
    with cm as tx:
        for p in planes_tmdl:
            tx.write_text(p["path"], p["text"])
        for p in planes_visual:
            tx.write_json(p["path"], p["data"])
    etapas.append(_etapa("apply", {"tmdl_files": len(planes_tmdl),
                                   "visuals": len(planes_visual),
                                   "transaction": cm.result["journal"]}))

    # -- verificacion: releer y barrer, nunca dar por hecho -------------------
    releido = tmdl_reader.read_semantic_model(active, strict=False)
    nombres = {(str(m.get("table", "")).casefold(), str(m.get("name", "")).casefold())
               for m in releido.get("measures") or []}
    renombrada = ((table.casefold(), nuevo.casefold()) in nombres
                  and (table.casefold(), viejo.casefold()) not in nombres)
    restos = _barrido_de_restos(active, table, viejo)
    etapas.append(_etapa("verificacion", {"renamed_verified": renombrada,
                                          "leftover_references": restos}))
    avisos = []
    if not renombrada:
        avisos.append("La relectura del TMDL no confirma el renombrado: revisa "
                      "el journal de la transaccion.")
    if restos:
        avisos.append(
            f"Quedan referencias a '{viejo}' fuera de los visuales tratados "
            f"(bookmarks o filtros): {restos}. Revisalas a mano; no se "
            "adivinan porque una referencia calificada puede ser una columna "
            "homonima de otra tabla.")
    return _informe(
        "rename_measure", etapas, applied=True,
        resumen=(f"'{viejo}' -> '{nuevo}' renombrada. {total_refs_dax} "
                 f"referencia(s) DAX y {len(planes_visual)} visual(es) "
                 "actualizados en una transaccion."),
        warnings=avisos)
