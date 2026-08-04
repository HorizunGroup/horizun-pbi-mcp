"""Utilidades de sistema de archivos: timestamps, escritura atomica, copias y zip."""
from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def timestamp() -> str:
    """Marca unica apta para archivos: fecha, microsegundos y token aleatorio.

    Con precision de segundos, dos exports consecutivos reutilizaban la misma
    ruta y el segundo sobrescribia silenciosamente el artefacto del primero.
    """
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f_") + uuid.uuid4().hex[:8]


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
    from horizun_pbi_mcp.services.txn import durable_write

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


#: Power BI Desktop rechaza abrir un .pbip si alguna ruta se pasa de estos
#: limites (`PBIProjectUtils.EnsureNotLong`). No es una limitacion del disco
#: —Windows admite rutas largas— sino del propio lector de proyectos, asi que
#: hay que respetarlos aunque el sistema de archivos acepte la escritura.
PBIP_MAX_RUTA_ARCHIVO = 260
PBIP_MAX_RUTA_CARPETA = 248


def rutas_demasiado_largas(base: PathLike, relativas) -> list:
    """Rutas que Power BI Desktop no podria leer, dadas `base` y sus relativas.

    Devuelve [{path, length, limit, kind}] vacio si todo cabe. Se comprueba
    ANTES de escribir: un proyecto a medio generar que Desktop no abre es peor
    que no generarlo.
    """
    raiz = Path(base)
    problemas = []
    vistas = set()
    for relativa in relativas:
        completa = raiz / relativa
        texto = str(completa)
        if len(texto) >= PBIP_MAX_RUTA_ARCHIVO:
            problemas.append({"path": texto, "length": len(texto),
                              "limit": PBIP_MAX_RUTA_ARCHIVO, "kind": "file"})
        carpeta = str(completa.parent)
        if len(carpeta) >= PBIP_MAX_RUTA_CARPETA and carpeta not in vistas:
            vistas.add(carpeta)
            problemas.append({"path": carpeta, "length": len(carpeta),
                              "limit": PBIP_MAX_RUTA_CARPETA, "kind": "folder"})
    return problemas


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"
