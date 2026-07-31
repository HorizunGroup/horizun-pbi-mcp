# Catálogo de tools — 88

Generado sobre el contrato congelado en `tests/golden/tools_v1.json`.
Las **34 del baseline** conservan nombre, parámetros, tipos, defaults y forma de respuesta desde la versión 0.1.0.

| Bloque | N.º |
|---|---|
| Baseline original | 34 |
| A — plataforma | 7 |
| B — modelo semántico | 11 |
| C — autoría PBIR | 16 |
| D — spec declarativo | 7 |
| E — auditoría integral | 5 |
| F — workflows | 8 |

---

## Sesión, capacidades y recuperación

| Tool | Qué hace |
|---|---|
| `pbi_health_check` | Estado general: dependencias, DLLs, sesión, journals pendientes |
| `pbi_capabilities` | Qué se puede hacer **ahora**, y qué no, con el motivo |
| `pbi_session_info` | Modelo y proyecto activos, y su frescura |
| `pbi_list_desktop_models` | Instancias de Power BI Desktop abiertas |
| `pbi_select_model` | Fija el modelo activo (exige puerto si hay varias) |
| `pbi_test_connection` | Valida la conexión |
| `pbi_list_pending_journals` | Journals de operaciones que quedaron a medias |
| `pbi_inspect_journal` | Compara un journal con el estado actual (solo lectura) |

## Consulta y exploración del modelo

| Tool | Qué hace |
|---|---|
| `pbi_run_dax` | DAX de solo lectura, con límite de filas, bytes, timeout y export |
| `pbi_validate_measures` | Valida DAX sin modificar el modelo |
| `pbi_model_summary` | Resumen compacto para orientarse |
| `pbi_search_model` | Busca por nombre y dentro del DAX |
| `pbi_get_object` | Detalle de una tabla, columna o medida |
| `pbi_measure_dependencies` | De qué depende y quién la usa |
| `pbi_column_dependencies` | Qué se rompe si tocas una columna |
| `pbi_list_tables` · `pbi_list_measures` · `pbi_list_relationships` | Inventarios |
| `pbi_list_hierarchies` · `pbi_list_roles` · `pbi_list_perspectives` · `pbi_list_partitions` | Objetos secundarios |
| `pbi_document_model` | Documentación Markdown del modelo |

## Modificación del modelo

> `mode`: `live` (Desktop abierto) o `pbip` (Desktop cerrado). **`both` está deshabilitado**; ver `docs/PHASE_1A_DESIGN.md`.

| Tool | Destructiva |
|---|---|
| `pbi_create_measure` · `pbi_update_measure` | No |
| `pbi_delete_measure` | **Sí** (`confirm`) |
| `pbi_set_column_visibility` · `pbi_hide_columns` | No |
| `pbi_set_relationship_direction` · `pbi_disable_auto_date_time` | No |
| `pbi_refresh_model` | Irreversible |

## Proyecto y planes

| Tool | Qué hace |
|---|---|
| `pbi_open_pbip_project` · `pbi_validate_pbip_project` | Abrir y validar el `.pbip` |
| `pbi_backup_pbip_project` | Backup con manifiesto de hashes |
| `pbi_plan_change` | Calcula un plan con diff y `plan_token`, sin escribir |
| `pbi_apply_plan` | Aplica el plan si el estado no cambió |

## Autoría de informes (PBIR)

| Tool | Destructiva |
|---|---|
| `pbi_report_capabilities` · `pbi_get_visual` · `pbi_list_report_pages` · `pbi_list_visuals` | No |
| `pbi_create_visual` · `pbi_duplicate_visual` · `pbi_create_html_visual` · `pbi_add_custom_visual` | No |
| `pbi_delete_visual` | **Sí** (`confirm`) |
| `pbi_set_visual_title` · `pbi_set_visual_z_order` · `pbi_replace_visual_field` · `pbi_copy_visual_format` · `pbi_update_visual_position` | No |
| `pbi_duplicate_page` · `pbi_rename_page` · `pbi_reorder_pages` | No |
| `pbi_delete_page` | **Sí** (`confirm`) |
| `pbi_detect_layout_issues` · `pbi_align_visuals` · `pbi_distribute_visuals` · `pbi_normalize_page_layout` · `pbi_arrange_visuals` | No |
| `pbi_document_report_layout` · `pbi_export_page_html` | No |

## Constructor declarativo

| Tool | Qué hace |
|---|---|
| `pbi_list_page_presets` | Los 6 presets con sus bloques |
| `pbi_generate_page_spec` | Borrador de spec desde un preset |
| `pbi_validate_page_spec` | Valida esquema y referencias, con JSON path |
| `pbi_preview_page_spec` | Maqueta HTML con las posiciones finales |
| `pbi_diff_page_spec` | Compara con una página existente |
| `pbi_apply_page_spec` | Materializa en una transacción (`dry_run` disponible) |
| `pbi_validate_generated_page` | Verifica la página ya escrita |
| `pbi_page_building_blocks` · `pbi_preview_spec_html` · `pbi_create_page_from_spec` · `pbi_generate_report_page` | Flujo original (baseline) |

## Auditoría

| Tool | Qué hace |
|---|---|
| `pbi_audit_project` | Integral: modelo + informe + layout, JSON/MD/HTML |
| `pbi_audit_model` · `pbi_audit_report_only` · `pbi_analyze_model_quality` | Por dominio |
| `pbi_list_audit_rules` · `pbi_list_autofix_rules` | Catálogos |
| `pbi_plan_audit_fixes` | Planifica correcciones de reglas **concretas** |
| `pbi_apply_audit_fixes` | **Destructiva** (`confirm`) |

## Workflows

| Tool | Resultado |
|---|---|
| `pbi_build_dashboard` · `pbi_build_executive_page` · `pbi_build_evm_page` | Una página completa y verificada |
| `pbi_repair_broken_references` | Diagnostica y repara con `mapping` |
| `pbi_normalize_report` | Geometría de todas las páginas |
| `pbi_prepare_delivery` | Checklist de entrega + plan |
| `pbi_compare_live_to_pbip` | Diferencias memoria vs disco |
| `pbi_generate_technical_documentation` | Markdown completo |

> Todos los workflows vienen en `dry_run=true` por defecto.

---

## Clases de riesgo

| Clase | Comportamiento |
|---|---|
| `read_only` | No modifica nada del usuario |
| `write_reversible` | Transacción con journal; rollback si falla |
| `write_destructive` | Exige `confirm=true`: `pbi_delete_measure`, `pbi_delete_visual`, `pbi_delete_page`, `pbi_apply_audit_fixes` |
| `write_irreversible` | `pbi_refresh_model` |
| `unsupported` | `mode="both"` y cloud/Fabric — declarados con su motivo en `pbi_capabilities` |
