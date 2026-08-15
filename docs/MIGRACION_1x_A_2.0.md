# Migración 1.x → 2.0.0

Tres llamadas cambian. Nada más: siguen siendo **134 tools** y ninguna se retira,
se renombra ni cambia de tipo.

Los tres cambios vienen de CORE-004 y se ratificaron por escrito **antes** de
tocar el código —el dossier es
[`audits/CONTRACT_003_RATIFICATION.md`](audits/CONTRACT_003_RATIFICATION.md)—.
Esa es la regla del proyecto: el contrato congelado no se rompe sin una firma.

## Por qué 2.0.0 y no 1.5.5

Porque **1.5.5 nunca se publicó**. La última que existe de verdad es la 1.5.4.
Expresar una ruptura de contrato como un parche sobre algo que nadie tiene sería
mentir dos veces: sobre la ruptura y sobre la versión.

---

## 1 · `pbi_refresh_model` y `pbi_open_and_refresh` exigen `confirm`

**Antes**

```json
{"name": "pbi_refresh_model", "arguments": {"type": "full"}}
```

**Ahora**

```json
{"name": "pbi_refresh_model", "arguments": {"type": "full", "confirm": true}}
```

Sin `confirm`, la respuesta es `ok: false` con `error: "validation_error"` y
**no se ejecuta nada**: la capa que refresca no llega a tocarse. Lo mismo para
`pbi_open_and_refresh`.

**Por qué.** Eran las dos únicas tools de las 134 anunciadas como
`destructiveHint` y sin ningún parámetro que confirmar. Un agente que decide si
preguntar mirando *«¿tiene `confirm`?»* —y varios lo hacen— no veía nada que
preguntar, mientras la operación bloquea el modelo durante minutos y descarta lo
que hubiera en memoria sin guardar.

**Si tu cliente se rompe.** Se rompe de la forma buena: la llamada devuelve un
error explícito con el motivo, no un silencio. Añade `confirm: true` donde ya
querías refrescar.

---

## 2 · `pbi_apply_plan` ya no aplica si omites `confirm`

**Antes** — esto aplicaba el plan:

```json
{"name": "pbi_apply_plan", "arguments": {"plan_token": "..."}}
```

**Ahora** — hay que decirlo:

```json
{"name": "pbi_apply_plan", "arguments": {"plan_token": "...", "confirm": true}}
```

**Por qué.** Era el único `confirm` de las 134 cuyo default no era `false`: el
gate venía abierto, y lo deliberado pasaba a ser *no* aplicar. Además rompía la
simetría con las otras ocho tools con `confirm`, que es justo la inconsistencia
por la que un agente generaliza mal.

**Este es el cambio con más probabilidad de romperte algo**, porque omitir un
parámetro que tiene default es lo normal. Busca en tu código las llamadas a
`pbi_apply_plan` sin `confirm` y decide, una por una, si querían aplicar.

---

## 3 · `pbi_open_pbip_project` y `pbi_select_model` dejan de ser «solo lectura»

**La llamada no cambia.** Lo que cambia es lo que anuncian:

| Anotación | Antes | Ahora |
|---|---|---|
| `readOnlyHint` | `true` | **`false`** |
| `destructiveHint` | `false` | `false` |
| `idempotentHint` | — | **`true`** |

**Por qué.** Escriben estado de **sesión**: cambian cuál es el proyecto o el
modelo activo, y con eso cambian a qué apunta todo lo que venga después. Un
cliente que las trataba como lecturas podía reapuntar la sesión sin avisar, y la
siguiente escritura —esa sí destructiva— iba al sitio equivocado.

**Qué hacer.** Probablemente nada. Si tu cliente decide por `readOnlyHint` si
ejecutar sin preguntar, ahora preguntará por una operación cotidiana; para eso
está `idempotentHint: true`, que es cierto —abrir dos veces el mismo proyecto
deja el mismo estado, y hay una prueba que lo comprueba— y le dice a un cliente
prudente que puede reintentar sin acumular efectos.

---

## Cómo saber si te afecta, sin leer tu código

Pídele al servidor su contrato y busca los tres:

```bash
python -m tests.contract_utils
```

O, desde un cliente MCP, mira `tools/list`: las tres tools traen sus
`annotations` y sus parámetros con los defaults nuevos. El contrato completo
está congelado en `tests/golden/tools_v1.json` y el healthcheck lo verifica
contra el baseline empaquetado en cada instalación.

## Lo que NO cambia

- Las **134 tools** siguen ahí, con los mismos nombres.
- Ningún payload pierde claves: la ampliación del golden a las 134 en 174
  muestras lo vigila, y retirar o renombrar una clave pone la suite en rojo.
- Los códigos de error siguen siendo los mismos.
- El resto de defaults, sin tocar.
