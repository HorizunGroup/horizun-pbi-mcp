# Catálogo de tools — 116

Generado sobre el contrato congelado en `tests/golden/tools_v1.json`.
Las **34 del baseline** conservan nombre, parámetros, tipos, defaults y forma de respuesta desde la versión 0.1.0.

> ¿No sabes por dónde empezar? Llama a **`pbi_start_here`**: mira el estado real
> del proyecto y responde con los tres o cuatro pasos que tocan ahora, cada uno
> con el motivo. Este catálogo es la referencia; esa tool es el camino.

Los recuentos salen de `tests/test_tool_contract.py`, que es lo que la suite
verifica. Antes se escribían a mano y la cabecera llegó a decir 101 con 112
tools registradas.

| Bloque | N.º |
|---|---|
| Baseline original | 34 |
| A — plataforma | 7 |
| B — modelo semántico | 16 |
| C — autoría PBIR | 17 |
| D — spec declarativo | 8 |
| E — auditoría integral | 6 |
| F — workflows | 8 |
| F R5 — atomicidad | 2 |
| G — conversión `.pbix` | 3 |
| H — composición, tema y marcadores | 7 |
| I — verificación antes de entregar | 2 |
| J — carga de datos | 2 |
| L — diseño y punto de entrada | 4 |
| **Total** | **116** |

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

## Conversión `.pbix` → `.pbip`

| Tool | Qué hace |
|---|---|
| `pbi_inspect_pbix` | Radiografía del `.pbix` sin convertirlo ni abrir Desktop |
| `pbi_list_convertible_pbix` | Vista previa de una carpeta: qué se copia, qué se traduce, qué necesita Desktop |
| `pbi_convert_pbix_to_pbip` | Genera el proyecto (informe PBIR + modelo TMDL); acepta archivo o carpeta |

## Composición e identidad visual

| Tool | Qué hace |
|---|---|
| `pbi_list_themes` | Paletas disponibles, verificadas contra daltonismo, con su escenario de uso |
| `pbi_apply_theme` | Escribe el tema y lo declara en el informe (las tres partes o ninguna) |
| `pbi_add_image_resource` | Incrusta una imagen y la declara: sin las dos cosas el visual sale vacío |
| `pbi_list_report_resources` | Recursos declarados, en disco y los que no cuadran |
| `pbi_set_conditional_format` | Color que sale del dato: convierte una matriz en mapa de calor |

## Autoría del modelo semántico

| Tool | Qué hace |
|---|---|
| `pbi_create_calculated_column` | Columna calculada DAX, declarada antes de la partición |
| `pbi_create_relationship` | Relación entre columnas, en `relationships.tmdl` |
| `pbi_create_hierarchy` | Jerarquía sobre columnas de la misma tabla |

## Propuestas y calidad del dato

| Tool | Qué hace |
|---|---|
| `pbi_propose_dashboard` | Clasifica el modelo y propone diseños completos, con su porqué |
| `pbi_profile_data` | Perfila los VALORES: porcentajes fuera de rango, columnas vacías |

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

## Diseño y punto de entrada

Cierran dos huecos del mismo tipo: tener las piezas no es lo mismo que saber usarlas.

| Tool | Qué hace |
|---|---|
| `pbi_start_here` | Mira el estado real y dice los siguientes pasos, con el motivo de cada uno |
| `pbi_list_design_systems` | Sistemas disponibles: cada uno decide tema, lienzo, rejilla y escala de texto a la vez |
| `pbi_apply_design_system` | Aplica el sistema y devuelve la rejilla, para colocar a mano sobre las mismas guías |
| `pbi_compose_page` | Compone una página entera sobre la rejilla a partir de la intención |

## Clases de riesgo

| Clase | Comportamiento |
|---|---|
| `read_only` | No modifica nada del usuario |
| `write_reversible` | Transacción con journal; rollback si falla |
| `write_destructive` | Exige `confirm=true`: `pbi_delete_measure`, `pbi_delete_visual`, `pbi_delete_page`, `pbi_apply_audit_fixes` |
| `write_irreversible` | `pbi_refresh_model` |
| `unsupported` | `mode="both"` y cloud/Fabric — declarados con su motivo en `pbi_capabilities` |
