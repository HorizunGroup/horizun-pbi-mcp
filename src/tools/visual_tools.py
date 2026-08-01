"""Tools de informe PBIR: leer, documentar, crear y acomodar visuales (Fases 7-10)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session, get_settings
from logging_config import get_logger
from powerbi import model_reader
from powerbi.errors import ValidationError
from pbip import layout_engine, pbir_reader, pbir_writer, tmdl_reader, visual_factory
from reporting import document_layout_markdown
from tools._common import guard, guard_mutation
from utils.file_utils import atomic_write_text, timestamp
from utils.validation import validate_position

log = get_logger("visual_tools")

RELOAD_HINT = (
    "Para ver los cambios: cierra y reabre el informe en Power BI Desktop. "
    "Si Desktop esta abierto y guardas (Ctrl+S), sobrescribiras estos cambios en disco; "
    "por eso conviene editar el PBIR con Desktop cerrado."
)


def _model_data() -> Optional[Dict[str, Any]]:
    """Modelo contra el que se resuelven los campos al escribir en el informe.

    Manda el TMDL del proyecto activo, no el modelo en vivo. Quien escribe un
    visual lo escribe en ESE .pbip, y sus campos tienen que existir ahi. Al
    reves —que mandara el modelo en vivo— bastaba con tener otro .pbix abierto
    en Desktop para que las medidas recien escritas en el TMDL se dieran por
    inexistentes y se rechazara la pagina.

    El modelo en vivo sigue siendo el respaldo: sin proyecto abierto, o sin
    TMDL, es la unica fuente que hay.
    """
    session = get_session()
    try:
        if session.active_pbip and session.active_pbip.has_tmdl:
            return tmdl_reader.read_semantic_model(session.active_pbip)
        if session.active_model:
            return model_reader.read_model(session)
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo leer el modelo para resolver campos: %s", exc)
    return None


def _measure_index(model_data: Optional[Dict[str, Any]]) -> Dict[str, str]:
    idx: Dict[str, str] = {}
    if not model_data:
        return idx
    for m in model_data.get("measures", []):
        if m.get("name") and m.get("table"):
            idx[m["name"]] = m["table"]
    return idx


def _detect_canvas(active) -> Dict[str, int]:
    try:
        pages = pbir_reader.list_pages(active)
        widths = [p.get("width") for p in pages if p.get("width")]
        heights = [p.get("height") for p in pages if p.get("height")]
        if widths and heights:
            return {"width": max(widths), "height": max(heights)}
    except Exception:  # noqa: BLE001
        pass
    return {"width": 1280, "height": 720}


def register(mcp) -> None:
    @mcp.tool()
    def pbi_list_report_pages() -> Dict[str, Any]:
        """Lista las paginas del informe PBIR activo (id, nombre, tamano, nº visuales)."""
        def _impl():
            active = get_session().require_active_pbip()
            pages = pbir_reader.list_pages(active)
            return {"count": len(pages), "pages": pages}
        return guard(_impl)

    @mcp.tool()
    def pbi_list_visuals(page: str) -> Dict[str, Any]:
        """Lista los visuales de una pagina: id, tipo, posicion, campos, titulo.

        `page`: id interno o nombre visible de la pagina.
        """
        def _impl():
            active = get_session().require_active_pbip()
            visuals = pbir_reader.list_visuals(active, page)
            return {"page": page, "count": len(visuals), "visuals": visuals}
        return guard(_impl)

    @mcp.tool()
    def pbi_document_report_layout() -> Dict[str, Any]:
        """Genera documentacion Markdown del layout del informe (paginas y visuales)."""
        def _impl():
            active = get_session().require_active_pbip()
            pages = pbir_reader.list_pages(active)
            for p in pages:
                p["visuals"] = pbir_reader.list_visuals(active, p["name"])
            md = document_layout_markdown(active.to_dict(), pages)
            out_path = get_settings().outputs_dir / f"report_layout_{timestamp()}.md"
            atomic_write_text(out_path, md)
            return {"output_path": str(out_path), "page_count": len(pages)}
        return guard(_impl)

    @mcp.tool()
    def pbi_create_visual(
        page: str,
        visual_type: str,
        fields: Dict[str, Any],
        position: Dict[str, float],
        title: Optional[str] = None,
    request_id: str = "") -> Dict[str, Any]:
        """Crea un visual PBIR en una pagina.

        `visual_type`: card|table|matrix|slicer|barChart|columnChart|lineChart|pieChart.
        `fields`: rol -> campos, p.ej. {"category":"Tabla[Col]",
                  "values":["[Medida]"], "legend":"Tabla[Col]"}.

        El rol se reconoce escrito como sea: el logico (`values`), el nombre
        que usa PBIR y que devuelve `pbi_list_visuals` (`Values`, `Y`,
        `Category`, `Data`) o un sinonimo (`measure`, `axis`). Cada campo puede
        ser `"Tabla[Campo]"` o el objeto que devuelve `pbi_list_visuals`, para
        poder leer una pagina y rehacerla sin traducir nada.

        Un rol que ese tipo de visual no tiene se RECHAZA con la lista de los
        validos. Antes se descartaba en silencio y el visual salia sin datos.

        `position`: {x, y, width, height} (z opcional).
        Clona un visual del mismo tipo como plantilla si existe. Hace backup.
        """
        def _impl():
            active = get_session().require_active_pbip()
            pos = validate_position(position)
            mi = _measure_index(_model_data())
            built = visual_factory.build_visual(active, visual_type, fields or {}, pos, title, mi)
            res = pbir_writer.write_visual(active, page, built["visual"])
            return {
                "visual_id": res["visual_id"],
                "file": res["file"],
                "backup": res["backup"],
                "actual_type": built["actual_type"],
                "origin": built["origin"],
                "warnings": built["warnings"],
                "reload_hint": RELOAD_HINT,
            }
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_add_custom_visual(visual_id: Optional[str] = None, request_id: str = "") -> Dict[str, Any]:
        """Registra un custom visual de AppSource en el informe (publicCustomVisuals).

        Sin argumentos registra "HTML Content" (renderiza HTML/SVG desde una medida
        DAX). Power BI Desktop lo descarga de AppSource al abrir el informe.
        """
        def _impl():
            active = get_session().require_active_pbip()
            vid = visual_id or pbir_writer.HTML_CONTENT_GUID
            return pbir_writer.add_public_custom_visual(active, vid)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_create_html_visual(
        page: str,
        html_measure: str,
        position: Dict[str, float],
        title: Optional[str] = None,
    request_id: str = "") -> Dict[str, Any]:
        """Crea un visual "HTML Content" que renderiza el HTML/SVG devuelto por una medida.

        `html_measure`: medida cuyo resultado es HTML (p.ej. "[HTML Panel EVM]").
        Registra el custom visual en el informe si aun no esta. La medida se crea
        aparte con pbi_create_measure (el HTML se arma en DAX, tipicamente con VARs
        y CONCATENATEX sobre los datos del modelo).
        """
        def _impl():
            active = get_session().require_active_pbip()
            pos = validate_position(position)
            mi = _measure_index(_model_data())
            # Se construye ANTES de escribir: si el visual no puede armarse, no
            # queda un custom visual registrado sin nadie que lo use.
            built = visual_factory.build_visual(
                active, "htmlContent", {"values": [html_measure]}, pos, title, mi)
            # report.json + visual.json en UNA sola transaccion.
            res = pbir_writer.write_visual_with_registration(
                active, page, pbir_writer.HTML_CONTENT_GUID, built["visual"],
                tool="pbi_create_html_visual")
            return {
                "visual_id": res["visual_id"],
                "file": res["file"],
                "backup": res["backup"],
                "transaction": res["transaction"],
                "custom_visual_registered": res["custom_visual_registered"],
                "warnings": built["warnings"],
                "reload_hint": RELOAD_HINT + " La primera vez, Desktop descargara el "
                               "visual HTML Content de AppSource (requiere internet).",
            }
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_update_visual_position(page: str, visual_id: str, x: float, y: float,
                                   width: float, height: float,
                                   z: Optional[float] = None, request_id: str = "") -> Dict[str, Any]:
        """Mueve/redimensiona un visual existente."""
        def _impl():
            active = get_session().require_active_pbip()
            res = pbir_writer.update_visual_position(
                active, page, visual_id, x, y, width, height, z)
            res["reload_hint"] = RELOAD_HINT
            return res
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_arrange_visuals(
        page: str,
        layout: str = "grid",
        visual_ids: Optional[List[str]] = None,
        canvas: Optional[Dict[str, float]] = None,
        spacing: float = 16,
        custom: Optional[Dict[str, Dict[str, float]]] = None,
    request_id: str = "") -> Dict[str, Any]:
        """Reorganiza los visuales de una pagina.

        `layout`: grid | dashboard | executive_summary | custom.
        `visual_ids`: subconjunto opcional (por defecto todos). `custom`: mapa
        visual_id -> {x,y,width,height} para layout 'custom'.
        """
        def _impl():
            active = get_session().require_active_pbip()
            visuals = pbir_reader.list_visuals(active, page)
            items = [{"visual_id": v["id"], "type": v["type"]} for v in visuals]
            if visual_ids:
                wanted = set(visual_ids)
                items = [i for i in items if i["visual_id"] in wanted]
            if not items:
                raise ValidationError("No hay visuales para acomodar en esta pagina.")
            cvs = canvas or _detect_canvas(active)
            new_positions = layout_engine.compute_layout(items, layout, cvs, spacing, custom)

            # Una sola transaccion sobre todos los visual.json: la capa PBIR lee,
            # valida y construye todo en memoria antes de escribir nada.
            res = pbir_writer.update_visuals_bulk(
                active, page, new_positions, tool="pbi_arrange_visuals")
            return {"page": page, "layout": layout, "canvas": cvs,
                    "moved": res["moved"], "positions": new_positions,
                    "backup": res.get("backup"),
                    "transaction": res.get("transaction"),
                    "reload_hint": RELOAD_HINT}
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_generate_report_page(
        page_name: str,
        objective: str = "",
        available_fields_hint: Optional[List[str]] = None,
        layout: str = "executive_summary",
        create_missing_measures: bool = False,
    request_id: str = "") -> Dict[str, Any]:
        """Genera una pagina con visuales propuestos a partir del modelo.

        Revisa el modelo, valida los campos sugeridos (no inventa campos), crea la
        pagina, agrega tarjetas para medidas y un grafico por categoria, y acomoda
        todo con el layout indicado. Devuelve un resumen de lo creado.
        """
        def _impl():
            session = get_session()
            active = session.require_active_pbip()
            model_data = _model_data()
            mi = _measure_index(model_data)

            # conjuntos de validacion
            known_measures = set(mi.keys())
            known_columns = set()
            if model_data:
                for t in model_data.get("tables", []):
                    for c in t.get("columns", []):
                        known_columns.add(f"{t['name']}[{c['name']}]")

            hints = available_fields_hint or []
            measures, columns, unknown = [], [], []
            for h in hints:
                bare = h.strip()
                name = bare[1:-1] if bare.startswith("[") and bare.endswith("]") else None
                if name and name in known_measures:
                    measures.append(name)
                elif bare in known_columns:
                    columns.append(bare)
                elif model_data is None:
                    # sin modelo cargado, aceptamos el hint tal cual
                    (measures if bare.startswith("[") else columns).append(bare)
                else:
                    unknown.append(h)

            warnings: List[str] = []
            if unknown:
                warnings.append(f"Campos ignorados por no existir en el modelo: {unknown}")

            canvas = _detect_canvas(active)

            # --- 1. Planear que visuales habra (sin construir ni escribir) ---
            plan = [{"type": "card", "fields": {"values": [f"[{m}]"]},
                     "title": m, "of": m} for m in measures[:4]]
            if columns and measures:
                plan.append({
                    "type": "clusteredColumnChart",
                    "fields": {"category": columns[0],
                               "values": [f"[{measures[0]}]"]},
                    "title": f"{measures[0]} por {columns[0]}", "of": None})

            if not plan:
                warnings.append(
                    "No se crearon visuales: faltan medidas/columnas validas en los hints.")

            # --- 2. Posiciones FINALES antes de construir ---------------------
            # No se escribe con una posicion provisional para reposicionar luego.
            items = [{"visual_id": str(i), "type": p["type"]}
                     for i, p in enumerate(plan)]
            positions = layout_engine.compute_layout(items, layout, canvas, 16) if plan else []
            por_indice = {p["visual_id"]: p for p in positions}

            # --- 3. Construir TODO en memoria --------------------------------
            planificados = []
            for i, p in enumerate(plan):
                pos = por_indice[str(i)]
                built = visual_factory.build_visual(
                    active, p["type"], p["fields"],
                    {"x": pos["x"], "y": pos["y"],
                     "width": pos["width"], "height": pos["height"]},
                    p["title"], mi)
                planificados.append({
                    "visual": built["visual"],
                    "meta": {"type": built["actual_type"], "of": p["of"]}})
                warnings.extend(built["warnings"])

            # --- 4. Una sola transaccion: pagina + metadatos + visuales -------
            # Compatibilidad observable: si no hay visuales que construir, se
            # sigue creando la pagina (vacia) y devolviendo su page_id, como
            # hacia esta tool antes de la Fase 1A.1. Se avisa en `warnings`.
            res = pbir_writer.create_page_with_visuals(
                active, page_name, canvas["width"], canvas["height"],
                planificados, tool="pbi_generate_report_page")

            return {
                "page_id": res["page_id"],
                "page_name": page_name,
                "objective": objective,
                "layout": layout,
                "created_visuals": res["visuals_created"],
                "used_measures": measures,
                "used_columns": columns,
                "warnings": warnings,
                "create_missing_measures": create_missing_measures,
                "backup": res.get("backup"),
                "transaction": res.get("transaction"),
                "reload_hint": RELOAD_HINT,
            }
        return guard_mutation(_impl)
