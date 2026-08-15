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

#: El contrato EMPAQUETADO: los nombres de tool que un runtime tiene que servir
#: y el nombre exacto del servidor. Vive junto a este modulo, o sea dentro del
#: wheel, para que el oraculo funcione tambien donde no existe `tests/`. Se
#: genera desde el golden con `python -m tests.contract_utils --write`, y
#: `tests/test_contrato_empaquetado.py` vigila que los dos digan lo mismo.
BASELINE = Path(__file__).with_name("contract_baseline.json")

_ARRANQUE = "from horizun_pbi_mcp import server; server.main()"


def contrato() -> dict[str, Any]:
    """El contrato empaquetado. Si falta, no se puede comprobar nada."""
    datos = json.loads(BASELINE.read_text(encoding="utf-8"))
    if not isinstance(datos.get("tools"), list) or not datos["tools"]:
        raise ValueError(f"{BASELINE} no lleva la lista de tools")
    return datos


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
              version_esperada: str | None = None,
              baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Lanza el servidor y habla MCP con el. Nunca lanza: devuelve el veredicto.

    Devolver en vez de lanzar es deliberado: quien llama tiene que poder
    distinguir "no arranco" de "arranco y sirve poco" y guardarlo en el estado,
    y una excepcion generica perderia esa diferencia.

    Lo que se exige, y por que cada cosa. El criterio anterior era *cien tools
    cualesquiera cuyo nombre empiece por `pbi_`*, con el contrato en 134. Eso
    aceptaba tres runtimes rotos distintos: uno al que le faltan 34 tools, uno
    que sirve 134 nombres que no son los del producto, y cualquiera de otra
    version. Ahora se comprueba el contrato: el nombre EXACTO del servidor, la
    version que se esperaba, y que no falte NINGUNA de las tools del baseline.
    Las de mas se admiten -son compatibles hacia atras y no rompen a ningun
    cliente-; las que faltan, no.
    """
    resultado: dict[str, Any] = {"ok": False, "fase": "arranque", "tools": 0}

    try:
        contrato_esperado = baseline if baseline is not None else contrato()
    except (OSError, ValueError) as exc:
        # Fail-closed: sin contrato no se puede comprobar, y "no se pudo
        # comprobar" no puede valer lo mismo que "esta bien".
        resultado.update(fase="contrato",
                         error=f"no se pudo leer el contrato empaquetado: {exc}")
        return resultado
    esperadas = set(contrato_esperado["tools"])

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
        # `finally` y no un `return` por rama: el hilo tiene que despertar al
        # principal TAMBIEN cuando stdout se cierra sin haber contestado, que es
        # lo que pasa cuando el runtime se muere al arrancar. Sin esto, un
        # proceso que revienta en el primer import costaba el TIMEOUT entero
        # -tres minutos- para acabar diciendo lo que ya se sabia en el primer
        # segundo, y multiplicado por cada intento de una instalacion.
        try:
            for linea in proc.stdout:
                if not linea.strip():
                    continue
                lineas.append(linea)
                try:
                    if json.loads(linea).get("id") == 2:
                        return
                except ValueError:
                    return                        # stdout sucio: no hay mas que ver
        finally:
            visto_el_final.set()

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

    # Cerrar stdin es la señal de apagado del transporte stdio. Un servidor que
    # no la atiende deja un proceso por cada arranque del cliente, y esos
    # procesos siguen ahi con el runtime abierto. Se le da un plazo, se le mata
    # si no lo cumple, y **siempre** se le hace `wait`: sin recoger al hijo
    # queda un zombie, que es exactamente el huerfano que se queria evitar.
    ignoro_el_cierre = False
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        ignoro_el_cierre = True
        proc.kill()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:          # pragma: no cover
            resultado.update(fase="proceso-inmatable",
                             error=f"el runtime (pid {proc.pid}) no murio ni "
                                   "con kill; queda un proceso suelto")
            return resultado
    lector.join(timeout=5)
    try:
        errores = proc.stderr.read() or ""
    except (OSError, ValueError):                  # pragma: no cover
        errores = ""
    finally:
        for flujo in (proc.stdout, proc.stderr):
            try:
                flujo.close()
            except (OSError, ValueError):          # pragma: no cover
                pass

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
    resultado.update(servidor=info.get("name"), version=info.get("version"))

    # El servidor tiene que ser EL nuestro. Aceptar cualquier `serverInfo` daba
    # por bueno un venv en el que hubiera quedado instalado otro servidor MCP.
    if info.get("name") != contrato_esperado["server"]:
        resultado.update(
            fase="server-info",
            error=(f"serverInfo.name es {info.get('name')!r} y el contrato dice "
                   f"{contrato_esperado['server']!r}"))
        return resultado
    if version_esperada is not None and info.get("version") != version_esperada:
        resultado.update(
            fase="version",
            error=(f"el runtime dice ser {info.get('version')!r} y se acaba de "
                   f"preparar {version_esperada!r}"))
        return resultado

    lista = next((m for m in mensajes if m.get("id") == 2), None)
    if not lista or "result" not in lista:
        resultado.update(fase="tools-list",
                         error="tools/list no devolvio result")
        return resultado

    tools = lista["result"].get("tools")
    if not isinstance(tools, list) or not all(
            isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
            for t in tools):
        resultado.update(fase="tools-list-malformado",
                         error="tools/list no devolvio una lista de tools con "
                               "nombre: el cliente no podria usarlas")
        return resultado

    nombres = {t["name"] for t in tools}
    resultado["tools"] = len(tools)
    resultado["fase"] = "completo"

    faltan = sorted(esperadas - nombres)
    if faltan:
        resultado["faltan"] = faltan[:10]
        resultado["error"] = (
            f"faltan {len(faltan)} de las {len(esperadas)} tools del contrato "
            f"(sirve {len(tools)}): {', '.join(faltan[:5])}"
            + (" ..." if len(faltan) > 5 else ""))
        return resultado

    # Las de mas se admiten a proposito: añadir una tool no rompe a ningun
    # cliente ya configurado, y prohibirlas convertiria cada ampliacion del
    # producto en una instalacion fallida.
    resultado["extra"] = sorted(nombres - esperadas)

    if ignoro_el_cierre:
        resultado.update(
            fase="no-termina",
            error="el runtime contesto pero no termino al cerrarse stdin; hubo "
                  "que matarlo. Asi dejaria un proceso vivo por cada arranque "
                  "del cliente.")
        return resultado
    if proc.returncode != 0:
        resultado["error"] = f"el runtime salio con codigo {proc.returncode}"
        return resultado

    resultado["ok"] = True
    return resultado
