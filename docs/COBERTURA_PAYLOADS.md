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
| Tools | **139** |
| Con payload congelado | **139** |
| — de éxito | **45** |
| — solo de error de dominio | **94** |
| Sin payload congelado | **0** |

### De qué depende cada exclusión

| Dependencia | Tools |
|---|---|
| solo error de dominio en estos escenarios (validation_error) | **25** |
| modelo-vivo | **24** |
| solo error de dominio en estos escenarios (no_active_pbip) | **11** |
| solo error de dominio en estos escenarios (schema_unsupported) | **10** |
| solo error de dominio en estos escenarios (page_spec_invalid) | **3** |
| solo error de dominio en estos escenarios (schema_unavailable) | **3** |
| solo error de dominio en estos escenarios (unexpected) | **2** |
| solo error de dominio en estos escenarios (pbix_conversion_failed) | **2** |
| solo error de dominio en estos escenarios (visual_factory_error) | **2** |
| solo error de dominio en estos escenarios (pbix_export_failed) | **2** |
| solo error de dominio en estos escenarios (conditional_format_error) | **2** |
| solo error de dominio en estos escenarios (sharepoint_not_configured) | **2** |
| solo error de dominio en estos escenarios (pbix_read_error) | **1** |
| solo error de dominio en estos escenarios (desktop_not_found) | **1** |
| solo error de dominio en estos escenarios (recovery_failed) | **1** |
| solo error de dominio en estos escenarios (model_discovery_error) | **1** |
| solo error de dominio en estos escenarios (model_author_error) | **1** |
| solo error de dominio en estos escenarios (power_query_error) | **1** |

**`requiere-argumentos` no es un bloqueo externo**: es trabajo de escribir una llamada válida por tool, y mientras esté ahí, G2.2 no está cumplido.

## Las tools, una por una

| Tool | Estado | Escenarios | Bloqueo medido |
|---|---|---|---|
| `pbi_add_custom_visual` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_add_image_resource` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_add_table_from_file` | éxito congelado | con-argumentos | — |
| `pbi_add_table_from_source` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_align_visuals` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_analyze_model_quality` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_apply_audit_fixes` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_apply_design_system` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_apply_page_spec` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (page_spec_invalid) |
| `pbi_apply_plan` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_apply_theme` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_arrange_visuals` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_audit_model` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_audit_project` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_audit_report_only` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_backup_pbip_project` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_build_dashboard` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_build_evm_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_build_executive_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_capabilities` | error de dominio congelado | con-proyecto, sin-proyecto | solo error de dominio en estos escenarios (unexpected) |
| `pbi_check_contract` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_close_desktop` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_column_dependencies` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_compare_live_to_pbip` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_compose_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unavailable) |
| `pbi_convert_pbix_to_pbip` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (pbix_conversion_failed) |
| `pbi_copy_visual_format` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_create_bookmark` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unavailable) |
| `pbi_create_calculated_column` | éxito congelado | con-argumentos | — |
| `pbi_create_calculated_table` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_create_hierarchy` | éxito congelado | con-argumentos | — |
| `pbi_create_html_visual` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (visual_factory_error) |
| `pbi_create_measure` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_create_page_from_spec` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_create_pbip_project` | éxito congelado | con-argumentos | — |
| `pbi_create_relationship` | éxito congelado | con-argumentos | — |
| `pbi_create_visual` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (visual_factory_error) |
| `pbi_define_brief` | éxito congelado | con-argumentos | — |
| `pbi_define_port_contract` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_delete_bookmark` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_delete_measure` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_delete_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_delete_visual` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_detect_layout_issues` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_diagnose_data` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_diff_page_spec` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (page_spec_invalid) |
| `pbi_disable_auto_date_time` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_distribute_visuals` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_document_model` | error de dominio congelado | sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_document_report_layout` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_duplicate_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_duplicate_visual` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_export_excel` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_export_page_html` | éxito congelado | con-argumentos | — |
| `pbi_export_pbix` | error de dominio congelado | con-argumentos, sin-proyecto | solo error de dominio en estos escenarios (pbix_export_failed) |
| `pbi_export_report_content` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_finalize_delivery` | error de dominio congelado | con-argumentos, sin-proyecto | solo error de dominio en estos escenarios (pbix_export_failed) |
| `pbi_generate_page_spec` | éxito congelado | con-argumentos | — |
| `pbi_generate_pdf_report` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (validation_error) |
| `pbi_generate_report_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unavailable) |
| `pbi_generate_technical_documentation` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_get_brief` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_get_object` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_get_power_query` | error de dominio congelado | con-proyecto, sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_get_visual` | éxito congelado | con-argumentos | — |
| `pbi_health_check` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_hide_columns` | éxito congelado | con-argumentos | — |
| `pbi_inspect_journal` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_inspect_pbix` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (pbix_read_error) |
| `pbi_list_audit_rules` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_autofix_rules` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_bookmarks` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_list_convertible_pbix` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (pbix_conversion_failed) |
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
| `pbi_list_visuals` | éxito congelado | con-argumentos | — |
| `pbi_measure_dependencies` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_model_summary` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_normalize_page_layout` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_normalize_report` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_open_and_refresh` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_open_in_desktop` | error de dominio congelado | con-argumentos, sin-proyecto | solo error de dominio en estos escenarios (desktop_not_found) |
| `pbi_open_pbip_project` | éxito congelado | con-argumentos | — |
| `pbi_page_building_blocks` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_plan_audit_fixes` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_plan_change` | éxito congelado | con-argumentos | — |
| `pbi_prepare_delivery` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_prepare_project` | éxito congelado | con-argumentos | — |
| `pbi_preview_page_spec` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (page_spec_invalid) |
| `pbi_preview_spec_html` | éxito congelado | con-argumentos | — |
| `pbi_profile_data` | error de dominio congelado | sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_propose_dashboard` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_purge_backups` | éxito congelado | con-argumentos | — |
| `pbi_recover_from_journal` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (recovery_failed) |
| `pbi_reflow_pages` | éxito congelado | con-argumentos | — |
| `pbi_refresh_model` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_rename_measure` | éxito congelado | con-argumentos | — |
| `pbi_rename_page` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_reorder_pages` | éxito congelado | con-argumentos | — |
| `pbi_repair_broken_references` | error de dominio congelado | sin-proyecto | solo error de dominio en estos escenarios (no_active_pbip) |
| `pbi_replace_visual_field` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_report_capabilities` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_run_dax` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_search_model` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_select_model` | error de dominio congelado | con-argumentos, sin-proyecto | solo error de dominio en estos escenarios (model_discovery_error) |
| `pbi_session_info` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_set_color_from_field` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (conditional_format_error) |
| `pbi_set_column_visibility` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_set_conditional_format` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (conditional_format_error) |
| `pbi_set_relationship_direction` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (validation_error) |
| `pbi_set_storage_mode` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (model_author_error) |
| `pbi_set_visual_filter` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_set_visual_title` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_set_visual_z_order` | éxito congelado | con-argumentos | — |
| `pbi_sharepoint_download_folder` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (sharepoint_not_configured) |
| `pbi_sharepoint_list_folder` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (sharepoint_not_configured) |
| `pbi_start_here` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_test_connection` | error de dominio congelado | con-proyecto, sin-proyecto | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_update_measure` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_update_power_query` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (power_query_error) |
| `pbi_update_visual_position` | error de dominio congelado | con-argumentos | solo error de dominio en estos escenarios (schema_unsupported) |
| `pbi_validate_desktop_render` | error de dominio congelado | con-argumentos, sin-proyecto | solo error de dominio en estos escenarios (unexpected) |
| `pbi_validate_generated_page` | éxito congelado | con-argumentos | — |
| `pbi_validate_measures` | error de dominio congelado | con-argumentos | modelo-vivo: el payload de exito exige Power BI Desktop sirviendo un modelo (no_active_model) |
| `pbi_validate_page_spec` | éxito congelado | con-argumentos | — |
| `pbi_validate_pbip_project` | éxito congelado | con-proyecto, sin-proyecto | — |
| `pbi_validate_tmdl` | éxito congelado | con-proyecto, sin-proyecto | — |
