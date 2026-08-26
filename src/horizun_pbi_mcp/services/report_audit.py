"""Auditoria del INFORME (PBIR) y auditoria integral del proyecto.

Combina tres motores que ya existen —modelo (`model_audit`), geometria
(`layout_doctor`) y referencias (`model_explorer`)— y anade las reglas propias
del informe: titulos, paginas vacias, campos rotos en visuales, duplicados.

Los autofixes NO se aplican en bloque. Se seleccionan por regla y por objeto,
se planifican y se aplican con un plan token, igual que cualquier otra
escritura.
"""
from __future__ import annotations

import html as html_mod
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip import pbir_reader
from horizun_pbi_mcp.services import layout_doctor, model_audit, model_explorer, scoring

INFO, WARNING, ERROR = "info", "warning", "error"


def _h(rule: str, severity: str, objeto: Dict[str, Any], evidencia: Dict[str, Any],
       recomendacion: str, auto_fix: bool = False) -> Dict[str, Any]:
    return {"rule": rule, "severity": severity, "domain": "report",
            "object": objeto, "evidence": evidencia,
            "recommendation": recomendacion, "auto_fix_available": auto_fix}


#: Alto minimo de un slicer, en px. Depende de si la cabecera esta visible:
#: con ella, 28 + selector 32 + relleno 8/8 = 76; sin ella, 32 + 16 = 48.
#:
#: Las dos cifras estan MEDIDAS contra el CLI oficial, no deducidas: se le
#: paso el mismo informe variando el alto y se busco donde deja de quejarse.
#: Con cabecera, 74 falla y 76 pasa; sin cabecera, 47 falla y 48 pasa. La
#: primera version de esta regla usaba 76 siempre y marcaba como rotos nueve
#: slicers sanos que ocultaban su cabecera.
ALTO_MINIMO_SLICER = 76
ALTO_MINIMO_SLICER_SIN_CABECERA = 48

#: Roles de eje de un scatter. Si hay campo en Detalles, estos DEBEN venir
#: agregados o Power BI no dibuja la nube de puntos.
_EJES_SCATTER = ("X", "Y")


def _propiedad_de_objeto(datos: Dict[str, Any], grupo: str,
                         propiedad: str) -> Optional[str]:
    """Valor literal de `visual.objects.<grupo>.<propiedad>`, si esta."""
    try:
        for entrada in datos["visual"]["objects"][grupo]:
            valor = entrada["properties"][propiedad]["expr"]["Literal"]["Value"]
            return str(valor).strip("'")
    except (KeyError, IndexError, TypeError):
        return None
    return None


def _formato_de_slicer(visual: Dict[str, Any]) -> Dict[str, Any]:
    """Modo y visibilidad de la cabecera, leidos del bloque `objects`.

    `read_visual_file` no devuelve `objects` -no lo necesita nadie mas-, asi
    que se relee el JSON. Es un archivo ya en disco y son pocos.
    """
    vacio = {"mode": None, "header": True}
    ruta = visual.get("file")
    if not ruta:
        return vacio
    from horizun_pbi_mcp.utils.json_utils import read_json

    try:
        datos = read_json(Path(ruta))
    except Exception:                          # noqa: BLE001 - visual ilegible
        return vacio
    cabecera = _propiedad_de_objeto(datos, "header", "show")
    return {"mode": _propiedad_de_objeto(datos, "data", "mode"),
            # Sin la propiedad, la cabecera se muestra: es el valor por defecto.
            "header": cabecera is None or cabecera.casefold() != "false"}


def _visual_no_renderiza(visual: Dict[str, Any], pid: str) -> List[Dict[str, Any]]:
    """Configuraciones que dejan al visual con un cartel en vez de datos."""
    hallazgos: List[Dict[str, Any]] = []
    tipo = visual.get("type")
    roles = visual.get("fields") or {}
    objeto = {"kind": "visual", "page": pid, "id": visual.get("id"),
              "type": tipo}

    if tipo == "scatterChart" and (roles.get("Category") or roles.get("Series")):
        sin_agregar = [
            p.get("ref") for eje in _EJES_SCATTER
            for p in roles.get(eje) or []
            if p.get("kind") == "column" and p.get("aggregation") is None]
        if sin_agregar:
            hallazgos.append(_h(
                "report_scatter_axis_not_aggregated", ERROR, objeto,
                {"fields": [r for r in sin_agregar if r],
                 "has_details": bool(roles.get("Category"))},
                "Con un campo en Detalles, Power BI exige que X e Y esten "
                "resumidos: sin eso NO dibuja el visual y muestra 'Quite los "
                "valores para mostrar los pares del eje X y el eje Y'. Pon un "
                "resumen (promedio o suma) en esos campos, o quita Detalles."))

    if tipo == "slicer":
        alto = float((visual.get("position") or {}).get("height") or 0)
        formato = _formato_de_slicer(visual)
        con_cabecera = formato["header"]
        piso = (ALTO_MINIMO_SLICER if con_cabecera
                else ALTO_MINIMO_SLICER_SIN_CABECERA)
        if 0 < alto < piso:
            modo = formato["mode"]
            desplegable = (modo or "").casefold() == "dropdown"
            desglose = ("cabecera 28 + selector 32 + relleno 8/8"
                        if con_cabecera else
                        "selector 32 + relleno 8/8, sin cabecera")
            hallazgos.append(_h(
                "report_slicer_below_height_floor", ERROR, objeto,
                {"height": alto, "minimum": piso, "header_shown": con_cabecera,
                 "mode": modo or "lista (por defecto)"},
                f"Un slicer con este formato necesita {piso} px ({desglose}). "
                f"Con {alto:.0f} " + ("se recorta." if desplegable else
                                      "solo se ve un elemento y una barra de "
                                      "scroll.")
                + " Sube el alto y, si aun no lo es, ponlo desplegable "
                  "(`data.mode = 'Dropdown'`)."))

    return hallazgos


def audit_report(active: ActivePbip,
                 model_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reglas propias del informe, sobre todas sus paginas."""
    hallazgos: List[Dict[str, Any]] = []
    paginas = pbir_reader.list_pages(active)
    indice = model_explorer.build_index(model_data) if model_data else None
    medidas_usadas: set = set()

    for p in paginas:
        pid = p["name"]
        visuales = pbir_reader.list_visuals(active, pid, strict=True)
        canvas = {"width": p.get("width", 1280), "height": p.get("height", 720)}

        if not visuales:
            hallazgos.append(_h(
                "report_page_empty", WARNING, {"kind": "page", "page": pid,
                                               "name": p.get("display_name")},
                {"visual_count": 0},
                "Una pagina vacia confunde al usuario. Anade contenido o "
                "eliminala."))

        # --- geometria (delegada al motor de layout) ---------------------
        for issue in layout_doctor.detect_issues(visuales, canvas)["issues"]:
            if issue["rule"] in ("layout_page_empty",):
                continue                       # ya cubierto arriba
            copia = dict(issue)
            copia["object"] = {**issue["object"], "page": pid}
            copia["domain"] = "layout"
            hallazgos.append(copia)

        # --- titulos ------------------------------------------------------
        for v in visuales:
            if not v.get("title"):
                hallazgos.append(_h(
                    "report_visual_without_title", WARNING,
                    {"kind": "visual", "page": pid, "id": v["id"],
                     "type": v.get("type")},
                    {"title": None},
                    "Un visual sin titulo obliga a adivinar que muestra. "
                    "Ponle uno descriptivo.", auto_fix=True))

        # --- visuales que Power BI se NIEGA a dibujar ---------------------
        # No es lo mismo un visual feo que uno en error: estos dos salen con
        # un cartel en vez de contenido, y ni el esquema PBIR ni el DAX que
        # generan delatan nada. Solo se veian abriendo el informe.
        for v in visuales:
            for hallazgo in _visual_no_renderiza(v, pid):
                hallazgos.append(hallazgo)

        # --- referencias rotas -------------------------------------------
        if indice is not None:
            for v in visuales:
                for ref in list(v.get("measures", [])) + list(v.get("columns", [])):
                    if not model_explorer.resolve_reference(ref, indice)["exists"]:
                        hallazgos.append(_h(
                            "report_broken_field_reference", ERROR,
                            {"kind": "visual", "page": pid, "id": v["id"],
                             "type": v.get("type")},
                            {"missing_reference": ref},
                            f"El visual referencia '{ref}', que no existe en el "
                            "modelo: aparecera vacio o con error."))
                    else:
                        nombre = ref.split("[")[-1].rstrip("]")
                        medidas_usadas.add(nombre)

        # --- visuales duplicados -----------------------------------------
        vistos: Dict[Tuple, str] = {}
        for v in visuales:
            clave = (v.get("type"), tuple(sorted(v.get("measures", []))),
                     tuple(sorted(v.get("columns", []))))
            if clave in vistos and any(clave[1] + clave[2]):
                hallazgos.append(_h(
                    "report_duplicate_visual", INFO,
                    {"kind": "visual_pair", "page": pid,
                     "visuals": [vistos[clave], v["id"]]},
                    {"type": v.get("type"), "fields": list(clave[1] + clave[2])},
                    "Dos visuales del mismo tipo con los mismos campos. Puede "
                    "ser intencionado (distinto filtro) o una copia olvidada."))
            else:
                vistos[clave] = v["id"]

    # --- tamano de lienzo inconsistente entre paginas --------------------
    tamanos = {(p.get("width"), p.get("height")) for p in paginas}
    if len(tamanos) > 1:
        hallazgos.append(_h(
            "report_inconsistent_canvas", INFO, {"kind": "report"},
            {"sizes": sorted(str(t) for t in tamanos)},
            "Las paginas tienen tamanos de lienzo distintos: al navegar entre "
            "ellas el informe 'salta'. Unifica el tamano."))

    # --- medidas que ningun visual usa -----------------------------------
    if model_data:
        sin_uso = []
        for m in model_data.get("measures", []):
            if m["name"] in medidas_usadas:
                continue
            usada_por_medida = any(
                m["name"] in model_explorer.extract_references(
                    o.get("expression"))["unqualified"]
                for o in model_data.get("measures", []) if o["name"] != m["name"])
            if not usada_por_medida:
                sin_uso.append(m["name"])
        for nombre in sin_uso:
            hallazgos.append(_h(
                "report_measure_unused_anywhere", INFO,
                {"kind": "measure", "name": nombre},
                {"used_in_visuals": False, "used_by_measures": False},
                "Ninguna medida ni ningun visual la usa. Candidata a retirar, "
                "tras comprobar que no se usa en otro informe."))

    por_severidad: Dict[str, int] = {}
    for h in hallazgos:
        por_severidad[h["severity"]] = por_severidad.get(h["severity"], 0) + 1
    return {"page_count": len(paginas), "finding_count": len(hallazgos),
            "by_severity": por_severidad, "findings": hallazgos}


# --------------------------------------------------------- auditoria total ---
def _reglas_aplicables(rules: Optional[List[str]],
                       hallazgos: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Reglas que cuentan para el divisor del puntaje.

    Las del MODELO estan registradas, asi que entran todas, disparen o no.
    Las del INFORME estan escritas en linea dentro de `audit_report` y no hay
    un catalogo del que sacarlas: para esas solo se puede contar las que
    dispararon. Es una limitacion conocida y esta anotada en el resultado
    (`rules_catalog`), en vez de fingir un catalogo completo.
    """
    salida: List[Dict[str, str]] = []
    vistos: set = set()
    for r in model_audit.reglas_disponibles():
        if rules is not None and r["rule"] not in rules:
            continue
        salida.append({"rule": r["rule"], "severity": r["severity"],
                       "domain": r.get("domain", "modelo")})
        vistos.add(r["rule"])

    for h in hallazgos:
        if h["rule"] in vistos:
            continue
        vistos.add(h["rule"])
        salida.append({"rule": h["rule"], "severity": h["severity"],
                       "domain": h.get("domain", "informe")})
    return salida


def _objetos_evaluados(active: ActivePbip,
                       model_data: Optional[Dict[str, Any]]) -> int:
    """Objetos examinables: los del modelo mas los del informe."""
    del_modelo = scoring.contar_objetos_modelo(model_data)
    try:
        paginas = pbir_reader.list_pages(active)
        visuales = sum(len(pbir_reader.list_visuals(
            active, p["display_name"], strict=True))
                       for p in paginas)
    except Exception:                                   # noqa: BLE001
        paginas, visuales = [], 0
    return del_modelo + scoring.contar_objetos_informe(len(paginas), visuales)


def _score(hallazgos: List[Dict[str, Any]], reglas: List[Dict[str, str]],
           objetos: int) -> Dict[str, Any]:
    """Puntaje normalizado. Ver services/scoring.py.

    El anterior era `100 - (errores*10 + avisos*3 + infos*1)`: como penalizaba
    el recuento absoluto, medía el tamano del informe. El PB4 real sacaba 0 por
    acumulacion de hallazgos informativos, y una vez en cero ni empeorar ni
    mejorar se notaba.
    """
    return scoring.compute(hallazgos, reglas, objetos)


def audit_project(active: ActivePbip, model_data: Optional[Dict[str, Any]],
                  *, rules: Optional[List[str]] = None,
                  min_severity: str = INFO) -> Dict[str, Any]:
    """Auditoria integral: modelo + informe + layout, con puntaje por dominio."""
    hallazgos: List[Dict[str, Any]] = []
    dominios: Dict[str, Dict[str, Any]] = {}
    avisos: List[str] = []

    if model_data:
        modelo = model_audit.audit(model_data, rules=rules, min_severity=min_severity)
        hallazgos.extend(modelo["findings"])
        avisos.extend(modelo.get("warnings") or [])

    informe = audit_report(active, model_data)
    orden = {INFO: 0, WARNING: 1, ERROR: 2}
    minimo = orden.get(min_severity, 0)
    for h in informe["findings"]:
        if orden[h["severity"]] < minimo:
            continue
        if rules is not None and h["rule"] not in rules:
            continue
        hallazgos.append(h)

    por_dominio_hallazgos: Dict[str, List[Dict[str, Any]]] = {}
    for h in hallazgos:
        d = dominios.setdefault(h["domain"], {"findings": 0, "by_severity": {}})
        d["findings"] += 1
        d["by_severity"][h["severity"]] = d["by_severity"].get(h["severity"], 0) + 1
        por_dominio_hallazgos.setdefault(h["domain"], []).append(h)

    # Reglas aplicables y objetos evaluados: sin ellos el puntaje volveria a
    # depender del numero de hallazgos, es decir, del tamano del informe.
    reglas_todas = _reglas_aplicables(rules, hallazgos)
    objetos = _objetos_evaluados(active, model_data)

    for nombre, d in dominios.items():
        reglas_dom = [r for r in reglas_todas if r.get("domain") == nombre] or None
        detalle = _score(por_dominio_hallazgos.get(nombre, []),
                         reglas_dom or [], objetos)
        d["score"] = detalle["score"]
        d["score_detail"] = detalle

    global_sev: Dict[str, int] = {}
    for h in hallazgos:
        global_sev[h["severity"]] = global_sev.get(h["severity"], 0) + 1

    prioritarios = sorted(
        hallazgos, key=lambda h: (-orden[h["severity"]], h["rule"]))[:15]

    puntuacion = _score(hallazgos, reglas_todas, objetos)
    return {
        "score": puntuacion["score"],
        "score_detail": puntuacion,
        "finding_count": len(hallazgos),
        "by_severity": global_sev,
        "by_domain": dominios,
        "findings": sorted(hallazgos, key=lambda h: (-orden[h["severity"]], h["rule"])),
        "priority": prioritarios,
        "auto_fixable": sorted({h["rule"] for h in hallazgos
                                if h["auto_fix_available"]}),
        "executive_summary": _resumen(global_sev, dominios, prioritarios),
        "warnings": avisos,
    }


# ------------------------------------------------------------- vista compacta -
#: Hallazgos completos que devuelve la vista compacta. El resto se resume en
#: sus grupos: el recuento por regla no se pierde, solo el detalle repetido.
COMPACT_MAX_FINDINGS = 50
#: Objetos de muestra por grupo.
COMPACT_MUESTRAS = 3


def _etiqueta_de_objeto(objeto: Dict[str, Any]) -> str:
    """Como se nombra un objeto en una muestra, sin volcarlo entero."""
    if not isinstance(objeto, dict):
        return str(objeto)
    for clave in ("name", "id", "page", "kind"):
        if objeto.get(clave):
            return str(objeto[clave])
    return "?"


def compactar(resultado: Dict[str, Any], *,
              max_findings: int = COMPACT_MAX_FINDINGS) -> Dict[str, Any]:
    """Vista compacta ADITIVA de una auditoria ya calculada.

    El problema que resuelve es de coste, no de estetica: `priority` repetia
    los quince hallazgos mas graves COMPLETOS, con su evidencia y su
    recomendacion, cuando ya estaban en `findings`. En un informe grande eso
    duplicaba decenas de miles de caracteres dentro de la misma respuesta.

    Aqui cada hallazgo se devuelve UNA vez y con un `finding_id` estable
    dentro de esta ejecucion; `priority` pasa a ser el ORDEN, es decir una
    lista de identificadores. Los recuentos y puntajes por dominio no se
    tocan: son justamente lo que no puede perderse al resumir.

    No se llama sola: `pbi_audit_project` la aplica solo con `compact=true`,
    asi que una llamada existente sigue recibiendo exactamente lo de antes.
    """
    orden = {INFO: 0, WARNING: 1, ERROR: 2}
    hallazgos = [dict(h, finding_id=f"F{i:03d}")
                 for i, h in enumerate(resultado.get("findings") or [], start=1)]
    por_id = {(h["rule"], _etiqueta_de_objeto(h.get("object", {})),
               h["severity"]): h["finding_id"] for h in hallazgos}

    grupos: Dict[str, Dict[str, Any]] = {}
    for h in hallazgos:
        g = grupos.setdefault(h["rule"], {
            "rule": h["rule"], "domain": h["domain"], "count": 0,
            "max_severity": INFO, "sample_objects": [],
            "auto_fix_available": bool(h.get("auto_fix_available")),
            "finding_ids": []})
        g["count"] += 1
        if orden[h["severity"]] > orden[g["max_severity"]]:
            g["max_severity"] = h["severity"]
        if len(g["sample_objects"]) < COMPACT_MUESTRAS:
            g["sample_objects"].append(_etiqueta_de_objeto(h.get("object", {})))
        g["finding_ids"].append(h["finding_id"])
    for g in grupos.values():
        g["sample_truncated"] = g["count"] > len(g["sample_objects"])

    prioridad = [
        por_id.get((p["rule"], _etiqueta_de_objeto(p.get("object", {})),
                    p["severity"]))
        for p in resultado.get("priority") or []]

    devueltos = hallazgos[:max_findings]
    compacto = dict(resultado)
    compacto.update({
        "view": "compact",
        "findings": devueltos,
        "finding_count": resultado.get("finding_count", len(hallazgos)),
        "returned_findings": len(devueltos),
        "total_findings": len(hallazgos),
        "truncated": len(devueltos) < len(hallazgos),
        # IDs y orden, no copias: el detalle ya viaja una vez en `findings`.
        "priority": [i for i in prioridad if i],
        "priority_note": ("Identificadores de `findings` en orden de "
                          "prioridad. El detalle no se repite."),
        "groups": sorted(grupos.values(),
                         key=lambda g: (-orden[g["max_severity"]], -g["count"],
                                        g["rule"])),
    })
    if compacto["truncated"]:
        compacto.setdefault("warnings", [])
        compacto["warnings"] = list(compacto["warnings"]) + [
            f"Se devuelven {len(devueltos)} de {len(hallazgos)} hallazgos; "
            "`groups` conserva el recuento completo por regla. Pide la vista "
            "completa con compact=false."]
    return compacto


def _resumen(sev: Dict[str, int], dominios: Dict[str, Any],
             prioritarios: List[Dict[str, Any]]) -> str:
    partes = []
    total = sum(sev.values())
    if not total:
        return "No se detectaron problemas con las reglas activas."
    partes.append(f"{total} hallazgo(s): "
                  f"{sev.get(ERROR,0)} error(es), {sev.get(WARNING,0)} aviso(s), "
                  f"{sev.get(INFO,0)} informativo(s).")
    peor = min(dominios.items(), key=lambda kv: kv[1]["score"], default=None)
    if peor:
        partes.append(f"El dominio mas debil es '{peor[0]}' (puntaje {peor[1]['score']}).")
    if prioritarios:
        partes.append(f"Lo primero a mirar: {prioritarios[0]['rule']}.")
    return " ".join(partes)


# -------------------------------------------------------------- autofixes ----
#: regla -> como se arregla. Solo las mecanicamente seguras.
AUTOFIXES: Dict[str, Dict[str, Any]] = {
    "report_visual_without_title": {
        "description": "Pone un titulo derivado de los campos del visual.",
        "target": "report",
    },
    "layout_out_of_canvas": {
        "description": "Mete el visual dentro del lienzo.",
        "target": "report",
    },
    "layout_visual_too_small": {
        "description": "Sube el visual al tamano minimo legible.",
        "target": "report",
    },
    "layout_overlap": {
        "description": "Normaliza el layout de la pagina para deshacer solapes.",
        "target": "report",
    },
}


def plan_fixes(active: ActivePbip, auditoria: Dict[str, Any],
               rules: List[str],
               objects: Optional[List[str]] = None) -> Dict[str, Any]:
    """Selecciona hallazgos arreglables y calcula QUE se haria. No escribe.

    Exige `rules` explicito: no existe "arreglar todo". `objects` acota aun mas,
    por id de visual o de pagina.
    """
    if not rules:
        raise ValidationError(
            "Indica que reglas quieres arreglar. No hay 'arreglar todo': cada "
            "correccion debe elegirse a conciencia.",
            details={"auto_fixable": auditoria.get("auto_fixable", []),
                     "available": sorted(AUTOFIXES)})

    desconocidas = [r for r in rules if r not in AUTOFIXES]
    if desconocidas:
        raise ValidationError(
            f"Estas reglas no tienen correccion automatica: {desconocidas}",
            details={"available": sorted(AUTOFIXES)})

    seleccion = [h for h in auditoria.get("findings", [])
                 if h["rule"] in rules and h["auto_fix_available"]]
    if objects:
        objetivos = set(objects)
        seleccion = [h for h in seleccion
                     if h["object"].get("id") in objetivos
                     or h["object"].get("page") in objetivos]

    acciones: List[Dict[str, Any]] = []
    for h in seleccion:
        obj = h["object"]
        if h["rule"] == "report_visual_without_title":
            v = pbir_reader.list_visuals(active, obj["page"], strict=True)
            actual = next((x for x in v if x["id"] == obj["id"]), None)
            if actual is None:
                continue
            campos = (actual.get("measures") or []) + (actual.get("columns") or [])
            titulo = (campos[0].split("[")[-1].rstrip("]") if campos
                      else (actual.get("type") or "Visual"))
            acciones.append({"rule": h["rule"], "action": "set_visual_title",
                             "page": obj["page"], "visual_id": obj["id"],
                             "new_title": titulo,
                             "reason": "Titulo derivado del primer campo del visual."})
        elif h["rule"] in ("layout_out_of_canvas", "layout_visual_too_small",
                           "layout_overlap"):
            pagina = obj.get("page")
            if pagina and not any(a["action"] == "normalize_layout"
                                  and a["page"] == pagina for a in acciones):
                acciones.append({"rule": h["rule"], "action": "normalize_layout",
                                 "page": pagina,
                                 "reason": "Normaliza la geometria de la pagina."})

    return {"planned": True, "rules": rules, "selected_findings": len(seleccion),
            "actions": acciones, "action_count": len(acciones),
            "note": "Nada se ha aplicado. Revisa las acciones y aplicalas."}


def apply_fixes(active: ActivePbip, acciones: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compila y aplica TODO el plan en una sola transaccion.

    Un plan es una unidad logica: si la ultima accion no existe o una escritura
    falla, ninguna de las anteriores puede quedar confirmada. Antes cada helper
    abria su propia transaccion y el bucle ocultaba las excepciones, por lo que
    la tool devolvia exito parcial sobre un informe a medio corregir.
    """
    from horizun_pbi_mcp.pbip import pbir_writer
    from horizun_pbi_mcp.services import pbir_edit, txn as txn_service

    permitidas = {"set_visual_title", "normalize_layout"}
    desconocidas = [a for a in acciones if a.get("action") not in permitidas]
    if desconocidas:
        raise ValidationError(
            "El plan contiene acciones desconocidas; no se aplico ninguna.",
            details={"actions": [a.get("action") for a in desconocidas],
                     "available": sorted(permitidas)})

    documentos: Dict[Any, Dict[str, Any]] = {}
    resultados: Dict[int, Dict[str, Any]] = {}

    # Primero se compilan los titulos. Si el mismo visual tambien se mueve, su
    # posicion se fusiona despues sobre ESTE documento, no sobre otra lectura
    # del original que borraria el titulo nuevo.
    for indice, a in enumerate(acciones):
        if a["action"] != "set_visual_title":
            continue
        plan = pbir_edit.plan_set_visual_title(
            active, a["page"], a["visual_id"], a["new_title"])
        existente = documentos.get(plan["path"])
        if existente is None:
            documentos[plan["path"]] = plan["data"]
        else:
            pbir_edit._fijar_titulo(existente, a["new_title"])  # noqa: SLF001
        resultados[indice] = {"before": plan["before"],
                              "after": plan["after"]}

    paginas = {p["name"]: p for p in pbir_reader.list_pages(active)}
    for indice, a in enumerate(acciones):
        if a["action"] != "normalize_layout":
            continue
        visuales = pbir_reader.list_visuals(active, a["page"], strict=True)
        p = paginas.get(a["page"], {})
        nuevas = layout_doctor.normalize(
            visuales, {"width": p.get("width"), "height": p.get("height")})
        planes = pbir_writer.plan_visuals_bulk(active, a["page"], nuevas)
        for plan in planes:
            documento = documentos.setdefault(plan["path"], plan["data"])
            documento["position"] = plan["after"]
        resultados[indice] = {"moved": len(planes)}

    aplicadas = [{**a, "result": resultados[i]}
                 for i, a in enumerate(acciones)]
    if not documentos:
        return {"applied": len(aplicadas), "failed": 0,
                "actions_applied": aplicadas, "actions_failed": [],
                "warnings": [], "transaction": None, "backup": None}

    pbir_edit.assert_escritura_pbir(active, "Aplicar correcciones de auditoria")
    cm = txn_service.project_transaction(
        active, list(documentos), tool="pbi_apply_audit_fixes")
    with cm as tx:
        for ruta, datos in documentos.items():
            tx.write_json(ruta, datos)

    return {"applied": len(aplicadas), "failed": 0,
            "actions_applied": aplicadas, "actions_failed": [],
            "warnings": [], "backup": cm.result["journal"],
            "transaction": cm.result}


# ----------------------------------------------------------------- salidas ---
def to_markdown(auditoria: Dict[str, Any],
                titulo: str = "Horizun PBI MCP — Auditoria") -> str:
    out = [f"# {titulo}", "",
           f"**Puntaje global: {auditoria['score']}/100**", "",
           auditoria["executive_summary"], "", "## Por dominio", "",
           "| Dominio | Puntaje | Hallazgos |", "|---|---|---|"]
    for dom, d in sorted(auditoria["by_domain"].items()):
        out.append(f"| {dom} | {d['score']} | {d['findings']} |")
    out += ["", "## Prioridad", ""]
    for h in auditoria["priority"]:
        obj = h["object"].get("name") or h["object"].get("id") or h["object"].get("kind")
        out += [f"### [{h['severity'].upper()}] {h['rule']}",
                f"- **Objeto:** {obj}",
                f"- **Evidencia:** `{json.dumps(h['evidence'], ensure_ascii=False)}`",
                f"- **Recomendacion:** {h['recommendation']}",
                f"- **Autofix:** {'si' if h['auto_fix_available'] else 'no'}", ""]
    return "\n".join(out)


def to_html(auditoria: Dict[str, Any],
            titulo: str = "Horizun PBI MCP — Auditoria") -> str:
    colores = {ERROR: "#E4572E", WARNING: "#F4A259", INFO: "#4C9AFF"}
    filas = "".join(
        f"<tr><td>{html_mod.escape(d)}</td><td>{v['score']}</td>"
        f"<td>{v['findings']}</td></tr>"
        for d, v in sorted(auditoria["by_domain"].items()))
    tarjetas = "".join(
        f'<div class="f" style="border-left-color:{colores.get(h["severity"], "#888")}">'
        f'<div class="r">{html_mod.escape(h["rule"])}'
        f'<span class="s">{h["severity"]}</span></div>'
        f'<div class="o">{html_mod.escape(str(h["object"].get("name") or h["object"].get("id") or h["object"].get("kind","")))}</div>'
        f'<div class="e">{html_mod.escape(json.dumps(h["evidence"], ensure_ascii=False))}</div>'
        f'<div class="rec">{html_mod.escape(h["recommendation"])}</div></div>'
        for h in auditoria["priority"])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html_mod.escape(titulo)}</title><style>
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#12121a;color:#E6E6EF;
margin:0;padding:32px;max-width:1000px;margin:0 auto}}
h1{{color:#2EC4B6}} .score{{font-size:44px;font-weight:700;color:#2EC4B6}}
table{{border-collapse:collapse;width:100%;margin:16px 0}}
th,td{{border:1px solid #2b2b3d;padding:8px 12px;text-align:left}}
th{{background:#1A1A26;color:#8AA0B4}}
.f{{background:#1A1A26;border-left:4px solid;border-radius:6px;padding:12px 16px;margin:10px 0}}
.r{{font-weight:600}} .s{{float:right;font-size:11px;text-transform:uppercase;color:#8AA0B4}}
.o{{color:#B7C0CC;font-size:13px;margin:4px 0}}
.e{{font-family:Consolas,monospace;font-size:12px;color:#8AA0B4}}
.rec{{margin-top:6px;font-size:14px}}
@media(prefers-color-scheme:light){{body{{background:#fff;color:#1a1a26}}
.f{{background:#f5f5f7}}}}</style></head><body>
<h1>{html_mod.escape(titulo)}</h1>
<div class="score">{auditoria['score']}<span style="font-size:18px;color:#8AA0B4">/100</span></div>
<p>{html_mod.escape(auditoria['executive_summary'])}</p>
<h2>Por dominio</h2><table><tr><th>Dominio</th><th>Puntaje</th><th>Hallazgos</th></tr>
{filas}</table><h2>Prioridad</h2>{tarjetas}</body></html>"""
