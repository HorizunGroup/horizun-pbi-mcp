"""Utilidades de sistema de archivos: timestamps, escritura atomica, copias y zip."""
from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def timestamp() -> str:
    """Marca de tiempo apta para nombres de archivo: YYYYmmdd_HHMMSS."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_dir(path: PathLike) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path: PathLike, text: str, encoding: str = "utf-8") -> Path:
    """Escribe texto de forma atomica y durable.

    Delega en `services.txn.durable_write` (flush + fsync + limpieza del
    temporal si el reemplazo falla), para no dejar archivos `.tmp` sueltos
    dentro del proyecto del usuario.
    """
    p = Path(path)
    from services.txn import durable_write

    durable_write(p, text.encode(encoding))
    return p


def copy_tree(src: PathLike, dst: PathLike) -> Path:
    """Copia recursiva de una carpeta (dst no debe existir)."""
    src_p, dst_p = Path(src), Path(dst)
    shutil.copytree(src_p, dst_p)
    return dst_p


def zip_dir(src_dir: PathLike, dst_zip: PathLike) -> Path:
    """Comprime una carpeta completa en un .zip."""
    src_p = Path(src_dir)
    dst_p = Path(dst_zip)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dst_p, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(src_p):
            for name in files:
                full = Path(root) / name
                zf.write(full, full.relative_to(src_p.parent))
    return dst_p


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
