# Conducir Power BI Desktop desde fuera, con evidencia que se sostiene

Borrador de descripción de PR para `codex/multiagent-audit-fixes`.

## De dónde viene

Una tanda real de uso intensivo —unas cuarenta llamadas recorriendo editar →
validar → abrir → capturar → exportar con varias ventanas de Power BI Desktop
abiertas— dejó ocho problemas. El diseño de verificación del repositorio
aguantó; lo que falló fueron carreras de UI Automation, contención de foco,
sesiones caducadas y funciones incompletas.

Después la rama pasó por una auditoría independiente y por tres rondas contra
un Desktop real. **Varias de las correcciones resultaron equivocadas y hubo que
sustituirlas**; eso está contado abajo, porque es la parte útil.

## Problemas originales y comportamiento resultante

| Problema reportado | Comportamiento ahora |
|---|---|
| «Guardar como» fallaba de forma intermitente con los mismos argumentos | Cada fase transitoria reintenta con tope de tiempo y vuelve a localizar sus controles; la ruta se teclea con cadencia decreciente; un desplegable sin cargar ya no se confunde con un formato ausente |
| La exportación rechazaba una ventana con título `Sin título - Power BI Desktop` | La identidad se sondea con tope: un título provisional espera, uno estable de otro documento se rechaza al instante |
| Capturas vacías con `frame_settled=true` y `data_loaded=true` | Cuatro señales separadas: identidad asentada, fotograma estable, datos cargados y fotograma uniforme; ninguna se declara verdadera sin la anterior |
| Tras `leave_open=true`, `pbi_close_desktop` no encontraba la ventana | La exportación devuelve `desktop_session` (pid + hora de arranque) y el cierre lo acepta, verificando identidad y rechazando un PID reciclado |
| Reiniciar Desktop dejaba la sesión con un puerto muerto | Las lecturas reseleccionan automáticamente la única instancia viva, aunque sea el PBIX recién exportado; las mutaciones siguen exigiendo identidad alta y coincidencia con el proyecto activo |
| Faltaba exportación nativa a `.pbit` | `format='pbit'` en `pbi_export_pbix` y `pbi_finalize_delivery`, con su diálogo de plantilla y verificación estructural |
| Elegir página y zoom exigía cerrar y reabrir | Se hacen en la ventana abierta bajo `confirm_reuse`, sin tocar `pages.json` |
| Faltaba lectura live de Power Query y particiones; parámetros inconsistentes | Lectura por DMV, alias con conflicto explícito y resolución de carpetas de proyecto |

## Instalación en clientes de escritorio gratuitos

La misma entrega queda instalable en las dos aplicaciones gratuitas de
escritorio. El instalador de Windows registra el marketplace personal que lee
ChatGPT Desktop, preserva sus entradas, respalda antes de cambiar y rechaza un
JSON inválido sin sobrescribirlo. Claude Desktop recibe un asset
`horizun-pbi-mcp-2.1.0.mcpb` de instalación con doble clic; Claude aporta UV y
Python para arrancar el bootstrap, sin Claude Code ni edición manual de
`claude_desktop_config.json`.

El `.mcpb` se construye solo desde el árbol confirmado de Git, por lo que no
puede capturar outputs, backups, PBIX/PBIP ni credenciales del equipo que crea
la release. Entra en `SHA256SUMS`, pasa el validador oficial
`@anthropic-ai/mcpb@2.1.2` y se ejercitó desde el ZIP extraído: inicialización
MCP correcta y exposición de `pbi_install_runtime` y `pbi_install_status` con
el entorno UV administrado.

## Lo que la evidencia demuestra, y lo que no

Esta es la parte que más cambió durante la revisión.

- **El zoom.** «Ajustar a la página» es un `Button` sin `Toggle` ni
  `SelectionItem`: medido contra Desktop real ofrece `Invoke` y
  `LegacyIAccessible`, y no hay estado que releer. Lo que sí publica Power BI
  es el nivel de zoom (`Informe ampliado a 72 %`), capturado entre el instante
  anterior a pulsar y el posterior. Eso demuestra que **el nivel de zoom cambió al pulsar**, no que
  el modo resultante sea «ajustar a la página». Un cambio de píxeles viaja como
  `visual_change` y nunca decide `verified`: abrir la cinta para llegar al
  control ya cambia la imagen. El bloque publica además `verified_means`, para
  que quien solo lea `verified` vea hasta dónde llega esa prueba; las
  descripciones de `pbi_validate_desktop_render` y `pbi_open_and_refresh` dicen
  lo mismo.
- **La ausencia de cambio no demuestra lo contrario.** Si nada cambia, la
  respuesta no afirma que ya estuviera ajustada: dice que no se puede
  distinguir de que la acción no llegara.
- **La guardia de foco acota, no impide.** Entre consultar la guardia y que
  `SendInput` entregue la tanda hay una ventana que Windows no deja cerrar, así
  que puede escaparse una tanda (20, 8 o 4 caracteres). Hay una prueba que fija
  ese límite para que nadie lo describa como imposibilidad.
- **Una página llamada como una pestaña de la cinta sí se puede elegir.** La
  afirmación anterior de que no se podían distinguir era falsa: la de cinta
  lleva `AutomationId='view'` bajo `ms-OverflowSet`/`tablist` y la de página no
  lleva id y cuelga de `carouselScrollPane` dentro de
  `explorationNavigationContent`. Son clases CSS, no texto traducido. Si el
  filtro no deja exactamente una, sigue rechazando.

## Los trece hallazgos de la revisión adversarial

Una revisión adversarial de tres lentes (34 agentes) sobre la evidencia del
zoom y las garantías de foco levantó 31 hallazgos; 13 sobrevivieron a la etapa
de refutación. Éste es el destino de cada uno.

| # | Hallazgo | Estado | Respaldo |
|---|---|---|---|
| 1 | La ventana de medición del anuncio de zoom empezaba al entrar en la fase, así que un anuncio provocado por la propia navegación por la cinta contaba como prueba | corregido | `uia_helper.ajustar_a_pagina` relee `anuncios_antes` justo antes de `invocar`; `test_el_anuncio_se_mide_desde_el_invoke_no_desde_la_entrada` (grupo 20) · `17730a0` |
| 1b | La segunda mitad de esa corrección —acotar los anuncios al contenedor del control— | descartado con motivo | no hay ningún contenedor medido para el anuncio; acotarlo sería adivinar y podría descartar la única evidencia. Queda dicho en el docstring del módulo · `17730a0` |
| 2 | El CHANGELOG publicaba `verified_by: "frame_changed"`, que el código ya no emite | corregido | sección `[Unreleased]` reescrita entera · `d23549a` |
| 3 | `docs/TOOL_CATALOG.md` decía que el zoom se verifica por el cambio de píxeles | corregido | fila de `pbi_validate_desktop_render`, línea 54 · `d23549a` |
| 4 | Docstrings que afirmaban exclusividad: «el único oráculo disponible», «solo expone `Invoke`» | corregido | `huella_de_ventana` y `ajustar_a_pagina` dicen lo medido (`Invoke` **y** `LegacyIAccessible`) · `17730a0` y este commit |
| 5 | `traer_al_frente` se cortocircuitaba por PID: la recuperación del foco del cuadro nunca llegaba a ejecutarse | corregido | parámetro `exacto=True`; `test_traer_al_frente_exacto_no_se_conforma_con_el_proceso` y `test_el_cuadro_se_recupera_con_exacto` (grupo 19) · `17730a0` |
| 6 | El CHANGELOG publicaba el mecanismo `ValuePattern.SetValue`-primero, que no corre | corregido | reescritura; `fijar_valor` se niega a correr y `test_fijar_valor_se_niega_a_correr` lo fija · `d23549a` |
| 7 | El catálogo decía tres intentos por fase; la fase del nombre hace seis | corregido | `docs/TOOL_CATALOG.md` línea 131 · `d23549a` |
| 8 | El CHANGELOG decía que el zoom distingue «ya estaba ajustada» de «no llegó la orden»; el código declara lo contrario | corregido | `_motivo_zoom` mantiene la disyunción; `test_sin_cambio_no_se_afirma_que_ya_estuviera_ajustada` · `d23549a` |
| 9 | «Es el único oráculo disponible» no estaba demostrado | corregido | misma corrección que el 4; la frase ya no existe · este commit |
| 10 | La tabla de lo medido afirmaba la CAUSA de una observación ambigua («because it was already fitted») | corregido | fila sustituida por lo observado · `d23549a` |
| 11 | La ambigüedad página/cinta se describía como consecuencia de la plataforma, no del selector | corregido | el selector desambigua por contenedor (grupo 18) y el texto lo dice · `8d5fd84` + `d23549a` |
| 12 | «Pending real-Desktop validation: nothing from this batch» excedía la evidencia publicada | corregido | «Known limitations» enumera lo que del lote descansa solo en dobles · este commit |
| 13 | «Across all 15 contention runs» no cuadraba con la tabla publicada | corregido | la tabla lista las cinco tandas y la cifra es 25 · `d23549a` |

Ninguno se reclasificó como limitación para poder cerrar: las tres limitaciones
de la sección siguiente son propiedades del mecanismo, no hallazgos aparcados.
Lo único descartado es 1b, y con motivo escrito.

## Correcciones que sustituyeron a otras correcciones

- `ValuePattern.SetValue` se introdujo como la vía limpia para el nombre y
  **no sirve**: tras llamarlo, UI Automation y `WM_GETTEXT` devolvían los 133
  caracteres pedidos y el cuadro guardaba con su nombre y carpeta por defecto.
  La ruta se teclea; el método ahora se niega a correr.
- La verificación del zoom por píxeles se retiró por lo dicho arriba.
- `traer_al_frente` devolvía éxito en cuanto cualquier ventana del proceso
  tenía el foco, así que la recuperación del foco del cuadro nunca se
  ejecutaba justo en su caso. Ahora acepta `exacto=True`.
- La foto de anuncios de zoom se tomaba al entrar en la fase, antes de navegar
  por la cinta; ahora se toma inmediatamente antes de pulsar.

## Evidencia automatizada

- Suite completa final: **3564 pasadas, 6 saltadas** (13 min 18 s).
- `python scripts/doctor.py`: correcto.
- `python -m tests.contract_utils`: el contrato no cambió.
- Contra el golden de `main` (`7cbc12d`, que es también el `merge-base`):
  **0 rupturas, 25 cambios compatibles**, todos parámetros opcionales con
  default y descripciones. El golden se regeneró **una vez**, deliberadamente,
  por las dos descripciones que este commit corrige; el diff del archivo son
  esas dos líneas y las 139 tools siguen intactas.
- `ruff check src tests`: limpio. `mypy` (sin argumentos, que es como el
  repositorio lo tiene configurado): limpio en sus 10 archivos de alcance —las
  fronteras de seguridad, transacciones y bloqueos—. Ese alcance **no** incluye
  los módulos de esta rama; ampliarlo no entra aquí.
- Regresiones nuevas en `tests/test_correcciones_de_auditoria.py` (22 grupos)
  y en los seis archivos de la primera tanda. Ejecutadas contra el commit
  anterior en un worktree: fallan **por aserción**, no por símbolos ausentes
  —las cuatro del grupo 21 lo hacen contra `d23549a`—.
- Las dos del grupo 22 (aislamiento de la sesión) **pasan** también contra el
  commit anterior, y así debe ser: no arreglan un defecto de código, fijan una
  vía que ya existía y que el procedimiento no obligaba a usar. Es una prueba
  de caracterización, no una regresión.

## Evidencia live

Proyectos sintéticos, instancias de prueba identificadas, sin ninguna ventana
de Desktop del usuario abierta. Son observaciones de esas ejecuciones, no
propiedades garantizadas del mecanismo.

| Comprobación | Resultado |
|---|---|
| `.pbix`, ruta de 161 caracteres | verificado, 13.920 bytes, informe y modelo, ~18 s |
| `.pbit` | verificado, 4.039 bytes, diálogo `Exportar una plantilla` aceptado, con `DataModelSchema` y sin `DataModel` |
| Apertura de la plantilla | Desktop crea un documento nuevo sin título, que es lo propio de una plantilla; sin diálogo de error en 90 s |
| Título provisional al abrir | `Sin título` a 2,9 s, `Demo` a 9,5 s |
| Cierre por `desktop_session` | cerrado y verificado, sin procesos huérfanos |
| Recuperación sin instancias | `stale_session` con `recovery="no_instances"` |
| Detección de archivo extraviado | reportó `Demo.pbix` en la carpeta del proyecto con su causa |
| Selección de página | `Portada` con `IsSelected`; lienzo 53.168 → 66.743 bytes |
| Página `Ver`, homónima de la cinta | elegida como página: `disambiguated_by: page_tab_container`; la de cinta (id `view`) intacta |
| Ajustar a la página | `verified_by: zoom_level_announced`, `Informe ampliado a 86 %` |
| Navegación sin `confirm_reuse` | rechazada; `pages.json` sin tocar |
| Sobrescritura sin permiso | Desktop ni se abre; destino idéntico byte a byte |
| Sobrescritura con `overwrite=true` | respaldo, modal de reemplazo aceptado, salida nueva de 3 páginas, Guardar pulsado una vez |

**Contención de foco**: 25 vueltas en cinco tandas de cinco, con una segunda
instancia de prueba robando el primer plano mientras se tecleaba una ruta de
161 caracteres. Con la configuración final: **5/5** con contención realista
(3-5 robos por vuelta) y **3/5** con contención patológica (~3 robos/s), donde
los dos fallos son la negativa a teclear sin primer plano. En las 25 vueltas no
se escribió ningún archivo en la ruta equivocada, no apareció ninguna salida
con el nombre por defecto, el proyecto de la otra ventana quedó idéntico y cada
fallo definitivo canceló su propio cuadro.

## Limitaciones que quedan

- La evidencia de zoom identifica un cambio de **nivel**, no el modo de vista.
  El estado de `LegacyIAccessible` del control no se examinó: es la única ruta
  sin explorar hacia identificar el modo.
- La guardia de foco acota a una tanda lo que puede desviarse; no lo elimina.
- Dos páginas con el mismo nombre visible siguen siendo ambiguas por diseño.
- **Lo que corrió en vivo es exactamente lo que listan las dos tablas.** De
  este mismo lote NO se ejercitaron contra Desktop real, y descansan en dobles:
  la recuperación de sesión con varias instancias, el puerto tomado por otro
  proceso (`mismatch`), el rechazo mutante por `document_mismatch`, el rechazo
  de un PID reciclado en `pbi_close_desktop` y las lecturas live de
  `$SYSTEM.TMSCHEMA_*`. Las tablas se midieron sin ninguna otra ventana de
  Desktop abierta, así que por construcción no podían cubrirlos.
- Fuera de esta rama y también sin probar en vivo: los modales de credenciales
  y de error de carga, que necesitan un modelo con origen externo.
- La contención se midió con una segunda instancia de Power BI Desktop; no se
  probó con aplicaciones de otra clase robando el foco.

## Estado persistido tocado por las pruebas

El smoke final usó ajustes aislados en
`outputs/live-smoke-20260904-r5/tool-outputs/session.json`; no escribió el
`outputs/session.json` normal. `doctor.py` sigue reportando allí una referencia
obsoleta preexistente y se preservó como estado local diagnosticable.

En las primeras rondas, antes de imponer ese aislamiento, los scripts live
abrieron proyectos con `project_locator.open_project()` y tocaron el archivo
normal. Al terminar se eliminó la referencia temporal que esas pruebas habían
creado y ambos campos quedaron a `null` en ese momento.

El valor que hubiera antes de la primera prueba **no es recuperable**: el
archivo no está en git y los registros del servidor redactan las rutas
personales (`.../Demo.pbip`), que además coinciden con el nombre del fixture
sintético. No se reconstruyó por suposición. Si había un proyecto activo, se
restaura con `pbi_open_pbip_project`. Es la única alteración de esta rama que
no se pudo deshacer.

Para que no vuelva a pasar, la vía de aislamiento —que ya existía y nadie
usaba— queda escrita en el procedimiento (`AGENTS.md`, §5) y ejercitada por una
prueba:

```bash
HORIZUN_PBI_MCP_OUTPUTS_DIR=/scratch/outputs python un_script_live.py
```

La variable se resuelve una sola vez, al construirse los ajustes, así que hay
que fijarla antes de que el proceso importe nada que los toque. Un `session.json`
que no parsea no se pisa —invariante previa a esta rama, en `Session._persist`:
perder la sesión persistida es reversible, destruir el archivo que explica qué
pasó no lo es—, y `pbi_session_info` lo reporta como `state: corrupt` con qué
hacer. El grupo 22 de `tests/test_correcciones_de_auditoria.py` comprueba que
la sesión se escribe donde se le dice y que la del usuario queda byte a byte
igual. Los scripts de
esta tanda fueron temporales y no se conservan en el repositorio.

## Base y alcance

Rama `codex/multiagent-audit-fixes` sobre `7cbc12d` (`main`, «Add
outcome metadata and workflow skill for discovery (#37)»), que es el
`merge-base` real. Contra el contrato congelado de esa base: **0 rupturas, 25
cambios compatibles** —parámetros opcionales con default y descripciones—.
Fuera del alcance y sin tocar: `pbix_to_pbip.py`, `secret_scan.py` y sus
`.bak-20260831-191525`, que son cambios ajenos en el árbol de trabajo.

## Commits

| Commit | Contenido |
|---|---|
| `595069b` | recuperación de sesión centralizada y lectura live de M y particiones |
| `67219eb` | carreras de UI Automation, identidad asentada y cierre tras exportar |
| `48f0e07` | congelar los cambios compatibles del contrato y documentar |
| `d161a72` | correcciones de la auditoría independiente y de la primera tanda live |
| `8c4bd79` | documentación de esa ronda |
| `8d5fd84` | los tres escenarios live: página y zoom, sobrescritura, contención |
| `abc59b2` | documentación de esos escenarios |
| `17730a0` | calibrar la evidencia del zoom, desambiguar la pestaña de página y acotar la garantía de foco |
| `d23549a` | un registro de cambios que no se contradice, y este borrador |
| `3a51dad` | que la respuesta y las descripciones no prometan el modo de vista; aislamiento de la sesión en pruebas live |
| `2148771` | la traza de los trece hallazgos y lo que del lote no corrió en vivo |
| `4e9334b` | cerrar el borrador inicial con la lista completa de commits |
| `6715fed` | conservar estado de runtime corrupto para diagnóstico en vez de sobrescribirlo |
| `4b13786` | cerrar huecos de rollback, backups y escritura atómica por lotes |
| `5115f82` | alinear contrato, clasificación de riesgo y metadatos con los efectos reales |
| `71b0546` | endurecer captura, exportación, compensación y recuperación de Desktop |
| `7a55432` | reseleccionar la única instancia viva para lecturas sin redirigir mutaciones |

## Cierre de la auditoría multiagente

Tres revisiones independientes cubrieron fronteras de escritura, contrato MCP y
automatización de Desktop. Además de los problemas originales, encontraron y se
corrigieron estos grupos:

- estado de runtime ilegible que podía perder la evidencia del fallo;
- prevalidación, rollback, manifiestos de backup, colisiones de destino y
  coincidencias TMDL sensibles a mayúsculas;
- envelopes que ocultaban estado u operación, capacidades DLL demasiado
  optimistas y riesgos que no reflejaban efectos reales;
- reutilización de ventanas homónimas, carreras entre Invoke y clic físico,
  timeouts de refresh, limpieza de exportaciones fallidas y verificación PBIT;
- recuperación de sesión de solo lectura que todavía rechazaba el único PBIX
  vivo cuando el proyecto activo seguía siendo el PBIP fuente.

El smoke live final se ejecutó sobre un PBIP sintético con un textbox visible:

| Comprobación final | Evidencia |
|---|---|
| `page + refresh=true` | identidad y frame asentados, `data_loaded=true`, captura representativa y visual visible |
| Restauración del PBIP | inventario SHA-256 idéntico antes y después de la captura |
| Exportación PBIT | plantilla PBIR válida, sin `DataModel`, cierre verificado por PID + hora de arranque |
| Guardar como repetido | 3/3 exportaciones con argumentos idénticos, mismo SHA-256, escritura estable y ruta abierta verificada |
| Reinicio de Desktop | puerto obsoleto `57498` reemplazado automáticamente por el único puerto vivo `57750` |
| Limpieza | cero procesos `PBIDesktop`/`msmdsrv` al terminar |

Evidencia local ignorada por Git: `outputs/live-smoke-20260904-r5/evidence.json`.

## Cómo revisarlo

Los archivos con más carga de decisión son
`src/horizun_pbi_mcp/powerbi/uia_helper.py` (el helper del cuadro de guardado),
`src/horizun_pbi_mcp/powerbi/desktop_navigation.py` (qué cuenta como evidencia)
y `src/horizun_pbi_mcp/services/pbix_export.py` (qué se afirma del resultado).
Las regresiones que explican por qué cada cosa es como es están en
`tests/test_correcciones_de_auditoria.py`, agrupadas por hallazgo.
