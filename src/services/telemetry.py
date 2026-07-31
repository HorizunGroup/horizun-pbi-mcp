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

from branding import LOGGER_NAME  # noqa: F401

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
]


def _redact_path(valor: str) -> str:
    """De una ruta solo se conservan los dos ultimos segmentos."""
    try:
        p = Path(valor)
        partes = p.parts
        if len(partes) > 2:
            return f".../{'/'.join(partes[-2:])}"
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
        evento: Dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
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
            evento["exc"] = self.formatException(record.exc_info).splitlines()[-1]
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
    from branding import env

    return (env("LOG_FORMAT") or "json").lower() != "text"
