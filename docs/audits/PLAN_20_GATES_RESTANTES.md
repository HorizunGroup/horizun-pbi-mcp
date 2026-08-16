# Los 18 gates que faltan, y qué hace falta exactamente para cada uno

> **El nombre del archivo dice 20 y es histórico.** Se conserva para no romper
> enlaces; el número que vale es el de la partición, y hoy son **18**. Si vuelve
> a moverse, se mueve aquí y en
> [`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md), que es de donde sale.

Ninguno pide más trabajo en esta máquina. Los 18 esperan **un entorno** o **una
autorización**, y este documento existe para que quien los tenga no tenga que
reconstruir el contexto: preparación, comando, la mutación que hay que inyectar
para que el verde signifique algo, el resultado esperado, qué evidencia guardar,
cómo limpiar después, el riesgo y quién tiene que autorizarlo.

Reparto: **5 parciales** —el mecanismo ya está demostrado aquí— y **13 externos
puros**. La partición está en
[`CLASIFICACION_GATES.md`](CLASIFICACION_GATES.md).

Antes de leer nada de esto conviene saber que **cinco de los que estaban en
esta lista se cayeron al comprobar qué había de verdad**. El 2026-08-15, por
inventario de la máquina: G4.3 solo necesitaba Node ≥20; G4.6 se cerró generando
y probando los cinco locks con sus intérpretes reales, sin simular marcadores
con `pip --python-version`. Y el mismo día, por lectura del remoto
tras el intento fallido de `v2.0.0`: **G6.2 y G7.2**, que estaban archivados por
arrastre de bloque y ya tenían su evidencia en el remoto sin que nadie hubiera
ido a mirarla (ver
[`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md)).

Son el tercer, cuarto, quinto y sexto caso después de G3.6, G4.7 y G5.5. **Antes
de aceptar cualquiera de los 18 de abajo, repite la comprobación**: la etiqueta
«externo» se pega a bloques enteros y sobrevive a que el bloqueo desaparezca.

---

## Grupo 1 · VM Windows limpia — G3.1, G3.2, G3.4, G4.1, G3.3

| | |
|---|---|
| Entorno | VM Windows 11 recién creada: **sin** Python, **sin** Node, **sin** Claude |
| Autorización | ninguna especial; es infraestructura, no permisos |
| Riesgo | ninguno fuera de la VM |

**Preparación.** Snapshot de la VM limpia antes de nada: los cinco gates parten
del mismo punto y sin snapshot hay que recrearla cuatro veces.

**G3.1 — instalación desde cero.**

1. Pegar el bloque de un pegado del README.
2. Esperar a `ready`.
3. `initialize` + `tools/list` por stdio.

*Esperado:* `ready` y **134 tools**. *Evidencia:* salida cruda del `tools/list`
con el recuento y el `install-status.json` completo.

**G3.2 — la ruta Codex.** Restaurar el snapshot, instalar **Codex sin Claude**,
repetir. *Esperado:* lo mismo. Es el gate que CLI-001 dejó parcial: el one-paste
instala y verifica solo la ruta Claude.

**G3.4 — Node 18.** Restaurar, instalar Node 18, repetir G3.1. *Esperado:*
`state=ready` **y** `validator` en `skipped_node_too_old` con motivo: el opcional
que falla no puede tumbar la instalación.

**G3.3 — `ready` implica handshake.** Sobre la instalación de G3.1, corromper el
runtime de las cuatro formas que ya cubre `tests/test_stdout_sin_mezclar.py`
—borrar el intérprete, borrar un *entry point*, borrar el paquete de
`site-packages`, dejar un servidor que muere tras `initialize`— y leer
`pbi_install_status`. *Esperado:* `state` vale `degraded`, **nunca** `ready`;
`sirviendo` apunta a N−1 y `degradacion.motivo` está relleno.

> G3.3 está **parcial**: el comportamiento local ya cumple el gate literal. Lo
> que falta es repetirlo sobre una instalación real en vez de sobre runtimes de
> prueba.

**G4.1 — actualización interrumpida.** Instalar 1.5.4 completa, cortar la red a
mitad de la actualización a 2.0.0, y hablar MCP con lo que quede sirviendo.
*Esperado:* **134 tools de 1.5.4**, `state` distinto de `ready`, y el error
visible. *Mutación que da sentido al verde:* sin cortar la red, el gate pasa
trivialmente; el corte es el gate.

**Limpieza.** Descartar la VM. No hay nada que devolver.

---

## Grupo 2 · Power BI Desktop — G5.1, G5.2, G5.3, G5.4, G5.6

| | |
|---|---|
| Entorno | **esta misma máquina, con Power BI Desktop CERRADO** |
| Autorización | ninguna: el fixture es sintético y desechable |
| Riesgo | **medio** — ver abajo |

**No hace falta una VM.** Las pruebas existen, el fixture se crea en un temporal
y se tira. Lo que hace falta es que **no haya ningún Power BI abierto**: las
cuatro pruebas `live` se niegan por diseño si lo hay —*«no toca ninguna ventana
que no haya abierto ella»*— y el 2026-08-15 se omitieron por eso, con la
instancia del usuario (PBIDesktop 52900) intacta antes y después.

**Preparación.**

1. Cerrar Power BI Desktop **a mano**, guardando lo que haya que guardar.
2. Comprobar que no queda ninguno:
   ```bash
   powershell -c "Get-Process PBIDesktop,msmdsrv -ErrorAction SilentlyContinue"
   ```
3. Anotar los PID vivos (debe ser vacío).

**Comando, uno por vez.** No en lote: si uno deja un proceso, el siguiente
miente.

```bash
python -m pytest tests/test_project_state.py::test_live_la_ventana_real_delata_un_pbip_sin_handles -m live -q -s
```

```bash
python -m pytest tests/test_capture_atomicity.py::test_live_captura_real_deja_el_proyecto_byte_a_byte_igual -m live -q -s
```

```bash
python -m pytest tests/test_dax_runner.py::test_run_dax_live -m live -q -s
```

```bash
python -m pytest tests/test_pbix_convert.py::test_export_tmdl_live -m live -q -s
```

**Después de cada una**, y antes de la siguiente:

- hashes del proyecto sintético comparados byte a byte;
- `Get-Process PBIDesktop,msmdsrv` — **cero procesos nuevos**;
- cero journals y cero temporales en el data root;
- el PNG producido, la página capturada y `data_loaded` en la salida.

**Riesgo.** Las pruebas abren Power BI Desktop de verdad. Cierran solo lo que
abrieron, comprobado por PID y hora de arranque, pero si una se interrumpe a
mitad puede quedar una ventana: por eso se ejecutan de una en una y se comprueba
el recuento entre medias.

**Lo que NO se puede hacer.** Abrir, escribir, guardar o refrescar un proyecto
real; usar los `outputs/` y `backups/` reales; dejar Desktop o `msmdsrv`
abiertos.

> G5.6 está **parcial** y no externo: su prueba existe, es local y ya se ejecutó
> para CORE-002. Lo que falta es repetirla dentro de la matriz de escenarios de
> TEST-003.

---

## Grupo 3 · VM sin salida a internet — G4.7

| | |
|---|---|
| Entorno | VM Windows sin ruta a internet, o detrás de un proxy corporativo |
| Autorización | ninguna |
| Riesgo | ninguno |

**El bundle ya existe** y se prueba aquí: `scripts/bundle.py` construye,
verifica sin extraer e instala, con `socket` y `subprocess` prohibidos durante
toda la instalación, y con un bundle real instalado por `pip --no-index` que
entrega las 134 tools. Lo que **no** demuestra prohibir `socket` es qué hace
Windows con un proxy mal configurado.

**Procedimiento.**

1. En una máquina con red: `python scripts/bundle.py construir --salida <dir>`.
2. Llevar el ZIP a la VM sin salida.
3. `python scripts/bundle.py verificar <zip>` → íntegro.
4. `python scripts/bundle.py instalar <zip> --destino <data root>`.
5. Hablar MCP por stdio con lo instalado.

*Esperado:* `ready`, 134 tools, y **ni un intento de conexión saliente** en el
registro del proxy. *Mutación:* cambiar un byte del ZIP antes del paso 3 tiene
que abortar **antes de escribir nada**.

---

## Grupo 4 · Release publicada — G6.1, G6.4

| | |
|---|---|
| Entorno | una release real de **v2.0.1** en el remoto |
| Autorización | **permiso explícito de publicación**, que este ciclo no tiene |
| Prerrequisito | el *trusted publisher* de PyPI, configurado a mano: es lo que falló en `v2.0.0` con `invalid-publisher`. Procedimiento en [`PYPI_TRUSTED_PUBLISHER.md`](PYPI_TRUSTED_PUBLISHER.md) |
| Riesgo | alto: publicar es irreversible en la práctica |

Los dos dependen de lo mismo. `scripts/downloads_manifest.json` declara
`pending_remote_release` precisamente para no fingir lo contrario, y hay una
prueba que lo vigila.

**G6.1 — se publica el artefacto probado, sin reconstruir.** Comparar el SHA-256
del wheel y del sdist publicados con los de `SHA256SUMS` del build que pasó CI.
*Esperado:* idénticos.

**G6.4 — el script remoto se verifica antes de ejecutarse.** Descargar el asset
`horizun-pbi-mcp-instalar.ps1` de la release y comprobar que su SHA-256 coincide
con el del manifiesto —el valor canónico está en
`scripts/downloads_manifest.json` y **no se copia aquí**, porque una segunda
copia en prosa envejece en silencio—. *Esperado:* coincide, y si se publica
cualquier otro contenido bajo ese nombre, el one-paste **no ejecuta nada**.

> G6.4 está **parcial**: la lógica está probada contra un servidor local en once
> escenarios de fallo. Lo único que falta es el asset real. Desde v2.0.1 ya
> existe **quién lo publica** —el job `publicar-github-release`, que además
> relee el asset y compara digest y URL contra el manifest—; antes no lo creaba
> nadie, que es RELEASE-004.

> **G6.2 salió de este grupo el 2026-08-15**, sin publicar nada: lo que pedía se
> observó en el run 31914746886, donde `publicar-mcp` quedó omitido porque
> `publicar-pypi` falló. Ver
> [`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md), que
> también acota qué **no** demuestra.

---

## Grupo 5 · Configuración del remoto de GitHub — G7.1, G7.3, G7.4, G7.5

| | |
|---|---|
| Entorno | el repositorio en GitHub |
| Autorización | **admin del repositorio** |
| Riesgo | bajo, pero son cambios de configuración de la organización |

| Gate | Qué hay que activar | Estado leído el 2026-08-15 | Cómo se comprueba |
|---|---|---|---|
| G7.1 | `main` protegida, sin push directo | **sin protección** | intentar un push directo: rechazado |
| G7.3 | Dependabot *security updates* | **deshabilitado** | la pestaña Security lo muestra activo |
| G7.4 | *Secret scanning* y *push protection* | **deshabilitados** | empujar un secreto de prueba: bloqueado |
| G7.5 | *Private vulnerability reporting* | **no habilitado** | la opción activa en Security |

Los comandos `gh api` de los cuatro están **escritos y sin ejecutar** en
[`../PLAN_SEGURIDAD_GITHUB.md`](../PLAN_SEGURIDAD_GITHUB.md), cada uno con su
verificación y su *rollback*. Los nombres de los checks obligatorios están
**leídos de los check-runs reales de `1f0405b`**, no inventados: un nombre
inventado en `required_status_checks` bloquea `main` para siempre esperando un
check que nadie va a publicar.

El repositorio ya trae lo que sí es local: los workflows de CodeQL y Dependabot
están escritos y las Actions pineadas por SHA (G7.6, cumplido). Lo que falta es
**encenderlo en el remoto**, y eso no se puede hacer desde aquí ni se debe hacer
sin permiso.

> **G7.2 salió de este grupo el 2026-08-15**: CodeQL llevaba en verde desde el
> push de `1f0405b` —run 31913970370, check-run `Analizar (python)`— y nadie
> había ido a mirarlo. El gate pedía una ejecución verde en Actions, no un
> ajuste de configuración; estaba en el grupo equivocado.

---

## Grupo 6 · Claude CLI aislable — G3.5

| | |
|---|---|
| Entorno | una instalación de Claude que se pueda deshabilitar sin tocar la del usuario |
| Autorización | ninguna, pero **hace falta aislamiento verificable** |
| Riesgo | alto si se hace mal: se tocaría la configuración real |

**Comprobado el 2026-08-15: no se puede aislar con lo documentado.** `claude
--help` no ofrece ninguna variable ni opción para apuntar configuración y datos a
un directorio temporal; `claude plugin disable --scope user|project|local` elige
**dónde** dentro de la instalación real, no **otra** instalación; y
`--plugin-dir` carga un plugin solo para una sesión, que no es lo que el gate
mide.

**Procedimiento, si algún día hay aislamiento.** Instalar y registrar el plugin
dentro del perfil temporal, comprobar `enabled`, deshabilitarlo, ejecutar la
verificación final del instalador y exigir **salida no cero**. Hoy el instalador
hace una coincidencia de subcadena sobre `claude plugin list`, que sigue
conteniendo el nombre cuando está deshabilitado: **ese es el defecto que el gate
persigue**.

*Limpieza:* borrar únicamente el perfil temporal.

---

## Infraestructura disponible en esta máquina, comprobada en solo lectura

| | Estado |
|---|---|
| Node | **25.8.2** — sirvió para cerrar G4.3 |
| npm | 11.11.1 |
| Claude CLI | 2.1.220, **sin aislamiento documentado** |
| Docker | 29.4.2 |
| WSL | presente (`docker-desktop`) |
| Power BI Desktop | instalado, **y abierto por el usuario** |
| Python | 3.14.3 |

**No se habilitó nada.** No se creó ninguna VM, no se tocó Hyper-V ni Windows
Sandbox, y no se pidieron privilegios de administrador.

Docker y WSL están disponibles y **podrían** servir para el grupo 1 —una imagen
Windows para la VM limpia—, pero Docker Desktop en esta máquina corre con backend
Linux: una imagen `windows/servercore` necesitaría cambiar el backend, que es un
cambio de configuración del sistema y queda fuera de lo autorizado.
