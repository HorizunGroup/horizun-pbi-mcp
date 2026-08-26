"""Lanza el helper de interfaz y le impone un plazo DURO.

El intento anterior corria COM en un hilo demonio y hacia `join(timeout)`. Eso
devuelve el control, pero no cancela nada: el hilo se queda dentro de COM para
siempre y el servidor acumula uno por intento. Aqui el plazo lo impone el
sistema operativo -se termina el proceso- y con el se va todo lo que tuviera
tomado.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Dict, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("desktop_helper")

#: Modulo que se ejecuta en el proceso auxiliar.
MODULO_HELPER = "horizun_pbi_mcp.powerbi.uia_helper"


class DesktopHelperError(PowerBIMCPError):
    code = "desktop_helper_failed"


class DesktopHelperTimeout(DesktopHelperError):
    """El helper no respondio y se termino. NO quedan hilos COM vivos."""

    code = "desktop_helper_timeout"


class DesktopHelperUnavailable(DesktopHelperError):
    """Falta lo necesario para conducir la interfaz."""

    code = "desktop_helper_unavailable"


def comtypes_disponible() -> Dict[str, Any]:
    """Si el extra de exportacion esta instalado. No importa comtypes aqui.

    Se comprueba con `importlib.util.find_spec`, que NO ejecuta el modulo:
    importar `comtypes` inicializa COM en este proceso, y eso es justo lo que
    este diseño saca fuera.
    """
    import importlib.util

    if os.name != "nt":
        return {"available": False, "reason": "solo existe en Windows",
                "platform": os.name}
    try:
        encontrado = importlib.util.find_spec("comtypes") is not None
    except (ImportError, ValueError):                     # pragma: no cover
        encontrado = False
    if encontrado:
        return {"available": True}
    return {
        "available": False,
        "reason": "falta el paquete 'comtypes'",
        "install": 'pip install "horizun-pbi-mcp[export]"',
        "detail": ("Exportar a .pbix conduce el cuadro de guardado de Power BI "
                   "Desktop por UI Automation, que es COM. El desplegable de "
                   "TIPO no se puede mover con mensajes Win32: se comprobo, y "
                   "el archivo salia guardado como .pbip."),
    }


def ejecutar(peticion: Dict[str, Any], *, timeout: float) -> Dict[str, Any]:
    """Corre el helper con plazo duro y devuelve su respuesta ya parseada."""
    estado = comtypes_disponible()
    if not estado["available"]:
        raise DesktopHelperUnavailable(
            estado.get("detail", "No se puede conducir la interfaz aqui.")
            + " " + estado.get("install", ""),
            details={"capability": "pbix_export", **estado})

    entrada = json.dumps(peticion, ensure_ascii=False, default=str)
    entorno = dict(os.environ)
    # El helper no debe heredar rastros del canal JSON-RPC del servidor.
    entorno.pop("PYTHONSTARTUP", None)
    entorno["PYTHONIOENCODING"] = "utf-8"

    try:
        completado = subprocess.run(
            [sys.executable, "-m", MODULO_HELPER],
            input=entrada, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=entorno,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        # `subprocess.run` ya mato el proceso al agotarse el plazo. Es la
        # diferencia con el hilo: aqui SI se cancela.
        raise DesktopHelperTimeout(
            f"El asistente de interfaz no respondio en {timeout:.0f} s y se "
            "termino. No queda ninguna llamada COM viva en el servidor.",
            details={"timeout": timeout, "phase": "helper",
                     "stderr": _recorte(exc.stderr)}) from exc

    if not completado.stdout.strip():
        raise DesktopHelperError(
            "El asistente de interfaz no devolvio nada.",
            details={"returncode": completado.returncode,
                     "stderr": _recorte(completado.stderr)})
    try:
        respuesta = json.loads(completado.stdout)
    except ValueError as exc:
        raise DesktopHelperError(
            "El asistente de interfaz devolvio algo que no es JSON.",
            details={"returncode": completado.returncode,
                     "stdout": _recorte(completado.stdout),
                     "stderr": _recorte(completado.stderr)}) from exc

    if completado.stderr.strip():
        respuesta["helper_stderr"] = _recorte(completado.stderr)
    if not respuesta.get("ok"):
        raise DesktopHelperError(
            respuesta.get("error", "El asistente de interfaz fallo."),
            details={k: v for k, v in respuesta.items() if k != "error"})
    return respuesta


def _recorte(texto: Optional[str], maximo: int = 800) -> str:
    """Recorta y redacta lo que venga del helper antes de propagarlo."""
    if not texto:
        return ""
    from horizun_pbi_mcp.services import redaction

    limpio = redaction.rutas(str(texto))
    return limpio[-maximo:]
