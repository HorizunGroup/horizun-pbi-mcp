"""Validaciones de entrada y seguridad de rutas (Fase 11)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Union

from powerbi.errors import PathSecurityError, ValidationError

PathLike = Union[str, Path]

# Nombres de objetos de modelo: permitimos casi todo salvo caracteres de control
# y los que rompen TMDL/DAX de forma peligrosa. TMDL cita con comillas simples.
_CONTROL = re.compile(r"[\x00-\x1f]")


def validate_object_name(name: str, kind: str = "objeto") -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"El nombre de {kind} no puede estar vacio.")
    if _CONTROL.search(name):
        raise ValidationError(f"El nombre de {kind} contiene caracteres de control invalidos.")
    if len(name) > 512:
        raise ValidationError(f"El nombre de {kind} es demasiado largo (>512).")
    return name.strip()


def validate_dax_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValidationError("La consulta DAX esta vacia.")
    return query.strip()


#: Topes duros de `pbi_run_dax`. Un cliente no puede superarlos.
MAX_ROWS_PERMITIDO = 1_000_000
MAX_BYTES_PERMITIDO = 256 * 1024 * 1024      # 256 MB
MAX_TIMEOUT_PERMITIDO = 3600                 # 1 hora


def validate_limit(valor, nombre: str, tope: int):
    """Valida un limite opcional: entero, positivo y por debajo del tope.

    Antes se hacia `int(max_rows)` sin mirar: un 0 devolvia cero filas
    silenciosamente, un negativo se colaba hasta el motor, y un valor absurdo
    (10**12 filas, un timeout de un ano) se aceptaba tal cual. Cada caso se
    rechaza ahora con su motivo.
    """
    if valor is None:
        return None
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValidationError(
            f"'{nombre}' debe ser un numero entero, no {type(valor).__name__}.",
            details={"parameter": nombre, "received": repr(valor)})
    if isinstance(valor, float) and not float(valor).is_integer():
        raise ValidationError(f"'{nombre}' debe ser un entero, no {valor}.",
                              details={"parameter": nombre, "received": valor})
    entero = int(valor)
    if entero <= 0:
        raise ValidationError(
            f"'{nombre}' debe ser mayor que cero (recibido {entero}). Omitelo "
            "para usar el valor por defecto.",
            details={"parameter": nombre, "received": entero})
    if entero > tope:
        raise ValidationError(
            f"'{nombre}' = {entero} supera el maximo permitido ({tope}).",
            details={"parameter": nombre, "received": entero, "max": tope})
    return entero


def validate_measure_expression(expr: str) -> str:
    if not isinstance(expr, str) or not expr.strip():
        raise ValidationError("La expresion DAX de la medida esta vacia.")
    return expr.strip()


def validate_position(pos: Dict[str, Any]) -> Dict[str, float]:
    """Valida y normaliza {x, y, width, height} (z opcional)."""
    if not isinstance(pos, dict):
        raise ValidationError("position debe ser un objeto con x, y, width, height.")
    out: Dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        if key not in pos:
            raise ValidationError(f"position.{key} es obligatorio.")
        try:
            out[key] = float(pos[key])
        except (TypeError, ValueError):
            raise ValidationError(f"position.{key} debe ser numerico.")
    if out["width"] <= 0 or out["height"] <= 0:
        raise ValidationError("width y height deben ser mayores que 0.")
    if "z" in pos and pos["z"] is not None:
        try:
            out["z"] = float(pos["z"])
        except (TypeError, ValueError):
            raise ValidationError("position.z debe ser numerico.")
    return out


def ensure_within_base(base: PathLike, target: PathLike) -> Path:
    """Garantiza que `target` queda dentro de `base` (anti path-traversal).

    Devuelve la ruta resuelta. Lanza PathSecurityError si se sale.
    """
    base_r = Path(base).resolve()
    target_r = Path(target).resolve()
    try:
        target_r.relative_to(base_r)
    except ValueError:
        raise PathSecurityError(
            "Ruta fuera del proyecto activo; operacion bloqueada por seguridad.",
            details={"base": str(base_r), "target": str(target_r)},
        )
    return target_r


# --- helpers TMDL ---
_TMDL_SAFE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def tmdl_needs_quote(name: str) -> bool:
    """TMDL requiere comillas simples si el nombre no es un identificador simple."""
    return not bool(_TMDL_SAFE.match(name))


def tmdl_quote_name(name: str) -> str:
    """Devuelve el nombre listo para TMDL, citado si hace falta.

    En TMDL las comillas simples internas se escapan duplicandolas.
    """
    if tmdl_needs_quote(name):
        escaped = name.replace("'", "''")
        return f"'{escaped}'"
    return name
