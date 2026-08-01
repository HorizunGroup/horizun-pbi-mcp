# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/).
Versionado semántico. **El contrato de las 34 tools originales nunca se rompe.**

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
