# Validación PBIR: dos capas, un oráculo de formato y sus límites

Antes de escribir un archivo del informe, Horizun PBI MCP lo valida en **dos capas independientes**. Ninguna sustituye a la otra, y ninguna promete más de lo que comprueba.

Para las propiedades que el propio servidor añade a `visual.objects`, una
tercera barrera específica compara además la estructura con el catálogo de
formato y con formas que Power BI Desktop exportó realmente.

---

## Capa 1 — validador interno por esquema

`services/pbir_schema.py`. Valida **cada documento por separado** contra el JSON Schema **oficial** que el propio archivo declara en `$schema`.

- Los esquemas son las **copias oficiales exactas** de `developer.microsoft.com`, con su cierre transitivo de `$ref` (22 documentos: `semanticQuery`, `filterConfiguration`, `formattingObjectDefinitions`, `visualConfiguration`…).
- Cada uno tiene su **SHA-256 fijado** en `src/services/schemas/pbir_manifest.json`.
- La validación la hace **`jsonschema`**, con el draft que declara cada documento (draft-07).
- Las referencias se resuelven contra un `referencing.Registry` construido **solo** con el manifiesto: allowlist, **cero acceso a red**, cero resolución de URLs arbitrarias.

**No se redistribuyen.** No declaran licencia ni permiso, así que se instalan aparte:

```bash
python scripts/fetch_pbir_schemas.py
```

**Sin esa caché, toda escritura PBIR falla con `schema_unavailable`.** No se degrada a "solo compruebo que sea JSON".

### Qué NO puede ver

Documentos sueltos. No ve nada que dependa de mirar el informe entero: si un objeto de formato existe para ese tipo de visual, si una columna ocupa un rol que solo admite medidas, si el nombre de un tema cuadra con el que referencia `report.json`.

---

## Capa 2 — validador oficial de Microsoft

`services/report_validator.py`. Ejecuta el CLI oficial sobre el `.Report` **completo**, después de escribir el lote y **antes de confirmar**.

```bash
python scripts/fetch_report_validator.py     # requiere Node >= 20
```

Paquete: `@microsoft/powerbi-report-authoring-cli@0.1.4` (MIT, Microsoft Corporation), versión exacta, tarball verificado por SHA-1 e integrity SHA-512 antes de instalar. **Ninguna operación normal ejecuta `npx` ni descarga `@latest`.**

Encuentra lo que la capa 1 no puede. Sobre un informe real de referencia: **44 errores y 12 avisos**.

| Diagnóstico | Qué detecta |
|---|---|
| `PBIR_FORMATTING_OBJECT_UNKNOWN` | Objeto de formato que no existe para ese tipo de visual |
| `PBIR_ROLE_KIND_MISMATCH` | Columna en un rol que solo admite medidas |
| `PBIR_THEME_FILE_NAME_MISMATCH` | El tema declara un nombre distinto del que referencia `report.json` |
| `PBIR_VISUAL_TYPE_UNKNOWN` | Visual personalizado no reconocido |
| `PBIR_VISUAL_DIR_WITHOUT_JSON` | Carpeta de visual sin su `visual.json` |

Se invoca **siempre con `--no-schema`**: por defecto el CLI descarga esquemas por red, y una mutación no puede depender de eso. Medido: en ese modo conserva los 44 errores semánticos y solo pierde el aviso de esquema inalcanzable, que ya cubre la capa 1.

**El código de salida del CLI es 0 incluso cuando falla.** Manda el recuento de diagnósticos, no el exit code.

---

## Oráculo de las rutas de formato administradas

`services/format_oracle.py` consulta `formatting effective-properties` del CLI
fijado y valida las rutas `(scope, group, property)` del visual completo,
incluidas las heredadas de una plantilla, con su tipo de valor y sus enums. Un
snapshot mínimo permite la misma barrera offline para las rutas administradas y
una prueba viva comprueba que no se separe del catálogo oficial.

El fixture sintético `format_objects_corpus.json` añade evidencia independiente
de visuales exportados por Desktop: conserva solo claves estructurales y tokens
de tipo. No contiene datos, identificadores, nombres, rutas ni conteos de los
informes de origen.

Sin el CLI oficial no se finge equivalencia completa; se conserva la barrera
estructural local. El oráculo tampoco afirma que una estructura válida produzca
una composición visualmente buena. Para comprobar que Desktop renderiza el archivo existe
`pbi_validate_desktop_render`; la evaluación estética/semántica de la captura
sigue siendo una capa distinta.

Antes de abrir un `.pbip`, `desktop_launcher` ejecuta además el validador TMDL.
Si encuentra errores estáticos o de `TmdlSerializer`, devuelve
`desktop_preflight_failed` con los hallazgos y no lanza Desktop. Esto evita que
un proyecto antiguo termine en una ventana `Sin título` con un Frown genérico;
por ejemplo, detecta una medida que colisiona con una columna de la misma tabla.

---

## Diagnósticos preexistentes

Un informe puede traer defectos propios. El de referencia trae 44. Atribuirlos a nuestra operación sería falso; ignorar los nuevos, peligroso.

El **baseline se toma antes de escribir**. Después se comparan diagnósticos **normalizados**: código, severidad, archivo relativo y ruta JSON. **Nunca el mensaje humano** — lleva rutas absolutas y texto variable.

| Situación | Resultado |
|---|---|
| Error nuevo | **Bloquea** y revierte |
| Más errores que antes | **Bloquea** |
| El mismo error, en otro archivo o ruta | **Bloquea** |
| Errores preexistentes idénticos | No se atribuyen a la operación |
| Aviso nuevo | No bloquea |
| Se resuelve un error preexistente | No bloquea |

Los preexistentes **no se corrigen automáticamente**, nunca.

---

## Selección de backend

| Capa 1 | Capa 2 | Comportamiento |
|---|---|---|
| disponible | disponible | Ambas. `validation_level = official_schema+report` |
| disponible | ausente | Solo esquema. `validation_level = official_schema` |
| no disponible | — | **Bloquea** con `schema_unavailable` |

En ningún caso se cae a "solo JSON parseable".

---

## El límite conocido: esquemas que Microsoft no publica

Power BI Desktop escribe `visualContainer/2.10.0` en informes recientes. **Esa URL devuelve 404** en el origen oficial. Igual con `bookmarks/2.0.0`.

**El CLI oficial tampoco puede validarlos**: los descarga de la misma URL y emite `PBIR_SCHEMA_UNREACHABLE`, saltándose la validación de esquema de esos archivos.

Es una incompatibilidad **upstream**, no de este servidor.

**Consecuencia práctica:** las escrituras sobre archivos que declaren esos esquemas se bloquean con `schema_unavailable` y `rule=no_publicado_upstream`.

Se optó por bloquear, no por adivinar. Validar 2.10.0 contra 2.7.0 daría falsos negativos —`additionalProperties: false` rechazaría propiedades nuevas legítimas— y falsos positivos en lo que 2.10.0 haya relajado.

Medido sobre un informe real de 443 documentos:

| | |
|---|---|
| Se validan | **176** |
| Bloqueados por esquema no publicado | **240** |
| Fuera de ámbito (`CustomVisuals/`, `StaticResources/`) | 25 |
| Incumplen de verdad | 2 |

**G10 queda como excepción de release documentada.**

---

## Códigos de error

| Código | Significa |
|---|---|
| `invalid_json` | El contenido no parsea |
| `schema_unsupported` | El `$schema` no está en el manifiesto, o un tipo PBIR conocido no lo declara |
| `schema_unavailable` | Los esquemas no están instalados, el hash no cuadra, o Microsoft no publica ese esquema |
| `schema_validation_failed` | Parsea, el esquema se conoce, y no cumple |
| `report_validation_failed` | El validador oficial encontró errores **nuevos** |
| `validator_unavailable` | Se necesita el validador oficial y no está |

Los errores dicen **archivo y ruta JSON** (`$.position.width`) y **nunca los valores**: son datos del informe.

---

## Verificar el estado

```bash
python scripts/doctor.py
```

```bash
python scripts/fetch_pbir_schemas.py --update      # recalcula el manifiesto
python scripts/fetch_report_validator.py --check   # estado del CLI oficial
```
