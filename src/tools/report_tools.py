"""Tools de autoria de informes PBIR: CRUD de visuales y paginas, y layout.

Todas las mutantes respetan la politica estricta de Power BI Desktop, escriben
en una sola transaccion con journal y revierten si algo falla.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session
from pbip import pbir_reader, pbir_writer
from services import layout_doctor, pbir_edit
from tools._common import guard, guard_mutation


def _active():
    return get_session().require_active_pbip()


def _tema_activo():
    from config import get_session

    return get_session().require_active_pbip()


def register(mcp) -> None:

    @mcp.tool()
    def pbi_add_image_resource(path: str, name: Optional[str] = None,
                               overwrite: bool = False,
                               request_id: str = "") -> Dict[str, Any]:
        """Incrusta una imagen en el informe y la deja lista para usar.

        La copia a StaticResources y la declara en report.json: sin las dos
        cosas Power BI no la encuentra y el visual sale vacio sin dar ningun
        error. Devuelve `item_name`, que es lo que se le pasa al visual de tipo
        'image' en options.resource.

        `path`: archivo local (.png .jpg .gif .bmp .svg).
        """
        from pbip import resources

        return guard_mutation(lambda: resources.add_image(
            _tema_activo(), path, name=name, overwrite=overwrite))

    @mcp.tool()
    def pbi_list_report_resources() -> Dict[str, Any]:
        """Recursos del informe: declarados, en disco y los que no cuadran.

        Un archivo sin declarar no lo encuentra Power BI; una declaracion sin
        archivo deja el visual vacio. Los dos casos son invisibles al abrir el
        informe, asi que se listan aparte.
        """
        from pbip import resources

        return guard(lambda: resources.list_resources(_tema_activo()))

    @mcp.tool()
    def pbi_create_bookmark(display_name: str, page: str,
                            filters: Optional[List[Dict[str, Any]]] = None,
                            target_visuals: Optional[List[str]] = None,
                            suppress_data: bool = False,
                            suppress_display: bool = False,
                            name: Optional[str] = None,
                            overwrite: bool = False,
                            request_id: str = "") -> Dict[str, Any]:
        """Crea un marcador: un estado del informe al que volver con un boton.

        Escribe el archivo del marcador Y lo mete en el indice: sin el indice
        Power BI no lo muestra aunque el archivo exista.

        `page`: id o titulo de la pagina que activara. `filters`: filtros a
        guardar, con la misma forma que en un page spec. `target_visuals`
        limita a que visuales afecta. Devuelve `usage`, listo para pasarselo a
        un boton con action='bookmark'.
        """
        from pbip import bookmarks

        return guard_mutation(lambda: bookmarks.create_bookmark(
            _tema_activo(), display_name, page, filters=filters,
            target_visuals=target_visuals, suppress_data=suppress_data,
            suppress_display=suppress_display, name=name, overwrite=overwrite))

    @mcp.tool()
    def pbi_list_bookmarks() -> Dict[str, Any]:
        """Marcadores del informe, y los que no cuadran entre indice y disco.

        Un marcador que no esta en el indice no se muestra; una entrada del
        indice sin archivo rompe el panel. Los dos casos son mudos al abrir.
        """
        from pbip import bookmarks

        return guard(lambda: bookmarks.list_bookmarks(_tema_activo()))

    @mcp.tool()
    def pbi_delete_bookmark(name: str, confirm: bool = False,
                            request_id: str = "") -> Dict[str, Any]:
        """Borra un marcador y lo quita del indice. Destructiva: confirm=true."""
        from pbip import bookmarks
        from powerbi.errors import ValidationError

        def _impl():
            if not confirm:
                raise ValidationError(
                    "Borrar un marcador es destructivo. Repite con confirm=true.")
            return bookmarks.delete_bookmark(_tema_activo(), name)

        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_list_themes() -> Dict[str, Any]:
        """Temas de informe disponibles, con su paleta y para que sirve cada uno.

        Devuelve, por tema: el escenario de uso, el color de fondo, los colores
        de serie EN SU ORDEN (el orden es lo que garantiza que dos series
        contiguas se distingan tambien con daltonismo) y los colores de estado.

        Usalo para PROPONER un esquema antes de construir, en vez de decidirlo
        por el usuario.
        """
        from pbip import theme

        return guard(lambda: {"count": len(theme.PRESETS),
                              "themes": theme.list_presets(),
                              "note": ("Los colores de estado (good/neutral/bad) "
                                       "son iguales en los tres: el semaforo "
                                       "significa lo mismo en cualquier tema.")})

    @mcp.tool()
    def pbi_apply_theme(preset: str = "control_room",
                        name: Optional[str] = None,
                        data_colors: Optional[List[str]] = None,
                        request_id: Optional[str] = None) -> Dict[str, Any]:
        """Aplica un tema de colores al informe .pbip activo.

        Escribe el JSON del tema en StaticResources/RegisteredResources y lo
        declara en report.json (themeCollection + resourcePackages). Sin las
        tres cosas Power BI Desktop lo ignora en silencio.

        `preset`: ver pbi_list_themes. `data_colors`: sustituye la paleta de
        series por la tuya (#RRGGBB); ojo, entonces el orden deja de estar
        verificado contra daltonismo. `name`: nombre visible del tema.

        Requiere el proyecto CERRADO en Power BI Desktop.
        """
        from pbip import theme

        def _impl():
            activo = _tema_activo()
            construido = theme.build_theme(preset=preset, name=name,
                                           data_colors=data_colors)
            return theme.apply_theme(activo, construido)

        return guard_mutation(_impl)

    # ---------------------------------------------------------- inspeccion ---
    @mcp.tool()
    def pbi_get_visual(page: str, visual_id: str) -> Dict[str, Any]:
        """Definicion completa y normalizada de un visual.

        Devuelve tipo, posicion, orden Z, titulo, campos por rol, medidas y
        columnas referenciadas, si tiene formato propio, sus filtros, y la
        definicion cruda por si hace falta inspeccionarla.
        """
        return guard(lambda: pbir_edit.get_visual(_active(), page, visual_id))

    @mcp.tool()
    def pbi_report_capabilities() -> Dict[str, Any]:
        """Version PBIR observada, tema, custom visuals y tipos clonables.

        Solo se pueden crear visuales de tipos ya presentes en el informe: se
        clona una estructura real en vez de inventarla. Esta tool dice cuales
        hay disponibles antes de intentarlo.
        """
        return guard(lambda: pbir_edit.report_capabilities(_active()))

    # ------------------------------------------------------ CRUD de visuales ---
    @mcp.tool()
    def pbi_duplicate_visual(page: str, visual_id: str,
                             target_page: Optional[str] = None,
                             offset_x: float = 24, offset_y: float = 24,
                             new_title: Optional[str] = None, request_id: str = "") -> Dict[str, Any]:
        """Duplica un visual conservando campos, formato y filtros.

        Solo se regenera el identificador, que debe ser unico. La copia se
        desplaza `offset_x`/`offset_y` para que no quede tapando al original.
        `target_page` permite copiarlo a otra pagina.
        """
        return guard_mutation(lambda: pbir_edit.duplicate_visual(
            _active(), page, visual_id, target_page=target_page,
            offset=(offset_x, offset_y), new_title=new_title))

    @mcp.tool()
    def pbi_delete_visual(page: str, visual_id: str,
                          confirm: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Elimina un visual. Operacion destructiva: requiere confirm=true.

        Devuelve la definicion previa, y el journal permite restaurarla.
        """
        return guard_mutation(lambda: pbir_edit.delete_visual(
            _active(), page, visual_id, confirm))

    @mcp.tool()
    def pbi_set_visual_title(page: str, visual_id: str, title: str, request_id: str = "") -> Dict[str, Any]:
        """Cambia el titulo de un visual PRESERVANDO su formato (fuente, color)."""
        return guard_mutation(lambda: pbir_edit.set_visual_title(
            _active(), page, visual_id, title))

    @mcp.tool()
    def pbi_set_conditional_format(page: str, visual_id: str, field: str,
                                   min_color: str, max_color: str,
                                   target: str = "background",
                                   mid_color: Optional[str] = None,
                                   null_strategy: str = "asZero",
                                   request_id: str = "") -> Dict[str, Any]:
        """Colorea un visual segun el valor de un campo (degradado).

        Es lo que convierte una matriz de numeros en un mapa de calor, o unas
        barras planas en una escala de semaforo.

        `field`: 'Tabla[Campo]' o '[Medida]' de donde sale el valor.
        `target`: 'background' o 'font' para tablas y matrices; 'bars' para
        barras, columnas y puntos. `mid_color`: si lo indicas, el degradado
        tiene tres paradas en vez de dos, util cuando hay un punto neutro.
        `null_strategy`: asZero | none | specificColor.

        Si el visual ya tenia una regla en ese mismo destino, se sustituye:
        dos degradados sobre la misma propiedad no se suman, se pisan.
        """
        from tools.visual_tools import _measure_index, _model_data

        return guard_mutation(lambda: pbir_edit.set_conditional_format(
            _active(), page, visual_id, field, min_color, max_color,
            target=target, mid_color=mid_color, null_strategy=null_strategy,
            measure_index=_measure_index(_model_data())))

    @mcp.tool()
    def pbi_set_visual_z_order(page: str, order: List[str], request_id: str = "") -> Dict[str, Any]:
        """Fija el orden Z de los visuales de una pagina.

        `order`: ids de MENOR a MAYOR z; el ultimo queda encima. Los visuales
        que no menciones se colocan por encima, conservando su orden relativo.
        """
        return guard_mutation(lambda: pbir_edit.set_visual_z_order(_active(), page, order))

    @mcp.tool()
    def pbi_replace_visual_field(page: str, visual_id: str, old_ref: str,
                                 new_ref: str, request_id: str = "") -> Dict[str, Any]:
        """Sustituye una referencia de campo dentro de un visual.

        `old_ref`/`new_ref`: 'Tabla[Campo]' o '[Medida]'. Trabaja sobre las
        proyecciones existentes; no crea roles nuevos. Falla si el visual no
        referencia `old_ref`, en vez de no hacer nada en silencio.

        El destino se valida contra el modelo antes de escribir: si no existe,
        es ambiguo o es de otro tipo (medida donde va una columna), se rechaza
        con `field_not_found` en lugar de inventarlo.
        """
        from tools.visual_tools import _model_data

        return guard_mutation(lambda: pbir_edit.replace_visual_field(
            _active(), page, visual_id, old_ref, new_ref, _model_data()))

    @mcp.tool()
    def pbi_copy_visual_format(source_page: str, source_visual: str,
                               target_page: str,
                               target_visuals: List[str], request_id: str = "") -> Dict[str, Any]:
        """Copia el formato de un visual a otros DEL MISMO TIPO.

        Se copia el formato pero no el texto del titulo, que es contenido.
        Copiar entre tipos distintos se rechaza: la estructura de formato no es
        intercambiable y Power BI podria rechazar el informe.
        """
        return guard_mutation(lambda: pbir_edit.copy_visual_format(
            _active(), source_page, source_visual, target_page, target_visuals))

    # ------------------------------------------------------- CRUD de paginas ---
    @mcp.tool()
    def pbi_duplicate_page(page: str, new_name: str, request_id: str = "") -> Dict[str, Any]:
        """Duplica una pagina con todos sus visuales, en una sola transaccion.

        Se regeneran los identificadores que deben ser unicos (el de la pagina
        y el de cada visual) y se conserva todo lo demas.
        """
        return guard_mutation(lambda: pbir_edit.duplicate_page(_active(), page, new_name))

    @mcp.tool()
    def pbi_delete_page(page: str, confirm: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Elimina una pagina y actualiza el orden y la pagina activa.

        Destructiva: requiere confirm=true. Se niega a borrar la ultima pagina
        del informe, porque un informe sin paginas no abre.
        """
        return guard_mutation(lambda: pbir_edit.delete_page(_active(), page, confirm))

    @mcp.tool()
    def pbi_rename_page(page: str, new_name: str, request_id: str = "") -> Dict[str, Any]:
        """Cambia el nombre visible de una pagina. El id interno no cambia."""
        return guard_mutation(lambda: pbir_edit.rename_page(_active(), page, new_name))

    @mcp.tool()
    def pbi_reorder_pages(order: List[str], request_id: str = "") -> Dict[str, Any]:
        """Fija el orden de las paginas del informe.

        Acepta ids o nombres visibles. Las paginas que no menciones quedan al
        final, conservando su orden relativo.
        """
        return guard_mutation(lambda: pbir_edit.reorder_pages(_active(), order))

    # ------------------------------------------------------------- layout -----
    @mcp.tool()
    def pbi_detect_layout_issues(page: Optional[str] = None) -> Dict[str, Any]:
        """Diagnostica la geometria de una pagina (o de todas). Solo lectura.

        Detecta solapamientos, visuales fuera del lienzo, tamanos demasiado
        pequenos, margenes, separaciones inconsistentes, orden Z duplicado o
        ausente, paginas vacias y paginas saturadas. Cada hallazgo trae su
        evidencia geometrica.
        """
        def _impl():
            active = _active()
            paginas = ([p for p in pbir_reader.list_pages(active)]
                       if page is None else
                       [p for p in pbir_reader.list_pages(active)
                        if p["name"] == page or p.get("display_name") == page])
            if not paginas:
                from powerbi.errors import ValidationError

                raise ValidationError(f"No se encontro la pagina '{page}'.")
            informes, total = [], 0
            for p in paginas:
                visuales = pbir_reader.list_visuals(active, p["name"])
                r = layout_doctor.detect_issues(
                    visuales, {"width": p.get("width"), "height": p.get("height")})
                r["page"] = p["name"]
                r["display_name"] = p.get("display_name")
                total += r["issue_count"]
                informes.append(r)
            return {"pages": informes, "total_issues": total,
                    "clean": total == 0}
        return guard(_impl)

    @mcp.tool()
    def pbi_align_visuals(page: str, visual_ids: List[str],
                          edge: str = "left", request_id: str = "") -> Dict[str, Any]:
        """Alinea varios visuales por un borde.

        `edge`: left | right | top | bottom | center_h | center_v.
        Determinista: la misma entrada produce siempre la misma salida.
        """
        def _impl():
            active = _active()
            visuales = pbir_reader.list_visuals(active, page)
            p = next((x for x in pbir_reader.list_pages(active)
                      if x["name"] == page or x.get("display_name") == page), {})
            nuevas = layout_doctor.align(
                visuales, visual_ids, edge,
                {"width": p.get("width"), "height": p.get("height")})
            res = pbir_writer.update_visuals_bulk(active, page, nuevas,
                                                  tool="pbi_align_visuals")
            res["edge"] = edge
            return res
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_distribute_visuals(page: str, visual_ids: List[str],
                               axis: str = "horizontal", request_id: str = "") -> Dict[str, Any]:
        """Reparte visuales con separacion uniforme. `axis`: horizontal|vertical.

        Necesita al menos tres visuales: con dos, la separacion ya es la que hay.
        """
        def _impl():
            active = _active()
            visuales = pbir_reader.list_visuals(active, page)
            nuevas = layout_doctor.distribute(visuales, visual_ids, axis)
            res = pbir_writer.update_visuals_bulk(active, page, nuevas,
                                                  tool="pbi_distribute_visuals")
            res["axis"] = axis
            return res
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_normalize_page_layout(page: str,
                                  dry_run: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Corrige lo corregible de una pagina sin reacomodarla entera.

        Mete dentro del lienzo lo que se sale, sube al minimo lo demasiado
        pequeno y respeta los margenes. NO mueve lo que ya esta bien: es una
        correccion conservadora. `dry_run=true` devuelve el plan sin escribir.
        """
        def _impl():
            active = _active()
            visuales = pbir_reader.list_visuals(active, page)
            p = next((x for x in pbir_reader.list_pages(active)
                      if x["name"] == page or x.get("display_name") == page), {})
            canvas = {"width": p.get("width"), "height": p.get("height")}
            nuevas = layout_doctor.normalize(visuales, canvas)
            if dry_run:
                return {"planned": True, "page": page, "canvas": canvas,
                        "would_change": len(nuevas), "positions": nuevas,
                        "note": "Nada se ha escrito (dry_run)."}
            if not nuevas:
                return {"page": page, "moved": 0,
                        "note": "El layout ya cumple: nada que corregir."}
            res = pbir_writer.update_visuals_bulk(
                active, page, nuevas, tool="pbi_normalize_page_layout")
            res["canvas"] = canvas
            return res
        return guard_mutation(_impl)
