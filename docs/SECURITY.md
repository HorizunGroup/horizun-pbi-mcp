# Guía de seguridad

Qué protege este servidor, cómo, y qué **no** puede prometer.

---

## 1. Modelo de amenazas

Este MCP recibe instrucciones de un LLM y escribe en archivos del usuario. Las amenazas reales no son un atacante remoto, sino:

| # | Amenaza | Mitigación |
|---|---|---|
| T1 | Un id de página o visual con sintaxis de ruta escribe fuera del proyecto | `services/paths.py` |
| T2 | Se pisa un cambio hecho por otro proceso | Fingerprints sha256 verificados tres veces |
| T3 | Power BI Desktop sobrescribe lo que escribimos | Política estricta |
| T4 | Una operación multiarchivo deja el proyecto a medias | Transacción compensada con journal |
| T5 | Se ejecuta DAX que no es de lectura | Clasificador fail-closed |
| T6 | Un secreto acaba en el log | Redacción en `services/telemetry.py` |
| T7 | Un backup dentro del `.pbip` lo corrompe | Destino validado |
| T8 | Se opera contra una sesión que ya no es la misma | Huella de sesión |

---

## 2. Rutas: identificadores, no rutas

Un id de página o de visual es un **identificador**. Se rechaza, antes de tocar el disco: separadores, `..`, rutas absolutas, sintaxis de unidad (`C:\x` y `C:x`), UNC, `\\?\`, `\\.\`, ADS de NTFS (`archivo.json:stream`), nombres reservados (`CON`, `NUL`, `AUX`, `COM1`…), componentes vacíos y con punto o espacio final.

La contención resuelve junctions y compara con `normcase` (NTFS no distingue mayúsculas) y **se revalida justo antes de escribir**: un enlace puede cambiar entre la validación y la escritura.

> `Path('base') / 'C:/otro'` devuelve `C:/otro`. Ese es el motivo de validar cada componente antes de unirlo.

---

## 3. DAX: fail-closed

Escáner léxico primero (comentarios, cadenas, identificadores citados y entre corchetes), clasificación después. Solo se permiten tres formas:

```
EVALUATE ...
DEFINE ... EVALUATE ...
SELECT ... FROM $SYSTEM....
```

Se rechaza todo lo demás, incluido lo ambiguo: XMLA, DDL, `;`, `DEFINE` sin `EVALUATE`, `SELECT` cuyo `FROM` no sea `$SYSTEM.`, tokens concatenados y delimitadores sin cerrar.

Como los literales se neutralizan primero, `EVALUATE ROW("DROP TABLE", 1)` **sigue siendo lectura**.

**No hay escape.** No existe variable de entorno que lo relaje; hay una prueba que lo verifica.

---

## 4. Política de Power BI Desktop

| Estado | Escritura PBIR |
|---|---|
| `closed` verificado | Permitida |
| `open` | **Bloqueada** |
| `unknown` | **Bloqueada** |

Señales **solo de lectura**: procesos, línea de comandos y archivos abiertos. Nunca se muta un archivo real para sondear si está bloqueado.

**Límite honesto:** esto no impide que Desktop sobrescriba el informe *después*. Solo evita que escribamos *nosotros* cuando hay indicios de que está abierto. El mensaje de error lo dice.

---

## 5. Escrituras: transacción compensada

```
PLAN → fingerprint de cada objetivo
SNAPSHOT → copia al journal + manifiesto
PRE-CHECK → re-verificar justo antes de reemplazar
WRITE → tmp → flush → fsync → validar → os.replace → limpiar
POST → releer y comparar
COMMIT | ROLLBACK
```

**No hay atomicidad multiarchivo del sistema de archivos.** Entre el primer y el último `os.replace` existe una ventana. Lo que se garantiza: es corta, el journal permite volver atrás, y **nunca se reporta éxito si el rollback no quedó limpio**.

El rollback **no pisa cambios externos**: si alguien tocó el archivo después de nuestra escritura, se marca `rollback_conflict` y se conserva el journal.

---

## 6. `mode="both"`: deshabilitado

`live` necesita Desktop **abierto**; `pbip` lo necesita **cerrado**. No hay estado del sistema en que ambos sean seguros en una llamada. Antes aplicaba `live` y fallaba en `pbip`, dejando un estado parcial determinista.

Ahora se rechaza **antes de cualquier efecto**. Sin bypass.

---

## 7. Datos que nunca salen

En el log solo se registra la **forma**: `<15 chars>`, `<2 elementos>`. Nunca el contenido de `query`, `dax`, `expression`, `rows`, `spec`, `html`, `password`, `token`… Las rutas se acortan a dos segmentos y los patrones `Password=…` se enmascaran.

En el repositorio no entran: `.pbix`, `.pbip` reales, `.Report/`, `.SemanticModel/`, `libs/`, `outputs/`, `backups/`, `.env`, `.mcp.json` ni credenciales. Los fixtures versionados son 100 % inventados.

---

## 8. Lo que este servidor **no** hace

| No hace | Por qué |
|---|---|
| Autenticarse en Microsoft o Fabric | Fuera de alcance; sin gestión de credenciales |
| Publicar en el Power BI Service | Solo local |
| Escribir por XMLA arbitrario | No hay forma segura de acotarlo |
| Purgar backups | Se define la política antes de borrar nada |
| Reanudar journals al arrancar | Puede ser peor que dejarlo quieto; ver `RECOVERY.md` |
| Adivinar el destino de una referencia rota | Requiere `mapping` explícito |
| "Arreglar todo" | Los autofixes se eligen por regla y objeto |

---

## 9. Si encuentras un fallo de seguridad

Reprodúcelo con un fixture sintético y un `tmp_path`. **Nunca uses un proyecto real** para demostrar un fallo de escritura: el "afuera" de una prueba debe vivir dentro del `tmp_path` de pytest (`synthetic.outside_marker_dir()`).
