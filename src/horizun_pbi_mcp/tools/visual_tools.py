"""Tools de informe PBIR: leer, documentar, crear y acomodar visuales (Fases 7-10)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session, get_settings
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi import model_reader
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip import layout_engine, pbir_reader, pbir_writer, tmdl_reader, visual_factory
from horizun_pbi_mcp.reporting import document_layout_markdown
from horizun_pbi_mcp.tools._common import guard, guard_mutation
from horizun_pbi_mcp.utils.file_utils import atomic_write_text, timestamp
from horizun_pbi_mcp.utils.validation import validate_position

log = get_logger("visual_tools")

RELOAD_HINT = (
    "Para ver los cambios: cierra y reabre el informe en Power BI Desktop. "
    "Si Desktop esta abierto y guardas (Ctrl+S), sobrescribiras estos cambios en disco; "
    "por eso conviene editar el PBIR con Desktop cerrado."
)


#: Aviso que se emite cuando los campos se validaron contra el modelo EN VIVO
#: en vez del TMDL. Se acumula aqui y las tools lo vacian en su respuesta.
_AVISO_FUENTE_VIVA = (
    "Los campos se validaron contra el modelo EN VIVO (este proyecto no tiene "
    "TMDL). Una medida creada con mode='live' y no guardada pasa esta "
    "comprobacion y desaparece al cerrar Power BI Desktop: la pagina queda en "
    "disco referenciando algo que ya no existe. Guarda con Ctrl+S antes de "
    "cerrar."
)
_AVISOS_DE_FUENTE: List[str] = []


def drenar_avisos_de_fuente() -> List[str]:
    """Devuelve y limpia los avisos sobre la fuente del modelo."""
    avisos = list(dict.fromkeys(_AVISOS_DE_FUENTE))
    _AVISOS_DE_FUENTE.clear()
    return avisos


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
            # RESPALDO, y conviene saber que se esta usando: aqui las
            # referencias se validan contra un modelo EN MEMORIA. Una medida
            # creada con mode='live' y no guardada existe para esta
            # comprobacion y desaparece al cerrar Desktop, dejando la pagina
            # -que si quedo en disco- con "Hubo un problema con uno o mas
            # campos". Paso de verdad, con cinco medidas y cuatro tarjetas.
            # No se puede impedir desde aqui, pero callarlo es peor.
            log.warning(
                "Sin TMDL: los campos se validan contra el modelo EN VIVO. Lo "
                "que no este guardado en Desktop no sobrevivira, y las paginas "
                "que lo referencien quedaran rotas.")
            _AVISOS_DE_FUENTE.append(_AVISO_FUENTE_VIVA)
            return model_reader.read_model(session)
    except Exception as exc:  # noqa: BLE001
        # Si hay una fuente autoritativa pero esta rota, None significaria
        # falsamente "no hay modelo para comprobar" y los escritores seguirian
        # con referencias inventadas. Se falla cerrado con la causa original.
        raise ValidationError(
            "No se pudo leer el modelo autoritativo para validar los campos; "
            "no se genera ningun visual.",
            details={"cause": f"{type(exc).__name__}: {exc}"}) from exc
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
    pages = pbir_reader.list_pages(active)
    widths = [p.get("width") for p in pages if p.get("width")]
    heights = [p.get("height") for p in pages if p.get("height")]
    if widths and heights:
        return {"width": max(widths), "height": max(heights)}
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
            visuals = pbir_reader.list_visuals(active, page, strict=True)
            return {"page": page, "count": len(visuals), "visuals": visuals}
        return guard(_impl)

    @mcp.tool()
    def pbi_document_report_layout() -> Dict[str, Any]:
        """Genera documentacion Markdown del layout del informe (paginas y visuales)."""
        def _impl():
            active = get_session().require_active_pbip()
            pages = pbir_reader.list_pages(active)
            for p in pages:
                p["visuals"] = pbir_reader.list_visuals(
                    active, p["name"], strict=True)
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
        options: Optional[Dict[str, Any]] = None,
    request_id: str = "") -> Dict[str, Any]:
        """Crea un visual PBIR en una pagina.

        `visual_type` acepta: actionButton, areaChart, barChart, button, card,
        cardVisual, clusteredBarChart, clusteredColumnChart, columnChart, donut,
        donutChart, funnel, gauge, htmlContent,
        htmlContent443BE3AD55E043BF878BED274D3A6855, image, kpi, lineChart,
        matrix, multiRowCard, navigation, pageNavigator, pieChart, pivotTable,
        rectangle, ribbonChart, scatterChart, shape, slicer, table, tableEx,
        text, textbox, treemap, waterfall y waterfallChart.

        Para visuales con datos, valida antes de escribir los roles obligatorios,
        la cardinalidad maxima de cada rol y el tipo de campo admitido: dimension
        (`Grouping`), medida (`Measure`) o cualquiera de ambos
        (`GroupingOrMeasure`). Los elementos decorativos (texto, forma, imagen,
        navegador y boton) rechazan `fields` porque no llevan consulta.

        `fields`: rol -> campos, p.ej. {"category":"Tabla[Col]",
                  "values":["[Medida]"], "legend":"Tabla[Col]"}.

        El rol se reconoce escrito como sea: el logico (`values`), el nombre
        que usa PBIR y que devuelve `pbi_list_visuals` (`Values`, `Y`,
        `Category`, `Data`) o un sinonimo (`measure`, `axis`). Cada campo puede
        ser `"Tabla[Campo]"` o el objeto que devuelve `pbi_list_visuals`, para
        poder leer una pagina y rehacerla sin traducir nada.

        Un rol que ese tipo de visual no tiene se RECHAZA con la lista de los
        validos. Antes se descartaba en silencio y el visual salia sin datos.

        `options`: formato adicional. Llaves reconocidas (una clave no
        reconocida se ignora, no se rechaza; ver `pbi_get_visual` para
        comprobar que quedo escrito):
        - `background_color`, `border_color` ('#RRGGBB'), `background_transparency`
          (0-100), `border_radius` (px): el marco de CUALQUIER visual (panel
          General > Efectos de Desktop). Sin `background_color`/`border_color`
          no se toca el marco.
        - `card`/`cardVisual`: `show_category_label`, `value_font_size`,
          `bold_value`, `value_color`.
        - `shape`: `fill`, `transparency`, `text`, `font_size`, `text_color`.
        - `textbox`: `text`, `font_size`, `color`, `bold`, `font`, `align`.
          El texto va en `options.text`, NO en `fields` ni en la raiz:
          {"type": "textbox", "options": {"text": "Resumen", "font_size": 20}}.
        - `image`: `resource` (ItemName ya registrado con
          `pbi_add_image_resource`), `name`, `scaling`.
        - `pageNavigator`: `show_hidden`, `show_current`.
        - `actionButton`: `action`, `icon`, `target_page`, `text`.
        - `format`: formato del VISUAL, no del contenedor. `mode`
          (segmentador: Dropdown, List, Between...), `header` (bool: el
          encabezado del campo del segmentador; false lo oculta), `dataLabels`
          (bool), `legend` (bool), `legendPosition` (Top, Bottom, Left,
          Right...). Una clave desconocida aqui SE RECHAZA con la lista de las
          validas: un formato que se pide y no se aplica deja el informe
          distinto de lo que se penso sin decir por que.

        `position`: {x, y, width, height} (z opcional).
        Clona un visual del mismo tipo como plantilla si existe. Hace backup.
        """
        def _impl():
            active = get_session().require_active_pbip()
            pos = validate_position(position)
            mi = _measure_index(_model_data())
            built = visual_factory.build_visual(
                active, visual_type, fields or {}, pos, title, mi, options=options)
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
    def pbi_set_visual_filter(
        page: str,
        visual_id: str,
        filters: List[Dict[str, Any]],
        merge: bool = False,
    request_id: str = "") -> Dict[str, Any]:
        """Filtra un visual EXISTENTE sin escribir `filterConfig` a mano.

        `filters`: lista de specs. Por COLUMNA,
        `{field: 'Tabla[Columna]', values?: [...], type?: 'Categorical' |
        'Advanced' | 'TopN' | 'Range' | 'RelativeDate' | 'Passthrough',
        exclude?: bool, raw?: {...}, hidden?: bool, locked?: bool,
        display_name?: str}`. Con `values` se arma un filtro de lista
        (Categorical); sin `values` ni `raw` el campo queda declarado pero
        SIN acotar, como el panel de filtros vacio. `raw` pasa una consulta
        semantica ya construida, para lo que este constructor no cubre.

        Por MEDIDA, `{measure: 'Tabla[Medida]', condition: 'GreaterThan',
        value: 0}` (`condition`: Equal | NotEqual | GreaterThan |
        GreaterThanOrEqual | LessThan | LessThanOrEqual, o los simbolos
        `> >= < <= = !=`). Es la forma de ENCADENAR slicers de dimension
        cuando varias tablas de hechos cuelgan de las mismas dimensiones y
        una relacion bidireccional crearia ambiguedad.

        `field`/`measure` van con el NOMBRE real de la tabla. La mitad interna
        de la consulta usa un ALIAS (`SourceRef.Source`) que esta tool
        resuelve sola: escribirlo a mano ahi es el error mas comun al
        construir un filtro de visual, y Power BI lo ignora sin decir por que.

        Por defecto REEMPLAZA los filtros del visual, y una lista vacia los
        quita todos. **Cuidado con los slicers**: suelen traer un filtro
        `Categorical` donde vive la seleccion del usuario, y reemplazarlo la
        borra. Para anadir sin pisar, `merge=true` (ahi una lista vacia no
        quita nada). No comprueba el campo contra el modelo: uno que no existe
        se escribe igual y Power BI lo resuelve en silencio a nada.
        """
        def _impl():
            active = get_session().require_active_pbip()
            res = pbir_writer.update_visual_filters(
                active, page, visual_id, filters or [], merge=merge)
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
            visuals = pbir_reader.list_visuals(active, page, strict=True)
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
