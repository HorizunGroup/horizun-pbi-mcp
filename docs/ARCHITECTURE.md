# Arquitectura de Horizun PBI MCP

_Estado verificado en el commit baseline `a304e33`. 4.982 líneas de Python en `src/`._

---

## 1. Arquitectura actual (lo que hay hoy)

```
                        cliente MCP (Claude Code / Desktop / Codex)
                                        │  JSON-RPC sobre stdio
                                        ▼
                            src/server.py — build_server()
                                  FastMCP("horizun-pbi-mcp")
                                        │  register(mcp) × 8
        ┌───────────────┬───────────────┼───────────────┬───────────────┐
        ▼               ▼               ▼               ▼               ▼
   dax_tools     documentation_   measure_tools   model_edit_    visual_tools
   (5 tools)     tools (5)        (3)             tools (4)      (9)
                                                          page_tools (4)
                                                          pbip_tools (3)
                                                          refresh_tools (1)
        └───────────────┴───────────────┬───────────────┴───────────────┘
                                        │  tools/_common.guard()
                                        │  ← ÚNICA abstracción transversal
                        ┌───────────────┴───────────────┐
                        ▼                               ▼
              src/powerbi/  (EN VIVO)          src/pbip/  (EN DISCO)
        ┌─────────────────────────┐     ┌──────────────────────────────┐
        │ clr_bootstrap  carga CLR│     │ project_locator  localiza    │
        │ adomd_client   ADOMD.NET│     │ tmdl_reader/writer  modelo   │
        │ dax_runner     consultas│     │ pbir_reader/writer  informe  │
        │ desktop_discovery puertos│    │ visual_factory   clonado     │
        │ model_reader   metadatos│     │ layout_engine    geometría   │
        │ model_writer   TOM      │     │ page_builder     hojas+HTML  │
        │ refresh                 │     │ backup                       │
        └─────────────────────────┘     └──────────────────────────────┘
                        │                               │
                        └───────────────┬───────────────┘
                                        ▼
                    src/utils/     json_utils (escritura atómica)
                                   file_utils · validation · change_log
                    src/config.py  Settings + Session (singletons de proceso)
                    src/reporting.py  Markdown + reglas de calidad
```

### Qué está bien resuelto

| Aspecto | Dónde | Por qué importa |
|---|---|---|
| Separación vivo/disco | `powerbi/` vs `pbip/` | Refleja una restricción real: el endpoint local **solo** expone datos, no visuales |
| Escritura JSON atómica | `utils/json_utils.py:39` | Serializa en memoria → `.tmp` → `os.replace`. Nunca deja un JSON a medias |
| Negativa a pisar JSON corrupto | `utils/json_utils.py:20` | Si no parsea, lanza en vez de sobrescribir |
| Clonado en vez de invención | `pbip/visual_factory.py:166` | Un `visual.json` inventado casi nunca abre bien; clonar preserva el andamiaje del tema |
| Logging fuera de stdout | `logging_config.py` | stdout es el canal JSON-RPC; escribir ahí rompe el protocolo |
| Aislamiento de errores en modo `both` | `tools/measure_tools.py:29` | Si falla `live`, `pbip` igual se intenta y se marca `consistent: False` |

### Qué falta (deuda estructural)

| # | Problema | Evidencia |
|---|---|---|
| A1 | **No existe capa de servicios.** Las tools llaman directo a los adaptadores | `page_tools.py:16` importa `_measure_index` y `_model_data` desde `visual_tools` |
| A2 | **`_dual()` duplicado** casi idéntico | `measure_tools.py:29` y `model_edit_tools.py:24` |
| A3 | **Tool que llama a otra tool** | `model_edit_tools.py:75`: `pbi_hide_columns` invoca `pbi_set_column_visibility`. Funciona sólo porque `mcp.tool()` de FastMCP 1.28.1 devuelve la función original |
| A4 | **Política de backup dispersa** | Cada llamada decide `do_backup=True/False`; no hay criterio central |
| A5 | **Sin control de concurrencia** | `grep -r "expected_state\|request_id\|dry_run" src/` → 0 resultados |
| A6 | **Escritura PBIR no acotada** | `ensure_within_base()` sólo se usa en `project_locator.py:24`; los writers construyen rutas con entrada del usuario |
| A7 | **Sin verificación post-escritura** | Se escribe y se devuelve; nadie relee para confirmar |
| A8 | **Backups no reutilizables** | `backup_before_edit()` crea copias, pero ninguna tool sabe restaurarlas |
| A9 | **Esquemas laxos** | 0 de 34 tools usan `enum`; `mode`, `layout`, `source`, `direction` son strings libres |

---

## 2. Arquitectura objetivo

```
   MCP tools            ← sólo firma, validación de entrada y serialización
       ↓                  (nunca abren archivos ni conexiones)
   Application services  ← workflows: crear_pagina, editar_medida, auditar
       ↓                  (deciden backup, lock, dry-run, verificación)
   Domain + validation   ← spec de página, posiciones, referencias del modelo
       ↓
   Adapters
     ├─ desktop discovery
     ├─ ADOMD.NET
     ├─ TOM
     ├─ TMDL filesystem
     └─ PBIR filesystem
```

### Servicios compartidos previstos

| Servicio | Responsabilidad | Resuelve |
|---|---|---|
| `sessions` | descubrir, seleccionar, detectar sesión muerta, reconectar | R6 (sesión obsoleta) |
| `safety` | `dry_run`, `confirm`, `expected_state`, `request_id` | A5 |
| `paths` | toda ruta acotada al proyecto activo | A6 |
| `locking` | lock por proyecto + detección de Desktop abierto | R2 |
| `backup` | incremental, con retención y **restauración** | A4, A8 |
| `verify` | releer después de escribir y comparar | A7 |
| `envelope` | respuesta uniforme `status/target/before/after/validation/backup/warnings` | contrato |
| `telemetry` | logging estructurado con request id y duración | observabilidad |

### Migración sin romper nada

Las 34 tools se conservan **con el mismo nombre y la misma firma**. Los campos nuevos se **añaden** al dict de respuesta; `ok` sigue existiendo. El golden en `tests/golden/tools_v1.json` bloquea cualquier desvío: un parámetro nuevo obligatorio o un `default` cambiado hacen fallar la suite con un informe legible.

---

## 3. Invariantes del proyecto

Reglas que ninguna fase puede romper. Ver también [AGENTS.md](../AGENTS.md).

1. **stdout es sagrado.** Todo log va a stderr o a fichero.
2. **Nunca sobrescribir un JSON que no parsea.**
3. **Toda escritura de proyecto va precedida de backup** y seguida de relectura.
4. **Ninguna ruta de escritura sale del proyecto activo.**
5. **No se inventan campos** que no existan en el modelo.
6. **Las tools destructivas exigen `confirm=true`.**
7. **Los fixtures versionados no contienen datos reales.**
8. **Las 34 tools del baseline no se renombran ni se eliminan** sin capa de compatibilidad.

---

## 4. Restricción externa que condiciona todo el diseño

El motor local de Power BI Desktop (`msmdsrv.exe` en `localhost:<puerto>`) expone **únicamente el modelo semántico**. Páginas, visuales y layout **no existen** en ningún endpoint vivo: sólo en archivos PBIR.

De ahí las dos capas, y de ahí que editar el informe con Power BI Desktop abierto sea peligroso: Desktop tiene su propia copia en memoria y al guardar sobrescribe el disco. Detectarlo y bloquearlo es trabajo de la Fase 1.
