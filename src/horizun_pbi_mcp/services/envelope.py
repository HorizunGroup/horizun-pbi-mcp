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
    "active_model_project_mismatch",
    "idempotency_attempt_superseded",
    "idempotency_conflict",
    "page_conflict",
    "plan_expired",
    "plan_operation_mismatch",
    "plan_payload_tampered",
    "plan_project_mismatch",
    "project_open_in_desktop",
    "recovery_conflict",
    "request_in_progress",
    "request_outcome_unknown",
    "stale_session",
    "dual_mode_not_safely_available",
    "measure_exists",
    "plan_token_stale",
    "request_id_conflict",
}

_ROLLBACK_CODES = {
    "rollback_incomplete",
    "bulk_partially_applied",
    "temporary_patch_not_restored",
}


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


def _side_effects(payload: Dict[str, Any], operation: str) -> List[Dict[str, Any]]:
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
    rutas_artefacto = []
    for clave in ("output_path", "output_pbix"):
        ruta = payload.get(clave)
        if ruta and ruta not in rutas_artefacto:
            rutas_artefacto.append(ruta)
    captura = payload.get("capture")
    if isinstance(captura, dict) and captura.get("path"):
        rutas_artefacto.append(captura["path"])
    for ruta in rutas_artefacto:
        efectos.append({"kind": "artifact", "path": ruta})

    # Los efectos de Desktop y refresh no dejan necesariamente una transaccion
    # ni un output_path. Sin estas entradas, las tools mas visibles parecian no
    # haber tocado nada pese a abrir/cerrar una aplicacion o mutar el modelo en
    # memoria. Solo se anuncian cuando el payload acredita que ocurrieron.
    if operation in {"pbi_open_in_desktop", "pbi_open_and_refresh",
                     "pbi_validate_desktop_render"} and payload.get("desktop_pid"):
        efectos.append({
            "kind": "desktop",
            "action": ("opened" if payload.get("launched_by_us") else "reused"),
            "pid": payload.get("desktop_pid"),
        })
    sesion_desktop = payload.get("desktop_session")
    if operation in {"pbi_export_pbix", "pbi_finalize_delivery"} \
            and isinstance(sesion_desktop, dict):
        efectos.append({
            "kind": "desktop",
            "action": "left_open" if sesion_desktop.get("closable") else "used",
            "pid": sesion_desktop.get("desktop_pid"),
        })
    if operation == "pbi_close_desktop" and payload.get("was_open"):
        efectos.append({
            "kind": "desktop",
            "action": "closed" if payload.get("verified_closed") else "close_attempted",
            "pid": payload.get("desktop_pid") or payload.get("pid"),
        })
    refresco = payload if operation == "pbi_refresh_model" else payload.get("refresh")
    if isinstance(refresco, dict) and refresco.get("status") == "ok":
        efectos.append({
            "kind": "live_model",
            "action": "refresh",
            "persisted": False,
            "refresh_type": refresco.get("refresh_type"),
        })
    return efectos


def _dedupe_warnings(avisos: Any) -> Any:
    """Colapsa avisos IDENTICOS en uno con su cuenta: «mensaje (×14)».

    Una llamada a pbi_apply_page_spec con 14 visuales devolvia 14 copias
    literales de «No habia un visual de este tipo para clonar...». Repetir un
    aviso no lo hace mas cierto: solo gasta la ventana de contexto del agente
    que lo lee. Se conserva el ORDEN de primera aparicion y el tipo (lista de
    cadenas), asi que quien busque un texto por `in` lo sigue encontrando.
    """
    if not isinstance(avisos, list):
        return avisos
    cuenta: Dict[str, int] = {}
    orden: List[str] = []
    for aviso in avisos:
        if not isinstance(aviso, str):
            return avisos  # forma inesperada: mejor intacta que adivinada
        if aviso not in cuenta:
            orden.append(aviso)
        cuenta[aviso] = cuenta.get(aviso, 0) + 1
    return [a if cuenta[a] == 1 else f"{a} (×{cuenta[a]})" for a in orden]


def success(payload: Dict[str, Any], *, operation: str, request_id: str,
            duration_ms: float) -> Dict[str, Any]:
    """Construye el envelope de exito conservando el payload original."""
    if not isinstance(payload, dict):
        payload = {"result": payload}
    out: Dict[str, Any] = {"ok": True}
    out.update(payload)                      # los campos originales, intactos
    # `status` y `operation` tambien son nombres del envelope. Antes se
    # perdian silenciosamente cuando un servicio los usaba para su resultado
    # de negocio (p.ej. apply_plan: no_change/applied). Se preservan de forma
    # aditiva con nombres sin colision; los nombres publicos del envelope
    # conservan su significado y compatibilidad.
    if "status" in payload:
        out["result_status"] = payload["status"]
    if "operation" in payload:
        out["target_operation"] = payload["operation"]
    out["status"] = _status_for_success(payload)
    out["request_id"] = request_id
    out["operation"] = operation
    out["duration_ms"] = round(duration_ms, 1)
    out.setdefault("warnings", [])
    out["warnings"] = _dedupe_warnings(out["warnings"])
    efectos_existentes = out.get("side_effects")
    acumulados = list(efectos_existentes) if isinstance(efectos_existentes, list) else []
    for efecto in _side_effects(payload, operation):
        if efecto not in acumulados:
            acumulados.append(efecto)
    out["side_effects"] = acumulados
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
