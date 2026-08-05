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

La reserva tiene que ser ATOMICA
--------------------------------
`comenzar()` hacia leer -> decidir -> escribir sin nada que lo hiciera
indivisible. Dos llamadas simultaneas con el mismo `request_id` leian las dos
que no habia registro, las dos escribian `in_flight` y las dos ejecutaban la
mutacion: exactamente lo que esta pieza existe para impedir. La ventana es
pequena pero real, y un cliente que reintenta por timeout es justo quien la
abre.

Dos cerrojos, porque son dos problemas distintos:

- **Entre procesos**: un cerrojo de archivo (`fcntl.flock` / `msvcrt.locking`)
  sobre `<request_id>.lock`, tomado durante la decision entera. Se eligio el
  cerrojo del sistema y no un archivo centinela porque **el sistema lo suelta
  solo cuando el proceso muere**; un centinela lo dejaria puesto para siempre.
- **Entre hilos**: un `threading.Lock` por `request_id`. En el mismo proceso el
  cerrojo de archivo no siempre distingue dos descriptores, y el servidor
  atiende en hilos.

Y el alta se hace ademas con `O_CREAT | O_EXCL`, que es atomico de por si: si
el cerrojo de archivo no llegara a aplicarse —hay sistemas de archivos en red
que lo ignoran—, dos altas simultaneas siguen sin poder ganar las dos.

El JSON corrupto no se pisa
---------------------------
`leer()` devolvia `None` ante un registro ilegible, asi que la llamada
siguiente lo tomaba por inexistente y lo SOBREESCRIBIA: se habilitaba una
mutacion que quiza ya se habia hecho, y encima se destruia la unica evidencia
de lo ocurrido. Ahora falla cerrado (`idempotency_record_corrupt`) y el
archivo se queda como esta, byte a byte. Nadie lo renombra ni lo borra: eso lo
decide una persona mirandolo.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

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
#: Espera maxima por el cerrojo de un `request_id`. La seccion critica es una
#: lectura y una escritura de unos pocos kilobytes: agotarlo no significa
#: "hay cola", significa que alguien lo tiene tomado y no lo suelta.
LOCK_TIMEOUT_SECONDS = 30.0
_LOCK_STEP = 0.01

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


class RegistroCorruptoError(PowerBIMCPError):
    """El registro de esa peticion existe pero no es JSON valido.

    Se falla cerrado a proposito. Un registro ilegible es la unica prueba de
    que esa peticion paso por aqui: tratarlo como inexistente habilitaria una
    mutacion que quiza ya ocurrio, y sobreescribirlo borraria la evidencia.
    """

    code = "idempotency_record_corrupt"


# ------------------------------------------------------- exclusion mutua ---
#: Un cerrojo por `request_id` dentro del proceso. Se guardan indefinidamente
#: porque un Lock son unas decenas de bytes y el numero de request_id vivos en
#: una sesion es pequeno; purgarlos abriria una carrera al purgar.
_CERROJOS: Dict[str, threading.Lock] = {}
_CERROJOS_LOCK = threading.Lock()


def _cerrojo_de_hilo(clave: str) -> threading.Lock:
    with _CERROJOS_LOCK:
        return _CERROJOS.setdefault(clave, threading.Lock())


if os.name == "nt":                                       # pragma: no cover
    import msvcrt

    def _intentar_tomar(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _soltar(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:                                                     # pragma: no cover
    import fcntl

    def _intentar_tomar(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _soltar(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextlib.contextmanager
def _cerrojo_de_archivo(ruta: Path, request_id: str) -> Iterator[None]:
    """Cerrojo del sistema sobre `ruta`, con espera acotada.

    Se reintenta sin bloquear en vez de usar la espera del sistema: en Windows
    `LK_LOCK` bloquea diez segundos fijos y no hay forma de acotarlo, y colgar
    un hilo del servidor sin limite no es una opcion.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    banderas = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    fd = os.open(ruta, banderas, 0o600)
    try:
        limite = time.time() + LOCK_TIMEOUT_SECONDS
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            if _intentar_tomar(fd):
                break
            if time.time() >= limite:
                raise RequestInProgressError(
                    f"El request_id '{request_id}' lleva mas de "
                    f"{LOCK_TIMEOUT_SECONDS:.0f}s tomado por otra llamada y no "
                    "se ha podido reservar. Espera a que termine antes de "
                    "reintentar.",
                    details={"request_id": request_id,
                             "lock_timeout_seconds": LOCK_TIMEOUT_SECONDS})
            time.sleep(_LOCK_STEP)
        try:
            yield
        finally:
            _soltar(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _exclusion(store: "Store", request_id: str) -> Iterator[None]:
    """Exclusion por `request_id`: primero entre hilos, luego entre procesos.

    El orden es siempre el mismo en todo el modulo; invertirlo en algun sitio
    seria un abrazo mortal entre dos procesos multihilo.
    """
    ruta = store._archivo_cerrojo(request_id)
    with _cerrojo_de_hilo(str(ruta)):
        with _cerrojo_de_archivo(ruta, request_id):
            yield


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

    def _archivo_cerrojo(self, request_id: str) -> Path:
        from horizun_pbi_mcp.services import paths as safe_paths

        seguro = safe_paths.safe_identifier(request_id, kind="request_id")
        # Archivo aparte: tomar el cerrojo no puede crear ni tocar el registro,
        # que es lo que decide si la peticion existe. Y `purgar` mira *.json.
        return self.root / f"{seguro}.lock"

    def _corrupto(self, f: Path, request_id: str) -> RegistroCorruptoError:
        return RegistroCorruptoError(
            f"El registro de idempotencia de '{request_id}' existe pero no es "
            "JSON valido. No se ejecuta la operacion ni se sobreescribe el "
            "archivo: es la unica prueba de que esa peticion paso por aqui y "
            "no se sabe si llego a aplicarse. Revisalo y decide tu si "
            f"borrarlo: {f}",
            details={"request_id": request_id, "path": str(f),
                     "recovery": "Inspecciona el archivo. Si el cambio NO se "
                                 "aplico, borralo a mano y reintenta. Si se "
                                 "aplico, borralo y NO reintentes."})

    def leer(self, request_id: str) -> Optional[Registro]:
        f = self._archivo(request_id)
        if not f.exists():
            return None
        try:
            crudo = f.read_text(encoding="utf-8")
        except OSError:
            log.warning("Registro de idempotencia ilegible: %s", f)
            return None
        try:
            datos = json.loads(crudo)
        except ValueError as exc:
            # Antes esto devolvia None y la llamada siguiente lo pisaba.
            log.error("Registro de idempotencia corrupto: %s (%s)", f, exc)
            raise self._corrupto(f, request_id) from exc
        reg = Registro.from_dict(datos)
        if time.time() - reg.created_at > TTL_SECONDS:
            return None
        return reg

    def _no_pisar_corrupto(self, request_id: str) -> None:
        """Invariante: nunca se sobreescribe un JSON que no parsea."""
        f = self._archivo(request_id)
        if not f.exists():
            return
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise self._corrupto(f, request_id) from exc
        except OSError:
            return          # ilegible por permisos: no es corrupcion probada

    def escribir(self, reg: Registro) -> None:
        from horizun_pbi_mcp.services.txn import durable_write

        self._no_pisar_corrupto(reg.request_id)
        reg.updated_at = time.time()
        self.root.mkdir(parents=True, exist_ok=True)
        durable_write(self._archivo(reg.request_id),
                      json.dumps(reg.to_dict(), ensure_ascii=False,
                                 indent=2).encode("utf-8"))

    def reservar(self, reg: Registro) -> bool:
        """Crea el registro SOLO si no existia. `True` si la reserva es nuestra.

        `O_CREAT | O_EXCL` es atomico en el sistema de archivos: de dos altas
        simultaneas, exactamente una lo consigue. Es la garantia de ultimo
        recurso, por debajo del cerrojo, para el unico caso que importa de
        verdad —dos llamadas que no ven registro— y el unico que sobrevive a un
        sistema de archivos que ignore los cerrojos.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        reg.updated_at = time.time()
        datos = json.dumps(reg.to_dict(), ensure_ascii=False,
                           indent=2).encode("utf-8")
        try:
            fd = os.open(self._archivo(reg.request_id),
                         os.O_CREAT | os.O_EXCL | os.O_WRONLY
                         | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "wb") as fh:
            fh.write(datos)
            fh.flush()
            os.fsync(fh.fileno())
        return True

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
#: Veredictos de `_decidir`. No son parte del contrato publico.
_EJECUTAR = "ejecutar"
_REPRODUCIR = "reproducir"
_EN_VUELO = "en_vuelo"


def comenzar(store: Store, request_id: str, operation: str,
             payload: Any) -> Optional[Dict[str, Any]]:
    """Abre (o resuelve) una peticion.

    Devuelve el resultado guardado si es un reintento ya resuelto; `None` si hay
    que ejecutar la mutacion. Lanza si hay conflicto o si sigue en vuelo.

    La decision se toma entera bajo el cerrojo del `request_id` —leer y
    reservar tienen que ser indivisibles o dos llamadas ejecutan la misma
    mutacion—, pero la ESPERA se hace fuera: quien esta ejecutando tiene que
    poder escribir su resultado, y si el que espera retuviera el cerrojo no
    podria. Por eso es un bucle y no una llamada anidada.
    """
    fp = plan_contract.fingerprint_de(payload)
    limite = time.time() + WAIT_SECONDS
    while True:
        with _exclusion(store, request_id):
            veredicto, valor = _decidir(store, request_id, operation, fp)
        if veredicto == _EJECUTAR:
            return None
        if veredicto == _REPRODUCIR:
            return valor
        if time.time() >= limite:
            raise RequestInProgressError(
                f"El request_id '{request_id}' se esta ejecutando ahora mismo. "
                "Espera a que termine antes de reintentar.",
                details={"request_id": request_id, "operation": valor.operation,
                         "age_seconds": round(valor.edad, 1)})
        time.sleep(_WAIT_STEP)


def _decidir(store: Store, request_id: str, operation: str, fp: str):
    """Que hacer con esta peticion. Se llama SIEMPRE con el cerrojo tomado."""
    reg = store.leer(request_id)

    if reg is None:
        nuevo = Registro(request_id=request_id, operation=operation,
                         payload_fingerprint=fp, state=IN_FLIGHT)
        if store.reservar(nuevo):
            return _EJECUTAR, None
        # Alguien gano el alta entre la lectura y esta linea, aun teniendo el
        # cerrojo: solo puede pasar si el cerrojo no se aplico. Se vuelve a
        # decidir con lo que hay ahora, que es lo unico honesto.
        log.warning("La reserva de %s la gano otra llamada: se reevalua.",
                    request_id)
        reg = store.leer(request_id)
        if reg is None:                                   # pragma: no cover
            return _EN_VUELO, Registro(request_id=request_id,
                                       operation=operation,
                                       payload_fingerprint=fp, state=IN_FLIGHT)

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
        return _REPRODUCIR, salida

    if reg.state == IN_FLIGHT:
        if reg.edad <= STALE_IN_FLIGHT_SECONDS:
            return _EN_VUELO, reg
        # Lleva parado demasiado: el proceso que lo abrio murio. Se reclama.
        # No se afirma que la mutacion no ocurriera, solo que nadie la vigila.
        log.warning("Peticion %s abandonada en vuelo hace %.0fs: se reclama.",
                    request_id, reg.edad)

    # in_flight abandonado, failed o compensated: se reabre y se ejecuta.
    store.escribir(Registro(request_id=request_id, operation=operation,
                            payload_fingerprint=fp, state=IN_FLIGHT))
    return _EJECUTAR, None


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
