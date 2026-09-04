# Conducir Power BI Desktop desde fuera, con evidencia que se sostiene

Borrador local de descripción de PR para `codex/desktop-ui-races-session-recovery`.
No publicado. Rama sin push.

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
| Reiniciar Desktop dejaba la sesión con un puerto muerto | Recuperación centralizada con la regla de `pbi_select_model`, que además exige que la instancia sirva el proyecto activo |
| Faltaba exportación nativa a `.pbit` | `format='pbit'` en `pbi_export_pbix` y `pbi_finalize_delivery`, con su diálogo de plantilla y verificación estructural |
| Elegir página y zoom exigía cerrar y reabrir | Se hacen en la ventana abierta bajo `confirm_reuse`, sin tocar `pages.json` |
| Faltaba lectura live de Power Query y particiones; parámetros inconsistentes | Lectura por DMV, alias con conflicto explícito y resolución de carpetas de proyecto |

## Lo que la evidencia demuestra, y lo que no

Esta es la parte que más cambió durante la revisión.

- **El zoom.** «Ajustar a la página» es un `Button` que solo expone `Invoke`:
  no hay estado que releer. Lo que sí publica Power BI es el nivel de zoom
  (`Informe ampliado a 72 %`), capturado entre el instante anterior a pulsar y
  el posterior. Eso demuestra que **el nivel de zoom cambió al pulsar**, no que
  el modo resultante sea «ajustar a la página». Un cambio de píxeles viaja como
  `visual_change` y nunca decide `verified`: abrir la cinta para llegar al
  control ya cambia la imagen.
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

- Suite completa: **3503 pasadas, 6 saltadas**.
- `python scripts/doctor.py`: correcto.
- `python -m tests.contract_utils`: el contrato no cambió.
- Contra el golden de `main`: **0 rupturas, 25 cambios compatibles**, todos
  parámetros opcionales con default y descripciones.
- `ruff` y `mypy` limpios.
- Regresiones nuevas en `tests/test_correcciones_de_auditoria.py` y en los seis
  archivos de la primera tanda. Ejecutadas contra el commit anterior en un
  worktree: fallan por aserción, no por símbolos ausentes.

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
- La guardia de foco acota a una tanda lo que puede desviarse; no lo elimina.
- Dos páginas con el mismo nombre visible siguen siendo ambiguas por diseño.
- Sin probar en vivo, y fuera de esta rama: los modales de credenciales y de
  error de carga, que necesitan un modelo con origen externo.
- La contención se midió con una segunda instancia de Power BI Desktop; no se
  probó con aplicaciones de otra clase robando el foco.

## Estado persistido tocado por las pruebas

`outputs/session.json` no está versionado y los scripts live abren proyectos
con `project_locator.open_project()`, que escribe en él. Al terminar quedó
apuntando a un proyecto temporal de las pruebas, ya borrado; se eliminó **esa**
referencia y el archivo queda con `active_model` y `active_pbip` a `null`.

El valor que hubiera antes de la primera prueba no es recuperable: el archivo
no está en git y los registros del servidor redactan las rutas personales
(`.../Demo.pbip`), que además coinciden con el nombre del fixture sintético. Si
había un proyecto activo, se restaura con `pbi_open_pbip_project`.

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
| (este) | calibrar la evidencia del zoom, desambiguar la pestaña de página, acotar la garantía de foco y reescribir el registro de cambios |

## Cómo revisarlo

Los archivos con más carga de decisión son
`src/horizun_pbi_mcp/powerbi/uia_helper.py` (el helper del cuadro de guardado),
`src/horizun_pbi_mcp/powerbi/desktop_navigation.py` (qué cuenta como evidencia)
y `src/horizun_pbi_mcp/services/pbix_export.py` (qué se afirma del resultado).
Las regresiones que explican por qué cada cosa es como es están en
`tests/test_correcciones_de_auditoria.py`, agrupadas por hallazgo.
