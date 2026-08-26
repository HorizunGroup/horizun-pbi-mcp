"""Publicacion compensada de un proyecto completo construido en staging.

Los generadores de proyectos no pueden escribir directamente en el destino:
un fallo tardio (validador, Desktop, disco) dejaria medio ``.Report`` o borraria
un proyecto anterior con ``overwrite=true``. La regla es la misma que para un
flujo PBIR multiarchivo:

1. construir y validar todo fuera del destino;
2. planificar la union de archivos viejos y nuevos;
3. publicar en una sola transaccion con backup;
4. revertir byte a byte si cualquier escritura o el commit falla.
"""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.config import ActivePbip, get_settings
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import paths as safe_paths
from horizun_pbi_mcp.services import txn as txn_service


class ProjectPublishError(PowerBIMCPError):
    code = "project_publish_failed"


_STAGE_PREFIX = ".hz_stage_"


def create_stage(base_dir: Path | str) -> Path:
    """Crea un staging corto, hermano del destino final."""
    base = Path(base_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    for _ in range(10):
        stage = base / f"{_STAGE_PREFIX}{uuid.uuid4().hex[:10]}"
        try:
            stage.mkdir()
            return stage
        except FileExistsError:                       # pragma: no cover
            continue
    raise ProjectPublishError(
        "No se pudo reservar una carpeta temporal para construir el proyecto.",
        details={"base_dir": str(base)})


def discard_stage(stage: Path | str) -> bool:
    """Elimina exclusivamente un staging creado por :func:`create_stage`."""
    ruta = Path(stage).resolve()
    if not ruta.name.startswith(_STAGE_PREFIX):
        raise ProjectPublishError(
            "Se rechazo eliminar una carpeta que no es un staging de Horizun.",
            details={"stage": str(ruta)})
    if ruta.exists():
        try:
            shutil.rmtree(ruta)
        except OSError as exc:
            # Es limpieza auxiliar. Si la publicacion ya se confirmo, convertir
            # este fallo en el resultado de la tool haria creer que el proyecto
            # no se creo e invitaria a sobrescribirlo en un reintento.
            from horizun_pbi_mcp.logging_config import get_logger

            get_logger("project_publish").warning(
                "No se pudo retirar staging %s: %s", ruta, exc)
            return False
    return True


def _files(root: Path) -> Dict[str, Path]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p
            for p in root.rglob("*") if p.is_file()}


def _dirs(root: Path) -> Dict[str, Path]:
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p
            for p in root.rglob("*") if p.is_dir()}


def _remove_empty_dirs(root: Path, keep: set[str]) -> None:
    if not root.exists():
        return
    directorios = [p for p in root.rglob("*") if p.is_dir()]
    for directory in sorted(directorios, key=lambda p: len(p.parts), reverse=True):
        if directory.relative_to(root).as_posix() in keep:
            continue
        try:
            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError:                                # pragma: no cover
            continue


def _assert_existing_target_writable(target: Path, *, operation: str) -> None:
    """Aplica la misma puerta de Desktop a un proyecto aun no seleccionado."""
    if not target.exists() or not any(target.iterdir()):
        return
    from horizun_pbi_mcp.services import project_state

    from horizun_pbi_mcp.services import project_resolver

    # Misma politica que el localizador: con dos candidatos no se elige uno.
    # Aqui ademas se va a REEMPLAZAR el destino, asi que equivocarse de
    # proyecto al comprobar si esta abierto en Desktop es doblemente caro.
    pbip = project_resolver.unico(target, "*.pbip", kind="proyecto .pbip",
                                  obligatorio=False)
    report = project_resolver.unico(target, "*.Report",
                                    kind="carpeta .Report",
                                    solo_directorios=True, obligatorio=False)
    model = project_resolver.unico(target, "*.SemanticModel",
                                   kind="carpeta .SemanticModel",
                                   solo_directorios=True, obligatorio=False)
    active = ActivePbip(
        pbip_path=str(pbip if pbip else target / "__unknown__.pbip"),
        project_dir=str(target),
        report_dir=str(report) if report else None,
        semantic_model_dir=str(model) if model else None,
        has_pbir=bool(report), has_tmdl=bool(model),
    )
    project_state.assert_writable(active, operation=operation)


def publish_tree(stage: Path | str, target: Path | str, *, overwrite: bool,
                 tool: str, request_id: Optional[str] = None) -> Dict[str, Any]:
    """Publica ``stage`` sobre ``target`` con journal y rollback completos."""
    staging = Path(stage).resolve()
    destino = Path(target).expanduser().resolve()
    if not staging.is_dir():
        raise ProjectPublishError(
            "La carpeta temporal del proyecto no existe.",
            details={"stage": str(staging)})
    if staging.parent != destino.parent or not staging.name.startswith(_STAGE_PREFIX):
        raise ProjectPublishError(
            "El staging debe ser un hermano reconocido del destino final.",
            details={"stage": str(staging), "target": str(destino)})
    if destino.exists() and not destino.is_dir():
        raise ProjectPublishError(
            "El destino del proyecto existe pero no es una carpeta.",
            details={"target": str(destino)})

    existentes = _files(destino)
    destino_no_vacio = destino.exists() and any(destino.iterdir())
    if destino_no_vacio and not overwrite:
        raise ProjectPublishError(
            f"La carpeta de destino ya existe y no esta vacia: {destino}. "
            "Usa overwrite=true si quieres reemplazarla.",
            details={"target": str(destino)})
    if destino_no_vacio:
        _assert_existing_target_writable(
            destino, operation="Reemplazar un proyecto PBIP existente")
    nuevos = _files(staging)
    nuevos_dirs = _dirs(staging)
    if not nuevos:
        raise ProjectPublishError(
            "El generador no produjo ningun archivo; no se publica un proyecto vacio.",
            details={"stage": str(staging)})

    # Las rutas se derivan exclusivamente de dos arboles ya resueltos. Se
    # revalida cada destino antes de darselo a la transaccion.
    relativas = sorted(set(existentes) | set(nuevos))
    objetivos = [safe_paths.ensure_contained(
        destino, destino / rel, kind="archivo de proyecto a publicar")
        for rel in relativas]

    backup_root = txn_service.resolve_backup_root(
        destino, get_settings().backups_dir, extra_forbidden=(staging,))
    cm = txn_service.transaction(
        destino, backup_root, objetivos, tool=tool, request_id=request_id)
    with cm as tx:
        for rel, archivo in existentes.items():
            if rel not in nuevos:
                tx.delete(archivo)
        for rel, archivo in nuevos.items():
            tx.write_bytes(destino / rel, archivo.read_bytes())
        for rel in sorted(nuevos_dirs, key=lambda p: len(Path(p).parts)):
            tx.ensure_directory(destino / rel)
        # Los directorios viejos ya vacios tambien son residuo: un directorio
        # de pagina sin page.json confunde tanto a Desktop como al lector.
        _remove_empty_dirs(destino, set(nuevos_dirs))

    return {
        "target": str(destino),
        "files": len(nuevos),
        "replaced_files": len(existentes),
        "transaction": cm.result,
    }
