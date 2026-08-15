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

> **Esta lista no es el conteo.** Quién está aquí y en qué categoría cae lo
> decide [`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md), que es la única
> partición de los 54. Durante un tiempo esta tabla se leyó como «22 gates
> externos» y se comparó con los conteos de aceptación, que cuentan otra cosa:
> aquí conviven gates **parciales** —con el mecanismo ya demostrado— con gates
> que no tienen nada hecho, y faltan los que esperan una ratificación o los que
> tienen trabajo local. Una prueba exige que las dos cuentas no se contradigan.

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
| G4.6 | INSTALL-009 | intérpretes 3.10, 3.11, 3.12 y 3.13 (**un lock se genera en su propio intérprete**) | esas cuatro versiones de Python en Windows |
| G4.7 | INSTALL-009 | VM sin salida real (**el bundle ya existe y se prueba aquí**) | VM desconectada o proxy corporativo |
| G5.1–G5.4 | TEST-003 | Power BI Desktop real | Desktop con un `.pbip` de prueba |
| G5.6 | TEST-003 | Desktop (**la prueba ya existe y es local**) | Desktop con un `.pbip` de prueba |
| G6.1 | RELEASE-001 | Release publicada | Permiso de publicación |
| G6.2 | RELEASE-002 | Release publicada | Permiso de publicación |
| G6.4 | INSTALL-003 | Asset de v1.5.5 | Permiso de publicación |
| G7.1–G7.5 | RELEASE-003 | Configuración del remoto | Admin del repositorio GitHub |

**14 filas, 21 gates** —dos filas agrupan un rango entero, `G5.1–G5.6` y
`G7.1–G7.5`, porque los seis y los cinco comparten el mismo bloqueo. Contar filas
y decir «14 externos» dejaría fuera siete gates que nadie estaría vigilando.

> **G4.7 salió de esta tabla el 2026-08-15**, y es el segundo caso después de
> G3.6. Su propia ficha decía «la parte *construir el bundle* sí es trabajo local
> y queda pendiente» **dentro** del documento cuyo encabezado promete lo
> contrario. Mientras el bundle no exista, G4.7 es `pendiente-local` en la
> partición y su procedimiento se conserva abajo para cuando vuelva: lo que
> quedará entonces —ejecutarlo en una VM realmente desconectada— sí es externo.

Los cuatro de release y los cinco del remoto comparten
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

> **Comprobado el 2026-08-15 si se podía aislar, y no se puede.** `claude
> --help` no documenta ninguna variable ni opción para apuntar la configuración
> y los datos a un directorio temporal; `claude plugin disable` acepta
> `--scope user|project|local`, que elige **dónde** dentro de la instalación
> real, no **otra** instalación. Registrar el plugin para deshabilitarlo
> después tocaría el marketplace y la caché del usuario, y eso está prohibido.
> `--plugin-dir` carga un plugin solo para una sesión, que no es lo que este
> gate mide. Sigue externo, y ahora se sabe por qué y no por suposición.

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

## G4.6 — reproducibilidad fuera de Windows

**Bloqueo exacto.** La matriz `win_amd64 × {3.10, 3.13, 3.14}` está fijada por
versión y SHA-256, y CI la instala de verdad en 3.10 y 3.13. Lo que falta es una
plataforma que no sea Windows: ahí no hay lock, el instalador cae al resolutor
—y lo declara— y esa instalación no es reproducible.

**Infraestructura.** Un runner Linux o macOS con Python ≥3.10.

**Procedimiento.** Añadir la plataforma a `MATRIZ` en `scripts/generar_lock.py`,
regenerar, y correr la suite completa allí: `test_dos_instalaciones_reales_...`
instala dos veces desde el lock y compara `pip freeze`.

**Resultado esperado.** Los dos `pip freeze` idénticos entre sí e iguales a lo
fijado, y `dependencias.source == "lock"` en el estado de instalación.

---

## G4.6 — un lock por intérprete, y faltan cuatro

**Se dio por cumplido el 2026-08-15 y CI lo desmintió el mismo día.** La matriz
se generaba desde un solo intérprete con `pip --python-version`, y eso **no
produce un lock fiel**: pip cambia las etiquetas de rueda compatibles pero
evalúa los **marcadores de entorno** contra el intérprete que corre. Los locks
de 3.10–3.13 salían sin `exceptiongroup` —que `anyio` solo pide en
`python_version < "3.11"`— y `--require-hashes` se negaba a instalarlos.

**Bloqueo exacto.** Hace falta ejecutar `python scripts/generar_lock.py` **con
cada intérprete**: 3.10, 3.11, 3.12 y 3.13, en Windows. No es una VM ni un
permiso: son cuatro instalaciones de Python.

**Resultado esperado.** Cuatro locks más, cada uno con su cabecera declarando su
versión, y `test_dos_instalaciones_reales_dan_exactamente_las_mismas_versiones`
en verde en cada uno.

**Mutación que da sentido al verde.** Pedirle al generador un lock de otra
versión: tiene que **negarse**, no producirlo.

---

## G4.7 — bundle offline y runbook de proxy

**Ya no está aquí por lo que faltaba antes.** El bundle **existe**:
`scripts/bundle.py` lo construye, lo verifica sin extraer y lo instala con la
promoción del ciclo de vida compartido. Se prueba con pip real y `--no-index`
—134 tools desde el wheelhouse— y con `socket` y `subprocess` prohibidos durante
toda la instalación. El runbook documenta los tres comandos.

**Bloqueo exacto, hoy.** Prohibir `socket` demuestra que el código no sale a la
red; no demuestra qué hace **Windows** con un proxy corporativo mal configurado
ni qué pasa en una VM realmente desconectada.

**Procedimiento.** Construir el bundle en una máquina con red, llevarlo a la VM
sin salida, `verificar` y `instalar`, y hablar MCP por stdio con lo instalado.

**Resultado esperado.** `ready`, 134 tools, y ni un intento de conexión saliente
en el registro del proxy.

**Ojo con el hermano.** G4.6 —el lock con hashes— compartía hallazgo con este y
**no** era externo: se cerró el 2026-08-15 instalando el lock en dos venv limpios y
comparando `pip freeze`. Que dos gates citen el mismo hallazgo no los hace igual
de inalcanzables; conviene mirarlos por separado antes de aparcar los dos.

---

## G5.1–G5.4 y G5.6 — Desktop real

> **Intentado el 2026-08-15, y las cuatro pruebas live se omitieron solas.**
> Había una instancia de Power BI Desktop del usuario abierta —`PBIDesktop`
> 52900 y `msmdsrv` 11580— y las pruebas se niegan por diseño: *«no toca
> ninguna ventana que no haya abierto ella»*. Es la decisión correcta y no se
> forzó: cerrar el Desktop de alguien para cerrar un gate sería cambiar
> evidencia por daño.
>
> Se comprobó después que **no se creó ni se destruyó ningún proceso**: los dos
> PID de antes son los dos PID de después.
>
> Para cosecharlos hace falta la máquina **sin Power BI abierto**. No hace falta
> una VM: el fixture es sintético y desechable, y las pruebas ya existen.

> **G5.5 salió de esta lista el 2026-08-15**, y es el tercer caso después de
> G3.6 y G4.7. Se había clasificado el bloque G5 entero como imposible sin mirar
> gate por gate. G5.5 —«un `.pbip` abierto se detecta como abierto»— **ya tenía
> evidencia live fechada el 2026-08-14**: es el mismo hallazgo que G1.1, CORE-001,
> y su prueba `test_live_la_ventana_real_delata_un_pbip_sin_handles` corre sobre
> un proyecto sintético desechable y solo cierra los procesos que ella misma
> arrancó. Estaba cumplido y contado como externo.
>
> **G5.6 pasó a parcial** por lo mismo a medias: la prueba
> `test_live_captura_real_deja_el_proyecto_byte_a_byte_igual` existe, es local y
> se ejecutó para CORE-002. Lo que falta no es escribirla, es repetirla dentro
> de la matriz de escenarios de TEST-003.
>
> La lección se repite: la etiqueta «externo» se pega a bloques enteros, y
> dentro de un bloque puede haber gates que ya están hechos.

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
