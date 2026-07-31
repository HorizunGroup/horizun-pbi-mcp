# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado semántico. **El contrato de las 34 tools originales nunca se rompe.**

---

## [1.0.0-rc.2] — 2026-07-31

Release candidate. 90 tools, 854 pruebas (2 omitidas, cada una con su condición documentada), contrato congelado.

### Añadido

- **Actualización real de páginas** (C2–C4). `apply_page_spec` sobre una página existente no hacía nada y devolvía éxito. Ahora despacha por desenlace explícito —`create`, `update`, `no_change`, `conflict`—, conserva el id de la página y el de cada visual equivalente, y ofrece `sync_mode` (`merge` por defecto, `replace` opcional).
- **Duplicación segura** (E4). `duplicate_page` copiaba visuales con ids nuevos sin remapear nada: interacciones, grupos y drillthrough seguían apuntando a la página original. Ahora se construye el mapa completo `old_id → new_id` y un id que no se pueda remapear **bloquea** con `unsupported_page_structure`.
- **Recuperación desde journal** (`pbi_recover_from_journal`) con cinco estados, verificación byte a byte y recreación de directorios padre.
- **Retención de backups** (`pbi_purge_backups`), que cierra **R5**. Dry-run por defecto, raíz validada, solo journals reconocibles, enlaces simbólicos no seguidos, y siempre se conservan el más reciente y todos los pendientes.
- **Validador oficial de Microsoft** (E3.2) como segunda capa: `@microsoft/powerbi-report-authoring-cli@0.1.4`, offline, con comparación pre/post de diagnósticos.
- **Fixture PBIR representativo** (`tests/fixtures/rich.py`): interacciones, marcadores, drillthrough, visual personalizado, referencia rota, CRLF y esquema no publicado. Sintético y anonimizado.
- `docs/DUAL_MODE.md`, `docs/VALIDATION.md`, `docs/RELEASE_CHECKLIST.md`, `CONTRIBUTING.md`.

### Corregido

- **Atomicidad de workflows** (D). `repair_broken_references` abría una transacción por visual **y capturaba la excepción para continuar**; `normalize_report`, una por página. Y `__exit__` llamaba a `commit()` sin protección: si el commit fallaba, la excepción salía **sin revertir**.
- **Rotación del log** (N). `RotatingFileHandler` escupía un traceback por stderr en mitad de `doctor` y del contract check, que salían con código 0.
- **Limpieza de directorios tras el commit**: entre la escritura y la limpieza el informe quedaba inválido. Movida dentro de la transacción; el rollback recrea los padres.
- **`_pages_metadata` propagaba un `pages.json` sin `$schema`** en lugar de garantizarlo.
- **DLLs sin fijar** (J3). `latest_stable()` se tragaba la última versión sin hash, y extraía sobre `libs/`: un fallo a medias dejaba una mezcla de dos versiones.

### Limitaciones conocidas

- `visualContainer/2.10.0` y `bookmarks/2.0.0` **no están publicados** por Microsoft (404). Ni el validador interno ni el CLI oficial pueden comprobarlos; las escrituras sobre archivos que los declaren se bloquean. **G10 queda como excepción documentada.**
- `mode="both"` **bloqueado**; R15 abierto.
- `filters` e `interactions` del page spec se **rechazan** con `unsupported_feature`.

---

## [1.0.0] — 2026-07-30 (interna, no publicada)

Endurecimiento previo a la publicación: contrato de planes, idempotencia,
honestidad de la API, redacción de secretos y empaquetado.

### Corregido — Planes e idempotencia

- **Contrato único y versionado de planes** (`services/plan_contract.py`). `pbi_apply_page_spec(dry_run=True)` producía un plan que `pbi_apply_plan` no sabía aplicar: sin `affected_files` (`KeyError: 'files'`) y con una huella de *argumentos* en el campo de la huella de *estado*. El aplicador ahora despacha por `operation` y el sobre describe los bytes exactos que se escribirán. Un sobre de versión desconocida se rechaza con `plan_version_unsupported`.
- **Idempotencia real** (`services/idempotency.py`). Estaba documentada pero no implementada: nadie llamaba a `comprobar_request`/`guardar_resultado` y `guard()` inventaba un `request_id` en cada llamada. Ahora hay cuatro estados (`in_flight`, `succeeded`, `failed`, `compensated`), registro persistente con escritura atómica, y `request_id` opcional en las 34 tools que mutan.

### Corregido — Honestidad de la API

- `filters` e `interactions` del page spec se aceptaban y se **descartaban en silencio**. Ahora se rechazan con `unsupported_feature` indicando la ruta JSON exacta. La serialización sigue pendiente.
- `pbi_replace_visual_field` escribía cualquier referencia sin comprobarla, y conservaba el tipo de nodo del campo viejo (una medida podía acabar en un nodo `Column`). Ahora valida contra el modelo y devuelve `field_not_found`.
- El *capability check* de PBIR era informativo y nadie lo miraba; además declaraba soportado un informe **sin** versión. Ahora bloquea con `pbir_version_unsupported` (fail-closed).
- La exportación de DAX decía «resultado completo» cuando ya venía truncado por filas y por bytes.

### Corregido — Seguridad y robustez

- `ConnectionFailedError` devolvía la connection string entera y `DaxQueryError` 2000 caracteres de la consulta. `services/redaction.py` deja el destino, la longitud y un prefijo corto.
- `max_rows`, `max_bytes` y `timeout_seconds` no se validaban: cero, negativos y valores desproporcionados llegaban al motor.
- El puntaje de auditoría medía el tamaño del informe, no su calidad (el PB4 real sacaba 0). Normalizado por reglas aplicables, objetos evaluados, severidad y tope por regla.

### Corregido — Calidad y empaquetado

- Tres aserciones que no podían fallar (dos `or True` y un test vacío bajo un *skip* incondicional).
- `LICENSE` MIT real; `mcp` acotada a `>=1.28.1,<2` con test de compatibilidad, porque el servidor depende del atributo privado `_mcp_server.version`.
- Se prueba también el **sdist**: construcción e instalación en un entorno limpio.

---

## [1.0.0] — 2026-07-30

Primera versión completa. 88 tools, contrato congelado.

### Añadido — Plataforma (Macrofase A)

- **Envelope de respuesta uniforme y aditivo**: `status`, `request_id`, `operation`, `duration_ms`, `warnings`, `side_effects`. Conserva `ok` y todos los campos previos.
- Estados: `success`, `warning`, `planned`, `error`, `conflict`, `rollback_incomplete`.
- **Logging JSON a stderr** con redacción: de DAX, filas, expresiones y rutas solo se registra su forma, nunca su contenido.
- **Idempotencia** por `request_id`; reutilizarlo con otros argumentos es `request_id_conflict`.
- **Planes con `plan_token`** que capturan el estado; si el proyecto cambia, el plan se rechaza (`plan_token_stale`).
- Tools: `pbi_health_check`, `pbi_capabilities`, `pbi_session_info`, `pbi_list_pending_journals`, `pbi_inspect_journal`, `pbi_plan_change`, `pbi_apply_plan`.

### Añadido — Modelo semántico (Macrofase B)

- **Exploración que funciona igual en vivo y sobre TMDL**: resumen, búsqueda (también dentro del DAX), dependencias directas, transitivas e inversas.
- Extracción de referencias con escáner léxico: una referencia escrita dentro de una cadena o un comentario no cuenta.
- **Auditoría del modelo** con 13 reglas de identificador estable, evidencia y `auto_fix_available`.
- **DAX con límites reales**: `max_bytes`, `timeout_seconds`, `export`, tipos por columna y estadísticas que distinguen truncamiento por filas o por tamaño.
- Tools: `pbi_model_summary`, `pbi_search_model`, `pbi_get_object`, `pbi_measure_dependencies`, `pbi_column_dependencies`, `pbi_list_hierarchies`, `pbi_list_roles`, `pbi_list_perspectives`, `pbi_list_partitions`, `pbi_audit_model`, `pbi_list_audit_rules`.

### Añadido — Autoría PBIR (Macrofase C)

- **CRUD completo de visuales**: duplicar (conservando campos, formato y filtros), eliminar, título, orden Z, reemplazar campo, copiar formato.
- **CRUD de páginas**: duplicar con todos sus visuales, eliminar actualizando orden y página activa, renombrar, reordenar.
- **Motor de layout determinista**: detecta solapamientos, fuera de lienzo, tamaños mínimos, márgenes, separaciones y orden Z; alinea, distribuye y normaliza.
- Tools: 16, de `pbi_get_visual` a `pbi_normalize_page_layout`.

### Añadido — Spec declarativo (Macrofase D)

- **Schema 1.0 versionado**, con errores que traen **JSON path** (`$.visuals[2].fields.values[0]`).
- Resolución contra el modelo: una referencia inexistente o **ambigua** se rechaza.
- **IDs deterministas** con semilla.
- Flujo completo: building blocks → spec → validar → preview → diff → plan → apply → verificar → rollback.
- 6 presets: `executive`, `financial`, `sales`, `operations`, `evm`, `detail`.

### Añadido — Auditoría integral (Macrofase E)

- `pbi_audit_project` combina modelo, informe y layout, con puntaje **por dominio** y resumen ejecutivo.
- Salidas en JSON, Markdown y HTML (con escapado verificado).
- **Autofixes seleccionables**: `plan_fixes` exige reglas explícitas. No existe "arreglar todo".

### Añadido — Workflows (Macrofase F)

- 8 workflows orientados a resultado, que componen servicios internos (nunca tools decoradas, verificado por AST).
- Cada uno recorre análisis → plan → preview → apply → verificación → reporte, con `dry_run` por defecto.

### Seguridad (Fase 1A y derivadas)

- **Rutas acotadas** al proyecto, con semántica real de Windows: UNC, `\\?\`, `\\.\`, `C:relativa`, ADS de NTFS, nombres reservados, junctions y revalidación anti-TOCTOU.
- **DAX de solo lectura**, fail-closed: solo `EVALUATE`, `DEFINE…EVALUATE` y DMVs de `$SYSTEM`. Sin escape.
- **Política estricta de Power BI Desktop**: `open` y `unknown` bloquean la escritura PBIR.
- **Transacción compensada** con journal, fingerprints sha256 verificados tres veces y rollback que **no pisa cambios externos**.
- **Backups** con destino validado (nunca dentro del `.pbip`), identificación por hash y manifiesto verificable.
- **Sesiones**: se detecta la obsoleta y la que reutilizó el puerto.

### Cambiado

- `mode="both"` **deshabilitado** en las 6 tools duales: `live` necesita Desktop abierto y `pbip` lo necesita cerrado. Antes aplicaba `live` y fallaba en `pbip`, dejando estado parcial.
- `pbi_run_dax` acepta `max_bytes`, `timeout_seconds` y `export` (opcionales).

### Corregido

- El rollback dejaba directorios de página vacíos y huérfanos.
- `os.replace` fallido dejaba un `.tmp` dentro del `.pbip`.
- `pbi_hide_columns` llamaba a otra tool decorada: los errores se volvían datos y el lote reportaba `ok:true` con fallos dentro.
- Una excepción .NET cruda de `SaveChanges` escapaba sin compensar el disco.
- Empaquetado: faltaban `services*` y `reporting` en `pyproject.toml`.
- `doctor.py` tenía el número de tools codificado a mano.

---

## [0.1.0] — 2026-07-07

Versión inicial: 34 tools, capa en vivo (ADOMD/TOM) y capa en disco (TMDL/PBIR).
