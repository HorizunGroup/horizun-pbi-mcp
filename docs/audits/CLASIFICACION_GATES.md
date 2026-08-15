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
| G1.5 | pendiente-ratificacion | los tres cambios de CORE-004(a)(b)(c) rompen el contrato congelado; dossier en `CONTRACT_003_RATIFICATION.md` |
| G1.6 | cumplido | el validador escribe en `tempfile.gettempdir()`, no en el proyecto |
| G1.7 | cumplido | redacción verificada sobre el formateador real |
| G1.8 | cumplido | cerrojo de proyecto, dos procesos de verdad |
| G2.1 | cumplido | `python -m tests.contract_utils` sale 0 |
| G2.2 | pendiente-local | el golden cubre **2 tools públicas de 134**; el inventario de CONTRACT-002 demuestra que muchas más son alcanzables sin Desktop |
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
| G4.3 | parcial | staging y cerrojo demostrados; `npm` está simulado |
| G4.4 | cumplido | `uninstall` con CLI real sobre un data root de prueba |
| G4.5 | cumplido | `purge` enumera y pesa antes de borrar |
| G4.6 | pendiente-local | el lock cubre **solo Python 3.14/win32** y `pyproject` admite ≥3.10; en 3.10 y 3.13 se cae al resolutor sin hashes |
| G4.7 | pendiente-local | el bundle offline **no existe**, y construirlo es trabajo local; solo la VM sin red es externa |
| G4.8 | cumplido | no se cae fuera de user-scope en silencio |
| G4.9 | cumplido | contención de la recuperación, 35 pruebas |
| G4.10 | cumplido | preflight; nunca dos servidores en el mismo stdout |
| G5.1 | pendiente-externo | Power BI Desktop con un modelo cargado |
| G5.2 | pendiente-externo | Desktop, `refresh` y una captura con datos |
| G5.3 | pendiente-externo | Desktop con un modelo vacío de verdad |
| G5.4 | pendiente-externo | Desktop, y cambio de página con captura |
| G5.5 | pendiente-externo | Desktop con un `.pbip` realmente abierto |
| G5.6 | pendiente-externo | Desktop, y rollback comparado byte a byte |
| G6.1 | pendiente-externo | release publicada de v1.5.5 |
| G6.2 | pendiente-externo | release publicada, con CI verde del mismo commit |
| G6.3 | cumplido | ningún camino publicado ejecuta desde `main` |
| G6.4 | parcial | lógica probada contra servidor local en 11 escenarios; falta el asset real |
| G6.5 | cumplido | una sola construcción |
| G7.1 | pendiente-externo | admin del remoto de GitHub |
| G7.2 | pendiente-externo | CodeQL en verde en el remoto |
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
| cumplido | **29** |
| parcial | **4** |
| pendiente-local | **3** |
| pendiente-ratificacion | **1** |
| pendiente-externo | **17** |
| **Total** | **54** |

**Trabajo local pendiente: G2.2, G4.6 y G4.7.** Mientras esos tres sigan aquí,
la frase honesta no es «100% local»: es el porcentaje y la lista.
