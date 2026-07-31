# Contribuir a Horizun PBI MCP

Este servidor escribe en los proyectos de Power BI de otras personas. Un fallo aquí no da un error: **corrompe un informe** y el usuario lo descubre al abrirlo, lejos de la operación que lo causó. Las reglas de abajo existen por eso.

Las reglas operativas completas están en [`AGENTS.md`](AGENTS.md) y tienen prioridad.

---

## Antes de tocar nada

```bash
python -m pytest -q
python scripts/doctor.py
python -m tests.contract_utils
```

Los tres en verde. **No se construye sobre rojo.**

## El contrato MCP es intocable

Las tools están congeladas en `tests/golden/tools_v1.json`.

**Sin aprobación explícita, prohibido:** eliminar o renombrar una tool, eliminar un parámetro, añadir uno **obligatorio**, cambiar un tipo o un valor por defecto, cambiar la forma de la respuesta.

**Permitido:** añadir tools, añadir parámetros **opcionales con default**, añadir campos al dict de respuesta, mejorar descripciones.

Tras un cambio deliberado y aprobado:

```bash
python -m tests.contract_utils --write
```

Nunca digas «el contrato no cambió» si regeneraste el golden. Reporta **rupturas (0)** y **compatibles (N)** por separado.

## Invariantes

1. **stdout es el canal JSON-RPC.** Todo log va a stderr o a archivo. Un `print()` de depuración rompe la conexión del cliente.
2. **Nunca sobrescribir un JSON que no parsea.** Si no se puede leer, se aborta.
3. **Toda escritura sobre el proyecto del usuario:** backup antes, relectura después, rollback si falla.
4. **Ninguna ruta de escritura sale del proyecto activo.**
5. **No se inventan campos.** Si no existe, se informa; no se adivina.
6. **Las destructivas exigen `confirm=true`.**
7. **Preferir clonar una plantilla real** antes que construir JSON de visual a mano.
8. **Fail-closed.** Ante la duda, bloquear. Un `unsupported_feature` es mejor que una escritura a ciegas.

## Una transacción por operación lógica

Prohibido:

- transacción dentro de un `for`;
- **llamar en bucle a una función que abre su propia transacción** (el caso que el chequeo léxico no ve);
- capturar una excepción para continuar tras una mutación fallida;
- devolver `ok:true` con suboperaciones fallidas;
- que una tool decorada llame a otra tool decorada.

El patrón correcto: **compilar todos los cambios en memoria** → calcular archivos afectados → **una** transacción → validar → commit → verificar.

```bash
python -m pytest tests/test_workflow_atomicity.py -q
```

Incluye dos chequeos estáticos que fallan si alguien reintroduce el patrón.

## Pruebas

Una prueba que no puede fallar es peor que ninguna: da confianza falsa.

**Prohibido:** `or True`, asserts sobre constantes, mocks que verifican su propio valor, `except` demasiado amplios, tests sin asserts, skips sin motivo.

**Toda corrección de defecto necesita una prueba de regresión que falle contra el commit anterior y pase con el arreglo.** Compruébalo:

```bash
git worktree add --detach /tmp/regresion <commit-anterior>
cp tests/test_lo_nuevo.py /tmp/regresion/tests/
cd /tmp/regresion && python -m pytest tests/test_lo_nuevo.py
```

Si pasa ahí, la prueba no prueba nada.

**Prepara la precondición.** El fixture `minimal` no tiene interacciones ni referencias: una prueba de duplicación sobre él pasa sin comprobar nada. Usa `tests/fixtures/rich.py` o construye el escenario.

**Path traversal:** el "afuera" se crea **dentro del `tmp_path` de pytest** (`synthetic.outside_marker_dir()`). Jamás una ruta real del equipo.

## Datos reales: nunca entran a git

| Nunca versionar | Sí versionar |
|---|---|
| `.pbix`, `.pbip` reales, `.Report/`, `.SemanticModel/` | `tests/fixtures/synthetic/**`, `tests/fixtures/rich.py` |
| `libs/` (DLLs de Microsoft) | `scripts/fetch_libs.py` + `libs_manifest.json` |
| `schemas_cache/`, `validator_cache/` | los manifiestos con URLs y hashes |
| `outputs/`, `backups/`, `*.log` | plantillas `*.example.*` |
| `.env`, `.mcp.json` | `.env.example` |

Los fixtures sintéticos **no contienen** nombres comerciales, datos, rutas ni GUID de ningún proyecto real. Hay pruebas que lo verifican.

## Dependencias

Versión **exacta** y **hash verificado antes de instalar**, en las tres cadenas: DLLs de Analysis Services, esquemas PBIR y CLI oficial de Microsoft.

Nunca `latest`, nunca `npx -y`, nunca descargar durante una operación normal. Fallo cerrado si el hash no coincide.

## Commits

- Sin remoto, sin `push`, sin publicar paquetes.
- **Un commit por fase**, temático y reversible.
- No mezclar correcciones funcionales con documentación o limpieza.
- El mensaje explica **qué estaba mal**, no solo qué se cambió.
- `a304e33` es el baseline: **no se reescribe**.

## Estilo

- Comentarios en el código: **por qué**, no qué. Si el código dice qué hace, el comentario sobra.
- Nombres y mensajes en español, como el resto del repositorio.
- Los mensajes de error dicen **qué pasó, dónde y qué hacer**. Nunca incluyen valores del informe del usuario ni rutas personales.
