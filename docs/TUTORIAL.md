# Tutorial: de la instalación a un dashboard

Recorrido completo sobre el **fixture sintético** del repositorio. No necesitas Power BI Desktop para los pasos 1–7.

---

## 1. Instalar

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/fetch_libs.py
```

```bash
python scripts/doctor.py
```

Debe terminar en `RESULTADO: instalacion operativa` con código **0**.

---

## 2. Registrar en tu cliente

```bash
python scripts/make_mcp_config.py --client all
```

Imprime el fragmento para Claude Code, Claude Desktop, Codex y stdio genérico, con rutas absolutas ya resueltas. Detalle en [`INSTALL.md`](INSTALL.md).

---

## 3. Orientarse

> Comprueba el estado del servidor y dime qué puedo hacer ahora mismo.

`pbi_health_check` y `pbi_capabilities`. La segunda dice qué está disponible **y qué no, con el motivo** — incluido que `mode="both"` está deshabilitado y por qué.

---

## 4. Abrir el proyecto

> Abre el proyecto `tests/fixtures/synthetic/minimal/Demo.pbip` y hazme un resumen del modelo.

`pbi_open_pbip_project` → `pbi_model_summary`. Verás 2 tablas, 6 columnas, 2 medidas, 1 relación.

---

## 5. Entender antes de tocar

> ¿De qué depende la medida `Ratio Pct` y quién la usa?

```
depends_on.measures : ['TotalAmount']
used_by             : []
is_unused           : True
```

> ¿Qué se rompe si oculto `Fact[Amount]`?

`pbi_column_dependencies` → la usa `TotalAmount`.

---

## 6. Auditar

> Audita el proyecto entero y genera el informe en HTML.

`pbi_audit_project(formats=["html"])`. Devuelve puntaje global y por dominio, resumen ejecutivo y hallazgos priorizados con evidencia. El HTML queda en `outputs/`.

---

## 7. Construir un dashboard

> Construye una página ejecutiva con `TotalAmount` y `Ratio Pct`, por `Calendar[Year]`. Enséñame el preview antes de aplicar.

```
pbi_build_executive_page(measures=[...], category="Calendar[Year]")   # dry_run por defecto
```

Devuelve las etapas `analisis → plan → preview`. Revisa el HTML del preview: **las posiciones son las definitivas**.

> Aplícalo.

```
pbi_build_executive_page(..., dry_run=false)
```

Añade `apply` y `verificacion`. Comprueba `valid: true` y `broken_references: []`.

---

## 8. Iterar con seguridad

> Duplica la tarjeta de `TotalAmount` y ponle de título "Importe acumulado".

`pbi_duplicate_visual` conserva campos y formato; solo regenera el id.

> ¿Hay problemas de layout en esa página?

`pbi_detect_layout_issues` — solapamientos con área exacta, fuera de lienzo, tamaños, orden Z.

> Normalízalo.

`pbi_normalize_page_layout(dry_run=true)` primero: corrige **solo** lo que está mal.

---

## 9. Deshacer

> Enséñame los journals de este proyecto.

`pbi_list_pending_journals(only_pending=false)` → uno por operación lógica.

> Inspecciona el último.

`pbi_inspect_journal` dice, por archivo, si sigue como el original y si hay respaldo. Para restaurar, [`RECOVERY.md`](RECOVERY.md).

---

## 10. Preparar la entrega

> ¿Está listo para entregar?

`pbi_prepare_delivery` devuelve un checklist con bloqueantes y el plan de correcciones disponibles.

> Aplica solo las correcciones de títulos ausentes.

```
pbi_plan_audit_fixes(rules=["report_visual_without_title"])
pbi_apply_audit_fixes(actions=[...], confirm=true)
```

No existe "arreglar todo": hay que nombrar las reglas.

> Genera la documentación técnica.

`pbi_generate_technical_documentation` → Markdown con modelo, dependencias, informe página a página y auditoría.

---

## 11. Con Power BI Desktop abierto

Solo la capa **en vivo**:

> Lista los modelos abiertos, selecciona el único y ejecuta `EVALUATE ROW("ok", 1)`.

Recuerda:

- `mode="live"` no persiste hasta que guardas con **Ctrl+S**.
- Con Desktop abierto, la escritura **PBIR está bloqueada** a propósito.
- `pbi_compare_live_to_pbip` te dice si hay cambios en memoria sin guardar.

---

## Errores que verás, y qué significan

| Error | Qué hacer |
|---|---|
| `project_open_in_desktop` | Cierra Desktop. Es intencionado |
| `dual_mode_not_safely_available` | Elige `live` o `pbip` |
| `dax_not_read_only` | Solo `EVALUATE`, `DEFINE…EVALUATE` y DMVs |
| `plan_token_stale` | El proyecto cambió: regenera el plan |
| `page_spec_invalid` | Mira `details.errors`: traen el JSON path exacto |
