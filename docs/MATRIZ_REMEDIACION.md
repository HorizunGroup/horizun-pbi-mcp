# Matriz de remediación

Ciclo abierto sobre la rama local `codex/p1-p4-audit-checkpoint`, base `2973f1d`
(v1.5.5). Los cambios están **commiteados en local**; **sin push, sin PR, sin
tag, sin publicación.**

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
| CONTRACT-002 | El golden congela solo el envelope `{result}`: una extensión del payload es invisible para la red de seguridad del contrato | Hallazgo derivado de CONTRACT-001 | 2026-08-14 | **Abierta** |

## Seguridad funcional

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| CORE-001 | Detección falsa de proyecto cerrado (`project_state` ignora el título de ventana que `desktop_launcher` sí usa) | Crítica | G1.1 | **Cerrada** — 2026-08-14, con evidencia live |
| CORE-002 | Traversal sin `ensure_within_base` y escritura sin transacción en `desktop_capture` | Crítica | G1.2, G1.3 | **Cerrada** — 2026-08-14, con captura live e igualdad byte a byte |
| CORE-003 | Tras el timeout, el hilo daemon sigue en `SaveChanges` y `safe_to_retry` sale `true` | Alta | G1.4 | **Parcialmente cerrada** |
| CORE-004 | Anotaciones y confirmaciones que no describen el efecto (4 sub-hallazgos) | Alta | G1.5, G1.6 | **Abierta** |
| CORE-005 | `msg` y `exc` entran al log sin pasar por `redact()` | Alta | G1.7 | **Abierta** |
| CORE-006 | Sin cerrojo interproceso en `txn`/`planning` (el mecanismo existe en `idempotency`) | Alta | G1.8 | **Abierta** |

## Instalación y ciclo de vida

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| INSTALL-001 | La siembra mueve el runtime de N−1 antes de validar el nuevo, sin rollback | Alta | G4.1 | **Abierta** |
| INSTALL-002 | Node <20 o fallo del validador opcional deja `state=failed` | Alta | G3.4 | **Parcialmente cerrada** |
| INSTALL-003 | Cinco caminos publicados ejecutan desde `main` sin pin ni verificación | Crítica | G6.3, G6.4 | **Abierta** — bloque 1 hecho el 2026-08-14 (marketplaces pinneados, publisher verificado, bootstrap de Claude retirado); falta el one-paste |
| INSTALL-004 | La verificación final es una coincidencia de subcadena sobre `plugin list` | Media | G3.5 | **Parcialmente cerrada** |
| INSTALL-005 | El wheel no lleva scripts, DLL, esquemas ni bootstrap | Alta | G3.6 | **Abierta** |
| INSTALL-006 | Los esquemas se publican por copia archivo a archivo sobre el destino vivo | Media | G4.2, G4.3 | **Parcialmente cerrada** |
| INSTALL-007 | Reintento sin `--scope user` y `ExecutionPolicy` persistente | Media | G4.8 | **Abierta** |
| INSTALL-008 | No existe `uninstall` ni `purge` | Media | G4.4, G4.5 | **Abierta** |
| INSTALL-009 | Sin lock ni hashes, sin bundle offline ni runbook de proxy | Media | G4.6, G4.7 | **Abierta** |
| INSTALL-010 | `ready` se escribe sin handshake contra el runtime instalado | Alta | G3.1, G3.3 | **Abierta** |

## Release y supply chain

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| RELEASE-001 | CI prueba en Windows; `publish-pypi` reconstruye en Ubuntu y publica eso | Crítica | G6.1, G6.5 | **Abierta** |
| RELEASE-002 | Los workflows de publicación no dependen de un CI verde | Crítica | G6.2 | **Abierta** |
| RELEASE-003 | Sin CodeQL ni Dependabot; actions con tags flotantes; controles del remoto sin comprobar | Alta | G7.1–G7.6 | **Abierta** |

## Pruebas y contrato

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| TEST-001 | `test_packaging` convierte fallos en skips y prueba en venv no limpio | Alta | G8.2, G8.3 | **Abierta** |
| TEST-002 | Inventario de las 134 tools: ejecución MCP, casos negativos, payload congelado | Alta | G2.3, G2.4 | **Abierta** |
| TEST-003 | Sin cobertura live verificada de los seis escenarios de Desktop | Alta | G5.1–G5.6 | **Abierta** |
| TEST-004 | `isolated_settings` deja sin DLL de Analysis Services a las pruebas live | Media | G5.2, G5.4, G5.6 | **Cerrada** — 2026-08-14 |

## Documentación y CLI

| Id | Asunto | Severidad | Gate | Estado |
|---|---|---|---|---|
| DOC-001 | El README ofrece `mode=both` —con ejemplo— y lo declara bloqueado en el mismo archivo | Media | G8.5 | **Abierta** |
| DOC-002 | `AGENTS.md:126` niega la publicación en PyPI que hace `publish-pypi.yml` | Media | G8.6 | **Abierta** |
| DOC-003 | "Completely empty PC" no dice que Power BI Desktop queda fuera | Baja | G8.7 | **Abierta** |
| DOC-004 | Sin runbook de update, rollback, uninstall, purge, proxy ni offline | Media | G8.8 | **Abierta** |
| CLI-001 | El one-paste instala y verifica solo Claude | Media | G3.2 | **Parcialmente cerrada** |

## Cuentas

30 entradas: **4 cerradas** (CONTRACT-001, CORE-001, CORE-002, TEST-004),
5 parcialmente cerradas, 21 abiertas. Conteo verificado sobre las filas, no
escrito de memoria.

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
RELEASE-002.

**Ninguna entrada de la auditoría se cerró en esta pasada.** Es un triaje
documental: se verificó el estado real de cada hallazgo contra el código de hoy,
no se remedió ninguno.

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
