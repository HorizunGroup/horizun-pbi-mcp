"""Envelope de respuesta uniforme. ADITIVO: nunca quita ni renombra nada.

Toda tool sigue devolviendo `ok` y exactamente los mismos campos que antes; el
envelope solo ANADE metadatos alrededor. Por eso el contrato de las 34 tools
originales no se rompe: `output_shape` sigue siendo `{result: object}` y las
claves preexistentes conservan su nombre y su significado.

Estados:
    success              la operacion hizo lo que pedia
    warning              lo hizo, pero hay algo que el usuario deberia mirar
    planned              no se aplico nada: es un plan (dry_run)
    error                fallo; no se aplico nada, o se revirtio limpiamente
    conflict             estado externo incompatible (concurrencia, sesion, modo)
    rollback_incomplete  fallo Y la reversion no quedo limpia: requiere accion
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Dict, List, Optional

SUCCESS = "success"
WARNING = "warning"
PLANNED = "planned"
ERROR = "error"
CONFLICT = "conflict"
ROLLBACK_INCOMPLETE = "rollback_incomplete"

#: Codigos de error que representan un choque con el estado externo, no un fallo
#: de la peticion en si. Se distinguen para que un cliente pueda reintentar.
_CONFLICT_CODES = {
    "project_open_in_desktop",
    "stale_session",
    "transaction_failed",
    "dual_mode_not_safely_available",
    "measure_exists",
    "plan_token_stale",
    "request_id_conflict",
}

_ROLLBACK_CODES = {"rollback_incomplete", "bulk_partially_applied"}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _status_for_error(code: str) -> str:
    if code in _ROLLBACK_CODES:
        return ROLLBACK_INCOMPLETE
    if code in _CONFLICT_CODES:
        return CONFLICT
    return ERROR


def _status_for_success(payload: Dict[str, Any]) -> str:
    if payload.get("planned") or payload.get("dry_run"):
        return PLANNED
    warnings = payload.get("warnings")
    if warnings:
        return WARNING
    # Un modo dual que quedo inconsistente no es un exito limpio.
    if payload.get("consistent") is False:
        return WARNING
    return SUCCESS


def _side_effects(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resume que se toco, para que el cliente no tenga que adivinarlo."""
    efectos: List[Dict[str, Any]] = []
    txn = payload.get("transaction")
    if isinstance(txn, dict):
        efectos.append({
            "kind": "files",
            "journal": txn.get("journal"),
            "files": [f.get("path") for f in txn.get("files", [])],
            "committed": txn.get("committed"),
        })
    if payload.get("backup") and not efectos:
        efectos.append({"kind": "backup", "path": payload["backup"]})
    live = payload.get("live")
    if isinstance(live, dict) and live.get("note"):
        efectos.append({"kind": "live_model",
                        "persisted": False,
                        "note": live["note"]})
    if payload.get("output_path"):
        efectos.append({"kind": "artifact", "path": payload["output_path"]})
    return efectos


def success(payload: Dict[str, Any], *, operation: str, request_id: str,
            duration_ms: float) -> Dict[str, Any]:
    """Construye el envelope de exito conservando el payload original."""
    if not isinstance(payload, dict):
        payload = {"result": payload}
    out: Dict[str, Any] = {"ok": True}
    out.update(payload)                      # los campos originales, intactos
    out["status"] = _status_for_success(payload)
    out["request_id"] = request_id
    out["operation"] = operation
    out["duration_ms"] = round(duration_ms, 1)
    out.setdefault("warnings", [])
    efectos = _side_effects(payload)
    if efectos:
        out["side_effects"] = efectos
    else:
        out.setdefault("side_effects", [])
    return out


def failure(code: str, message: str, details: Optional[Dict[str, Any]], *,
            operation: str, request_id: str, duration_ms: float,
            exc_type: Optional[str] = None) -> Dict[str, Any]:
    """Construye el envelope de error conservando `error` y `message`."""
    out: Dict[str, Any] = {
        "ok": False,
        "error": code,
        "message": message,
        "status": _status_for_error(code),
        "request_id": request_id,
        "operation": operation,
        "duration_ms": round(duration_ms, 1),
        "warnings": [],
        "side_effects": [],
    }
    if details:
        out["details"] = details
        # Si el error trae informacion de compensacion, se expone como efecto.
        comp = details.get("compensation")
        if isinstance(comp, dict):
            out["side_effects"] = [{
                "kind": "compensation",
                "clean": comp.get("clean"),
                "journal": comp.get("journal"),
                "by_outcome": comp.get("by_outcome"),
            }]
    if exc_type:
        out["type"] = exc_type
    return out


class Timer:
    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_exc):
        return False

    @property
    def ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0
