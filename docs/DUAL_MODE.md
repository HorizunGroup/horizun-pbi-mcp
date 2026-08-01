# `mode="both"` — por qué está bloqueado (R15)

**Estado: ABIERTO. `both` no se habilita en la v1.0.0-rc.5.**

Este documento explica por qué, qué se conserva mientras tanto, y cómo tendría que diseñarse si algún día se implementa. **No describe nada que exista hoy.**

---

## El problema, en una frase

`live` y `pbip` escriben en **dos recursos distintos** que no comparten transacción, y sus precondiciones son **mutuamente excluyentes**.

| Modo | Escribe en | Requiere |
|---|---|---|
| `live` | modelo en memoria de `msmdsrv.exe` | Power BI Desktop **abierto** |
| `pbip` | archivos TMDL/PBIR en disco | Power BI Desktop **cerrado** |

`live` solo es posible si Desktop está abierto, porque el motor únicamente existe mientras lo está. `pbip` escribe archivos que Desktop sobrescribe al guardar, así que la política estricta lo bloquea cuando Desktop está `open` o `unknown`.

**No existe ningún estado del sistema en el que ambos destinos puedan escribirse con seguridad en una sola llamada.**

## Qué pasaba antes

`both` aplicaba `live` primero y `pbip` después. Con Desktop abierto —el único estado en que `live` funciona— el resultado era un **estado parcial determinista**: un `SaveChanges` ejecutado, el cambio vivo en memoria, el disco intacto, y `consistent: False` en la respuesta.

Medido, no supuesto: la columna quedaba oculta en el modelo en memoria y el TMDL sin tocar.

## Qué hace hoy

Las seis tools duales rechazan `mode="both"` con `dual_mode_not_safely_available` **antes de cualquier efecto**: antes de conectar a TOM, de validar contra el motor, de leer para planificar, de crear un journal, de hacer backup o de tocar un archivo.

`pbi_create_measure` · `pbi_update_measure` · `pbi_delete_measure` · `pbi_set_column_visibility` · `pbi_hide_columns` · `pbi_set_relationship_direction`

Sin bypass por variable de entorno. `tests/test_dual_mode_guard.py` lo verifica tool por tool comprobando que no hubo conexión, ni `SaveChanges`, ni escritura, ni entrada en el change log, ni journal, y que la huella del proyecto no cambió.

`_apply_both_compensated()` sigue en `tools/model_edit_tools.py` como **mecanismo interno**, con pruebas unitarias directas. No es alcanzable desde la tool pública y no justifica aceptar `both`.

---

## Diseño futuro: saga de dos etapas — NO IMPLEMENTADO

Si se retoma, `both` **no puede** presentarse como una transacción. No hay transacción distribuida entre Analysis Services y el sistema de archivos, y simularla sería mentir sobre la garantía.

La forma honesta es una **saga**: dos etapas con compensación explícita, y un resultado que diga si la compensación fue completa.

```
1. preflight live      ¿hay sesión? ¿el objeto existe? ¿el cambio es válido?
2. preflight PBIR      ¿Desktop cerrado? ¿versión soportada? ¿esquema válido?
3. snapshot            estado previo de AMBOS destinos, con huellas
4. plan compuesto      qué cambia en cada lado, en memoria
5. confirmación        el usuario aprueba el plan compuesto
6. etapa 1             aplicar en un destino
7. verificación 1      releer y comprobar
8. etapa 2             aplicar en el otro
9. verificación 2      releer y comprobar
10. compensación       si la 2 falla, deshacer la 1
11. resultado          applied | compensated | partial_failure
```

**El paso 10 es el que decide si esto puede existir.** Compensar `live` significa revertir en memoria con otro `SaveChanges`, que puede fallar por su cuenta. Compensar `pbip` es un rollback de archivos, que ya sabemos hacer. Un fallo en la compensación deja `partial_failure`, que **no es éxito** y exige intervención manual.

Y queda el problema de fondo: los pasos 6–9 exigen que Desktop esté abierto para `live` y cerrado para `pbip`. Cualquier saga real tendría que pedir al usuario que cierre Desktop entre las dos etapas, lo que la convierte en un **workflow guiado**, no en una operación atómica.

### Condiciones para habilitarlo

No antes de tener, todas:

1. pruebas de fallo en **cada** frontera (6, 7, 8, 9) con compensación verificada;
2. prueba de fallo **de la compensación**, con `partial_failure` correctamente reportado;
3. una respuesta que distinga `applied`, `compensated` y `partial_failure` sin ambigüedad;
4. una decisión explícita sobre el conflicto abierto/cerrado de Desktop.

**R15 no se cierra hasta entonces.** Mientras tanto, la recomendación es elegir un destino:

- **`live`** para iterar rápido con Desktop abierto, sabiendo que se pierde al cerrar sin guardar;
- **`pbip`** para cambios duraderos y versionables, con Desktop cerrado.
