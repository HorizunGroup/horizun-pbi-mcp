# Horizun PBI MCP

Servidor **MCP** (Model Context Protocol) para trabajar con **Power BI Desktop local** y con proyectos **`.pbip`** desde Claude Code.

**v1.0.0-rc.11** — 116 tools, 1262 pruebas (3 omitidas, con su condición documentada). Cubre dos capas complementarias:

| Capa | Para qué | Cómo |
|---|---|---|
| **En vivo** (Power BI Desktop abierto en `localhost:<puerto>`) | Consultar datos (DAX), documentar el modelo, crear/editar medidas, refrescar | ADOMD.NET + TOM vía `pythonnet` |
| **En disco** (proyecto `.pbip`) | Generar/acomodar visuales, editar el modelo de forma durable | TMDL (modelo) + PBIR (informe), editando archivos |

> **Regla clave:** el endpoint local **solo expone la capa de DATOS** (modelo semántico). Los **visuales/páginas/layout NO** están en ese endpoint ni en ninguna API en vivo — se editan por archivos PBIR. Este MCP respeta esa separación: no intenta mover visuales "en vivo".

---

## Documentación

| Documento | Para qué |
|---|---|
| [`docs/INSTALL.md`](docs/INSTALL.md) | Instalar y registrar el servidor en Claude Code, Claude Desktop, Codex o un cliente stdio |
| [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md) | Las 34 tools del baseline: dominio, clase de riesgo, precondiciones |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Arquitectura actual, deuda estructural e invariantes |
| [`docs/CAPABILITY_MATRIX.md`](docs/CAPABILITY_MATRIX.md) | Convivencia con otros MCP de Power BI, con niveles de verificación |
| [`AGENTS.md`](AGENTS.md) | Reglas para modificar este repositorio sin romper el contrato |
| [`docs/TOOL_CATALOG.md`](docs/TOOL_CATALOG.md) | Las 116 tools por bloque, con su clase de riesgo |
| [`docs/DUAL_MODE.md`](docs/DUAL_MODE.md) | Por qué `mode="both"` está bloqueado (R15) |
| [`docs/VALIDATION.md`](docs/VALIDATION.md) | Las dos capas de validación PBIR y sus límites |
| [`docs/RELEASE_CHECKLIST.md`](docs/RELEASE_CHECKLIST.md) | Qué se comprueba antes de publicar |
| [`docs/BACKLOG.md`](docs/BACKLOG.md) | Lo que queda abierto, con evidencia y cómo comprobarlo |
| [`docs/TUTORIAL.md`](docs/TUTORIAL.md) | De la instalación a un dashboard, paso a paso |
| [`docs/SECURITY.md`](docs/SECURITY.md) | Modelo de amenazas, garantías y lo que **no** promete |
| [`docs/RECOVERY.md`](docs/RECOVERY.md) | Qué hacer cuando algo queda a medias |
| [`docs/PHASE_1A_DESIGN.md`](docs/PHASE_1A_DESIGN.md) | Diseño de la capa de seguridad |
| [`CHANGELOG.md`](CHANGELOG.md) | Historial de versiones |
| [`tests/fixtures/README.md`](tests/fixtures/README.md) | Estrategia de fixtures: sintéticos versionados + copia local ignorada |

---

## Qué hace

- **DAX en vivo:** ejecuta consultas contra el modelo abierto y devuelve columnas/filas con tiempos.
- **Documentación:** tablas, columnas, medidas, relaciones, jerarquías, roles (RLS) y análisis de calidad → Markdown.
- **Medidas:** crear/editar/borrar medidas DAX en el modelo abierto (`live`), en el archivo TMDL (`pbip`) o en ambos (`both`).
- **Refresh local:** refresca el modelo abierto en Desktop (no el Service).
- **PBIP:** abrir/validar proyectos, backups automáticos.
- **Conversión `.pbix` → `.pbip`:** informe a PBIR (copiado si el `.pbix` ya lo trae, traducido si guarda el formato heredado) y modelo a TMDL, archivo suelto o carpeta en lote.
- **Visuales PBIR:** listar/documentar visuales, crear visuales (clonando plantillas reales del informe), mover/redimensionar y acomodar por layouts.

## Qué NO hace

- No mueve ni crea visuales "en vivo" en el lienzo abierto (Power BI Desktop no expone API para eso). Los visuales se editan por archivos PBIR con el proyecto `.pbip`.
- No refresca ni publica en el **Power BI Service** (solo local).
- No extrae el modelo de un `.pbix` sin Power BI Desktop: el stream `DataModel` es un backup comprimido con XPress9 que solo el motor de Analysis Services sabe leer. Al convertir, el `.pbix` se abre en Desktop para serializar el modelo.
- No traduce los marcadores del formato **heredado** a PBIR: su modelo de estado es distinto y la conversión los reporta como pendientes (`dropped`) en vez de perderlos en silencio. Crear marcadores nuevos sí se puede (`pbi_create_bookmark`).
- No inventa campos ni medidas inexistentes al generar páginas.

---

## Requisitos

- **Windows** (Power BI Desktop es Windows-only) con **Power BI Desktop** instalado.
- **Python 3.10+** (probado en 3.14).
- **.NET Framework 4.x** (viene con Windows) — lo usa `pythonnet`.
- Dependencias Python: `mcp` (incluye FastMCP), `pythonnet`, `psutil`, `python-dotenv`.
- **DLLs de ADOMD.NET + TOM** (Analysis Services). Se descargan sin admin con `scripts/fetch_libs.py` (no requieren instalarse en el GAC).
- Para editar/crear **visuales**: el informe guardado como **`.pbip` con PBIR** activado.
- *(Opcional)* Tabular Editor **no es necesario** — ver [Decisiones técnicas](#decisiones-técnicas).

---

## Instalación

### Directa desde Codex o Claude (recomendada)

No necesitas descargar ni registrar un `.exe`, crear `.mcp.json` ni localizar
manualmente este repositorio. El plugin prepara un entorno Python aislado en la
carpeta de datos del cliente y verifica todas las descargas.

**Codex:**

```bash
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
codex plugin add horizun-pbi-mcp@horizun
```

**Claude Code:**

```bash
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

Al abrir la primera sesión, el plugin ejecuta toda la preparación en segundo
plano automáticamente. Consulta `pbi_install_status`; cuando termine, reinicia
el cliente y quedarán disponibles las 116 tools `pbi_*`. No hay descargas ni
scripts adicionales que el usuario deba ejecutar manualmente.

> **Límite técnico honesto:** no hay ejecutable propio, pero sí necesitas
> Windows, Power BI Desktop y Python 3.10+. El servidor debe correr localmente:
> un MCP remoto no puede acceder al motor local de Desktop ni a tus `.pbip`.

### Instalación manual para desarrollo

```bash
cd horizun-pbi-mcp

# 1) Dependencias Python
python -m pip install -r requirements.txt
#   o:  python -m pip install -e .

# 2) DLLs de Analysis Services (ADOMD.NET + TOM) -> carpeta libs/
#    Versión fijada (19.84.1) y verificada por SHA-256 antes de instalar.
python scripts/fetch_libs.py

# 3) Esquemas oficiales del PBIR (necesarios para ESCRIBIR)
#    Sin ellos, toda escritura PBIR falla con schema_unavailable.
python scripts/fetch_pbir_schemas.py

# 4) (opcional, recomendado) validador PBIR oficial de Microsoft
#    Requiere Node >= 20. Añade validación semántica del informe completo.
python scripts/fetch_report_validator.py

# 5) (opcional) configuración
copy .env.example .env    # y edítalo
```

Comprueba el resultado en cualquier momento:

```bash
python scripts/doctor.py
```

### Verificar

Con **Power BI Desktop abierto** en un informe:

```bash
python src/server.py     # arranca el servidor MCP (stdio); Ctrl+C para salir
```

Para una prueba rápida sin MCP, en Python:

```python
import sys; sys.path.insert(0, "src")
from config import get_session
from powerbi import desktop_discovery, dax_runner
s = get_session()
print(desktop_discovery.discover_instances())
desktop_discovery.select_model(s)
print(dax_runner.run_dax(s, 'EVALUATE ROW("ok", 1)'))
```

---

## Registro en un cliente MCP

Guía completa para **Claude Code, Claude Desktop, Codex y clientes stdio genéricos**: [`docs/INSTALL.md`](docs/INSTALL.md).

Cada cliente resuelve las variables de entorno, el directorio de trabajo y el intérprete de Python de forma distinta, así que en vez de una plantilla con `${VAR}` que falla en la mitad de ellos, hay un generador que resuelve las rutas absolutas de tu máquina:

```bash
python scripts/make_mcp_config.py --client all
```

Sólo imprime. Para crear el `.mcp.json` local de este repositorio (que está en `.gitignore`):

```bash
python scripts/make_mcp_config.py --client claude-code --write
```

Antes de registrar nada, comprueba la instalación:

```bash
python scripts/doctor.py
```

Sale con código **0** si todo lo obligatorio está bien. Distingue dependencia faltante, DLL faltante, servidor que no arranca, contrato MCP inesperado, Desktop cerrado, sesión obsoleta y múltiples instancias. Que Power BI Desktop esté cerrado **no** hace fallar el diagnóstico base (usa `--require-desktop` si quieres exigirlo).

### Variables de entorno (todas opcionales)

| Variable | Default | Descripción |
|---|---|---|
| `HORIZUN_PBI_MCP_LIBS_DIR` | `./libs` | Carpeta con las DLLs de ADOMD.NET/TOM |
| `HORIZUN_PBI_MCP_DOTNET_RUNTIME` | `netfx` | Runtime de pythonnet (`netfx` o `coreclr`) |
| `HORIZUN_PBI_MCP_MAX_ROWS` | `1000` | Límite de filas por defecto en DAX |
| `HORIZUN_PBI_MCP_OUTPUTS_DIR` | `./outputs` | Documentación y `change_log.md` |
| `HORIZUN_PBI_MCP_BACKUPS_DIR` | `./backups` | Backups de `.pbip` |
| `HORIZUN_PBI_MCP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `HORIZUN_PBI_MCP_DEFAULT_PBIP` | — | `.pbip` a abrir al iniciar |

---

## Tools disponibles (93)

> Catálogo completo por bloque: [`docs/TOOL_CATALOG.md`](docs/TOOL_CATALOG.md).
> Inventario del baseline con clase de riesgo y precondiciones: [`docs/TOOL_INVENTORY.md`](docs/TOOL_INVENTORY.md).
> Los nombres y firmas están congelados en `tests/golden/tools_v1.json` y verificados por `tests/test_tool_contract.py`.

**Conexión / DAX**
- `pbi_list_desktop_models` — lista modelos abiertos (puerto, connection string, catálogo, nº tablas).
- `pbi_select_model` — fija el modelo activo (por `port` si hay varios).
- `pbi_run_dax` — ejecuta DAX (`query`, `max_rows`).
- `pbi_test_connection` — valida la conexión activa.
- `pbi_validate_measures` — valida DAX de medidas SIN modificar el modelo (dry-run con `DEFINE MEASURE`); útil antes de crearlas.

**Documentación (Fase 3)**
- `pbi_list_tables`, `pbi_list_measures`, `pbi_list_relationships` — con `source: live|pbip`.
- `pbi_analyze_model_quality` — problemas típicos del modelo.
- `pbi_document_model` — documentación completa en Markdown a `outputs/`.

**Medidas (Fase 4)** — `mode: live|pbip|both`, `overwrite`
- `pbi_create_measure`, `pbi_update_measure`, `pbi_delete_measure` (destructiva: `confirm=true`).

**Refresh (Fase 5)**
- `pbi_refresh_model` — `type: full|calculate|clear_values`, `tables` opcional (local).

**Proyecto PBIP (Fase 6)**
- `pbi_open_pbip_project` (`path`), `pbi_validate_pbip_project`, `pbi_backup_pbip_project` (`mode: folder|zip`, `scope: report|model|both`).

**Conversión `.pbix` → `.pbip`**
- `pbi_inspect_pbix` — radiografía del archivo sin convertirlo ni abrir Desktop: formato del informe, si lleva modelo propio, páginas y recursos.
- `pbi_list_convertible_pbix` — vista previa de una carpeta: qué se copiaría, qué habría que traducir y cuáles necesitan Desktop.
- `pbi_convert_pbix_to_pbip` — genera el proyecto. Acepta un `.pbix` o una carpeta (`recursive`), y devuelve por archivo lo escrito, los avisos y lo que quedó fuera (`dropped`).

> El informe se traduce sin Desktop, pero el **modelo** obliga a abrir cada `.pbix` en Power BI Desktop (se reutiliza la sesión si ya está abierto, y se cierra si la abrió la tool). Con `include_model=false` se genera solo la mitad del informe, al instante. El `.pbix` original nunca se modifica.
>
> Power BI Desktop **no abre un `.pbip` con rutas de 260 caracteres o más**: elige un `out_dir` corto (`C:\pbip`). La tool lo comprueba antes de escribir y aborta con el detalle en vez de dejar un proyecto que no abre.

**Edición de modelo**
- `pbi_set_column_visibility` / `pbi_hide_columns` — ocultar/mostrar columnas (p.ej. IDs). `mode: live|pbip|both`.
- `pbi_set_relationship_direction` — filtro cruzado `single|both` de una relación. `mode: live|pbip|both`.
- `pbi_disable_auto_date_time` — activa/desactiva "Auto fecha y hora" (solo `pbip`).

**Informe PBIR (Fases 7–10)**
- `pbi_list_report_pages`, `pbi_list_visuals` (`page`), `pbi_document_report_layout`.
- `pbi_create_visual` — `page`, `visual_type`, `fields`, `position`, `title` (clona un visual existente como plantilla).
- `pbi_update_visual_position`, `pbi_arrange_visuals` (`layout: grid|dashboard|executive_summary|custom`).
- `pbi_generate_report_page` — página asistida a partir del modelo.

**HTML dentro de Power BI**
- `pbi_add_custom_visual` — registra un custom visual de AppSource en el informe (por defecto **HTML Content**, que renderiza HTML/SVG desde una medida DAX).
- `pbi_create_html_visual` — crea un visual HTML Content enlazado a una medida que devuelve HTML (`html_measure`).
- `pbi_create_measure` con `data_category: "ImageUrl"` — medidas que devuelven un data-URI **SVG** y se renderizan como imagen en tablas/matrices nativas.

**Generación de hojas por lenguaje natural**
- `pbi_page_building_blocks` — inventario del contenido (modelo + catálogo de visuales existentes + canvas) para diseñar una hoja.
- `pbi_preview_spec_html` — maqueta **HTML** de una hoja propuesta (revisar antes de escribir).
- `pbi_create_page_from_spec` — materializa una hoja PBIR completa desde un `spec` (clona visuales existentes por estilo).
- `pbi_export_page_html` — exporta una página existente a maqueta HTML.

Toda tool devuelve `{"ok": true/false, ...}`; en error incluye `error` (código) y `message` (mensaje original del motor, sin ocultar).

> **Flujo de generación de hojas:** `pbi_page_building_blocks` → (Claude interpreta tu instrucción y arma un `spec`) → `pbi_preview_spec_html` (revisas el HTML) → `pbi_create_page_from_spec` (se escribe el PBIR).

---

## Ejemplos de uso (en lenguaje natural con Claude)

- **Correr DAX:** *"Lista los modelos abiertos, selecciona el único, y corre `EVALUATE TOPN(10, Ventas)`."*
- **Documentar:** *"Documenta el modelo activo y analiza su calidad."* → genera `outputs/model_documentation_*.md`.
- **Crear medida:** *"Crea la medida `Margen % = DIVIDE([Utilidad],[Ventas])` en la tabla Ventas, formato `0.0%`, modo both."*
- **Listar visuales:** *"Abre el `.pbip` en C:/…/Informe.pbip y lista los visuales de la página 'Resumen'."*
- **Crear visual:** ver [`examples/sample_visual_specs.json`](examples/sample_visual_specs.json).
- **Acomodar página:** *"Acomoda la página 'Resumen' con layout executive_summary."*

Más DAX en [`examples/sample_queries.md`](examples/sample_queries.md).

> ⚠️ **Edición de PBIR y estado de Desktop:** las ediciones de **informe** (visuales/layout) se hacen en archivos; conviene hacerlas con **Power BI Desktop cerrado** y reabrir para verlas (si Desktop está abierto y guardas, sobrescribe los cambios en disco). Las ediciones de **modelo en vivo** (medidas `live`) requieren Desktop **abierto** y se persisten al guardar (Ctrl+S).

---

## Troubleshooting

- **No detecta el puerto / "No se detecto ningun modelo":** abre el informe en Power BI Desktop; el puerto cambia en cada arranque (el MCP lo descubre solo). Si usas la versión de Microsoft Store, igual se detecta por proceso.
- **`adomd_not_installed` / `tom_not_installed`:** ejecuta `python scripts/fetch_libs.py`. Verifica que `libs/Microsoft.AnalysisServices.AdomdClient.dll` exista.
- **`clr_not_available`:** falta .NET; prueba `PBI_MCP_DOTNET_RUNTIME=coreclr`.
- **Error DAX:** el mensaje del motor se devuelve tal cual en `message`. Revisa la sintaxis (EVALUATE, comillas).
- **`pbir_not_enabled`:** el informe no está en PBIR. Guarda como `.pbip` y activa *Formato de reporte mejorado (PBIR)* en Opciones → Características de vista previa (si aplica en tu versión) antes de guardar.
- **Power BI no recarga los cambios de visuales:** ciérralo y reábrelo; PBIR se carga al abrir, no en caliente.
- **Permisos/OneDrive:** si el `.pbip` está en OneDrive, cierra Desktop antes de editar archivos y espera a que OneDrive termine de sincronizar; los backups se guardan en `backups/`.

---

## Decisiones técnicas

- **TOM vía `pythonnet` (no Tabular Editor CLI).** Se evaluaron: (1) Tabular Editor 2 CLI, (2) `pythonnet` cargando TOM, (3) editar TMDL directo. Como `pythonnet` funciona en Python 3.14 y las DLLs de ADOMD.NET/TOM se pueden **vendorizar en `libs/` sin admin ni GAC**, se eligió cargarlas directamente con `pythonnet` (runtime `netfx`). Es más estable, sin dependencias externas de instalación, y da control total (crear/editar medidas y refrescar como lo hace Tabular Editor). La edición **durable** sigue disponible por **TMDL** en `.pbip`.
- **Visuales por clonación.** `pbi_create_visual` clona un visual existente del mismo tipo como plantilla (conserva el andamiaje de formato/tema) y solo cae a una plantilla mínima si no hay ninguno, avisando que debe validarse en Desktop.
- **Seguridad (Fase 11):** backup automático antes de cada escritura en `.pbip`; JSON atómico (no deja archivos corruptos); no sobrescribe JSON ilegible; validación de rutas; `change_log.md` en `outputs/`; operaciones destructivas requieren `confirm=true`.

## Limitaciones / riesgos abiertos

Ninguna de estas es un defecto que se pueda corregir desde aquí. Están documentadas porque afectan a lo que el servidor puede prometer.

### Esquemas que Microsoft no publica

Power BI Desktop escribe `visualContainer/2.10.0` en informes recientes, y esa URL devuelve **404** en el origen oficial. Lo mismo con `bookmarks/2.0.0`. **El CLI oficial de Microsoft tampoco puede validarlos** — emite `PBIR_SCHEMA_UNREACHABLE` y se salta la validación de esquema de esos archivos.

Consecuencia: las escrituras sobre archivos que declaren esos esquemas se **bloquean** con `schema_unavailable` (`rule=no_publicado_upstream`). Es deliberado y fail-closed: validar 2.10.0 contra 2.7.0 sería adivinar, y `additionalProperties: false` rechazaría propiedades nuevas legítimas.

Medido sobre un informe real de 443 documentos: 176 se validan, 240 quedan bloqueados por esta causa.

**G10 queda como excepción de release documentada.**

### `mode="both"` bloqueado

`live` exige Power BI Desktop abierto; `pbip` lo exige cerrado. No hay ningún estado del sistema en que ambos destinos puedan escribirse con seguridad en una llamada. Ver [`docs/DUAL_MODE.md`](docs/DUAL_MODE.md). **R15 abierto.**

### `filters` e `interactions` del page spec

Se **rechazan** con `unsupported_feature` indicando la ruta JSON exacta. No se descartan en silencio. Su serialización a PBIR está pendiente.

### Otras

- **PBIR** debe estar activado en el `.pbip`; `pbi_validate_pbip_project` lo comprueba.
- El **nombre amigable** del informe abierto no siempre es legible desde el motor (se reporta puerto + catálogo).
- El **parser TMDL** en disco es pragmático (tablas, columnas, medidas, relaciones); para metadatos ricos, usa la ruta `live`.
- `pbi_generate_report_page` es una **composición heurística**; no inventa campos y avisa lo que ignora.
- El servidor **arranca sin Node**; lo que queda bloqueado son las escrituras que necesiten el validador oficial.

---

## Estructura del proyecto

```
horizun-pbi-mcp/
├─ src/
│  ├─ server.py            # FastMCP + registro de tools
│  ├─ config.py            # settings + sesión (modelo/pbip activos)
│  ├─ logging_config.py
│  ├─ reporting.py         # documentación Markdown + calidad
│  ├─ powerbi/             # capa en vivo (ADOMD/TOM)
│  ├─ pbip/                # capa en disco (TMDL/PBIR)
│  ├─ tools/               # tools MCP por área
│  └─ utils/               # JSON, archivos, validación, change_log
├─ scripts/fetch_libs.py   # descarga DLLs de Analysis Services
├─ examples/  tests/  outputs/  libs/
├─ README.md  PLAN.md  pyproject.toml  requirements.txt  .env.example
```

## Pruebas

```bash
python -m pytest -q
```

**1262 pruebas, 3 omitidas.** La omisión es de entorno y dice cómo ejecutarla:

| Omitida | Condición |
|---|---|
| `test_run_dax_live` | Requiere una instancia de Power BI Desktop sirviendo un modelo. `python -m pytest -m live` |
| `test_no_llega_a_cero_por_acumular_infos` | Requiere que el modelo sintético dispare solo reglas informativas |

Marcadores disponibles:

```bash
python -m pytest -m "not packaging"     # rápido: omite wheel y sdist
python -m pytest -m live                # contra Power BI Desktop abierto
python -m pytest -m live_validator      # contra el CLI oficial de Microsoft
```

Verificar el contrato MCP (las 116 tools están congeladas):

```bash
python -m tests.contract_utils
```

Devuelve 0 si no hay rupturas, 1 si las hay, con un informe que dice **qué** cambió y **si rompe compatibilidad**.

Diagnóstico de la instalación:

```bash
python scripts/doctor.py
```

## Licencia

Código abierto bajo la licencia [Apache License 2.0](LICENSE). Consulta también
[NOTICE](NOTICE) para atribuciones y marcas de terceros.
