# Instalación y registro de Horizun PBI MCP

## Plugin directo para Codex y Claude Code

Esta es la vía recomendada para usuarios finales. No requiere un instalador
ejecutable propio ni editar archivos MCP a mano:

```bash
# Codex
codex plugin marketplace add HorizunGroup/horizun-pbi-mcp
codex plugin add horizun-pbi-mcp@horizun

# Claude Code
claude plugin marketplace add HorizunGroup/horizun-pbi-mcp
claude plugin install horizun-pbi-mcp@horizun
```

La preparación empieza automáticamente en la primera sesión. Mientras avanza
verás `pbi_install_runtime` y `pbi_install_status`; tras reiniciar el cliente
aparecerán las 108 tools. No hay que descargar ni ejecutar nada por separado. El
runtime y las descargas verificadas quedan en datos locales del plugin, fuera
del repositorio y de tus proyectos.

Python 3.10+ sigue siendo un requisito: es el proceso local que permite hablar
con Power BI Desktop. Node 20 solo es necesario para el validador PBIR opcional.

Guía reproducible desde cero. Al final, un cliente MCP debe ver 108 tools `pbi_*`.

---

## 1. Requisitos

| Requisito | Por qué | Obligatorio |
|---|---|---|
| **Windows** | Power BI Desktop sólo existe en Windows | Para la capa EN VIVO. La capa EN DISCO (`.pbip`) funciona en cualquier SO |
| **Python ≥ 3.10** | Probado en 3.14.3 | Sí |
| **.NET Framework 4.x** | Lo usa `pythonnet` (runtime `netfx`) | Para la capa EN VIVO |
| **Power BI Desktop** | Levanta el motor local `msmdsrv.exe` | Para la capa EN VIVO |
| **DLLs de Analysis Services** | ADOMD.NET + TOM. Se vendorizan en `libs/`, sin admin ni GAC | Sí |

---

## 2. Instalación

```bash
python -m pip install -r requirements.txt
```

```bash
python scripts/fetch_libs.py
```

El segundo comando descarga las DLLs de Analysis Services a `libs/`. No requiere permisos de administrador y no toca el GAC.

### Comprobar antes de registrar nada

```bash
python scripts/doctor.py
```

Debe terminar con `RESULTADO: instalacion operativa` y **código de salida 0**. Si algo obligatorio falla, el diagnóstico dice exactamente qué y cómo arreglarlo.

---

## 3. Registro por cliente

No hay una forma portable común: cada cliente resuelve las variables, el directorio de trabajo y el intérprete de Python a su manera. Por eso el generador emite **rutas absolutas ya resueltas** en tu máquina.

```bash
python scripts/make_mcp_config.py --client all
```

Imprime el fragmento correcto para cada cliente. **No modifica ninguna configuración global**: los ficheros globales se pegan a mano, a propósito.

### Comparativa

| | Claude Code | Claude Desktop | Codex | stdio genérico |
|---|---|---|---|---|
| **Archivo** | `.mcp.json` del proyecto, o `~/.claude.json` | `%APPDATA%\Claude\claude_desktop_config.json` | `~/.codex/config.toml` | el de tu cliente |
| **Formato** | JSON | JSON | **TOML** | JSON habitual |
| **¿Expande `${VAR}`?** | Sí | **No lo asumas** | **No lo asumas** | Desconocido |
| **Directorio de trabajo** | Hereda el de Claude Code | No configurable | Hereda el del proceso | Variable |
| **¿Busca Python?** | No: usa `command` literal | No | No | No |
| **Variables de entorno** | objeto `env` | objeto `env` | tabla `[mcp_servers.x.env]` | según cliente |
| **Comprobación** | `/mcp` | reiniciar y mirar el panel | reiniciar y listar | `scripts/doctor.py` |

> **Por qué no se usa `${PBI_MCP_HOME}` en las plantillas.** Sólo un cliente de los cuatro garantiza expandirlo. Una plantilla que funciona en uno y falla en silencio en los otros tres es peor que una ruta absoluta explícita. Si tu cliente sí expande variables, puedes sustituirlas después: el servidor no depende de ninguna.

### Claude Code

```bash
python scripts/make_mcp_config.py --client claude-code --write
```

Crea `.mcp.json` **dentro de este repositorio** (está en `.gitignore`: es tu configuración local). Reinicia Claude Code en esta carpeta y verifica con `/mcp`.

### Claude Desktop

```bash
python scripts/make_mcp_config.py --client claude-desktop
```

Copia el bloque `mcpServers` a `%APPDATA%\Claude\claude_desktop_config.json` y reinicia la aplicación. Si usas un entorno virtual, apunta al `python.exe` **de ese venv**.

### Codex

Dos métodos oficiales. **El primero es el recomendado.**

#### Método 1 — `codex mcp add` con el paquete instalado

Instala el paquete (crea el ejecutable `horizun-pbi-mcp` en el `PATH`) y regístralo con el CLI de Codex:

```bash
python -m pip install horizun-pbi-mcp
```

```bash
codex mcp add horizun-pbi-mcp -- horizun-pbi-mcp
```

```bash
codex mcp list
```

Ventaja: no hay ninguna ruta absoluta que mantener. Si mueves el repositorio o cambias de intérprete, sigue funcionando.

> Si usas un entorno virtual, actívalo **antes** de `pip install` y de `codex mcp add`: el ejecutable se crea dentro de ese venv, y Codex lanzará el que encuentre en el `PATH`.

Para pasar variables de entorno:

```bash
codex mcp add horizun-pbi-mcp --env HORIZUN_PBI_MCP_LOG_LEVEL=INFO -- horizun-pbi-mcp
```

#### Método 2 — `~/.codex/config.toml` a mano

Útil si trabajas desde el repositorio sin instalar el paquete:

```bash
python scripts/make_mcp_config.py --client codex
```

Pega la sección TOML resultante en `~/.codex/config.toml`. Es **TOML**, no JSON:

```toml
[mcp_servers.horizun-pbi-mcp]
command = "C:/ruta/a/python.exe"
args = ["C:/ruta/al/repositorio/src/server.py"]

[mcp_servers.horizun-pbi-mcp.env]
HORIZUN_PBI_MCP_LOG_LEVEL = "INFO"
```

Ambas rutas deben ser **absolutas**: Codex no expande `${VAR}` ni busca el intérprete por ti.

#### Comprobar

```bash
codex mcp list
```

Debe aparecer `horizun-pbi-mcp`. Si no, revisa que `horizun-pbi-mcp --help` funcione en la misma terminal desde la que lanzas Codex.

---

## 4. Verificación end-to-end

```bash
python -m pytest -q
```

```bash
python scripts/doctor.py --check-dax --check-pbip "tests/fixtures/synthetic/minimal/Demo.pbip"
```

`--check-dax` ejecuta `EVALUATE ROW("ok", 1, "probe", "doctor")` contra el modelo abierto: estrictamente de solo lectura. `--check-pbip` abre y valida un `.pbip` sin escribir nada en él.

Si Power BI Desktop no está abierto, el diagnóstico base **no falla**: marca esas comprobaciones como omitidas. Para exigir Desktop:

```bash
python scripts/doctor.py --require-desktop
```

---

## 5. Variables de entorno

Todas opcionales. Ver `.env.example`.

| Variable | Defecto | Para qué |
|---|---|---|
| `HORIZUN_PBI_MCP_LIBS_DIR` | `./libs` | Dónde están las DLLs |
| `HORIZUN_PBI_MCP_DOTNET_RUNTIME` | `netfx` | `netfx` o `coreclr` |
| `HORIZUN_PBI_MCP_MAX_ROWS` | `1000` | Límite de filas en DAX |
| `HORIZUN_PBI_MCP_COMMAND_TIMEOUT` | `120` | Timeout de comandos (s) |
| `HORIZUN_PBI_MCP_OUTPUTS_DIR` | `./outputs` | Documentación y `change_log.md` |
| `HORIZUN_PBI_MCP_BACKUPS_DIR` | `./backups` | Backups. **Apunta siempre fuera del `.pbip`** |
| `HORIZUN_PBI_MCP_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `HORIZUN_PBI_MCP_DEFAULT_PBIP` | — | `.pbip` a abrir al arrancar |

---

## 6. Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| `No se detecto ningun modelo` | Desktop cerrado, o puerto cambiado | El puerto cambia en cada arranque; se descubre solo. Abre el informe |
| `adomd_not_installed` / `tom_not_installed` | Faltan DLLs | `python scripts/fetch_libs.py` |
| `clr_not_available` | Falta .NET | Prueba `PBI_MCP_DOTNET_RUNTIME=coreclr` |
| `pbir_not_enabled` | El informe no está en PBIR | Guarda como `.pbip` con formato de reporte mejorado |
| Los cambios de visuales no aparecen | PBIR se carga al abrir | Cierra y reabre Desktop |
| Se perdieron cambios del informe | Desktop estaba abierto y guardó encima | Edita el PBIR **con Desktop cerrado**. Los backups están en `backups/` |
| El servidor arranca pero el cliente no lo ve | Ruta o intérprete mal en la config | `python scripts/make_mcp_config.py --client <tu-cliente>` y vuelve a pegar |
| Sesión apuntando a un puerto muerto | `outputs/session.json` obsoleto | `python scripts/doctor.py` lo detecta; borra el fichero o reselecciona |

---

## 7. Convivencia con otros MCP de Power BI

Los prefijos no chocan (`pbi_*` vs `pbir_*`), así que se pueden registrar varios servidores a la vez.

**Cuidado:** dos servidores escribiendo el mismo `.pbip` no se coordinan entre sí. Hasta que la Fase 1 añada bloqueo y detección de cambios externos, usa uno solo por proyecto a la vez. Ver [CAPABILITY_MATRIX.md](CAPABILITY_MATRIX.md).
