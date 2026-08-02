# Inventario de las 34 tools

_Congelado en `tests/golden/tools_v1.json`. Verificado por handshake MCP real sobre stdio._

## Clases de riesgo

| Clase | N.º | Qué significa |
|---|---|---|
| `read_only` | 16 | No modifica nada del usuario. Algunas escriben artefactos en `outputs/` |
| `session_state` | 2 | Cambia qué modelo/proyecto está activo. Persiste en `outputs/session.json` |
| `write_safe` | 1 | Sólo crea un backup |
| `write_reversible` | 13 | Modifica modelo o informe. Hace backup previo |
| `write_irreversible` | 1 | Altera datos sin vuelta atrás |
| `write_destructive` | 1 | Borra definiciones. Exige `confirm=true` |

> **Estado real de las garantías (baseline):** el backup existe, pero **no hay restauración**, **no hay `dry_run`**, **no hay `expected_state`** y **no se verifica releyendo**. La clase `write_reversible` describe la *intención*, no una garantía cerrada. Se completa en la Fase 1.

---

## 1. Sesión, conexión y DAX

| Tool | Clase | Precondición | Devuelve | Cuándo NO usarla |
|---|---|---|---|---|
| `pbi_list_desktop_models` | `read_only` | ninguna | `count`, `instances[]` (puerto, pid, catálogo, nº tablas), `diagnostics` | — |
| `pbi_select_model` | `session_state` | ≥1 instancia viva | `active_model` | Si hay varias instancias **exige** `port`: no elige sola, y eso es deliberado |
| `pbi_run_dax` | `read_only`\* | modelo activo | `columns`, `rows`, `truncated`, `elapsed_ms` | \*Es de lectura **por intención, no por validación**: no comprueba el tipo de sentencia |
| `pbi_test_connection` | `read_only` | modelo activo | `connected`, `elapsed_ms` | — |
| `pbi_validate_measures` | `read_only` | modelo activo | por medida: `valid`, `value`, `error` | Úsala **antes** de `pbi_create_measure`: valida sin tocar el modelo |

## 2. Documentación del modelo

| Tool | Clase | Precondición | Devuelve |
|---|---|---|---|
| `pbi_list_tables` | `read_only` | modelo activo (`live`) o `.pbip` (`pbip`) | `tables[]` con columnas y tipos |
| `pbi_list_measures` | `read_only` | ídem | `measures[]` con DAX, formato, carpeta |
| `pbi_list_relationships` | `read_only` | ídem | `relationships[]` con cardinalidad y filtro cruzado |
| `pbi_analyze_model_quality` | `read_only` | ídem | `issue_count`, `by_severity`, `issues[]` |
| `pbi_document_model` | `read_only` | ídem | ruta del Markdown en `outputs/` |

> `pbi_analyze_model_quality` aplica **7 reglas heurísticas** sobre el modelo (`src/reporting.py:23`). No tienen identificador estable ni `auto_fix_available`, y no hay ninguna regla de informe. Se rehace en la Fase 4.

## 3. Medidas — `mode: live | pbip | both`

| Tool | Clase | Precondición | Devuelve |
|---|---|---|---|
| `pbi_create_measure` | `write_reversible` | según `mode` | `action`, `before`, `after` |
| `pbi_update_measure` | `write_reversible` | ídem | ídem. Lo no especificado se conserva |
| `pbi_delete_measure` | **`write_destructive`** | ídem + **`confirm=true`** | `action`, `before` |

**Semántica de `mode`:**
- `live` → modelo en memoria de Desktop (TOM). **Requiere Ctrl+S del usuario** para persistir.
- `pbip` → archivo TMDL en disco, con backup.
- `both` → ambos, con los errores aislados: si un lado falla, el otro se intenta igual y se devuelve `consistent: false`.

## 4. Edición de modelo

| Tool | Clase | Nota |
|---|---|---|
| `pbi_set_column_visibility` | `write_reversible` | `mode: live\|pbip\|both` |
| `pbi_hide_columns` | `write_reversible` | Lote sobre la anterior. **Sin preview ni diff** |
| `pbi_set_relationship_direction` | `write_reversible` | Pasar a `single` puede alterar totales que dependían de la bidireccional |
| `pbi_disable_auto_date_time` | `write_reversible` | Sólo `pbip`. **Sólo evita tablas NUEVAS**: las `LocalDateTable_*` existentes siguen ahí y borrarlas puede romper visuales que usen la jerarquía automática |

## 5. Proyecto `.pbip`

| Tool | Clase | Devuelve |
|---|---|---|
| `pbi_open_pbip_project` | `session_state` | estructura detectada + avisos si falta PBIR o TMDL |
| `pbi_validate_pbip_project` | `read_only` | `valid`, `checks`, `warnings` |
| `pbi_backup_pbip_project` | `write_safe` | ruta del backup (`mode: folder\|zip`, `scope: report\|model\|both`) |

## 6. Refresh

| Tool | Clase | Nota |
|---|---|---|
| `pbi_refresh_model` | **`write_irreversible`** | Sólo local, nunca el Service. `type: full\|calculate\|clear_values`. No tiene vuelta atrás salvo refrescar de nuevo |

## 7. Informe PBIR

| Tool | Clase | Precondición | Nota |
|---|---|---|---|
| `pbi_list_report_pages` | `read_only` | `.pbip` con PBIR | — |
| `pbi_list_visuals` | `read_only` | ídem | `page` acepta id o nombre visible |
| `pbi_document_report_layout` | `read_only` | ídem | Markdown en `outputs/` |
| `pbi_create_visual` | `write_reversible` | ídem | Clona una plantilla del mismo tipo. Sin plantilla avisa que hay que validar en Desktop |
| `pbi_add_custom_visual` | `write_reversible` | ídem | Registra en `publicCustomVisuals`. Desktop lo descarga de AppSource al abrir |
| `pbi_create_html_visual` | `write_reversible` | ídem | Requiere una medida que **devuelva HTML** |
| `pbi_update_visual_position` | `write_reversible` | ídem | — |
| `pbi_arrange_visuals` | `write_reversible` | ídem | `layout: grid\|dashboard\|executive_summary\|custom` |
| `pbi_generate_report_page` | `write_reversible` | ídem | Composición heurística. No inventa campos: informa los que ignora |

> ⚠️ **Todas las de escritura PBIR comparten un riesgo no mitigado en el baseline:** ninguna comprueba si Power BI Desktop tiene el proyecto abierto. Si lo está y el usuario guarda, sobrescribe lo escrito en disco. Sólo hay un aviso textual (`RELOAD_HINT`).

## 8. Generación declarativa de hojas

| Tool | Clase | Papel en el flujo |
|---|---|---|
| `pbi_page_building_blocks` | `read_only` | **1.** Inventario: modelo + catálogo de visuales existentes + canvas |
| `pbi_preview_spec_html` | `read_only` | **3.** Maqueta HTML del spec propuesto, sin escribir nada |
| `pbi_create_page_from_spec` | `write_reversible` | **4.** Materializa la página PBIR |
| `pbi_export_page_html` | `read_only` | Maqueta de una página **ya existente** |

Flujo previsto: `building_blocks` → *(el LLM arma el spec)* → `preview_spec_html` → *(el usuario aprueba)* → `create_page_from_spec`.

**Falta hoy:** `schema_version` en el spec, validación de esquema, validación de referencias contra el modelo, `diff` previo y `rollback`. Fase 3.

---

## Anexo: parámetros que deberían ser `enum`

Ninguna tool declara `enum` en su `inputSchema`; se validan a mano en runtime. Convertirlos es trabajo de la Fase 1 y es **aditivo** (no rompe el contrato).

| Parámetro | Tools | Valores admitidos hoy |
|---|---|---|
| `mode` | 6 tools de escritura | `live`, `pbip`, `both` |
| `source` | 5 de documentación | `live`, `pbip` |
| `layout` | `arrange_visuals`, `generate_report_page` | `grid`, `dashboard`, `executive_summary`, `custom` |
| `direction` | `set_relationship_direction` | `single`, `both` |
| `type` | `refresh_model` | `full`, `calculate`, `clear_values`, `automatic`, `data_only` |
| `mode` / `scope` | `backup_pbip_project` | `folder`/`zip` · `report`/`model`/`both` |
| `visual_type` | `create_visual` | 8 alias → 8 tipos PBIR reales |
