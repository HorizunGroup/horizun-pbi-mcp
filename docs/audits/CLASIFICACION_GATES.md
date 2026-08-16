# Clasificación disjunta de los 54 gates

Los tres documentos de gates contaban cosas distintas y ninguno lo decía.
[`ACCEPTANCE_10_OF_10.md`](ACCEPTANCE_10_OF_10.md) declaraba **30 cumplidos, 5
parciales, 19 pendientes**; [`EXTERNAL_GATES.md`](EXTERNAL_GATES.md) declaraba
**22 externos**. No son cifras comparables: los 22 incluían cuatro de los
parciales y dejaban fuera a G1.5 —que espera una ratificación humana— y a G2.2
—que tiene trabajo local pendiente—. Con esas dos cuentas se podía decir «no
queda trabajo local» sin que ningún documento lo desmintiera.

Este archivo es la **única partición**: cada uno de los 54 gates aparece aquí
**exactamente una vez**, en una de cinco categorías, con el motivo. El resto de
documentos se derivan de él, y `tests/test_clasificacion_gates.py` falla si
alguno deja de cuadrar: si sobra un gate, si falta, si aparece dos veces, si la
suma no da 54, si un cumplido sigue listado como externo o si un «externo puro»
confiesa trabajo local en su propia ficha.

> **Los 20 que faltan tienen su plan, uno por uno**, en
> [`PLAN_20_GATES_RESTANTES.md`](PLAN_20_GATES_RESTANTES.md): entorno exacto,
> preparación, comando, la mutación que hay que inyectar para que el verde
> signifique algo, resultado esperado, evidencia, limpieza, riesgo y quién tiene
> que autorizarlo.

## Las cinco categorías, y por qué son estas

| Categoría | Qué significa | Quién lo desbloquea |
|---|---|---|
| `cumplido` | Verificado, con evidencia fechada y regresión | nadie: está hecho |
| `parcial` | El mecanismo está probado; falta evidencia de un entorno que aquí no existe | quien tenga ese entorno |
| `pendiente-local` | **Hay trabajo que se puede hacer en esta máquina** y no está hecho | quien siga trabajando |
| `pendiente-ratificacion` | El trabajo está identificado pero exige una decisión humana | la persona responsable |
| `pendiente-externo` | Ninguna cantidad de trabajo local lo cierra | una VM, una release, Desktop o el remoto |

La diferencia entre `parcial` y `pendiente-externo` es de grado, no de tipo: en
los dos falta un entorno. Se separan porque un `parcial` **ya tiene el mecanismo
demostrado aquí** y un `pendiente-externo` no tiene nada. Confundirlos fue parte
del problema: cuatro parciales estaban contados como externos.

`pendiente-local` es la categoría que obliga a la honestidad. Mientras tenga un
solo gate, **no se puede decir «100% local»**.

## La partición

| Gate | Categoría | Motivo |
|---|---|---|
| G1.1 | cumplido | evidencia live fechada 2026-08-14 |
| G1.2 | cumplido | `tests/test_capture_atomicity.py`, 13 pruebas |
| G1.3 | cumplido | `tests/test_capture_atomicity.py`: la captura es transaccional |
| G1.4 | cumplido | `tests/test_core_seguridad_operativa.py` |
| G1.5 | cumplido | CONTRACT-003 **ratificado el 2026-08-15** y aplicado en 2.0.0: `confirm` exigido en las dos de refresh, default de `pbi_apply_plan` a `false`, y las dos de sesión reclasificadas a `session_write` con `idempotentHint` comprobado. 21 regresiones |
| G1.6 | cumplido | el validador escribe en `tempfile.gettempdir()`, no en el proyecto |
| G1.7 | cumplido | redacción verificada sobre el formateador real |
| G1.8 | cumplido | cerrojo de proyecto, dos procesos de verdad |
| G2.1 | cumplido | `python -m tests.contract_utils` sale 0 |
| G2.2 | cumplido | **134 de 134** con payload congelado: 44 de éxito y 90 de error de dominio, 174 muestras capturadas por `call_tool`. Cero exclusiones sin dependencia medida (ver `docs/COBERTURA_PAYLOADS.md`) |
| G2.3 | cumplido | `docs/INVENTARIO_TOOLS.md` generado; 134/134 ejecutadas por `call_tool` |
| G2.4 | cumplido | 134 casos negativos, cero excepciones declaradas |
| G2.5 | cumplido | CONTRACT-001 ratificada; CONTRACT-003 repite el mecanismo |
| G3.1 | pendiente-externo | VM Windows limpia sin Python, Node ni Claude |
| G3.2 | pendiente-externo | la misma VM, ruta Codex sin Claude |
| G3.3 | parcial | el comportamiento local ya cumple el gate literal; falta repetirlo sobre una instalación real |
| G3.4 | pendiente-externo | VM con Node 18 en el PATH |
| G3.5 | pendiente-externo | instalación real de Claude que se pueda deshabilitar |
| G3.6 | cumplido | instalación pip pura de wheel y sdist, fuera del checkout |
| G4.1 | parcial | el lanzador real sirve N−1 con fallo inyectado; el runtime servido es de prueba |
| G4.2 | cumplido | publicación atómica de esquemas por el ciclo de vida compartido |
| G4.3 | cumplido | **`npm` real**: instalación de verdad, corte a mitad con el proceso matado, destino anterior byte a byte intacto, cero huérfanos y reintento limpio. Solo en un data root temporal |
| G4.4 | cumplido | `uninstall` con CLI real sobre un data root de prueba |
| G4.5 | cumplido | `purge` enumera y pesa antes de borrar |
| G4.6 | parcial | **cerrado antes de tiempo y reabierto por CI**: la matriz se generaba con `pip --python-version`, que evalúa los marcadores contra el intérprete que corre, así que los locks de 3.10–3.13 omitían dependencias condicionales y **no instalaban**. Hoy hay un lock fiel, el de 3.14, generado en su propio intérprete. Faltan cuatro, y cada uno exige ejecutar el generador con esa versión |
| G4.7 | parcial | el bundle **existe**: `scripts/bundle.py` lo construye, verifica e instala; probado con pip real y `--no-index` -134 tools- y con `socket` prohibido. Falta la VM realmente desconectada o un proxy corporativo |
| G4.8 | cumplido | no se cae fuera de user-scope en silencio |
| G4.9 | cumplido | contención de la recuperación, 35 pruebas |
| G4.10 | cumplido | preflight; nunca dos servidores en el mismo stdout |
| G5.1 | pendiente-externo | Power BI Desktop con un modelo cargado |
| G5.2 | pendiente-externo | Desktop, `refresh` y una captura con datos |
| G5.3 | pendiente-externo | Desktop con un modelo vacío de verdad |
| G5.4 | pendiente-externo | Desktop, y cambio de página con captura |
| G5.5 | cumplido | mismo hallazgo y misma evidencia **live fechada el 2026-08-14** que G1.1: `test_live_la_ventana_real_delata_un_pbip_sin_handles`, sobre un `.pbip` sintético y desechable |
| G5.6 | parcial | la prueba **existe y es local**: `test_live_captura_real_deja_el_proyecto_byte_a_byte_igual`, sobre proyecto sintético. Se ejecutó el 2026-08-14 para CORE-002; falta repetirla con la matriz de escenarios de TEST-003 |
| G6.1 | pendiente-externo | no existe ninguna 2.x en PyPI: el intento de v2.0.0 falló con `invalid-publisher` antes de subir nada |
| G6.2 | cumplido | **observado en el remoto el 2026-08-15**: en el run 31914746886 `publicar-pypi` falló y `publicar-mcp` quedó **omitido** sin ejecutar un paso, por `needs`. Evidencia y su alcance exacto en [`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md); la variante «suite en rojo» está cubierta por mutación en `tests/test_release_pipeline.py` |
| G6.3 | cumplido | ningún camino publicado ejecuta desde `main` |
| G6.4 | parcial | lógica probada contra servidor local en 11 escenarios; el asset sigue sin existir en ninguna release. **v2.0.1 añade quien lo publica**: `publicar-github-release` lo sube y relee su SHA-256 y su URL contra el manifest |
| G6.5 | cumplido | una sola construcción |
| G7.1 | pendiente-externo | admin del remoto de GitHub |
| G7.2 | cumplido | **CodeQL verde sobre `main`/`1f0405b`**: run 31913970370, check-run `Analizar (python)` con `conclusion: success` el 2026-08-15. Comandos de lectura y salida capturada en [`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md) |
| G7.3 | pendiente-externo | Dependabot *security updates* en el remoto |
| G7.4 | pendiente-externo | *secret scanning* y *push protection* en el remoto |
| G7.5 | pendiente-externo | *private vulnerability reporting* en el remoto |
| G7.6 | cumplido | Actions pineadas por SHA, cero tags flotantes |
| G8.1 | cumplido | suite completa en verde sin excluir `packaging` |
| G8.2 | cumplido | dos mutaciones medidas |
| G8.3 | cumplido | venv de packaging limpio |
| G8.4 | cumplido | `scripts/doctor.py` sale 0 sin traceback |
| G8.5 | cumplido | el README no se contradice |
| G8.6 | cumplido | la política de publicación coincide con los workflows |
| G8.7 | cumplido | el alcance del «PC vacío» está dicho |
| G8.8 | cumplido | runbook por procedimiento |

## Cuentas

| Categoría | Gates |
|---|---|
| cumplido | **35** |
| parcial | **6** |
| pendiente-local | **0** |
| pendiente-ratificacion | **0** |
| pendiente-externo | **13** |
| **Total** | **54** |

**Trabajo local pendiente: ninguno. Pendiente de ratificación: ninguno.**
Los 19 gates que no están cumplidos esperan **un entorno**: 6 parciales con el
mecanismo ya demostrado aquí y 13 externos puros.

### Los dos que se movieron el 2026-08-15, y por qué no fue por cansancio

**G6.2 y G7.2 pasaron de `pendiente-externo` a `cumplido`** después del intento
fallido de publicar `v2.0.0`. No se cerraron porque el ciclo terminara: se
cerraron porque ese intento produjo, **en el remoto de verdad**, la observación
que los dos pedían, y está capturada con sus comandos de lectura en
[`EVIDENCIA_REMOTA_2026-08-15.md`](EVIDENCIA_REMOTA_2026-08-15.md).

Es el cuarto caso de la misma lección que ya dejaron G3.6, G4.7 y G5.5: la
etiqueta «externo» se pega a bloques enteros. G6.2 estaba archivado junto a G6.1
y G6.4 bajo «hace falta una release publicada», y resultó que **no la
necesitaba**: lo que pedía —que un fallo aguas arriba impida publicar— se ve en
un run que falló, no en uno que publicó.

**Lo que ese movimiento NO autoriza a decir.** El tag `v2.0.0` existe en el
remoto y **no es una release publicada**: no hay GitHub Release, no hay 2.x en
PyPI y no hay 2.x en el MCP Registry. G6.1 y G6.4 siguen exactamente donde
estaban, y el tag no cuenta como evidencia de ninguno de los dos.

La firma que faltaba llegó: CONTRACT-003 se ratificó el 2026-08-15 y sus tres
cambios están aplicados en 2.0.0. El dossier
[`CONTRACT_003_RATIFICATION.md`](CONTRACT_003_RATIFICATION.md) se conserva como
lo que es —el registro de una decisión tomada con los datos delante— y la
migración para quien consuma el contrato está en
[`../MIGRACION_1x_A_2.0.md`](../MIGRACION_1x_A_2.0.md).

Esa frase solo se puede escribir porque `pendiente-local` y
`pendiente-ratificacion` están vacíos, y hay una prueba que la ata.
