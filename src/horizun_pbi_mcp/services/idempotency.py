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

Lo que NO se hace: reclamar un `in_flight` por viejo
----------------------------------------------------
Antes, pasado `STALE_IN_FLIGHT_SECONDS`, un `in_flight` se daba por abandonado
y se AUTORIZABA otra ejecucion. Eso era una carrera con la mutacion todavia
corriendo:

1. `comenzar()` autoriza y arranca la mutacion A;
2. A tarda mas del umbral —`pbi_open_and_refresh` sobre un modelo grande lo
   pasa sin despeinarse— y sigue perfectamente viva;
3. una segunda llamada ve el registro "antiguo", lo reclama y arranca B;
4. A termina y su `terminar_ok()` pisa el registro de B.

Dos mutaciones con el mismo `request_id`, que es exactamente lo que esta pieza
existe para impedir. **Antiguo no prueba muerto**, y aunque el proceso hubiera
muerto, nadie sabe si la escritura llego a aplicarse.

Ahora se falla cerrado: `request_outcome_unknown` con `safe_to_retry=False`.
El error dice si el proceso dueno sigue vivo —diagnostico, no permiso— y como
salir. Reabrir esa peticion es una decision de quien mira el proyecto y
comprueba si el cambio esta o no: `descartar_en_vuelo()`.

Identidad de intento
--------------------
Cada autorizacion acuna un `attempt_id` irrepetible y anota el proceso dueno.
Cerrar es un compare-and-set bajo la misma exclusion que la reserva: si el
registro ya pertenece a otro intento, el resultado NO se escribe
(`idempotency_attempt_superseded`). Un intento antiguo que despierta tarde no
puede completar ni borrar el rastro de uno nuevo.

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
import uuid
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


class ResultadoDesconocidoError(PowerBIMCPError):
    """Hubo un intento anterior y no se sabe como acabo.

    Es el estado que antes se resolvia solo, y mal: un `in_flight` viejo se
    daba por muerto y se AUTORIZABA otra ejecucion. "Antiguo" no prueba nada.
    Un `pbi_open_and_refresh` puede tardar mas que el umbral y estar
    perfectamente vivo; y aunque el proceso hubiera muerto, nadie sabe si la
    escritura llego a aplicarse. Autorizar ahi es duplicar la mutacion.

    Se falla cerrado y la salida es de quien mira, no del servidor.
    """

    code = "request_outcome_unknown"


class IntentoCaducadoError(PowerBIMCPError):
    """Se intento cerrar una peticion que ya pertenece a otro intento.

    Un intento antiguo no puede completar ni pisar el registro de uno nuevo:
    su resultado describe una ejecucion que ya nadie esta esperando.
    """

    code = "idempotency_attempt_superseded"


class RegistroIlegibleError(PowerBIMCPError):
    """El registro existe y no se pudo leer. No es lo mismo que no existir."""

    code = "idempotency_record_unreadable"


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
#: La primitiva de cerrojo vive en `services/cerrojo.py` desde CORE-006. Estaba
#: aqui, bien hecha, y el camino que ESCRIBE el proyecto no tenia ninguna; la
#: respuesta no era escribir una segunda -dos implementaciones del mismo
#: mecanismo son dos formas distintas de quedarse a medias- sino extraer esta y
#: usarla en los dos sitios. El comportamiento no cambia.
from horizun_pbi_mcp.services import cerrojo as _cerrojo  # noqa: E402


@contextlib.contextmanager
def _exclusion(store: "Store", request_id: str) -> Iterator[None]:
    """Exclusion por `request_id`: primero entre hilos, luego entre procesos.

    El orden es siempre el mismo en todo el modulo; invertirlo en algun sitio
    seria un abrazo mortal entre dos procesos multihilo.
    """
    ruta = store._archivo_cerrojo(request_id)

    def _agotado(timeout: float) -> Exception:
        return RequestInProgressError(
            f"El request_id '{request_id}' lleva mas de {timeout:.0f}s tomado "
            "por otra llamada y no se ha podido reservar. Espera a que termine "
            "antes de reintentar.",
            details={"request_id": request_id,
                     "lock_timeout_seconds": timeout})

    with _cerrojo.exclusion(ruta, timeout=LOCK_TIMEOUT_SECONDS,
                            al_agotarse=_agotado):
        yield


def _proceso_vivo(pid: Optional[int], arranque: Optional[float]) -> Optional[bool]:
    """Si el proceso que abrio la peticion sigue existiendo.

    `None` cuando no se puede saber —no se guardo el dueno, o no hay permisos
    para preguntarlo—. Se compara TAMBIEN el instante de arranque: los pid se
    reciclan, y confundir un proceso nuevo con el que murio seria peor que no
    mirar nada.

    Esto NO autoriza nada por si solo. Que el dueno haya muerto no prueba que
    la mutacion no se aplicara: solo dice que ya no hay nadie vigilandola.
    """
    if not pid:
        return None
    try:
        import psutil
    except Exception:                                      # noqa: BLE001
        return None
    try:
        p = psutil.Process(pid)
        if arranque and abs(p.create_time() - arranque) > 1.0:
            return False                      # ese pid es de OTRO proceso
        return p.is_running()
    except psutil.NoSuchProcess:
        # No existe es NO EXISTE, no "no se sabe": son dos diagnosticos
        # distintos y mezclarlos deja al que lee sin la unica pista util.
        return False
    except Exception:                                      # noqa: BLE001
        return None                            # sin permisos, por ejemplo


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
    #: Identidad del INTENTO, no de la peticion. Se acuna una vez, al
    #: autorizar, y no se reutiliza jamas: es lo que permite comprobar al
    #: cerrar que quien escribe el resultado sigue siendo el dueno.
    attempt_id: Optional[str] = None
    #: Quien lo abrio. Sirve para saber si sigue vivo, no para autorizar.
    owner_pid: Optional[int] = None
    owner_started: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"request_id": self.request_id, "operation": self.operation,
                "payload_fingerprint": self.payload_fingerprint,
                "state": self.state, "created_at": self.created_at,
                "updated_at": self.updated_at, "result": self.result,
                "error": self.error, "safe_to_retry": self.safe_to_retry,
                "attempt_id": self.attempt_id, "owner_pid": self.owner_pid,
                "owner_started": self.owner_started}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Registro":
        return Registro(
            request_id=d["request_id"], operation=d.get("operation", ""),
            payload_fingerprint=d.get("payload_fingerprint", ""),
            state=d.get("state", FAILED),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            result=d.get("result"), error=d.get("error"),
            safe_to_retry=d.get("safe_to_retry"),
            # Los tres son opcionales a proposito: un registro escrito por una
            # version anterior no los trae y tiene que seguir leyendose.
            attempt_id=d.get("attempt_id"), owner_pid=d.get("owner_pid"),
            owner_started=d.get("owner_started"))

    @property
    def edad(self) -> float:
        return time.time() - self.updated_at

    @property
    def dueno_vivo(self) -> Optional[bool]:
        return _proceso_vivo(self.owner_pid, self.owner_started)


def _mi_arranque() -> Optional[float]:
    try:
        import psutil

        return psutil.Process(os.getpid()).create_time()
    except Exception:                                      # noqa: BLE001
        return None


def _nuevo_intento(request_id: str, operation: str, fp: str) -> Registro:
    """Un intento nuevo, con identidad propia y dueno declarado."""
    return Registro(request_id=request_id, operation=operation,
                    payload_fingerprint=fp, state=IN_FLIGHT,
                    attempt_id=uuid.uuid4().hex,
                    owner_pid=os.getpid(), owner_started=_mi_arranque())


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
        except OSError as exc:
            # Antes esto devolvia None, y "no se pudo leer" se convertia en
            # "no existe": permiso para volver a mutar por un fallo de disco.
            log.error("Registro de idempotencia ilegible: %s (%s)", f, exc)
            raise RegistroIlegibleError(
                f"El registro de idempotencia de '{request_id}' existe pero no "
                f"se pudo leer ({exc}). No se ejecuta la operacion: no poder "
                "comprobar si ya se hizo no es lo mismo que saber que no se "
                "hizo. Revisa permisos y el estado del disco.",
                details={"request_id": request_id, "path": str(f),
                         "os_error": str(exc)}) from exc
        try:
            datos = json.loads(crudo)
        except ValueError as exc:
            # Antes esto devolvia None y la llamada siguiente lo pisaba.
            log.error("Registro de idempotencia corrupto: %s (%s)", f, exc)
            raise self._corrupto(f, request_id) from exc
        # El TTL ya NO se aplica aqui. Ocultar un registro por viejo convertia
        # "caducado" en "no existe": un in_flight o un fallo inseguro de mas
        # de 24h volvia a autorizarse —el reclaim inseguro, por la puerta del
        # calendario—. Quien caduca es `purgar()`, y solo lo terminal
        # inequivocamente seguro.
        return Registro.from_dict(datos)

    def _no_pisar_corrupto(self, request_id: str) -> None:
        """Invariante: nunca se sobreescribe un JSON que no parsea."""
        f = self._archivo(request_id)
        if not f.exists():
            return
        try:
            json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise self._corrupto(f, request_id) from exc
        except OSError as exc:
            # No es corrupcion probada, pero tampoco permiso para pisarlo: si
            # no se puede leer lo que hay, no se puede saber que se destruye.
            raise RegistroIlegibleError(
                f"No se pudo leer el registro de '{request_id}' antes de "
                f"reescribirlo ({exc}). No se sobreescribe a ciegas.",
                details={"request_id": request_id, "path": str(f),
                         "os_error": str(exc)}) from exc

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

        Son dos pasos y tienen que serlo: el `O_EXCL` **reclama el hueco** y el
        `durable_write` **pone el contenido** con tmp + fsync + replace. Antes
        se escribia directo sobre el descriptor y un corte a mitad dejaba un
        registro parcial. Ahora un corte entre los dos pasos deja el archivo
        vacio, que no es JSON valido y por tanto bloquea —que es lo correcto:
        si se murio reservando, nadie sabe si la mutacion llego a empezar—.
        """
        from horizun_pbi_mcp.services.txn import durable_write

        self.root.mkdir(parents=True, exist_ok=True)
        destino = self._archivo(reg.request_id)
        try:
            os.close(os.open(destino, os.O_CREAT | os.O_EXCL | os.O_WRONLY
                             | getattr(os, "O_BINARY", 0), 0o600))
        except FileExistsError:
            return False
        reg.updated_at = time.time()
        durable_write(destino, json.dumps(reg.to_dict(), ensure_ascii=False,
                                          indent=2).encode("utf-8"))
        return True

    def borrar(self, request_id: str) -> None:
        try:
            self._archivo(request_id).unlink(missing_ok=True)
        except OSError:                                     # pragma: no cover
            pass

    def purgar(self) -> int:
        """Elimina SOLO lo terminal inequivocamente seguro y caducado.

        Purgar era la otra puerta del reclaim: borraba por edad cualquier
        registro —incluido un `in_flight` o un fallo inseguro— y el siguiente
        `comenzar()` con ese request_id volvia a autorizarse. **La duda no
        prescribe**: lo incierto se queda hasta que una persona lo resuelva.

        Se puede ir: `succeeded`, `compensated` y `failed` con
        `safe_to_retry=True`, pasado el TTL. Se queda: todo `in_flight`, los
        fallos inseguros o sin veredicto, y lo corrupto o ilegible, que es
        evidencia.

        Cada borrado se decide bajo la exclusion de SU request_id, releyendo
        el registro con el cerrojo tomado: entre ver el archivo y borrarlo,
        otra llamada pudo reabrir la peticion, y borrar eso seria matar un
        intento vivo por una lectura vieja.
        """
        if not self.root.exists():
            return 0
        n = 0
        for f in self.root.glob("*.json"):
            try:
                with _exclusion(self, f.stem):
                    reg = self.leer(f.stem)
                    if reg is None or \
                            time.time() - reg.created_at <= TTL_SECONDS:
                        continue
                    if reg.state == IN_FLIGHT:
                        continue              # incierto: no prescribe
                    if reg.state == FAILED and reg.safe_to_retry is not True:
                        continue              # inseguro o sin veredicto
                    f.unlink(missing_ok=True)
                    n += 1
            except PowerBIMCPError:
                continue                      # corrupto/ilegible: evidencia
            except OSError:                                 # pragma: no cover
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


@dataclass
class Intento:
    """Lo que sale de abrir una peticion.

    `attempt_id` no es `None` exactamente cuando hay que ejecutar, y hay que
    devolverlo al cerrar: es lo que demuestra que quien escribe el resultado
    sigue siendo el dueno de la reserva.
    """

    request_id: str
    attempt_id: Optional[str] = None
    replay: Optional[Dict[str, Any]] = None

    @property
    def hay_que_ejecutar(self) -> bool:
        return self.attempt_id is not None


def comenzar_intento(store: Store, request_id: str, operation: str,
                     payload: Any) -> Intento:
    """Abre (o resuelve) una peticion, devolviendo la identidad del intento.

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
            return Intento(request_id=request_id, attempt_id=valor)
        if veredicto == _REPRODUCIR:
            return Intento(request_id=request_id, replay=valor)
        if time.time() >= limite:
            raise RequestInProgressError(
                f"El request_id '{request_id}' se esta ejecutando ahora mismo. "
                "Espera a que termine antes de reintentar.",
                details={"request_id": request_id, "operation": valor.operation,
                         "age_seconds": round(valor.edad, 1)})
        time.sleep(_WAIT_STEP)


def comenzar(store: Store, request_id: str, operation: str,
             payload: Any) -> Optional[Dict[str, Any]]:
    """Igual que `comenzar_intento`, con la forma de siempre.

    Devuelve el resultado guardado si es un reintento ya resuelto, o `None` si
    hay que ejecutar la mutacion. Quien pueda, use `comenzar_intento`: sin el
    `attempt_id` no se puede comprobar al cerrar quien es el dueno.
    """
    return comenzar_intento(store, request_id, operation, payload).replay


def _decidir(store: Store, request_id: str, operation: str, fp: str):
    """Que hacer con esta peticion. Se llama SIEMPRE con el cerrojo tomado."""
    reg = store.leer(request_id)

    if reg is None:
        nuevo = _nuevo_intento(request_id, operation, fp)
        if store.reservar(nuevo):
            return _EJECUTAR, nuevo.attempt_id
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
        raise _desconocido(reg)

    if reg.state == COMPENSATED or (reg.state == FAILED
                                    and reg.safe_to_retry is True):
        # Reintentar es seguro DECLARADO: compensated deshizo el cambio
        # entero, y el failed lo afirmo quien lo cerro. Solo con esa
        # afirmacion se reabre. Antes se reabria CUALQUIER failed sin mirar
        # el veredicto, y un `bulk_partially_applied` cerrado con
        # safe_to_retry=False se ejecutaba dos veces con el mismo request_id:
        # guardar el veredicto y no consultarlo es no tenerlo.
        nuevo = _nuevo_intento(request_id, operation, fp)
        store.escribir(nuevo)
        return _EJECUTAR, nuevo.attempt_id

    if reg.state == FAILED and reg.safe_to_retry is False \
            and isinstance(reg.error, dict):
        # Fallo declarado NO seguro: se reproduce lo que paso, sin ejecutar.
        # La raiz lleva el veredicto aunque el error guardado no lo trajera.
        salida = dict(reg.error)
        salida["idempotent_replay"] = True
        salida["safe_to_retry"] = False
        return _REPRODUCIR, salida

    # failed sin veredicto (registro de una version anterior o cierre
    # incompleto), inseguro sin error que reproducir, o un estado que este
    # codigo no conoce: no hay base para autorizar nada.
    raise _fallo_sin_permiso(reg)


def _fallo_sin_permiso(reg: Registro) -> ResultadoDesconocidoError:
    """Un fallo sin veredicto de reintento no autoriza nada.

    `safe_to_retry=None` lo dejan los registros de versiones anteriores y los
    cierres que no llegaron a declararlo. Tratarlo como "si se puede" seria
    decidir por quien no dijo nada, que es como volvio la carrera la ultima
    vez.
    """
    motivo = ("sin veredicto de reintento" if reg.safe_to_retry is None
              else "declarado NO seguro de reintentar y sin resultado "
                   "guardado que reproducir")
    return ResultadoDesconocidoError(
        f"El request_id '{reg.request_id}' termino en '{reg.state}' {motivo}. "
        "No se autoriza otra ejecucion con ese mismo request_id.",
        details={"request_id": reg.request_id, "operation": reg.operation,
                 "state": reg.state,
                 "stored_safe_to_retry": reg.safe_to_retry,
                 "safe_to_retry": False,
                 "recovery": (
                     "Comprueba en el proyecto si el cambio se aplico. Si NO "
                     "se aplico y quieres reintentar, usa un request_id nuevo "
                     "o borra el registro a mano. Si se aplico, no "
                     "reintentes.")})


def _desconocido(reg: Registro) -> ResultadoDesconocidoError:
    """El intento anterior lleva demasiado y no se sabe como acabo.

    Antes esto se "reclamaba": se daba el registro por abandonado y se
    autorizaba otra ejecucion. Es la carrera que quedaba. Una operacion
    legitima puede pasar del umbral —abrir y refrescar un modelo grande lo
    hace— y seguir viva; y aunque el dueno hubiera muerto, nadie sabe si la
    escritura llego a aplicarse. Autorizar ahi duplica la mutacion.

    Se mira si el proceso dueno sigue existiendo, no para decidir sino para
    que quien lea el error sepa cual de los dos casos tiene delante.
    """
    vivo = reg.dueno_vivo
    if vivo is True:
        diagnostico = (f"El proceso que la abrio (pid {reg.owner_pid}) SIGUE "
                       "VIVO: lo mas probable es que la operacion aun este "
                       "trabajando. Espera a que acabe.")
    elif vivo is False:
        diagnostico = (f"El proceso que la abrio (pid {reg.owner_pid}) YA NO "
                       "EXISTE, pero eso no dice si el cambio llego a "
                       "aplicarse: pudo morir antes, durante o despues.")
    else:
        diagnostico = ("No se ha podido comprobar si el proceso que la abrio "
                       "sigue vivo.")
    return ResultadoDesconocidoError(
        f"El request_id '{reg.request_id}' quedo en vuelo hace "
        f"{reg.edad:.0f}s y no se sabe como acabo. {diagnostico} No se "
        "autoriza otra ejecucion: seria la segunda mutacion con el mismo "
        "request_id, que es justo lo que esto existe para impedir.",
        details={
            "request_id": reg.request_id, "operation": reg.operation,
            "age_seconds": round(reg.edad, 1), "owner_pid": reg.owner_pid,
            "owner_alive": vivo, "safe_to_retry": False,
            "recovery": (
                "1) Comprueba en el proyecto si el cambio se aplico. "
                "2) Si NO se aplico, descarta el intento con "
                "idempotency.descartar_en_vuelo(...) o borra el registro a "
                "mano, y reintenta. 3) Si SI se aplico, no reintentes: usa un "
                "request_id nuevo para el siguiente cambio."),
        })


def _cerrar(store: Store, reg: Registro, attempt_id: Optional[str]) -> None:
    """Escribe el estado final SOLO si el intento sigue siendo el dueno.

    El compare-and-set va bajo la misma exclusion que la reserva: sin eso, un
    intento antiguo que despierta tarde pisa el registro de uno nuevo y borra
    su rastro.

    Compatibilidad: si el registro guardado no tiene `attempt_id` —lo escribio
    una version anterior— o si quien cierra no lo trae, no hay con que
    comparar y se escribe. La comprobacion aplica cuando los dos existen, que
    es el caso de todo lo que se ejecuta a partir de ahora.
    """
    with _exclusion(store, reg.request_id):
        actual = store.leer(reg.request_id)
        if (actual is not None and attempt_id is not None
                and actual.attempt_id is not None
                and actual.attempt_id != attempt_id):
            raise IntentoCaducadoError(
                f"El intento {attempt_id} de '{reg.request_id}' ya no es el "
                f"dueno de la reserva (ahora lo es {actual.attempt_id}, en "
                f"estado '{actual.state}'). No se escribe su resultado: "
                "describe una ejecucion que ya nadie esta esperando, y pisarlo "
                "borraria el rastro del intento en curso.",
                details={"request_id": reg.request_id,
                         "attempt_id": attempt_id,
                         "current_attempt_id": actual.attempt_id,
                         "current_state": actual.state})
        reg.attempt_id = attempt_id or (actual.attempt_id if actual else None)
        store.escribir(reg)


def terminar_ok(store: Store, request_id: str, operation: str, payload: Any,
                resultado: Dict[str, Any], *,
                attempt_id: Optional[str] = None) -> None:
    _cerrar(store, Registro(
        request_id=request_id, operation=operation,
        payload_fingerprint=plan_contract.fingerprint_de(payload),
        state=SUCCEEDED, result=resultado, safe_to_retry=None), attempt_id)


def terminar_error(store: Store, request_id: str, operation: str, payload: Any,
                   error: Dict[str, Any], *, safe_to_retry: bool,
                   compensado: bool = False,
                   attempt_id: Optional[str] = None) -> None:
    """Cierra una peticion fallida diciendo si reintentar es seguro.

    `compensado=True` significa que el cambio se deshizo por completo: el estado
    es el de antes y reintentar no duplica nada.
    """
    _cerrar(store, Registro(
        request_id=request_id, operation=operation,
        payload_fingerprint=plan_contract.fingerprint_de(payload),
        state=COMPENSATED if compensado else FAILED,
        error=error, safe_to_retry=True if compensado else safe_to_retry),
        attempt_id)


def descartar_en_vuelo(store: Store, request_id: str, *,
                       motivo: str = "descartado a mano") -> Dict[str, Any]:
    """Recuperacion EXPLICITA de una peticion con resultado desconocido.

    El servidor no hace esto solo, y ese es el punto: quien lo llama esta
    afirmando que ha mirado el proyecto y que el cambio NO se aplico.

    Esa afirmacion es exactamente lo que hace seguro reintentar, asi que el
    registro queda como `failed` con `safe_to_retry=True`. Guardar `False` y
    reabrir igualmente —como se hacia— era incoherente por los dos lados a la
    vez: el registro decia una cosa y la autorizacion hacia la contraria.
    Ahora que el veredicto GOBIERNA la autorizacion, tiene que decir la
    verdad de lo que se acaba de comprobar.

    No es una compensacion: `compensated` significa que el servidor deshizo
    el cambio y puede demostrarlo. Aqui no se deshizo nada; alguien miro y
    dijo que nunca llego a haberlo. Por eso el estado es `failed` con el
    motivo dentro, y no `compensated`.
    """
    with _exclusion(store, request_id):
        actual = store.leer(request_id)
        if actual is None:
            return {"request_id": request_id, "discarded": False,
                    "reason": "No hay ningun registro para ese request_id."}
        if actual.state != IN_FLIGHT:
            return {"request_id": request_id, "discarded": False,
                    "reason": f"No esta en vuelo, esta en '{actual.state}'."}
        actual.state = FAILED
        actual.safe_to_retry = True
        actual.error = {"error": "in_flight_discarded", "message": motivo,
                        "reviewed_by_human": True}
        # Identidad nueva: el intento viejo, si despierta, ya no es el dueno y
        # no podra pisar lo que escriba el siguiente.
        actual.attempt_id = uuid.uuid4().hex
        store.escribir(actual)
    log.warning("Peticion %s descartada a mano: %s", request_id, motivo)
    return {"request_id": request_id, "discarded": True,
            "previous_state": IN_FLIGHT, "state": FAILED,
            "note": "El siguiente intento con ese request_id se autorizara."}


def estado(store: Store, request_id: str) -> Optional[Dict[str, Any]]:
    reg = store.leer(request_id)
    return reg.to_dict() if reg else None
