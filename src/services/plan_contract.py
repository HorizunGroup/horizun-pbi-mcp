"""Contrato unico y versionado de los planes (`plan_token`).

El problema que resuelve
------------------------
Habia dos formas incompatibles de construir un plan:

- `planning.plan()` guardaba ``{"files": {ruta: texto}}`` y una huella de estado
  real;
- `pbi_apply_page_spec(dry_run=True)` guardaba ``{"spec": ..., "seed": ...}``
  y —peor— metia ``args_fingerprint(spec)`` en el campo del *state*
  fingerprint.

`planning.apply()` hacia `plan["files"]` a ciegas, asi que el segundo caso
moria con `KeyError`. Y aunque hubiera tenido `files`, la comparacion de
huellas habria fallado siempre, porque una huella de argumentos nunca puede
coincidir con una huella de archivos.

La solucion no es anadir un `if` en el aplicador: es que todos los planes
compartan un sobre con la misma forma, y que el aplicador **despache por
`operation`** en vez de asumir que todos se aplican igual.

El sobre
--------
Campos obligatorios (los valida `validate_envelope`):

==================== ==========================================================
plan_version         entero. Si no es `PLAN_VERSION`, se rechaza.
operation            que operacion se aplicara. El aplicador despacha por esto.
created_at/expires_at ISO-8601 UTC. Vencido -> `plan_expired`.
request_id           enlaza el plan con la idempotencia (Fase B).
project_root         ruta normalizada del .pbip sobre el que se calculo.
project_fingerprint  identidad del proyecto; distinta => plan de otro proyecto.
payload              argumentos normalizados que produjeron el plan.
payload_fingerprint  detecta manipulacion del payload.
affected_files       que archivos se tocan, con su contenido final y su estado
                     previo. Es la unica fuente de verdad de la escritura.
preconditions        lo que debe seguir siendo cierto al aplicar.
expected_effects     lo que el usuario aprueba: resumen legible.
==================== ==========================================================

Cada entrada de `affected_files` lleva `kind` (``json`` o ``text``) porque el
contenido se escribe con `write_json` o `write_text` segun el caso: los JSON
del PBIR se serializan preservando CRLF, y el TMDL es texto plano.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from powerbi.errors import PowerBIMCPError

#: Version del sobre. Se sube cuando cambia de forma incompatible.
PLAN_VERSION = 1

#: Vida por defecto de un plan (segundos).
DEFAULT_TTL_SECONDS = 3600

_CAMPOS = ("plan_version", "operation", "created_at", "expires_at", "request_id",
           "project_root", "project_fingerprint", "payload", "payload_fingerprint",
           "affected_files", "preconditions", "expected_effects")


class PlanVersionUnsupportedError(PowerBIMCPError):
    """El sobre viene de una version del contrato que este servidor no aplica."""

    code = "plan_version_unsupported"


class PlanExpiredError(PowerBIMCPError):
    """El plan caduco. Se recalcula, no se prorroga."""

    code = "plan_expired"


class PlanOperationMismatchError(PowerBIMCPError):
    """Se pidio aplicar el plan como una operacion distinta de la planificada."""

    code = "plan_operation_mismatch"


class PlanPayloadTamperedError(PowerBIMCPError):
    """El payload no cuadra con su huella: el sobre se modifico despues."""

    code = "plan_payload_tampered"


class PlanProjectMismatchError(PowerBIMCPError):
    """El plan se calculo sobre otro proyecto que el activo ahora."""

    code = "plan_project_mismatch"


class PlanContractError(PowerBIMCPError):
    """El sobre esta mal formado (falta un campo obligatorio, tipo erroneo...)."""

    code = "plan_contract_invalid"


# --------------------------------------------------------------- utilidades ---
def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint_de(valor: Any) -> str:
    """Huella estable e independiente del orden de claves."""
    texto = json.dumps(valor, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


def normalizar_ruta(ruta: Any) -> str:
    """Forma canonica de una ruta, para comparar proyectos sin falsos negativos.

    En Windows dos rutas al mismo archivo pueden diferir en mayusculas o en el
    separador; comparar las cadenas tal cual daria `plan_project_mismatch` sobre
    el mismo proyecto.
    """
    import os

    return os.path.normcase(os.path.abspath(str(ruta)))


def project_fingerprint(project_root: Any) -> str:
    """Identidad del proyecto: su ruta canonica."""
    return hashlib.sha256(normalizar_ruta(project_root).encode("utf-8")).hexdigest()[:16]


def contenido_como_texto(entrada: Dict[str, Any]) -> str:
    """Texto final de una entrada de `affected_files`, para diff y comparacion."""
    if entrada.get("kind") == "json":
        return json.dumps(entrada["content"], indent=2, ensure_ascii=False)
    return str(entrada["content"])


# ----------------------------------------------------------------- creacion ---
def build_envelope(
    *,
    operation: str,
    project_root: Any,
    payload: Dict[str, Any],
    affected_files: List[Dict[str, Any]],
    preconditions: Dict[str, Any],
    expected_effects: Dict[str, Any],
    request_id: Optional[str] = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Dict[str, Any]:
    """Construye un sobre valido. Es el UNICO camino para crear un plan."""
    creado = _ahora()
    sobre = {
        "plan_version": PLAN_VERSION,
        "operation": operation,
        "created_at": creado.isoformat(),
        "expires_at": (creado + timedelta(seconds=ttl_seconds)).isoformat(),
        "request_id": request_id or "",
        "project_root": normalizar_ruta(project_root),
        "project_fingerprint": project_fingerprint(project_root),
        "payload": payload,
        "payload_fingerprint": fingerprint_de(payload),
        "affected_files": affected_files,
        "preconditions": preconditions,
        "expected_effects": expected_effects,
    }
    validate_envelope(sobre)
    return sobre


def archivo_afectado(path: Any, contenido: Any, *, kind: str,
                     estado_previo: str) -> Dict[str, Any]:
    """Una entrada de `affected_files`.

    `estado_previo` es ``present`` o ``absent``: distingue modificar de crear, y
    permite detectar que alguien creo el archivo entre planificar y aplicar.
    """
    if kind not in ("json", "text"):
        raise PlanContractError(f"kind invalido: {kind!r}. Usa 'json' o 'text'.")
    return {"path": str(path), "kind": kind, "content": contenido,
            "prior_state": estado_previo}


# ---------------------------------------------------------------- validacion ---
def validate_envelope(sobre: Any) -> None:
    """Comprueba forma y version. No mira el estado del disco."""
    if not isinstance(sobre, dict):
        raise PlanContractError("El plan no es un objeto.")

    version = sobre.get("plan_version")
    if version != PLAN_VERSION:
        raise PlanVersionUnsupportedError(
            f"Este plan usa el contrato version {version!r} y este servidor "
            f"aplica la version {PLAN_VERSION}. Vuelve a generarlo con dry_run.",
            details={"plan_version": version, "supported": PLAN_VERSION})

    faltan = [c for c in _CAMPOS if c not in sobre]
    if faltan:
        raise PlanContractError(
            f"El plan no cumple el contrato: faltan {faltan}.",
            details={"missing": faltan, "plan_version": PLAN_VERSION})

    if not isinstance(sobre["affected_files"], list):
        raise PlanContractError("'affected_files' debe ser una lista.")
    for i, entrada in enumerate(sobre["affected_files"]):
        if not isinstance(entrada, dict) or "path" not in entrada:
            raise PlanContractError(f"affected_files[{i}] no tiene 'path'.")
        if entrada.get("kind") not in ("json", "text"):
            raise PlanContractError(
                f"affected_files[{i}].kind invalido: {entrada.get('kind')!r}.")
        if entrada.get("prior_state") not in ("present", "absent"):
            raise PlanContractError(
                f"affected_files[{i}].prior_state invalido: "
                f"{entrada.get('prior_state')!r}.")


def assert_no_expirado(sobre: Dict[str, Any]) -> None:
    try:
        vence = datetime.fromisoformat(sobre["expires_at"])
    except (TypeError, ValueError) as exc:
        raise PlanContractError(
            f"'expires_at' no es una fecha ISO valida: {sobre.get('expires_at')!r}"
        ) from exc
    if vence.tzinfo is None:
        vence = vence.replace(tzinfo=timezone.utc)
    if _ahora() > vence:
        raise PlanExpiredError(
            f"El plan de '{sobre['operation']}' caduco el {sobre['expires_at']}. "
            "Genera uno nuevo con dry_run=true.",
            details={"expires_at": sobre["expires_at"],
                     "operation": sobre["operation"]})


def assert_payload_integro(sobre: Dict[str, Any]) -> None:
    esperada = sobre.get("payload_fingerprint")
    real = fingerprint_de(sobre.get("payload"))
    if esperada != real:
        raise PlanPayloadTamperedError(
            "El contenido del plan no coincide con su huella: fue modificado "
            "despues de aprobarse. Genera el plan de nuevo.",
            details={"expected": esperada, "actual": real,
                     "operation": sobre.get("operation")})


def assert_mismo_proyecto(sobre: Dict[str, Any], project_root: Any) -> None:
    actual = project_fingerprint(project_root)
    if sobre.get("project_fingerprint") != actual:
        raise PlanProjectMismatchError(
            "Este plan se calculo sobre otro proyecto. Abre el proyecto correcto "
            "o genera el plan de nuevo sobre el activo.",
            details={"plan_project": sobre.get("project_root"),
                     "active_project": normalizar_ruta(project_root)})


def assert_operacion(sobre: Dict[str, Any], esperada: str) -> None:
    if esperada and sobre.get("operation") != esperada:
        raise PlanOperationMismatchError(
            f"Este plan es de '{sobre.get('operation')}', no de '{esperada}'. "
            "Un plan solo puede aplicarse como la operacion que lo genero.",
            details={"plan_operation": sobre.get("operation"),
                     "requested_operation": esperada})


def rutas(sobre: Dict[str, Any]) -> List[Path]:
    """Rutas escritas Y borradas, en el orden del plan y sin duplicados.

    La huella inicial incluye ambos grupos. Recalcularla solo con las
    escrituras hacia que todo plan ``sync_mode=replace`` pareciera obsoleto
    incluso sin cambios y, peor aun, no vigilaba el archivo que se iba a
    eliminar.
    """
    candidatos = [e["path"] for e in sobre["affected_files"]]
    candidatos += list(
        (sobre.get("expected_effects") or {}).get("files_deleted") or [])
    salida: List[Path] = []
    vistos = set()
    for candidato in candidatos:
        ruta = Path(candidato)
        clave = normalizar_ruta(ruta)
        if clave not in vistos:
            vistos.add(clave)
            salida.append(ruta)
    return salida
