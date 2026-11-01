# Verificación de `feat/perfil-local` contra Power BI Desktop

Fecha: 31-oct-2026. Rama verificada: `feat/perfil-local` en `e3b90c0` (PR #64),
comparada con `main` en `2c6d075` (v2.1.3). Arreglo nuevo: rama hija
`fix/perfil-local-tabla-calculada`.

Todo con datos sintéticos (CSV de cuatro filas creados por los scripts) y con
proyectos creados por los propios scripts. Ningún tablero de cliente se abrió.

## Entorno y método

| | |
|---|---|
| Sistema | Windows 11 Home 26300 |
| Power BI Desktop | 2.159.883.0 (octubre 2026) |
| Python | 3.12 en venv; `.[test]` + `comtypes` (extra `export`) |
| Cliente | el servidor se arranca por stdio y se le habla con el cliente MCP del SDK `mcp`, como lo haría Claude o Codex |
| Aislamiento | `HORIZUN_PBI_MCP_OUTPUTS_DIR` y `HORIZUN_PBI_MCP_BACKUPS_DIR` apuntan a carpetas de trabajo, fuera del proyecto; cada Desktop que se abre es del script y se cierra por su PID y línea de comandos |

`main` se probó en un worktree aparte, con el mismo intérprete y
`PYTHONPATH=<worktree>/src`, para que la única diferencia fuera el código.

Los scripts están en [`scripts/verificacion_perfil_local/`](../scripts/verificacion_perfil_local/):

| Script | Qué hace |
|---|---|
| `repro.py` | puntos 1, 2, 3 y 4: catálogos `full`/`local`, crea un PBIP, importa un CSV con ruta **relativa**, crea la tabla calculada `Semanas`, la relaciona con `Ventas[semana]`, valida y abre en Desktop |
| `dialogo.py` | punto 3 con la rama: abre el proyecto roto de `main` saltándose el preflight (solo en ese proceso), para que Desktop muestre su diálogo |
| `repro_ruta.py` | punto 4 (ruta absoluta) de punta a punta: importar con ruta relativa → abrir → refrescar → contar con DAX |
| `bench.py` | banco de 18 tareas con Claude Haiku (`claude -p --model haiku`) y qwen3:8b (Ollama) |

## Resultado por punto

| # | Defecto | `main` | rama | Veredicto |
|---|---|---|---|---|
| 1 | `pbi_create_calculated_table` escribía `sourceColumn: semana` | escribe `sourceColumn: semana`; Desktop no abre el proyecto: «La relación "…" usa un Id. de columna 20 no válido» | escribe `sourceColumn: [semana]`; Desktop abre el proyecto con la relación en 17,8 s (2 tablas servidas) | **Arreglado** |
| 2 | `pbi_validate_tmdl` no lo detectaba | 0 hallazgos sobre ese proyecto | el validador de la rama sobre el proyecto de `main`: `tmdl_calculated_source_column_unbracketed`, severidad `error`. `pbi_open_in_desktop` lo frena en el preflight en 1,5 s sin lanzar Desktop | **Arreglado** |
| 3 | `pbi_open_in_desktop` devolvía `desktop_timeout` genérico | `desktop_timeout` a los 62,6 s (plazo 60 s; con el plazo por defecto serían 300 s): «puede estar pidiendo credenciales…» | `desktop_load_failed` a los 18,9 s con `dialog_title` «Se encontraron problemas» y `dialog_text` «Hay un problema con el contenido de definition en Power BI Project. La relación "…" usa un Id. de columna 20 no válido.»; el Desktop lanzado se cierra solo | **Arreglado** |
| 4a | catálogo reducido | `HORIZUN_PBI_MCP_PROFILE=local` se ignora: 139 tools, ~47 000 tokens | `local`: 22 tools, 17 968 caracteres, ~4 500 tokens (−90 %); `full`: 140 (139 + `pbi_find_tool`) | **Funciona** |
| 4b | relaciones en el perfil local | — (no hay perfil) | `pbi_create_relationship` está en `local`; el banco la usa (tareas 8, 9, 10) | **Funciona** |
| 4c | rutas absolutas en tablas desde archivo | con `path="datos/ventas.csv"` la M queda `File.Contents("datos\ventas.csv")`; Desktop abre pero el refresco falla: «La ruta de acceso al archivo proporcionado debe ser una ruta de acceso absoluta válida» | la M queda con la ruta absoluta; refresco correcto: 3 filas, `SUM(monto)` = 225 | **Arreglado** |

Tokens del catálogo: caracteres del `tools/list` serializado ÷ 4. Es una
estimación, la misma regla que usa el presupuesto de `tests/test_local_profile.py`.

## Lo que encontró el banco y se arregló

**A. `pbi_create_calculated_table` sin `columns` y con Desktop cerrado.** Sin
`columns`, la tool deduce las columnas ejecutando la expresión en el motor de
Desktop, así que respondía el `no_active_model` genérico: «usa mode='pbip' con
Desktop cerrado». Esta tool no tiene `mode`, Desktop ya estaba cerrado y
abrirlo tampoco sirve, porque escribir el TMDL exige cerrarlo. La descripción
local decía además «columns opcional». Las dos fallas de Haiku en el perfil
local (tareas 6 y 10) son este bucle, y también la mayoría de las fallas de qwen.

Arreglo (`cecd103`): el error conserva su código (`no_active_model`), pero
ahora dice la única salida («Repite la llamada con
`columns=[{"name": …, "data_type": …}]`») y lista los tipos válidos. La
descripción local dice que `columns` hace falta. Prueba:
`tests/test_tabla_calculada_sin_columnas.py`. Tres de sus cuatro pruebas
fallan en `e3b90c0` y pasan con el arreglo; la cuarta fija que no se escribe nada.
El contrato no cambió (`python -m tests.contract_utils`: «El contrato MCP no cambio»).

## Lo que encontró el banco y queda pendiente

**B. Los modelos pequeños copian el `request_id` de una respuesta a la
siguiente llamada.** Cada respuesta trae `request_id`, el esquema lo ofrece
como parámetro, y qwen3:8b lo reutiliza en llamadas distintas: 25 de sus 38
llamadas fallidas en el perfil local son `idempotency_conflict`, repartidas en
6 tareas. Con el arreglo A siguen siendo 19 de 27, y explican 2 de las 4
fallas que le quedan. Haiku no lo hace (0). Arreglarlo pide una decisión: quitar
`request_id` del esquema y de las respuestas compactas **solo en el perfil
`local`** (el perfil `full` y su contrato no cambian).

**C. Tipos y nombres con otra capitalización.** qwen pidió
`data_type: "datetime"` (válido: `dateTime`) y el visual `bar_chart` (válido:
`barChart`). Los dos errores ya dicen qué valores valen; el modelo anunció que
reintentaría y se detuvo. Aceptar estos valores sin distinguir mayúsculas sería
otra mejora, no un defecto.

## Banco del perfil local

18 tareas sobre una copia nueva del mismo proyecto base (`Ventas`, `Productos`,
`Calendario` y la medida `Total Monto`): 4 medidas, 3 tablas calculadas, 2
relaciones, 1 tabla calculada con relación, 3 páginas de 3 visuales, 2
validaciones de TMDL (una sobre un proyecto con el defecto del punto 1), 1
columna calculada, 1 consulta del modelo y 1 importación de CSV.

- **Acierto**: se juzga en disco (TMDL/PBIR) o, en validar y consultar, por
  una línea pedida en la respuesta. Nunca por lo que el modelo dice que hizo.
- **1.ª acción correcta**: la primera llamada que no es de lectura
  (`pbi_open_pbip_project`, `pbi_list_tables`, `pbi_start_here`,
  `pbi_health_check`, `pbi_find_tool`, `pbi_get_power_query`) es la
  herramienta esperada. Mirar las tablas antes de crear una medida no cuenta
  como error.
- **Tokens**: entrada (incluida la caché) + salida, sumados en las 18 tareas.

| Modelo | Perfil | Commit | Aciertos | 1.ª acción correcta | Llamadas (con error) | Tokens entrada | Tokens salida |
|---|---|---|---|---|---|---|---|
| Haiku | `full` | `e3b90c0` | 18/18 | 10/18 | 73 (0) | 5.320.563 | 23.821 |
| Haiku | `local` | `e3b90c0` | 16/18 | 17/18 | 72 (0) | 1.016.244 | 24.508 |
| Haiku | `local` | `cecd103` | **18/18** | 17/18 | 61 (0) | **797.362** | 19.452 |
| qwen3:8b | `local` | `e3b90c0` | 12/18 | 17/18 | 74 (38) | 341.051 | 6.284 |
| qwen3:8b | `local` | `cecd103` | **14/18** | 16/18 | 62 (27) | 275.572 | 5.899 |

En resumen:

- Con Haiku, el perfil `local` (con el arreglo A) acierta lo mismo que `full`
  (18/18) con un 85 % menos de tokens de entrada, y acierta más a la primera
  (17/18 contra 10/18). En `full` el modelo pasa por herramientas intermedias
  (`pbi_prepare_project`, `pbi_validate_page_spec`, `pbi_apply_plan`).
- Antes del arreglo A, las dos fallas de Haiku en `local` (tareas 6 y 10) eran
  el bucle de `columns`.
- Las 4 fallas que le quedan a qwen3:8b son los pendientes B (tareas 4 y 10,
  `request_id` copiado) y C (tarea 7 `datetime`, tarea 11 `bar_chart`).
- qwen3:8b en `full` no se completó: con el catálogo de ~47 000 tokens
  truncado a su contexto, una sola tarea tardó unos 15 min (acertó, con 62.864
  tokens de entrada; en `local` la misma tarea gasta 24.837). Se cortó para no
  demorar la entrega.

Límites del banco:

- Una corrida por tarea: no mide variación entre corridas.
- Haiku corre con `claude -p` en la sesión de la persona, con
  `--strict-mcp-config --tools ""`: solo ve este servidor MCP, pero su
  contexto incluye el prompt de Claude Code y la configuración de usuario. Esa
  carga es la misma en `local` y `full`, así que la diferencia entre perfiles
  sí es comparable; el valor absoluto no es el de un cliente mínimo.
- qwen3:8b corre con un bucle propio contra la API de Ollama (`num_ctx` 32 768,
  temperatura 0, sin *thinking*, máximo 12 turnos). En `full` el catálogo
  (~47 000 tokens) no cabe en ese contexto.

## Suite

| Rama | pytest | ruff | mypy | doctor | contrato |
|---|---|---|---|---|---|
| `feat/perfil-local` (`e3b90c0`) | 3835 passed, 12 skipped (36 min 47 s) | ok | ok | 0 (aviso: no hay `.mcp.json`) | sin cambios |
| `fix/perfil-local-tabla-calculada` (`cecd103` + este informe) | 3836 passed, 17 skipped, 2 failed (35 min 58 s); las 2 (`test_packaging.py::test_una_instalacion_pip_pura_no_se_declara_operativa[wheel\|sdist]`) pasan al repetirlas con `libs/` en el worktree | ok | ok | 0 con `libs/` (1 sin ella) | sin cambios |

Las 2 fallas y el `doctor` en rojo de la primera corrida venían de que el
worktree no tenía `libs/`: está en `.gitignore` y `git worktree` no la copia.

La suite incluye pruebas *live* que abren Power BI Desktop real cuando está
instalado; corrieron en esta máquina.
