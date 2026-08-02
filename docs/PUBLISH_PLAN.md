# Publicación: cómo se hizo y cómo repetirlo

Este documento describe la publicación oficial: el repositorio histórico se
conserva como legacy privado y la versión pública se exporta con historia nueva
desde un árbol verificado.

Repositorio público: **https://github.com/HorizunGroup/horizun-pbi-mcp**

---

## El problema que había que resolver

El árbol versionado estaba limpio. El **historial no**: dos blobs de los dos commits más antiguos contenían rutas personales (`C:/Users/<usuario>/...`) como valor de ejemplo en `.env.example` y `examples/mcp-config.example.json`. Ya no están en el árbol actual, pero seguían siendo alcanzables desde el historial.

Y `AGENTS.md` establece que **`a304e33` no se reescribe**.

## La decisión

**No se filtra ni se reescribe el repositorio de desarrollo.** Se exporta una copia saneada a otro directorio y se publica esa, con **historia nueva de un solo commit**.

Los commits de desarrollo quedan únicamente en el repositorio local, que **no tiene remoto** y no lo tendrá.

---

## Procedimiento

### 1. Exportar el árbol versionado

```bash
mkdir C:\tmp\horizun-publish
```

```bash
git archive --format=tar HEAD | tar -x -C C:\tmp\horizun-publish
```

`git archive` exporta exactamente lo que `git ls-files` lista, y nada más. `libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`, `build/`, `.mcp.json` y `.env` quedan fuera **por construcción**, no por acordarse de excluirlos.

### 2. Verificar la copia antes de tocar nada externo

Comparar el inventario contra `git ls-files`, y escanear en busca de rutas personales, credenciales y datos reales. Luego, la instalación completa **desde la copia**:

```bash
python -m pip install -e .
python scripts/fetch_libs.py
python scripts/fetch_pbir_schemas.py
python scripts/fetch_report_validator.py
python -m pytest -q
python scripts/doctor.py
python -m tests.contract_utils
```

Los tres últimos en verde, y el handshake stdio contra el paquete instalado.

### 3. Historia nueva

```bash
git init -b main
git add -A
git commit -m "Horizun PBI MCP v1.0.0"
```

**Antes de etiquetar**, comprobar que `branding.VERSION` y `pyproject.toml` declaran esa misma versión. Etiquetar un commit que declara otra produce un paquete que miente sobre lo que es — pasó con `rc.2` y hubo que corregirlo.

```bash
git tag -a v1.0.0 -m "Horizun PBI MCP v1.0.0"
```

### 4. Publicar

```bash
gh repo create horizun-pbi-mcp --public --source=. --remote=origin
git push -u origin main
git push origin v1.0.0
gh release create v1.0.0 --notes-file RELEASE_NOTES_1.0.0.md --verify-tag
```

### 5. Esperar al CI

**No se declara terminado hasta que la matriz está completa en verde.** Las
compuertas corren en máquinas limpias de GitHub; la validación local usa un
entorno virtual sin paquetes del repositorio como evidencia reproducible.

```bash
gh run list --repo HorizunGroup/horizun-pbi-mcp
gh run view <RUN_ID> --repo HorizunGroup/horizun-pbi-mcp
```

---

## Actualizaciones posteriores

La copia saneada conserva su `.git` y su remoto. Para publicar un cambio:

```bash
cd C:\tmp\horizun-publish
git fetch origin && git reset --hard origin/main
```

Se re-exporta el árbol desde el repositorio de desarrollo, se sincroniza sobre la copia, y se hace **un commit nuevo** sobre `main`. **Nunca se reescribe un commit ya publicado.**

---

## Lo que se publicó

| Directorio | Archivos |
|---|---|
| `src/` | 73 |
| `tests/` | 61 |
| `docs/` | 13 |
| raíz | 12 |
| `scripts/` | 7 |
| `examples/` | 4 |
| `.github/` | 1 |
| **Total** | **171** |

Las tres dependencias externas —DLL de Analysis Services, esquemas PBIR y CLI oficial de Microsoft— **no se redistribuyen**: se instalan con sus scripts, con versión fijada y hash verificado.

## Configuración del repositorio

| Ajuste | Valor |
|---|---|
| Rama principal | `main` |
| CI | `windows-latest`, Python 3.10 y 3.13 |
| Releases | `v1.0.0` estable, con CI en máquinas limpias y validación local aislada |

**Pendiente de configurar a mano** (necesita permisos que el token de CLI no tiene): protección de rama sobre `main` — requerir PR, requerir CI en verde, prohibir force-push. Se hace en *Settings → Branches → Add rule*.
