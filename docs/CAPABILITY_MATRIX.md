# Matriz de capacidades y convivencia

_Generado en la Fase 0. Fecha de inspección: 2026-07-30._

## Cómo leer esta matriz

Cada afirmación lleva su **nivel de verificación**. No se mezclan.

| Nivel | Significado |
|---|---|
| **Probada** | Se ejecutó en esta máquina y se observó el resultado |
| **Observada** | Se leyó del artefacto instalado (código, bundle, manifiesto) sin ejecutarlo |
| **Declarada** | Lo dice su documentación; no se comprobó |
| **Pendiente** | No se pudo verificar sin violar una restricción de la Fase 0 |

---

## Servidores inspeccionados

| Servidor | Versión | Estado | Nivel máximo alcanzado |
|---|---|---|---|
| **Horizun PBI MCP** (este repo) | 1.0.0-rc.10 | Presente, arranca | **Probada** — handshake stdio, 116 tools, 1255 pruebas, DAX en vivo, fixture PBIR |
| **powerbi-report-mcp** | 0.9.6 | Presente y compilado en `..\PowerBI MCP\powerbi-report-mcp\dist\index.js` | **Observada** — 57 nombres de tool extraídos del bundle y del README. **No ejecutado** |
| **@microsoft/powerbi-modeling-mcp** | 0.5.0-beta.11 | Descargado a temporal, extraído y leído. **No ejecutado** | **Declarada** — README + CHANGELOG + `index.js`. Ejecución **detenida** por condición de parada (§2.1) |

### Procedencia del paquete de Microsoft (inspección del 2026-07-30)

| Campo | Valor |
|---|---|
| Paquete | `@microsoft/powerbi-modeling-mcp` |
| Versión fijada | `0.5.0-beta.11` (**no** se usó `@latest`) |
| Origen | `https://registry.npmjs.org/@microsoft/powerbi-modeling-mcp/-/powerbi-modeling-mcp-0.5.0-beta.11.tgz` |
| SHA-1 | `fe7552d74cd3093a6935a11f7365c5eeffaa8ea1` — **verificado** contra el descargado |
| Integridad | `sha512-a5aO6glpBFIlaHHe+8LRunNPExJqsbnskRHDW5y7Vb7Jac85KqMUUEvxRuL2IkwJDDng0FEhfZNUbqw3ehmQIw==` |
| Licencia | **Microsoft Software License Terms (PREVIEW)** — propietaria, no OSS |
| Repositorio | `github.com/microsoft/powerbi-modeling-mcp` |
| Método | `npm pack` a un directorio temporal. **Sin instalación global. Sin `npx -y`. Sin tocar ninguna configuración MCP** |
| Estado del canal | Sólo existen versiones `0.5.0-beta.*`. **No hay release estable** |

---

## 2.1 Por qué se detuvo la ejecución

La autorización decía: *"Si el paquete exige autenticación o una conexión con efectos externos, detenerse."* Se cumplen **tres** condiciones de parada, halladas leyendo el paquete antes de ejecutarlo:

**1. Telemetría a Microsoft por el mero hecho de usarlo.** README §Data Collection:

> "The software may collect information about you and your use of the software and send it to Microsoft. […] **Your use of the software operates as your consent to these practices.**"

Arrancarlo es una conexión con efectos externos y consiente el envío de datos. No es una decisión que corresponda tomar a un agente.

**2. Aceptación de licencia por uso.** LICENSE, encabezado: *"BY USING THE SOFTWARE, YOU ACCEPT THESE TERMS."* Además restringe el uso: *"You may not use the software in a live operating environment unless Microsoft permits you to do so under another agreement."* En este equipo hay Power BI Desktop abierto con un modelo real.

**3. Auto-instalación de 48 MB en tiempo de ejecución.** El paquete de 34 KB es sólo un lanzador. `index.js:96` ejecuta `npm install @microsoft/powerbi-modeling-mcp-win32-x64@<version>` si el paquete de plataforma no está presente:

```js
execFileSync('npm', ['install', `${platformPackageName}@${version}`], { ... })
```

| Paquete de plataforma | Dato |
|---|---|
| Nombre | `@microsoft/powerbi-modeling-mcp-win32-x64` |
| Tamaño | **50.425.117 bytes (~48 MB)**, 7 archivos |
| SHA-1 | `296f8168c4982760b1b8ba0b381f0cdbbbfa3501` |

Es contenible (pre-descargando el paquete de plataforma fijado a un `node_modules` temporal), pero **no cambia nada**: los puntos 1 y 2 bastan para detenerse.

**Para levantar el bloqueo hace falta que el responsable del proyecto decida**, con conocimiento de la telemetría y de los términos de licencia, si autoriza arrancarlo. Sólo entonces subiría de *Declarada* a *Observada*/*Probada*.

---

## 2.2 Capacidad DECLARADA de @microsoft/powerbi-modeling-mcp 0.5.0-beta.11

Extraída de README y CHANGELOG. **Nada de esto está observado ni probado.**

| Área declarada | Evidencia |
|---|---|
| Tools por dominio | `database_operations`, `table_operations`, `column_operations`, `dax_operations`, operaciones de medidas, jerarquías de usuario |
| Conexión | Power BI Desktop, **workspace de Fabric** y carpetas **PBIP/TMDL**. Prompts `/ConnectToPowerBIDesktop`, `/ConnectToFabric`, `/ConnectToPowerBIProject` |
| Modelado | crear/actualizar tablas, columnas, medidas, relaciones; `IsKey`; `sortByColumn`; Expression Context; Direct Lake |
| DAX | ejecutar y validar, métricas de ejecución, **impersonación con roles y UPN** |
| Serialización | `ExportTMDL`, `ExportTMSL`, `DeployToFabric` |
| Refresh | `RefreshWithXMLA`, `RefreshWithAPI`, `CheckStatusOfRefreshWithAPI`, `CancelRefreshWithAPI` |
| Lotes | operaciones por lote nativas en todas las tools, con soporte transaccional (declarado) |
| Buenas prácticas | evaluación e implementación de best practices de modelado |
| Autenticación | Entra ID vía Azure Identity SDK; modos `AzureCLI`, `DefaultAzureCredential`, `managedidentity`, service principal |
| Transporte | stdio y **HTTP opcional**, con advertencia propia: *"no MCP-level auth in HTTP mode"* |

### Dos señales que conviene no pasar por alto

1. **`0.5.0-beta.11`: "Skip write-operation confirmation prompts by default. Provide `--require-confirmation` flag."** Las operaciones de escritura **no piden confirmación por defecto**. Es la política opuesta a la de este proyecto (`confirm=true` obligatorio en lo destructivo).
2. **Canal exclusivamente beta**, con cambios de ruptura recientes (`Rename Refresh to RefreshWithXMLA (breaking change)` en beta.3). Construir una dependencia sobre él es asumir su inestabilidad.

---

## 2.3 Impacto sobre la reorientación al modelo en vivo

La reorientación aprobada apunta a: capa en vivo, ADOMD/TOM, puente `live|pbip|both`, auditoría del modelo. **Ese es exactamente el territorio declarado por Microsoft**: su servidor dice cubrir modelado semántico sobre Desktop *y* PBIP/TMDL, DAX, refresh y best practices.

Dicho eso, y manteniendo la disciplina de niveles, lo declarado **no demuestra** nada sobre: seguridad de sus escrituras, comportamiento con Desktop abierto, rollback, telemetría real, ni si el puente `live↔pbip` que ofrecemos existe ahí de verdad. Aplica el mismo criterio que pediste para `powerbi-report-mcp`: **provisional hasta probar contratos y comportamiento**.

Lo que sigue siendo, hasta donde alcanza la evidencia, exclusivo de este proyecto:

| Capacidad | Estado frente a Microsoft (declarado) |
|---|---|
| HTML/SVG dentro de Power BI vía medida DAX + `data_category="ImageUrl"` | No aparece en su documentación |
| Creación de visuales y páginas PBIR | No aparece: su alcance declarado es el **modelo**, no el informe |
| Preview HTML de una hoja antes de escribirla | No aparece |
| Funcionamiento **sin telemetría y sin licencia propietaria** | Diferencia estructural, no de funcionalidad |
| Política de confirmación explícita en escrituras destructivas | Opuesta a su default declarado |

---

## 1. Horizun PBI MCP vs. @microsoft/powerbi-modeling-mcp

| Capacidad | Horizun PBI MCP | Microsoft MCP 0.5.0-beta.11 | Estrategia |
|---|---|---|---|
| DAX en vivo | ✅ **Probada** (`EVALUATE ROW` en 2 ms, puerto 58770) | 📄 **Declarada** (`dax_operations`, con métricas e impersonación) | Mantener compatibilidad |
| Medidas / TOM | ✅ **Probada** | 📄 **Declarada** (TOM 19.114.1.3) | Mantener; no priorizar duplicación |
| Lectura de modelo (TMDL) | ✅ **Probada** | 📄 **Declarada** (`ExportTMDL`, conexión a carpeta PBIP) | Mantener |
| PBIR (informe) | ✅ **Probada** | ❌ **No declarada** — su alcance es el modelo | **Sigue siendo nuestro** |
| Visuales / páginas | ✅ **Probada** | ❌ **No declarada** | **Sigue siendo nuestro** |
| HTML/SVG por medida | ✅ **Probada** | ❌ **No declarada** | **Sigue siendo nuestro** |
| Auditoría integral | 🟡 Parcial (7 reglas) | 📄 **Declarada** (best practices) | Comparar antes de expandir |
| Refresh | ✅ Local | 📄 **Declarada** (XMLA + API async, Fabric) | Ellos van más lejos |
| Power BI Service / Fabric | ❌ No | 📄 **Declarada** (workspaces, DeployToFabric, Entra ID) | No competir aquí |
| Confirmación en escrituras | ✅ `confirm=true` obligatorio en destructivas | 📄 **Declarada: desactivada por defecto** (`--require-confirmation` para activarla) | Diferencia de política, a nuestro favor |
| Licencia / telemetría | Apache-2.0, sin telemetría | Propietaria PREVIEW, telemetría por uso | Diferencia estructural |

**Ninguna fila de la columna Microsoft pasa de *Declarada*.** Está leída de su documentación, no ejecutada. El nombre de un paquete —y su README— no son evidencia de comportamiento.

---

## 2. Horizun PBI MCP vs. powerbi-report-mcp 0.9.6 — hallazgo relevante

Este servidor **ya está compilado en el equipo** y cubre justo el dominio que la auditoría identificó como el diferenciador de Horizun PBI MCP: PBIR.

**57 tools observadas** en su bundle, agrupadas:

| Área | Tools observadas (muestra) |
|---|---|
| Páginas | `pbir_create_page`, `pbir_delete_page`, `pbir_duplicate_page`, `pbir_rename_page`, `pbir_reorder_pages`, `pbir_set_active_page`, `pbir_set_page_visibility`, `pbir_update_page_size` |
| Visuales | `pbir_add_visual`, `pbir_get_visual`, `pbir_delete_visual`, `pbir_duplicate_visual`, `pbir_move_visual`, `pbir_change_visual_type`, `pbir_format_visual`, `pbir_set_visual_title`, `pbir_set_visual_sort`, `pbir_set_visual_interaction`, `pbir_update_visual_bindings` |
| Layout | `pbir_auto_layout`, `pbir_layout_grid`, `pbir_validate_wireframe` |
| Temas | `pbir_apply_theme`, `pbir_get_report_theme`, `pbir_set_report_theme`, `pbir_diff_report_theme`, `pbir_audit_theme_compliance`, `pbir_lookup_theme_property` |
| Filtros | `pbir_add_page_filter`, `pbir_list_filters`, `pbir_remove_filter`, `pbir_clear_filters`, `pbir_set_filter_pane` |
| Marcadores | `pbir_add_bookmark`, `pbir_list_bookmarks`, `pbir_rename_bookmark`, `pbir_delete_bookmark` |
| Lotes | `pbir_bulk_bind`, `pbir_bulk_delete_visuals`, `pbir_bulk_update_format` |
| Formato condicional | `pbir_set_conditional_format`, `pbir_set_datapoint_colors`, `pbir_set_page_background` |

### Solapamiento real

| Capacidad | Horizun PBI MCP | powerbi-report-mcp | Veredicto |
|---|---|---|---|
| Listar páginas/visuales | ✅ 3 tools | ✅ observadas | **Duplicado** |
| Crear/mover visual | ✅ 2 tools | ✅ observadas | **Duplicado** |
| Borrar/duplicar visual y página | ❌ | ✅ observadas | **Ellos van por delante** |
| Temas, marcadores, filtros | ❌ | ✅ observadas | **Solo ellos** |
| Operaciones por lotes | ❌ | ✅ observadas | **Solo ellos** |
| Formato condicional | ❌ | ✅ observadas | **Solo ellos** |
| Capa EN VIVO (ADOMD/TOM) | ✅ **Probada** | ❌ no observada | **Solo nosotros** |
| Medidas DAX (crear/editar) | ✅ **Probada** | ❌ no observada (tiene `pbir_manage_extension_measures`, que es otra cosa) | **Solo nosotros** |
| Refresh local | ✅ | ❌ no observada | **Solo nosotros** |
| Documentación del modelo | ✅ **Probada** | ❌ no observada | **Solo nosotros** |
| HTML/SVG por medida DAX | ✅ **Probada** | ❌ no observada | **Solo nosotros** |
| Generación declarativa de páginas + preview HTML | ✅ **Probada** | 🟡 `pbir_validate_wireframe` sugiere algo parecido | **A revisar** |
| Modo dual live+pbip | ✅ **Probada** | ❌ | **Solo nosotros** |

### Qué implica para el plan

La premisa de la auditoría anterior —"PBIR es el diferenciador principal"— **queda debilitada**: hay un servidor local, más maduro en ese dominio concreto, ya construido.

Lo que sigue siendo genuinamente único de Horizun PBI MCP:

1. **La capa EN VIVO** (ADOMD.NET + TOM contra `msmdsrv.exe`). Consultar datos reales, crear medidas, refrescar.
2. **El puente vivo↔disco** (`mode: live|pbip|both`), que ningún otro de los dos hace.
3. **HTML/SVG dentro de Power BI** vía medida DAX + `data_category="ImageUrl"`.
4. **Documentación y auditoría del modelo semántico.**

Esto no cierra la puerta a las fases 2–3, pero **cambia su justificación**: dejarían de ser "la ventaja competitiva" para ser "lo mínimo para que la capa en vivo sea utilizable de punta a punta". Es una decisión de producto, no técnica, y corresponde al responsable del proyecto.

---

## 3. Estrategia de convivencia

Los tres servidores pueden registrarse a la vez: los prefijos no chocan (`pbi_*` vs `pbir_*` vs los de Microsoft).

**Riesgo real de convivencia:** dos servidores escribiendo el mismo `.pbip` sin coordinación. Ninguno de los dos conoce los bloqueos del otro. Mitigación en la Fase 1: lock de archivo + `expected_state` + detección de modificación externa entre lectura y escritura.

**Sobre reutilizar su código:** no se ha copiado ni integrado nada. `powerbi-report-mcp` trae `LICENSE` propio; cualquier reutilización exigiría revisarlo primero. La vía recomendada es **registrar ambos servidores**, no fusionarlos.

---

## 4. Para completar esta matriz

- [x] ~~Descargar e inspeccionar `@microsoft/powerbi-modeling-mcp`~~ → hecho: versión fijada, integridad verificada, leído. Nivel **Declarada**.
- [ ] **Decisión del responsable:** autorizar el arranque de la beta de Microsoft, sabiendo que (a) su uso consiente telemetría a Microsoft, (b) usarlo acepta sus términos de licencia PREVIEW, (c) auto-instala 48 MB. Sólo entonces sube a *Observada*/*Probada*.
- [ ] Ejecutar `powerbi-report-mcp` con `tools/list` contra un fixture sintético → sube de *Observada* a *Probada*. **Sin bloqueos conocidos**: es un build local ya presente, sin telemetría ni licencia propietaria detectadas.
- [ ] Comparar contratos y comportamiento antes de duplicar cualquier capacidad PBIR nueva.

## 5. Reutilización de código: no

No se ha copiado ni integrado una sola línea de ninguno de los dos.

- `@microsoft/powerbi-modeling-mcp`: **licencia propietaria** (Microsoft Software License Terms, PREVIEW). Prohíbe expresamente el uso en entorno productivo sin otro acuerdo. Incorporar su código no es una opción.
- `powerbi-report-mcp`: trae `LICENSE` propio; habría que revisarlo antes de cualquier reutilización.

La vía correcta sigue siendo **registrar los servidores por separado**, no fusionarlos.
