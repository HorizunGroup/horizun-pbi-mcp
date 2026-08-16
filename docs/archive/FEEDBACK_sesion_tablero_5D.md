# Feedback de uso — Horizun PBI MCP v1.0.1

**Fecha:** 2026-08-04
**Escenario:** construir de cero un tablero 5D BIM de 4 páginas (portada,
presupuesto, 4D cronograma, 5D valor ganado) sobre un `.pbix` existente con
modelo BIM + cronograma de MS Project + costos de ERP.
**Volumen:** ~120 llamadas al MCP, 22 medidas creadas, 4 páginas, 2 conversiones
de formato, 3 rediseños completos.

---

## Lo que funcionó bien

Vale la pena decirlo antes de la lista de problemas, porque estas decisiones
se notan y son las que hicieron el trabajo posible:

- **El bloqueo por "Desktop tiene el proyecto abierto"** evitó al menos dos
  pérdidas de trabajo. El mensaje explica *por qué* bloquea (que el Ctrl+S del
  usuario sobrescribiría) en vez de solo negarse. Excelente.
- **Transacciones con journal y backup** en cada escritura. Da confianza para
  operar sobre el archivo de trabajo de alguien.
- **`pbi_validate_tmdl`** con el aviso de `tmdl_transform_without_culture`
  detectó, sin ejecutar nada, el riesgo del separador decimal — que resultó ser
  un error real (valores ×100 en el ERP).
- **Los mensajes de error accionables.** `pbi_convert_pbix_to_pbip` no dijo
  "ruta demasiado larga": dijo *"mide 310 caracteres, elige un out_dir al menos
  51 caracteres más corto (p.ej. C:\pbip)"*. Se resolvió al primer intento.
- **`pbi_run_dax`** fue la herramienta más usada de todas. Poder verificar cada
  número contra el modelo en vivo cambió por completo la calidad del trabajo.

---

## 1. CRÍTICO — Proyecto activo y modelo activo pueden ser archivos distintos

### Observación
`pbi_select_model` apuntaba al modelo en vivo de `Sesion9.pbix`, mientras
`pbi_start_here` reportaba:

```
Proyecto activo: Control_Acceso.pbip
```

Un archivo **completamente distinto**, de otro cliente. `pbi_report_capabilities`
devolvió los visuales y el tema de `Control_Acceso`, no del informe en el que
estábamos trabajando.

### Impacto
Estuve a punto de escribir 4 páginas nuevas dentro del informe equivocado.
Solo lo detecté porque el conteo de visuales no cuadraba con lo que había
inspeccionado minutos antes.

En un uso desatendido —o con alguien con menos contexto— esto corrompe el
trabajo de otro proyecto de forma silenciosa.

### Sugerencia
- Que `pbi_start_here` y `pbi_health_check` marquen en ROJO cuando
  `active_model` y `active_pbip` no correspondan al mismo archivo.
- Que las herramientas de informe (`pbi_apply_page_spec`, `pbi_compose_page`,
  `pbi_create_visual`) **se nieguen** si hay desajuste, igual que se niegan
  cuando Desktop tiene el proyecto abierto. El precedente ya existe y funciona.
- Alternativa mínima: incluir el nombre del proyecto destino en la respuesta de
  toda herramienta que escriba en el informe.

---

## 2. CRÍTICO — `mode='live'` produce medidas efímeras que rompen informes

### Observación
Creé 5 medidas con `pbi_create_measure(mode='live')`. La respuesta avisa:

> "Cambio aplicado en el modelo en memoria. Para que quede guardado usa Ctrl+S."

El usuario cerró Desktop sin guardar. Las medidas desaparecieron. Las páginas
que ya las referenciaban quedaron con **"Hubo un problema con uno o más
campos"** en 4 tarjetas.

### Impacto
Estado inconsistente entre el informe (en disco) y el modelo (en memoria), que
ninguna validación detecta porque cada mitad es válida por separado.

### Sugerencia
- Que `mode='live'` devuelva `"persisted": false` de forma **prominente**, no
  como nota al pie, y que lo repita en la respuesta de cualquier herramienta que
  después referencie esa medida.
- Que `pbi_apply_page_spec` valide las referencias contra el **modelo
  persistido** (TMDL), no solo contra el vivo. Una página en disco que apunta a
  una medida que solo existe en memoria es una bomba de tiempo.
- Considerar un `pbi_persist_live_changes` que serialice el modelo vivo a TMDL
  sin depender del Ctrl+S del usuario.

---

## 3. ALTO — `sync_mode='merge'` deja huérfanos al cambiar el lienzo

### Observación
Apliqué `pbi_apply_design_system('sala')` (1920×1080), compuse 4 páginas, y
después cambié a `informe` (1280×720) y recompuse. Con `merge` (el defecto),
los visuales de la composición anterior **se conservaron fuera del lienzo**:

```
"not_removed": ["5423ec9e284a415f8222", "6691c6aa4ee14658894f", ...]
PBIR_LAYOUT_OUT_OF_BOUNDS_WIDTH  ×3
PBIR_LAYOUT_OUT_OF_BOUNDS_HEIGHT ×1
```

### Impacto
La página queda con basura invisible que sí se lleva al render y a la
publicación. Hay que descubrirlo con `pbi_detect_layout_issues` y limpiar a mano.

### Sugerencia
- Detectar que el lienzo cambió de tamaño y **avisar** que `merge` va a dejar
  visuales fuera de límites, proponiendo `replace`.
- O reflujar automáticamente los conservados a la nueva rejilla.

---

## 4. ALTO — Cambiar el sistema de diseño después no tiene camino de vuelta

### Observación
La documentación de `pbi_list_design_systems` avisa:

> "Elígelo ANTES de la primera página; cambiarlo después obliga a recolocarlo todo."

Es un aviso honesto, pero en la práctica el usuario **sí cambia de opinión**
(en esta sesión pasó: oscuro → claro a mitad del trabajo). Y cuando pasa, la
herramienta no ayuda: hay que recomponer cada página a mano.

Además hay un detalle no obvio: los `textbox` de título que genera
`pbi_compose_page` llevan el color **hardcodeado** (`#FFFFFF` con el tema
oscuro). Al cambiar a tema claro quedan blancos sobre blanco — invisibles.
Recomponer la página los arregla, pero no está dicho en ninguna parte.

### Sugerencia
- Un `pbi_reflow_pages(system=...)` que reescale posiciones y recalcule los
  colores de texto de los elementos decorativos.
- Como mínimo, que `pbi_apply_design_system` avise cuántas páginas existen ya
  con otro lienzo y qué va a pasar con ellas.

---

## 5. MEDIO — `pbi_compose_page` tiene una sola forma posible

### Observación
La composición es siempre título → KPIs → hero → supports → detail. Está
documentado como decisión deliberada ("la coherencia sale de que ninguna
página pueda inventarse su propio orden"), y para 3 de las 4 páginas funcionó.

Pero para la portada —que necesitaba nombre de proyecto grande, un visor 3D
protagonista y navegación— tuve que abandonarla y bajar a `pbi_apply_page_spec`
con posiciones calculadas a mano.

### Sugerencia
No romper la opinión de la herramienta, pero añadir 2-3 arquetipos más:
`portada` (título grande + navegación + cifras), `detalle` (tabla dominante),
`monitor` (un visual a pantalla completa + KPIs al margen). Que el usuario elija
arquetipo, no geometría.

---

## 6. MEDIO — Sin validación de propiedades de formato

### Observación
*Este hallazgo salió de escribir PBIR a mano, no del MCP — pero es directamente
relevante para su diseño.*

Escribí formato de `tableEx` con propiedades plausibles pero no verificadas
(`backColorPrimary`, `rowPadding`, `gridVerticalColor`, `total`). Resultado:

- **Tarjetas y gráficos**: ignoran en silencio la propiedad desconocida.
- **`tableEx`**: **no renderiza en absoluto**. Tabla vacía y error en pantalla.

O sea, la tolerancia a propiedades inválidas **no es uniforme entre tipos de
visual**.

También me mordió `labelPrecision: 0` en `card`: es válida, pero sobrescribe el
`formatString` de la medida. Puse 0 para que el presupuesto no llevara
decimales y terminó mostrando SPI 0,88 como **1**.

### Sugerencia
- Aplicar a las propiedades de formato la misma disciplina que ya se aplica a
  los roles: validar contra el catálogo antes de escribir.
- Advertir cuando una propiedad de formato del visual **anula** el
  `formatString` de la medida (`labelPrecision`, `labelDisplayUnits`).

---

## 7. MEDIO — Muchos ciclos cerrar/abrir Desktop

### Observación
El bloqueo por proyecto abierto es correcto, pero en esta sesión obligó a
**5 ciclos** de cerrar Desktop → escribir → reabrir → refrescar. Cada uno cuesta
~40 s más la atención del usuario, y en dos de ellos se perdió el refresco.

### Sugerencia
- Poder **encolar** varias operaciones de escritura y ejecutarlas en un solo
  cierre (`pbi_batch_report_edits`).
- O que `pbi_open_in_desktop` tenga `refresh_after=true` para no perder el
  refresco en cada ciclo.

---

## 8. BAJO — El navegador de páginas muestra las páginas ocultas

### Observación
Marqué 5 páginas con `"visibility": "HiddenInViewMode"` en `page.json` para que
el visual `pageNavigator` mostrara solo las 4 reales. En modo edición las siguió
mostrando, con los nombres en vertical por falta de ancho. Hubo que borrarlas.

### Sugerencia
Documentar que `pageNavigator` respeta `visibility` solo en modo vista, o
exponer su opción de excluir páginas ocultas.

---

## 9. Petición de función — Visuales personalizados

Ver documento aparte: **`ISSUE_visuales_personalizados.md`**.

Resumen: `page_spec.py:179` valida contra `visual_factory.TYPE_MAP`, una tupla
fija de 29 tipos nativos. Los visuales personalizados del informe (visor APS,
buildMotion) no se pueden escribir, y son justamente el motivo de conectar BIM
con Power BI. La solución propuesta es descubrirlos leyendo
`CustomVisuals/*/resources/*.pbiviz.json`, que ya declara sus roles.

---

## Resumen priorizado

| # | Severidad | Hallazgo |
|---|---|---|
| 1 | Crítico | Proyecto activo ≠ modelo activo, sin aviso |
| 2 | Crítico | `mode='live'` efímero rompe informes en disco |
| 9 | Alto | Visuales personalizados no soportados |
| 3 | Alto | `merge` deja huérfanos al cambiar lienzo |
| 4 | Alto | Cambiar sistema de diseño no tiene reflujo |
| 5 | Medio | `compose_page` con una sola forma |
| 6 | Medio | Sin validación de propiedades de formato |
| 7 | Medio | Demasiados ciclos cerrar/abrir Desktop |
| 8 | Bajo | `pageNavigator` ignora `visibility` en edición |

**Los dos críticos comparten causa raíz:** el MCP mantiene dos estados
(modelo vivo y proyecto en disco) que pueden divergir, y ninguna validación
cruza los dos. Es donde yo pondría el siguiente esfuerzo.
