"""Idempotencia real de las mutaciones: estados, persistencia y conflictos.

Que habia antes
---------------
`services.operations` tenia `comprobar_request()` y `guardar_resultado()`, pero
**nadie los llamaba**. `guard()` generaba un `request_id` nuevo en cada llamada,
asi que dos peticiones identicas del cliente siempre se ejecutaban dos veces.
La garantia estaba documentada, no implementada.

El protocolo
------------
Cuatro estados, y un registro que sobrevive al proceso:

``in_flight``    la mutacion empezo y no se sabe como acabo.
``succeeded``    acabo bien; se guarda el resultado y se reproduce tal cual.
``failed``       acabo mal; se guarda si es seguro reintentar.
``compensated``  fallo pero se deshizo del todo: reintentar es seguro.

Reglas:

- mismo `request_id` + mismo payload + ``succeeded`` -> devuelve lo guardado sin
  volver a mutar;
- mismo `request_id` + payload distinto -> `idempotency_conflict`;
- mismo `request_id` mientras esta ``in_flight`` -> espera acotada y, si sigue,
  `request_in_progress`;
- ``failed`` -> se devuelve el error con `safe_to_retry` explicito;
- ``compensated`` -> se puede reintentar.

Un `in_flight` de un proceso que murio no puede bloquear para siempre: pasado
`STALE_IN_FLIGHT_SECONDS` se considera abandonado y se permite reintentar,
marcandolo como `failed` con `safe_to_retry=False` —porque nadie sabe si la
escritura llego a ocurrir, y decir lo contrario seria mentir—.

Persistencia
------------
Un archivo JSON por `request_id` bajo `<backups>/_idempotency/`, escrito con
`durable_write` (tmp + fsync + replace). Un proceso interrumpido a mitad deja el
registro anterior intacto, nunca un archivo a medias.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import plan_contract

log = get_logger("idempotency")

#: Cuanto se recuerda un request_id (segundos).
TTL_SECONDS = 24 * 3600
#: Pasado esto, un in_flight se considera de un proceso muerto.
STALE_IN_FLIGHT_SECONDS = 300
#: Espera maxima ante un in_flight vivo, antes de devolver request_in_progress.
WAIT_SECONDS = 2.0
_WAIT_STEP = 0.05

IN_FLIGHT = "in_flight"
SUCCEEDED = "succeeded"
FAILED = "failed"
COMPENSATED = "compensated"


class IdempotencyConflictError(PowerBIMCPError):
    """El mismo request_id llego con un payload distinto."""

    code = "idempotency_conflict"


class RequestInProgressError(PowerBIMCPError):
    """Ese request_id se esta ejecutando ahora mismo en otra llamada."""

    code = "request_in_progress"


@dataclass
class Registro:
    request_id: str
    operation: str
    payload_fingerprint: str
    state: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    safe_to_retry: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"request_id": self.request_id, "operation": self.operation,
                "payload_fingerprint": self.payload_fingerprint,
                "state": self.state, "created_at": self.created_at,
                "updated_at": self.updated_at, "result": self.result,
                "error": self.error, "safe_to_retry": self.safe_to_retry}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Registro":
        return Registro(
            request_id=d["request_id"], operation=d.get("operation", ""),
            payload_fingerprint=d.get("payload_fingerprint", ""),
            state=d.get("state", FAILED),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            result=d.get("result"), error=d.get("error"),
            safe_to_retry=d.get("safe_to_retry"))

    @property
    def edad(self) -> float:
        return time.time() - self.updated_at


class Store:
    """Registro persistente de peticiones. Un archivo por `request_id`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _archivo(self, request_id: str) -> Path:
        from horizun_pbi_mcp.services import paths as safe_paths

        # El request_id viene del cliente: nunca se concatena a una ruta sin
        # validarlo, o un '..' escribiria fuera del directorio del registro.
        seguro = safe_paths.safe_identifier(request_id, kind="request_id")
        return self.root / f"{seguro}.json"

    def leer(self, request_id: str) -> Optional[Registro]:
        f = self._archivo(request_id)
        if not f.exists():
            return None
        try:
            datos = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            log.warning("Registro de idempotencia ilegible, se ignora: %s", f)
            return None
        reg = Registro.from_dict(datos)
        if time.time() - reg.created_at > TTL_SECONDS:
            return None
        return reg

    def escribir(self, reg: Registro) -> None:
        from horizun_pbi_mcp.services.txn import durable_write

        reg.updated_at = time.time()
        self.root.mkdir(parents=True, exist_ok=True)
        durable_write(self._archivo(reg.request_id),
                      json.dumps(reg.to_dict(), ensure_ascii=False,
                                 indent=2).encode("utf-8"))

    def borrar(self, request_id: str) -> None:
        try:
            self._archivo(request_id).unlink(missing_ok=True)
        except OSError:                                     # pragma: no cover
            pass

    def purgar(self) -> int:
        """Elimina registros caducados. Devuelve cuantos."""
        if not self.root.exists():
            return 0
        n = 0
        for f in self.root.glob("*.json"):
            try:
                datos = json.loads(f.read_text(encoding="utf-8"))
                if time.time() - datos.get("created_at", 0) > TTL_SECONDS:
                    f.unlink(missing_ok=True)
                    n += 1
            except (ValueError, OSError):
                continue
        return n


def store_por_defecto() -> Store:
    from horizun_pbi_mcp.config import get_settings

    base = getattr(get_settings(), "backups_dir", None) or Path("backups")
    return Store(Path(base) / "_idempotency")


# ------------------------------------------------------------------ protocolo ---
def comenzar(store: Store, request_id: str, operation: str,
             payload: Any) -> Optional[Dict[str, Any]]:
    """Abre (o resuelve) una peticion.

    Devuelve el resultado guardado si es un reintento ya resuelto; `None` si hay
    que ejecutar la mutacion. Lanza si hay conflicto o si sigue en vuelo.
    """
    fp = plan_contract.fingerprint_de(payload)
    reg = store.leer(request_id)

    if reg is None:
        store.escribir(Registro(request_id=request_id, operation=operation,
                                payload_fingerprint=fp, state=IN_FLIGHT))
        return None

    if reg.payload_fingerprint != fp:
        raise IdempotencyConflictError(
            f"El request_id '{request_id}' ya se uso con argumentos distintos "
            f"(operacion '{reg.operation}', estado '{reg.state}'). Usa un "
            "request_id nuevo, o repite exactamente los mismos argumentos.",
            details={"request_id": request_id, "stored_operation": reg.operation,
                     "stored_state": reg.state})

    if reg.state == SUCCEEDED and reg.result is not None:
        salida = dict(reg.result)
        salida["idempotent_replay"] = True
        return salida

    if reg.state == IN_FLIGHT:
        reg = _esperar_o_reclamar(store, reg, fp)
        if reg is None:
            return None                       # se reclamo: hay que ejecutar
        if reg.state == SUCCEEDED and reg.result is not None:
            salida = dict(reg.result)
            salida["idempotent_replay"] = True
            return salida
        raise RequestInProgressError(
            f"El request_id '{request_id}' se esta ejecutando ahora mismo. "
            "Espera a que termine antes de reintentar.",
            details={"request_id": request_id, "operation": reg.operation,
                     "age_seconds": round(reg.edad, 1)})

    # failed / compensated: se puede reintentar, se reabre.
    store.escribir(Registro(request_id=request_id, operation=operation,
                            payload_fingerprint=fp, state=IN_FLIGHT))
    return None


def _esperar_o_reclamar(store: Store, reg: Registro,
                        fp: str) -> Optional[Registro]:
    """Espera acotada a que un in_flight se resuelva.

    Si lleva parado mas de `STALE_IN_FLIGHT_SECONDS`, el proceso que lo abrio
    murio: se reclama la entrada. No se afirma que la mutacion no ocurriera.
    """
    if reg.edad > STALE_IN_FLIGHT_SECONDS:
        log.warning("Peticion %s abandonada en vuelo hace %.0fs: se reclama.",
                    reg.request_id, reg.edad)
        store.escribir(Registro(request_id=reg.request_id, operation=reg.operation,
                                payload_fingerprint=fp, state=IN_FLIGHT))
        return None

    limite = time.time() + WAIT_SECONDS
    while time.time() < limite:
        time.sleep(_WAIT_STEP)
        actual = store.leer(reg.request_id)
        if actual is None or actual.state != IN_FLIGHT:
            return actual
    return reg


def terminar_ok(store: Store, request_id: str, operation: str, payload: Any,
                resultado: Dict[str, Any]) -> None:
    store.escribir(Registro(
        request_id=request_id, operation=operation,
        payload_fingerprint=plan_contract.fingerprint_de(payload),
        state=SUCCEEDED, result=resultado, safe_to_retry=None))


def terminar_error(store: Store, request_id: str, operation: str, payload: Any,
                   error: Dict[str, Any], *, safe_to_retry: bool,
                   compensado: bool = False) -> None:
    """Cierra una peticion fallida diciendo si reintentar es seguro.

    `compensado=True` significa que el cambio se deshizo por completo: el estado
    es el de antes y reintentar no duplica nada.
    """
    store.escribir(Registro(
        request_id=request_id, operation=operation,
        payload_fingerprint=plan_contract.fingerprint_de(payload),
        state=COMPENSATED if compensado else FAILED,
        error=error, safe_to_retry=True if compensado else safe_to_retry))


def estado(store: Store, request_id: str) -> Optional[Dict[str, Any]]:
    reg = store.leer(request_id)
    return reg.to_dict() if reg else None
