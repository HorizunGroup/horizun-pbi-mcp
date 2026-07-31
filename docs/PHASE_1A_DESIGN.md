# Fase 1A — contención de riesgos críticos

_Implementada sobre el commit de Fase 0 `82bc6c9`. Sin cambios en el contrato de las 34 tools._

Esta fase no añade funcionalidad. Cierra cinco riesgos que permitían escribir fuera del proyecto, pisar cambios concurrentes, dejar un `.pbip` a medias o ejecutar DAX arbitrario.

---

## 1. Módulos nuevos

| Módulo | Responsabilidad única |
|---|---|
| `src/services/paths.py` | Ninguna ruta de lectura/escritura sale del proyecto activo |
| `src/services/dax_guard.py` | Solo se ejecutan consultas reconocidas como de solo lectura |
| `src/services/project_state.py` | No se escribe PBIR si Desktop puede tener el proyecto abierto |
| `src/services/txn.py` | Transacción compensada: journal, verificación y rollback |

---

## 2. Rutas (`paths.py`)

Dos problemas, dos funciones:

- **`safe_identifier()`** — un id de página o de visual es un *identificador*, no una ruta. Rechaza, **antes de tocar el disco**: separadores, `.`/`..`, rutas absolutas, sintaxis de unidad (`C:\x` y `C:x`), UNC (`\\srv\r`), extendidas (`\\?\`), de dispositivo (`\\.\`), ADS de NTFS (`archivo.json:stream`), nombres reservados (`CON`, `NUL`, `AUX`, `COM1`…), componentes vacíos y componentes con punto o espacio final.
- **`assert_not_path_syntax()`** — más permisiva, para nombres visibles: admite `"Resumen ejecutivo 2026"` pero rechaza cualquier sintaxis de ruta.
- **`ensure_contained()`** — resuelve enlaces (junctions y reparse points) en ambos extremos y compara con `os.path.normcase`, porque NTFS no distingue mayúsculas. Detecta cambio de unidad.
- **`assert_still_contained()`** — misma comprobación, con nombre propio, para llamarla **justo antes de escribir**: un junction puede cambiar de destino entre la validación y la escritura.

> `Path('base') / 'C:/otro'` devuelve `C:/otro`. Ese es el motivo de validar cada componente antes de unirlo, y no solo normalizar la cadena resultante.

---

## 3. Clasificador DAX (`dax_guard.py`)

**No es un parser de DAX.** Es un clasificador léxico deliberadamente estrecho, `fail-closed`.

**Paso 1 — escaneo léxico.** Se recorre el texto reconociendo comentarios (`//`, `--`, `/* */` sin anidar), cadenas (`"…"`, escape `""`), identificadores citados (`'…'`, escape `''`) y entre corchetes (`[…]`, escape `]]`). Su contenido se sustituye por un centinela opaco. Un delimitador sin cerrar → rechazo.

**Paso 2 — clasificación**, solo sobre el residuo. La consulta se permite si su **estructura completa** encaja en una forma reconocida:

| Forma | Ejemplo |
|---|---|
| `evaluate` | `EVALUATE TOPN(10, Ventas)` |
| `define_evaluate` | `DEFINE MEASURE T[M] = 1 EVALUATE ROW("v", T[M])` |
| `dmv_select` | `SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES` |

Se rechaza: XMLA (`<`), palabras de modificación como token suelto, `;` (podría encadenar sentencias), `DEFINE` sin `EVALUATE`, `SELECT` cuyo `FROM` no sea exactamente `$SYSTEM.<rowset>`, mezclas `EVALUATE`+`SELECT`, tokens concatenados (`EVALUATEX`) y **todo lo ambiguo**.

Como los literales se neutralizan primero, `EVALUATE ROW("DROP TABLE", 1)` sigue siendo lectura, y `SELECTCOLUMNS` no se confunde con `SELECT`.

**No hay escape.** No existe ninguna variable de entorno que relaje la política; hay una prueba que lo verifica.

---

## 4. Proyecto abierto (`project_state.py`)

**Límite honesto:** esto **no impide** que Power BI Desktop sobrescriba el informe después. Desktop tiene su copia en memoria y al guardar escribe encima. Lo único que se consigue es no escribir *nosotros* cuando hay indicios de que está abierto. El mensaje de error lo dice explícitamente.

**Señales, todas de solo lectura.** No se renombra, no se escribe un temporal, no se intenta un `os.replace` de prueba sobre archivos reales:

| Situación | Estado |
|---|---|
| Ni `PBIDesktop.exe` ni `msmdsrv.exe` | `closed` (alta) |
| `msmdsrv` sin `PBIDesktop` atribuible | `unknown` |
| `PBIDesktop` con el proyecto en `cmdline()` o en `open_files()` | `open` (alta) |
| `PBIDesktop` presente pero el sistema deniega la inspección | `unknown` |
| `PBIDesktop` inspeccionable y ninguno referencia el proyecto | `closed` (media) |

**Política (estricta, no desactivable):** solo `closed` permite escribir. `open` y `unknown` bloquean. El modo `warn` y la confirmación por llamada quedan para 1B.

Hay una caché de **1 segundo** sobre el escaneo de procesos: enumerar procesos cuesta ~150 ms y una página con cinco visuales pagaría cinco escaneos. La ventana es mínima y la transacción revalida el fingerprint de cada archivo de todos modos.

---

## 5. Transacción compensada (`txn.py`)

El sistema de archivos **no ofrece atomicidad multiarchivo**. Esto es una transacción *compensada*: entre el primer y el último `os.replace` existe una ventana en la que el proyecto está a medias. Lo que se garantiza es que la ventana es corta, que el journal permite volver atrás y que **nunca se reporta éxito si el rollback no fue limpio**.

```
PLAN      fingerprint (sha256 + tamaño, o "absent") de cada objetivo
SNAPSHOT  copiar los objetivos al journal + manifiesto (status: open)
PRE-CHECK re-verificar el fingerprint justo antes de cada reemplazo
WRITE     temporal → flush → fsync → validar → os.replace → limpiar en finally
POST      releer del disco y comparar con lo que se pretendía escribir
COMMIT    cerrar el manifiesto   |   FALLO → ROLLBACK
```

El fingerprint se verifica **tres veces**. Nunca se usan marcas de tiempo. `absent` es un estado de primera clase: si un archivo que planeábamos crear apareció entre medias, es una colisión.

### Rollback consciente de concurrencia

Un archivo solo se toca si su contenido actual **sigue siendo el que escribimos nosotros**:

| Situación | Resultado |
|---|---|
| Preexistente, sin cambios externos | `restored` (byte a byte, verificado) |
| Creado por la transacción, sin cambios externos | `restored` (se elimina) |
| Cambió después de nuestra escritura | `rollback_conflict` — **no se toca** |
| Nunca se llegó a escribir | `unchanged` |
| Se intentó restaurar y falló | `rollback_failed` |

Si algún archivo queda en `rollback_conflict` o `rollback_failed`, el error propagado es `RollbackIncompleteError`, con el journal y el detalle por archivo — no un fallo normal.

### Destino de backups

`resolve_backup_root()` **falla de forma accionable antes de tocar el proyecto** si:

- no hay `PBI_MCP_BACKUPS_DIR` configurado (no se elige un destino por defecto en silencio);
- el destino cae dentro del `.pbip`, del `.Report` o del `.SemanticModel`;
- el proyecto está dentro de la carpeta de backups (recursión);
- el destino no es escribible.

Cada proyecto usa un subdirectorio `<nombre>_<hash12>` donde el hash es `sha256` de su ruta absoluta normalizada: **dos `Demo.pbip` en carpetas distintas nunca comparten backups**.

**No hay purga automática en 1A**, y nunca se borran backups preexistentes del usuario.

---

## 6. Sesiones (`desktop_discovery.py`, `config.py`)

Que el puerto vuelva a estar abierto no prueba nada: Desktop asigna un puerto nuevo en cada arranque y el sistema reutiliza puertos y PIDs.

`ActiveModel` guarda ahora la **identidad** de la sesión: `pid`, `process_started`, `workspace` y `session_fingerprint` (hash de puerto + pid + hora de arranque + catálogo).

`verify_model()` devuelve:

| Estado | Cuándo |
|---|---|
| `ok` | todo coincide |
| `stale` | el puerto ya no existe o no responde |
| `mismatch` | el puerto lo ocupa otro proceso, el PID se reutilizó, o sirve otro catálogo |

`require_active_model()` lo comprueba y lanza `StaleSessionError` con un mensaje accionable. Una sesión recargada de `session.json` arranca **sin verificar**: no se confía en lo guardado.

Además, el descubrimiento hace un **pre-chequeo TCP de 0,5 s** antes de intentar ADOMD. Sin él, los archivos `msmdsrv.port.txt` huérfanos (había **5** en este equipo) provocaban una conexión completa con timeout largo contra cada puerto muerto. **No se borra ninguno**: solo se marcan `unreachable`.

---

## 7. Durabilidad y residuos

`durable_write()` centraliza la escritura: temporal en el mismo directorio → `flush` → `fsync` → validar el temporal → `os.replace` → **limpiar el temporal en `finally`**.

Esto corrige un fallo real: en Windows `os.replace` falla con `WinError 5` si otro proceso tiene el destino abierto —justo el escenario de Desktop abierto— y antes dejaba un `visual.json.tmp` huérfano **dentro del `.pbip` del usuario**. El original siempre quedaba intacto; el problema era la basura.

No se hace `fsync` del directorio: Windows no lo admite.

---

## 7 bis. Composición bulk (Fase 1A.1)

La Fase 1A dejó los escritores individuales seguros, pero los flujos que escriben **varios** archivos seguían encadenando N transacciones de un archivo. Una página de 5 visuales que fallaba en el 3.º dejaba 2 escritos y una página a medias.

### API bulk, en la capa PBIR

Ninguna tool coordina temporales, journals ni rollback: esa responsabilidad vive en `pbip/pbir_writer.py` y `services/txn.py`.

| Función | Archivos que abarca |
|---|---|
| `create_page_with_visuals(...)` | `page.json` + `pages.json` + N × `visual.json` |
| `update_visuals_bulk(...)` | N × `visual.json` |
| `write_visual_with_registration(...)` | `report.json` + `visual.json` |

Todas reciben contenido **ya validado y construido**, y ejecutan **una sola transacción**. Las APIs individuales (`write_visual`, `update_visual_position`, `create_page`, `add_public_custom_visual`) siguen existiendo para las tools de un solo objeto.

### Orden obligatorio en los flujos

```
1. validar el spec completo
2. resolver posiciones FINALES
3. construir TODOS los visuales en memoria   ← aquí falla si algo no se puede armar
4. calcular todos los archivos destino
5. abrir UNA transacción
6. escribir el conjunto
7. verificar
8. rollback completo ante cualquier fallo
```

El paso 3 es el que garantiza que **un fallo construyendo el visual N no produce ninguna escritura**: la página no llega a crearse. Y en `pbi_generate_report_page` los visuales se construyen ya con su posición final, en vez de escribirlos con una provisional para reposicionarlos después.

### Límites reales de atomicidad

- **No hay atomicidad de sistema de archivos.** Entre el primer y el último `os.replace` el proyecto está a medias. Lo que hay es compensación por journal.
- Una operación lógica produce **un solo journal**, no N backups completos. El journal conoce **todos** los archivos afectados.
- El rollback cubre archivos **modificados, creados y eliminados**, y además retira los **directorios** que la transacción creó y quedaron vacíos (antes dejaba un `<pageId>/` huérfano sin `page.json`, que el propio lector de páginas interpretaba mal).

### Comportamiento ante conflicto

Si alguien modifica externamente un archivo:

| Momento | Resultado |
|---|---|
| Antes de que lo escribamos | La transacción **aborta** en el pre-chequeo; se conserva el cambio externo |
| Después de escribirlo, antes de verificar | La verificación posterior lo detecta; el archivo queda marcado `rollback_conflict` |
| Durante el rollback | **No se pisa**; se marca `rollback_conflict` y se conserva el journal |

En todos esos casos la operación termina en `RollbackIncompleteError`, **nunca en éxito**.

### Empaquetado

`pyproject.toml` omitía `services*` en `packages.find.include` **y** `reporting` en `py-modules`. Un `pip install -e .` no lo revelaba, porque resuelve todo desde `src/`. La prueba de `tests/test_packaging.py` construye un wheel real, lo instala en un venv y verifica el arranque con 34 tools fuera del repositorio.

---

## 8. Estado de los riesgos

| Id | Riesgo | Estado |
|---|---|---|
| R2 | Corromper un `.pbip`: sin lock, sin `expected_state`, sin detectar Desktop | **Cerrado** en 1A |
| R3 | Traversal de escritura en PBIR | **Cerrado** en 1A |
| R5 | Backups sin retención ni ubicación validada | **Parcialmente cerrado**: ubicación validada y manifiesto; la purga queda fuera a propósito |
| R6 | `session.json` apuntando a un puerto muerto | **Cerrado** en 1A |
| R7 | `pbi_run_dax` sin validación de solo lectura | **Cerrado** en 1A |
| R11 | Residuo `.tmp` dentro del `.pbip` | **Cerrado** en 1A |
| R12 | Directorio de página vacío tras un rollback | **Cerrado** en 1A.1 |
| R13 | Atomicidad de flujos PBIR multiarchivo | **Parcialmente cerrado**: los 5 flujos PBIR son transacciones únicas; `pbi_hide_columns` (TMDL) sigue pendiente |
| R14 | Empaquetado incompleto | **Cerrado** en 1A.1, con prueba de wheel instalado |

## 7 ter. `pbi_hide_columns` (Fase 1A.2)

Era el último flujo multiarchivo no atómico, y tenía un defecto más grave que el recuento de transacciones: **llamaba a otra tool decorada con `guard()`**. Los errores se convertían en datos, el bucle seguía, y el lote devolvía `ok:true` con los fallos enterrados en `results`.

### Corrección

- **`hide_columns_service()`**, sin decorar, en `tools/model_edit_tools.py`. Las tools envuelven servicios; nunca a otras tools.
- **Validación completa antes de escribir**: tipo de `columns`, cada `table` y `column` no vacíos, nombres válidos, duplicados detectados. Un fallo indica **índice, tabla y columna**, y no se escribe nada, no se llama a `SaveChanges` y no se crea journal.
- **Lote TMDL**: cada `.tmdl` se localiza y se lee **una vez**, los cambios se agrupan por archivo, se mutan en memoria y se escriben en **una sola transacción**.
- **Lote TOM**: una sola conexión, validación de todas las tablas y columnas, captura de `before_hidden`, y **un único `SaveChanges`** independientemente de N.
- **Duplicados exactos**: se aplican una vez, pero se reportan en todas sus posiciones. La operación es idempotente.
- **Lista vacía**: se conserva el comportamiento previo (no es un error).

### Semántica de `count`

`count` sigue siendo el **número de entradas solicitadas**, duplicados incluidos — igual que antes, cuando era `len(results)` con un resultado por iteración. `results` mantiene una entrada por solicitud, en el mismo orden, aunque internamente se agrupe por archivo. La lista de duplicados descartados va en `duplicates_ignored`, un campo **añadido**, no un cambio de significado.

### Qué garantiza TOM, y qué no

`SaveChanges()` envía el lote en una sola operación, pero **no es una transacción distribuida**. Si el motor rechaza el lote, los objetos en memoria pueden quedar modificados hasta que Power BI Desktop se recargue. Lo que sí se garantiza: no hay escrituras parciales por nuestra parte, y una validación fallida no persiste nada (`SaveChanges` se llama **cero** veces).

### `mode="both"`: compensado

```
1. validar AMBOS destinos            ← si lo vivo no valida, el disco ni se toca
2. escribir el lote TMDL (journal restaurable)
3. aplicar el lote en vivo (1 SaveChanges)
4. si falla → compensar el disco desde el journal
5. verificar la compensación
6. reportar el conflicto sin ocultarlo
```

Un detalle que costó encontrar: `SaveChanges` puede lanzar una **excepción .NET cruda**. Si escapaba sin envolver, la compensación no se ejecutaba y el disco quedaba modificado con el modelo en vivo intacto. Ahora se envuelve como `live_write_failed`, y el coordinador captura `Exception`, no solo `PowerBIMCPError`.

Un fallo total llega al `guard()` exterior como excepción de dominio (`bulk_partially_applied`), no como una lista de éxitos y errores.

---

## 7 quater. `mode="both"` bloqueado (Fase 1A.3)

Al probar el flujo público apareció una contradicción que las pruebas con dobles no revelaban:

```
live → necesita Power BI Desktop ABIERTO   (TOM habla con msmdsrv.exe)
pbip → necesita Desktop CERRADO            (política estricta de 1A)
```

**No existe ningún estado del sistema en el que ambos puedan escribirse con seguridad en una sola llamada.** Y como la implementación dual aplicaba `live` primero y `pbip` después, con Desktop abierto el resultado era un **estado parcial determinista**. Verificado sobre el código de `7adb725`:

```
resultado de _dual:  live aplicado: True | pbip aplicado: False
                     pbip_error: project_open_in_desktop | consistent: False
efectos reales:      SaveChanges: 1 | columna oculta en TOM: True | TMDL: sin cambios
```

### Matriz real de modos

| Modo | Requisito | Estado |
|---|---|---|
| `live` | Desktop abierto y sesión válida | **Disponible** |
| `pbip` | Proyecto cerrado o verificablemente seguro | **Disponible** |
| `both` | Requisitos mutuamente incompatibles | **Bloqueado en 1A** |

### Precondición central

`services/dual_mode.py` expone `assert_mode_is_safely_executable(mode)`, que se ejecuta **lo primero** en cada tool dual: antes de abrir una conexión TOM, de validar objetos contra el motor, de crear un journal, de leer para planificar o de tocar un archivo. La decisión vive en un solo sitio; ninguna tool la duplica.

También centraliza `normalize_mode()` y `run_dual()`, que estaban duplicados en `measure_tools` y `model_edit_tools` (deuda A2 de la auditoría). `run_dual` ya no ejecuta los dos lados aislando errores: propaga la excepción, en vez de convertirla en un `consistent: False` con la mitad del trabajo hecho.

**No hay bypass por variable de entorno.**

### El coordinador compensado, como mecanismo interno

`_apply_both_compensated()` se conserva y se prueba de forma directa, pero **no es alcanzable desde la tool pública** y no justifica aceptar `both`. La Fase 1B decidirá entre workflow en dos etapas, persistir solo por TOM, abandonar `both`, u otra coordinación.

### Taxonomía de errores corregida

| Código | Cuándo | Intervención |
|---|---|---|
| `bulk_apply_failed` | Falló y la compensación dejó **todo** como estaba (`applied_to: "ninguno"`) | No |
| `bulk_partially_applied` | Compensación incompleta o en conflicto | **Sí** |

Antes, una compensación limpia terminaba como `BulkPartialError` con `applied_to: "ninguno"` — semánticamente contradictorio: inducía a buscar a mano algo que no existía.

---

### Riesgos residuales

1. **`mode="both"` está bloqueado, no resuelto.** Límite del sistema, no del código. Es el riesgo **R15**, y sigue **abierto**: hoy no hay forma de aplicar un cambio a los dos destinos en una sola operación. El usuario debe elegir `live` (y guardar con Ctrl+S) o `pbip` (con Desktop cerrado).
2. **La ventana entre `os.replace`** del primer y el último archivo no es atómica a nivel de sistema de archivos. Si el proceso muere ahí, el journal permite recuperación **manual**; no hay reanudación automática al arrancar.
3. **La caché de 1 s** del estado del proyecto: para colarse, Desktop tendría que abrir el proyecto dentro de esa ventana.
4. **Sin purga de journals.** Se acumulan en la carpeta de backups hasta que se defina la política.
5. **`backup_before_edit`** sigue existiendo para rutas que no han migrado a transacción. No sabe restaurar.

---

## 9. Pendiente para 1B

- Envelope uniforme (`status/target/before/after/validation/backup/warnings`).
- `request_id` y `dry_run` expuestos como parámetros.
- `expected_state` suministrado por el cliente (concurrencia **entre** llamadas; en 1A es interno a cada operación).
- Modo `warn` y confirmación por llamada para la política de proyecto abierto.
- Enums en `mode`, `source`, `layout`, `direction`, `type`, `scope` — **con la categoría `CONTRATO RESTRINGIDO`** en el comparador: estrechar `string`→`enum` no es compatible por defecto.
- Logging estructurado general.
- Transacción única para las operaciones por lote de `page_builder.py` y `visual_tools.py` (hoy hacen N transacciones de un archivo).
