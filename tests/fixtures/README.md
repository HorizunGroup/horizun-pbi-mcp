# Fixtures de Horizun PBI MCP — estrategia híbrida

Dos niveles, con reglas distintas. **Ningún proyecto real entra a git.**

| Nivel | Ruta | ¿Versionado? | ¿Escritura? | Para qué |
|---|---|---|---|---|
| **Sintético** | `tests/fixtures/synthetic/` | ✅ Sí | ✅ Sí, sobre copia temporal | Todas las pruebas reproducibles del repositorio |
| **Local de compatibilidad** | `tests/fixtures/local/` | ❌ **Nunca** (ignorado) | ❌ **Solo lectura** | Comprobar que leemos PBIR/TMDL generado por Power BI Desktop de verdad |

---

## 1. Fixture sintético (versionado)

`synthetic/minimal/` es un proyecto `.pbip` completo e **inventado**:

```
Demo.pbip
Demo.Report/
  definition.pbir                    PBIR v4.0, datasetReference byPath
  definition/
    report.json                      themeCollection + publicCustomVisuals vacío
    pages/
      pages.json                     pageOrder + activePageName
      page01/
        page.json                    1280x720, displayName "Pagina Uno"
        visuals/
          tmplcard0000000000/        plantilla card CLONABLE (título con estilo)
          tmplcol00000000000/        plantilla clusteredColumnChart CLONABLE
Demo.SemanticModel/
  definition/
    model.tmdl                       culture es-ES, TimeIntelligence=0
    relationships.tmdl               Fact.DateKey -> Calendar.Date, OneDirection
    tables/
      Fact.tmdl                      2 medidas (una MULTILÍNEA con VAR/RETURN), 3 columnas
      Calendar.tmdl                  dataCategory: Time, 1 columna oculta
```

**Contenido:** tablas `Fact` y `Calendar`, medidas `TotalAmount` y `Ratio Pct`, columnas `Amount`, `DateKey`, `FactID`, `Date`, `Year`, `MonthNumber`. Todo inventado. Cero datos, cero nombres comerciales, cero información de ningún proyecto real.

### Casos que cubre

| Caso requerido | Cómo se cubre |
|---|---|
| Proyecto `.pbip` | `Demo.pbip` con `artifacts[].report.path` |
| Definición `.Report` | `definition.pbir` v4.0 + `definition/` |
| Páginas | `pages.json` + `page01/page.json` |
| Visual válido clonable | 2 plantillas con título estilado, para verificar que el clonado preserva formato |
| TMDL | `model.tmdl`, `tables/*.tmdl`, `relationships.tmdl` — incluye medida multilínea |
| Referencias modelo↔visual | Los visuales apuntan a `Fact.TotalAmount` y `Calendar.Year`, que **existen** en el TMDL |
| JSON corrupto | `synthetic/broken/corrupt_visual.json`, `corrupt_page.json` vía `broken_json(kind)` |
| Referencia inexistente | Constantes `MISSING_MEASURE`, `MISSING_COLUMN` |
| Path traversal | `traversal_payloads()` + `outside_marker_dir(sandbox)` |
| Cambio concurrente | La prueba modifica la copia entre lectura y escritura |
| Rollback | La prueba respalda, escribe, restaura y compara |

### Uso

```python
from tests.fixtures import synthetic

def test_algo(tmp_path):
    pbip = synthetic.materialize(tmp_path)   # copia MUTABLE en tmp_path
    ...                                      # escribe sin miedo
```

> `materialize()` copia. **Nunca** escribas sobre `synthetic/minimal/` directamente: ensuciaría el árbol de git y las pruebas dejarían de ser reproducibles.

### Regla para el traversal

`outside_marker_dir()` crea el directorio "fuera del proyecto" **dentro del `tmp_path` de pytest**. El "afuera" es relativo al proyecto sintético, pero sigue contenido en el sandbox. Ninguna prueba debe apuntar jamás a una ruta real del equipo.

---

## 2. Fixture local de compatibilidad (ignorado)

Opcional. Sirve para una sola cosa: comprobar que el parser lee PBIR/TMDL **real** de Power BI Desktop, que tiene mucha más variedad que el sintético.

### Cómo prepararlo

```bash
python scripts/setup_local_fixture.py --source "C:/ruta/a/MiInforme.pbip"
```

El script copia el `.pbip` y sus carpetas `.Report`/`.SemanticModel` a `tests/fixtures/local/`, y **marca los archivos como solo lectura**.

### Reglas innegociables

- **Nunca** se toca el proyecto original: el script solo lee de la fuente.
- **Nunca** se versiona: `tests/fixtures/local/` está en `.gitignore`, y el script verifica que git lo ignore antes de copiar nada.
- **Nunca** se escribe sobre la copia: las pruebas que la usen son de solo lectura y están marcadas `@pytest.mark.local_fixture`.
- **Nunca** se vuelcan datos completos a los logs: las pruebas reportan conteos y formas, no contenido.
- Las pruebas que dependan de ella **se omiten** (`skip`) si la carpeta no existe. La suite obligatoria nunca depende de este nivel.

### Si hace falta conservar estructura PBIR real

Extraer después un fixture **mínimo y anonimizado** desde la copia local (renombrando tablas, columnas y medidas, y vaciando cualquier literal), y promoverlo a `synthetic/` **sujeto a revisión explícita**. Nunca promover automáticamente.
