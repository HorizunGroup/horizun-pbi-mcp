"""Idempotencia, planes y reintentos de las operaciones mutantes.

Tres problemas distintos que se resuelven aqui:

1. REINTENTOS. Un cliente MCP puede reenviar la misma peticion (timeout, red,
   reintento del agente). Si trae el mismo `request_id` y los mismos argumentos,
   se devuelve el resultado guardado en vez de aplicar el cambio dos veces.

2. CONFLICTO DE IDENTIDAD. Si llega el mismo `request_id` con argumentos
   DISTINTOS, es un error del cliente: se rechaza en vez de adivinar.

3. PLANES. `dry_run` produce un plan con un `plan_token` que captura el
   fingerprint del estado sobre el que se calculo. Al aplicar, si el estado
   cambio, el token esta obsoleto y se rechaza: el plan que el usuario aprobo ya
   no describe lo que iba a pasar.

Todo vive en memoria del proceso: es una salvaguarda dentro de una sesion del
servidor, no un almacen duradero.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from powerbi.errors import PowerBIMCPError

#: Cuanto se recuerda un request_id o un plan (segundos).
TTL_SECONDS = 3600
#: Tope de entradas, para no crecer sin limite en sesiones largas.
MAX_ENTRIES = 500


class RequestIdConflictError(PowerBIMCPError):
    """El mismo request_id llego con argumentos distintos."""

    code = "request_id_conflict"


class PlanTokenStaleError(PowerBIMCPError):
    """El plan ya no describe el estado actual: se calculo sobre otro."""

    code = "plan_token_stale"


class PlanNotFoundError(PowerBIMCPError):
    """El plan_token no existe o expiro."""

    code = "plan_not_found"


def args_fingerprint(payload: Any) -> str:
    """Huella estable de unos argumentos, independiente del orden de claves."""
    texto = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:32]


@dataclass
class _Entrada:
    fingerprint: str
    resultado: Optional[Dict[str, Any]]
    creada: float = field(default_factory=time.monotonic)


@dataclass
class Plan:
    plan_token: str
    operation: str
    args_fingerprint: str
    state_fingerprint: str
    plan: Dict[str, Any]
    creado: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {"plan_token": self.plan_token, "operation": self.operation,
                "plan": self.plan}


class _Registro:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._requests: Dict[str, _Entrada] = {}
        self._planes: Dict[str, Plan] = {}

    def _purgar(self) -> None:
        ahora = time.monotonic()
        for almacen in (self._requests, self._planes):
            viejos = [k for k, v in almacen.items()
                      if ahora - getattr(v, "creada", ahora) > TTL_SECONDS]
            for k in viejos:
                almacen.pop(k, None)
            while len(almacen) > MAX_ENTRIES:
                almacen.pop(next(iter(almacen)), None)

    # -- idempotencia -------------------------------------------------------
    def comprobar_request(self, request_id: Optional[str],
                          args: Any) -> Optional[Dict[str, Any]]:
        """Devuelve el resultado guardado si es un reintento identico."""
        if not request_id:
            return None
        fp = args_fingerprint(args)
        with self._lock:
            self._purgar()
            entrada = self._requests.get(request_id)
            if entrada is None:
                self._requests[request_id] = _Entrada(fingerprint=fp, resultado=None)
                return None
            if entrada.fingerprint != fp:
                raise RequestIdConflictError(
                    f"El request_id '{request_id}' ya se uso en esta sesion con "
                    "argumentos distintos. Usa un request_id nuevo, o repite "
                    "exactamente los mismos argumentos para reintentar.",
                    details={"request_id": request_id})
            if entrada.resultado is not None:
                out = dict(entrada.resultado)
                out["idempotent_replay"] = True
                return out
            return None

    def guardar_resultado(self, request_id: Optional[str],
                          resultado: Dict[str, Any]) -> None:
        if not request_id:
            return
        with self._lock:
            entrada = self._requests.get(request_id)
            if entrada is not None:
                entrada.resultado = resultado

    # -- planes -------------------------------------------------------------
    def crear_plan(self, operation: str, args: Any, state_fingerprint: str,
                   plan: Dict[str, Any]) -> Plan:
        token = f"plan_{uuid.uuid4().hex[:20]}"
        p = Plan(plan_token=token, operation=operation,
                 args_fingerprint=args_fingerprint(args),
                 state_fingerprint=state_fingerprint, plan=plan)
        with self._lock:
            self._purgar()
            self._planes[token] = p
        return p

    def plan_por_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Sobre del plan, SIN validar frescura.

        Existe para poder rechazar un sobre por version, caducidad o proyecto
        antes de tocar el disco: comprobar la huella de estado exige leer
        archivos, y no tiene sentido leerlos si el plan ya es invalido.
        """
        with self._lock:
            self._purgar()
            p = self._planes.get(token)
        return p.plan if p is not None else None

    def obtener_plan(self, token: str, state_fingerprint: str) -> Plan:
        with self._lock:
            self._purgar()
            p = self._planes.get(token)
        if p is None:
            raise PlanNotFoundError(
                f"El plan '{token}' no existe o expiro. Vuelve a generarlo con "
                "dry_run=true.", details={"plan_token": token})
        if p.state_fingerprint != state_fingerprint:
            raise PlanTokenStaleError(
                "El proyecto cambio desde que se calculo este plan: aplicarlo "
                "haria algo distinto de lo aprobado. Genera el plan de nuevo.",
                details={"plan_token": token,
                         "expected_state": p.state_fingerprint,
                         "actual_state": state_fingerprint})
        return p

    def consumir_plan(self, token: str) -> None:
        with self._lock:
            self._planes.pop(token, None)

    def planes_vivos(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._purgar()
            return [{"plan_token": p.plan_token, "operation": p.operation,
                     "age_seconds": round(time.monotonic() - p.creado, 1)}
                    for p in self._planes.values()]

    def limpiar(self) -> None:
        with self._lock:
            self._requests.clear()
            self._planes.clear()


_REGISTRO = _Registro()


def registro() -> _Registro:
    return _REGISTRO


def state_fingerprint_of(paths: List[Any]) -> str:
    """Huella del estado de un conjunto de archivos (existan o no)."""
    from services.txn import fingerprint

    partes = []
    for p in sorted(str(x) for x in paths):
        fp = fingerprint(p)
        partes.append(f"{p}:{fp.state}:{fp.sha256 or '-'}")
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()[:32]
