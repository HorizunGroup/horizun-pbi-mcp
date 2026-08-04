"""Genera la configuracion MCP de Horizun PBI MCP para distintos clientes.

Existe porque NO hay una forma portable comun: cada cliente expande variables
de entorno, resuelve el directorio de trabajo y encuentra Python de forma
distinta. En vez de apostar por `${VAR}` y que falle en la mitad de ellos,
este script resuelve las rutas absolutas AQUI y emite un fragmento que ya
funciona en la maquina donde se ejecuta.

Por defecto SOLO IMPRIME. Nunca toca la configuracion real del usuario:
`--write` esta permitido unicamente para el `.mcp.json` DENTRO de este
repositorio; los ficheros globales de Claude Desktop o Codex jamas se
modifican, se muestran para que los pegues tu.

Uso:
    python scripts/make_mcp_config.py --client claude-code
    python scripts/make_mcp_config.py --client claude-desktop
    python scripts/make_mcp_config.py --client codex
    python scripts/make_mcp_config.py --client generic
    python scripts/make_mcp_config.py --client all
    python scripts/make_mcp_config.py --client claude-code --write   # solo .mcp.json local
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = PROJECT_ROOT / "src" / "horizun_pbi_mcp" / "server.py"
SRC_DIR = PROJECT_ROOT / "src"

#: El servidor se arranca como MODULO, no por la ruta del fichero. Ejecutar
#: `python src/horizun_pbi_mcp/server.py` pone en sys.path el directorio DEL
#: FICHERO (src/horizun_pbi_mcp/), no `src/`, asi que `import horizun_pbi_mcp`
#: no resuelve en un clon limpio. En la maquina de quien lo desarrolla suele
#: funcionar igualmente, porque una instalacion editable deja un .pth con
#: `src/` dentro: exactamente la clase de fallo que solo aparece en casa de
#: otro. Con `-m` mas PYTHONPATH funciona en ambos casos.
SERVER_ARGS = ["-m", "horizun_pbi_mcp.server"]
SERVER_NAME = "horizun-pbi-mcp"


def _json_path(p: Path) -> str:
    """Ruta con barras normales: valida en JSON en Windows sin escapes dobles."""
    return str(p).replace("\\", "/")


def _python_exe(override: str | None) -> str:
    return override or _json_path(Path(sys.executable))


def _env_block() -> Dict[str, str]:
    # Prefijo actual. Las PBI_MCP_* siguen funcionando con menor precedencia.
    return {"HORIZUN_PBI_MCP_LOG_LEVEL": "INFO",
            "PYTHONPATH": _json_path(SRC_DIR)}


# --------------------------------------------------------------- clientes ----
def cfg_claude_code(python_exe: str) -> str:
    payload = {
        "mcpServers": {
            SERVER_NAME: {
                "type": "stdio",
                "command": python_exe,
                "args": list(SERVER_ARGS),
                "env": _env_block(),
            }
        }
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def cfg_claude_desktop(python_exe: str) -> str:
    payload = {
        "mcpServers": {
            SERVER_NAME: {
                "command": python_exe,
                "args": list(SERVER_ARGS),
                "env": _env_block(),
            }
        }
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def cfg_codex(python_exe: str) -> str:
    env_lines = "\n".join(f'{k} = "{v}"' for k, v in _env_block().items())
    return (
        f"[mcp_servers.{SERVER_NAME}]\n"
        f'command = "{python_exe}"\n'
        f'args = ["{_json_path(SERVER_PY)}"]\n'
        f"\n"
        f"[mcp_servers.{SERVER_NAME}.env]\n"
        f"{env_lines}\n"
    )


def cfg_generic(python_exe: str) -> str:
    payload = {
        "name": SERVER_NAME,
        "transport": "stdio",
        "command": python_exe,
        "args": list(SERVER_ARGS),
        "env": _env_block(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


NOTES = {
    "claude-code": {
        "file": ".mcp.json en la raiz del proyecto (o ~/.claude.json para todos)",
        "expands_vars": "Si, admite ${VAR}. Aun asi este script emite rutas "
                        "absolutas: funcionan siempre, sin depender de la expansion.",
        "cwd": "Hereda el directorio desde el que arrancaste Claude Code. "
               "El servidor no depende de ello: resuelve todo desde src/config.py.",
        "python": "No busca Python: usa exactamente el 'command' que le des.",
        "env": "Objeto 'env' dentro de la entrada del servidor.",
        "verify": "En Claude Code: /mcp   (debe aparecer 'horizun-pbi-mcp' conectado)",
    },
    "claude-desktop": {
        "file": r"%APPDATA%\Claude\claude_desktop_config.json",
        "expands_vars": "NO asumas que expande ${VAR}: usa rutas absolutas "
                        "(es lo que emite este script).",
        "cwd": "No configurable; no lo necesites.",
        "python": "No busca Python: usa el 'command' literal. Si usas un venv, "
                  "apunta al python.exe DEL VENV.",
        "env": "Objeto 'env' dentro de la entrada del servidor.",
        "verify": "Reinicia Claude Desktop y busca el servidor en el panel de "
                  "herramientas.",
    },
    "codex": {
        "file": "~/.codex/config.toml  (formato TOML, no JSON)",
        "expands_vars": "No lo asumas: rutas absolutas.",
        "cwd": "Hereda el del proceso de Codex.",
        "python": "Usa el 'command' literal.",
        "env": "Tabla [mcp_servers.<nombre>.env].",
        "verify": "Lista los servidores MCP desde Codex tras reiniciarlo.",
    },
    "generic": {
        "file": "el que use tu cliente MCP por stdio",
        "expands_vars": "Desconocido: por eso se emiten rutas absolutas.",
        "cwd": "Irrelevante para este servidor.",
        "python": "Usa el 'command' literal.",
        "env": "Segun el cliente.",
        "verify": "python scripts/doctor.py   (comprueba que el servidor arranca)",
    },
}

BUILDERS = {
    "claude-code": cfg_claude_code,
    "claude-desktop": cfg_claude_desktop,
    "codex": cfg_codex,
    "generic": cfg_generic,
}


def emit(client: str, python_exe: str) -> str:
    note = NOTES[client]
    body = BUILDERS[client](python_exe)
    sep = "=" * 74
    return (
        f"\n{sep}\n  {client}\n{sep}\n"
        f"  archivo            : {note['file']}\n"
        f"  expande variables  : {note['expands_vars']}\n"
        f"  directorio trabajo : {note['cwd']}\n"
        f"  como halla Python  : {note['python']}\n"
        f"  variables de entorno: {note['env']}\n"
        f"  comprobacion       : {note['verify']}\n"
        f"{'-' * 74}\n{body}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client", required=True,
                    choices=[*BUILDERS.keys(), "all"],
                    help="Cliente MCP de destino.")
    ap.add_argument("--python", metavar="PATH",
                    help="Ruta al interprete Python a usar (por defecto, el actual).")
    ap.add_argument("--write", action="store_true",
                    help="Escribe .mcp.json EN ESTE REPOSITORIO. No toca ninguna "
                         "configuracion global del usuario.")
    args = ap.parse_args()

    if not SERVER_PY.exists():
        print(f"ERROR: no se encuentra {SERVER_PY}", file=sys.stderr)
        return 2

    python_exe = _python_exe(args.python)
    if not Path(python_exe).exists():
        print(f"AVISO: el interprete '{python_exe}' no existe en disco.", file=sys.stderr)

    clients = list(BUILDERS) if args.client == "all" else [args.client]
    for c in clients:
        print(emit(c, python_exe))

    if args.write:
        if args.client not in ("claude-code", "all"):
            print("ERROR: --write solo aplica a 'claude-code' (.mcp.json local). "
                  "Los ficheros globales se pegan a mano, a proposito.",
                  file=sys.stderr)
            return 2
        target = PROJECT_ROOT / ".mcp.json"
        if target.exists():
            print(f"\nERROR: {target} ya existe; no se sobrescribe. "
                  "Borralo tu si quieres regenerarlo.", file=sys.stderr)
            return 2
        target.write_text(cfg_claude_code(python_exe) + "\n", encoding="utf-8")
        print(f"\nEscrito: {target}")
        print("  (esta en .gitignore: es TU configuracion local, no se versiona)")
        print("  Reinicia Claude Code en esta carpeta y comprueba con /mcp")
    else:
        print("\nNada se ha escrito. Para crear el .mcp.json local de este repo:")
        print("  python scripts/make_mcp_config.py --client claude-code --write")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
