"""INSTALL-010 — `ready` solo despues de un handshake MCP real.

El defecto: `install()` escribia `state=ready` porque ninguno de sus pasos habia
lanzado una excepcion. Eso demuestra que pip no fallo y que las descargas
cuadraron de hash; no demuestra que el servidor **arranque**. Un venv al que le
falte una dependencia transitiva, un `mcp` incompatible o un modulo que no
importa dan exactamente el mismo `ready`, y el fallo aparece mucho despues, en
el cliente, con un mensaje que no menciona la instalacion.

Es el mismo defecto de forma que TEST-001 y RELEASE-001: dar por buena una senal
mas estrecha de lo que aparenta. Aqui el oraculo tiene que ser el protocolo
mismo, no la ausencia de excepciones.

**Se ejecuta contra el STAGING, antes de promover.** El enunciado original lo
pedia antes de escribir `ready`, que habria significado promover y despues
revertir si fallaba. Comprobarlo antes es estrictamente mejor: un runtime que no
arranca nunca llega a sustituir al que si funcionaba, y no hace falta rollback
porque no hubo cambio que deshacer.

Solo biblioteca estandar: esto corre desde `plugin_bootstrap` con el Python
anfitrion, y tambien desde la CLI empaquetada.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

#: Arranque en frio de un venv nuevo en Windows: importar pythonnet y construir
#: 17 modulos de tools no es instantaneo. Generoso a proposito -un timeout corto
#: convertiria una maquina lenta en una instalacion fallida-, pero acotado: sin
#: tope, un servidor que se cuelga dejaria la instalacion colgada con el.
TIMEOUT = 180

#: Por debajo de esto el servidor arranco pero no registro sus modulos, que es
#: un fallo distinto de "no arranca" y merece decirse aparte.
MINIMO_TOOLS = 100

_ARRANQUE = "from horizun_pbi_mcp import server; server.main()"


def _flags_sin_ventana() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}


def entry_points(runtime: Path) -> list[Path]:
    scripts = runtime / ("Scripts" if os.name == "nt" else "bin")
    sufijo = ".exe" if os.name == "nt" else ""
    return [scripts / f"horizun-pbi-mcp{sufijo}", scripts / f"powerbi-mcp{sufijo}"]


def verificar(python: Path, *, env: dict[str, str] | None = None,
              cwd: Path | None = None, timeout: int = TIMEOUT,
              minimo_tools: int = MINIMO_TOOLS) -> dict[str, Any]:
    """Lanza el servidor y habla MCP con el. Nunca lanza: devuelve el veredicto.

    Devolver en vez de lanzar es deliberado: quien llama tiene que poder
    distinguir "no arranco" de "arranco y sirve poco" y guardarlo en el estado,
    y una excepcion generica perderia esa diferencia.
    """
    resultado: dict[str, Any] = {"ok": False, "fase": "arranque", "tools": 0}

    if not python.is_file():
        resultado["error"] = f"no hay interprete en {python}"
        return resultado

    faltan = [str(p) for p in entry_points(python.parent.parent) if not p.exists()]
    if faltan:
        resultado.update(fase="entry-points",
                         error=f"pip no instalo los ejecutables de consola: {faltan}")
        return resultado

    entorno = dict(os.environ if env is None else env)
    # El checkout no puede rescatar a un runtime roto: si `src/` esta en el
    # path, esto mediria el repositorio y no lo que se acaba de construir.
    entorno["PYTHONPATH"] = ""
    entorno.setdefault("PBI_MCP_LOG_LEVEL", "ERROR")

    try:
        proc = subprocess.Popen(
            [str(python), "-c", _ARRANQUE],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            cwd=str(cwd) if cwd else None, env=entorno, **_flags_sin_ventana())
    except OSError as exc:
        resultado["error"] = f"no se pudo lanzar el runtime: {exc}"
        return resultado

    peticiones = (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "horizun-healthcheck", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    # NO se usa `communicate()`, y el motivo lo encontro un ensayo real: cierra
    # stdin en cuanto termina de escribir, y el bucle stdio del servidor puede
    # ver el EOF y apagarse ANTES de procesar la ultima peticion. El sintoma es
    # un `initialize` correcto seguido de un `tools/list` sin respuesta, sobre un
    # runtime que momentos antes servia 134 tools.
    #
    # Eso no es un fallo cosmetico: seria un FALSO NEGATIVO, y un falso negativo
    # aqui rechaza un runtime bueno y tumba una instalacion que iba bien. Peor
    # que el defecto original, que al menos fallaba hacia el lado optimista.
    #
    # Se arregla sincronizando por EVENTO -leer hasta ver la respuesta que se
    # espera- y no ampliando el plazo. El plazo sigue existiendo, pero ahora
    # acusa a lo que tiene que acusar: un servidor que de verdad no contesta.
    import threading

    lineas: list[str] = []
    visto_el_final = threading.Event()

    def _leer() -> None:
        for linea in proc.stdout:
            if linea.strip():
                lineas.append(linea)
                try:
                    if json.loads(linea).get("id") == 2:
                        visto_el_final.set()
                        return
                except ValueError:
                    visto_el_final.set()          # stdout sucio: no hay mas que ver
                    return

    lector = threading.Thread(target=_leer, daemon=True)
    lector.start()
    try:
        for peticion in peticiones:
            proc.stdin.write(json.dumps(peticion) + "\n")
            proc.stdin.flush()
    except OSError:
        pass                                       # murio al arrancar; se ve abajo

    respondio = visto_el_final.wait(timeout)
    errores = ""
    try:
        proc.stdin.close()
    except OSError:
        pass
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
    try:
        errores = proc.stderr.read() or ""
    except (OSError, ValueError):                  # pragma: no cover
        errores = ""

    if not respondio and not lineas:
        resultado.update(fase="timeout",
                         error=f"el runtime no respondio en {timeout}s",
                         stderr=errores[-1500:])
        return resultado

    lineas = [l for l in lineas if l.strip()]
    if not lineas:
        resultado.update(
            fase="sin-respuesta",
            error="el runtime no escribio nada por stdout",
            stderr=(errores or "")[-1500:])
        return resultado

    # stdout es el canal JSON-RPC: un `print` de depuracion lo rompe, y esa es
    # una de las formas reales de romper un cliente MCP sin romper una prueba.
    mensajes = []
    for linea in lineas:
        try:
            mensajes.append(json.loads(linea))
        except ValueError:
            resultado.update(
                fase="stdout-sucio",
                error=f"stdout lleva algo que no es JSON-RPC: {linea[:200]!r}",
                stderr=(errores or "")[-1500:])
            return resultado

    inicial = next((m for m in mensajes if m.get("id") == 1), None)
    if not inicial or "result" not in inicial:
        resultado.update(fase="initialize",
                         error=f"initialize no devolvio result: {inicial}")
        return resultado

    info = inicial["result"].get("serverInfo") or {}
    lista = next((m for m in mensajes if m.get("id") == 2), None)
    if not lista or "result" not in lista:
        resultado.update(fase="tools-list", servidor=info.get("name"),
                         error="tools/list no devolvio result")
        return resultado

    tools = lista["result"].get("tools") or []
    nombres = [t.get("name", "") for t in tools]
    resultado.update(fase="completo", tools=len(tools),
                     servidor=info.get("name"), version=info.get("version"))

    if len(tools) < minimo_tools:
        resultado["error"] = (
            f"el servidor arranco pero solo registro {len(tools)} tools "
            f"(minimo {minimo_tools}): faltan modulos")
        return resultado
    if not all(n.startswith("pbi_") for n in nombres):
        resultado["error"] = "hay tools que no son del producto"
        return resultado
    if proc.returncode not in (0, None):
        resultado["error"] = f"el runtime salio con codigo {proc.returncode}"
        return resultado

    resultado["ok"] = True
    return resultado
