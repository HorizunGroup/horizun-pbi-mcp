# Aceptación 10 de 10 — gates medibles

Qué tiene que ser cierto para declarar el producto en 10 de 10. Cada gate es una
**comprobación ejecutable con veredicto binario**, no una intención. Un gate sin
comando que lo decida no es un gate: es una opinión con formato de tabla.

Compañeros de este documento: [`AUDIT_2026-08-14.md`](AUDIT_2026-08-14.md) (la
evidencia), [`../MATRIZ_REMEDIACION.md`](../MATRIZ_REMEDIACION.md) (el estado) y
**[`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md)**, que es la partición de
donde salen los números de abajo: cada gate en una sola categoría, y una prueba
que exige que sumen 54.

---

## Reglas que se aplican a los diez bloques

1. **Toda corrección llega con una prueba que falla antes y pasa después.** Sin
   el "falla antes" no se sabe si la prueba ata algo. Es la lección de
   CONTRACT-001: cinco pruebas en verde solo demostraron algo cuando se rompió la
   guarda a propósito y se apagaron.
2. **Un skip no es un verde.** En CI, todo skip de un gate cuenta como fallo
   salvo que su motivo esté en una lista corta y declarada de causas
   ambientales.
3. **Verde sin oráculo no vale.** Si la forma correcta la define el mismo código
   que se prueba, el gate no ha comprobado nada. Los jueces externos
   disponibles son `TmdlSerializer`, el CLI oficial de Microsoft, Power BI
   Desktop y el sistema de archivos real.
4. **"Pendiente de evidencia" es un estado legítimo y bloqueante.** Un gate que
   requiere Desktop real, instalación limpia o el remoto de GitHub y no se
   ejecutó **no está cumplido**. No se aprueba por lectura de código.
5. **Cada gate nombra su hallazgo.** Un gate que no cierra nada de la auditoría
   sobra; un hallazgo sin gate que lo cierre está sin plan.

---

## G1 — Seguridad funcional

Cierra CORE-001 … CORE-006.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G1.1 | Un `.pbip` abierto en Desktop **sin** referencia en cmdline ni en descriptores hace que `assert_writable` **bloquee** | Prueba live: abrir desde recientes, pedir escritura PBIR, exigir `ProjectOpenInDesktopError`. Debe fallar contra el código de hoy | CORE-001 |
| G1.2 | Ninguna escritura sale del proyecto activo | Prueba con `.pbip` cuyo `artifacts[].report.path` apunta fuera: se rechaza antes de escribir | CORE-002 |
| G1.3 | Toda escritura de `desktop_capture` es transaccional | Corte a mitad de `preparar_vista_de_captura`: el proyecto queda intacto o recuperable por journal, nunca a medias | CORE-002 |
| G1.4 | `safe_to_retry` nunca es `true` con `cancel_confirmed: false` | Prueba unitaria sobre la respuesta de `refresh_timeout`; la combinación contradictoria debe ser inexpresable | CORE-003 |
| G1.5 | La anotación de riesgo describe el efecto real | Prueba que recorra las 134: `destructiveHint` ⇒ existe `confirm` con default `False`; `readOnlyHint` ⇒ no escribe fuera del proceso | CORE-004 |
| G1.6 | El validador no escribe dentro del proyecto del usuario | Ejecutar `pbi_validate_pbip_project` y exigir cero archivos nuevos bajo el `.Report` | CORE-004(d) |
| G1.7 | Ningún log lleva rutas ni secretos sin redactar | Emitir excepción con ruta y token conocidos; exigir que no aparezcan literales en `msg` ni en `exc` | CORE-005 |
| G1.8 | Dos procesos no pierden cambios sobre el mismo proyecto | Dos procesos reales escribiendo el mismo `.pbip`: uno espera o falla limpio. **Nunca ambos en verde** | CORE-006 |

**Umbral: 8 de 8.** G1.1 y G1.8 exigen ejecución real; sin ella el bloque queda
*pendiente de evidencia*.

---

## G2 — Contrato y payloads

Cierra CONTRACT-002 y TEST-002.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G2.1 | `python -m tests.contract_utils` sale 0 | Comando directo | — (ya cumplido) |
| G2.2 | Retirar o renombrar una clave del payload **rompe la suite** | Mutación: quitar una clave de una respuesta y exigir rojo. Hoy pasa en verde | CONTRACT-002 |
| G2.3 | Inventario tool por tool publicado | Documento con las 134: ejecución MCP directa, casos negativos, anotación, confirmación, payload congelado | TEST-002 |
| G2.4 | Toda tool tiene al menos un caso negativo | Recuento sobre el inventario; las excepciones se declaran con motivo | TEST-002 |
| G2.5 | El contrato no rompe sin ratificación registrada | Toda diferencia incompatible exige entrada en la matriz | CONTRACT-001 (precedente) |

**Umbral: 5 de 5.** G2.2 es el gate real del bloque: sin él, G2.1 mide menos de
lo que aparenta.

---

## G3 — Instalación limpia, Codex y Claude

Cierra INSTALL-002, -004, -005, -010 y CLI-001.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G3.1 | Máquina limpia + Claude ⇒ `ready` y 134 tools | VM sin Python/Node/Claude: one-paste, luego `tools/list` end-to-end | INSTALL-010 |
| G3.2 | Máquina limpia + **Codex sin Claude** ⇒ verificado igual | Misma VM, ruta Codex guiada, con verificación equivalente a `claude plugin list` | CLI-001 |
| G3.3 | `ready` implica handshake real | Corromper el runtime tras instalar y exigir `state != ready` | INSTALL-010 |
| G3.4 | Node 18 en el PATH **no** impide instalar | VM con Node 18: `state=ready` y `validator=skipped_*` con motivo | INSTALL-002 |
| G3.5 | El instalador no declara éxito con plugin *disabled* o desactualizado | Deshabilitar el plugin y exigir que el instalador falle | INSTALL-004 |
| G3.6 | `pip install` solo declara operativo lo que lo está | Instalación pip pura: `pbi_health_check` enumera qué falta (DLL, esquemas) en vez de aparentar normalidad | INSTALL-005 |

**Umbral: 6 de 6, todos en máquina limpia.** Ninguno se aprueba por lectura de
código: este bloque es *pendiente de evidencia* mientras no haya corrida fechada.

---

## G4 — Update con rollback y uninstall

Cierra INSTALL-001, -006, -007, -008, -009, -011, -012.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G4.1 | Una actualización interrumpida deja N−1 **funcionando** | Fallo inyectado en `fetch_libs` durante update; exigir que la versión anterior siga arrancando y sirviendo `tools/list` | INSTALL-001 |
| G4.2 | La instalación de esquemas es atómica de verdad | Corte a mitad: destino intacto o completo, nunca mezclado. Publicación por *rename*, no copia por archivo | INSTALL-006 |
| G4.3 | Lo mismo para el validador npm | Corte a mitad de la instalación npm | INSTALL-006 |
| G4.4 | `uninstall` existe y deja el data root limpio | Instalar, desinstalar, medir bytes residuales; solo queda lo que el usuario eligió conservar | INSTALL-008 |
| G4.5 | `purge` enumera antes de borrar | Ejecución en seco que lista rutas y tamaños y exige confirmación | INSTALL-008 |
| G4.6 | Dos instalaciones consecutivas dan las mismas versiones | Comparar el conjunto resuelto; exige lock y hashes | INSTALL-009 |
| G4.7 | Hay bundle offline y runbook de proxy, y funcionan | Instalación en VM sin salida directa a internet | INSTALL-009 |
| G4.8 | No se cae fuera de user-scope en silencio | Si winget no puede user-scope, se anuncia y se pide consentimiento; `ExecutionPolicy` se declara y se documenta cómo revertir | INSTALL-007 |
| G4.9 | La recuperación de una promoción nunca opera fuera del data root ni fuera del cerrojo | Journal preparado a mano con rutas hostiles —`..`, absolutas ajenas, UNC, flujos alternos, junction— y con otro proceso reteniendo el cerrojo: ninguna ruta de fuera se crea, mueve ni borra, y no se recupera mientras el cerrojo es de otro | INSTALL-011 |
| G4.10 | **Cero mezcla de respuestas**: nunca dos servidores MCP en el mismo canal | Runtime activo que emite bytes y muere pronto —`initialize` y adiós, línea JSON a medias, stdout no-JSON, `tools/list` y adiós—. En la sesión del cliente: ni un `id` repetido, ni dos `serverInfo`, ni una línea que no sea JSON-RPC. **Decidido mirando el canal, nunca la duración del proceso** | INSTALL-012 |

**Umbral: 10 de 10.**

---

## G5 — Desktop real

Cierra TEST-003 y da la única evidencia posible de G1.1.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G5.1 | Filtro por medida encadena slicers en un informe real | Aplicar y abrir; el visual filtra | TEST-003 |
| G5.2 | `refresh` + captura produce una imagen con datos | `data_loaded: true` y tablas no vacías en la captura | TEST-003, TEST-004 |
| G5.3 | `data_loaded: false` cuando el modelo está vacío | Capturar sin refrescar; exigir el aviso | TEST-003 |
| G5.4 | El cambio de página captura la página pedida | Capturar una página no activa y verificar cuál salió | TEST-003, TEST-004 |
| G5.5 | Un `.pbip` abierto se detecta como abierto | Es G1.1 visto desde aquí | CORE-001 |
| G5.6 | Un rollback real restaura byte a byte | Escritura fallida sobre informe real; comparar hashes antes/después | TEST-003, TEST-004 |

**Umbral: 6 de 6, con corrida fechada.** Se ejecuta bajo demanda (marcador
propio), no en cada CI.

---

## G6 — Supply chain y publicación del mismo artefacto probado

Cierra RELEASE-001, -002 e INSTALL-003.

| # | Gate | Cómo se decide | Cierra | Estado |
|---|---|---|---|---|
| G6.1 | Se publica **el artefacto probado**, sin reconstruir | El digest publicado coincide con el que pasó la suite. Comparación de hashes, no de intenciones | RELEASE-001 | ⏳ pendiente de release real |
| G6.2 | Publicar exige CI verde sobre el mismo commit | Tag con la suite en rojo que no llega a publicar | RELEASE-002 | ⏳ pendiente de release real |
| G6.3 | Ningún camino publicado ejecuta desde `main` | Revisión de README, `docs/INSTALL.md`, `instalar.ps1` y `marketplace.json`: todo resuelve a tag o commit fijo | INSTALL-003 | ✅ **2026-08-14** |
| G6.4 | El script remoto se verifica antes de ejecutarse | Hash o firma comprobados antes del `Invoke-Expression` | INSTALL-003 | 🟡 lógica probada contra servidor local en 11 escenarios; el asset de v1.5.5 no existe |
| G6.5 | Un solo entorno de build | Sin diferencias de runner ni de versión de action entre probar y publicar | RELEASE-001 | ✅ **2026-08-14** |

**Umbral: 5 de 5.** G6.3 y G6.4 son los de mayor severidad de todo el documento:
son ejecución remota de código en la máquina de cada usuario.

---

## G7 — Controles GitHub

Cierra RELEASE-003.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G7.1 | `main` protegida, sin push directo | `gh api` sobre la protección de rama | RELEASE-003 |
| G7.2 | CodeQL activo y en verde | Workflow presente + última corrida | RELEASE-003 |
| G7.3 | Dependabot *security updates* activo | `.github/dependabot.yml` + estado en el remoto | RELEASE-003 |
| G7.4 | *Secret scanning* y *push protection* activos | `gh api` | RELEASE-003 |
| G7.5 | *Private vulnerability reporting* activo | `gh api` | RELEASE-003 |
| G7.6 | Actions pineadas por SHA | Revisión de los tres workflows (`ci`, `release`, `codeql`): cero tags flotantes, y cada SHA con su versión humana al lado | RELEASE-003 — ✅ **2026-08-14** |

**Umbral: 6 de 6.** G7.1–G7.5 son configuración del remoto: *pendiente de
evidencia* hasta que haya salida de `gh api` guardada. G7.6 se decide leyendo el
repositorio.

---

## G8 — Suite, packaging y documentación

Cierra TEST-001, DOC-001 … DOC-004.

| # | Gate | Cómo se decide | Cierra |
|---|---|---|---|
| G8.1 | `python -m pytest -q` en verde, sin excluir `packaging` | Comando directo | TEST-001 |
| G8.2 | En CI, un skip de packaging es un **fallo** | Wheel roto a propósito: la suite se pone roja, no amarilla | TEST-001 — ✅ **2026-08-14**, dos mutaciones medidas |
| G8.3 | El venv de packaging es limpio | Sin `--system-site-packages`, con dependencias resueltas | TEST-001 — ✅ **2026-08-14** |
| G8.4 | `scripts/doctor.py` sale 0 y sin traceback | Comando directo; un exit 0 con traceback impreso no cuenta | — |
| G8.5 | El README no se contradice a sí mismo | Comprobación automática: `both` no aparece como valor ofrecido ni en ejemplos | DOC-001 |
| G8.6 | La política de publicación es coherente | `AGENTS.md` y `CONTRIBUTING.md` describen lo que hacen los workflows | DOC-002 |
| G8.7 | El alcance del "PC vacío" está dicho | La documentación nombra Power BI Desktop como requisito no cubierto | DOC-003 |
| G8.8 | Existe runbook por procedimiento | Update, rollback, uninstall, purge, proxy, offline y recuperación: cada paso, un comando | DOC-004 |

**Umbral: 8 de 8.**

---

## Cómputo

La columna de la derecha **no se opina: se deriva** de
[`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md) —«requiere entorno real» son
los `parcial` más los `pendiente-externo`— y una prueba exige que coincida con
las fichas de [`EXTERNAL_GATES.md`](EXTERNAL_GATES.md). Un gate con trabajo local
pendiente **no cuenta como externo**, por muy bloqueado que parezca: esa
confusión es la que permitió afirmar «no queda trabajo local».

| Bloque | Gates | Ejecutables aquí | Requieren entorno real |
|---|---|---|---|
| G1 Seguridad funcional | 8 | 8 | 0 |
| G2 Contrato y payloads | 5 | 5 | 0 |
| G3 Instalación limpia | 6 | 1 | 5 (VM limpia) |
| G4 Update y uninstall | 10 | 7 | 3 (VM limpia, npm real, runner no-Windows) |
| G5 Desktop real | 6 | 1 | 5 (Desktop) |
| G6 Supply chain | 5 | 2 | 3 (publicación real) |
| G7 Controles GitHub | 6 | 1 | 5 (remoto) |
| G8 Suite y documentación | 8 | 8 | 0 |
| **Total** | **54** | **33** | **21** |

**10 de 10 = los 54 gates cumplidos, con evidencia fechada.**

El total subió de 52 a 54 el 2026-08-14: **G4.9** (INSTALL-011) y **G4.10**
(INSTALL-012), dos gates que no existían porque tampoco existían los hallazgos
que cubren — los introdujo la propia remediación y los encontró la revisión
independiente. Los tres «ejecutables hoy» de G4 son G4.2, G4.9 y G4.10: mueven
archivos reales o hablan con procesos reales en directorios temporales, y
ninguno necesita una máquina limpia.

Veintiocho de los cincuenta y cuatro exigen una máquina limpia, un Desktop real,
una publicación real o el remoto de GitHub. Esa proporción es el hallazgo de
fondo de la auditoría: **el producto se ha estado midiendo casi entero por
lectura de código**, y las tres formas de "verde sin oráculo" —INSTALL-010,
TEST-001 y RELEASE-001— son el mismo defecto repetido en tres capas.

**Tres gates cumplidos con evidencia, el 2026-08-14**, sobre la rama
`codex/p1-p4-audit-checkpoint` tras el séptimo commit de código (`8f5c35a`):

| Gate | Comando | Resultado |
|---|---|---|
| G2.1 | `python -m tests.contract_utils` | exit 0 — «El contrato MCP no cambio» |
| G8.1 | `python -m pytest -q` | 2225 passed, 3 skipped, sin excluir `packaging` |
| G8.4 | `python scripts/doctor.py` | exit 0, sin traceback |

Los tres skips son ambientales y ninguno es de packaging, así que G8.1 cuenta
como cumplido bajo la regla 2. Los tres son justo los baratos: no dicen nada
sobre instalación limpia, Desktop real, publicación ni controles del remoto,
que es donde vive el problema.

### Segunda pasada — cinco gates más, el 2026-08-14

Rama `codex/install-003-immutable-sources`, seis commits sobre `b2d851a`.
Todos se deciden leyendo el repositorio o ejecutando un comando, que es la
condición para poder cumplirlos sin máquina limpia ni publicación real.

| Gate | Cómo se comprobó | Resultado |
|---|---|---|
| G6.3 | Barrido de README, `docs/INSTALL.md`, la skill, `instalar.ps1`, los dos `marketplace.json` y los workflows | ✅ cero referencias móviles ejecutables |
| G6.5 | `tests/test_release_pipeline.py::test_el_build_es_uno_solo_y_los_demas_lo_consumen` | ✅ un solo job construye; los otros tres descargan y verifican |
| G7.6 | `tests/test_repo_security.py`, sobre los tres workflows | ✅ cero tags flotantes; cada SHA con su versión humana |
| G8.2 | Dos mutaciones sobre el mismo árbol roto, viejas contra nuevas | ✅ verde/ámbar → rojo |
| G8.3 | `tests/test_packaging.py`, venv sin `--system-site-packages` y sin `--no-deps` | ✅ resolución real de las diez dependencias |

**G6.4 queda a medias, y es el matiz que importa.** El bloque de un pegado
comprueba el SHA-256 antes de ejecutar nada, y las once rutas de fallo —hash
incorrecto, ausente, malformado, `Content-Length` excesivo, stream excesivo,
descarga truncada, HTTP 500, salida no cero— están probadas contra un servidor
HTTP local con un centinela en disco como oráculo. Pero **el asset de v1.5.5 no
existe**: la URL que ese bloque descargaría hoy devuelve 404. La lógica está
cumplida; la evidencia de punta a punta, no. Bajo la regla 4, eso no es un gate
cumplido.

### Tercera pasada — el bloque de ciclo de vida, el 2026-08-14

Rama `codex/installer-lifecycle-hardening`, cinco commits sobre `9290a7d`.
Aquí sí se mueve el total: **G4.9 es nuevo**, porque el hallazgo que cubre
—INSTALL-011— tampoco existía cuando se escribieron los gates.

| Gate | Cómo se comprobó | Resultado |
|---|---|---|
| G4.9 | 35 pruebas en `tests/test_lifecycle_containment.py`: journal hostil con `..`, absolutas ajenas, UNC, flujos alternos y junction; cerrojo en manos de un proceso vivo ajeno | ✅ **2026-08-14** — 34 de las 35 fallan contra el commit anterior |
| G4.2 | `tests/test_publicacion_atomica.py`, con el destino observado **en el instante de publicar** | ✅ **2026-08-14** — publicación por *rename*; ninguna copia sobre el destino vivo |
| G4.1 | `tests/test_launcher_fallback.py`, fallo inyectado en pip, DLL, esquemas, handshake y promoción; el **lanzador real** contesta `tools/list` por stdio | 🟡 el mecanismo está demostrado de punta a punta con un runtime que arranca de verdad, pero es un runtime de prueba: falta la misma corrida sobre una instalación real, y eso es la VM |
| G4.3 | Mismas pruebas, con `npm` simulado: `--prefix` apunta al staging y nunca al destino vivo | 🟡 la forma está demostrada; falta una corrida con `npm` real |
| G3.3 | Runtime promovido y después corrompido —sin intérprete, sin *entry points*, y uno que revienta al arrancar— | 🟡 el lanzador deja de anunciarlo y sirve N−1, pero sobre runtimes de prueba: la corrida sobre una instalación real es la VM |

**Por qué tres de los cinco son amarillos y no verdes.** En los tres el
mecanismo se ejerce entero —el lanzador real, hablando MCP por stdio, entrega
las 134 tools de la versión anterior— pero el runtime que se sirve es un venv
de prueba con un servidor mínimo, no la instalación de 1 GB con pythonnet, las
DLL de Analysis Services y los esquemas. Que el mecanismo funcione es
condición necesaria y no suficiente: la regla 4 pide el entorno real, y aquí no
lo hay. Llamarlos verdes sería exactamente el defecto que esta auditoría
persigue.

### Cuarta pasada — lo que la revisión independiente encontró, el 2026-08-14

Rama `codex/installer-lifecycle-hardening`, cinco commits sobre `85e3098`. La
tercera pasada quedó **sin ratificar**: la revisión confirmó los siete commits y
las 137 pruebas focalizadas, y aun así encontró huecos de corrección. Dos de
ellos son hallazgos nuevos con gate propio.

| Gate | Cómo se comprobó | Resultado |
|---|---|---|
| G4.10 | `tests/test_stdout_sin_mezclar.py`: cuatro formas de ensuciar el canal y morir, con el **lanzador real** y el canal auditado por `id` repetido, `serverInfo` distinto y líneas no-JSON | ✅ **2026-08-14** |
| G4.3 | `tests/test_publicacion_concurrente.py`: dos procesos de verdad sobre el mismo destino, el primero parado en el hueco no atómico | 🟡 la concurrencia y el *rename* quedan cerrados; sigue faltando una corrida con `npm` real |
| G3.3 | Cuatro corrupciones —sin intérprete, sin *entry point*, sin paquete, y un servidor que responde a medias— con `state` exigido distinto de `ready` | 🟡 el comportamiento local **sí cumple el gate literal**; lo que falta es la corrida sobre una instalación real, y eso es la VM |

**G3.3 pasa a cumplirse localmente y sigue amarillo, y la diferencia importa.**
La entrega anterior cambiaba `sirviendo` a last-known-good y dejaba `state` en
`ready`; una prueba propia lo afirmaba, contradiciendo el gate palabra por
palabra. Ahora `state` vale `degraded` tras cualquiera de las cuatro
corrupciones. Amarillo, no verde, porque los runtimes que se corrompen son de
prueba: el gate vive en el bloque G3, que es *pendiente de evidencia* completo
hasta la VM.

### Cómputo actualizado

| | Gates |
|---|---|
| Cumplidos con evidencia | **30** (G1.1, G1.2, G1.3, G1.4, G1.6, G1.7, G1.8, G2.1, G2.3, G2.4, G2.5, G3.6, G4.2, G4.4, G4.5, G4.8, G4.9, G4.10, G5.5, G6.3, G6.5, G7.6, G8.1, G8.2, G8.3, G8.4, G8.5, G8.6, G8.7, G8.8) |
| Parciales | **6** (G3.3, G4.1, G4.3, G4.6, G5.6, G6.4) |
| Pendientes | **18** — de los cuales **2 son trabajo local** (G2.2, G4.7), 1 espera ratificación (G1.5) y 15 son externos |
| **Total** | **54** |

### Quinta pasada — seguridad del core, el 2026-08-15

| Gate | Cómo se comprobó | Resultado |
|---|---|---|
| G1.4 | `tests/test_core_seguridad_operativa.py`: la combinación `cancel_confirmed: false` + `safe_to_retry: true` pasa a ser inexpresable, y un `refresh_timeout` sin `details` tampoco se da por seguro | ✅ **2026-08-15** |
| G1.7 | Un log con una ruta de cliente y un token conocidos, emitido por el formateador real: ninguno aparece literal en `msg` ni en `exc`, y el evento sigue siendo JSON de una línea | ✅ **2026-08-15** |
| G1.1 · G1.2 · G1.3 | Evidencia **live** fechada el 2026-08-14 en [`AUDIT_2026-08-14.md`](AUDIT_2026-08-14.md): `test_live_la_ventana_real_delata_un_pbip_sin_handles` para G1.1, y las 13 de `tests/test_capture_atomicity.py` para G1.2 y G1.3 | ✅ — **no eran gates nuevos: el cómputo estaba incompleto.** Se detectó cruzando la matriz con esta tabla y se resolvió leyendo la evidencia, no dándola por buena |
| G2.5 | CONTRACT-001 ratificada y registrada; **CONTRACT-003** repite el mecanismo en vivo: tres cambios incompatibles que CORE-004 pedía se registraron en vez de aplicarse. `python -m tests.contract_utils` falla ante una diferencia incompatible | ✅ **2026-08-15** |
| G2.2 | `tests/test_contrato_de_payload.py` —seis mutaciones y tres compatibles— sobre un golden de **91 muestras / 53 tools**, capturadas por `call_tool` en dos escenarios; y `docs/COBERTURA_PAYLOADS.md`, que da la dependencia medida de cada exclusión | ⛔ **pendiente-local** — de 2 a 53 tools, y la excusa cae: solo **14** dependen de un modelo vivo; **77** solo necesitan argumentos válidos |
| G2.3 · G2.4 | `docs/INVENTARIO_TOOLS.md`, generado por `python -m tests.inventario_tools`, y `tests/test_inventario_tools.py`: las 134 ejecutadas por `call_tool` contra su caso negativo, con el recuento sacado de las llamadas **observadas** | ✅ **2026-08-15** — 134/134 ejecutadas, **cero declaradas**: 114 rechazadas en validación, 11 con sobre `ok: false` y código, 1 con el adaptador roto, 8 sin modo de fallo verificadas |
| G4.6 | `tests/test_lock_de_dependencias.py`: 34 pruebas sobre la **matriz** de locks, la selección exacta y el fallback; más dos venv limpios instalados desde el lock y `pip freeze` comparado | 🟡 **2026-08-15** — matriz `win_amd64 × {3.10, 3.13, 3.14}`, verificada de verdad en las tres (CI corre 3.10 y 3.13). Fuera de la matriz cae al resolutor y **declara que no es reproducible**; falta un runner no-Windows |
| G4.4 · G4.5 | `tests/test_desinstalacion.py`: 12 pruebas, incluida la CLI real sobre un data root de prueba | ✅ **2026-08-15** — el seco es el DEFECTO, no una opción; `residual_bytes` tras desinstalar es exactamente el peso de los datos del usuario |
| G4.8 | `tests/test_instalador_dryrun.py`: el reintento sin `--scope` se anuncia ANTES —se comprueba el orden en el código—, se verifica dónde aterrizó, y `-SoloUserScope` lo prohíbe | ✅ **2026-08-15** — el reintento se conserva a propósito: quitarlo rompe el PC vacío |
| G8.8 | `docs/RUNBOOK_INSTALACION.md`, seis procedimientos con comandos ejecutables, comprobados contra la instalación real en solo lectura | ✅ **2026-08-15** — declara lo que NO existe en vez de ofrecer comandos que fallarían |
| G8.5 · G8.6 · G8.7 | `tests/test_documentacion_veraz.py`: seis comprobaciones que contrastan lo que el documento promete con lo que el código hace | ✅ **2026-08-15** — 5 de 6 rojas antes |
| G1.6 | `tests/test_validador_no_escribe_en_el_proyecto.py`: el ciclo completo con el CLI simulado, exigiendo cero archivos nuevos bajo el proyecto | ✅ **2026-08-15** — el temporal vivía en el directorio del usuario y una prueba existente lo EXIGÍA así |
| G1.8 | `tests/test_concurrencia_entre_clientes.py`: dos procesos reales sobre el mismo `.pbip`; el segundo espera su turno y los dos aplican, en vez de que uno falle | ✅ **2026-08-15** — «nunca ambos en verde» ya se cumplía por la huella; lo que faltaba era no perder el trabajo del segundo |
| G3.6 | `tests/test_packaging.py::test_una_instalacion_pip_pura_no_se_declara_operativa`: artefacto construido, venv limpio, fuera del checkout y con la caché de esquemas del usuario aislada | ✅ **2026-08-15** — el gate no exigía VM: exigía una instalación pip pura, y eso sí se puede montar aquí |

Los dos son ejecutables sin máquina limpia, sin Desktop y sin remoto, que es por
lo que se eligieron primero: cerraban entero, no a medias.

Los treinta y nueve pendientes siguen siendo, en su mayoría, los que exigen una
máquina limpia, un Desktop real, una publicación real o el remoto de GitHub —
que es el hallazgo de fondo de la auditoría y no lo cambia ninguna cantidad de
trabajo local.
