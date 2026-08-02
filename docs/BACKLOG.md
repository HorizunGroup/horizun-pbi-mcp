# Backlog

Lo que queda abierto, por qué importa y cómo se comprueba. Ordenado por lo que
más duele.

Se actualizó el 2026-08-01 después de auditar por AST las **116 tools**, probar
conversiones reales PBIX→PBIP y volver a abrir los resultados en Power BI
Desktop. La suite integrada quedó en **1493 passed, 3 skipped**. La lista de
abajo no es una lluvia de ideas: es lo que sabemos que falta, con evidencia.

---

## 1. El bloque `objects` de un visual no lo valida nadie

**Estado:** parcialmente cerrado. Declarado como limitación en
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

El esquema oficial `formattingObjectDefinitions` declara
`DataViewObjectPropertyDefinitions` como `additionalProperties: {}`. Acepta
literalmente cualquier cosa. Ahí viven el formato condicional, los tamaños de
fuente y los colores de cada visual.

**Consecuencia medida:** el formato condicional se escribió mal durante toda la
vida del proyecto —faltaba el nivel `color`— y pasó el validador oficial de
Microsoft con cero errores. Solo se vio abriendo el informe y viendo una tabla
sin colorear.

**Barrera actual:** `services.pbir_schema.validar_objetos_visual()` comprueba
antes de cada escritura la gramática de los envoltorios que el servidor
produce: grupos de formato, `solid.color`, expresiones, y los gradientes
`FillRule`. Con ello una forma como `solid: {expr: ...}` se bloquea y revierte
la transacción, aunque el esquema oficial la acepte. La regresión se ejecuta
contra el commit anterior y falla allí.

Además, la fábrica ya consulta el catálogo oficial para exigir roles,
cardinalidades y clase de campo (`Grouping`, `Measure` o
`GroupingOrMeasure`) antes de escribir. Esto cerró otra vía por la que Desktop
aceptaba el archivo pero dejaba un visual vacío o semánticamente incorrecto.
No sustituye el oráculo de formato: los roles viven fuera de `objects`.

**Lo que aún falta:** un oráculo de equivalencia para el bloque completo. La
forma más barata es un corpus anonimizado de visuales reales exportados de
Power BI Desktop, y comparar la estructura que generamos contra la que produce
la herramienta. No es validación de esquema; es equivalencia con el producto.
La barrera actual no puede descubrir una propiedad nueva, una combinación que
Desktop ignora, ni demostrar que un visual se pinta como se espera.

---

## 2. La comprobación visual sigue siendo manual

**Estado:** parcialmente cerrado.

Lo único de «se ve bien» que está automatizado es el **contraste WCAG**
(`tests/test_design_y_guia.py`), y cerró una clase real: el título en `#0B0B0B`
sobre fondo `#1A1A19`.

Lo que sigue exigiendo ojos: que el número quepa, que la tabla pinte, que la
leyenda no tape la barra, que el eje no se corte.

**Procedimiento actual**, para no reinventarlo cada vez:

```python
from powerbi import desktop_launcher
desktop_launcher.open_pbix(r"C:\ruta\Proyecto.pbip", timeout=300)
```

Después hay que **refrescar** —un proyecto recién generado abre sin datos— y
recorrer las páginas con capturas.

**Qué haría falta:** exportar la página a imagen sin intervención. Power BI
Desktop no expone eso por línea de comandos; el camino realista es el servicio
o `Export-PowerBIReport`, y ninguno funciona sobre un `.pbip` local.

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
   tiene 1493 pruebas aprobadas, pero los oráculos externos siguen siendo
   obligatorios.
