# Cobertura de payloads — CONTRACT-002

**Este documento no se escribe: se calcula.** Lo genera
`python -m tests.cobertura_payloads` recorriendo las 134 tools por
`call_tool`, y `tests/test_contrato_de_payload.py` falla si deja de coincidir.

## Por qué existe

El golden congelaba `pbi_health_check`, `pbi_capabilities` y `guide.situacion`
—o sea **dos tools públicas de 134**— y de ahí se concluyó que «el resto
necesita Power BI Desktop». Nadie lo había comprobado tool por tool. Medido, el
reparto es otro: lo que bloquea a la mayoría no es Desktop, son **argumentos que
hay que construir**, y eso es trabajo, no un impedimento.

## Qué significa cada estado

| Estado | Qué hay congelado | Qué falta |
|---|---|---|
| éxito congelado | la forma de una respuesta buena | nada |
| error de dominio congelado | la forma del error que contesta | el éxito, y la columna «bloqueo» dice por qué |
| **pendiente** | nada | lo que diga la columna «bloqueo» |

Los dos escenarios son deterministas y ninguno toca Power BI: `sin-proyecto`
—nada abierto— y `con-proyecto` —un `.pbip` sintético en un temporal—. El
descubrimiento de Desktop se sustituye para que el golden no dependa de si quien
lo genera tiene un informe abierto.


## Cuentas

| | |
|---|---|
| Tools | **134** |
| Con payload congelado | **53** |
| — de éxito | **24** |
| — solo de error de dominio | **29** |
| Sin payload congelado | **81** |

### De qué depende cada exclusión

| Dependencia | Tools |
|---|---|
| requiere-argumentos | **77** |
| modelo-vivo | **14** |
| solo error de dominio en estos escenarios (no_active_pbip) | **10** |
| no-se-ejecuta | **4** |
| solo error de dominio en estos escenarios (validation_error) | **4** |
| solo error de dominio en estos escenarios (model_discovery_error) | **1** |

**`requiere-argumentos` no es un bloqueo externo**: es trabajo de escribir una llamada válida por tool, y mientras esté ahí, G2.2 no está cumplido.

## Las tools, una por una

| Tool | Estado | Escenarios | Bloqueo medido |
|---|---|---|---|
| `pbi_add_custom_visual` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_add_image_resource` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_add_table_from_file` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_add_table_from_source` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_align_visuals` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_analyze_model_quality` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_apply_audit_fixes` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_apply_design_system` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_apply_page_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_apply_plan` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_apply_theme` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_arrange_visuals` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_audit_model` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_audit_project` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_audit_report_only` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_backup_pbip_project` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_build_dashboard` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_build_evm_page` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_build_executive_page` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_capabilities` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_check_contract` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_close_desktop` | **pendiente** | — | no-se-ejecuta: clasificada como destructiva y no se ejecuta a ciegas sin proyecto |
| `pbi_column_dependencies` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_compare_live_to_pbip` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_compose_page` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_convert_pbix_to_pbip` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_copy_visual_format` | **pendiente** | — | requiere-argumentos: 4 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_bookmark` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_calculated_column` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_calculated_table` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_hierarchy` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_html_visual` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_measure` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_page_from_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_pbip_project` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_relationship` | **pendiente** | — | requiere-argumentos: 4 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_create_visual` | **pendiente** | — | requiere-argumentos: 4 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_define_brief` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_define_port_contract` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_delete_bookmark` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_delete_measure` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_delete_page` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_delete_visual` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_detect_layout_issues` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_diagnose_data` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_diff_page_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_disable_auto_date_time` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_distribute_visuals` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_document_model` | error de dominio congelado | sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_document_report_layout` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_duplicate_page` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_duplicate_visual` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_export_excel` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_export_page_html` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_export_report_content` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_generate_page_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_generate_pdf_report` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_generate_report_page` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_generate_technical_documentation` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_get_brief` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_get_object` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_get_visual` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_health_check` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_hide_columns` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_inspect_journal` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_inspect_pbix` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_list_audit_rules` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_autofix_rules` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_bookmarks` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_convertible_pbix` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_list_design_systems` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_desktop_models` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_hierarchies` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_measures` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_page_presets` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_partitions` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_pending_journals` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_perspectives` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_relationships` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_report_pages` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_report_resources` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_roles` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_tables` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_list_themes` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_visuals` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_measure_dependencies` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_model_summary` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_normalize_page_layout` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_normalize_report` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_open_and_refresh` | **pendiente** | — | no-se-ejecuta: clasificada como destructiva y no se ejecuta a ciegas sin proyecto |
| `pbi_open_in_desktop` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_open_pbip_project` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_page_building_blocks` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_plan_audit_fixes` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_plan_change` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_prepare_delivery` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_preview_page_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_preview_spec_html` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_profile_data` | error de dominio congelado | sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_propose_dashboard` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_purge_backups` | **pendiente** | — | no-se-ejecuta: clasificada como destructiva y no se ejecuta a ciegas sin proyecto |
| `pbi_recover_from_journal` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_reflow_pages` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_refresh_model` | **pendiente** | — | no-se-ejecuta: clasificada como destructiva y no se ejecuta a ciegas sin proyecto |
| `pbi_rename_measure` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_rename_page` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_reorder_pages` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_repair_broken_references` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_replace_visual_field` | **pendiente** | — | requiere-argumentos: 4 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_report_capabilities` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_run_dax` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_search_model` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_select_model` | error de dominio congelado | con-proyecto, sin-proyecto | solo error de dominio en estos escenarios (model_discovery_error) |
| `pbi_session_info` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_set_color_from_field` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_column_visibility` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_conditional_format` | **pendiente** | — | requiere-argumentos: 5 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_relationship_direction` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_storage_mode` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_visual_filter` | **pendiente** | — | requiere-argumentos: 3 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_visual_title` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_set_visual_z_order` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_sharepoint_download_folder` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_sharepoint_list_folder` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_start_here` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_test_connection` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_update_measure` | **pendiente** | — | requiere-argumentos: 2 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_update_visual_position` | **pendiente** | — | requiere-argumentos: 6 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_validate_desktop_render` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_validate_generated_page` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_validate_measures` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_validate_page_spec` | **pendiente** | — | requiere-argumentos: 1 parametro(s) obligatorio(s) que hay que construir a mano |
| `pbi_validate_pbip_project` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_validate_tmdl` | éxito congelado | con-proyecto, sin-proyecto | — |
