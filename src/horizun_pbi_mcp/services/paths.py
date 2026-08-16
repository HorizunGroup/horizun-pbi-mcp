"""Acotacion de rutas al proyecto activo, con semantica real de Windows.

Dos problemas distintos, dos funciones distintas:

1. Un id de pagina o de visual es un IDENTIFICADOR, no una ruta. Se valida con
   `safe_identifier()`, que rechaza cualquier sintaxis de ruta ANTES de tocar
   el sistema de archivos.

2. Una ruta compuesta debe quedar dentro del proyecto. Se valida con
   `ensure_contained()`, que resuelve enlaces (junctions y reparse points) y
   compara de forma insensible a mayusculas, como hace NTFS.

No basta con normalizar la cadena y aplicar `relative_to()`: en Windows
`Path('base') / 'C:/otro'` devuelve `C:/otro`, `\\\\?\\C:\\x` evita la
normalizacion del sistema, `archivo.json:stream` escribe en un flujo alterno
NTFS y `CON` es un dispositivo, no un archivo.

TOCTOU: `ensure_contained()` se vuelve a llamar inmediatamente antes de
escribir (ver `services.txn`), porque un enlace puede cambiar entre la
validacion y la escritura.
"""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePath
from typing import Iterable, NoReturn, Union

from horizun_pbi_mcp.powerbi.errors import PathSecurityError, ValidationError

PathLike = Union[str, Path]

# Dispositivos reservados de Windows. Reservados con o sin extension, y en
# cualquier combinacion de mayusculas.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}

# Caracteres que Windows no admite en un nombre de archivo. `:` incluido: es el
# separador de flujos de datos alternos (ADS) y de unidad.
_ILLEGAL_CHARS = set('<>:"/\\|?*')

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Prefijos de ruta especiales de Windows.
_UNC_PREFIXES = ("\\\\", "//")
_EXTENDED_PREFIX = "\\\\?\\"
_DEVICE_PREFIX = "\\\\.\\"

# `C:algo` (relativa a la unidad) y `C:\algo` (absoluta).
_DRIVE_SYNTAX = re.compile(r"^[A-Za-z]:")


def _reject(reason: str, value: str, kind: str) -> NoReturn:
    raise PathSecurityError(
        f"{kind} invalido: {reason}",
        details={"value": value, "kind": kind, "reason": reason},
    )


def safe_identifier(value: str, kind: str = "identificador") -> str:
    """Valida que `value` sea un identificador simple, nunca una ruta.

    Se usa para ids de pagina y de visual. Rechaza, antes de tocar el disco:
    separadores, `.`/`..`, rutas absolutas, sintaxis de unidad, UNC, rutas
    extendidas y de dispositivo, ADS de NTFS, nombres reservados, componentes
    vacios y componentes con punto o espacio final.
    """
    if not isinstance(value, str):
        _reject("no es una cadena", repr(value), kind)
    if value == "":
        _reject("esta vacio", value, kind)
    if _CONTROL.search(value):
        _reject("contiene caracteres de control", value, kind)

    # Prefijos especiales antes que nada: \\?\ evita la normalizacion de Windows.
    if value.startswith(_EXTENDED_PREFIX):
        _reject("es una ruta extendida (\\\\?\\)", value, kind)
    if value.startswith(_DEVICE_PREFIX):
        _reject("es una ruta de dispositivo (\\\\.\\)", value, kind)
    if value.startswith(_UNC_PREFIXES):
        _reject("es una ruta UNC de red", value, kind)
    if _DRIVE_SYNTAX.match(value):
        # Cubre tanto `C:\x` (absoluta) como `C:x` (relativa a la unidad).
        _reject("usa sintaxis de unidad (C: ...)", value, kind)
    if value.startswith(("/", "\\")):
        _reject("es una ruta absoluta", value, kind)

    illegal = sorted(set(value) & _ILLEGAL_CHARS)
    if illegal:
        detalle = "separador de ruta" if set(illegal) & set("/\\") else (
            "flujo alterno NTFS o unidad" if ":" in illegal else "caracter no permitido")
        _reject(f"contiene {detalle}: {illegal}", value, kind)

    if value in (".", ".."):
        _reject("es un componente relativo", value, kind)

    # `nombre.` y `nombre ` los normaliza Windows en silencio: dos ids distintos
    # podrian apuntar al mismo archivo.
    if value != value.rstrip(" ."):
        _reject("termina en punto o espacio", value, kind)

    stem = value.split(".", 1)[0].upper()
    if stem in _RESERVED_NAMES:
        _reject(f"es un nombre de dispositivo reservado ({stem})", value, kind)

    return value


def assert_not_path_syntax(value: str, kind: str = "nombre") -> str:
    """Rechaza sintaxis de ruta, pero admite nombres visibles arbitrarios.

    Un `displayName` de pagina puede llevar espacios, acentos o puntuacion
    ("Resumen ejecutivo 2026"). Eso es legitimo. Lo que nunca es legitimo es que
    traiga separadores, `..`, sintaxis de unidad, UNC o un flujo alterno NTFS.

    Es mas permisiva que `safe_identifier`: se usa cuando el valor puede ser un
    nombre para mostrar y no solo un id de carpeta.
    """
    if not isinstance(value, str) or value == "":
        _reject("esta vacio", str(value), kind)
    if _CONTROL.search(value):
        _reject("contiene caracteres de control", value, kind)
    if value.startswith(_EXTENDED_PREFIX):
        _reject("es una ruta extendida (\\\\?\\)", value, kind)
    if value.startswith(_DEVICE_PREFIX):
        _reject("es una ruta de dispositivo (\\\\.\\)", value, kind)
    if value.startswith(_UNC_PREFIXES):
        _reject("es una ruta UNC de red", value, kind)
    if _DRIVE_SYNTAX.match(value):
        _reject("usa sintaxis de unidad (C: ...)", value, kind)
    if "/" in value or "\\" in value:
        _reject("contiene separadores de ruta", value, kind)
    if ":" in value:
        _reject("contiene ':' (flujo alterno NTFS o unidad)", value, kind)
    if value in (".", "..") or value.strip() in (".", ".."):
        _reject("es un componente relativo", value, kind)
    return value


def ensure_contained(base: PathLike, target: PathLike, *,
                     kind: str = "ruta") -> Path:
    """Garantiza que `target` queda dentro de `base`. Devuelve la ruta resuelta.

    Resuelve enlaces simbolicos, junctions y reparse points en AMBOS extremos y
    compara con `os.path.normcase`, porque NTFS no distingue mayusculas.
    """
    base_r = Path(base).resolve()
    target_r = Path(target).resolve()

    base_n = os.path.normcase(str(base_r))
    target_n = os.path.normcase(str(target_r))

    # Unidades distintas: `relative_to` daria un error poco claro.
    if base_r.drive and target_r.drive and \
            os.path.normcase(base_r.drive) != os.path.normcase(target_r.drive):
        raise PathSecurityError(
            "La ruta apunta a otra unidad; operacion bloqueada por seguridad.",
            details={"base": str(base_r), "target": str(target_r), "kind": kind},
        )

    if target_n != base_n and not target_n.startswith(base_n + os.sep):
        raise PathSecurityError(
            "Ruta fuera del proyecto activo; operacion bloqueada por seguridad.",
            details={"base": str(base_r), "target": str(target_r), "kind": kind},
        )
    return target_r


def safe_join(base: PathLike, *parts: str, kind: str = "ruta") -> Path:
    """Une identificadores validados a `base` y comprueba la contencion.

    Cada parte se valida como identificador: asi un `..` o una unidad no llegan
    nunca a combinarse con el root.
    """
    base_p = Path(base)
    for part in parts:
        safe_identifier(part, kind=kind)
    joined = base_p.joinpath(*parts)
    return ensure_contained(base_p, joined, kind=kind)


def assert_still_contained(base: PathLike, target: PathLike, *,
                           kind: str = "ruta") -> Path:
    """Revalidacion anti-TOCTOU, para llamar justo antes de escribir.

    Es la misma comprobacion que `ensure_contained`, con nombre propio para que
    en el codigo se lea por que se repite: entre la validacion y la escritura,
    un junction pudo haber cambiado de destino.
    """
    return ensure_contained(base, target, kind=kind)


def is_inside(base: PathLike, target: PathLike) -> bool:
    """Version booleana de `ensure_contained` (no lanza)."""
    try:
        ensure_contained(base, target)
        return True
    except PathSecurityError:
        return False


def relative_key(base: PathLike, target: PathLike) -> str:
    """Clave estable y portable de `target` respecto de `base`.

    Se usa en manifiestos y fingerprints: siempre con `/`, para que un
    manifiesto sea legible y comparable entre plataformas.
    """
    base_r = Path(base).resolve()
    target_r = Path(target).resolve()
    return PurePath(os.path.relpath(target_r, base_r)).as_posix()


def validate_identifiers(values: Iterable[str], kind: str = "identificador") -> None:
    """Valida un lote de identificadores (falla en el primero invalido)."""
    for v in values:
        safe_identifier(v, kind=kind)
