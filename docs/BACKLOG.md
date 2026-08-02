# Backlog

Lo que queda abierto, por qué importa y cómo se comprueba. Ordenado por lo que
más duele.

Se escribió el 2026-08-01, después de una sesión que encontró **doce defectos**
con la suite en verde. La lista de abajo no es una lluvia de ideas: es lo que
sabemos que falta, con evidencia.

---

## 1. El bloque `objects` de un visual no lo valida nadie

**Estado:** abierto. Declarado como excepción en
[`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).

El esquema oficial `formattingObjectDefinitions` declara
`DataViewObjectPropertyDefinitions` como `additionalProperties: {}`. Acepta
literalmente cualquier cosa. Ahí viven el formato condicional, los tamaños de
fuente y los colores de cada visual.

**Consecuencia medida:** el formato condicional se escribió mal durante toda la
vida del proyecto —faltaba el nivel `color`— y pasó el validador oficial de
Microsoft con cero errores. Solo se vio abriendo el informe y viendo una tabla
sin colorear.

**Único detector hoy:** una persona mirando la pantalla.

**Qué haría falta:** un oráculo propio para ese bloque. La forma más barata es
un corpus de visuales reales exportados de Power BI Desktop, y comparar la
estructura que generamos contra la que produce la herramienta. No es
validación de esquema; es equivalencia con lo que hace el producto.

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

**Estado:** abierto, acotado.

Nueve tipos con datos: `card`, `cardVisual`, `tableEx`, `pivotTable`, `slicer`,
`clusteredBarChart`, `clusteredColumnChart`, `lineChart`, `pieChart`. Más los
de composición.

**No están** los que se piden a menudo: `gauge`, `kpi`, `donutChart`,
`areaChart`, `scatterChart`, `treemap`, `funnel`, `waterfall`, `multiRowCard`,
`ribbonChart`.

**Cómo añadir uno sin repetir el error de `cardVisual`:** los roles **no se
deducen**. Se escribe un visual por cada par (tipo, rol candidato), se corre el
CLI oficial sobre el informe entero y se lee qué devuelve `PBIR_ROLE_UNKNOWN`.
Sale la tabla autoritativa en una pasada. `tests/test_generadores_abren.py`
tiene el barrido montado.

---

## 4. Roles conocidos que no se ofrecen

**Estado:** abierto, trivial.

El barrido contra el CLI oficial encontró roles válidos que `ROLE_MAP` no
expone: `tooltips` en casi todos los gráficos, `Y2` en `lineChart` (eje
secundario), `Rows` en los de barras.

Están verificados contra el validador; solo hay que declararlos y probarlos.

---

## 5. El color mínimo de un degradado sobre tema oscuro

**Estado:** abierto, es una trampa de uso, no un defecto.

`pbi_set_conditional_format` toma los colores de quien llama. Con `#FFFFFF`
como mínimo sobre un tema oscuro, los valores bajos quedan **blanco sobre
blanco**: la celda se pinta y el número desaparece.

**Qué haría falta:** que el degradado por defecto salga del tema aplicado —el
mínimo debería ser el fondo del tema, no blanco—, o al menos avisar cuando el
contraste del extremo bajo caiga por debajo de 3:1. La cuenta ya existe en
`tests/test_design_y_guia.py::contraste`.

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

## 8. Lo que aprendimos y no debe perderse

Tres reglas que salieron caras. Están en el código como comentarios, pero
conviene tenerlas juntas:

1. **Una suite verde no prueba nada si la forma correcta la define el mismo
   código que se prueba.** Hay que preguntarle a `TmdlSerializer` y al CLI
   oficial. `tests/test_generadores_abren.py`.

2. **Una prueba que pasa pero no habría cazado el defecto no vale nada.**
   Verificar por mutación: revertir el arreglo y comprobar que falla, y que el
   mensaje nombra la línea culpable.

3. **Lo que solo se ejecuta en la máquina del que programa, solo funciona
   ahí.** La instalación limpia encontró dos defectos que ninguna de las 1255
   pruebas veía, porque todas corrían sobre un entorno que ya estaba bien.
