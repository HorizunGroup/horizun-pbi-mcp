"""Lanza el servidor real o un MCP mínimo que instala su runtime local."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import plugin_bootstrap as bootstrap

#: Versiones del protocolo MCP que este launcher sabe hablar, de la mas vieja
#: a la mas nueva. Se escriben a mano y NO se importan del SDK a proposito: el
#: bootstrap del bundle corre con cero dependencias -su pyproject declara
#: `dependencies = []`- y un `import mcp` aqui lo dejaria sin arrancar justo en
#: el primer inicio. Quien vigila que no se queden atras es
#: `tests/test_mcpb_distribution.py`, comparandolas con las del SDK.
#:
#: Aceptarlas todas es honesto: los cuatro metodos que implementa este servidor
#: -initialize, tools/list, tools/call y ping- son identicos en las cuatro.
VERSIONES_SOPORTADAS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

#: La que se ofrece cuando no se puede hablar la que pidieron.
PROTOCOL_VERSION = VERSIONES_SOPORTADAS[-1]


def negociar_version(pedida: Any) -> str:
    """La version del protocolo que se responde a `initialize`.

    La especificacion es explicita: si el servidor soporta la que pidio el
    cliente DEBE responder esa misma, y solo si no puede ofrece otra suya. Un
    cliente que recibe una version que no pidio ni entiende deberia cortar la
    conexion, asi que contestar siempre la propia -como se hacia- convierte un
    cliente perfectamente compatible en una extension que no arranca.
    """
    return pedida if pedida in VERSIONES_SOPORTADAS else PROTOCOL_VERSION


#: Tope del registro de instalacion antes de rotarlo. Existe porque el registro
#: es UNO para todas las versiones -tiene que vivir fuera de la carpeta que la
#: promocion renombra- y se abre en modo append: sin tope crece para siempre.
LIMITE_LOG = 2 * 1024 * 1024


def _rotar_log(log: Path) -> None:
    """Acota el registro conservando UNA vuelta anterior.

    Se guarda la anterior en vez de truncar porque la instalacion que falla se
    suele explicar en la de antes. Un registro que no se puede rotar no puede
    impedir instalar: eso seria cambiar un archivo grande por una extension que
    no arranca.
    """
    try:
        if log.is_file() and log.stat().st_size > LIMITE_LOG:
            os.replace(log, log.with_name(log.name + ".anterior"))
    except OSError as exc:                                    # pragma: no cover
        print(f"launcher: no se pudo rotar el registro: {exc}", file=sys.stderr)


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
    if status.get("state") in {"corrupt", "unreadable"}:
        # No se repara sobrescribiendo evidencia. La tool devuelve un error
        # util y deja el archivo exacto para que pueda inspeccionarse/apartarse.
        return status
    # `installing` a secas no basta: un instalador matado a media faena deja
    # ese estado escrito para siempre y nadie reintentaria.
    if bootstrap.instalacion_en_curso(status=status):
        return status
    p = bootstrap.paths()
    p["cache"].mkdir(parents=True, exist_ok=True)
    bootstrap._write_status(  # noqa: SLF001 - coordinacion del launcher hermano
        p, state="installing", ready=False, step="starting",
        message="Iniciando la preparación automática del runtime.")
    _rotar_log(p["log"])
    log = open(p["log"], "a", encoding="utf-8")
    command = [sys.executable, str(Path(__file__).with_name("plugin_bootstrap.py"))]
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": log,
                              "stderr": subprocess.STDOUT, "close_fds": True}
    # DETACHED_PROCESS ademas de CREATE_NO_WINDOW: la instalacion tiene que
    # sobrevivir a que el cliente reinicie el servidor MCP a media faena. El
    # precio es que el instalador se queda sin consola, y por eso CADA
    # subproceso suyo debe pedir su propio CREATE_NO_WINDOW.
    flags = bootstrap.flags_sin_ventana().get("creationflags", 0)
    if os.name == "nt":
        flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, creationflags=flags, **kwargs)
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
    pendiente = (status.get("state") == "not_installed" or
                 status.get("version") != bootstrap.VERSION or
                 (status.get("state") == "installing" and
                  not bootstrap.instalacion_en_curso(status=status)))
    if auto_install and pendiente:
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
            # Un BOM UTF-8 por linea no viene de un cliente MCP real, pero SI
            # de terminales Windows haciendo pruebas a mano (PowerShell 5.1 lo
            # anade al pipear). Tolerarlo cuesta una linea y evita un launcher
            # mudo justo cuando alguien intenta diagnosticarlo.
            request = json.loads(line.lstrip("\ufeff"))
            method = request.get("method")
            request_id = request.get("id")
            if method == "initialize":
                pedida = (request.get("params") or {}).get("protocolVersion")
                _result(request_id, {"protocolVersion": negociar_version(pedida),
                                     "capabilities": {"tools": {}},
                                     "serverInfo": {"name": "horizun-pbi-mcp-installer",
                                                    "version": bootstrap.VERSION}})
            elif method == "tools/list":
                _result(request_id, {"tools": tools})
            elif method == "tools/call":
                name = request.get("params", {}).get("name")
                if name == "pbi_install_runtime":
                    status = _start_install()
                    _result(request_id, _tool_text(
                        status, error=status.get("state") in
                        {"failed", "corrupt", "unreadable"}))
                elif name == "pbi_install_status":
                    status = bootstrap.read_status()
                    _result(request_id, _tool_text(
                        status, error=status.get("state") in
                        {"failed", "corrupt", "unreadable"}))
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


def _servir(seleccion: dict[str, Any]) -> int:
    """Ejecuta el runtime elegido heredando el stdio del cliente."""
    python = Path(seleccion["python"])
    # Las rutas de libs, esquemas y validador tienen que ser las DE ESA
    # version: si se sirve N−1, sus DLL y sus esquemas estan en su carpeta, no
    # en la de la actualizacion que fallo.
    p = bootstrap.paths(cache=python.parent.parent.parent)
    env = bootstrap.runtime_env(p)
    # `-m` en vez de la ruta del fichero: el paquete se instala con pip en el
    # entorno del plugin, asi que el arranque no depende de donde este el
    # arbol de fuentes.
    command = [str(python), "-m", "horizun_pbi_mcp.server"]
    # SIN creationflags, a proposito y medido: esta llamada no redirige el
    # stdio, asi que el hijo hereda el del cliente. Pedir CREATE_NO_WINDOW le
    # daria una consola NUEVA y el servidor leeria de ella en vez de las
    # tuberias del cliente: el handshake MCP se queda colgado para siempre.
    # Aqui no hay ventana que evitar: el cliente ya arranca al lanzador sin ella.
    return subprocess.call(command, cwd=str(bootstrap.PLUGIN_ROOT), env=env)


def main() -> int:
    """Verifica PRIMERO, entrega el canal DESPUES, y nunca al reves.

    INSTALL-012. Antes se ejecutaba el runtime activo heredandole el stdio del
    cliente y, si moria pronto con codigo distinto de cero, se arrancaba N−1
    sobre esa MISMA conexion. El argumento era que no habia llegado a escribir
    nada; nadie lo comprobaba, y no se podia comprobar: el hijo escribe
    directamente en el stdout del cliente, asi que este proceso no ve un solo
    byte de lo que emite. La duracion de un proceso no dice nada sobre lo que
    alcanzo a emitir, y un runtime que contestaba `initialize` y se caia a los
    dos segundos dejaba al cliente hablando con dos servidores por el mismo
    canal, con dos `serverInfo` y dos respuestas para el mismo `id`.

    `elegir_runtime_verificado()` hace el handshake en un proceso aparte, con
    tuberias propias. El stdin del cliente no se toca, asi que no hay peticiones
    consumidas que reproducir. Al que salga de ahi se le entrega el stdio, y a
    partir de ese momento no se arranca nada mas sobre esa conexion: si se cae,
    se devuelve su codigo y el fallback queda para la siguiente reconexion.
    """
    seleccion = bootstrap.elegir_runtime_verificado()

    if seleccion["modo"] == "last-known-good":
        print(f"launcher: la version {bootstrap.VERSION} no esta operativa; "
              f"sirviendo el ultimo runtime bueno ({seleccion['version']}). "
              "La causa esta en pbi_install_status, campo `degradacion`.",
              file=sys.stderr)
    if seleccion["python"]:
        return _servir(seleccion)

    return bootstrap_server()


if __name__ == "__main__":
    raise SystemExit(main())
