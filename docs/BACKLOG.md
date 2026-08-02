# Backlog

Lo que queda abierto, por qué importa y cómo se comprueba. Ordenado por lo que
más duele.

Se actualizó el 2026-08-02 después de auditar por AST las **117 tools**, probar
conversiones reales PBIX→PBIP y volver a abrir los resultados en Power BI
Desktop. La suite integrada quedó en **1540 passed, 3 skipped**. La lista de
abajo no es una lluvia de ideas: es lo que sabemos que falta, con evidencia.

---

## 1. Equivalencia completa del bloque `objects`

**Estado:** parcialmente cerrado. La estructura que genera Horizun ya tiene
oráculo; la equivalencia visual completa sigue siendo una limitación en
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

El esquema oficial `formattingObjectDefinitions` declara
`DataViewObjectPropertyDefinitions` como `additionalProperties: {}`. Acepta
literalmente cualquier cosa. Ahí viven el formato condicional, los tamaños de
fuente y los colores de cada visual.

**Consecuencia medida:** el formato condicional se escribió mal durante toda la
vida del proyecto —faltaba el nivel `color`— y pasó el validador oficial de
Microsoft con cero errores. Solo se vio abriendo el informe y viendo una tabla
sin colorear.

**Barreras actuales:** `services.pbir_schema.validar_objetos_visual()` comprueba
antes de cada escritura la gramática de los envoltorios que el servidor
produce: grupos de formato, `solid.color`, expresiones, y los gradientes
`FillRule`. Con ello una forma como `solid: {expr: ...}` se bloquea y revierte
la transacción, aunque el esquema oficial la acepte. La regresión se ejecuta
contra el commit anterior y falla allí.

`services.format_oracle` añade la pieza que faltaba para las rutas que el MCP
administra: consulta `formatting effective-properties` del CLI oficial y
compara grupo, propiedad, tipo y enum por `visualType`. Un snapshot mínimo de
esas mismas rutas mantiene la barrera sin Node; una prueba viva exige que el
snapshot continúe siendo subconjunto exacto del catálogo oficial instalado.

El corpus sintético `tests/fixtures/synthetic/format_objects_corpus.json`
conserva únicamente formas estructurales que Desktop exportó. Se construyó
desde copias temporales de 125 PBIX y reemplaza cada hoja por un token de tipo.
No contiene rutas, conteos por origen, hashes, GUID, IDs, URLs, textos, nombres
de página/tabla/campo, valores del modelo ni tipos custom. El extractor falla
cerrado ante cualquier clave no incluida en su allowlist.

Esta evidencia descubrió y cerró defectos que el esquema aceptaba: `cardVisual`
usaba `value.color` en vez de `value.fontColor`; formas e iconos escribían enums
inventados; `table`/`matrix` aceptaban propiedades que Desktop ignora; y los
`FillRule` perdían `Aggregation.Function` y usaban un selector incorrecto.

Además, la fábrica consulta el catálogo oficial para exigir roles,
cardinalidades y clase de campo (`Grouping`, `Measure` o
`GroupingOrMeasure`) antes de escribir. Esto cerró otra vía por la que Desktop
aceptaba el archivo pero dejaba un visual vacío o semánticamente incorrecto.
Los roles viven fuera de `objects` y se validan por separado.

**Cierre adicional:** cuando está instalado el CLI oficial, cada escritura PBIR
vuelve a comparar **todas** las propiedades presentes en `objects` y
`visualContainerObjects` del visual contra `effective-properties`, incluidas
las heredadas de una plantilla. Una propiedad o grupo desconocido bloquea la
transacción con `format_oracle`. Sin el CLI se conserva la barrera estructural
offline y no se finge equivalencia completa. La comprobación estructural
tampoco demuestra por sí sola que un visual se pinte como se espera: eso
pertenece a la comprobación renderizada del punto siguiente.

---

## 2. Interpretación automática de la comprobación visual

**Estado:** parcialmente cerrado. La captura ya es automática y segura; decidir
si el resultado es visualmente correcto todavía requiere inspección.

El **contraste WCAG** (`tests/test_design_y_guia.py`) cerró una clase real: el
título en `#0B0B0B` sobre fondo `#1A1A19`.

La tool `pbi_validate_desktop_render` abre el `.pbix`/`.pbip`, correlaciona la
ventana exacta por PID y hora de creación, y la renderiza con `PrintWindow` sin
depender del foco. Falla cerrado ante ventanas ambiguas o PID reciclado, escribe
el PNG atómicamente en `outputs/desktop_captures` y solo cierra Desktop si esa
misma llamada lo abrió. Se probó sobre Power BI Desktop real.

Lo que sigue exigiendo ojos: que el número quepa, que la tabla pinte, que la
leyenda no tape la barra y que el eje no se corte. La captura demuestra que la
ventana renderizó; no interpreta semánticamente sus píxeles.

**Procedimiento actual**, para no reinventarlo cada vez:

```python
resultado = pbi_validate_desktop_render(
    path=r"C:\ruta\Proyecto.pbip", timeout=300, capture_timeout=30)
```

Si el proyecto recién generado no tiene datos materializados, todavía hay que
refrescarlo. La tool captura la página visible; recorrer todas las páginas y
clasificar defectos de composición sigue pendiente.

Durante esta revisión apareció un caso real que antes terminaba en un Frown de
Desktop: `Vista_Obra` tenía las medidas `Ejecutado` y `Programado` con el mismo
nombre que sus columnas. El parser TMDL lo aceptaba, pero Power BI rechazaba el
modelo al cargarlo. El launcher ahora ejecuta el lint/TOM antes de abrir la
ventana y devuelve `desktop_preflight_failed` con las dos reglas y su evidencia;
no deja un proceso `Sin título` colgado. La regresión está en
`tests/test_desktop_preflight.py`.

**Qué haría falta:** navegación determinista por todas las páginas y un oráculo
de imagen/layout que pueda emitir diagnósticos concretos, sin confundir una
diferencia legítima de datos o tema con un defecto.

---

## 3. Cobertura de tipos de visual

**Estado:** cerrado el 2026-08-01.

Están los nueve tipos con datos originales y los diez que faltaban: `gauge`,
`kpi`, `donutChart`, `areaChart`, `scatterChart`, `treemap`, `funnel`,
`waterfallChart`, `multiRowCard` y `ribbonChart`. `waterfall` se acepta como
alias, pero se escribe como `waterfallChart`, que es el nombre real del
catálogo oficial. Más los de composición.

**Cómo añadir uno sin repetir el error de `cardVisual`:** los roles **no se
deducen**. Se escribe un visual por cada par (tipo, rol candidato), se corre el
CLI oficial sobre el informe entero y se lee qué devuelve `PBIR_ROLE_UNKNOWN`.
Sale la tabla autoritativa en una pasada. `tests/test_generadores_abren.py`
tiene el barrido montado.

---

## 4. Roles conocidos que no se ofrecen

**Estado:** cerrado el 2026-08-01.

`ROLE_MAP` ya expone `tooltips`, `Y2` y `Rows`, además de los roles propios de
los diez tipos nuevos. Los nombres se consultaron con `catalog describe` del
CLI oficial; la prueba `abre` genera cada par tipo/rol y exige cero
`PBIR_ROLE_UNKNOWN` sobre el informe completo.

---

## 5. El color mínimo de un degradado sobre tema oscuro

**Estado:** cerrado el 2026-08-01.

`pbi_set_conditional_format` toma los colores de quien llama. Con `#FFFFFF`
como mínimo sobre un tema oscuro, los valores bajos quedan **blanco sobre
blanco**: la celda se pinta y el número desaparece.

La operación conserva los colores explícitos del llamante, pero ahora lee el
tema activo y avisa si cualquiera de los extremos queda por debajo de 3:1. Al
pintar el fondo compara contra la tinta del tema; al pintar fuente o marcas,
contra la superficie. Así `#FFFFFF` sobre texto blanco en el tema oscuro ya no
pasa en silencio.

---

## 6. R15 — `both` bloqueado

**Estado:** abierto por diseño. Analizado en [`DUAL_MODE.md`](DUAL_MODE.md).

`live` exige Power BI Desktop **abierto**; `pbip` exige que esté **cerrado**.
Son precondiciones mutuamente excluyentes, así que `both` producía un estado
parcial determinista. Se bloqueó en vez de fingir atomicidad.

No es deuda: es una decisión con su razonamiento escrito. Reabrirlo exige
convertirlo en un **workflow guiado**, no en una operación.

---

## 7. G10 — dos esquemas PBIR sin publicar

**Estado:** abierto **upstream**, no nuestro.

`visualContainer/2.10.0` y `bookmarks/2.0.0` devuelven 404 en el origen oficial
de Microsoft. Su propio CLI tampoco los valida: emite
`PBIR_SCHEMA_UNREACHABLE` y se los salta.

No hay nada que hacer de este lado hasta que Microsoft los publique.

---

## 8. `pbi_apply_plan` y la confirmación contractual

**Estado:** pendiente de decisión de contrato.

La tool aplica un plan firmado mediante `plan_token`, pero conserva
`confirm=true` como valor por defecto histórico. Si se interpreta que el token
es la aprobación explícita, el contrato actual es coherente. Si se exige además
un `confirm=true` escrito por el cliente, el default debe pasar a `false`.

Ese cambio rompería el contrato MCP congelado y por eso no se hizo durante la
auditoría. Requiere aprobación deliberada y actualización del golden; no debe
entrar disfrazado de refactor.

---

## 9. Lo que aprendimos y no debe perderse

Tres reglas que salieron caras. Están en el código como comentarios, pero
conviene tenerlas juntas:

1. **Una suite verde no prueba nada si la forma correcta la define el mismo
   código que se prueba.** Hay que preguntarle a `TmdlSerializer` y al CLI
   oficial. `tests/test_generadores_abren.py`.

2. **Una prueba que pasa pero no habría cazado el defecto no vale nada.**
   Verificar por mutación: revertir el arreglo y comprobar que falla, y que el
   mensaje nombra la línea culpable.

3. **Lo que solo se ejecuta en la máquina del que programa, solo funciona
   ahí.** La instalación limpia encontró dos defectos que ninguna prueba veía,
   porque todas corrían sobre un entorno que ya estaba bien. La suite actual
   tiene 1540 pruebas aprobadas, pero los oráculos externos siguen siendo
   obligatorios.
