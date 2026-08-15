"""Logging estructurado JSON a stderr, con redaccion.

stdout es el canal JSON-RPC del MCP: escribir ahi rompe la conexion. Todo va a
stderr o a archivo.

REDACCION: nunca se registran consultas DAX completas, filas de resultados,
expresiones de medidas, rutas absolutas ni nada que parezca un secreto. De cada
uno se guarda su forma (longitud, numero de filas, nombre del archivo), que es
lo util para diagnosticar, no su contenido.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.branding import LOGGER_NAME  # noqa: F401

#: Claves cuyo VALOR nunca se registra tal cual.
_SENSIBLES = {
    "query", "dax", "expression", "expressions", "rows", "columns", "result",
    "results", "password", "pwd", "secret", "token", "api_key", "apikey",
    "connection_string", "content", "html", "text", "spec", "visual",
    "data", "payload", "before", "after", "value", "values",
}

#: Patrones que se enmascaran incluso dentro de un texto libre.
_PATRONES = [
    (re.compile(r"(?i)(password|pwd|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
     r"\1=***"),
    (re.compile(r"(?i)(user id|uid)\s*=\s*[^;]+"), r"\1=***"),
    # CORE-005. `Bearer <token>` y los secretos con prefijo reconocible: los
    # dos aparecen SUELTOS en el texto de una excepcion, sin una clave delante
    # que los delate, asi que los dos patrones de arriba no los veian.
    (re.compile(r"(?i)\b(bearer)\s+\S+"), r"\1 ***"),
    (re.compile(r"(?i)\b(?:github_pat|ghp|gho|ghu|ghs|ghr|pat|sk|xox[abprs])"
                r"[-_][A-Za-z0-9_\-]{16,}"), "***"),
]

#: Rutas absolutas EMBEBIDAS en un texto libre.
#:
#: `_parece_ruta` solo reconoce cadenas que SON una ruta -empiezan por unidad o
#: por barra-, y el caso que importa es otro: una frase que CONTIENE una. El
#: texto de una excepcion casi nunca es una ruta y casi siempre lleva una
#: dentro. Se cubren unidad de Windows, UNC y los dos prefijos de perfil de
#: usuario en POSIX; se deja fuera cualquier `/algo` suelto a proposito, porque
#: enmascararlo destrozaria URLs, mensajes y nombres de tools sin proteger nada.
#: Los espacios importan y son la parte delicada. `Cliente Confidencial` lleva
#: uno, asi que una clase que corte en el primer espacio deja fuera justo el
#: segmento que hay que ocultar. Se admite un espacio solo si la ruta CONTINUA
#: -si mas adelante queda un separador-, para no tragarse media frase detras del
#: nombre del archivo.
_RUTAS_EN_TEXTO = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\s\\/]+[\\/]|(?:/home|/Users)/)"
    r"(?:[^\s\"'<>|,;)]|[ ](?=[^\s\"'<>|,;)]*[\\/]))*")


def _redact_path(valor: str) -> str:
    """De una ruta solo se conserva el NOMBRE DEL ARCHIVO.

    Conservaba los dos ultimos segmentos, y el penultimo es exactamente donde
    vive el nombre del cliente: `...\\OneDrive\\Cliente Confidencial\\x.pbip`
    quedaba en `.../Cliente Confidencial/x.pbip`. La carpeta padre ayuda a
    diagnosticar y es justo la que no puede viajar, asi que se elige lo que este
    modulo ya prometia en su cabecera -«nombre del archivo»- y no una version
    mas generosa que nadie habia declarado.
    """
    try:
        p = Path(valor)
        partes = p.parts
        if len(partes) > 1:
            return f".../{partes[-1]}"
        return str(p)
    except (OSError, ValueError):
        return "<ruta>"


def _parece_ruta(valor: str) -> bool:
    return (("\\" in valor or "/" in valor)
            and (":" in valor[:3] or valor.startswith(("/", "\\", "."))))


def redact(valor: Any, clave: str = "", _profundidad: int = 0) -> Any:
    """Devuelve una version registrable: forma si, contenido no."""
    if _profundidad > 4:
        return "<...>"
    if clave.lower() in _SENSIBLES:
        if isinstance(valor, str):
            return f"<{len(valor)} chars>"
        if isinstance(valor, (list, tuple)):
            return f"<{len(valor)} elementos>"
        if isinstance(valor, dict):
            return f"<{len(valor)} claves>"
        return "<oculto>"
    if isinstance(valor, str):
        if _parece_ruta(valor):
            return _redact_path(valor)
        texto = valor
        for patron, reemplazo in _PATRONES:
            texto = patron.sub(reemplazo, texto)
        # Las rutas embebidas se reducen a sus dos ultimos segmentos: el nombre
        # del archivo sigue sirviendo para diagnosticar y desaparecen el usuario
        # de Windows y el nombre del cliente, que es lo que no puede viajar.
        texto = _RUTAS_EN_TEXTO.sub(lambda m: _redact_path(m.group(0)), texto)
        return texto if len(texto) <= 200 else f"{texto[:200]}...<+{len(texto) - 200}>"
    if isinstance(valor, dict):
        return {k: redact(v, k, _profundidad + 1) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        if len(valor) > 20:
            return f"<{len(valor)} elementos>"
        return [redact(v, clave, _profundidad + 1) for v in valor]
    return valor


class JsonFormatter(logging.Formatter):
    """Una linea JSON por evento."""

    def format(self, record: logging.LogRecord) -> str:
        # CORE-005. `redact()` sabe reconocer rutas y secretos, y se aplicaba
        # SOLO a `extra_data`, que es justo el campo que casi nunca los lleva.
        # El mensaje libre y la ultima linea de la excepcion si los llevan a
        # menudo -`ValidationError` incluye `path` y `pbip_path`-, y acababan
        # literales en `outputs/*.log`, que es el archivo que alguien adjunta
        # cuando pide ayuda: ruta completa con el nombre de usuario de Windows
        # y, en un `.pbip` de cliente, el nombre del cliente.
        evento: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for campo in ("request_id", "operation", "duration_ms", "result",
                      "error_code", "session", "status"):
            valor = getattr(record, campo, None)
            if valor is not None:
                evento[campo] = valor
        extra = getattr(record, "extra_data", None)
        if extra:
            evento["data"] = redact(extra)
        if record.exc_info:
            evento["exc"] = redact(
                self.formatException(record.exc_info).splitlines()[-1])
        return json.dumps(evento, ensure_ascii=False)


def log_operation(logger: logging.Logger, *, operation: str, request_id: str,
                  duration_ms: float, status: str, ok: bool,
                  error_code: Optional[str] = None,
                  session: Optional[str] = None,
                  extra: Optional[Dict[str, Any]] = None) -> None:
    """Registra el resultado de una tool. Nunca incluye contenido sensible."""
    nivel = logging.INFO if ok else logging.WARNING
    logger.log(nivel, "tool_call", extra={
        "request_id": request_id,
        "operation": operation,
        "duration_ms": round(duration_ms, 1),
        "status": status,
        "result": "ok" if ok else "error",
        "error_code": error_code,
        "session": session,
        "extra_data": extra or {},
    })


def use_json_logging() -> bool:
    """JSON por defecto; texto plano si *_LOG_FORMAT=text."""
    from horizun_pbi_mcp.branding import env

    return (env("LOG_FORMAT") or "json").lower() != "text"
