"""Spec declarativo de paginas: schema versionado, validacion, diff y aplicacion.

    building blocks -> spec -> validacion -> resolucion contra el modelo
    -> layout -> preview -> diff -> plan token -> apply -> verificacion

Tres decisiones que hacen esto utilizable:

1. Los errores de validacion traen JSON PATH (`visuals[2].fields.values[0]`),
   no un mensaje suelto: un agente puede corregir el spec sin adivinar donde.
2. Con `seed`, los identificadores son DETERMINISTAS: el mismo spec produce
   los mismos ids, asi que un diff entre dos ejecuciones es legible.
3. El layout se resuelve ANTES del preview, y el preview usa exactamente esas
   posiciones. Lo que se ve es lo que se escribe.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from config import ActivePbip
from powerbi.errors import PowerBIMCPError, ValidationError
from pbip import layout_engine, page_builder, pbir_reader, pbir_writer, visual_factory
from services import layout_doctor, model_explorer

SCHEMA_VERSION = "1.0"
SUPPORTED_VERSIONS = ("1.0",)


class UnsupportedSpecFeature(PowerBIMCPError):
    """El spec pide algo que este servidor todavia no sabe escribir en PBIR."""

    code = "unsupported_feature"


#: Propiedades que el esquema acepta pero que NADIE serializa a PBIR todavia.
#: Se rechazan en vez de descartarse: un spec con filtros producia una pagina
#: sin filtros y sin decirlo, y el usuario solo lo descubria abriendo el
#: informe. Cuando se implemente la serializacion, se quitan de aqui.
NO_SOPORTADO_AUN: Dict[str, str] = {}


def assert_soportado(spec: Dict[str, Any]) -> None:
    """Rechaza lo que el spec admite pero el escritor no sabe materializar.

    Se llama ANTES de compilar, de modo que falla igual en validate, preview,
    diff, dry_run y apply: nunca se llega a prometer una pagina que no incluye
    lo que se pidio.
    """
    no_soportadas = []
    for clave, motivo in NO_SOPORTADO_AUN.items():
        valor = spec.get(clave)
        if isinstance(valor, list) and valor:
            for i, entrada in enumerate(valor):
                no_soportadas.append({
                    "path": f"$.{clave}[{i}]", "property": clave,
                    "reason": motivo,
                    "value": entrada if isinstance(entrada, (str, int, float))
                    else "(objeto)"})

    if no_soportadas:
        propiedades = sorted({x["property"] for x in no_soportadas})
        raise UnsupportedSpecFeature(
            f"El spec usa {len(no_soportadas)} elemento(s) de "
            f"{', '.join(propiedades)} que este servidor todavia no escribe en "
            "PBIR. Se rechaza en vez de crear la pagina sin ellos.",
            details={"unsupported": no_soportadas,
                     "properties": propiedades,
                     "schema_version": SCHEMA_VERSION})


class SpecValidationError(PowerBIMCPError):
    """El spec no es valido. `details.errors` trae el JSON path de cada fallo."""

    code = "page_spec_invalid"


# ------------------------------------------------------------------ presets ---
#: Cada preset describe la INTENCION de una pagina: que bloques la componen y
#: en que proporciones. Los campos concretos los pone quien genera el spec.
PRESETS: Dict[str, Dict[str, Any]] = {
    "executive": {
        "description": "Resumen ejecutivo: fila de KPIs y un grafico protagonista.",
        "layout": "executive_summary",
        "blocks": [{"role": "kpi", "count": 4, "type": "card"},
                   {"role": "hero", "count": 1, "type": "columnChart"},
                   {"role": "support", "count": 2, "type": "barChart"}],
    },
    "financial": {
        "description": "Financiero: KPIs monetarios, evolucion y detalle tabular.",
        "layout": "dashboard",
        "blocks": [{"role": "kpi", "count": 4, "type": "card"},
                   {"role": "trend", "count": 1, "type": "lineChart"},
                   {"role": "detail", "count": 1, "type": "table"}],
    },
    "sales": {
        "description": "Ventas: KPIs, ranking por categoria y evolucion temporal.",
        "layout": "dashboard",
        "blocks": [{"role": "kpi", "count": 3, "type": "card"},
                   {"role": "ranking", "count": 1, "type": "barChart"},
                   {"role": "trend", "count": 1, "type": "lineChart"}],
    },
    "operations": {
        "description": "Operaciones: indicadores y desglose por dimension.",
        "layout": "dashboard",
        "blocks": [{"role": "kpi", "count": 4, "type": "card"},
                   {"role": "breakdown", "count": 2, "type": "columnChart"}],
    },
    "evm": {
        "description": "EVM: PV/EV/AC/CPI/SPI y curva S.",
        "layout": "executive_summary",
        "blocks": [{"role": "kpi", "count": 5, "type": "card"},
                   {"role": "s_curve", "count": 1, "type": "lineChart"},
                   {"role": "variance", "count": 1, "type": "columnChart"}],
    },
    "detail": {
        "description": "Detalle: tabla amplia con contexto minimo.",
        "layout": "grid",
        "blocks": [{"role": "kpi", "count": 2, "type": "card"},
                   {"role": "detail", "count": 1, "type": "table"}],
    },
}


def list_presets() -> List[Dict[str, Any]]:
    return [{"preset": k, **{kk: vv for kk, vv in v.items() if kk != "blocks"},
             "blocks": v["blocks"]} for k, v in sorted(PRESETS.items())]


# --------------------------------------------------------------- validacion ---
def _err(path: str, mensaje: str, sugerencia: str = "") -> Dict[str, str]:
    e = {"path": path, "message": mensaje}
    if sugerencia:
        e["hint"] = sugerencia
    return e


def validate_schema(spec: Any) -> List[Dict[str, str]]:
    """Valida la FORMA del spec. Devuelve la lista de errores con su JSON path."""
    errores: List[Dict[str, str]] = []
    if not isinstance(spec, dict):
        return [_err("$", "El spec debe ser un objeto JSON.")]

    version = spec.get("schema_version")
    if version is None:
        errores.append(_err("$.schema_version", "Falta 'schema_version'.",
                            f"Usa '{SCHEMA_VERSION}'."))
    elif version not in SUPPORTED_VERSIONS:
        errores.append(_err("$.schema_version",
                            f"Version no soportada: '{version}'.",
                            f"Soportadas: {list(SUPPORTED_VERSIONS)}."))

    page = spec.get("page")
    if not isinstance(page, dict):
        errores.append(_err("$.page", "Falta el objeto 'page'."))
    else:
        if not page.get("name"):
            errores.append(_err("$.page.name", "La pagina necesita un nombre."))
        for dim in ("width", "height"):
            if dim in page and not isinstance(page[dim], (int, float)):
                errores.append(_err(f"$.page.{dim}", f"'{dim}' debe ser numerico."))
            elif dim in page and page[dim] <= 0:
                errores.append(_err(f"$.page.{dim}", f"'{dim}' debe ser > 0."))

    visuals = spec.get("visuals")
    if not isinstance(visuals, list):
        errores.append(_err("$.visuals", "'visuals' debe ser una lista."))
    elif not visuals:
        errores.append(_err("$.visuals", "El spec necesita al menos un visual."))
    else:
        for i, v in enumerate(visuals):
            base = f"$.visuals[{i}]"
            if not isinstance(v, dict):
                errores.append(_err(base, "Cada visual debe ser un objeto."))
                continue
            if not v.get("type"):
                errores.append(_err(f"{base}.type", "Falta 'type'.",
                                    f"Soportados: {sorted(set(visual_factory.TYPE_MAP))}"))
            elif str(v["type"]).lower() not in visual_factory.TYPE_MAP:
                errores.append(_err(
                    f"{base}.type", f"Tipo no soportado: '{v['type']}'.",
                    f"Soportados: {sorted(set(visual_factory.TYPE_MAP.values()))}"))
            campos = v.get("fields")
            if campos is not None and not isinstance(campos, dict):
                errores.append(_err(f"{base}.fields",
                                    "'fields' debe ser un objeto rol -> campos."))
            pos = v.get("position")
            if pos is not None:
                if not isinstance(pos, dict):
                    errores.append(_err(f"{base}.position",
                                        "'position' debe ser un objeto."))
                else:
                    for k in ("x", "y", "width", "height"):
                        if k in pos and not isinstance(pos[k], (int, float)):
                            errores.append(_err(f"{base}.position.{k}",
                                                f"'{k}' debe ser numerico."))

    for clave in ("filters", "interactions"):
        if clave in spec and not isinstance(spec[clave], list):
            errores.append(_err(f"$.{clave}", f"'{clave}' debe ser una lista."))

    layout = spec.get("layout")
    if layout is not None and not isinstance(layout, dict):
        errores.append(_err("$.layout", "'layout' debe ser un objeto."))
    return errores


def resolve_references(spec: Dict[str, Any],
                       model_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Comprueba que cada campo del spec exista en el modelo.

    Sin modelo cargado no se inventa nada: se reporta `unresolved` y quien
    llama decide. Una referencia ambigua (nombre de columna repetido en varias
    tablas) se rechaza en vez de elegir una.
    """
    if not model_data:
        return {"resolved": False, "reason": "No hay modelo cargado para validar.",
                "errors": [], "warnings": ["Los campos no se han verificado."]}

    indice = model_explorer.build_index(model_data)
    errores: List[Dict[str, str]] = []
    avisos: List[str] = []
    resueltos: List[Dict[str, str]] = []

    for i, v in enumerate(spec.get("visuals") or []):
        campos = v.get("fields") or {}
        for rol, refs in campos.items():
            lista = refs if isinstance(refs, list) else [refs]
            for j, ref in enumerate(lista):
                path = f"$.visuals[{i}].fields.{rol}[{j}]"
                if not isinstance(ref, str) or not ref.strip():
                    errores.append(_err(path, "Referencia vacia."))
                    continue
                limpio = ref.strip()
                r = model_explorer.resolve_reference(limpio.strip("[]") if
                                                     limpio.startswith("[") else limpio,
                                                     indice)
                if not r["exists"]:
                    errores.append(_err(
                        path, f"'{ref}' no existe en el modelo.",
                        "Usa 'Tabla[Columna]' o '[Medida]'. Consulta los campos "
                        "disponibles con pbi_page_building_blocks."))
                    continue
                if r.get("note") == "resuelta por nombre de columna unico":
                    coincidencias = [c for c in indice["columns"]
                                     if c.endswith(f"[{limpio}]")]
                    if len(coincidencias) > 1:
                        errores.append(_err(
                            path, f"'{ref}' es ambiguo: existe en {coincidencias}.",
                            "Cualifica la referencia con su tabla."))
                        continue
                    avisos.append(f"{path}: '{ref}' se resolvio como {r['ref']}.")
                resueltos.append({"path": path, "ref": ref, "resolved": r["ref"],
                                  "kind": r["kind"]})

    return {"resolved": not errores, "errors": errores, "warnings": avisos,
            "references": resueltos}


# ------------------------------------------------------------------ layout ---
def resolve_layout(spec: Dict[str, Any], canvas: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Calcula la posicion FINAL de cada visual. Determinista."""
    visuals = spec.get("visuals") or []
    layout = (spec.get("layout") or {})
    modo = layout.get("preset") or layout.get("mode")
    spacing = int(layout.get("gap", layout.get("spacing", 16)))

    if modo in PRESETS:
        modo = PRESETS[modo]["layout"]
    if modo is None:
        modo = "grid" if not all(v.get("position") for v in visuals) else None

    if modo is None:
        return [dict(v["position"]) for v in visuals]

    items = [{"visual_id": str(i), "type": visual_factory.resolve_type(v["type"])}
             for i, v in enumerate(visuals)]
    calculadas = layout_engine.compute_layout(items, modo, canvas, spacing)
    por_indice = {c["visual_id"]: c for c in calculadas}
    salida = []
    for i, v in enumerate(visuals):
        c = por_indice[str(i)]
        salida.append({"x": c["x"], "y": c["y"],
                       "width": c["width"], "height": c["height"]})
    return salida


def deterministic_id(seed: str, kind: str, index: int) -> str:
    """Id reproducible a partir de una semilla. Sin semilla, uno aleatorio."""
    if not seed:
        return pbir_writer.new_id()
    material = f"{seed}|{kind}|{index}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:20]


# ----------------------------------------------------------------- proceso ---
def compile_spec(active: ActivePbip, spec: Dict[str, Any],
                 model_data: Optional[Dict[str, Any]],
                 *, seed: str = "") -> Dict[str, Any]:
    """Valida, resuelve referencias y layout, y construye TODO en memoria.

    No escribe nada. Es el paso comun a preview, diff y apply, para que los
    tres describan exactamente lo mismo.
    """
    errores = validate_schema(spec)
    if errores:
        raise SpecValidationError(
            f"El spec tiene {len(errores)} error(es) de esquema.",
            details={"errors": errores, "schema_version": SCHEMA_VERSION})

    # Antes de resolver nada: lo que no sabemos escribir se rechaza, no se
    # ignora. Si no, preview y diff prometerian una pagina que apply no crea.
    assert_soportado(spec)

    refs = resolve_references(spec, model_data)
    if refs["errors"]:
        raise SpecValidationError(
            f"El spec referencia {len(refs['errors'])} campo(s) que no existen "
            "o son ambiguos.",
            details={"errors": refs["errors"], "warnings": refs["warnings"]})

    page = spec["page"]
    canvas = {"width": int(page.get("width", 1280)),
              "height": int(page.get("height", 720))}
    posiciones = resolve_layout(spec, canvas)

    indice_medidas = {}
    if model_data:
        for m in model_data.get("measures", []):
            if m.get("name") and m.get("table"):
                indice_medidas[m["name"]] = m["table"]

    construidos, avisos = [], list(refs.get("warnings") or [])
    for i, (v, pos) in enumerate(zip(spec["visuals"], posiciones)):
        built = visual_factory.build_visual(
            active, v["type"], v.get("fields", {}), pos, v.get("title"),
            indice_medidas, options=v.get("options"))
        vid = deterministic_id(seed, "visual", i)
        built["visual"]["name"] = vid
        construidos.append({"visual": built["visual"],
                            "meta": {"type": built["actual_type"],
                                     "title": v.get("title"),
                                     "origin": built["origin"],
                                     "options": v.get("options"),
                                     "index": i}})
        avisos.extend(built["warnings"])

    geometria = layout_doctor.detect_issues(
        [{"id": c["visual"]["name"], "type": c["meta"]["type"],
          "position": c["visual"]["position"]} for c in construidos], canvas)

    # Filtros de pagina e interacciones. Los filtros por visual viajan en el
    # propio visual; los de pagina, en la pagina.
    from pbip import filter_builder

    for c, v in zip(construidos, spec["visuals"]):
        propio = filter_builder.build_filter_config(v.get("filters") or [])
        if propio:
            c["visual"]["filterConfig"] = propio

    filtros_pagina = filter_builder.build_filter_config(spec.get("filters") or [])
    interacciones = filter_builder.build_interactions(
        spec.get("interactions") or [],
        [c["visual"]["name"] for c in construidos])

    return {"page_name": page["name"], "canvas": canvas, "visuals": construidos,
            "positions": posiciones, "warnings": avisos,
            "layout_issues": geometria, "references": refs.get("references", []),
            "page_filter_config": filtros_pagina,
            "page_interactions": interacciones,
            "seed": seed}


def preview(active: ActivePbip, compilado: Dict[str, Any]) -> str:
    """Maqueta HTML con las posiciones FINALES: lo que se ve es lo que se escribe."""
    visuales = []
    for c, pos in zip(compilado["visuals"], compilado["positions"]):
        nodo = c["visual"].get("visual", {})
        campos = pbir_reader._extract_fields(nodo)      # noqa: SLF001
        visuales.append({"type": c["meta"]["type"], "title": c["meta"]["title"],
                         "position": pos, "measures": campos["measures"],
                         "columns": campos["columns"],
                         "options": c["meta"].get("options")})
    return page_builder.render_html(compilado["page_name"], compilado["canvas"],
                                    visuales, standalone=True)


def diff_against_page(active: ActivePbip, compilado: Dict[str, Any],
                      page: Optional[str] = None) -> Dict[str, Any]:
    """Compara el spec compilado con una pagina existente."""
    objetivo = page or compilado["page_name"]
    try:
        actuales = pbir_reader.list_visuals(active, objetivo)
        existe = True
    except Exception:                                   # noqa: BLE001
        actuales, existe = [], False

    if not existe:
        return {"page": objetivo, "page_exists": False,
                "change": "create",
                "added": [{"type": c["meta"]["type"], "title": c["meta"]["title"]}
                          for c in compilado["visuals"]],
                "removed": [], "modified": [],
                "summary": f"Se creara la pagina con {len(compilado['visuals'])} visuales."}

    def firma(tipo, titulo, medidas, columnas):
        return (tipo, titulo or "", tuple(sorted(medidas)), tuple(sorted(columnas)))

    nuevos = []
    for c in compilado["visuals"]:
        campos = pbir_reader._extract_fields(c["visual"].get("visual", {}))  # noqa: SLF001
        nuevos.append({"key": firma(c["meta"]["type"], c["meta"]["title"],
                                    campos["measures"], campos["columns"]),
                       "type": c["meta"]["type"], "title": c["meta"]["title"]})
    viejos = [{"key": firma(v.get("type"), v.get("title"),
                            v.get("measures", []), v.get("columns", [])),
               "type": v.get("type"), "title": v.get("title"), "id": v["id"]}
              for v in actuales]

    claves_nuevas = {n["key"] for n in nuevos}
    claves_viejas = {o["key"] for o in viejos}
    return {
        "page": objetivo, "page_exists": True, "change": "update",
        "added": [{"type": n["type"], "title": n["title"]}
                  for n in nuevos if n["key"] not in claves_viejas],
        "removed": [{"type": o["type"], "title": o["title"], "id": o["id"]}
                    for o in viejos if o["key"] not in claves_nuevas],
        "unchanged": len(claves_nuevas & claves_viejas),
        "summary": (f"{len(claves_nuevas - claves_viejas)} visual(es) nuevos, "
                    f"{len(claves_viejas - claves_nuevas)} sobrarian, "
                    f"{len(claves_nuevas & claves_viejas)} iguales."),
    }


def apply_spec(active: ActivePbip, compilado: Dict[str, Any], *,
               page: Optional[str] = None,
               sync_mode: str = "merge") -> Dict[str, Any]:
    """Aplica el spec en UNA sola transaccion: crea o ACTUALIZA.

    Antes llamaba siempre a `create_page_with_visuals`, que ante una pagina
    existente devolvia `created: False` y no tocaba nada: aplicar un spec sobre
    una pagina que ya existia no hacia nada y decia que todo habia ido bien.
    """
    from pathlib import Path as _Path

    from services import page_update
    from services import txn as txn_service

    plan = page_update.planificar(active, compilado, page=page,
                                  sync_mode=sync_mode)
    if plan["change"] == page_update.NO_CHANGE:
        return {"change": page_update.NO_CHANGE, "page_id": plan["page_id"],
                "applied": 0, "summary": page_update.resumen(plan),
                "visuals_created": []}

    pbir_edit_mod = __import__("services.pbir_edit", fromlist=["x"])
    pbir_edit_mod.assert_escritura_pbir(active, "Aplicar un page spec")

    destinos = list(plan["files"]) + list(plan["deletes"])
    cm = txn_service.project_transaction(active, destinos,
                                         tool="pbi_apply_page_spec")
    with cm as t:
        for ruta, datos in plan["files"].items():
            t.write_json(_Path(ruta), datos)
        for ruta in plan["deletes"]:
            t.delete(_Path(ruta))
            try:
                if _Path(ruta).parent.exists() and not any(_Path(ruta).parent.iterdir()):
                    _Path(ruta).parent.rmdir()
            except OSError:                           # pragma: no cover
                pass
    for d in plan["ensure_dirs"]:
        _Path(d).mkdir(parents=True, exist_ok=True)

    return {"change": plan["change"], "page_id": plan["page_id"],
            "created": plan["change"] == page_update.CREATE,
            "display_name": compilado["page_name"],
            "applied": len(plan["files"]) + len(plan["deletes"]),
            "added": plan["added"], "updated": plan["updated"],
            "kept": plan["kept"], "removed": plan.get("removed", []),
            "not_removed": plan.get("not_removed", []),
            "sync_mode": plan["sync_mode"],
            "summary": page_update.resumen(plan),
            "visuals_created": [{"id": v} for v in plan["added"]],
            "backup": cm.result["journal"], "transaction": cm.result,
            "validation_report": cm.validation}


def validate_generated_page(active: ActivePbip, page: str,
                            model_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Verifica una pagina YA escrita: referencias y geometria."""
    visuales = pbir_reader.list_visuals(active, page)
    p = next((x for x in pbir_reader.list_pages(active)
              if x["name"] == page or x.get("display_name") == page), {})
    canvas = {"width": p.get("width", 1280), "height": p.get("height", 720)}

    rotas: List[Dict[str, str]] = []
    if model_data:
        indice = model_explorer.build_index(model_data)
        for v in visuales:
            for ref in list(v.get("measures", [])) + list(v.get("columns", [])):
                if not model_explorer.resolve_reference(ref, indice)["exists"]:
                    rotas.append({"visual_id": v["id"], "reference": ref})

    geometria = layout_doctor.detect_issues(visuales, canvas)
    sin_titulo = [v["id"] for v in visuales if not v.get("title")]

    return {
        "page": page, "visual_count": len(visuales), "canvas": canvas,
        "broken_references": rotas,
        "layout": {"issue_count": geometria["issue_count"],
                   "by_severity": geometria["by_severity"],
                   "issues": geometria["issues"]},
        "visuals_without_title": sin_titulo,
        "valid": not rotas and geometria["by_severity"].get("error", 0) == 0,
        "warnings": ([f"{len(rotas)} referencia(s) rota(s)"] if rotas else [])
        + ([f"{len(sin_titulo)} visual(es) sin titulo"] if sin_titulo else []),
    }
