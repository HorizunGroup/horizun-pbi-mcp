# CONTRACT-003 — dossier de ratificación

**Estado: preparado, NO aplicado.** Nada de lo que hay aquí está en el código.
Los tres cambios rompen el contrato MCP congelado, y el contrato solo se rompe
con una ratificación humana registrada —esa es la regla que CONTRACT-001
estableció y que G2.5 vigila—.

Este documento existe para que la decisión se pueda tomar **con lo que hay que
saber delante**: qué cambia exactamente, a quién puede romper, qué pasa si se
deja como está, si hay una alternativa compatible, y qué se activaría después.

Vienen de CORE-004(a)(b)(c). El cuarto sub-hallazgo, (d), no rompía nada y está
cerrado desde el 2026-08-15.

| | |
|---|---|
| Gate bloqueado | G1.5 — «la anotación de riesgo describe el efecto real» |
| Categoría | `pendiente-ratificacion` en [`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md) |
| Quién decide | la persona responsable del producto |
| Qué NO hace falta | ningún entorno: es una decisión, no una VM |

---

## Cambio 1 — `confirm` exigido en `pbi_refresh_model` y `pbi_open_and_refresh`

### Contrato actual

```
pbi_refresh_model(tables?, request_id?)          annotations: destructiveHint=true
pbi_open_and_refresh(path?, ...)                 annotations: destructiveHint=true
```

Son las **únicas dos** tools de las 134 con `destructiveHint: true` y **sin**
parámetro `confirm`.

### Contrato propuesto

```
pbi_refresh_model(tables?, confirm: bool = False, request_id?)
pbi_open_and_refresh(path?, confirm: bool = False, ...)
```

### Diff de schema

| | Actual | Propuesto |
|---|---|---|
| Parámetros | sin `confirm` | `confirm: boolean, default false` |
| Requeridos | sin cambio | sin cambio |
| Anotaciones | `destructiveHint: true` | igual |
| Comportamiento | ejecuta | **rechaza** con `validation_error` si `confirm` no es `true` |

El parámetro es opcional con default, así que **el schema es aditivo**. Lo que
rompe es el **comportamiento**: toda llamada existente que hoy refresca, mañana
devuelve `ok: false`.

### A quién puede romper

Cualquier cliente o agente que ya llame a estas dos tools: la llamada sigue
siendo válida y deja de hacer nada. En un agente, eso se manifiesta como «pedí
un refresh y no pasó nada», que es peor que un error de schema porque no
detiene el flujo.

### Peligro de dejarlo como está

Un refresh puede tardar minutos, bloquea el modelo y **descarta lo que hubiera
en memoria sin guardar**. Es la definición de irreversible del propio producto,
y hoy se dispara sin preguntar. Un agente que decide por `destructiveHint` verá
`true` y confirmará con la persona; uno que decide por *«¿tiene `confirm`?»* —que
es lo que hacen varios— no verá nada que pedirle.

### Alternativa compatible

**Sí la hay, y es buena.** Añadir `confirm` con default `false` pero **no**
rechazar todavía: devolver un `warning` en el envelope —`confirmation_advised`—
durante una versión, y exigirlo en la siguiente. El schema se vuelve aditivo *y*
el comportamiento se mantiene, a cambio de que la protección tarde un ciclo.

### Plan de deprecación

1. `1.6.0`: se añade `confirm` (aditivo). Sin él, la tool ejecuta **y avisa** en
   `warnings[]`, con el texto exacto que habrá que usar.
2. `1.6.x`: el aviso se repite en la documentación y en `pbi_start_here`.
3. `2.0.0`: sin `confirm: true`, `validation_error`.

### Versión semántica recomendada

`2.0.0` si se aplica de golpe. `1.6.0` para el paso aditivo del plan de arriba.

### Pruebas que se activarían tras ratificación

* `test_destructive_tool_requires_confirmation`, parametrizado con las dos
  tools nuevas (hoy cubre cuatro).
* Una regresión que llame sin `confirm` y exija `ok: false` + código estable.
* Regenerar `tests/golden/tools_v1.json` y el baseline empaquetado.

---

## Cambio 2 — `pbi_apply_plan` pasa de `confirm=True` a `confirm=False`

### Contrato actual

```python
def pbi_apply_plan(plan_token: str, confirm: bool = True, ...)
```

Es **el único** `confirm` de las 134 cuyo default no es `False`.

### Contrato propuesto

```python
def pbi_apply_plan(plan_token: str, confirm: bool = False, ...)
```

### Diff de schema

| | Actual | Propuesto |
|---|---|---|
| `confirm.default` | `true` | `false` |

Un cambio de **default**, que en MCP es contrato: el cliente que omite el
parámetro obtiene otro comportamiento.

### A quién puede romper

Todo el que llame `pbi_apply_plan(plan_token=...)` sin `confirm`: hoy aplica,
mañana no. Es el cambio con más probabilidad de romper flujos existentes,
porque omitir un parámetro con default es lo normal.

### Peligro de dejarlo como está

**Un gate que viene abierto no es un gate.** El `confirm` de esta tool existe
para que aplicar un plan sea deliberado, y con `default=true` lo deliberado es
*no* aplicarlo. Además rompe la simetría con las otras ocho tools con `confirm`,
y esa inconsistencia es justo la que hace que un agente generalice mal.

### Alternativa compatible

**No hay una limpia.** Cualquier forma de exigir confirmación cambia lo que pasa
al omitir el parámetro. Lo más suave es el mismo plan por fases: avisar primero
—«esta llamada aplicará sin confirmación explícita; en 2.0 no lo hará»— y
cambiar el default después.

### Plan de deprecación

1. `1.6.0`: se mantiene `default=true` y se emite `warnings[]` cuando se omite.
2. `2.0.0`: `default=false`.

### Versión semántica recomendada

`2.0.0`. No hay forma de hacerlo en una menor sin mentir sobre lo que es.

### Pruebas que se activarían tras ratificación

* Añadir `pbi_apply_plan` a `test_destructive_tool_requires_confirmation`.
* Una prueba de que **omitir** `confirm` no aplica nada y lo dice.

---

## Cambio 3 — retirar `readOnlyHint` de `pbi_open_pbip_project` y `pbi_select_model`

### Contrato actual

```
pbi_open_pbip_project   risk: read_only   annotations: readOnlyHint = true
pbi_select_model        risk: read_only   annotations: readOnlyHint = true
```

### Contrato propuesto

```
pbi_open_pbip_project   risk: session_write   annotations: readOnlyHint = false
pbi_select_model        risk: session_write   annotations: readOnlyHint = false
```

### Diff de anotaciones

| | Actual | Propuesto |
|---|---|---|
| `readOnlyHint` | `true` | `false` |
| `destructiveHint` | `false` | `false` (no cambia) |

### A quién puede romper

A quien use `readOnlyHint` para decidir **si ejecuta sin preguntar**. Al pasar a
`false`, un cliente prudente empezará a pedir confirmación para abrir un
proyecto, que es una operación cotidiana: el riesgo aquí es **fricción**, no
pérdida de datos.

### Peligro de dejarlo como está

Las dos **escriben estado de sesión**: cambian cuál es el proyecto o el modelo
activo, y con eso cambian a qué apunta todo lo que venga después. Un agente que
las trate como lecturas puede reapuntar la sesión a otro proyecto sin avisar, y
la siguiente escritura —esa sí destructiva— irá al sitio equivocado. Es el
escenario que CORE-001 y CORE-002 ya trataron desde otro ángulo.

### Alternativa compatible

**Sí, y probablemente sea la correcta.** MCP admite `idempotentHint` y
`openWorldHint` además de los dos que se usan aquí. Mantener `readOnlyHint:
true` es insostenible —no son lecturas—, pero se puede acompañar el cambio con
`idempotentHint: true`, que es cierto y le dice al cliente que repetir la
llamada no acumula efectos. Así el cliente prudente tiene con qué no pedir
confirmación dos veces.

### Plan de deprecación

Las anotaciones no tienen fase intermedia: o describen el efecto o no. Se
recomienda aplicarlo **junto** con el cambio 1, en la misma versión, para que un
cliente se adapte una sola vez.

### Versión semántica recomendada

`2.0.0`, junto con los otros dos.

### Pruebas que se activarían tras ratificación

* `tests/test_tool_annotations.py`: mover las dos de `read_only` a la clase
  nueva y exigir que ninguna tool que escriba sesión siga anotada de lectura.
* Regenerar el golden del contrato y el baseline empaquetado.

---

## Resumen para decidir

| Cambio | Rompe | Alternativa compatible | Recomendación |
|---|---|---|---|
| 1 · `confirm` en refresh | comportamiento | sí: avisar antes de exigir | fases, `1.6.0` → `2.0.0` |
| 2 · default de `apply_plan` | comportamiento | no limpia | `2.0.0` |
| 3 · `readOnlyHint` | decisión del cliente | parcial: añadir `idempotentHint` | `2.0.0`, con el 1 |

**Lo que este ciclo hizo, y no más:** registrarlos, medirlos y dejarlos
preparados. El contrato sigue exactamente como estaba —`python -m
tests.contract_utils` sale 0— y G1.5 seguirá en `pendiente-ratificacion` hasta
que alguien firme.
