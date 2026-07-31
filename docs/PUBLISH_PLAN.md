# Plan de publicación — `v1.0.0-rc.1`

**Nada de esto se ha ejecutado.** No hay remoto, no hay push, no hay repositorio creado. Este documento describe lo que se haría y con qué comandos exactos.

---

## El problema del historial

El árbol versionado está **limpio**: 169 archivos, sin credenciales, sin datos reales, sin el nombre de usuario de esta máquina.

El **historial no**. Dos blobs de los dos commits más antiguos contienen rutas personales:

| Blob | Archivo | Commits |
|---|---|---|
| `0f7cd541dc` | `.env.example` | `a304e33`, `82bc6c9` |
| `bcb86c4ac7` | `examples/mcp-config.example.json` | `a304e33`, `82bc6c9` |

Ambos traían `C:/Users/<usuario>/OneDrive/Documentos/...` como valor de ejemplo. Ya no están en el árbol actual.

`git fsck` no reporta corrupción.

---

## Restricción que no se negocia

`AGENTS.md` establece que **`a304e33` no se reescribe**. Este repositorio es el de desarrollo y conserva su historia tal cual.

Por tanto: **no se filtra ni se reescribe este repositorio**. Se prepara una **copia saneada en otro directorio**, y es esa la que se publica.

---

## Procedimiento propuesto

### 1. Copia saneada, fuera de este repositorio

```bash
mkdir C:\tmp\horizun-publish
cd C:\tmp\horizun-publish
git init -b main
```

Copiar el árbol versionado (solo lo que `git ls-files` lista — nunca `libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`, `.mcp.json`, `.env`):

```bash
cd C:\Users\<tu-usuario>\OneDrive\Documentos\PowerBI-MCP
git archive --format=tar HEAD | tar -x -C C:\tmp\horizun-publish
```

`git archive` respeta `.gitignore` por construcción: exporta exactamente los 169 archivos versionados y nada más.

### 2. Historia pública nueva

```bash
cd C:\tmp\horizun-publish
git add -A
git commit -m "Horizun PBI MCP v1.0.0-rc.1"
git tag -a v1.0.0-rc.1 -m "Release candidate 1"
```

Un solo commit inicial. Los 42 commits de desarrollo quedan **solo en el repositorio original**, que es local y no tendrá remoto.

**Alternativa** si prefieres conservar la trazabilidad: `git filter-repo` sobre la copia (nunca sobre el original) para reescribir esos dos blobs. Cambia todos los SHA, pero mantiene los 42 mensajes. Es tu decisión; la opción 1 es la que menos riesgo tiene.

### 3. Verificar la copia antes de publicar

```bash
cd C:\tmp\horizun-publish
python -m pip install -e .
python scripts/fetch_libs.py
python scripts/fetch_pbir_schemas.py
python scripts/fetch_report_validator.py
python -m pytest -q
python scripts/doctor.py
python -m tests.contract_utils
```

Y volver a escanear el historial de la copia:

```bash
git rev-list --objects --all | ForEach-Object { ... }   # sin rutas personales
git fsck
```

### 4. Repositorio privado

```bash
gh repo create horizun-pbi-mcp --private --source=C:\tmp\horizun-publish --push
```

**Requiere tu autorización explícita.** Es la primera acción externa.

### 5. Configuración posterior

| Ajuste | Valor |
|---|---|
| Rama principal | `main` |
| Protección de rama | Requerir PR, requerir CI en verde, prohibir force-push |
| CI | **Windows** (`windows-latest`) — Power BI Desktop es Windows-only y la suite usa rutas de Windows |
| Release | `v1.0.0-rc.1`, marcada como **pre-release** |

Esbozo de CI:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: python -m pip install -e .[test]
      - run: python scripts/fetch_pbir_schemas.py
      - run: python scripts/fetch_report_validator.py
      - run: python -m pytest -q
      - run: python -m tests.contract_utils
```

`fetch_libs.py` **no** entra en CI: las DLL de Analysis Services solo hacen falta para la capa en vivo, que necesita Power BI Desktop. La suite pasa sin ellas.

### 6. `v1.0.0` estable

Solo después de:

1. clonar el repositorio publicado en una máquina o entorno **limpio**;
2. instalar siguiendo el README, sin atajos;
3. `pytest`, `doctor` y contract check en verde ahí;
4. registrar el MCP en un cliente y comprobar el handshake.

---

## Lo que se publicaría

| Directorio | Archivos |
|---|---|
| `src/` | 73 |
| `tests/` | 61 |
| `docs/` | 12 |
| raíz | 12 |
| `scripts/` | 7 |
| `examples/` | 4 |
| **Total** | **169** |

Ignorados y **no publicados**: `libs/`, `outputs/`, `backups/`, `schemas_cache/`, `validator_cache/`, `build/`, `.mcp.json`, `.env`, cachés de Python.

Las tres dependencias externas —DLL de Analysis Services, esquemas PBIR y CLI oficial de Microsoft— **no se redistribuyen**: se instalan con sus scripts, con versión fijada y hash verificado.
