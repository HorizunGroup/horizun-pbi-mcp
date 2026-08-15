# Gates externos — lo que no se puede cerrar en esta máquina

Un gate llega aquí cuando **ninguna cantidad de trabajo local lo cierra**: le
falta una VM limpia, una release publicada, permisos del remoto de GitHub, Power
BI Desktop con un modelo real o una autorización que este ciclo no tiene.

Estar en esta lista **no es un aprobado**. La regla 4 de
[`ACCEPTANCE_10_OF_10.md`](ACCEPTANCE_10_OF_10.md) dice que «pendiente de
evidencia» es un estado legítimo y **bloqueante**: mientras un gate esté aquí, el
hallazgo que cierra no puede declararse cerrado.

Cada entrada trae lo necesario para que otra persona lo cierre sin reconstruir el
contexto: qué bloquea exactamente, qué hace falta, cómo se reproduce, qué
resultado se espera y qué evidencia hay que guardar.

Última revisión: **2026-08-15**.

> **G3.6 salió de esta lista el 2026-08-15.** Estaba anotado como «candidato a
> trabajo local» y resultó serlo: el gate no pedía una VM, pedía una
> *instalación pip pura*, y eso se monta aquí —artefacto construido, venv
> limpio, fuera del checkout y con la caché de esquemas del usuario aislada—.
> Conviene recordarlo antes de aparcar cualquier otro: la etiqueta «externo» se
> pega con facilidad.

---

## Resumen

| Gate | Hallazgo | Bloqueo | Autorización / infraestructura |
|---|---|---|---|
| G3.1 | INSTALL-010 | VM limpia | Windows sin Python/Node/Claude |
| G3.2 | CLI-001 | VM limpia | La misma VM, ruta Codex |
| G3.3 | INSTALL-010 | VM limpia (**el comportamiento local ya cumple el gate**) | La misma VM |
| G3.4 | INSTALL-002 | VM con Node 18 | Windows + Node 18 en el PATH |
| G3.5 | INSTALL-004 | Claude CLI real | Instalación de Claude que se pueda deshabilitar |
| G4.1 | INSTALL-001 | VM limpia (mecanismo ya demostrado) | La misma VM |
| G4.3 | INSTALL-006 | `npm` real + red | Node ≥20 y salida a registry.npmjs.org |
| G4.7 | INSTALL-009 | VM sin salida directa | VM + proxy o red cortada |
| G5.1–G5.6 | TEST-003 | Power BI Desktop real | Desktop con un `.pbip` de prueba |
| G6.1 | RELEASE-001 | Release publicada | Permiso de publicación |
| G6.2 | RELEASE-002 | Release publicada | Permiso de publicación |
| G6.4 | INSTALL-003 | Asset de v1.5.5 | Permiso de publicación |
| G7.1–G7.5 | RELEASE-003 | Configuración del remoto | Admin del repositorio GitHub |

**13 gates externos.** Los cuatro de release y los cinco del remoto comparten
una sola dependencia: que exista una release real de v1.5.5, que **no existe**, y
que este ciclo tiene prohibido crear.

---

## G3.1 · G3.2 · G3.3 · G3.4 — instalación limpia

**Bloqueo exacto.** Todo el bloque G3 mide *qué le pasa a alguien que instala
desde cero*. Esta máquina tiene Python, Node 25, Claude y una instalación previa
del plugin, así que cualquier medición aquí responde a otra pregunta.

**Infraestructura.** Una VM Windows 11 recién creada, sin Python, sin Node y sin
Claude. Para G3.4, la misma VM con Node 18 en el PATH.

**Procedimiento.**

1. Snapshot de la VM limpia (para repetir los cuatro gates desde el mismo punto).
2. G3.1: pegar el bloque de un pegado de `README.md`. Esperar a `ready`. Hablar
   MCP por stdio: `initialize` + `tools/list`. Contar tools.
3. G3.2: restaurar el snapshot, instalar **Codex sin Claude** y repetir la
   verificación equivalente.
4. G3.3: sobre la instalación de G3.1, corromper el runtime promovido de las
   cuatro formas que ya cubre `tests/test_stdout_sin_mezclar.py` —borrar el
   intérprete, borrar un *entry point*, borrar el paquete de `site-packages`, y
   dejar un servidor que muere tras `initialize`— y leer `pbi_install_status`.
5. G3.4: restaurar el snapshot, instalar Node 18, repetir G3.1.

**Resultado esperado.** G3.1 y G3.2: `ready` y **134 tools**. G3.3: `state` vale
`degraded` —nunca `ready`—, `sirviendo` apunta a N−1 y `degradacion.motivo` está
relleno. G3.4: `state=ready` y `validator` en `skipped_node_too_old` con motivo.

**Evidencia.** Salida cruda del `tools/list` con el recuento, el
`install-status.json` completo de cada paso, y la versión de Windows y de Node.

> **G3.3 merece una nota.** El comportamiento local **ya cumple el gate
> literal**: tras cualquiera de las cuatro corrupciones, `state` deja de ser
> `ready`. Lo que falta es repetirlo sobre una instalación real en vez de sobre
> runtimes de prueba. Es el único gate de esta lista que está aquí por el
> *entorno* y no por el *comportamiento*.

---

## G3.5 — el instalador no declara éxito con el plugin deshabilitado

**Bloqueo exacto.** Hace falta una instalación real de Claude CLI sobre la que se
pueda deshabilitar el plugin, y este ciclo tiene prohibido modificar
instalaciones reales de Claude.

**Procedimiento.** En la VM de G3.1, tras una instalación buena:
`claude plugin disable horizun-pbi-mcp`, y volver a ejecutar la verificación
final del instalador.

**Resultado esperado.** El instalador **falla**. Hoy hace una coincidencia de
subcadena sobre la salida de `claude plugin list`, que sigue conteniendo el
nombre del plugin cuando está deshabilitado.

**Evidencia.** Salida de `claude plugin list` en los dos estados y el código de
salida del instalador en cada uno.

---

## G4.1 — una actualización interrumpida deja N−1 funcionando

**Bloqueo exacto.** El mecanismo está demostrado de punta a punta: el lanzador
real, hablando MCP por stdio, entrega las 134 tools de la versión anterior con
fallo inyectado en pip, DLL, esquemas, handshake y promoción. Lo que falta es que
el runtime servido sea una instalación **real** —1 GB con pythonnet, las DLL de
Analysis Services y los esquemas— y no un venv de prueba.

**Procedimiento.** En la VM, instalar 1.5.4 completa, cortar la red a mitad de la
actualización a 1.5.5, y hablar MCP con lo que quede sirviendo.

**Resultado esperado.** 134 tools de 1.5.4, `state` distinto de `ready`, y el
error de la actualización visible.

---

## G4.3 — el validador npm se publica de forma atómica

**Bloqueo exacto.** Las pruebas locales simulan `npm`: se comprueba que
`--prefix` apunta al staging y nunca al destino vivo, y que dos procesos no se
pisan, pero ningún `npm install` real llega a ejecutarse.

**Infraestructura.** Node ≥20 y salida a `registry.npmjs.org`.

**Procedimiento.** Instalar el validador sobre un destino que ya tenga una
versión, matar el proceso a mitad del `npm install`, y comparar el destino con su
estado anterior byte a byte.

**Resultado esperado.** Destino idéntico al de antes; ningún `.staging-` ni
journal huérfano; la siguiente instalación termina limpia.

---

## G4.7 — bundle offline y runbook de proxy

**Bloqueo exacto.** No existe ni el bundle ni el runbook, y comprobarlos exige
una VM sin salida directa a internet. La parte *construir el bundle* sí es
trabajo local y queda pendiente bajo INSTALL-009.

---

## G5.1–G5.6 — Desktop real

**Bloqueo exacto.** Los seis gates miden filtros, capturas, `data_loaded` y
rollback contra Power BI Desktop con un modelo cargado. Este ciclo tiene
prohibido escribir proyectos reales, así que hace falta un `.pbip` de prueba
creado para esto.

**Procedimiento.** Abrir el `.pbip` sintético en Desktop y ejecutar
`python -m pytest -m live`.

**Evidencia.** Las capturas PNG producidas, los hashes antes/después del rollback
y la salida de `pbi_session_info`.

---

## G6.1 · G6.2 · G6.4 — la release que no existe

**Bloqueo exacto.** Los tres necesitan una release publicada de v1.5.5.
`scripts/downloads_manifest.json` declara `status: pending_remote_release`
precisamente para no fingir lo contrario, y una prueba lo vigila.

**Autorización.** Permiso explícito para publicar, que este ciclo **no tiene**.

**Procedimiento.** Publicar la release; descargar el asset publicado; comparar su
SHA-256 con el congelado en el manifiesto y en el bloque de un pegado (G6.4);
comparar el digest publicado en PyPI con el que pasó la suite (G6.1); empujar un
tag con la suite en rojo y comprobar que no publica (G6.2).

**Evidencia.** Los digests de las dos partes, la URL del asset y el run de CI que
no publicó.

---

## G7.1–G7.5 — controles del remoto

**Bloqueo exacto.** Son ajustes de configuración del repositorio en GitHub:
protección de `main`, CodeQL, Dependabot, *secret scanning* y *private
vulnerability reporting*. Este ciclo tiene prohibido cambiar configuración remota.

**Autorización.** Permisos de administración sobre el repositorio.

**Procedimiento.** Aplicarlos y capturar la salida de `gh api` de cada uno.

**Evidencia.** La respuesta JSON de `gh api` por gate, fechada.

> G7.6 —actions pineadas por SHA— **ya está cumplido** y no es externo: se
> decide leyendo los workflows, y `tests/test_repo_security.py` lo vigila.
