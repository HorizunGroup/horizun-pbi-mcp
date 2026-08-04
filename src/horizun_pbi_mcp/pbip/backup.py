"""Backups de proyectos .pbip, con destino validado y manifiesto verificable.

Fase 1A. Tres reglas que antes no se cumplian:

1. El destino NUNCA puede estar dentro del `.pbip`, del `.Report` ni del
   `.SemanticModel`. Si lo estuviera, Power BI podria interpretar la copia como
   parte del informe. La validacion vive en `services.txn.resolve_backup_root`
   y falla de forma accionable ANTES de tocar el proyecto.
2. Cada proyecto se identifica por un HASH ESTABLE de su ruta absoluta, no por
   su nombre: dos `Informe.pbip` en carpetas distintas no comparten backups.
3. Cada backup lleva un manifiesto con el sha256 de cada archivo, para poder
   comprobar una restauracion byte a byte.

No hay purga automatica en esta fase, y nunca se borran backups preexistentes
del usuario.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import ActivePbip, Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import BackupError
from horizun_pbi_mcp.services import paths as safe_paths
from horizun_pbi_mcp.services import txn as txn_service
from horizun_pbi_mcp.utils.file_utils import copy_tree, timestamp

log = get_logger("backup")

MANIFEST_NAME = "manifest.json"
_STAGE_PREFIX = ".hz_backup_tmp_"


def _source_dirs(active: ActivePbip, scope: str) -> List[Path]:
    dirs: List[Path] = []
    if scope in ("report", "both") and active.report_dir:
        dirs.append(Path(active.report_dir))
    if scope in ("model", "both") and active.semantic_model_dir:
        dirs.append(Path(active.semantic_model_dir))
    return [d for d in dirs if d.exists()]


def _backup_root(active: ActivePbip) -> Path:
    """Raiz de backups validada para este proyecto (por hash de su ruta)."""
    return txn_service.project_backup_root(active)


def _build_manifest(active: ActivePbip, sources: List[Path], scope: str,
                    mode: str) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    project_dir = Path(active.project_dir)
    for src in sources:
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            fp = txn_service.fingerprint(path)
            try:
                key = safe_paths.relative_key(project_dir, path)
            except ValueError:                       # pragma: no cover
                key = path.name
            files.append({"path": key, **fp.to_dict()})
    return {
        "manifest_version": txn_service.MANIFEST_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "tool": "pbi_backup_pbip_project",
        "algorithm": "sha256",
        "source_root": str(project_dir),
        "project_id": txn_service.project_id(project_dir),
        "scope": scope,
        "mode": mode,
        "items": [s.name for s in sources],
        "files": files,
    }


def backup_project(session: Session, mode: str = "folder",
                   scope: str = "both") -> Dict[str, Any]:
    """Crea un backup del proyecto activo, con manifiesto de hashes.

    - mode: "folder" (copia) o "zip".
    - scope: "report", "model" o "both".
    """
    if mode not in ("folder", "zip"):
        raise BackupError("mode debe ser 'folder' o 'zip'.")
    if scope not in ("report", "model", "both"):
        raise BackupError("scope debe ser 'report', 'model' o 'both'.")

    active = session.require_active_pbip()
    sources = _source_dirs(active, scope)
    if not sources:
        raise BackupError(f"No hay carpetas para respaldar (scope={scope}).")

    # Valida el destino ANTES de copiar nada.
    root = _backup_root(active)
    stamp = f"{timestamp()}_{uuid.uuid4().hex[:6]}"
    manifest = _build_manifest(active, sources, scope, mode)

    stage_name = f"{_STAGE_PREFIX}{stamp}"
    stage: Optional[Path] = None
    try:
        if mode == "zip":
            dest = root / f"{stamp}.zip"
            stage = root / f"{stage_name}.zip"
            with zipfile.ZipFile(stage, "w", zipfile.ZIP_DEFLATED) as zf:
                for src in sources:
                    for path in src.rglob("*"):
                        if path.is_file():
                            zf.write(path, path.relative_to(src.parent))
                zf.writestr(MANIFEST_NAME,
                            json.dumps(manifest, indent=2, ensure_ascii=False))
            _verify_zip(stage, manifest)
            os.replace(stage, dest)
            backup_path = dest
        else:
            dest = root / stamp
            stage = root / stage_name
            stage.mkdir(parents=True, exist_ok=False)
            for src in sources:
                copy_tree(src, stage / src.name)
            from horizun_pbi_mcp.services.txn import durable_write

            durable_write(
                stage / MANIFEST_NAME,
                (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
                 ).encode("utf-8"))
            comprobacion = verify_backup(stage)
            if not comprobacion["clean"]:
                raise BackupError(
                    "La copia del backup no coincide con el origen; no se "
                    "publica un respaldo parcial.", details=comprobacion)
            os.replace(stage, dest)
            backup_path = dest
    except BackupError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise BackupError(f"No se pudo crear el backup: {exc}") from exc
    finally:
        # El destino solo se hace visible al final. Cualquier fallo anterior
        # deja exclusivamente este staging reconocible, que se retira aqui.
        if stage is not None and stage.exists():
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
            else:
                try:
                    stage.unlink()
                except OSError:                       # pragma: no cover
                    pass

    log.info("Backup creado: %s (%d archivos)", backup_path, len(manifest["files"]))
    return {
        "backup_path": str(backup_path),
        "mode": mode,
        "scope": scope,
        "items": [s.name for s in sources],
        "manifest": MANIFEST_NAME,
        "file_count": len(manifest["files"]),
        "project_id": manifest["project_id"],
    }


def _verify_zip(path: Path, manifest: Dict[str, Any]) -> None:
    """Relee el ZIP terminado y verifica tamaño y sha256 de cada entrada."""
    import hashlib

    with zipfile.ZipFile(path, "r") as zf:
        nombres = {n.replace("\\", "/"): n for n in zf.namelist()}
        for entry in manifest["files"]:
            clave = str(entry["path"]).replace("\\", "/")
            real = nombres.get(clave)
            if real is None:
                raise BackupError(
                    "El ZIP de backup quedo incompleto; no se publica.",
                    details={"missing": clave})
            datos = zf.read(real)
            if (len(datos) != entry.get("size") or
                    hashlib.sha256(datos).hexdigest() != entry.get("sha256")):
                raise BackupError(
                    "El ZIP de backup no coincide con el origen; no se publica.",
                    details={"mismatch": clave})


def backup_before_edit(active: ActivePbip, target: str = "report") -> Optional[str]:
    """Respaldo previo a una edicion, en el destino validado del proyecto.

    Se conserva por compatibilidad con los flujos que aun no usan
    `services.txn` (que ya respalda por si mismo, y ademas sabe restaurar).
    """
    src = None
    if target == "report" and active.report_dir:
        src = Path(active.report_dir)
    elif target == "model" and active.semantic_model_dir:
        src = Path(active.semantic_model_dir)
    if not src or not src.exists():
        return None

    root = _backup_root(active)
    stamp = f"{timestamp()}_{uuid.uuid4().hex[:6]}"
    dest = root / f"pre_edit_{stamp}" / src.name
    try:
        copy_tree(src, dest)
    except OSError as exc:
        raise BackupError(f"No se pudo crear el backup previo a la edicion: {exc}") from exc
    log.info("Backup previo a edicion: %s", dest)
    return str(dest)


def verify_backup(backup_dir: Path) -> Dict[str, Any]:
    """Comprueba un backup contra su manifiesto, archivo por archivo.

    Devuelve el detalle por archivo: `ok`, `mismatch` o `missing`. Sirve para
    demostrar que una restauracion es byte a byte y para detectar manifiestos
    incompletos o backups corruptos.
    """
    bdir = Path(backup_dir)
    manifest_path = bdir / MANIFEST_NAME
    if not manifest_path.exists():
        raise BackupError(f"El backup no tiene {MANIFEST_NAME}: {bdir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BackupError(f"Manifiesto ilegible: {exc}") from exc

    results: List[Dict[str, Any]] = []
    for entry in manifest.get("files", []):
        rel = entry["path"]
        # El backup guarda cada carpeta origen por su nombre; el manifiesto usa
        # rutas relativas al proyecto, que empiezan por ese mismo nombre.
        candidate = bdir / rel
        if not candidate.exists():
            results.append({"path": rel, "status": "missing"})
            continue
        actual = txn_service.fingerprint(candidate)
        expected = txn_service.Fingerprint(
            entry.get("state", "present"), entry.get("sha256"), entry.get("size"))
        results.append({"path": rel,
                        "status": "ok" if actual.matches(expected) else "mismatch"})

    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    return {"backup_path": str(bdir), "checked": len(results),
            "by_status": by_status, "clean": by_status.get("ok", 0) == len(results),
            "files": results}
