# Reglas del repositorio para agentes

Instrucciones operativas para cualquier agente (o persona) que modifique Horizun PBI MCP.
Tienen prioridad sobre cualquier costumbre general.

---

## 1. Antes de tocar nada

```bash
python -m pytest -q                    # debe pasar en verde
python scripts/doctor.py               # debe salir con codigo 0
```

Si el baseline ya está roto, **arréglalo o repórtalo antes** de añadir nada. No se construye sobre rojo.

---

## 2. El contrato MCP es intocable

Las **34 tools** del baseline están congeladas en `tests/golden/tools_v1.json`.

**Prohibido sin aprobación explícita:**
- eliminar una tool
- renombrar una tool
- eliminar un parámetro
- añadir un parámetro **obligatorio**
- cambiar el tipo de un parámetro
- cambiar un valor por defecto
- cambiar la forma de la respuesta

**Permitido:**
- añadir tools nuevas
- añadir parámetros **opcionales con default**
- añadir campos nuevos al dict de respuesta
- mejorar descripciones

Comprobar en cualquier momento:

```bash
python -m tests.contract_utils
```

Devuelve 0 si no hay rupturas, 1 si las hay, con un informe que dice **qué** cambió y **si rompe compatibilidad** — no un volcado de dos JSON.

Tras un cambio deliberado y aprobado:

```bash
python -m tests.contract_utils --write
```

---

## 3. Invariantes que ninguna fase puede romper

1. **stdout es el canal JSON-RPC.** Todo log va a stderr o a fichero. Un `print()` de depuración rompe la conexión del cliente.
2. **Nunca sobrescribir un JSON que no parsea.** Si no se puede leer, se aborta.
3. **Toda escritura sobre el proyecto del usuario:** backup antes, relectura después.
4. **Ninguna ruta de escritura sale del proyecto activo.** Usa `ensure_within_base()`.
5. **No se inventan campos** que no existan en el modelo. Si un campo no existe, se informa; no se adivina.
6. **Las tools destructivas exigen `confirm=true`.**
7. **Preferir clonar una plantilla real** antes que construir JSON de visual a mano.

---

## 4. Datos reales: nunca entran a git

| Nunca versionar | Sí versionar |
|---|---|
| `.pbix`, `.pbip` reales, `.Report/`, `.SemanticModel/` | `tests/fixtures/synthetic/**` |
| `libs/` (DLLs) | `scripts/fetch_libs.py` |
| `outputs/`, `backups/`, `*.log` | plantillas `*.example.*` |
| `.env`, `.mcp.json`, credenciales | `.env.example`, `.mcp.json.example` |
| `tests/fixtures/local/` | `docs/`, `tests/` |

Antes de cualquier commit:

```bash
git status --short --ignored
```

Los fixtures sintéticos **no contienen** nombres comerciales, datos ni información de ningún proyecto real. Si necesitas estructura PBIR real, usa el fixture local ignorado (`scripts/setup_local_fixture.py`) y **jamás lo promuevas a `synthetic/` sin anonimizar y sin revisión**.

---

## 5. Pruebas

| Nivel | Dónde | Regla |
|---|---|---|
| Unitarias | `tests/test_*.py` | Sin E/S real fuera de `tmp_path` |
| Fixtures sintéticos | `tests/fixtures/synthetic/` | Usar `materialize(tmp_path)`. **Nunca** escribir sobre el fixture versionado |
| Contrato MCP | `tests/test_tool_contract.py` | Debe pasar siempre |
| En vivo | marcadas `@pytest.mark.skip` o `live` | No se ejecutan solas. Nunca destructivas sobre un modelo real |
| Fixture local | marcadas `local_fixture` | Sólo lectura. Se omiten si la carpeta no existe |

**Pruebas de path traversal:** el "afuera" debe crearse **dentro del `tmp_path` de pytest** (`synthetic.outside_marker_dir()`). Jamás apuntar a una ruta real del equipo, ni siquiera para demostrar un fallo.

---

## 6. Git y contribuciones

Este es el repositorio **público**. Se contribuye por **ramas y pull requests**.

- **Nunca `force-push` a `main`.** Rehacer la historia publicada rompe cualquier clon y cualquier referencia a un commit.
- Una rama por cambio, con un nombre que diga qué hace.
- **Antes de abrir un PR**, los tres en verde:

  ```bash
  python -m pytest -q
  python scripts/doctor.py
  python -m tests.contract_utils
  ```

  El CI los repite en `windows-latest` con Python 3.10 y 3.13. Un PR en rojo no se revisa.

- **El contrato MCP está congelado.** Ver la sección 2: añadir es libre, cambiar o quitar no.
- **Nunca se versionan datos reales**: ni `.pbix`, ni `.pbip` de nadie, ni DLLs, ni `outputs/`, ni `backups/`, ni `.env`, ni `.mcp.json`. Ver la sección 4.
- Un commit por cambio lógico. El mensaje explica **qué estaba mal**, no solo qué se tocó.
- No se publica en PyPI desde este repositorio.

Guía extendida para contribuir: [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## 7. Estado de los riesgos

Tres estados, y solo tres: **cerrado**, **parcialmente cerrado**, **pendiente**.
Detalle en `docs/PHASE_1A_DESIGN.md`.

| Id | Riesgo | Estado |
|---|---|---|
| R2 | Corromper un `.pbip`: sin detección de Desktop, sin verificación posterior | **Cerrado** (1A) |
| R3 | Traversal de escritura en PBIR | **Cerrado** (1A) |
| R5 | Backups sin ubicación validada ni retención | **Cerrado** (F/R5) — ubicación validada, identificación por hash, manifiesto, y purga con `pbi_purge_backups`: dry-run por defecto, raíz validada, solo journals reconocibles, enlaces simbólicos no seguidos, y se conservan siempre el más reciente y todos los pendientes |
| R6 | `session.json` apuntando a un puerto muerto o reutilizado | **Cerrado** (1A) |
| R7 | `pbi_run_dax` sin validación de solo lectura | **Cerrado** (1A) |
| R11 | Residuo `.tmp` dentro del `.pbip` al fallar `os.replace` | **Cerrado** (1A) |
| R12 | Rollback dejaba directorios de página vacíos y huérfanos | **Cerrado** (1A.1) |
| R13 | **Atomicidad de flujos PBIR multiarchivo** | **Parcialmente cerrado** — ver abajo |
| R14 | Empaquetado incompleto (`services*`, `reporting`) | **Cerrado** (1A.1), con prueba de wheel instalado |

### R13 — atomicidad multiarchivo, en detalle

**No marques este riesgo como cerrado mientras quede un solo flujo sin cubrir.**

| Flujo | Archivos | Estado |
|---|---|---|
| `pbi_create_page_from_spec` | page.json + pages.json + N visual.json | ✅ una transacción |
| `pbi_arrange_visuals` | N visual.json | ✅ una transacción |
| `pbi_generate_report_page` | page.json + pages.json + N visual.json | ✅ una transacción |
| `pbi_create_html_visual` | report.json + visual.json | ✅ una transacción |
| `pbir_writer.create_page` | page.json + pages.json | ✅ una transacción |
| `pbi_hide_columns` (`pbip`) | N archivos TMDL | ✅ una transacción (1A.2) |
| `pbi_hide_columns` (`live`) | N columnas TOM | ✅ un solo `SaveChanges` (1A.2) |

#### Ampliación del inventario (Fase D) — flujos que faltaban

El inventario de arriba se hizo con búsquedas **léxicas** (`grep` de `project_transaction` dentro de un `for`). Ese método tiene un punto ciego: **la transacción se abre dentro de la función llamada, no dentro del bucle**. Dos workflows de alto nivel caían justo ahí y no aparecían:

| Flujo | Qué hacía | Evidencia |
|---|---|---|
| `pbi_repair_broken_references` | Una transacción **por visual**, dentro de un `for`, con `except Exception` que **seguía adelante**. Si fallaba el quinto, los cuatro anteriores quedaban confirmados y la tool devolvía `ok:true` con una lista de fallidos. | `workflows.py:222` (antes) |
| `pbi_normalize_report` | Una transacción **por página**. Atómico dentro de cada una, pero si fallaba la tercera, las dos primeras quedaban reacomodadas. | `workflows.py:273` (antes) |

Y un tercer defecto, en la frontera del **commit**:

| Flujo | Qué hacía |
|---|---|
| `txn._ProjectTransactionCM.__exit__` | Llamaba a `commit()` sin protección. Si el commit fallaba por sí mismo (manifiesto, disco, permisos), la excepción salía **sin revertir**: los archivos quedaban escritos y la operación parecía fallida. |

**Corregido en la Fase D**: se factorizaron `pbir_writer.plan_visuals_bulk()` y `pbir_edit.plan_replace_visual_field()` (puros, sin escribir), los dos workflows compilan todo y escriben en **una** transacción, y `__exit__` revierte si el commit falla.

`tests/test_workflow_atomicity.py` inyecta fallos en primera / intermedia / última escritura, validación previa, commit y compensación, y exige restauración byte a byte y cero directorios huérfanos. **Seis de esas pruebas fallan contra el commit anterior.**

Incluye además dos chequeos estáticos: uno léxico (transacción dentro de un `for`) y **uno que cubre el punto ciego**: un bucle que llama a una función que abre su propia transacción.

**Verificado también**: exactamente **1 `SaveChanges` por función**, 7 en total, contando nodos `Call` del AST y no ocurrencias de texto —un `grep` cuenta 6 en `set_columns_hidden_bulk` porque los menciona la docstring—. La afirmación original de R13 se sostiene.

**R13 (atomicidad de un solo destino): cerrado.** Todos los flujos de un mismo destino son transacciones únicas o un solo `SaveChanges`.

---

## 7 bis. R15 — consistencia dual: **ABIERTO**

Este riesgo **no está cerrado y no se cerrará en la Fase 1A.** Es un límite del sistema.

| Modo | Requisito | Estado |
|---|---|---|
| `live` | Power BI Desktop **abierto** y sesión válida | ✅ Disponible |
| `pbip` | Proyecto **cerrado** o verificablemente seguro | ✅ Disponible |
| `both` | **Requisitos mutuamente incompatibles** | 🚫 **Bloqueado en 1A** |

`live` habla con `msmdsrv.exe`, que solo existe si Desktop está abierto. `pbip` escribe archivos que Desktop sobrescribe al guardar, así que la política estricta lo bloquea si Desktop está `open` o `unknown`. **No hay ningún estado del sistema en el que ambos destinos puedan escribirse con seguridad en una sola llamada.**

Lo que hacía antes: aplicaba `live` primero, `pbip` después. Con Desktop abierto — el único estado en que `live` es posible — el resultado era un **estado parcial determinista**: 1 `SaveChanges` ejecutado, columna oculta en memoria, disco intacto, `consistent: False`.

**Ahora:** las seis tools duales rechazan `mode="both"` con `dual_mode_not_safely_available` **antes de cualquier efecto** — antes de conectar a TOM, de validar contra el motor, de crear journal, de leer para planificar o de tocar un archivo. Sin bypass por variable de entorno.

Las seis: `pbi_create_measure`, `pbi_update_measure`, `pbi_delete_measure`, `pbi_set_column_visibility`, `pbi_hide_columns`, `pbi_set_relationship_direction`.

### El coordinador compensado sigue ahí, como mecanismo interno

`_apply_both_compensated()` en `tools/model_edit_tools.py` implementa disco→memoria con compensación. **No es alcanzable desde la tool pública** y no justifica que `both` se acepte. Se conserva con pruebas unitarias directas porque la Fase 1B tendrá que decidir entre: workflow en dos etapas, persistir solo por TOM y dejar que el usuario guarde, abandonar `both`, u otra coordinación segura.

### Taxonomía de errores del coordinador

| Código | Cuándo | Requiere intervención |
|---|---|---|
| `bulk_apply_failed` | Falló y la compensación dejó **todo** como estaba | No |
| `bulk_partially_applied` | La compensación quedó incompleta o en conflicto | **Sí**, con el journal |

No se informa "parcial" cuando la restauración fue completa.

### Cómo auditar esto tú mismo

No te fíes de la lista de arriba. Los cinco patrones a buscar:

```bash
# 1. Una tool decorada llamando a otra tool decorada
grep -rn "@mcp.tool" -A40 src/tools/ | grep -E "pbi_[a-z_]+\("
# 2. Escrituras dentro de bucles
grep -rnE "for |while " -A6 src/ | grep -E "write_|SaveChanges|create_page"
# 3. Transacciones abiertas dentro de bucles  ← el patrón malo
grep -rn "project_transaction\|with transaction" src/
# 4. Varios SaveChanges en una misma función
grep -rn "SaveChanges" src/powerbi/
# 5. backup_before_edit (no restaura; solo para rutas no migradas)
grep -rn "backup_before_edit" src/
```

Un `for` **dentro** de una transacción es correcto. Una transacción **dentro** de un `for` no lo es.

**Nunca hagas que una tool llame a otra tool decorada.** `guard()` convierte los errores en datos: el bucle continúa, el resultado exterior dice `ok:true` y los fallos quedan enterrados en la lista. Extrae un servicio sin decorar y que ambas tools lo envuelvan.

No marques ningún riesgo como cerrado sin una prueba de regresión que **falle antes** del arreglo y pase después.
