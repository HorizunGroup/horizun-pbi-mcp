"""Un runtime de mentira que ARRANCA de verdad.

Las pruebas del lanzador no pueden construir el runtime real -1 GB entre venv,
DLL de Analysis Services, esquemas PBIR y 586 archivos de npm- y tampoco pueden
sustituir al lanzador por una funcion: lo que hay que demostrar es que el
proceso real, hablando MCP por stdio, entrega las tools de la version que toca.

La salida: un venv de verdad (`--without-pip`, 0,07 s) con un paquete
`horizun_pbi_mcp` minimo dentro de su `site-packages`. El interprete es real, el
`-m horizun_pbi_mcp.server` resuelve de verdad, y el servidor contesta MCP por
stdio con el numero de tools que se le pida. Lo unico falso es lo que no se
esta midiendo.

Los NOMBRES de las tools salen del golden del contrato, no de un `range(134)`:
una prueba que dijera "134 tools" con nombres inventados no distinguiria un
runtime bueno de uno que registra basura, que es justo lo que INSTALL-010
tiene que separar.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GOLDEN = RAIZ / "tests" / "golden" / "tools_v1.json"

SERVIDOR_REAL = "horizun-pbi-mcp"


def nombres_del_contrato() -> list[str]:
    datos = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [t["name"] for t in datos["tools"]]


_PLANTILLA = '''"""Servidor MCP minimo por stdio. Solo biblioteca estandar."""
import json
import sys

SERVIDOR = {servidor!r}
VERSION = {version!r}
TOOLS = [{{"name": n, "description": n,
          "inputSchema": {{"type": "object", "properties": {{}}}}}}
         for n in {nombres!r}]
BASURA_EN_STDOUT = {basura!r}
BASURA_AL_CERRAR = {basura_al_cerrar!r}
MUERE_AL_ARRANCAR = {muere!r}
INVIERTE_EL_ORDEN = {invierte!r}
MUERE_TRAS_INITIALIZE = {muere_tras_initialize!r}
MUERE_TRAS_TOOLS = {muere_tras_tools!r}
LINEA_PARCIAL = {linea_parcial!r}


def _responder(payload):
    sys.stdout.write(json.dumps(payload) + "\\n")
    sys.stdout.flush()


def _initialize(ident):
    return {{"jsonrpc": "2.0", "id": ident, "result": {{
        "protocolVersion": "2024-11-05",
        "capabilities": {{"tools": {{}}}},
        "serverInfo": {{"name": SERVIDOR, "version": VERSION}}}}}}


def main():
    if MUERE_AL_ARRANCAR:
        sys.stderr.write("runtime falso: reventado a proposito\\n")
        raise SystemExit(3)
    if BASURA_EN_STDOUT:
        sys.stdout.write("esto no es JSON-RPC\\n")
        sys.stdout.flush()
    if LINEA_PARCIAL:
        # Media linea JSON-RPC y se muere: el cliente ya tiene bytes.
        sys.stdout.write('{{"jsonrpc":"2.0","id":1,"resu')
        sys.stdout.flush()
        raise SystemExit(4)

    pendiente_initialize = None
    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            peticion = json.loads(linea)
        except ValueError:
            continue
        metodo, ident = peticion.get("method"), peticion.get("id")
        if metodo == "initialize":
            if INVIERTE_EL_ORDEN:
                # Se guarda para contestar DESPUES de tools/list. Las dos
                # respuestas son validas; solo llegan al reves.
                pendiente_initialize = ident
                continue
            _responder(_initialize(ident))
            if MUERE_TRAS_INITIALIZE:
                sys.stdout.flush()
                raise SystemExit(5)
        elif metodo == "tools/list":
            _responder({{"jsonrpc": "2.0", "id": ident,
                        "result": {{"tools": TOOLS}}}})
            if pendiente_initialize is not None:
                _responder(_initialize(pendiente_initialize))
                pendiente_initialize = None
            if MUERE_TRAS_TOOLS:
                sys.stdout.flush()
                raise SystemExit(6)
        elif ident is not None:
            _responder({{"jsonrpc": "2.0", "id": ident,
                        "error": {{"code": -32601, "message": metodo}}}})

    if BASURA_AL_CERRAR:
        # Lo que nadie miraba: ruido DESPUES de tools/list, al apagarse.
        # Un `print` de despedida, un atexit, un aviso de una libreria.
        sys.stdout.write("apagando el servidor...\\n")
        sys.stdout.flush()


main()
'''


def _site_packages(python: Path) -> Path:
    salida = subprocess.run(
        [str(python), "-c",
         "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, timeout=120, check=True).stdout.strip()
    return Path(salida)


def escribir_stub(python: Path, *, version: str, servidor: str = SERVIDOR_REAL,
                  nombres: list[str] | None = None, basura: bool = False,
                  muere: bool = False, basura_al_cerrar: bool = False,
                  invierte: bool = False, muere_tras_initialize: bool = False,
                  muere_tras_tools: bool = False,
                  linea_parcial: bool = False) -> Path:
    """(Re)escribe el `horizun_pbi_mcp.server` de un runtime ya existente.

    Va aparte de `crear` porque la instalacion real no siempre construye el
    venv: si hay un runtime anterior en disco, `_semilla` lo COPIA al staging y
    el paso de `venv` no llega a ejecutarse. Una prueba que solo plantara el
    servidor en el paso de `venv` acabaria midiendo la version anterior
    creyendo que mide la nueva.
    """
    paquete = _site_packages(python) / "horizun_pbi_mcp"
    paquete.mkdir(parents=True, exist_ok=True)
    (paquete / "__init__.py").write_text("", encoding="utf-8")
    (paquete / "server.py").write_text(
        _PLANTILLA.format(servidor=servidor, version=version,
                          nombres=nombres if nombres is not None
                          else nombres_del_contrato(),
                          basura=basura, muere=muere,
                          basura_al_cerrar=basura_al_cerrar,
                          invierte=invierte,
                          muere_tras_initialize=muere_tras_initialize,
                          muere_tras_tools=muere_tras_tools,
                          linea_parcial=linea_parcial),
        encoding="utf-8")
    return python


def crear(carpeta: Path, *, version: str, sin_entry_points: bool = False,
          **stub) -> Path:
    """Deja en `carpeta` un runtime con la forma que espera el ciclo de vida.

    Devuelve la ruta del interprete. `carpeta` es la de una version
    (`<root>/1.5.4`), no la del venv: dentro va `runtime/`, igual que en la
    instalacion real.
    """
    runtime = carpeta / "runtime"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(runtime)],
                   check=True, capture_output=True, timeout=300)
    scripts = runtime / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")

    if not sin_entry_points:
        sufijo = ".exe" if os.name == "nt" else ""
        for nombre in ("horizun-pbi-mcp", "powerbi-mcp"):
            (scripts / f"{nombre}{sufijo}").write_bytes(b"")

    escribir_stub(python, version=version, **stub)

    # Lo que acompaña a un runtime instalado; nada de esto lo mira el lanzador,
    # pero su ausencia haria que la prueba pareciera mas limpia de lo que es.
    for resto in ("libs", "schemas/pbir", "validator"):
        (carpeta / resto).mkdir(parents=True, exist_ok=True)
    return python


def hablar_mcp(comando: list[str], *, env: dict, timeout: int = 120) -> dict:
    """initialize + notifications/initialized + tools/list contra un proceso real.

    Devuelve `{"initialize": ..., "tools": [...], "stderr": ..., "rc": ...}`.
    """
    peticiones = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "prueba", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    entrada = "".join(json.dumps(p) + "\n" for p in peticiones)
    proc = subprocess.run(comando, input=entrada, capture_output=True,
                          text=True, timeout=timeout, env=env)

    mensajes = []
    for linea in proc.stdout.splitlines():
        if not linea.strip():
            continue
        try:
            mensajes.append(json.loads(linea))
        except ValueError:
            mensajes.append({"_no_json": linea})
    inicial = next((m for m in mensajes if m.get("id") == 1), None)
    lista = next((m for m in mensajes if m.get("id") == 2), None)
    return {
        "initialize": (inicial or {}).get("result"),
        "servidor": ((inicial or {}).get("result") or {}).get("serverInfo") or {},
        "tools": [t.get("name") for t in
                  ((lista or {}).get("result") or {}).get("tools") or []],
        "mensajes": mensajes,
        "stdout_crudo": proc.stdout,
        "stderr": proc.stderr,
        "rc": proc.returncode,
    }


def mezcla(sesion: dict) -> dict:
    """Señales de que MAS DE UN servidor escribio en el mismo canal.

    Ninguna de las tres es una heuristica: cada una es imposible en una sesion
    con un solo servidor MCP correcto.

      - dos respuestas para el mismo `id`: el protocolo empareja peticion y
        respuesta por id, y un servidor contesta cada una una vez;
      - dos `serverInfo` distintos: solo hay un `initialize` por conexion;
      - lineas que no son JSON-RPC: el canal es exclusivamente JSON-RPC.
    """
    from collections import Counter

    por_id = Counter(m["id"] for m in sesion["mensajes"]
                     if isinstance(m, dict) and m.get("id") is not None)
    identidades = set()
    for m in sesion["mensajes"]:
        info = ((m or {}).get("result") or {}).get("serverInfo")
        if isinstance(info, dict):
            identidades.add((info.get("name"), info.get("version")))
    return {
        "ids_repetidos": {i: n for i, n in por_id.items() if n > 1},
        "identidades": identidades,
        "lineas_no_json": [m["_no_json"] for m in sesion["mensajes"]
                           if isinstance(m, dict) and "_no_json" in m],
    }
