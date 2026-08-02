# Plan — MCP propio para Power BI (Desktop local)

> Documento de diseño. Decidido con Claude Code el 2026-07-06.
> Estado: **plan aprobado, sin código todavía.** Arranque acordado: escribir código en fases (empezando por Fase 0) cuando se decida.

## Objetivo

Crear un servidor **MCP propio** para Power BI que hable con el **Desktop local** (el informe abierto en el PC) y cubra:

1. Consultar datos con **DAX** (lenguaje natural → DAX → resultados)
2. **Documentar** el modelo (medidas, tablas, relaciones, RLS)
3. **Crear/editar medidas** DAX
4. **Refrescar** el dataset (local)
5. **Generar y acomodar visualizaciones**

## Realidad clave: el "Desktop local" son DOS capas

Con Power BI Desktop abierto, se expone un motor de Analysis Services en `localhost:<puerto>`.
Ese motor **solo es la capa de DATOS** (modelo semántico). La capa de **INFORME**
(visuales, páginas, layout del lienzo) **NO** está en ese endpoint ni en ninguna API en vivo.

| Objetivo | ¿En vivo (endpoint local)? | Cómo |
|---|---|---|
| Consultar datos con DAX | Sí | ADOMD → `executeQueries` contra `localhost` |
| Documentar el modelo | Sí | Leer metadatos vía TOM |
| Crear/editar medidas DAX | Sí | TOM escribe al modelo abierto (como Tabular Editor) |
| Refrescar dataset (local) | Sí | TOM `RefreshType.Full` (workspaces en nube quedan fuera) |
| **Generar/acomodar visuales** | **No en vivo** | Solo por archivos PBIP/PBIR en disco |

## Decisión: se trabaja en formato PBIP (proyecto)

Confirmado: el usuario usará **`.pbip`** (Power BI Project). Guardar el informe así lo
descompone en archivos de texto en disco:

```
MiInforme.pbip
├─ MiInforme.SemanticModel/
│   └─ definition/ …            ← TMDL: tablas, medidas, relaciones (texto editable)
└─ MiInforme.Report/
    └─ definition/
        └─ pages/<pagina>/visuals/<visual>/visual.json   ← PBIR: cada visual es un JSON
```

- **TMDL** (Tabular Model Definition Language) = modelo y medidas como texto.
- **PBIR** (formato de informe mejorado) = cada visual es un JSON con tipo, campos y posición (x, y, alto, ancho).

Así, "generar y acomodar visuales" = **escribir archivos JSON**. Power BI Desktop detecta el
cambio en disco y recarga.

> **A VERIFICAR antes de la Fase 3:** PBIR fue *preview* durante 2024. Confirmar si en la
> versión instalada de 2026 ya es GA y si la vista previa está activada
> (Opciones → Características de vista previa). Es el supuesto técnico crítico del plan.

## Arquitectura recomendada (híbrida)

```
Claude Code
    │ MCP (stdio)
    ▼
Servidor MCP  (Python + FastMCP)
    ├─ ADOMD → localhost:<puerto>   ← consultas DAX + documentar (EN VIVO, rápido)
    ├─ TOM  → localhost:<puerto>    ← crear/editar medidas + refrescar (EN VIVO)
    └─ archivos TMDL + PBIR         ← generar/acomodar visuales + medidas durables (DISCO)
```

**Regla de reparto:**
- Lo que es **consulta o dato** → endpoint en vivo (rápido, inmediato).
- Lo que es **autoría durable** (visuales, y opcionalmente medidas) → archivos PBIP.

## Lenguaje / librerías

- **Servidor MCP:** Python con **FastMCP** (lo más rápido de montar).
- **DAX en vivo:** `pyadomd` (requiere el cliente **ADOMD.NET** instalado).
- **Escribir al modelo (TOM):** Python es incómodo con TOM (es .NET). Dos caminos:
  - `pythonnet` cargando TOM, **o**
  - invocar **Tabular Editor 2 (CLI, gratis)** desde el servidor ← **recomendado** (robusto, evita interop).
- **Visuales PBIR:** edición de JSON pura, agnóstico del lenguaje (Python directo).

## Roadmap por fases

- **Fase 0 — Conexión + DAX.** Descubrir el puerto local del Desktop, `pyadomd`, tool `pbi_run_dax`.
  Es el "hola mundo" y ya da valor.
- **Fase 1 — Documentar.** `pbi_document_model` → medidas/tablas/relaciones a Markdown o Excel.
- **Fase 2 — Medidas.** Crear/editar DAX (Tabular Editor CLI o TMDL).
- **Fase 3 — Visuales.** Generar y acomodar visuales escribiendo PBIR. **Fase de mayor riesgo** (depende de PBIR GA).
- **Fase 4 — Refresh/gestión** local.

## Riesgos / decisiones abiertas

- **PBIR en preview** → la Fase 3 (visuales) es la de mayor riesgo. Validar pronto con un `.pbip`
  de prueba antes de invertir en el resto.
- **Puerto local dinámico** → el Desktop cambia de puerto en cada arranque. El MCP debe
  descubrirlo (leer el proceso `msmdsrv` o el archivo de conexión temporal de PBI Desktop).
- **En vivo vs archivos** → editar medidas en vivo por TOM no queda en el `.pbix`/`.pbip` hasta
  guardar. Decidir si el MCP escribe en vivo, en archivos, o ambos coordinados.
- **Prerrequisitos de entorno:** cliente ADOMD.NET instalado; Tabular Editor 2 si se usa esa vía.

## Próximo paso acordado

Guardar este plan (hecho). Cuando se decida construir, arrancar por **Fase 0** — y antes,
**verificar el estado de PBIR** en la versión instalada.

---

## Actualización 2026-07-07 — Implementación (validado en máquina)

Construido e integrado. Validaciones técnicas realizadas **contra el entorno real**:

- **`pythonnet` funciona en Python 3.14.3.** `import clr` OK con runtime `netfx`.
- **ADOMD.NET + TOM no estaban instalados** (ni GAC ni Program Files) ni Tabular Editor.
  → Se **vendorizan las DLLs** de `Microsoft.AnalysisServices.*` (v19.84.1, net45) en `libs/`
  vía `scripts/fetch_libs.py` (NuGet, **sin admin/GAC**).
- **DAX en vivo validado:** conexión a `localhost:<puerto>`, descubrimiento de catálogo,
  `EVALUATE`, DMVs, y lectura de modelo con TOM — todo OK contra Desktop abierto.
- **PBIR confirmado GA** en los `.pbip` de prueba (`definition.pbir` v4.0, `definition/pages/<id>/`).
- **TMDL** con indentación por tabs (medida = 1 tab, props = 2, expresión = 3).

### Decisión técnica (cambia respecto al plan original)

> **TOM vía `pythonnet` con DLLs vendorizadas** — en vez de **Tabular Editor 2 CLI**.
> Motivo: pythonnet es estable aquí, las DLLs se obtienen sin instalar nada en el sistema,
> y así se evita una dependencia externa (TE2 no estaba instalado). Da el mismo poder
> (crear/editar medidas, refrescar) que TE2 y mantiene la edición durable por TMDL.

### Bugs encontrados y corregidos durante la validación (vía smoke tests)

1. **Deadlock en `config.get_session`**: tomaba un `Lock` no reentrante y volvía a pedirlo
   dentro de `get_settings`. → locks separados + resolver settings fuera del lock.
2. **Colisión de backups en el mismo segundo**: `timestamp()` a segundos hacía fallar
   `copytree`. → sufijo aleatorio corto en el nombre del backup.
3. **Heurística de "ID visible"** no detectaba camelCase (`ClienteID`). → patrón ampliado.

### Estado por fases

Fase 0–11 implementadas y probadas (live + archivos). 23 tools MCP registradas.
33 pruebas `pytest` en verde (las que requieren Desktop se saltan). README y ejemplos listos.

### Revisión adversarial multi-agente (5 dimensiones + verificación)

Se corrió una revisión con subagentes (find → verify) sobre `src/`. De 26 hallazgos
crudos, 15 confirmados. **Correcciones aplicadas:**

- **Path traversal** en `project_locator` (crítico): rutas de `artifacts`/`byPath` del
  `.pbip` ahora se validan con `ensure_within_base` contra el **directorio del proyecto**
  (no el del report — el `.SemanticModel` es un hermano `../`, que es legítimo).
- **Cuelgue por puertos muertos**: `AdomdClient` añade `Connect Timeout` a la cadena y
  fija `CommandTimeout`. Evita hangs indefinidos con archivos de puerto obsoletos.
- **`.NET .Message`** (PascalCase) en `desktop_discovery` (antes usaba `message`).
- **TMDL multilínea**: líneas de expresión (incl. en blanco) se indentan a 3 tabs.
- **Validación en modo `live`** (nombre/expresión de medida), consistente con `pbip`.
- **Modo `both`**: si un lado falla, el otro igual se intenta y se reporta la inconsistencia.
- **Referencias de campo vacías/malformadas** (`Tabla[]`, `[`) ahora se rechazan.
- `except` demasiado amplios estrechados (find_template, list_visuals).

**Hallazgo rechazado con criterio:** "citar valores de propiedad TMDL (formatString/
displayFolder)". Es **incorrecto**: los valores de propiedad TMDL toman el resto de la
línea (los espacios son válidos sin comillas) y citar `formatString` lo rompería
(`#,0` debe ir sin comillas). No se aplicó.
