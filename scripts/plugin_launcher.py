"""Lanza el servidor real o un MCP mínimo que instala su runtime local."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import plugin_bootstrap as bootstrap

PROTOCOL_VERSION = "2025-11-25"


def _reply(payload: dict[str, Any]) -> None:
    # JSON ASCII evita depender de la pagina de codigos de la consola Windows.
    sys.stdout.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _result(request_id: Any, result: Any) -> None:
    _reply({"jsonrpc": "2.0", "id": request_id, "result": result})


def _tool_text(data: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": error}


def _start_install() -> dict[str, Any]:
    status = bootstrap.read_status()
    if status.get("state") == "installing":
        return status
    p = bootstrap.paths()
    p["root"].mkdir(parents=True, exist_ok=True)
    bootstrap._write_status(  # noqa: SLF001 - coordinacion del launcher hermano
        p, state="installing", ready=False, step="starting",
        message="Iniciando la preparación automática del runtime.")
    log = open(p["log"], "a", encoding="utf-8")
    command = [sys.executable, str(Path(__file__).with_name("plugin_bootstrap.py"))]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": log,
                              "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                                   getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        bootstrap._write_status(  # noqa: SLF001
            p, state="failed", ready=False,
            message=f"No se pudo iniciar la instalación: {type(exc).__name__}: {exc}")
        raise
    finally:
        log.close()
    return {"state": "installing", "ready": False, "pid": process.pid,
            "data_dir": str(p["root"]), "log": str(p["log"]),
            "message": "Instalación iniciada. Consulta pbi_install_status."}


def bootstrap_server() -> int:
    status = bootstrap.read_status()
    auto_install = os.environ.get("HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL") != "1"
    if auto_install and (status.get("state") == "not_installed" or
                         status.get("version") != bootstrap.VERSION):
        try:
            _start_install()
        except Exception as exc:
            print(f"launcher: no se pudo iniciar el instalador: {exc}", file=sys.stderr)

    tools = [
        {"name": "pbi_install_runtime",
         "description": "Prepara en segundo plano el runtime local aislado y verificado del plugin.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
        {"name": "pbi_install_status",
         "description": "Informa el avance o el error de la instalación del runtime local.",
         "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    ]
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                _result(request_id, {"protocolVersion": PROTOCOL_VERSION,
                                     "capabilities": {"tools": {}},
                                     "serverInfo": {"name": "horizun-pbi-mcp-installer",
                                                    "version": bootstrap.VERSION}})
            elif method == "tools/list":
                _result(request_id, {"tools": tools})
            elif method == "tools/call":
                name = request.get("params", {}).get("name")
                if name == "pbi_install_runtime":
                    _result(request_id, _tool_text(_start_install()))
                elif name == "pbi_install_status":
                    status = bootstrap.read_status()
                    _result(request_id, _tool_text(status, error=status.get("state") == "failed"))
                else:
                    _reply({"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32601, "message": "Tool desconocida"}})
            elif method == "ping":
                _result(request_id, {})
            elif request_id is not None:
                _reply({"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32601, "message": "Método desconocido"}})
        except Exception as exc:
            print(f"launcher: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


def main() -> int:
    status = bootstrap.read_status()
    p = bootstrap.paths()
    if status.get("ready") and status.get("version") == bootstrap.VERSION and p["python"].is_file():
        env = bootstrap.runtime_env(p)
        command = [str(p["python"]), str(bootstrap.PLUGIN_ROOT / "src/server.py")]
        if os.name == "nt":
            return subprocess.call(command, cwd=str(bootstrap.PLUGIN_ROOT), env=env)
        os.execve(str(p["python"]), command, env)
    return bootstrap_server()


if __name__ == "__main__":
    raise SystemExit(main())
