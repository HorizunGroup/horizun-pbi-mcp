"""Lectura/escritura de JSON robusta para archivos PBIR.

Reglas (Fase 11):
- UTF-8 sin BOM, indentado a 2 espacios (como escribe Power BI).
- Escritura atomica (archivo temporal + os.replace) para no dejar archivos corruptos.
- Si un JSON existente no se puede parsear, NO se sobrescribe: se lanza error.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Union

from horizun_pbi_mcp.powerbi.errors import ValidationError

PathLike = Union[str, Path]


def read_json(path: PathLike) -> Any:
    """Lee y parsea un JSON. Lanza ValidationError si esta corrupto."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")  # tolera BOM al leer
        return json.loads(text)
    except FileNotFoundError:
        raise
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"JSON invalido o corrupto: {p} ({exc})", details={"path": str(p)}
        ) from exc


def dumps(data: Any) -> str:
    """Serializa a string JSON con el formato estandar del proyecto."""
    return json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False)


def write_json(path: PathLike, data: Any) -> Path:
    """Escribe un JSON de forma atomica y durable.

    Serializa primero en memoria (si falla, no toca el archivo destino) y
    delega en `services.txn.durable_write`, que hace flush + fsync, valida el
    temporal y —lo importante— LIMPIA el temporal si el reemplazo falla.

    Sin esa limpieza quedaba un `.tmp` dentro del .pbip del usuario cada vez que
    otro proceso (tipicamente Power BI Desktop) tenia el destino abierto: en
    Windows `os.replace` falla entonces con WinError 5.
    """
    p = Path(path)
    try:
        text = dumps(data)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"No se pudo serializar a JSON: {exc}", details={"path": str(p)}
        ) from exc

    from horizun_pbi_mcp.services.txn import durable_write

    durable_write(p, text.encode("utf-8").replace(b"\n", detect_newline(p)))
    return p


def detect_newline(path: PathLike) -> bytes:
    """Final de linea del archivo existente. Por defecto, el de Power BI (CRLF).

    Power BI Desktop escribe los JSON del PBIR con CRLF. Si reescribimos con LF,
    el contenido es identico pero el archivo cambia byte a byte: un diff de git
    marca el archivo entero y una comparacion de huellas deja de servir para
    verificar que no tocamos nada. Se conserva lo que ya habia.
    """
    p = Path(path)
    if not p.exists():
        return b"\r\n"
    try:
        muestra = p.read_bytes()[:8192]
    except OSError:                                   # pragma: no cover
        return b"\r\n"
    if b"\r\n" in muestra:
        return b"\r\n"
    if b"\n" in muestra:
        return b"\n"
    return b"\r\n"


def safe_update_json(path: PathLike, mutate) -> Any:
    """Lee un JSON, aplica `mutate(data) -> data` y lo reescribe atomicamente.

    Si el archivo original esta corrupto, aborta sin escribir.
    """
    data = read_json(path)
    new = mutate(data)
    if new is None:
        new = data
    write_json(path, new)
    return new
