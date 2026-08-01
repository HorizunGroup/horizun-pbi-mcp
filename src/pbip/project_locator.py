"""Ubicacion y validacion de proyectos Power BI Project (.pbip)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from config import ActivePbip, Session
from logging_config import get_logger
from powerbi.errors import PathSecurityError, PbipNotFoundError, PbipStructureError
from utils.json_utils import read_json
from utils.validation import ensure_within_base

log = get_logger("pbip_locator")


def _safe_candidate(base: Path, candidate: Path) -> Optional[Path]:
    """Resuelve `candidate` y verifica que quede dentro de `base` (anti path-traversal).

    Devuelve la ruta resuelta si existe y es segura; None si sale del proyecto o no existe.
    El limite es el directorio del PROYECTO (no el del report), porque el modelo
    semantico es un hermano (`../MiInforme.SemanticModel`), que es legitimo.
    """
    try:
        resolved = ensure_within_base(base, candidate)
    except PathSecurityError as exc:
        log.warning("Ruta bloqueada por seguridad (fuera del proyecto): %s", exc.message)
        return None
    return resolved if resolved.exists() else None


def _find_pbip_file(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_dir():
        matches = sorted(p.glob("*.pbip"))
        if not matches:
            raise PbipNotFoundError(f"No se encontro ningun .pbip en la carpeta {p}.")
        return matches[0]
    if p.suffix.lower() != ".pbip":
        raise PbipNotFoundError(f"La ruta no es un archivo .pbip: {p}")
    if not p.exists():
        raise PbipNotFoundError(f"El archivo .pbip no existe: {p}")
    return p


def _resolve_report_dir(pbip_file: Path, project_dir: Path) -> Optional[Path]:
    try:
        data = read_json(pbip_file)
        for art in data.get("artifacts", []):
            rep = art.get("report")
            if rep and rep.get("path"):
                cand = _safe_candidate(project_dir, project_dir / rep["path"])
                if cand:
                    return cand
    except Exception as exc:  # noqa: BLE001
        log.debug("No se pudo leer artifacts del .pbip: %s", exc)
    # Respaldo: buscar una carpeta *.Report hermana
    reports = sorted(project_dir.glob("*.Report"))
    return reports[0] if reports else None


def _resolve_semantic_model_dir(project_dir: Path, report_dir: Optional[Path]) -> Optional[Path]:
    # 1) desde definition.pbir del report (datasetReference.byPath)
    if report_dir:
        pbir = report_dir / "definition.pbir"
        if pbir.exists():
            try:
                data = read_json(pbir)
                by_path = (data.get("datasetReference", {})
                           .get("byPath", {}).get("path"))
                if by_path:
                    cand = _safe_candidate(project_dir, report_dir / by_path)
                    if cand:
                        return cand
            except Exception:  # noqa: BLE001
                pass
    # 2) respaldo: carpeta *.SemanticModel hermana
    sms = sorted(project_dir.glob("*.SemanticModel"))
    return sms[0] if sms else None


def _build_active_pbip(pbip_file: Path) -> ActivePbip:
    project_dir = pbip_file.parent
    report_dir = _resolve_report_dir(pbip_file, project_dir)
    sm_dir = _resolve_semantic_model_dir(project_dir, report_dir)

    has_pbir = bool(
        report_dir
        and (report_dir / "definition.pbir").exists()
        and (report_dir / "definition" / "pages").exists()
    )
    has_tmdl = bool(
        sm_dir
        and (sm_dir / "definition").exists()
        and any((sm_dir / "definition").glob("*.tmdl"))
    )
    return ActivePbip(
        pbip_path=str(pbip_file),
        project_dir=str(project_dir),
        report_dir=str(report_dir) if report_dir else None,
        semantic_model_dir=str(sm_dir) if sm_dir else None,
        report_name=report_dir.stem.replace(".Report", "") if report_dir else pbip_file.stem,
        has_pbir=has_pbir,
        has_tmdl=has_tmdl,
    )


def open_project(session: Session, path: str) -> Dict[str, Any]:
    """Abre un .pbip, detecta su estructura y lo marca como proyecto activo."""
    pbip_file = _find_pbip_file(path)
    active = _build_active_pbip(pbip_file)
    session.set_active_pbip(active)

    summary = active.to_dict()
    summary["warnings"] = []
    if not active.report_dir:
        summary["warnings"].append("No se encontro la carpeta .Report.")
    if not active.has_pbir:
        summary["warnings"].append(
            "El informe NO usa formato PBIR (o esta sin extraer). No se podran "
            "leer/crear visuales por archivo; guarda el informe como PBIP con el "
            "formato de reporte mejorado (PBIR) activado."
        )
    if not active.has_tmdl:
        summary["warnings"].append(
            "No se detecto modelo TMDL (.SemanticModel/definition). Las ediciones "
            "de modelo por archivo no estaran disponibles."
        )
    log.info("Proyecto .pbip activo: %s (pbir=%s tmdl=%s)",
             pbip_file, active.has_pbir, active.has_tmdl)
    return summary


def validate_project(session: Session) -> Dict[str, Any]:
    """Revisa a fondo la estructura del proyecto .pbip activo."""
    active = session.require_active_pbip()
    project_dir = Path(active.project_dir)
    report_dir = Path(active.report_dir) if active.report_dir else None

    checks: Dict[str, Any] = {
        "pbip_exists": Path(active.pbip_path).exists(),
        "report_dir_exists": bool(report_dir and report_dir.exists()),
        "semantic_model_exists": bool(
            active.semantic_model_dir and Path(active.semantic_model_dir).exists()),
        "has_pbir": active.has_pbir,
        "has_tmdl": active.has_tmdl,
        "enhanced_report_format": False,
        "pbir_version": None,
        "page_count": None,
    }
    warnings = []

    if report_dir and (report_dir / "definition.pbir").exists():
        try:
            pbir = read_json(report_dir / "definition.pbir")
            checks["pbir_version"] = pbir.get("version")
            schema = str(pbir.get("$schema", ""))
            checks["enhanced_report_format"] = "definitionProperties" in schema
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"No se pudo leer definition.pbir: {exc}")

    if active.has_pbir:
        pages_dir = report_dir / "definition" / "pages"
        try:
            checks["page_count"] = sum(
                1 for d in pages_dir.iterdir() if d.is_dir())
        except OSError:
            pass
    else:
        warnings.append(
            "Sin PBIR: las tools de visuales (listar/crear/acomodar) no funcionaran.")

    if not checks["has_tmdl"]:
        warnings.append("Sin TMDL: no se pueden editar medidas por archivo (modo pbip).")

    valid = checks["pbip_exists"] and checks["report_dir_exists"]

    # El TMDL se valida de verdad, no solo se comprueba que exista. Antes esta
    # funcion devolvia valid:true sobre modelos que Power BI Desktop se negaba
    # a abrir, y Desktop acababa siendo el unico detector de errores.
    tmdl: Dict[str, Any] = {"checked": False, "reason": "el proyecto no tiene TMDL"}
    if checks["has_tmdl"] and active.semantic_model_dir:
        from services import tmdl_validate  # perezoso: evita un ciclo de import

        definition = Path(active.semantic_model_dir) / "definition"
        try:
            resultado = tmdl_validate.validate(definition)
            tmdl = {
                "checked": True,
                "valid": resultado["valid"],
                "error_count": resultado["error_count"],
                "warning_count": resultado["warning_count"],
                "parsed": resultado["parsed"],
                "parse_checked": resultado["parse_checked"],
                "findings": resultado["findings"],
            }
            # Solo se invalida cuando SE PUDO comprobar y salio mal. Si no se
            # pudo mirar, se dice, pero no se acusa.
            if not resultado["valid"]:
                valid = False
            for hallazgo in resultado["findings"]:
                if hallazgo["severity"] == "error":
                    warnings.append(
                        f"TMDL: {hallazgo['rule']} - "
                        f"{hallazgo['evidence']}")
        except Exception as exc:  # noqa: BLE001
            tmdl = {"checked": False, "reason": str(exc)}
            warnings.append(f"No se pudo validar el TMDL: {exc}")

    checks["tmdl_valid"] = tmdl.get("valid") if tmdl.get("checked") else None

    return {"valid": bool(valid), "checks": checks, "warnings": warnings,
            "tmdl": tmdl, "project": active.to_dict()}
