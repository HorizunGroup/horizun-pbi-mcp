"""El veredicto de reintento manda, y la duda no prescribe.

Dos bloqueos encontrados revisando el PR anterior, con la misma forma que
todo lo demas de esta pieza: una via lateral por la que un `request_id` con
resultado incierto volvia a autorizarse.

1. **`safe_to_retry=False` se ignoraba.** `_decidir()` reabria cualquier
   `failed` sin consultar el veredicto: un `bulk_partially_applied` cerrado
   con `safe_to_retry=False` se ejecutaba OTRA VEZ con el mismo request_id.
   Guardar el veredicto y no consultarlo es no tenerlo.

2. **El TTL borraba estados inciertos.** `leer()` ocultaba cualquier registro
   de mas de 24h y `purgar()` lo eliminaba, incluidos `in_flight` y fallos
   inseguros. "Caducado" se convertia en "no existe", y el siguiente
   `comenzar()` autorizaba: el reclaim inseguro, otra vez, por la puerta del
   calendario.

El contrato que fijan estas pruebas:

- `succeeded`  -> reproducir;
- `compensated` -> intento nuevo (el cambio se deshizo entero);
- `failed` + `safe_to_retry=True`  -> intento nuevo;
- `failed` + `safe_to_retry=False` -> reproducir el error guardado, SIN
  ejecutar, con `safe_to_retry=False` en la raiz;
- `failed` sin veredicto (registros de versiones anteriores) -> cerrado,
  `request_outcome_unknown`;
- `in_flight` no caduca; los fallos no seguros tampoco; solo lo terminal
  inequivocamente seguro se purga, y decidiendolo bajo el cerrojo del
  request_id, no sobre una lectura vieja.

Todas las pruebas provocan las transiciones reales y cuentan ejecuciones.
Ninguna hace grep del codigo fuente.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time

import pytest

from horizun_pbi_mcp.services import idempotency, plan_contract
from horizun_pbi_mcp.services.idempotency import Store

PAYLOAD = {"operation": "pbi_apply_page_spec", "arguments": {"spec": {"n": 1}}}

#: Margen para la cita entre hilos: agotarlo significa coordinacion rota,
#: nunca maquina lenta.
TIMEOUT = 30.0


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "_idem")


def envejecer(store, request_id, *, updated=None, created=None):
    """Retrasa los relojes del registro en el archivo, sin pasar por el Store."""
    f = store.root / f"{request_id}.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    if updated is not None:
        datos["updated_at"] = time.time() - updated
    if created is not None:
        datos["created_at"] = time.time() - created
    f.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


def registro_a_mano(store, request_id, **campos):
    """Un registro escrito tal cual, como lo dejaria una version anterior."""
    store.root.mkdir(parents=True, exist_ok=True)
    base = {"request_id": request_id, "operation": "op",
            "payload_fingerprint": plan_contract.fingerprint_de(PAYLOAD),
            "state": idempotency.FAILED, "created_at": time.time(),
            "updated_at": time.time(), "result": None, "error": None,
            "safe_to_retry": None}
    base.update(campos)
    (store.root / f"{request_id}.json").write_text(
        json.dumps(base, ensure_ascii=False), encoding="utf-8")


# ================================================== bloqueo 1: el veredicto ==
def test_fallo_inseguro_no_vuelve_a_ejecutar(store):
    """El escenario exacto: bulk parcialmente aplicado, cerrado como NO
    seguro, y el mismo request_id vuelve a llegar."""
    ejecuciones = 0
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert a.hay_que_ejecutar
    ejecuciones += 1
    idempotency.terminar_error(
        store, "r1", "op", PAYLOAD,
        {"ok": False, "error": "bulk_partially_applied",
         "message": "3 de 7 archivos escritos"},
        safe_to_retry=False, attempt_id=a.attempt_id)

    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    if b.hay_que_ejecutar:                                # pragma: no cover
        ejecuciones += 1
    assert ejecuciones == 1, (
        "un fallo declarado NO seguro no puede autorizar otra mutacion: "
        "medio bulk aplicado dos veces es el desastre que esto impide")

    assert b.replay["idempotent_replay"] is True
    assert b.replay["safe_to_retry"] is False
    assert b.replay["error"] == "bulk_partially_applied", (
        "se reproduce EL error que paso, no uno generico")
    # Y el registro no se toco: sigue contando el fallo original.
    est = idempotency.estado(store, "r1")
    assert est["state"] == idempotency.FAILED
    assert est["safe_to_retry"] is False


def test_fallo_sin_veredicto_falla_cerrado(store):
    """`safe_to_retry=None` no es permiso: es un registro que no dijo nada.

    Lo dejan las versiones anteriores y los cierres incompletos. Decidir
    "si se puede" por quien no dijo nada seria inventarse el veredicto.
    """
    registro_a_mano(store, "legacy", state=idempotency.FAILED,
                    safe_to_retry=None,
                    error={"ok": False, "error": "algo_fallo"})

    with pytest.raises(idempotency.ResultadoDesconocidoError) as exc:
        idempotency.comenzar_intento(store, "legacy", "op", PAYLOAD)
    assert exc.value.code == "request_outcome_unknown"
    assert exc.value.details["safe_to_retry"] is False
    assert "recovery" in exc.value.details


def test_fallo_inseguro_sin_error_guardado_tambien_falla_cerrado(store):
    """No hay nada que reproducir: cerrado, no autorizado."""
    registro_a_mano(store, "r1", state=idempotency.FAILED,
                    safe_to_retry=False, error=None)

    with pytest.raises(idempotency.ResultadoDesconocidoError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)


def test_fallo_seguro_si_permite_intento_nuevo(store):
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "validation_error"},
                               safe_to_retry=True, attempt_id=a.attempt_id)

    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert b.hay_que_ejecutar, "True es un veredicto: quien cerro lo afirmo"
    assert b.attempt_id != a.attempt_id


def test_compensado_si_permite_intento_nuevo(store):
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "bulk_apply_failed"},
                               safe_to_retry=False, compensado=True,
                               attempt_id=a.attempt_id)

    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert b.hay_que_ejecutar, (
        "compensated significa que el cambio se deshizo ENTERO: es el unico "
        "fallo cuyo reintento es seguro por construccion")


def test_descartar_autoriza_exactamente_un_intento_nuevo(store, monkeypatch):
    """La recuperacion explicita tiene que ser coherente con la autorizacion.

    Quien descarta afirma que miro el proyecto y el cambio NO se aplico. Esa
    afirmacion es exactamente lo que hace seguro reintentar: guardar
    `safe_to_retry=False` y luego reabrir ignorandolo seria incoherente por
    los dos lados a la vez.
    """
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", updated=idempotency.STALE_IN_FLIGHT_SECONDS + 60)
    with pytest.raises(idempotency.ResultadoDesconocidoError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)

    r = idempotency.descartar_en_vuelo(
        store, "r1", motivo="comprobado: el cambio no esta en el proyecto")
    assert r["discarded"] is True

    est = idempotency.estado(store, "r1")
    assert est["safe_to_retry"] is True, (
        "el descarte ES la afirmacion humana de que reintentar es seguro; "
        "guardar False y reabrir igualmente seria mentir dos veces")

    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert b.hay_que_ejecutar
    assert b.attempt_id != a.attempt_id, "identidad nueva, no la del muerto"

    # Exactamente UNO: el siguiente se encuentra el vuelo del nuevo intento.
    monkeypatch.setattr(idempotency, "WAIT_SECONDS", 0.15)
    with pytest.raises(idempotency.RequestInProgressError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)


# ============================================== bloqueo 2: el TTL y el purgado ==
def test_un_in_flight_mas_viejo_que_el_ttl_ni_se_oculta_ni_se_purga(store):
    """Ocultarlo por edad convertia "caducado" en "no existe": reclaim."""
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", updated=idempotency.TTL_SECONDS + 3600,
              created=idempotency.TTL_SECONDS + 3600)

    est = idempotency.estado(store, "r1")
    assert est is not None, "un in_flight viejo NO es un in_flight inexistente"
    assert est["state"] == idempotency.IN_FLIGHT

    assert store.purgar() == 0, "la duda no prescribe"
    assert (store.root / "r1.json").exists()

    # Y la peticion sigue cerrada, no reabierta por vieja.
    with pytest.raises(idempotency.ResultadoDesconocidoError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)


def test_un_fallo_inseguro_mas_viejo_que_el_ttl_no_se_purga(store):
    """Borrar la evidencia de un fallo inseguro es autorizar su repeticion."""
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(
        store, "r1", "op", PAYLOAD,
        {"ok": False, "error": "bulk_partially_applied"},
        safe_to_retry=False, attempt_id=a.attempt_id)
    envejecer(store, "r1", created=idempotency.TTL_SECONDS + 3600)

    assert store.purgar() == 0
    assert (store.root / "r1.json").exists()

    # El registro sigue mandando: reproduce, no ejecuta.
    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert not b.hay_que_ejecutar
    assert b.replay["error"] == "bulk_partially_applied"


def test_un_fallo_sin_veredicto_viejo_tampoco_se_purga(store):
    registro_a_mano(store, "legacy", state=idempotency.FAILED,
                    safe_to_retry=None,
                    created_at=time.time() - idempotency.TTL_SECONDS - 3600)
    assert store.purgar() == 0
    assert (store.root / "legacy.json").exists()


def test_lo_terminal_inequivocamente_seguro_si_se_purga(store):
    """El guard no puede convertir el purgado en un no-op: succeeded,
    compensated y failed-seguro caducados si se van."""
    for rid, extra in (
            ("ok", {"state": idempotency.SUCCEEDED, "result": {"ok": True}}),
            ("comp", {"state": idempotency.COMPENSATED, "safe_to_retry": True}),
            ("fs", {"state": idempotency.FAILED, "safe_to_retry": True})):
        registro_a_mano(store, rid,
                        created_at=time.time() - idempotency.TTL_SECONDS - 3600,
                        **extra)

    assert store.purgar() == 3
    assert not list(store.root.glob("*.json"))
    # Y tras el purgado, ese request_id vuelve a estar disponible.
    assert idempotency.comenzar_intento(store, "ok", "op",
                                        PAYLOAD).hay_que_ejecutar


def test_el_purgado_no_borra_un_intento_reabierto(store, monkeypatch):
    """La decision de borrar se toma bajo el cerrojo, no sobre lo ya leido.

    Se para al purgado JUSTO antes de tomar la exclusion del request_id, se
    reabre la peticion desde el hilo principal, y se le deja seguir: tiene
    que releer y no borrar el intento vivo.
    """
    it = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "x"},
                               safe_to_retry=True, attempt_id=it.attempt_id)
    envejecer(store, "r1", created=idempotency.TTL_SECONDS + 3600)

    real = idempotency._exclusion
    purgado_en_la_puerta = threading.Event()
    puede_seguir = threading.Event()
    hilo_purga = []

    @contextlib.contextmanager
    def exclusion_con_cita(s, rid):
        if threading.current_thread() in hilo_purga:
            purgado_en_la_puerta.set()
            assert puede_seguir.wait(TIMEOUT)
        with real(s, rid):
            yield

    monkeypatch.setattr(idempotency, "_exclusion", exclusion_con_cita)
    borrados = []
    t = threading.Thread(target=lambda: borrados.append(store.purgar()))
    hilo_purga.append(t)
    t.start()
    try:
        assert purgado_en_la_puerta.wait(TIMEOUT), (
            "el purgado no paso por la exclusion del request_id: decide "
            "sobre una lectura que nadie protege")
        # Con el purgado parado en la puerta, la peticion se reabre.
        b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
        assert b.hay_que_ejecutar
    finally:
        puede_seguir.set()
        t.join(TIMEOUT)

    assert borrados == [0], "borro un intento que acababa de reabrirse"
    est = idempotency.estado(store, "r1")
    assert est is not None and est["state"] == idempotency.IN_FLIGHT
    assert est["attempt_id"] == b.attempt_id
    # Y ese intento sigue siendo dueno: puede cerrar.
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True},
                            attempt_id=b.attempt_id)


# =========================================== el veredicto llega al sobre raiz ==
def test_el_replay_inseguro_lleva_el_veredicto_en_la_raiz(store):
    """Aunque el error guardado no trajera la clave, la raiz la lleva."""
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "media_escritura"},
                               safe_to_retry=False, attempt_id=a.attempt_id)

    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert b.replay["safe_to_retry"] is False, (
        "sin la clave en la raiz, un cliente decide reintentar a ciegas")


def test_el_resultado_incierto_llega_al_sobre_de_la_tool(store, monkeypatch,
                                                         tmp_path):
    """Por el camino real de `guard_mutation`, no por el servicio a pelo."""
    from horizun_pbi_mcp.tools import _common

    monkeypatch.setattr(idempotency, "store_por_defecto", lambda: store)
    ejecutadas = []

    def pbi_mutacion(request_id="rid-x"):
        return _common.guard_mutation(
            lambda: ejecutadas.append(True) or {"applied": 1})

    primera = pbi_mutacion()
    assert primera["ok"] is True and ejecutadas == [True]

    # El registro queda en vuelo y viejo: como si el proceso anterior
    # hubiera desaparecido a mitad.
    f = store.root / "rid-x.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["state"] = idempotency.IN_FLIGHT
    datos["updated_at"] = time.time() - idempotency.STALE_IN_FLIGHT_SECONDS - 60
    f.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")

    salida = pbi_mutacion()
    assert ejecutadas == [True], "la mutacion no puede repetirse"
    assert salida["ok"] is False
    assert salida["error"] == "request_outcome_unknown"
    assert salida["safe_to_retry"] is False, (
        "en la RAIZ del sobre: details no lo lee ningun cliente automatico")
