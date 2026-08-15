# Inventario de tools — TEST-002

**Este documento no se escribe: se calcula.** Lo genera
`python -m tests.inventario_tools` a partir del servidor MCP real, y
`tests/test_inventario_tools.py` falla si el archivo deja de coincidir con lo
que el servidor declara hoy. Un inventario escrito a mano envejece en la primera
edicion que alguien haga de una tool, y nadie se entera porque el documento
sigue teniendo aspecto de completo.

Cierra G2.3 («inventario tool por tool publicado») y G2.4 («toda tool tiene al
menos un caso negativo; las excepciones se declaran con motivo»).

## Como leer la columna «caso negativo»

| Valor | Que se hace | Que se exige |
|---|---|---|
| falta un requerido | llamarla por MCP con `{}` | la validacion la rechaza antes de ejecutar nada |
| tipo invalido | un valor de otro tipo en el parametro que se indica | lo mismo: rechazo en la validacion |
| sin proyecto activo | ejecutarla de verdad, sin nada abierto | responde un sobre `ok: false` con codigo, nunca una excepcion |
| sin modo de fallo | ejecutarla de verdad, sin nada abierto | responde `ok: true`: no hay entrada ni estado que la haga fallar |
| adaptador roto | se le rompe el adaptador del entorno que consulta | responde un sobre con codigo, **nunca una traza** |
| **declarada** | no se ejecuta | el motivo va en la tabla de abajo, que es lo que G2.4 exige |

Las cinco primeras **se ejecutan por MCP** (`call_tool`) cada vez que corre la
suite: la columna no es una promesa, es lo que acaba de pasar.

«Sin modo de fallo» no es un aprobado gratis. Son tools que contestan lo mismo
el primer dia que el ultimo —capacidades, reglas, temas—, y **la exencion se
comprueba**: si alguna empieza a depender del proyecto abierto dejara de
contestar `ok: true` y la prueba lo dira. Una exencion que nadie vuelve a mirar
es un agujero con nombre bonito.

## Lo que este inventario NO demuestra

Conviene decirlo aqui, y no en una nota al pie, porque es lo que alguien podria
dar por hecho al ver 134 filas en verde:

* **No prueba que las tools hagan bien su trabajo.** Prueba que rechazan lo que
  no deben aceptar y que, cuando no pueden trabajar, lo dicen con un codigo. Lo
  otro son las pruebas de cada dominio, que van por su cuenta.
* **Los casos negativos son de entrada y de estado, no de semantica.** «Un DAX
  con la sintaxis rota» o «un tema con un color imposible» no salen de un
  esquema: hay que escribirlos a mano, tool por tool, y varios ya viven en los
  archivos de su dominio.
* **La columna de payload congelado la llena CONTRACT-002**, no esto, y hoy
  son dos tools de 134. Que el resto «necesita Desktop» es una hipotesis que
  esta sin comprobar tool por tool.


## Cuentas

| | |
|---|---|
| Tools | **134** |
| Ejecutadas por MCP en cada corrida | **134** |
| Con caso negativo que las hace fallar | **126** |
| Sin modo de fallo (ejecutadas, se exige `ok: true`) | **8** |
| Excepciones declaradas con motivo | **0** |
| De solo lectura | **55** |
| Con `confirm` | **9** |
| Con payload congelado | **2** |

## Las tools, una por una

| Tool | Riesgo | Params | Req. | `confirm` | Payload congelado | Caso negativo | Campo |
|---|---|---|---|---|---|---|---|
| `pbi_add_custom_visual` | escritura | 2 | 0 | — | — | tipo invalido | `visual_id` |
| `pbi_add_image_resource` | escritura | 4 | 1 | — | — | falta un requerido | `path` |
| `pbi_add_table_from_file` | escritura | 10 | 1 | — | — | falta un requerido | `path` |
| `pbi_add_table_from_source` | escritura | 14 | 3 | — | — | falta un requerido | `columns, source, table_name` |
| `pbi_align_visuals` | escritura | 4 | 2 | — | — | falta un requerido | `page, visual_ids` |
| `pbi_analyze_model_quality` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_apply_audit_fixes` | destructiva | 3 | 1 | sí | — | falta un requerido | `actions` |
| `pbi_apply_design_system` | escritura | 2 | 1 | — | — | falta un requerido | `system` |
| `pbi_apply_page_spec` | escritura | 6 | 1 | — | — | falta un requerido | `spec` |
| `pbi_apply_plan` | destructiva | 4 | 1 | sí | — | falta un requerido | `plan_token` |
| `pbi_apply_theme` | escritura | 7 | 0 | — | — | tipo invalido | `preset` |
| `pbi_arrange_visuals` | escritura | 7 | 1 | — | — | falta un requerido | `page` |
| `pbi_audit_model` | solo lectura | 3 | 0 | — | — | tipo invalido | `source` |
| `pbi_audit_project` | escritura | 3 | 0 | — | — | tipo invalido | `rules` |
| `pbi_audit_report_only` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_backup_pbip_project` | escritura | 3 | 0 | — | — | tipo invalido | `mode` |
| `pbi_build_dashboard` | escritura | 7 | 2 | — | — | falta un requerido | `measures, name` |
| `pbi_build_evm_page` | escritura | 6 | 1 | — | — | falta un requerido | `measures` |
| `pbi_build_executive_page` | escritura | 6 | 1 | — | — | falta un requerido | `measures` |
| `pbi_capabilities` | solo lectura | 0 | 0 | — | sí | sin modo de fallo | — |
| `pbi_check_contract` | solo lectura | 3 | 0 | — | — | tipo invalido | `source_path` |
| `pbi_close_desktop` | destructiva | 4 | 0 | sí | — | tipo invalido | `path` |
| `pbi_column_dependencies` | solo lectura | 3 | 2 | — | — | falta un requerido | `column, table` |
| `pbi_compare_live_to_pbip` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_compose_page` | escritura | 10 | 2 | — | — | falta un requerido | `system, title` |
| `pbi_convert_pbix_to_pbip` | escritura | 10 | 2 | — | — | falta un requerido | `out_dir, path` |
| `pbi_copy_visual_format` | escritura | 5 | 4 | — | — | falta un requerido | `source_page, source_visual, target_page, target_visuals` |
| `pbi_create_bookmark` | escritura | 9 | 2 | — | — | falta un requerido | `display_name, page` |
| `pbi_create_calculated_column` | escritura | 11 | 3 | — | — | falta un requerido | `expression, name, table` |
| `pbi_create_calculated_table` | escritura | 6 | 2 | — | — | falta un requerido | `expression, name` |
| `pbi_create_hierarchy` | escritura | 7 | 3 | — | — | falta un requerido | `levels, name, table` |
| `pbi_create_html_visual` | escritura | 5 | 3 | — | — | falta un requerido | `html_measure, page, position` |
| `pbi_create_measure` | escritura | 10 | 3 | — | — | falta un requerido | `expression, name, table` |
| `pbi_create_page_from_spec` | escritura | 2 | 1 | — | — | falta un requerido | `spec` |
| `pbi_create_pbip_project` | escritura | 9 | 2 | — | — | falta un requerido | `name, out_dir` |
| `pbi_create_relationship` | escritura | 11 | 4 | — | — | falta un requerido | `from_column, from_table, to_column, to_table` |
| `pbi_create_visual` | escritura | 7 | 4 | — | — | falta un requerido | `fields, page, position, visual_type` |
| `pbi_define_brief` | escritura | 9 | 2 | — | — | falta un requerido | `audience, purpose` |
| `pbi_define_port_contract` | escritura | 3 | 1 | — | — | falta un requerido | `datasets` |
| `pbi_delete_bookmark` | destructiva | 3 | 1 | sí | — | falta un requerido | `name` |
| `pbi_delete_measure` | destructiva | 5 | 2 | sí | — | falta un requerido | `name, table` |
| `pbi_delete_page` | destructiva | 3 | 1 | sí | — | falta un requerido | `page` |
| `pbi_delete_visual` | destructiva | 4 | 2 | sí | — | falta un requerido | `page, visual_id` |
| `pbi_detect_layout_issues` | solo lectura | 1 | 0 | — | — | tipo invalido | `page` |
| `pbi_diagnose_data` | solo lectura | 2 | 0 | — | — | tipo invalido | `tables` |
| `pbi_diff_page_spec` | solo lectura | 2 | 1 | — | — | falta un requerido | `spec` |
| `pbi_disable_auto_date_time` | escritura | 2 | 0 | — | — | tipo invalido | `enabled` |
| `pbi_distribute_visuals` | escritura | 4 | 2 | — | — | falta un requerido | `page, visual_ids` |
| `pbi_document_model` | escritura | 2 | 0 | — | — | tipo invalido | `source` |
| `pbi_document_report_layout` | escritura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_duplicate_page` | escritura | 3 | 2 | — | — | falta un requerido | `new_name, page` |
| `pbi_duplicate_visual` | escritura | 7 | 2 | — | — | falta un requerido | `page, visual_id` |
| `pbi_export_excel` | escritura | 6 | 0 | — | — | tipo invalido | `source` |
| `pbi_export_page_html` | escritura | 1 | 1 | — | — | falta un requerido | `page` |
| `pbi_export_report_content` | escritura | 8 | 1 | — | — | falta un requerido | `select` |
| `pbi_generate_page_spec` | solo lectura | 6 | 1 | — | — | falta un requerido | `page_name` |
| `pbi_generate_pdf_report` | escritura | 7 | 0 | — | — | tipo invalido | `report_type` |
| `pbi_generate_report_page` | escritura | 6 | 1 | — | — | falta un requerido | `page_name` |
| `pbi_generate_technical_documentation` | escritura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_get_brief` | solo lectura | 1 | 0 | — | — | tipo invalido | `request_id` |
| `pbi_get_object` | solo lectura | 3 | 2 | — | — | falta un requerido | `kind, name` |
| `pbi_get_visual` | solo lectura | 2 | 2 | — | — | falta un requerido | `page, visual_id` |
| `pbi_health_check` | solo lectura | 0 | 0 | — | sí | sin modo de fallo | — |
| `pbi_hide_columns` | escritura | 4 | 1 | — | — | falta un requerido | `columns` |
| `pbi_inspect_journal` | solo lectura | 1 | 1 | — | — | falta un requerido | `journal` |
| `pbi_inspect_pbix` | solo lectura | 1 | 1 | — | — | falta un requerido | `path` |
| `pbi_list_audit_rules` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_list_autofix_rules` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_list_bookmarks` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_list_convertible_pbix` | solo lectura | 2 | 1 | — | — | falta un requerido | `path` |
| `pbi_list_design_systems` | solo lectura | 1 | 0 | — | — | tipo invalido | `request_id` |
| `pbi_list_desktop_models` | solo lectura | 0 | 0 | — | — | adaptador roto | — |
| `pbi_list_hierarchies` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_measures` | solo lectura | 3 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_page_presets` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_list_partitions` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_pending_journals` | solo lectura | 1 | 0 | — | — | tipo invalido | `only_pending` |
| `pbi_list_perspectives` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_relationships` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_report_pages` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_list_report_resources` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_list_roles` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_tables` | solo lectura | 3 | 0 | — | — | tipo invalido | `source` |
| `pbi_list_themes` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_list_visuals` | solo lectura | 1 | 1 | — | — | falta un requerido | `page` |
| `pbi_measure_dependencies` | solo lectura | 3 | 1 | — | — | falta un requerido | `name` |
| `pbi_model_summary` | solo lectura | 1 | 0 | — | — | tipo invalido | `source` |
| `pbi_normalize_page_layout` | escritura | 3 | 1 | — | — | falta un requerido | `page` |
| `pbi_normalize_report` | escritura | 2 | 0 | — | — | tipo invalido | `dry_run` |
| `pbi_open_and_refresh` | destructiva | 8 | 0 | — | — | tipo invalido | `path` |
| `pbi_open_in_desktop` | escritura | 5 | 0 | — | — | tipo invalido | `path` |
| `pbi_open_pbip_project` | solo lectura | 1 | 1 | — | — | falta un requerido | `path` |
| `pbi_page_building_blocks` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_plan_audit_fixes` | solo lectura | 2 | 1 | — | — | falta un requerido | `rules` |
| `pbi_plan_change` | solo lectura | 2 | 2 | — | — | falta un requerido | `arguments, operation` |
| `pbi_prepare_delivery` | escritura | 2 | 0 | — | — | tipo invalido | `dry_run` |
| `pbi_preview_page_spec` | escritura | 2 | 1 | — | — | falta un requerido | `spec` |
| `pbi_preview_spec_html` | escritura | 1 | 1 | — | — | falta un requerido | `spec` |
| `pbi_profile_data` | escritura | 2 | 0 | — | — | tipo invalido | `tables` |
| `pbi_propose_dashboard` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_purge_backups` | destructiva | 4 | 0 | sí | — | tipo invalido | `days` |
| `pbi_recover_from_journal` | destructiva | 4 | 1 | sí | — | falta un requerido | `journal` |
| `pbi_reflow_pages` | escritura | 4 | 1 | — | — | falta un requerido | `system` |
| `pbi_refresh_model` | destructiva | 4 | 0 | — | — | tipo invalido | `type` |
| `pbi_rename_measure` | escritura | 5 | 3 | — | — | falta un requerido | `new_name, old_name, table` |
| `pbi_rename_page` | escritura | 3 | 2 | — | — | falta un requerido | `new_name, page` |
| `pbi_reorder_pages` | escritura | 2 | 1 | — | — | falta un requerido | `order` |
| `pbi_repair_broken_references` | escritura | 3 | 0 | — | — | tipo invalido | `mapping` |
| `pbi_replace_visual_field` | escritura | 5 | 4 | — | — | falta un requerido | `new_ref, old_ref, page, visual_id` |
| `pbi_report_capabilities` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_run_dax` | escritura | 5 | 1 | — | — | falta un requerido | `query` |
| `pbi_search_model` | solo lectura | 4 | 1 | — | — | falta un requerido | `term` |
| `pbi_select_model` | solo lectura | 3 | 0 | — | — | tipo invalido | `port` |
| `pbi_session_info` | solo lectura | 0 | 0 | — | — | sin modo de fallo | — |
| `pbi_set_color_from_field` | escritura | 6 | 3 | — | — | falta un requerido | `field, page, visual_id` |
| `pbi_set_column_visibility` | escritura | 5 | 2 | — | — | falta un requerido | `column, table` |
| `pbi_set_conditional_format` | escritura | 13 | 5 | — | — | falta un requerido | `field, max_color, min_color, page, visual_id` |
| `pbi_set_relationship_direction` | escritura | 5 | 2 | — | — | falta un requerido | `from_table, to_table` |
| `pbi_set_storage_mode` | escritura | 3 | 2 | — | — | falta un requerido | `mode, table` |
| `pbi_set_visual_filter` | escritura | 5 | 3 | — | — | falta un requerido | `filters, page, visual_id` |
| `pbi_set_visual_title` | escritura | 5 | 2 | — | — | falta un requerido | `page, visual_id` |
| `pbi_set_visual_z_order` | escritura | 3 | 2 | — | — | falta un requerido | `order, page` |
| `pbi_sharepoint_download_folder` | escritura | 7 | 1 | — | — | falta un requerido | `site_url` |
| `pbi_sharepoint_list_folder` | solo lectura | 5 | 1 | — | — | falta un requerido | `site_url` |
| `pbi_start_here` | solo lectura | 1 | 0 | — | — | tipo invalido | `request_id` |
| `pbi_test_connection` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_update_measure` | escritura | 8 | 2 | — | — | falta un requerido | `name, table` |
| `pbi_update_visual_position` | escritura | 8 | 6 | — | — | falta un requerido | `height, page, visual_id, width, x, y` |
| `pbi_validate_desktop_render` | escritura | 9 | 0 | — | — | tipo invalido | `path` |
| `pbi_validate_generated_page` | solo lectura | 1 | 1 | — | — | falta un requerido | `page` |
| `pbi_validate_measures` | solo lectura | 1 | 1 | — | — | falta un requerido | `measures` |
| `pbi_validate_page_spec` | solo lectura | 1 | 1 | — | — | falta un requerido | `spec` |
| `pbi_validate_pbip_project` | solo lectura | 0 | 0 | — | — | sin proyecto activo | — |
| `pbi_validate_tmdl` | solo lectura | 2 | 0 | — | — | tipo invalido | `path` |
