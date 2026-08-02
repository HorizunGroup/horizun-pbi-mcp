# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado semántico. **El contrato de las 34 tools originales nunca se rompe.**

---

## [1.0.1] — 2026-08-02

### Corregido

- El oráculo oficial comprueba también visuales que solo contienen
  `visualContainerObjects`; ya no cae al snapshot parcial en ese caso.
- Las expresiones de formato vacías (`expr: {}`) se rechazan antes de escribir.
- La degradación a un esquema PBIR anterior se limita a versiones que el
  manifiesto identifica expresamente como no publicadas por Microsoft.
- Una sesión PBIP ya abierta puede reutilizarse sin validar primero una copia
  distinta o incompleta del modelo guardado en disco.

---

## [1.0.0] — 2026-08-02

Primera versión estable del repositorio oficial. **117 tools, 1542 pruebas
aprobadas y 3 omitidas por condiciones externas documentadas.**

### Incluye

- Validación previa de TMDL/TOM antes de abrir Power BI Desktop, que bloquea
  colisiones de nombres y evita el Frown genérico de proyectos inválidos.
- Oráculo estructural para las propiedades administradas de `objects`,
  validación de roles y tipos contra el catálogo oficial y captura segura de
  la ventana exacta de Desktop.
- Transacciones atómicas, backups, journals, rollback y contrato MCP
  compatible con las 34 tools originales.

### Límites publicados

- La equivalencia visual completa de `objects` aún requiere inspección
  renderizada para combinaciones no cubiertas por el oráculo.
- `mode="both"` permanece bloqueado por incompatibilidad entre Desktop abierto
  y escritura PBIP segura; los dos esquemas Microsoft no publicados siguen
  siendo una limitación upstream.

---

## [1.0.0-rc.11] — 2026-08-01

**116 tools, 1262 pruebas.**

Tres defectos encontrados corriendo **por primera vez** el checklist de release
de las cinco etapas sobre el tag publicado, más una segunda pasada de «abrir y
mirar».

### Corregido

- **`requirements.txt` había divergido de `pyproject.toml` en las seis
  dependencias.** El README ofrece `pip install -r requirements.txt` como
  primera opción, y ese archivo decía `mcp>=1.10` **sin tope**: una instalación
  limpia traía `mcp` 2.0.0 —donde `mcp.server.fastmcp` ya no existe— y **el
  servidor no llegaba ni a importar**. `jsonschema` y `referencing` faltaban del
  todo, así que toda escritura PBIR habría fallado con `schema_unavailable`.

  Ninguna de las 1255 pruebas lo veía, porque todas corren sobre el entorno de
  desarrollo, que ya estaba bien.

- **`doctor.py` comprobaba tres dependencias de las seis.** Una instalación sin
  `jsonschema` reportaba «Dependencias de Python: OK» y luego fallaba cada
  escritura. Un diagnóstico que no mira lo que importa es peor que no tenerlo.

- **`pbi_create_measure` dejaba escribir medidas que impiden abrir el
  proyecto.** Una medida no puede llamarse como una columna de su tabla, y su
  nombre es único en **todo** el modelo, no por tabla. El parser TMDL se traga
  las dos; el motor las rechaza al cargar. Comprobado abriéndolo: Power BI deja
  una ventana **«Sin título» con el modelo vacío** y dice que no puede crear la
  medida. El lint conocía las dos reglas desde siempre; el escritor no las
  consultaba.

### Documentación

- El checklist declaraba una excepción **obsoleta** —`filters`/`interactions`
  rechazados, cuando funcionan desde rc.9— y **no declaraba la que sí existe**:
  el bloque `objects` de un visual no lo valida ningún esquema
  (`additionalProperties: {}`), así que el único detector es abrir y mirar.
- **`docs/BACKLOG.md`** — lo que queda abierto, con evidencia y cómo se
  comprueba. Ocho puntos, ordenados por lo que más duele.

---

## [1.0.0-rc.10] — 2026-08-01

**116 tools, 1255 pruebas.**

Tres defectos que **no se ven ni abriendo el archivo ni validándolo**: solo
mirando la pantalla. Los tres pasaban el validador oficial de Microsoft con
cero errores.

### Corregido

- **El formato condicional no pintaba nada.** La regla se escribía como
  `{"solid": <expresión>}`, sin el nivel `color` que lleva cualquier otro color
  del PBIR. El esquema oficial declara esa parte como
  `additionalProperties: {}` —acepta literalmente cualquier cosa—, así que el
  CLI de Microsoft daba el visto bueno y Power BI simplemente no coloreaba.
  Se descubrió abriendo el informe y viendo una tabla sin color.

- **Colorear una segunda medida borraba la primera.** Se reemplazaba cualquier
  bloque que tuviera esa propiedad, sin mirar a qué campo apuntaba: en una
  matriz de varias métricas acababa pintada solo la última. Ahora cada regla se
  acota a su campo con `selector.metadata` —que el esquema describe como
  *"defines the scope to a specific field"*—, y solo se reemplaza la del mismo
  campo. El rodeo conocido, dinamizar las métricas a filas, ya no hace falta.

- **El título de una página podía quedar invisible.** Un informe admite **un**
  tema, pero `pbi_compose_page` incrustaba el color del sistema de la página.
  Componer con `informe` sobre un informe con el tema de `sala` escribía el
  título en `#0B0B0B` sobre un fondo `#1A1A19`: contraste 1,02:1. El color pasa
  a salir del tema que el informe tiene puesto; la geometría sigue siendo de la
  página. Hay una prueba de contraste WCAG que lo caza sin abrir nada.

- **El número del indicador era el texto más pequeño de la página.** En `sala`
  —lienzo de 1920×1080 pensado para leerse a cuatro metros— el KPI salía al
  tamaño por defecto. Cada sistema declara ahora su tamaño de KPI (44pt en
  `sala`, 28pt en el resto) y se apaga la etiqueta de categoría, que repetía el
  título de la tarjeta y salía más grande que el propio dato.

### Verificado abriéndolo

Se generó un proyecto con datos reales, se abrió en Power BI Desktop y se
miraron las cuatro páginas. Es lo que encontró los cuatro defectos de arriba:
la suite estaba en verde y el validador oficial también.

---

## [1.0.0-rc.9] — 2026-08-01

**116 tools, 1233 pruebas.**

Seis defectos que ningún validador propio veía, y los dos huecos que quedaban
entre tener las piezas y saber usarlas.

### Añadido

- **Capa de diseño** (`pbi_list_design_systems`, `pbi_apply_design_system`,
  `pbi_compose_page`). Había dos mitades que no se hablaban: el tema sabía de
  color y de tipografía y nada de dónde va cada cosa; el motor de layout
  colocaba con `ceil(sqrt(n))` sin saber de qué color era el fondo. Entre las
  dos no había rejilla, ni márgenes constantes, ni banda de título. El
  resultado se notaba: páginas correctas y sin criterio.

  Un **sistema de diseño** posee las dos mitades: de qué tema saca el color
  —de los que ya estaban verificados contra daltonismo, no de una paleta
  nueva—, sobre qué rejilla de 12 columnas se coloca todo, qué alturas tienen
  las bandas y qué tamaño tiene cada nivel de texto. Tres sistemas, y cada uno
  resuelve un escenario distinto: `sala` (1920×1080, se lee a cuatro metros),
  `informe` (1280×720, se exporta a PDF) y `foco` (el color saturado reservado
  al semáforo).

  `pbi_compose_page` traduce la intención —«un título, cuatro indicadores, un
  gráfico protagonista y dos de apoyo»— en una página colocada. La composición
  es rígida a propósito: la coherencia entre páginas sale de que ninguna pueda
  inventarse su propio orden. Y si algo no cabe **se dice con la cuenta
  hecha**, en vez de encogerlo hasta que no se lea.

- **`pbi_start_here`** — un punto de entrada para 116 tools. Ciento dieciséis
  tools con buen nombre siguen siendo ciento dieciséis tools: el catálogo
  estaba completo y el camino no existía. Esta mira el estado real —si hay
  proyecto, si tiene modelo o solo informe, si está vacío, si Power BI Desktop
  lo tiene abierto e impide escribir el TMDL— y responde con tres o cuatro
  pasos concretos, cada uno con **por qué** toca ahora. Un paso sin motivo es
  una orden, y una orden no se puede saltar con criterio.

  Cuenta visuales, no solo páginas: un proyecto recién creado trae una página
  vacía, y responderle «ya tienes una» a quien todavía no tiene nada es la
  clase de respuesta que hace desconfiar del resto.

- **`tests/test_generadores_abren.py`** — la prueba que faltaba, y la que
  encontró todo lo anterior. Construye un `.pbip` con los generadores **de
  verdad** (esqueleto, tablas desde archivo, medidas, tema, los nueve tipos de
  visual con datos, filtros, interacciones y marcadores) y le pregunta a los dos
  oráculos reales si eso abre.

  Se verificó por mutación: al revertir cada arreglo, la prueba falla y **nombra
  la línea culpable** (`ROLE_MAP['cardVisual']['values'] = 'Values'`).

  Las que necesitan las DLL y Node se marcan `abre` y se omiten solas; el
  contrato de roles, el de tipos de interacción —anclado al esquema oficial
  cacheado— y el viaje de ida y vuelta no necesitan nada y corren en CI.

### Corregido

- **El catálogo de tools mentía sobre su propio tamaño.** Anunciaba 101 con 112
  registradas, y su tabla de bloques sumaba una tercera cifra. Ahora los
  recuentos salen de las constantes que la suite verifica, y hay una prueba que
  lo mantiene sincronizado.

Y seis defectos del mismo linaje: el servidor escribía algo, se lo enseñaba a un
validador **propio**, y el validador propio decía que sí. Ninguno de los 1169
tests los veía, porque la forma correcta la definía el mismo código que se
estaba probando. Se encontraron preguntándole a los dos únicos jueces que no son
nuestros: `TmdlSerializer` (el código con el que Power BI lee el modelo) y el
CLI oficial `@microsoft/powerbi-report-authoring-cli`.

- **Los campos de un visual se descartaban en silencio si el rol no coincidía
  en mayúsculas.** El rol se buscaba con `fields.get(rol)`, exacto. Escribir
  `{"Values": [...]}` —que es el nombre que aparece **en el propio
  `visual.json`** y el que devuelve `pbi_list_visuals`— no casaba con la clave
  `values`, y el visual se escribía sin ningún dato. Sin error. El informe abre
  y pinta una tarjeta en blanco, que es peor que no abrir: nadie va a buscar un
  fallo que nunca se dio. Ahora el rol se reconoce escrito como sea.

- **Un rol mal escrito junto a uno bueno desaparecía sin ni siquiera un aviso.**
  `{"category": [...], "valeus": [...]}` producía un gráfico con eje y sin
  barras. Ahora un rol que ese tipo de visual no tiene se **rechaza**, con la
  lista de los válidos.

- **`cardVisual` declaraba el rol `Values`; PBIR exige `Data`.** El tipo estaba
  anunciado como soportado y **siempre** generaba un informe inválido
  (`PBIR_ROLE_UNKNOWN` más `PBIR_ROLE_REQUIRED_MISSING`). El mapa de roles
  completo se comprobó uno a uno contra el validador oficial en vez de deducirlo.

- **El lector y el escritor del mismo servidor no se entendían.**
  `pbi_list_visuals` devuelve los roles con el nombre PBIR (`Category`, `Y`) y
  cada campo como un objeto; el generador esperaba roles lógicos y cadenas. Leer
  una página para hacer otra parecida —el flujo más natural que hay— fallaba, y
  si alguien extraía el `ref` a mano, el visual salía vacío. Ahora se aceptan
  las dos formas.

- **`interactions` estaba declarado, validado y era inservible.** Referencia
  visuales por id, y los ids los genera el compilador: quien escribe el spec no
  puede conocerlos. Todos los generadores del repositorio le pasaban `[]`, y por
  eso nadie descubrió el defecto siguiente. Ahora cada visual se puede señalar
  por su posición, por un `id` propio del spec o por su título.

- **Dos de los tres tipos de interacción no existían en PBIR.** `INTERACCIONES`
  decía `("NoFilter", "Filter", "Highlight")`. El esquema oficial de
  `page/2.1.0` dice `Default`, `DataFilter`, `HighlightFilter` y `NoFilter`.
  `Filter` y `Highlight` producían una página que el esquema rechaza, y
  `Default` no se ofrecía. Los nombres antiguos siguen valiendo como alias.

- **Una prueba `live` reventaba en vez de omitirse.** La condición de `skipif`
  se evalúa al recolectar y el cuerpo volvía a buscar la instancia: si Power BI
  Desktop se cerraba entre las dos cosas —en una suite de cuatro minutos,
  pasa— salía un `StopIteration` pelado.

---

## [1.0.0-rc.8] — 2026-08-01

**112 tools, 1169 pruebas.**

Tres defectos que solo se ven **abriendo** el informe. Ninguno lo detecta un
validador de esquema: el JSON es correcto en los tres casos.

### Corregido

- **El esqueleto generaba informes que Power BI se negaba a abrir.** Un informe
  necesita un tema base *resuelto*, y son cuatro cosas que van juntas o no van:
  la declaración en `themeCollection`, **`reportVersionAtImport` dentro de
  ella**, la entrada en `resourcePackages` y el archivo en disco. Faltaban
  todas. Power BI lo dice literalmente —«La propiedad necesaria
  'reportVersionAtImport' no se incluyó»— pero solo al abrir.

  El tema base ahora **lo genera el MCP** (`HorizunBase`) en vez de copiar el de
  Microsoft: vendorizar `CY26SU05.json` en un repositorio Apache-2.0 no es
  nuestro para hacerlo. Paleta neutra a propósito; la identidad propia se aplica
  con `pbi_apply_theme`.

- **`title` se imprimía sobre el lienzo.** En un spec, `title` identifica al
  visual; en un elemento de composición no es una etiqueta que nadie quiera ver.
  Salía «Titulo» sobre el título de una portada y habría salido «Logo Acme»
  sobre un logo. Ahora los decorativos no lo muestran salvo `show_title: true`.

- **Y al revés: pedir un título en una tarjeta no lo mostraba.** Se escribía el
  texto pero no `show`, y el defecto de una tarjeta es *oculto*. Se pedía un
  rótulo, no fallaba nada, y en pantalla no había rótulo.

### Añadido

- **Altura mínima automática en los textos.** Por debajo del piso que exige el
  tamaño de fuente, Power BI mete barra de scroll y corta el texto. Se aplica la
  fórmula del validador oficial —`max(18, ceil(pt × 25/16)) + relleno`—, se
  corrige hacia arriba y **se avisa**: quien compone una página no tiene por qué
  saber la fórmula.
- **Formato de tarjeta desde el spec**: `value_font_size`, `bold_value`,
  `value_color` y `show_category_label`, para que el número pese más que su
  etiqueta y no se repita el mismo texto arriba y abajo. Sin opciones no se
  toca nada: no se inventa formato que nadie encargó.

---

## [1.0.0-rc.7] — 2026-08-01

**112 tools, 1157 pruebas.** Corrige un `pbi_create_pbip_project` que generaba
proyectos que Power BI Desktop no abría.

### Corregido

**Al esqueleto le faltaban `.platform` y `definition/version.json`.** Sin ellos
el TMDL parsea, el validador propio dice que todo está bien, y Desktop abre una
ventana «Sin título» con el modelo vacío: ni carga ni explica por qué. Salió al
abrir el proyecto recién creado, no en las pruebas — el modelo era correcto; lo
que faltaba era del lado del informe, que `pbi_validate_tmdl` no mira.

Arreglado de raíz, no solo añadiendo los dos archivos: **el generador ahora pasa
el informe que escribe por el validador oficial de Microsoft** y aborta si hay
errores. Generar un proyecto que no abre es peor que no generarlo. Si el CLI no
está instalado se dice (`report_validation.checked: false`) en vez de darlo por
bueno.

Cada artefacto lleva su propio `logicalId`: dos artefactos no pueden compartir
identidad.

### Verificado de extremo a extremo

De dos rutas de archivo a un modelo que abre, sin escribir TMDL a mano:
`PB5-ERP_COSTOS_REALES.csv` (449 filas, suma **$1.031.062,23**, coincidiendo al
centavo con un cálculo independiente) y `PB5-EDI-CRONOGRAMA.xlsx` (20 columnas,
fechas incluidas). TMDL válido, informe **`passed` sin un solo diagnóstico**, y
abierto en Desktop.

---

## [1.0.0-rc.6] — 2026-08-01

**112 tools, 1155 pruebas**, contrato congelado (todo lo nuevo es aditivo).

Esta versión sale de un caso real: construir dos tableros y romper el proyecto
seis veces seguidas descubriendo a mano lo que el MCP debía haber dicho. El hilo
que une todo lo de abajo es dejar de usar Power BI Desktop como detector de
errores — llega al final, cuando ya se entregó.

### Corregido — una tabla que se creaba y no existía

**`pbi_create_calculated_table` escribía el archivo de la tabla pero no la
declaraba en `model.tmdl`.** Sin la línea `ref table <nombre>`, la tabla está en
disco y **no forma parte del modelo**: el `.tmdl` se ve perfecto, el proyecto
abre sin quejarse, y todo lo que la use —una medida, un visual— aparece roto sin
decir por qué.

Se detectó al escribir la prueba de extremo a extremo del punto anterior, no
usando la tool: precisamente el tipo de fallo que no se manifiesta hasta que
alguien abre el informe y ve una página vacía.

Arreglado en tres sitios, porque uno solo no basta:

- `pbi_create_calculated_table` y `pbi_add_table_from_file` declaran la tabla al
  crearla, en la misma operación.
- El validador gana dos reglas: **`tmdl_table_not_referenced`** (hay archivo y
  no hay declaración) y **`tmdl_ref_table_missing`** (hay declaración y no hay
  archivo). Las dos son errores, no avisos.
- El fixture `sample_pbip` no declaraba su tabla, así que no representaba un
  `.pbip` real y dejaba pasar justo este fallo. Ahora sí.

### Añadido

- **`pbi_create_pbip_project`**: crea un proyecto `.pbip` vacío pero válido y lo
  deja activo. Es lo que faltaba para armar un tablero **solo con rutas de
  archivos**: crear el proyecto, cargarle los datos y componer las páginas sin
  abrir Power BI Desktop hasta el final. Escribe el mínimo que Power BI acepta,
  con la referencia entre informe y modelo en ruta **relativa** —una absoluta
  ataría el proyecto a la máquina donde se creó— y con una página, porque un
  informe sin ninguna no abre.

  No declara `sourceQueryCulture` a propósito: la cultura se fija en cada
  consulta, que es lo único que no obliga a suponer cómo escribe los decimales
  cada origen.

- **`pbi_add_table_from_file`**: carga un archivo al modelo recorriendo los
  mismos pasos que una persona en Power Query —abrir, promover encabezados,
  cambiar tipos, cargar— y con los nombres de paso que pone Power BI en
  español (`Origen`, `Encabezados promovidos`, `Tipo cambiado`), para que la
  consulta se pueda abrir y editar en el editor sin desentonar. Admite `.csv`,
  `.txt`, `.tsv`, `.xlsx`, `.xlsm` y `.json` **sin dependencias nuevas**: un
  `.xlsx` se lee como lo que es, un zip con XML.

  Tres decisiones que evitan por construcción los fallos de escribir la M a
  mano:

  - **La cultura se deduce del archivo**, mirando cómo escribe los decimales, y
    se emite siempre explícita. Contra el CSV real que motivó todo esto acierta
    a la primera (`.` → `en-US`); a mano costó un refresh fallido y un contraste
    contra el origen para descubrirlo.
  - **Las fechas de Excel se detectan por su formato**, no por su valor. Excel
    guarda `45715` y aparte un `numFmt` que dice que es una fecha; sin mirarlo,
    una fecha se declara como entero y la carga revienta.
  - **Lo escrito se valida antes de confirmarse.** Si el TMDL generado no pasara
    `pbi_validate_tmdl`, se aborta. Automatizar el error sería peor que
    cometerlo a mano.

  Sobre el cronograma real de 20 columnas acierta las 20, incluidas dos que
  parecen fechas y no lo son porque mezclan texto (`NOD`): las deja como texto
  en vez de forzarlas.

- **`pbi_validate_tmdl`**: comprueba si un modelo TMDL abrirá, sin abrir Power
  BI Desktop. Dos capas: un lint estático en Python puro —funciona sin las DLL
  de Analysis Services— y, si están disponibles, un parseo con
  `TmdlSerializer`, **el mismo serializador que usa Power BI para abrir el
  proyecto**. Cada hallazgo trae regla, severidad, archivo y línea.
- **`pbi_open_in_desktop`**: abre un `.pbip` o `.pbix`, espera a que el motor
  local sirva el modelo, identifica cuál de las instancias le corresponde —el
  puerto es dinámico— y lo deja como modelo activo. Reutiliza la sesión si el
  archivo ya estaba abierto y nunca cierra una ventana del usuario. Cierra el
  ciclo de trabajo: ahora se puede comprobar que un proyecto **abre de verdad**
  sin pedírselo a nadie.

### Corregido

- **`pbi_validate_pbip_project` decía `valid: true` sobre proyectos que Power
  BI Desktop se negaba a abrir.** Solo comprobaba que los archivos existieran;
  nunca miraba dentro del TMDL. En una sesión real devolvió `valid: true` cinco
  veces seguidas mientras Desktop abortaba la carga, así que Desktop acabó
  siendo el único detector de errores disponible: caro y tarde. Ahora incorpora
  la validación real y añade el bloque `tmdl` a la respuesta. Solo invalida
  cuando **pudo** comprobarlo y salió mal: si no se pudo mirar, se dice.

### Las cinco trampas que ahora se detectan

Salieron de romper un proyecto real cinco veces seguidas:

1. **Propiedad de tabla después de sus hijos.** TMDL exige que las propiedades
   del objeto vayan antes que sus medidas y columnas. Insertar medidas justo
   debajo de `table X` deja huérfano lo que venía detrás. Power BI aborta con
   «se detectó una sangría no válida».
2. **Comentario `///` sobre una relación.** Se serializa como `description`, y
   `SingleColumnRelationship` no tiene esa propiedad.
3. **Medida con el mismo nombre que una columna de su tabla.** El parser lo
   acepta; el motor lo rechaza al crear la base. Solo se ve al abrir.
4. **Nombre de medida duplicado entre tablas.** En un modelo tabular el nombre
   de medida es global, no por tabla.
5. **`Table.TransformColumnTypes` sin cultura explícita** sobre un origen de
   texto, con `sourceQueryCulture` no invariante. Es el más peligroso porque
   **no da ningún error**: un CSV con punto decimal se lee como separador de
   miles y los totales salen inflados. El informe abre, pinta y miente.

El aviso 5 solo se emite cuando el origen entrega texto (`Csv.Document`,
`Json.Document`…). Excel y las bases de datos devuelven valores ya tipados: ahí
la cultura no cambia nada y avisar sería ruido.

### Encontrado al pasar el validador por los 23 proyectos del equipo

Tres clases de proyecto que el validador trataba mal, y una que ya venía rota:

- **`.pbip` de solo informe** (conexión en vivo a un dataset publicado, o
  convertido con `include_model=false`). Es legítimo y no tiene TMDL que
  validar. Antes salía como ruta rota; ahora se explica como lo que es
  (`tmdl_report_only_project`), que no es lo mismo que un fallo.
- **Modelos en formato `model.bim`** (TMSL/JSON): es el formato por defecto de
  un `.pbip` sin el preview de TMDL, o sea **la mayoría**. Se quedaban sin
  evaluar. Ahora se normalizan a la misma forma y se les aplican los chequeos
  semánticos, que no dependen del formato. Los estructurales no aplican: en un
  JSON no hay sangría que romper.
- **`create_calculated_table` perdía el tipo de columna en silencio.** Leía
  solo `data_type`; con `dataType` —como se llama la propiedad en TMDL y en el
  esquema JSON de la tool— caía al defecto `string`. Una columna numérica se
  escribía como texto y las agregaciones dejaban de funcionar sin que nada
  fallara. Ahora se aceptan las dos grafías y **una clave desconocida se
  rechaza** en vez de degradar el tipo: un typo no puede costar una tabla.

Resultado del barrido: 23 de 23 proyectos evaluados, **cero errores**, un único
aviso repetido (`tmdl_transform_without_culture` en `PowerBIMTemplate`, que lee
de `Json.Document` bajo `sourceQueryCulture: es-CO`).

### Lo que sigue sin poder comprobarse estáticamente

Documentado en la propia respuesta (`limitations`), no escondido: un blanco o un
duplicado en la columna del lado «uno» de una relación depende de los datos, no
del TMDL, y solo aparece al refrescar. Para eso está `pbi_refresh_model`.

---

## [1.0.0-rc.5] — 2026-07-31

**108 tools, 1097 pruebas**, contrato congelado. Integra tres correcciones que
salieron de tareas en segundo plano y refuerza el contrato de tipos de visual.

### Corregido

- **`TYPE_MAP` ahora se DERIVA en minúsculas** (`{real.lower(): real}`) en vez
  de escribirse a mano. Antes se corrigió bajando las claves una a una, lo que
  dejaba el defecto a un descuido de distancia: bastaba añadir una clave en
  camelCase para volver a anunciar un tipo que se rechaza. Ahora es imposible
  por construcción.
- **Se anunciaba menos de lo que se acepta**: el mensaje de error del factory,
  el hint del validador y `pbi_page_building_blocks` listaban solo los
  `visualType` reales, ocultando los alias cómodos (`matrix`, `barChart`,
  `button`). Los tres beben ahora de `SUPPORTED`, y hay pruebas que comprueban
  que no puedan divergir.
- **La prueba `live` de DAX no se ejecutaba nunca**: importaba nombres que ya no
  existen dentro de un `except Exception: return False`, así que el ImportError
  se leía como "no hay Desktop abierto" y salía omitida incluso con un modelo
  cargado. El import pasa a nivel de módulo: renombrar algo rompe la
  recolección en vez de disfrazarse de omisión.
- **La prueba de idempotencia en vuelo era intermitente**: se coordinaba por
  reloj (`sleep` de 0,15 s contra una espera de 1 s) y bajo la carga de la suite
  completa ese margen no siempre se cumplía. Ahora los dos hilos se citan por
  eventos, con dos barreras, y el resultado no depende de lo que tarde nadie.

---

## [1.0.0-rc.4] — 2026-07-31

**108 tools, 1008 pruebas** (2 omitidas), contrato congelado.

### Añadido

- **Elementos de composición**: `textbox`, `shape`, `image`, `actionButton` y
  `pageNavigator`. Hasta ahora el servidor solo sabía crear visuales de datos,
  así que no podía hacer una portada ni un menú de navegación. No llevan
  consulta: su contenido se define en `options` (texto, relleno, forma, página
  destino), y pedirles campos es un error explícito en vez de un visual vacío.
  Las estructuras se extrajeron de informes reales, no de la documentación.
- **Identidad visual**: `pbi_list_themes` y `pbi_apply_theme`, con tres paletas
  verificadas con el validador de la skill `dataviz` (banda de luminosidad,
  croma, separación bajo protanopia/deuteranopia/tritanopia y contraste). Los
  colores de estado son fijos en los tres temas: el semáforo significa lo mismo
  se pinte donde se pinte, y un color de estado nunca se reutiliza como serie.
  Aplicar un tema escribe el JSON, lo declara en `themeCollection` y lo registra
  en `resourcePackages`: sin las tres cosas Desktop lo ignora en silencio.
- La vista previa HTML dibuja los elementos de composición **con su aspecto**
  (color, texto, botones) en vez de como cajas de alambre, de modo que se puede
  juzgar una portada sin abrir Power BI Desktop.

### Corregido

- **`TYPE_MAP` declaraba claves en camelCase y la búsqueda las pasaba a
  minúsculas**: `cardVisual`, `tableEx` y `pivotTable` se anunciaban como
  soportados y se rechazaban al usarlos, con un mensaje que los listaba como
  válidos. Ahora hay una prueba que recorre todos los tipos anunciados.
- **El detector de layout trataba los elementos de composición como gráficos**:
  una portada normal producía una veintena de avisos falsos —un fondo *debe*
  estar debajo de todo, y un botón no es "demasiado pequeño para mostrar datos"—
  y entre ellos se perdía el aviso de verdad. Ahora el solape y el tamaño mínimo
  solo aplican a los visuales de datos; el orden Z se sigue comprobando en todos.

### Añadido (conversión)

- **Conversión `.pbix` → `.pbip`**, en archivo suelto o carpeta en lote:
  `pbi_convert_pbix_to_pbip`, `pbi_inspect_pbix` y `pbi_list_convertible_pbix`.
  - **Informe**: si el `.pbix` ya guarda PBIR (Desktop reciente lo hace) se copia
    byte a byte; si trae el `Report/Layout` heredado se traduce. La traducción
    resuelve los alias de tabla a nombres de entidad, fusiona `projections` con
    `prototypeQuery.Select`, convierte los enums numéricos a cadena y pasa
    `OrderBy` a `sortDefinition`. Las equivalencias se derivaron comparando un
    informe real guardado por Desktop en los dos formatos.
  - **Modelo**: el stream `DataModel` es un backup ABF comprimido con XPress9 y
    no se puede leer sin el motor, así que el `.pbix` se abre en Power BI
    Desktop y se serializa a TMDL con el `TmdlSerializer` oficial. Se reutiliza
    la sesión si el informe ya está abierto y solo se cierra lo que abrió la
    tool. El `.pbix` original nunca se modifica.
  - La conversión reporta lo que **no** tiene equivalente (`dropped`) en vez de
    perderlo en silencio: hoy, los marcadores del formato heredado.
  - Verificado sobre 72 informes heredados reales: 6705 documentos válidos
    contra los esquemas oficiales, y proyectos que Power BI Desktop abre.

### Corregido

- El serializador de TMDL corre sobre .NET Framework, que rechaza rutas de 260
  caracteres o más aunque Windows las admita. Ahora se serializa en un temporal
  corto y se traslada al destino con Python.
- Power BI Desktop tampoco abre un `.pbip` con rutas largas
  (`PBIProjectUtils.EnsureNotLong`). La conversión lo comprueba **antes** de
  escribir y aborta indicando cuánto sobra, en vez de dejar un proyecto que no
  abre.
- El descubrimiento de instancias daba por listo un motor que aún no había
  cargado el modelo: Desktop crea la base antes de poblarla y había una ventana
  de varios segundos en la que el TMDL habría salido sin tablas.

### Desbloqueado — esquemas que Microsoft no publica

Power BI escribe versiones de esquema antes de publicarlas: `visualContainer`
2.10.0 y 2.11.0 dan **404**. Eso bloqueaba **toda** escritura sobre cualquier
informe guardado con una versión reciente de Desktop, que es casi cualquiera.

Ahora se comprueba contra la versión anterior de la misma familia y se perdona
solo lo que una versión posterior pudo **añadir** (una propiedad nueva, un valor
nuevo de enumeración). Un tipo equivocado o un campo obligatorio ausente sigue
bloqueando. Medido sobre **275 archivos reales** que declaran 2.10 u 2.11: en
todos, lo único que discrepaba contra 2.7.0 era la cadena de versión del propio
`$schema`. La aproximación no cruza versiones mayores, y sin ninguna versión
anterior en caché se mantiene el bloqueo. El único esquema sin publicar que
queda sin alternativa es `bookmarks/` (plural), que algunos informes declaran
para el índice de marcadores; los que este servidor escribe —`bookmark/2.1.0`
y `bookmarksMetadata/1.0.0`— sí están publicados, así que crear marcadores se
comprueba entero.

### Añadido — autoría que faltaba

- **Formato condicional** (`pbi_set_conditional_format`): degradado de dos o
  tres paradas sobre fondo, texto o barras. Es lo que convierte una matriz de
  números en un mapa de calor. Con selector comodín, o el color solo pintaría
  la primera fila.
- **Filtros e interacciones**: antes se rechazaban por no saber serializarlos.
  La trampa del formato es que el filtro tiene dos mitades con reglas distintas
  —`field` referencia la tabla por nombre y la consulta interna por alias—, y
  escribir el nombre en ambas produce un filtro que Power BI ignora sin avisar.
- **Modelo semántico más allá de las medidas**: `pbi_create_calculated_column`,
  `pbi_create_relationship` y `pbi_create_hierarchy`.
- **Recursos**: `pbi_add_image_resource` y `pbi_list_report_resources`. Copiar
  una imagen sin declararla la deja invisible para Power BI, y declararla sin
  copiarla deja el visual vacío: los dos casos son mudos al abrir el informe.
- **`pbi_propose_dashboard`**: clasifica el modelo —qué columna es un estado,
  cuál una fecha, cuáles forman una familia comparable— y devuelve diseños
  completos con su porqué y un spec aplicable, en vez de esperar instrucciones.
- **`pbi_profile_data`**: perfila los VALORES, no la estructura. Detecta
  porcentajes fuera de 0-100, columnas vacías o de un solo valor. Sobre un
  modelo real encontró en segundos un `pct_codificado` que valía −800.
- **Marcadores**: `pbi_create_bookmark`, `pbi_list_bookmarks` y
  `pbi_delete_bookmark`. Se escribe el archivo Y el índice, porque sin índice
  Power BI no lo muestra aunque el archivo exista. Dentro de un marcador el
  filtro usa la clave `expression`, no `field` como en `filterConfig`: son
  estructuras parecidas con nombres distintos, y usar la de al lado produce un
  marcador que no restaura nada.
- **`pbi_set_storage_mode`**: import / directQuery / dual. Devuelve el modo
  anterior y cuántas particiones cambiaron, porque es un cambio que hay que
  poder deshacer sabiendo exactamente qué se tocó, y avisa de que DirectQuery
  exige consultas plegables y desactiva las columnas calculadas.
- **`pbi_create_calculated_table`**: deduce las columnas EJECUTANDO el DAX
  contra el modelo abierto, porque TMDL las exige declaradas y no se pueden
  adivinar leyendo la expresión.

### Corregido — precedencias y dialectos

- **La validación de campos miraba el modelo equivocado**: prefería el modelo
  en vivo sobre el TMDL del proyecto, así que bastaba tener otro `.pbix` abierto
  en Desktop para que las medidas recién escritas se dieran por inexistentes.
- **Dos dialectos de spec incompatibles**: se validaba con
  `{schema_version, page}` y se aplicaba con `{page_name}`. Un spec que pasaba
  la validación rebotaba al crearlo, con un error que ni mencionaba que hubiera
  dos formatos. Ahora `pbi_create_page_from_spec` acepta los dos.

---

## [1.0.0-rc.3] — 2026-07-31

**90 tools, 859 pruebas** (2 omitidas), contrato congelado.

### Añadido

- Distribución como plugin local de **Codex** y **Claude Code**, con manifiestos
  nativos, skill de instalación y preparación automática del runtime aislado.
- Arranque de instalación por MCP: no exige descargar, registrar ni ejecutar un
  binario propio. Python sigue siendo necesario para acceder a Power BI Desktop
  y a archivos locales.

### Cambiado

- Licencia del proyecto a **Apache License 2.0**, con `NOTICE` y metadatos de
  paquete coherentes. Los binarios de Microsoft siguen sin redistribuirse.
- Versión declarada: `1.0.0-rc.3` visible, `1.0.0rc3` en PEP 440.

---

## [1.0.0-rc.2] — 2026-07-31

Sustituye a `1.0.0-rc.1`, cuya matriz de CI estaba en rojo. **90 tools, 854 pruebas** (2 omitidas), contrato congelado.

### Corregido

- **El contract check dependía de la versión de Python.** `test_contract_matches_golden` fallaba en 3.10 y pasaba en 3.13, reportando las 90 tools como «descripción modificada» sin que nada del producto hubiera cambiado.

  Python 3.13 cambió cómo se guardan los docstrings ([gh-81283](https://github.com/python/cpython/issues/81283)): desde esa versión el compilador les quita la sangría. Las descripciones de las tools **son** sus docstrings, y el golden se generó con 3.14, así que en 3.10 sobraba exactamente la sangría (`pbi_list_tables` 130 → 138 bytes).

  El contrato normaliza ahora con `inspect.cleandoc` antes de congelar y de comparar. El golden no cambia ni un byte: lo que cambia es que 3.10 produzca lo mismo. `requires-python = ">=3.10"` se conserva — el producto sí soporta 3.10; el defecto estaba en cómo se congelaba el contrato.

- Las acciones del workflow suben a `checkout@v7`, `setup-python@v7`, `setup-node@v7` y `upload-artifact@v7`: las anteriores corren sobre un runtime Node que el runner marca como obsoleto.

### Cambiado

- Versión declarada: `1.0.0-rc.2` visible, `1.0.0rc2` en PEP 440.

---

## [1.0.0-rc.1] — 2026-07-31

Primera candidata pública. 90 tools, contrato congelado.

> **Sustituida por `1.0.0-rc.2`**: se publicó con la matriz de CI en rojo (`test (3.10)` fallaba y `build` quedaba saltado). El tag y su evidencia se conservan.

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
- `LICENSE` se publicó inicialmente como MIT; desde RC3 el proyecto usa Apache-2.0. `mcp` sigue acotada a `>=1.28.1,<2` con test de compatibilidad, porque el servidor depende del atributo privado `_mcp_server.version`.
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
