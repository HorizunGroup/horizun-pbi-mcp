"""Llamadas válidas, una por tool — CONTRACT-002 / G2.2.

El inventario medía que **77 tools** no tenían payload congelado por una sola
razón: el esquema rechaza `{}` y nadie les había escrito una llamada válida.
«Requiere argumentos» no es un impedimento externo, es trabajo, y este archivo
es ese trabajo.

## Cómo se ejecutan, y por qué así

**Cada llamada va sobre una copia FRESCA del proyecto sintético.** Muchas de
estas tools escriben, y si compartieran proyecto el resultado de una dependería
de cuáles se hubieran ejecutado antes: el golden pasaría a depender del orden
alfabético. Con una copia por llamada, el orden deja de existir como variable.

**La red y los procesos están PROHIBIDOS durante toda la pasada.** No es una
precaución decorativa: es el mecanismo que convierte «esta tool necesita
Desktop» de suposición en **demostración**. Si una tool intenta arrancar Power
BI o resolver un nombre, la prohibición salta, la tool queda **declarada con su
dependencia** y su payload no se congela. Ninguna suposición sobrevive a eso.

## Los valores

Referencian lo que el proyecto sintético tiene de verdad —la página `pg1`, la
tabla `Ventas`— para que la respuesta sea la real y no un error de «no existe».
Donde hace falta una ruta, `{tmp}` y `{pbip}` se sustituyen en el momento.

Cuatro tools reciben a propósito una ruta que **no existe** —las que abren o
cierran Desktop—: lo que se les exige es que la rechacen **antes** de arrancar
nada, y la prohibición de `subprocess` comprueba que lo hagan.
"""
from __future__ import annotations

from typing import Any, Dict

ARGUMENTOS = {
    # --- informe: paginas y visuales sobre el proyecto sintetico -------------
    "pbi_list_visuals": {"page": "pg1"},
    "pbi_get_visual": {"page": "pg1", "visual_id": "v1"},
    "pbi_delete_visual": {"page": "pg1", "visual_id": "v1"},
    "pbi_duplicate_visual": {"page": "pg1", "visual_id": "v1"},
    "pbi_set_visual_title": {"page": "pg1", "visual_id": "v1", "title": "T"},
    "pbi_set_visual_filter": {"page": "pg1", "visual_id": "v1", "filters": []},
    "pbi_set_visual_z_order": {"page": "pg1", "order": ["v1"]},
    "pbi_set_color_from_field": {"page": "pg1", "visual_id": "v1", "field": "Ventas[Importe]"},
    "pbi_set_conditional_format": {"page": "pg1", "visual_id": "v1",
                                   "field": "Ventas[Importe]",
                                   "min_color": "#FFFFFF", "max_color": "#000000"},
    "pbi_replace_visual_field": {"page": "pg1", "visual_id": "v1",
                                 "old_ref": "Ventas[Importe]", "new_ref": "Ventas[Unidades]"},
    "pbi_update_visual_position": {"page": "pg1", "visual_id": "v1",
                                   "x": 0, "y": 0, "width": 100, "height": 100},
    "pbi_align_visuals": {"page": "pg1", "visual_ids": ["v1", "v2"]},
    "pbi_distribute_visuals": {"page": "pg1", "visual_ids": ["v1", "v2"]},
    "pbi_copy_visual_format": {"source_page": "pg1", "source_visual": "v1",
                               "target_page": "pg1", "target_visuals": ["v2"]},
    "pbi_create_visual": {"page": "pg1", "visual_type": "card",
                          "fields": {"Values": ["Ventas[Importe]"]},
                          "position": {"x": 0, "y": 0, "width": 200, "height": 100}},
    "pbi_create_html_visual": {"page": "pg1", "html_measure": "Ventas[HTML]",
                               "position": {"x": 0, "y": 0, "width": 200, "height": 100}},
    "pbi_arrange_visuals": {"page": "pg1"},
    "pbi_normalize_page_layout": {"page": "pg1"},
    "pbi_validate_generated_page": {"page": "pg1"},
    "pbi_export_page_html": {"page": "pg1"},
    "pbi_generate_page_spec": {"page_name": "pg1"},
    "pbi_generate_report_page": {"page_name": "Nueva"},
    "pbi_rename_page": {"page": "pg1", "new_name": "Renombrada"},
    "pbi_duplicate_page": {"page": "pg1", "new_name": "Copia"},
    "pbi_delete_page": {"page": "pg1"},
    "pbi_reorder_pages": {"order": ["pg1"]},
    "pbi_create_bookmark": {"display_name": "Marcador", "page": "pg1"},
    "pbi_delete_bookmark": {"name": "Marcador"},
    "pbi_export_report_content": {"select": {"pages": ["pg1"]}},
    "pbi_reflow_pages": {"system": "sala"},
    "pbi_compose_page": {"system": "sala", "title": "Titulo"},
    "pbi_apply_design_system": {"system": "sala"},

    # --- specs de pagina ----------------------------------------------------
    "pbi_apply_page_spec": {"spec": {"name": "pg1", "displayName": "P1", "visuals": []}},
    "pbi_create_page_from_spec": {"spec": {"name": "pg9", "displayName": "P9", "visuals": []}},
    "pbi_diff_page_spec": {"spec": {"name": "pg1", "displayName": "P1", "visuals": []}},
    "pbi_preview_page_spec": {"spec": {"name": "pg1", "displayName": "P1", "visuals": []}},
    "pbi_preview_spec_html": {"spec": {"name": "pg1", "displayName": "P1", "visuals": []}},
    "pbi_validate_page_spec": {"spec": {"name": "pg1", "displayName": "P1", "visuals": []}},

    # --- modelo -------------------------------------------------------------
    "pbi_create_measure": {"table": "Ventas", "name": "M1", "expression": "SUM(Ventas[Importe])"},
    "pbi_update_measure": {"table": "Ventas", "name": "Total", "expression": "1"},
    "pbi_delete_measure": {"table": "Ventas", "name": "Total"},
    "pbi_rename_measure": {"table": "Ventas", "old_name": "Total", "new_name": "TotalNuevo"},
    "pbi_measure_dependencies": {"name": "Total"},
    "pbi_column_dependencies": {"table": "Ventas", "column": "Importe"},
    "pbi_create_calculated_column": {"table": "Ventas", "name": "C1", "expression": "1"},
    "pbi_create_calculated_table": {"name": "T1", "expression": "ROW(\"a\",1)"},
    "pbi_create_hierarchy": {"table": "Ventas", "name": "H1", "levels": ["Importe"]},
    "pbi_create_relationship": {"from_table": "Ventas", "from_column": "Importe",
                                "to_table": "Ventas", "to_column": "Unidades"},
    # `mode` viene en `live`, y en ese camino la respuesta depende de si el CLR
    # se cargo antes en el proceso: `tom_not_installed` o `no_active_model`
    # segun la prueba que se hubiera ejecutado. Con `pbip` la respuesta sale del
    # disco y es la misma siempre. Es la misma leccion que `pbi_hide_columns`.
    "pbi_set_relationship_direction": {"from_table": "Ventas", "to_table": "Ventas",
                                       "mode": "pbip"},
    "pbi_set_column_visibility": {"table": "Ventas", "column": "Importe", "hidden": True},
    "pbi_set_storage_mode": {"table": "Ventas", "mode": "import"},
    # `columns` es una lista de OBJETOS, no de referencias en texto, y `mode`
    # viene en `live` por defecto -que exige un modelo servido-. Las dos cosas
    # se descubrieron llamandola: es la diferencia entre escribir la llamada y
    # suponerla.
    "pbi_hide_columns": {"columns": [{"table": "Ventas", "column": "Importe"}],
                         "mode": "pbip"},
    "pbi_search_model": {"term": "Ventas"},
    "pbi_get_object": {"kind": "table", "name": "Ventas"},
    "pbi_validate_measures": {"measures": [{"name": "M", "dax": "1"}]},
    "pbi_run_dax": {"query": "EVALUATE ROW(\"a\", 1)"},

    # --- planificacion, auditoria y contratos -------------------------------
    "pbi_plan_change": {"operation": "create_measure",
                        "arguments": {"table": "Ventas", "name": "M",
                                      "expression": "1"}},
    "pbi_apply_plan": {"plan_token": "token-que-no-existe"},
    "pbi_plan_audit_fixes": {"rules": ["auto_date_time"]},
    "pbi_apply_audit_fixes": {"actions": []},
    "pbi_define_brief": {"purpose": "Control", "audience": "Direccion"},
    "pbi_define_port_contract": {"datasets": [{"name": "Ventas", "grain": "dia"}]},
    "pbi_build_dashboard": {"name": "D", "measures": ["Ventas[Importe]"]},
    "pbi_build_evm_page": {"measures": ["Ventas[Importe]"]},
    "pbi_build_executive_page": {"measures": ["Ventas[Importe]"]},

    # --- journals y respaldos ----------------------------------------------
    "pbi_inspect_journal": {"journal": "no-existe.json"},
    "pbi_recover_from_journal": {"journal": "no-existe.json"},
}

# Los que necesitan una ruta: `{tmp}` se sustituye por el temporal de la pasada
# y `{pbip}` por el proyecto sintetico.
ARGUMENTOS.update({
    "pbi_open_pbip_project": {"path": "{pbip}"},
    # Desde CONTRACT-003 es `session_write`, asi que el escenario
    # `con-proyecto` -que solo ejecuta lecturas- ya no la cubre. Un puerto que
    # no sirve nada da un error de dominio estable y sin salir a ningun lado.
    "pbi_select_model": {"port": 65000},
    "pbi_create_pbip_project": {"out_dir": "{tmp}/nuevo", "name": "Nuevo"},
    "pbi_add_image_resource": {"path": "{tmp}/recursos/logo.png"},
    "pbi_add_table_from_file": {"path": "{tmp}/recursos/datos.csv"},
    "pbi_add_table_from_source": {"source": "{tmp}/recursos/datos.csv",
                                  "table_name": "Nueva",
                                  "columns": [{"name": "a", "dataType": "string"}]},
    "pbi_list_convertible_pbix": {"path": "{tmp}/recursos"},
    "pbi_inspect_pbix": {"path": "{tmp}/recursos/no-existe.pbix"},
    "pbi_convert_pbix_to_pbip": {"path": "{tmp}/recursos/no-existe.pbix",
                                 "out_dir": "{tmp}/convertido"},
    # Sin `confirm`: el modo seco. Los respaldos viven en el tmp de la pasada.
    "pbi_purge_backups": {},
    # Sin modelo activo contesta al instante y no abre ninguna conexion.
    "pbi_refresh_model": {},
    # Rutas que NO existen: la tool tiene que rechazarlas ANTES de arrancar
    # nada. Si en vez de eso lanza Desktop, la prohibicion de `subprocess` lo
    # delata y la tool queda declarada, no congelada.
    "pbi_open_and_refresh": {"path": "{tmp}/recursos/no-existe.pbip"},
    "pbi_close_desktop": {"path": "{tmp}/recursos/no-existe.pbip"},
    "pbi_open_in_desktop": {"path": "{tmp}/recursos/no-existe.pbip"},
    "pbi_validate_desktop_render": {"path": "{tmp}/recursos/no-existe.pbip"},
    # URL sintacticamente invalida: se rechaza antes de resolver nada. Si aun
    # asi saliera a la red, la prohibicion de `socket` lo delata.
    "pbi_sharepoint_list_folder": {"site_url": "no-es-una-url"},
    "pbi_sharepoint_download_folder": {"site_url": "no-es-una-url"},
})
