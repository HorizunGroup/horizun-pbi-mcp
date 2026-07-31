# Checklist de publicación

Lo que se comprueba antes de etiquetar una release. Cada línea es un comando, no una intención.

---

## 1. Árbol y contrato

```bash
git status --short
```

Vacío. Nada sin versionar que debiera estarlo.

```bash
python -m tests.contract_utils
```

Sale con **0**. Si sale 1, dice qué cambió y **si rompe compatibilidad**. Un cambio compatible aprobado se congela con `--write`; uno incompatible no se publica.

## 2. Suite completa

```bash
python -m pytest -q
```

**Sin excluir `packaging`.** Las omisiones deben ser solo de entorno y decir cómo ejecutarlas.

## 3. Diagnóstico

```bash
python scripts/doctor.py
```

Sale con **0** y **sin traceback**. Un exit 0 con un traceback impreso no cuenta: `doctor` y el contract check comparten el logger del servidor, y un fallo de rotación ensuciaba stderr.

## 4. Empaquetado

Wheel y sdist se construyen e instalan en un entorno limpio, y el servidor arranca **desde el paquete instalado, fuera del repositorio**:

```bash
python -m pytest -m packaging -q
```

Comprueba además que el wheel:

- lleve `services/`, `reporting`, `branding` y el manifiesto de esquemas;
- **no** lleve DLLs, fixtures, `outputs/`, `backups/` ni los esquemas de terceros;
- responda al handshake stdio con `serverInfo.name = horizun-pbi-mcp` y la versión del producto.

## 5. Dependencias verificadas

```bash
python scripts/fetch_libs.py --check
python scripts/fetch_pbir_schemas.py
python scripts/fetch_report_validator.py --check
```

Los tres verifican **hash antes de instalar** y fallan cerrados. Ninguno usa `latest` ni `npx`.

## 6. Sin datos reales ni rutas personales

```bash
git status --short --ignored
```

`libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`, `.env` y `.mcp.json` **ignorados**.

Y sobre el árbol versionado, ninguna coincidencia de: rutas de usuario, nombres de proyectos reales, credenciales, tokens.

## 7. Validación sobre un `.pbip` real

Sobre una **copia** fuera de OneDrive, nunca sobre el original:

1. huella completa del original **antes**;
2. smoke de solo lectura con Desktop abierto;
3. cerrar Desktop **sin guardar**;
4. operaciones PBIR sobre la copia cerrada: audit, dry-run, apply, update de página, duplicación, borrado, workflows;
5. fallo inyectado → **rollback byte a byte**;
6. recuperación desde journal;
7. abrir la copia en Desktop: debe cargar sin error nuevo;
8. guard: con el proyecto abierto, la escritura se **bloquea**; con **otro** proyecto abierto, **no** hay falso bloqueo;
9. eliminar solo los residuos propios;
10. huella del original **después**: idéntica.

## 8. Documentación coherente

- Conteo de tools y de pruebas tomados de la **ejecución final**, no estimados.
- Limitaciones descritas como son: esquemas no publicados upstream, `both` bloqueado, `filters`/`interactions` rechazados.
- Ningún ejemplo con rutas personales.

---

## Excepciones aceptadas — no bloquean la RC

| Excepción | Por qué |
|---|---|
| `visualContainer/2.10.0` y `bookmarks/2.0.0` sin publicar | 404 en el origen oficial; el CLI de Microsoft tampoco los valida |
| **G10** parcialmente cerrado | Consecuencia directa de lo anterior |
| **R15** abierto, `both` bloqueado | Precondiciones mutuamente excluyentes ([`DUAL_MODE.md`](DUAL_MODE.md)) |
| `filters`/`interactions` rechazados | Serialización PBIR pendiente; se rechazan, no se ignoran |
| Errores preexistentes del informe del usuario | No se corrigen automáticamente, nunca |
| Dos pruebas omitidas | Requieren Desktop abierto o una precondición del modelo |

---

## Etiquetado

Primero una **release candidate**, marcada como *pre-release*. La actual es **`v1.0.0-rc.3`**.

La versión declarada en `branding.VERSION` / `pyproject.toml` debe coincidir con el tag **antes** de etiquetar. Instalar desde un tag y obtener un paquete que reporta otra versión es exactamente lo que estas comprobaciones existen para evitar.

`v1.0.0` estable solo después de:

1. clonar el repositorio **publicado** en una máquina o entorno limpio;
2. instalar siguiendo el README, sin atajos;
3. `pytest`, `doctor` y contract check en verde **allí**;
4. registrar el MCP en un cliente y comprobar el handshake;
5. la matriz de CI **completamente** en verde, sin jobs saltados por dependencia.
