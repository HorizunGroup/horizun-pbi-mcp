# Matriz de remediación

Ciclo abierto sobre la rama local `codex/p1-p4-audit-checkpoint`, base `2973f1d`
(v1.5.5). Los cambios están **commiteados en local**; **sin push, sin PR, sin
tag, sin publicación.**

> **Segunda pasada — 2026-08-14, rama `codex/install-003-immutable-sources`.**
> A diferencia de la primera, esta **sí remedia**: seis commits sobre `b2d851a`
> que cierran TEST-001 y dejan parcialmente cerradas INSTALL-003, RELEASE-001,
> RELEASE-002 y RELEASE-003. Cada una con su prueba en rojo contra el commit
> anterior. El detalle está al final de este documento, en
> [Segunda pasada](#segunda-pasada--2026-08-14). Sigue sin haber push, PR, tag
> ni publicación.

> **Tercera pasada — 2026-08-14, rama `codex/installer-lifecycle-hardening`.**
> Cinco commits sobre `9290a7d`. No avanza por la lista: cierra los defectos que
> una revisión independiente encontró en el bloque de ciclo de vida que la
> pasada anterior dio por bueno. Registra **INSTALL-011** (hallazgo nuevo, Alta,
> cerrado en la misma pasada) y su gate **G4.9**, y mejora la evidencia de
> INSTALL-001, INSTALL-006 e INSTALL-010. El detalle está en
> [Tercera pasada](#tercera-pasada--2026-08-14). Sigue sin haber push, PR, tag
> ni publicación.

> **Cuarta pasada — 2026-08-14, misma rama.** Cinco commits sobre `85e3098`. La
> tercera pasada quedó **sin ratificar**: la revisión independiente confirmó los
> siete commits, el árbol limpio y las 137 pruebas focalizadas, y aun así
> encontró huecos de corrección. Registra **INSTALL-012** (hallazgo nuevo, Alta,
> cerrado) y su gate **G4.10**; hace que G3.3 se cumpla al pie de la letra;
> serializa la publicación de componentes y acota su respaldo; y **corrige una
> afirmación equivocada** de `01c2495` sobre el `Get-FileHash`. El detalle está
> en [Cuarta pasada](#cuarta-pasada--2026-08-14). Sigue sin haber push, PR, tag
> ni publicación.

## Los siete commits de código

Historial lógico y bisectable: cada commit deja la suite en verde y, cuando
cambia el contrato, lleva su propia porción de `tests/golden/tools_v1.json`.
Ninguno quedó con el contrato en rojo.

| Hash | Commit |
|---|---|
| `e32b966` | `test(contract): detectar ampliaciones sin ocultar rupturas` |
| `166ab05` | `fix(dual-mode): resolver la pista de modo sin AttributeError` |
| `6447cc3` | `fix(format): validar unicamente el delta administrado` |
| `7a78624` | `fix(schema): usar una familia compatible para schemas no publicados` |
| `3813130` | `feat(filters): soportar filtros por medida y merge` |
| `5a2feb9` | `feat(project): permitir proyecto activo cuando path se omite` |
| `8f5c35a` | `feat(capture): refrescar antes de capturar y declarar data_loaded` |

Los tres cambios contractuales de CONTRACT-001 se reparten así: `merge` en
`3813130`, los `path` de `pbi_close_desktop` / `pbi_open_and_refresh` /
`pbi_open_in_desktop` en `5a2feb9`, y `pbi_validate_desktop_render` entero
—`path` incluido— en `8f5c35a`, porque su firma y su cuerpo son el mismo
cambio: relajar la firma sin llevarse el cuerpo habría anunciado un `path`
opcional que la tool aún no sabía resolver.

## Validación tras el commit 7

Sobre el snapshot completo, el 2026-08-14:

| Comando | Resultado |
|---|---|
| `python -m pytest -q` | **2225 passed, 3 skipped** (243 s) |
| `python scripts/doctor.py` | **exit 0**, sin traceback, 1 aviso no bloqueante |
| `python -m tests.contract_utils` | **exit 0** — «El contrato MCP no cambio» |
| `git diff --check` | **exit 0** |

Los tres skips son ambientales: dos exigen Power BI Desktop sirviendo un modelo
y uno es deliberado (el modelo sintético dispara reglas no informativas).
Ninguno es de packaging. El aviso de `doctor` es sesión obsoleta de esta
máquina, detectada correctamente.

Esto **no cierra ningún hallazgo**: la suite en verde es exactamente la señal
que TEST-001, INSTALL-010 y RELEASE-001 dicen que no basta.

Esta matriz existe para que ninguna decisión del ciclo viva solo en el hilo de
una conversación. Cada entrada dice qué se autorizó, quién lo autorizó, con qué
evidencia se comprobó y en qué estado quedó. Una entrada sin evidencia
reproducible no está cerrada, está pendiente.

Esta es la **matriz canónica**. No hay ni habrá una segunda. La evidencia
detallada de la auditoría vive en
[`docs/audits/AUDIT_2026-08-14.md`](audits/AUDIT_2026-08-14.md) y los criterios
de aceptación en
[`docs/audits/ACCEPTANCE_10_OF_10.md`](audits/ACCEPTANCE_10_OF_10.md); aquí está
el estado.

Los identificadores de este ciclo (`CONTRACT-`, `CORE-`, `INSTALL-`, `RELEASE-`,
`TEST-`, `DOC-`, `CLI-`) no guardan relación con los riesgos históricos R2–R15
del proyecto, que **no se tocan** sin una prueba que falle antes y pase después.

## Contrato

| Id | Asunto | Autorizado por | Fecha | Estado |
|---|---|---|---|---|
| CONTRACT-001 | Cambios compatibles de contrato MCP (4 path opcionales, 7 parámetros nuevos, 5 descripciones, golden, guarda de ampliaciones) | Pablo — ratificación explícita | 2026-08-14 | **Cerrada — ratificada y verificada** |
| CONTRACT-002 | El golden congela solo el envelope `{result}`: una extensión del payload es invisible para la red de seguridad del contrato | Hallazgo derivado de CONTRACT-001 | 2026-08-15 | **Cerrada** — quinta pasada. `tests/golden/payloads_v1.json` congela la **forma** del payload —claves y tipos, nunca valores— y `tests/payload_contract.py` distingue lo que rompe (retirar, renombrar, cambiar de tipo) de lo que no (añadir). Seis mutaciones lo demuestran, incluida una clave **anidada**, que es justo lo que el `output_shape` declarado no podía ver. **Ampliada el 2026-08-15**: el muestreo pasa por `call_tool` —antes llamaba a las funciones registradas— y recorre **las 134 en dos escenarios deterministas**, así que el golden pasa de 2 tools a **53** (24 con payload de éxito, 29 solo de error). Y sobre todo, cada exclusión trae ahora su **dependencia medida** en `docs/COBERTURA_PAYLOADS.md`: «el resto necesita Desktop» era falso —solo **14** dependen de un modelo vivo; **77** solo necesitan argumentos válidos, que es trabajo—. **Cerrada del todo el 2026-08-15**: `tests/payload_argumentos.py` trae una llamada válida por tool, cada una sobre una **copia fresca** del proyecto sintético —muchas escriben, y compartiendo proyecto el resultado dependería del orden—, y con la red y los procesos **prohibidos**, que es lo que convierte «necesita Desktop» de suposición en medición. **134 de 134 con payload congelado**: 44 de éxito, 90 de error de dominio, 174 muestras. Cero exclusiones sin dependencia medida |
| CONTRACT-003 | Tres cambios de riesgo que CORE-004 pide y que rompen el contrato: `confirm` exigido en `pbi_refresh_model` y `pbi_open_and_refresh`; `pbi_apply_plan` de `confirm=True` a `False`; y `readOnlyHint` retirado de `pbi_open_pbip_project` / `pbi_select_model` | Pendiente de ratificación — derivado de CORE-004(a)(b)(c) | 2026-08-15 | **Cerrada** — ratificada por escrito el 2026-08-15 y aplicada en **2.0.0**, no antes: la versión pública sigue siendo 1.5.4 y 1.5.5 nunca se publicó, así que la ruptura se expresa como mayor. Los tres cambios, con sus 21 regresiones, en `tests/test_contract_003.py`; la migración para clientes en `docs/MIGRACION_1x_A_2.0.md`. [`docs/audits/CONTRACT_003_RATIFICATION.md`](audits/CONTRACT_003_RATIFICATION.md) trae, por cambio: contrato actual y propuesto, diff de schema y anotaciones, a quién puede romper, el peligro de dejarlo como está, la alternativa compatible cuando la hay, el plan de deprecación, la versión semántica recomendada y las pruebas que se activarían. El contrato **no se ha tocado**: `python -m tests.contract_utils` sale 0 |

## Seguridad funcional

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| CORE-001 | Detección falsa de proyecto cerrado (`project_state` ignora el título de ventana que `desktop_launcher` sí usa) | Crítica | G1.1 | **Cerrada** — 2026-08-14, con evidencia live |
| CORE-002 | Traversal sin `ensure_within_base` y escritura sin transacción en `desktop_capture` | Crítica | G1.2, G1.3 | **Cerrada** — 2026-08-14, con captura live e igualdad byte a byte |
| CORE-003 | Tras el timeout, el hilo daemon sigue en `SaveChanges` y `safe_to_retry` sale `true` | Alta | G1.4 | **Cerrada** — 2026-08-15, quinta pasada. `safe_to_retry` es `False` cuando `cancel_confirmed` es `false`, y la regla se aplica al HECHO y no a un código concreto: cualquier salida que declare `cancel_confirmed: false` afirma que algo sigue corriendo. Un `refresh_timeout` sin `details` tampoco acredita: hace falta un `true` explícito |
| CORE-004 | Anotaciones y confirmaciones que no describen el efecto (4 sub-hallazgos) | Alta | G1.5, G1.6 | **Cerrada** — 2026-08-15, quinta pasada. **(d) cerrado y G1.6 cumplido**: el temporal del validador sale del árbol del usuario. **(a), (b) y (c) aplicados el 2026-08-15 en 2.0.0**, tras la ratificación escrita: `confirm` exigido en `pbi_refresh_model` y `pbi_open_and_refresh`, default de `pbi_apply_plan` a `False`, y las dos tools de sesión reclasificadas a `session_write` —`readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true` comprobado abriendo dos veces—. **Cerrada**; G1.5 cumplido |
| CORE-005 | `msg` y `exc` entran al log sin pasar por `redact()` | Alta | G1.7 | **Cerrada** — 2026-08-15, quinta pasada. Los dos campos pasan por `redact()`. Hizo falta además ampliar la redacción: reconocía cadenas que SON una ruta y no frases que CONTIENEN una, que es el caso del texto de una excepción; y `_redact_path` conservaba dos segmentos, justo donde vive el nombre del cliente |
| CORE-006 | Sin cerrojo interproceso en `txn`/`planning` (el mecanismo existe en `idempotency`) | Alta | G1.8 | **Cerrada** — 2026-08-15, quinta pasada. Cerrojo por proyecto en `txn` —y con él `planning`, que escribe a través de `txn`—, con la primitiva EXTRAÍDA a `services/cerrojo.py` en vez de duplicada. Dos procesos reales sobre el mismo `.pbip`: el segundo espera su turno y los dos aplican. **El *lost update* del hallazgo no se reproducía**: la huella de `Transaction` ya impedía sobrescribir en silencio, y el segundo fallaba con `transaction_failed`; lo que se pierde sin cerrojo es el trabajo del segundo, no el del primero |

## Instalación y ciclo de vida

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| INSTALL-001 | La siembra mueve el runtime de N−1 antes de validar el nuevo, sin rollback | Alta | G4.1 | **Parcialmente cerrada** — 2026-08-14, tercera pasada. Además de la siembra por copia y la promoción con journal, ahora el **lanzador real** selecciona y ejecuta N−1: con fallo inyectado en pip, DLL, esquemas, handshake y promoción, un cliente MCP por stdio recibe las 134 tools de la versión anterior, no las dos del bootstrap. G4.1 queda amarillo: el runtime servido es de prueba, y la corrida sobre una instalación real es la VM |
| INSTALL-002 | Node <20 o fallo del validador opcional deja `state=failed` | Alta | G3.4 | **Parcialmente cerrada** — 2026-08-14 con preflight por versión de Node, fallo del opcional no fatal y motivo registrado. G3.4 exige VM con Node 18 |
| INSTALL-003 | Cinco caminos publicados ejecutan desde `main` sin pin ni verificación | Crítica | G6.3, G6.4 | **Parcialmente cerrada** — 2026-08-14. Los cinco caminos resuelven a referencia fija y el one-paste verifica el SHA-256 antes de ejecutar; falta descargar el asset de v1.5.5 y comprobar sus bytes, y esa release no existe |
| INSTALL-004 | La verificación final es una coincidencia de subcadena sobre `plugin list` | Media | G3.5 | **Parcialmente cerrada** — 2026-08-15, quinta pasada. La verificación aísla la LÍNEA del plugin y juzga su estado: rechaza `disabled`, `inactive` y las líneas con error, y un formato que no reconozca **avisa en vez de aprobar**. Node deja de imprimirse en verde sin comparar: se contrasta contra un mínimo, y una prueba exige que ese mínimo coincida con el de `plugin_bootstrap.py` —si divergieran, el instalador aprobaría un Node que el bootstrap rechaza después—. **G3.5 sigue pendiente**: probarlo con el plugin realmente deshabilitado exige una instalación de Claude, y este ciclo no modifica instalaciones reales |
| INSTALL-005 | El wheel no lleva scripts, DLL, esquemas ni bootstrap | Alta | G3.6 | **Cerrada** — 2026-08-15, quinta pasada. `pbi_health_check` distingue *instalado* de *operativo*: `completeness` enumera cada pieza que falta, qué deja de funcionar sin ella y **el comando exacto** que la completa, y separa lo obligatorio de lo opcional. G3.6 cumplido y comprobado sobre una instalación **pip pura** —artefacto construido, venv limpio, fuera del checkout—. **Cerrada del todo el 2026-08-15**: los tres descargadores viven en `horizun_pbi_mcp/completado/` y el comando de completado viaja en el wheel como `horizun-pbi-completar`. Antes recomendaba `python scripts/fetch_libs.py`, y `scripts/` no se empaqueta: el diagnóstico era correcto y la instrucción, imposible. Verificado instalando wheel **y** sdist en venv limpios: el ejecutable está, y `--check` sale 1 enumerando lo que falta |
| INSTALL-006 | Los esquemas se publican por copia archivo a archivo sobre el destino vivo | Media | G4.2, G4.3 | **Cerrada** — 2026-08-14, tercera pasada. Esquemas y validador preparan en un hermano, se releen enteros y se publican con el ciclo de vida compartido; el destino se observa **en el instante de publicar** y sigue byte a byte como estaba. G4.2 cumplido. **Cuarta pasada**: cada publicador toma el cerrojo de la raíz de su componente antes de recuperar, preparar, promover o limpiar —dos procesos de verdad lo demuestran— y el respaldo de cada publicación se recoge al terminar, así que deja de crecer con cada actualización. **G4.3 cerrado el 2026-08-15 con `npm` de verdad**: instalación real sobre un destino que ya tenía una versión, el proceso matado a mitad, el destino anterior byte a byte intacto, cero `.staging-` y cero journals huérfanos, y el reintento posterior limpio. Solo hacía falta Node ≥20, no una VM: llevaba meses en la lista de lo imposible por no haberlo comprobado |
| INSTALL-007 | Reintento sin `--scope user` y `ExecutionPolicy` persistente | Media | G4.8 | **Cerrada** — 2026-08-15, quinta pasada. El reintento **se conserva** —sin él se rompe el camino del PC vacío, que winget bloquea con `0x8A150044` cuando un manifiesto ajeno no está etiquetado como *user*— pero deja de ser silencioso: se anuncia **antes**, se comprueba **después** si aterrizó en el perfil, y `-SoloUserScope` permite prohibirlo a quien exija user-scope estricto. El cambio de `ExecutionPolicy` se declara permanente en el código y `RUNBOOK_INSTALACION.md` dice cómo revertirlo |
| INSTALL-008 | No existe `uninstall` ni `purge` | Media | G4.4, G4.5 | **Cerrada** — 2026-08-15, quinta pasada. `--uninstall`, `--purge` e `--inventory`. **La ejecución en seco es el comportamiento por defecto**: sin `--confirm` enumeran y no tocan nada, así que un error de dedo es un susto y no una pérdida. `outputs/` y `backups/` sobreviven salvo que se pidan; tras desinstalar, `residual_bytes` es exactamente el peso de los datos del usuario. Bajo el cerrojo del ciclo de vida y sin salir nunca del data root |
| INSTALL-009 | Sin lock ni hashes, sin bundle offline ni runbook de proxy | Media | G4.6, G4.7 | **Parcialmente cerrada** — **G4.6 cumplido el 2026-08-15**: cinco locks `win_amd64 × {3.10, 3.11, 3.12, 3.13, 3.14}`, cada uno generado con su intérprete real y fijado por versión y SHA-256. Los cinco se instalaron dos veces en venv limpios con `--require-hashes` y produjeron conjuntos idénticos; una matriz ligera de CI repite el oráculo en las cinco versiones. El generador se niega a resolver para otro intérprete, evitando la falsa garantía anterior de `pip --python-version`, y el instalador selecciona sólo por coincidencia exacta. **G4.7 sigue parcial**: `scripts/bundle.py` construye, verifica e instala el bundle offline, probado con pip real, `--no-index`, 134 tools y red prohibida; aún falta observarlo en una VM realmente desconectada o detrás de un proxy corporativo |
| INSTALL-010 | `ready` se escribe sin handshake contra el runtime instalado | Alta | G3.1, G3.3 | **Parcialmente cerrada** — 2026-08-14, tercera pasada. El oráculo pasa de «100 tools cualesquiera con prefijo `pbi_`» a exigir el contrato: `serverInfo.name` exacto, versión igual a la preparada, `tools/list` bien formado y ninguna de las 134 ausente, contra un baseline **empaquetado en el wheel**. **Cuarta pasada**: G3.3 pasa a cumplirse **literalmente** —tras corromper el activo, `state` vale `degraded` y no `ready`, que es lo que el gate pide y lo que la tercera pasada no hacía—. Sigue amarillo porque los runtimes que se corrompen son de prueba; G3.1 exige VM limpia |
| INSTALL-011 | La recuperación confía rutas del journal y se ejecuta fuera del lock, permitiendo operaciones fuera del data root y carreras con una promoción | Alta | G4.9 | **Cerrada** — 2026-08-14, tercera pasada. Reproducido: un journal preparado a mano movió `root/.staging-demo` a una carpeta hermana de la raíz. El journal deja de ser autoridad sobre rutas —esquema 2, solo nombres de hijos directos, validados léxica y resueltamente— y el ciclo de vida entero pasa a ocurrir dentro del cerrojo |
| INSTALL-012 | El launcher puede mezclar dos servidores MCP en el mismo stdout porque infiere ausencia de salida a partir de la duración del proceso | Alta | G4.10 | **Cerrada** — 2026-08-14, cuarta pasada. El umbral temporal desaparece: el handshake se hace en un proceso aparte con tuberías propias y solo se le entrega el stdio del cliente a un runtime ya verificado. Entregado el canal, no se arranca nada más sobre él |

## Release y supply chain

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| RELEASE-001 | CI prueba en Windows; `publish-pypi` reconstruye en Ubuntu y publica eso | Crítica | G6.1, G6.5 | **Parcialmente cerrada** — 2026-08-14. Una sola construcción, `SHA256SUMS`, SBOM y verificación en cada consumidor; G6.5 cumplido. G6.1 exige una release real |
| RELEASE-002 | Los workflows de publicación no dependen de un CI verde | Crítica | G6.2 | **Cerrada** — 2026-08-15. Publicación con `needs` sobre build y test, solo desde tag, dispatch inerte por defecto y catorce guardas demostradas por mutación. **G6.2 cumplido con evidencia del remoto**: en el run 31914746886 `publicar-pypi` falló y `publicar-mcp` quedó omitido sin ejecutar un paso. Alcance exacto en [`audits/EVIDENCIA_REMOTA_2026-08-15.md`](audits/EVIDENCIA_REMOTA_2026-08-15.md) |
| RELEASE-003 | Sin CodeQL ni Dependabot; actions con tags flotantes; controles del remoto sin comprobar | Alta | G7.1–G7.6 | **Parcialmente cerrada** — 2026-08-15. G7.6 cumplido (cero tags flotantes) y **G7.2 cumplido**: CodeQL en verde sobre `main`/`1f0405b`, run 31913970370. G7.1, G7.3, G7.4 y G7.5 son ajustes del remoto, **comprobados como deshabilitados** el 2026-08-15; los comandos están preparados y sin ejecutar en [`PLAN_SEGURIDAD_GITHUB.md`](PLAN_SEGURIDAD_GITHUB.md) |
| RELEASE-004 | **Ningún job creaba la GitHub Release**, mientras el one-paste del README, de `docs/INSTALL.md` y de la skill descarga el instalador de `releases/download/v<version>/…`: el camino de instalación que se ofrece apuntaba a un asset inexistente | Crítica | G6.4 | **Parcialmente cerrada** — 2026-08-15. Defecto de **omisión**, descubierto por el intento fallido de `v2.0.0`: configurar PyPI y relanzar habría publicado paquete y registro con el one-paste en 404. Se añade `publicar-github-release` —`needs` de los cuatro anteriores, único job con `contents: write` y sin OIDC, assets derivados de `SHA256SUMS`, sin reemplazar nada, idempotente, y relectura de cada asset tras subirlo, incluida la URL del instalador contra el manifest—. Guardas y mutaciones en `tests/test_release_pipeline.py`; el flujo entero contra una API simulada en `tests/test_release_github.py`. **Falta ejecutarlo**: mientras no exista la release en el remoto, G6.4 sigue parcial |

## Pruebas y contrato

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| TEST-001 | `test_packaging` convierte fallos en skips y prueba en venv no limpio | Alta | G8.2, G8.3 | **Cerrada** — 2026-08-14, con dos mutaciones medidas: un paquete irresoluble y otro que no compila salían verde y ámbar, ahora salen rojo |
| TEST-002 | Inventario de las 134 tools: ejecución MCP, casos negativos, payload congelado | Alta | G2.3, G2.4 | **Cerrada** — 2026-08-15, quinta pasada. `docs/INVENTARIO_TOOLS.md` publica el inventario tool por tool y **se genera** con `python -m tests.inventario_tools`, no se escribe. Las 134 se ejecutan por `call_tool` en cada corrida contra un caso negativo calculado de su propio esquema: 114 rechazadas en la validación antes del cuerpo, 11 con sobre `ok: false` y código sin proyecto activo, 1 con el adaptador del entorno roto a propósito, y 8 sin modo de fallo —ejecutadas igual, se les exige `ok: true`, para que la exención caduque sola—. **Cero excepciones declaradas**: las dos que lo estaban —`pbi_list_desktop_models` y `pbi_test_connection`— lo estaban por *determinismo*, no por imposibilidad, y una prueba cuenta las llamadas **observadas**, no las prometidas. La columna de payload congelado la llena CONTRACT-002, que sigue parcial |
| TEST-003 | Sin cobertura live verificada de los seis escenarios de Desktop | Alta | G5.1–G5.6 | **Abierta** — reevaluada gate por gate el 2026-08-15 en vez de dar el bloque por imposible. **G5.5 ya estaba cumplido**: mismo hallazgo y misma evidencia live del 2026-08-14 que G1.1, sobre un `.pbip` sintético desechable. **G5.6 pasa a parcial**: su prueba existe, es local y se ejecutó para CORE-002; falta repetirla dentro de la matriz de escenarios. Siguen externos G5.1–G5.4, que sí exigen Desktop sirviendo un modelo con datos |
| TEST-004 | `isolated_settings` deja sin DLL de Analysis Services a las pruebas live | Media | G5.2, G5.4, G5.6 | **Cerrada** — 2026-08-14 |

## Documentación y CLI

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| DOC-001 | El README ofrece `mode=both` —con ejemplo— y lo declara bloqueado en el mismo archivo | Media | G8.5 | **Cerrada** — 2026-08-15, quinta pasada. El ejemplo deja de mandar usarlo y cada listado del modo dice que está bloqueado. Una prueba caza los usos de `mode` sin distintivo y **no** las menciones que explican el bloqueo |
| DOC-002 | `AGENTS.md:126` niega la publicación en PyPI que ahora hace `release.yml` (antes `publish-pypi.yml`, retirado el 2026-08-14) | Media | G8.6 | **Cerrada** — 2026-08-15, quinta pasada. `AGENTS.md` describe la política real: se publica por tag y solo tras el build-and-test verde del mismo commit. La prueba compara la afirmación con los workflows que existen |
| DOC-003 | "Completely empty PC" no dice que Power BI Desktop queda fuera | Baja | G8.7 | **Cerrada** — 2026-08-15, quinta pasada. El README dice qué cubre el «PC vacío» y qué no: sin Desktop queda todo el lado `.pbip` y no queda la capa LIVE, la captura ni la validación de render |
| DOC-004 | Sin runbook de update, rollback, uninstall, purge, proxy ni offline | Media | G8.8 | **Cerrada** — 2026-08-15, quinta pasada. `docs/RUNBOOK_INSTALACION.md` cubre los seis procedimientos, cada paso un comando ejecutable y comprobado contra la instalación real en solo lectura. **Dice explícitamente lo que NO existe** y da el procedimiento manual, en vez de ofrecer comandos que fallarían. Esa lista **se encoge**: `uninstall` y `purge` existen desde INSTALL-008 y el runbook los documenta con su modo seco; hoy solo queda fuera el bundle offline (G4.7). Una prueba exige que los scripts citados existan y que la lista de ausentes sea la de hoy |
| CLI-001 | El one-paste instala y verifica solo Claude | Media | G3.2 | **Parcialmente cerrada** |

## Los gates, en una sola partición

Los conteos de gates viven en
[`docs/audits/CLASIFICACION_GATES.md`](audits/CLASIFICACION_GATES.md): cada uno
de los 54 en **una sola** de cinco categorías —cumplido, parcial,
pendiente-local, pendiente-ratificación, pendiente-externo— con su motivo, y una
prueba que exige que sumen 54 y que ningún otro documento los contradiga.

Existe porque durante un tiempo hubo dos cuentas incomparables —«30 cumplidos, 5
parciales, 19 pendientes» y «22 externos»— y entre las dos se podía afirmar «no
queda trabajo local» sin que nada lo desmintiera. **Hoy no queda ningún gate con trabajo local ni pendiente de firma**: los 20 que
faltan esperan un entorno. Y esa lista se encogió dos veces el mismo día, una
por mirar dentro en vez de dar el bloque por imposible —**G4.3** solo necesitaba
Node ≥20— y otra que CI reabrió y luego se cerró con evidencia real: **G4.6**.

## Cuentas

34 entradas: **23 cerradas**
(CONTRACT-001, CONTRACT-002, CONTRACT-003, CORE-001, CORE-002, CORE-003,
CORE-004, CORE-005, CORE-006, DOC-001, DOC-002, DOC-003, DOC-004, INSTALL-005,
INSTALL-006, INSTALL-007, INSTALL-008, INSTALL-011, INSTALL-012, RELEASE-002,
TEST-001, TEST-002, TEST-004),
**10 parcialmente cerradas**
(INSTALL-001, INSTALL-002, INSTALL-003, INSTALL-004, INSTALL-009,
INSTALL-010, RELEASE-001, RELEASE-003, RELEASE-004, CLI-001),
**1 abierta**.

**Este conteo no se escribe a mano.** `tests/test_documentacion_coherente.py`
lo recalcula desde las filas de las seis tablas y falla si el párrafo y la
matriz dejan de coincidir, si un identificador aparece en un documento y no en
el otro, o si el estado que declara esta matriz contradice el que declara
`audits/AUDIT_2026-08-14.md`. Un conteo escrito de memoria envejece en la
primera edición y nadie se entera.

Al 2026-08-15, tras la **quinta** pasada. La cuenta anterior era 7 / 11 / 14:
cierran **CORE-003**, **CORE-005** y **CORE-006**, e **INSTALL-005** pasa de
abierta a parcial con su gate cumplido. Las cuatro con regresión roja contra el
commit anterior y sin depender de ninguna máquina limpia.

Dos pasadas seguidas han cerrado un hallazgo que **introdujo la pasada
anterior** (INSTALL-011 lo trajo la remediación de INSTALL-001; INSTALL-012, la
de INSTALL-011). No es casualidad ni mala suerte: es lo que pasa cuando se
sustituye un mecanismo por otro más complejo, y es el argumento más fuerte a
favor de que cada pasada la revise alguien que no la escribió.

CORE-002 se cerró el 2026-08-14 tras destrabar TEST-004: captura live real con
página explícita y fit-to-page, PNG producido, **14 archivos antes y 14 después
con el mismo hash**, cero `.tmp`, cero journals pendientes, cero procesos
restantes, en 10,67 s.

TEST-004 nació de refutar una hipótesis. Se propuso registrar **CORE-007** —
«`open_pbix` confunde ventana abierta, motor disponible y datos cargados»— y la
medición instrumentada lo descartó: `open_pbix` resuelve las tres etapas en
~10 s. El bloqueo era que `isolated_settings` apunta `libs_dir` a un `tmp_path`
vacío, así que ADOMD no carga y `desktop_discovery` no puede leer `catalog` ni
`table_count`. **CORE-007 no figura en esta matriz**: la causa propuesta no
existía. La separación de readiness queda como posible mejora de diagnóstico.

La evidencia completa está en
[`audits/AUDIT_2026-08-14.md`](audits/AUDIT_2026-08-14.md#core-001--detección-falsa-de-proyecto-cerrado).

**R2 sigue pendiente de revisión independiente.** Cerrar CORE-001 no lo
reclasifica: eso exige su propia prueba que falle antes y pase después.
Cinco de severidad crítica: CORE-001, CORE-002, INSTALL-003, RELEASE-001,
RELEASE-002. Las dos primeras están cerradas y las tres restantes,
parcialmente: **ninguna crítica sigue enteramente abierta**, y lo que les falta
a las tres es la misma cosa — una release real de v1.5.5, que no existe.

**Ninguna entrada de la auditoría se cerró en la PRIMERA pasada.** Aquella fue
un triaje documental: se verificó el estado real de cada hallazgo contra el
código de hoy, sin remediar ninguno. La segunda pasada sí remedia, y está al
final de este documento.

Tres hallazgos resultaron distintos de como los describía el reporte original
—CORE-003, INSTALL-002 e INSTALL-004, todos parcialmente atendidos ya— y uno
resultó peor: `fetch_pbir_schemas.py:17` **afirma** instalar de forma atómica y
`:217-218` copia archivo a archivo sobre el destino vivo (INSTALL-006).

---

## CONTRACT-001 — Ratificación de los cambios compatibles de contrato

**Fecha:** 2026-08-14
**Origen:** addendum de inventario de diferencias de contrato del árbol de
trabajo contra el contrato congelado en `2973f1d`.

### Qué quedó autorizado

1. Los cuatro parámetros `path` pasan de requeridos `string` a opcionales
   `null|string` con default `None`.
2. Los siete parámetros opcionales nuevos.
3. Las cinco mejoras de descripción pública.
4. Actualizar `tests/golden/tools_v1.json` **exclusivamente** con esas
   diferencias.
5. Revisar y **conservar** el soporte de ampliaciones de tipo en
   `tests/contract_utils.py`, **condicionado** a que pruebas independientes
   demuestren que sigue detectando estrechamientos, cambios incompatibles de
   tipo, cambios de defaults existentes y parámetros nuevos requeridos.

### Qué NO quedó autorizado

La ratificación es cerrada. **No** permite eliminar tools, cambiar outputs,
modificar otros defaults ni introducir diferencias adicionales. Cualquier
diferencia fuera del inventario de abajo requiere una entrada nueva en esta
matriz, no se ampara en CONTRACT-001.

### Evidencia — el inventario, reproducido

No se dio por bueno el inventario recordado: se recalculó comparando el
contrato **que sirve el servidor ahora** contra `git show
HEAD:tests/golden/tools_v1.json`. Salió idéntico al ratificado, sin sobrantes.

Totales: **134 tools antes y 134 ahora**, cero eliminadas, cero nuevas.

**A. Los cuatro `path`** — requerido `string` → opcional `null|string`, default `None`:

| Tool | Parámetro |
|---|---|
| `pbi_close_desktop` | `path` |
| `pbi_open_and_refresh` | `path` |
| `pbi_open_in_desktop` | `path` |
| `pbi_validate_desktop_render` | `path` |

Omitirlo no es un hueco: `tools/_common.py::ruta_de_proyecto` resuelve el
proyecto `.pbip` activo que el servidor ya conoce, y falla con `ValidationError`
si no hay ninguno o si llegan `path` y `pbip_path` distintos entre sí.

**B. Los siete parámetros opcionales nuevos:**

| Tool | Parámetro | Tipo | Default |
|---|---|---|---|
| `pbi_close_desktop` | `pbip_path` | `null\|string` | `null` |
| `pbi_open_and_refresh` | `pbip_path` | `null\|string` | `null` |
| `pbi_open_in_desktop` | `pbip_path` | `null\|string` | `null` |
| `pbi_set_visual_filter` | `merge` | `boolean` | `false` |
| `pbi_validate_desktop_render` | `pbip_path` | `null\|string` | `null` |
| `pbi_validate_desktop_render` | `refresh` | `boolean` | `false` |
| `pbi_validate_desktop_render` | `refresh_timeout_seconds` | `integer\|null` | `null` |

Los cambios preservan todas las invocaciones anteriormente válidas. Los
parámetros nuevos conservan el comportamiento previo cuando se omiten; los
cuatro path opcionales amplían el contrato permitiendo una invocación que antes
era inválida y que ahora resuelve el proyecto activo.

**C. Las cinco descripciones públicas:**

| Tool | Caracteres |
|---|---|
| `pbi_close_desktop` | 716 → 824 |
| `pbi_open_and_refresh` | 588 → 753 |
| `pbi_open_in_desktop` | 727 → 845 |
| `pbi_set_visual_filter` | 1051 → 1590 |
| `pbi_validate_desktop_render` | 1064 → 1936 |

**D. Lo que se comprobó que NO cambió** — cada uno medido, no supuesto:

| Comprobación | Resultado |
|---|---|
| Tools eliminadas | 0 |
| Tools nuevas | 0 |
| Parámetros eliminados | 0 |
| Parámetros nuevos obligatorios | 0 |
| Parámetros que pasaron a obligatorios | 0 |
| Defaults de parámetros preexistentes | 0 modificados |
| Enums | 0 cambios |
| `output_shape` declarado | 0 cambios |
| `annotations` | 0 cambios |

Veredicto del recálculo: **contrato cambiado de forma compatible**.

**Matiz sobre "no cambiar outputs".** Lo verificado es que la *forma de salida
declarada en el contrato* no cambió. Para estas tools esa forma es el envelope
genérico `{"type": "object", "properties": ["result"]}`, así que el golden no
congela las claves del payload. Que CONTRACT-001 no autorice cambiar outputs se
cumple al nivel que el contrato congela.

**Claves nuevas de `pbi_capabilities`**, registradas aquí para que no queden
sin declarar:

| Clave | Tipo | Origen |
|---|---|---|
| `written_unchecked_schemas` | `array[string]` | `tools/ops_tools.py::_cap_validador_oficial` |
| `unchecked_note` | `string` | `tools/ops_tools.py::_cap_validador_oficial` |

**Clasificación: extensión compatible de respuesta**, permitida por
`AGENTS.md:35` —*"adding new fields to the response dict"* está en la lista de
cambios admitidos, frente a *"changing the response shape"* (`AGENTS.md:30`),
que sí sería ruptura. No se retiró ni se renombró ninguna clave: solo se
añadieron.

Y el punto que hay que dejar dicho en voz alta: **el golden actual solo congela
el envelope `{result}` y no detectaría un cambio interno del payload.** Estas
dos claves están *admitidas* por la regla, pero no están *verificadas* por la
red de seguridad — ni lo estaría una clave retirada o renombrada, que sí
rompería a un cliente. Esa limitación queda registrada como **CONTRACT-002**,
hallazgo independiente pendiente de remediación: no forma parte de lo ratificado
en CONTRACT-001 ni se cierra con él.

**E. El golden.** `tests/golden/tools_v1.json` se regeneró y coincide byte a
byte con lo que sirve el servidor: `test_contract_matches_golden` compara
servidor contra golden y pasa. Su diff contra `HEAD` contiene exactamente los
bloques A, B y C y nada más.

### Evidencia — la condición del punto 5

Conservar el soporte de ampliaciones de tipo solo era autorizable si la guarda
seguía cazando lo demás. Las cuatro detecciones exigidas tienen prueba propia:

| Detección exigida | Prueba |
|---|---|
| Estrechamientos (`null\|string` → `string`) | `test_estrechar_un_tipo_sigue_siendo_ruptura` — `tests/test_tool_contract.py:426` |
| Cambios incompatibles de tipo (`string` → `integer`) | `test_diff_detects_type_change` — `:406` |
| Cambios de defaults existentes | `test_diff_detects_changed_default` — `:397` |
| Parámetros nuevos requeridos | `test_diff_detects_new_required_param` — `:387` |
| *(control positivo)* la ampliación se acepta | `test_ampliar_un_tipo_no_es_ruptura` — `:415` |

Que estén en verde no demuestra que detecten: demuestra que hoy nadie rompió
nada. Así que se rompió la guarda **a propósito**, una mutación por vez sobre
una copia del módulo, exigiendo que la detección correspondiente se apagara.
Una mutación que sobrevive es una prueba que no ata nada.

| Mutación en `contract_utils.py` | Apagó | Veredicto |
|---|---|---|
| M1 — `_es_ampliacion` devuelve siempre `True` | estrechamiento, tipo incompatible | muerta |
| M2 — `_es_ampliacion` devuelve siempre `False` | ampliación aceptada | muerta |
| M3 — se salta la comparación de defaults | default existente cambiado | muerta |
| M4 — un parámetro nuevo obligatorio pasa por compatible | parámetro nuevo obligatorio | muerta |
| M5 — `_es_ampliacion` decidida al revés | estrechamiento, ampliación aceptada | muerta |

Las cinco mutaciones mueren y ninguna apaga de más: cada prueba está atada al
comportamiento que dice cubrir, y aceptar ampliaciones es una decisión
deliberada de la guarda, no la ausencia de una comprobación.

El criterio conservado es estricto y direccional: `_es_ampliacion` exige que el
conjunto de tipos viejo sea **subconjunto propio** del nuevo. `string` →
`null|string` pasa; `null|string` → `string` no.

### Cómo reproducir la evidencia

```bash
python -m pytest tests/test_tool_contract.py -q
```

29 pruebas en verde el 2026-08-14. Las cinco pruebas unitarias de la guarda
quedan como cobertura permanente del repositorio.

Recalcular el inventario contra el contrato congelado en `HEAD` y correr las
mutaciones fueron comprobaciones de este ciclo; sus resultados están arriba. El
arnés de mutación **no se incorpora al repositorio**: la adopción de mutation
testing reproducible se evaluará después como tarea separada de calidad/CI.

### Estado

**CERRADA** el 2026-08-14. Las cinco autorizaciones se cumplen, la condición del
punto 5 está satisfecha con prueba por mutación, y no se detectó ninguna
diferencia fuera del inventario ratificado.

### Pendiente, fuera de CONTRACT-001

- CONTRACT-002, abajo: el golden no cubre el interior del payload.
- Suite completa (`python -m pytest -q`), `scripts/doctor.py` y el resto del
  `docs/RELEASE_CHECKLIST.md`: no se corrieron en esta verificación, que se
  limitó al contrato.
- Entrada de CHANGELOG del ciclo.
- Adopción de mutation testing reproducible en CI: tarea separada de calidad,
  sin abrir todavía.
- Push, PR, tag y publicación: **expresamente no autorizados todavía**. Los
  commits locales de la rama `codex/p1-p4-audit-checkpoint` sí están
  autorizados y hechos.

---

## CONTRACT-002 — El golden no congela el interior del payload

**Fecha:** 2026-08-14
**Origen:** hallazgo derivado de la verificación de CONTRACT-001. **No está
ratificado ni cerrado por aquella entrada.**

### El hallazgo

`tests/golden/tools_v1.json` congela el `output_shape` *declarado* de cada tool.
Para las tools con envelope genérico —`pbi_capabilities`,
`pbi_set_visual_filter`, `pbi_validate_desktop_render` y las demás que devuelven
`{"type": "object", "properties": ["result"], "required": ["result"]}`— ese
declarado no dice nada del contenido. Consecuencia: **la red de seguridad del
contrato no ve ningún cambio dentro del payload.**

Añadir claves es un cambio permitido (`AGENTS.md:35`), así que ahí la ceguera no
hace daño. El problema es el caso contrario: **retirar o renombrar una clave del
payload rompe a un cliente y hoy pasaría en verde**, sin que
`python -m tests.contract_utils` diga una palabra. La suite daría la misma
sensación de seguridad en los dos casos, que es exactamente el modo de fallo
contra el que esta red existe.

Las dos claves de `pbi_capabilities` registradas en CONTRACT-001 no son el
defecto: son lo que lo hizo visible.

### Alcance

No cuantificado todavía. Falta contar cuántas de las 134 tools declaran envelope
genérico frente a una forma real, que es lo que decide si esto se remedia
enriqueciendo el `output_shape` declarado, congelando las claves del payload en
el golden, o ambas cosas.

### Estado

**Abierta — pendiente de remediación.** Sin diagnóstico cerrado, sin propuesta
elegida y sin autorización pedida. No bloquea CONTRACT-001, que quedó cerrada
con su propio alcance.

---

## Segunda pasada — 2026-08-14

Rama `codex/install-003-immutable-sources`, base `b2d851a`. Seis commits
locales. **Sin push, sin PR, sin tag, sin publicación.**

| Hash | Commit | Cierra |
|---|---|---|
| `103162c` | `fix(installer): add side-effect-free dry run and lock release asset` | INSTALL-003 (1/2) |
| `7ac01ee` | `fix(installer): verify immutable release bootstrap` | INSTALL-003 (2/2) |
| `67719ed` | `test(packaging): fail closed in clean wheel and sdist installs` | TEST-001 |
| `dec1318` | `ci(release): build once and test published artifacts` | RELEASE-001 |
| `0a2e618` | `ci(release): gate publishing on verified artifacts` | RELEASE-002 |
| `c74dd11` | `ci(security): pin actions and add repository security checks` | RELEASE-003 |

### El rojo de cada uno

Ninguna corrección se aceptó sin ver antes fallar su prueba contra el commit
anterior. No es ceremonia: una prueba que nunca ha estado en rojo no ha
demostrado que ate nada.

| Corrección | Rojo contra | Resultado |
|---|---|---|
| `-DryRun` sin efectos | `b2d851a` | 9 de 12 fallando. El log de sombras dejó escrito lo que el instalador viejo hacía con `-DryRun`: `claude --version`, `claude plugin marketplace add`, `claude plugin install`, `claude plugin list`. Sin `param()`, PowerShell se traga la bandera como argumento suelto e **instalaba de verdad** |
| One-paste verificado | `b2d851a` | **28 de 28** fallando |
| Packaging fail-closed | `7ac01ee`, dos mutaciones | ver abajo |
| DAG de publicación | — | 9 guardas × 9 mutaciones, cada una enciende la suya y ninguna apaga de más |

### Un defecto del propio ciclo: la suite estuvo en rojo cinco commits

Conviene dejarlo escrito en vez de que lo descubra quien bisecte.

La prueba que exigía conservar `instalar.ps1 | iex` estaba en **dos** sitios, no
en uno. El de `tests/test_supply_chain.py` se invirtió al arreglar el one-paste;
el de `tests/test_plugin_distribution.py:191` —`assert "instalar.ps1 | iex" in
docs`— no se vio hasta correr la suite **completa** al final del ciclo, porque
entre commit y commit solo se corrieron las pruebas focalizadas.

Consecuencia: desde `7ac01ee` hasta `c74dd11`, **una** aserción falla. Los seis
commits son correctos en lo suyo y su evidencia se sostiene, pero
`python -m pytest -q` no sale limpio en ese rango, que es exactamente lo que
`AGENTS.md` pide de cada commit. Se corrige en el commit siguiente. Para
bisectar dentro de ese tramo:

```bash
python -m pytest -q -k "not test_el_instalador_de_un_pegado_es_ascii_y_sin_admin"
```

La lección es la de siempre en este documento: **una prueba focalizada verde no
es la suite verde**, y es el mismo error de forma que TEST-001 describe — creer
una señal más estrecha de lo que aparenta.

### TEST-001 — las dos mutaciones

El punto del hallazgo no era que faltaran pruebas, sino que las que había eran
**estructuralmente incapaces** de fallar. Se midió sobre el mismo árbol roto,
con las pruebas viejas y las nuevas:

| Mutación | Pruebas viejas | Pruebas nuevas |
|---|---|---|
| `mcp>=99,<100` — dependencia que no existe | **14 passed** (verde con un paquete que nadie puede instalar) | **10 failed**, con el listado de versiones reales de PyPI |
| `build-system` con un requisito inexistente | **3 passed, 11 skipped** (ámbar con un paquete que no compila) | **10 failed, 11 errors** |

El ámbar es el hallazgo: entre 2263 pruebas, un skip es invisible.

### `py -3` no era una sonda inocente

Lo encontró la prueba de «cero efectos», no la lectura del código. En Windows
moderno `py` es el Python Install Manager: preguntarle por un intérprete que no
tiene lo hace **descargarlo e instalarlo**. Con `LOCALAPPDATA` limpio, un solo
`py -3 -c "import sys;print(sys.executable)"` dejó `pythoncore-3.14-64-3.14.7.zip`,
su `.job` y `last_welcome.txt` en el caché.

Es decir: en el PC vacío —el único caso donde `-DryRun` de verdad importa— la
sonda de diagnóstico instalaba Python. En seco ahora se resuelve mirando disco,
como ya hacía `launch.cmd`, y el diagnóstico no empeora.

### El asset congelado

| Dato | Valor |
|---|---|
| Archivo | `scripts/instalar.ps1` |
| Nombre del asset | `horizun-pbi-mcp-instalar.ps1` |
| Tamaño (**el 2026-08-14**) | 21 016 bytes |
| SHA-256 (**el 2026-08-14**) | `33fa1058d95445b97b7118d1c1a0fff9392d464f9bafdfdfc11dd069f970dad5` |
| Codificación | ASCII, sin BOM, LF, cero CRLF |
| Blob de git | byte a byte idéntico al árbol de trabajo |
| Estado del asset remoto | `pending_remote_release` |

> **Los dos valores de arriba son de aquella pasada y ya no son los vigentes.**
> El instalador cambió después, y los números se quedaron aquí escritos. Se
> conservan porque esta sección es el registro fechado de lo que se congeló ese
> día, no el estado de hoy. **El valor canónico, y el único, vive en
> `scripts/downloads_manifest.json`**, que es de donde lo leen el build, el
> `release_verify`, el `release_publish` y el bloque de un pegado. Copiarlo a
> prosa es como envejeció este.

`git ls-remote --tags origin` el 2026-08-14: el último tag publicado era
**v1.5.4**, y **v1.5.5 nunca existió**. Al 2026-08-15 el último *release
publicado* sigue siendo **v1.5.4**: el tag `v2.0.0` sí existe en el remoto, pero
es el de un intento fallido —sin GitHub Release, sin PyPI y sin MCP Registry—.
Ni el manifest ni esta matriz afirman lo contrario. Ese es exactamente el motivo
de que INSTALL-003 quede parcial: la lógica del one-paste está probada contra un
servidor HTTP local en los once escenarios, pero el asset que descargaría hoy
devuelve 404.

### Lo que sigue faltando, y por qué

Tres entradas quedan parciales por la **misma** razón, y conviene decirlo una
vez en vez de tres: **hace falta una release real de v2.0.1**.

| Entrada | Lo que falta | Gate |
|---|---|---|
| INSTALL-003 | Descargar el asset publicado y comprobar que sus bytes son los congelados | G6.4 |
| RELEASE-001 | Comparar el digest publicado con el que pasó la suite | G6.1 |
| RELEASE-004 | Ejecutar el job que crea la release y publica el asset | G6.4 |
| RELEASE-003 | Los **cuatro** ajustes del remoto que faltan, con salida de `gh api` guardada | G7.1, G7.3, G7.4, G7.5 |

Ninguna se puede cerrar leyendo código, y ninguna se va a marcar cerrada por
haber terminado el ciclo.

**Dos que sí se cerraron, y no por cansancio.** RELEASE-002 (G6.2) y la parte
CodeQL de RELEASE-003 (G7.2) se cerraron el 2026-08-15 con evidencia **del
remoto**, producida por el intento fallido de `v2.0.0` y capturada con sus
comandos de lectura en
[`audits/EVIDENCIA_REMOTA_2026-08-15.md`](audits/EVIDENCIA_REMOTA_2026-08-15.md).
Ese documento también dice qué **no** demuestran: en particular, que el tag
`v2.0.0` **es solo un tag** —no hay release, ni PyPI, ni registro— y no cierra
G6.1 ni G6.4.

### Acciones humanas necesarias

Nada de esto está autorizado en este ciclo y ninguna se ha hecho:

1. Revisar y fusionar la rama; crear el tag `v1.5.5`.
2. Publicar la release **con el asset exacto** de 21 016 bytes y ese SHA-256.
   Publicar cualquier otro contenido bajo ese nombre rompe el one-paste — que
   es justo lo que se pretende.
3. Crear los environments `pypi` y `mcp-registry` con revisores requeridos.
4. Activar los seis controles del remoto listados en
   [`SECURITY.md`](../SECURITY.md#pending-remote-controls) y guardar la salida
   de `gh api` como evidencia.
5. Después de publicar: descargar el asset, comparar hashes y cerrar la parte
   remota de INSTALL-003, RELEASE-001 y RELEASE-002.


---

## Tercera pasada — ciclo de vida de instalación (2026-08-14, en curso)

Rama `codex/installer-lifecycle-hardening`, base `513fc1d`.

| Hash | Commit | Cierra |
|---|---|---|
| `5307ab5` | `fix(installer): preserve last-known-good runtime during upgrades` | INSTALL-001, INSTALL-002 |
| `c725e19` | `fix(installer): never clean away the last usable N-1 runtime` | INSTALL-001 (hallado por ensayo) |
| `ff079a3` | `fix(installer): require an MCP handshake before declaring ready` | INSTALL-010 |
| `30597a1` | `fix(healthcheck): wait for the reply instead of racing stdin EOF` | INSTALL-010 (hallado por ensayo) |

**Arquitectura introducida.** El núcleo del ciclo de vida vive ahora en
`src/horizun_pbi_mcp/lifecycle/` —dentro del paquete, no en `scripts/`—, solo
con biblioteca estándar. `plugin_bootstrap.py` lo carga **por ruta**, no con
`import`: corre con el Python anfitrión antes de que exista el entorno aislado,
e importar el paquete arrastraría dependencias que todavía no están. Esa
ubicación es también el cimiento de INSTALL-005: si el núcleo viaja en el wheel,
la CLI empaquetada puede preparar el runtime igual que el plugin, en vez de
haber dos implementaciones.

**Rojo:** 21 de 21 en `tests/test_lifecycle_upgrade.py` contra `513fc1d`.

### El ensayo real, y lo que encontró que las pruebas no veían

Antes de tocar nada más se ejecutó el ciclo de vida completo contra un perfil
temporal (`HORIZUN_PBI_PLUGIN_DATA`), con componentes reales. Encontró **dos
defectos que ninguna prueba unitaria veía**, y ambos en el camino de éxito:

1. **La limpieza borraba el último N−1 utilizable.** La promoción conservaba
   como `.previous-` una carpeta recién creada con solo el status, y acto
   seguido `_limpiar_huerfanos` borraba `1.5.4`. Resultado: `ready` sin nada a
   lo que volver. Corregido en `c725e19`.
2. **El healthcheck tenía una carrera de EOF** que producía *falsos negativos*:
   `communicate()` cierra stdin y el servidor puede apagarse antes de contestar
   `tools/list`. Un falso negativo aquí rechaza un runtime bueno y tumba una
   instalación que iba bien — peor que el defecto original, que al menos fallaba
   hacia el lado optimista. Corregido por sincronización de evento en `30597a1`.

Corrida final, las cuatro etapas en verde:

| Etapa | Resultado |
|---|---|
| Instalación limpia | `ready` en 47,5 s · 10 DLL · 23 esquemas · validador con Node v25.8.2 · **134 tools** |
| Actualización | `ready` en 14,6 s · **134 tools** · N−1 intacto (intérprete + 10 DLL) |
| Actualización rota | `failed` · staging descartado · cero huérfanos · **N−1 sirviendo 134 tools** |

Esa última fila es la que INSTALL-001 pedía y que hasta ahora nadie había
comprobado arrancando el runtime, solo mirando si el directorio seguía en disco.

### Lo que queda de esta macro-iteración

Sin empezar, y ninguna se ha tocado: **INSTALL-004, -005, -006, -007, -008,
-009 y CLI-001**. La arquitectura de staging/promoción/rollback ya está
disponible para todas ellas, que es la dependencia que compartían.

Nota sobre **INSTALL-007**: pide retirar el reintento de winget sin
`--scope user`. Ese reintento se añadió **a propósito** en v1.5.4 (`b2d851a`
y anteriores) porque winget responde `No applicable installer found`
(0x8A150044) cuando un manifiesto ajeno no está etiquetado como *user*, aunque
su instalador por defecto sí instale en el perfil. Retirarlo sin más vuelve a
romper el camino del PC vacío que aquella versión arregló. La salida razonable
—pendiente de decisión— es dejarlo **explícito y consentido** en vez de
silencioso: user-scope por defecto, reintento solo bajo una bandera declarada,
y verificación posterior de que nada aterrizó fuera del perfil.

---

## Tercera pasada — 2026-08-14

Rama `codex/installer-lifecycle-hardening`, cinco commits sobre `9290a7d`. El
objetivo no era avanzar por la lista, sino cerrar los defectos que una revisión
independiente encontró en el bloque de ciclo de vida que la pasada anterior dio
por bueno. **Sin push, sin PR, sin tag, sin publicación.**

| Hash | Commit | Cierra |
|---|---|---|
| `01c2495` | `fix(bootstrap): make SHA verification independent of module autoload` | INSTALL-003 (evidencia) |
| `1428907` | `fix(lifecycle): contain and serialize promotion recovery` | INSTALL-011 |
| `4410bf0` | `fix(launcher): serve last-known-good runtime after failed upgrades` | INSTALL-001 |
| `fac774f` | `fix(healthcheck): enforce the packaged MCP contract before ready` | INSTALL-010 |
| `b482601` | `fix(installer): publish schemas and validator atomically` | INSTALL-006 |

### El rojo de cada uno

| Corrección | Rojo contra | Resultado |
|---|---|---|
| SHA sin autoload | `9290a7d` | 4 de 4 fallando |
| Recuperación contenida | `01c2495` | **34 de 35** fallando |
| Fallback a N−1 | `1428907` | el lanzador servía 2 tools donde ahora sirve 134 |
| Contrato en el healthcheck | `4410bf0` | **18 fallando y 2 pasando** de 20 |
| Publicación atómica | `fac774f` | 6 fallando y 4 sin llegar a cargar, de 16 |

### INSTALL-011 — el hallazgo nuevo

`promotion.recuperar()` sacaba `staging`, `destino` y `anterior` del
`.promotion.json` como **rutas absolutas** y las usaba tal cual. El journal es
un archivo dentro del directorio de datos: quien pueda escribirlo decide a qué
carpeta le hace `os.rename` un proceso que normalmente arranca solo, sin nadie
delante. Reproducido: un journal preparado a mano movió `root/.staging-demo` a
`OUTSIDE_DESTINATION`, hermana de la raíz.

Y la segunda mitad, que agrava la primera: `install()` llamaba a `recuperar()`
**antes** de adquirir el cerrojo, o sea que la operación más destructiva del
ciclo de vida era la única sin exclusión mutua.

Se registra como hallazgo propio y no como nota al pie de INSTALL-001 porque es
un defecto distinto, con su propio camino de explotación y su propio gate
(G4.9). Esconderlo dentro de una entrada ya existente habría hecho que su cierre
pareciera parte de otra cosa.

### Un oráculo que casi cuela

Merece quedar escrito porque es el mismo error de forma que persigue toda esta
auditoría. El primer intento de probar que el bloque de un pegado no depende de
`Get-FileHash` fue definir una función `Get-FileHash` que lanzaba. **No sirve.**
En cuanto el guion provoca la importación de `Microsoft.PowerShell.Utility`
—basta un `New-Object`— la del módulo vuelve a ganar y el oráculo se desactiva a
mitad de camino. Con él, las pruebas daban por **bueno el bloque viejo**, que sí
llamaba al comando. El detector definitivo es un `Set-PSBreakpoint` por nombre
de comando, que se dispara mire quien mire y sobrevive a la reimportación.

Un oráculo que puede desactivarse solo es peor que no tenerlo: no deja hueco
visible, deja un verde.

**Corrección a lo escrito en `01c2495` (añadida en la cuarta pasada).** Aquel
commit describió el `Get-FileHash` como si la comprobación de integridad
«pudiera apagarse sola» y ejecutar bytes sin verificar. **No es cierto**, y la
revisión independiente hizo bien en señalarlo. Con
`$ErrorActionPreference = 'Stop'`, un comando que no resuelve **lanza**, así que
el bloque abortaba en el `catch`. Se comprobó a posteriori: con el nombre del
comando sustituido por uno inexistente y el hash correcto, el resultado es
«abortado», nunca «ejecutado». Nunca hubo un camino por el que se ejecutara un
instalador sin verificar.

Lo que sí había es una **dependencia ambiental** en el único paso que no se
puede saltar, capaz de convertir una instalación buena en una fallida según cómo
esté la sesión de quien pega. Eso es hardening, y como hardening queda
documentado. La corrección del código se mantiene; lo que se corrige es la
afirmación, en `scripts/one_paste.ps1`, `CHANGELOG.md` y
`tests/test_one_paste.py`.

El defecto de resolución por nombre **sí** tenía consecuencia real, y estaba en
la otra mitad de la misma línea: `& powershell` resuelve alias y funciones
*antes* que el PATH, así que un nombre secuestrado habría ejecutado otro
programa con el script ya verificado como argumento. Eso se cierra en la cuarta
pasada usando la ruta absoluta de `$PSHOME`.

### Lo que NO se tocó, a propósito

**INSTALL-004, -005, -007, -008, -009 y CLI-001** siguen sin empezar. El encargo
de esta pasada era cerrar el rojo del bloque de ciclo de vida antes de seguir
avanzando por la lista.

### El baseline reportado no se reprodujo

El punto de partida decía que la suite completa fallaba en dos pruebas de
`tests/test_one_paste.py` por un `Get-FileHash` que no resolvía. **En esta
máquina no se reprodujo:** la corrida sobre `9290a7d` limpio dio *2406 passed,
5 skipped, 0 failed* en 438 s. Se midió además el mecanismo propuesto y tampoco
se sostiene: en Windows PowerShell 5.1 `Microsoft.PowerShell.Utility` está en el
estado inicial de la sesión, no se autocarga, y `Get-FileHash` resuelve incluso
con `PSModulePath` vacío o apuntando a un recurso de red muerto.

La corrección se hizo igualmente, y no por deferencia: una comprobación de
integridad que depende de que el entorno resuelva un comando es un defecto por
sí sola, reproduzca o no en esta máquina. Lo que cambia es lo que se puede
afirmar — no «se arregló el fallo reportado», sino «se eliminó una dependencia
ambiental de la única verificación del bloque, y ahora hay una prueba que lo
fija».

---

## Cuarta pasada — 2026-08-14

Misma rama, cuatro commits de código sobre `85e3098` más este de documentación.
La tercera pasada **no quedó ratificada**: la revisión independiente confirmó
los siete commits, el árbol limpio, las 137 pruebas focalizadas, `doctor` en 0 y
el contrato en 0 — y aun así encontró cinco huecos de corrección. Ninguno de los
hallazgos abiertos (INSTALL-004, -005, -007, -008, -009, CLI-001) se ha tocado.

| Hash | Commit | Cierra |
|---|---|---|
| `ff86b10` | `fix(healthcheck): validate the complete stdout stream` | INSTALL-010 (evidencia) |
| `b8daac2` | `fix(launcher): prevent mixed MCP fallback and invalidate broken runtimes` | INSTALL-012, G3.3 |
| `bf92f14` | `fix(installer): serialize component publication and bound rollback data` | INSTALL-006 (concurrencia) |
| `cbb6965` | `fix(bootstrap): make verified execution and failure reporting unambiguous` | INSTALL-003 (evidencia) |

### El rojo de cada uno

| Corrección | Rojo contra | Resultado |
|---|---|---|
| stdout hasta EOF | `85e3098` | 2 de 2 fallando |
| Sin mezcla de servidores + G3.3 | `ff86b10` | **19 de 21** fallando |
| Cerrojo y respaldo acotado | `b8daac2` | 5 de 8 fallando |
| Mensajes veraces y ruta absoluta | `bf92f14` | 3 de 3 fallando |

### INSTALL-012 — el hallazgo nuevo

El lanzador ejecutaba el runtime activo heredándole el stdio del cliente y, si
moría con código distinto de cero antes de `SEGUNDOS_DE_ARRANQUE = 20`,
arrancaba N−1 **sobre esa misma conexión**. El comentario que lo justificaba
decía «como no llegó a escribir nada por stdout, las tuberías del cliente siguen
limpias».

Eso no se medía en ninguna parte, y no se podía medir: el hijo escribe
directamente en el stdout del cliente, así que el lanzador no ve un solo byte de
lo que emite. **La duración de un proceso no dice nada sobre lo que alcanzó a
emitir.** Un runtime que contesta `initialize` y se cae a los dos segundos
dejaba al cliente con dos `serverInfo` y dos respuestas para el mismo `id` en el
mismo canal. Un cliente MCP no tiene forma de detectar eso: se queda con la
primera y sigue hablando con la segunda.

La corrección es **preflight**: el handshake se hace en un proceso aparte, con
tuberías propias, y solo se le entrega el stdio del cliente a un runtime que ya
demostró que habla MCP. Entregado el canal, no se arranca nada más sobre él. De
las dos arquitecturas admisibles se eligió esta sobre el proxy porque el proxy
añade un salto de tuberías a cada mensaje durante toda la sesión, y aquí el
coste es un arranque de servidor una vez.

Es el mismo error de forma que INSTALL-010 y TEST-001: **deducir una propiedad a
partir de una señal más estrecha de lo que aparenta.** Allí era «no lanzó
excepción, luego arranca»; aquí, «duró poco, luego no escribió».

### G3.3, que la tercera pasada no cumplía

El gate dice: *corromper el runtime tras instalar y exigir `state != ready`*. La
tercera pasada cambiaba `sirviendo` a last-known-good y dejaba `state` en
`ready`, y una prueba propia lo afirmaba —`assert status["state"] == "ready"`—.
El campo que un cliente mira para saber si esto funciona seguía diciendo que sí
sobre un runtime que ya no arranca.

Ahora `state` es el estado **operativo** y vale `degraded`; el resultado del
último intento no se pierde, se muda a `estado_instalacion`. La degradación se
descubre por dos caminos que se complementan: el estructural —falta el
intérprete o los *entry points*— se deduce al leer, y el profundo —falta el
paquete, el servidor muere a medias— lo descubre el preflight y se anota bajo el
cerrojo del ciclo de vida.

### Dos pasadas, dos hallazgos introducidos por la anterior

INSTALL-011 lo trajo la remediación de INSTALL-001; INSTALL-012, la de
INSTALL-011. No es mala suerte: es lo que pasa cuando se sustituye un mecanismo
por otro más complejo, y es el argumento más fuerte a favor de que cada pasada
la revise alguien que no la escribió.
