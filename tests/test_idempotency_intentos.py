"""Reclamar un `in_flight` por viejo era una carrera con la mutacion viva.

El PR anterior cerro la carrera del alta —dos llamadas que no ven registro—
pero dejo abierta la grave:

1. `comenzar(..., request_id="r1")` autoriza y arranca la mutacion A;
2. A tarda mas de `STALE_IN_FLIGHT_SECONDS` **y sigue viva**;
3. una segunda llamada ve el registro "antiguo", lo reclama, y `comenzar()`
   vuelve a autorizar: arranca la mutacion B;
4. A termina y su `terminar_ok()` pisa el registro de B.

Dos mutaciones con el mismo `request_id`. **Antiguo no prueba muerto**: un
`pbi_open_and_refresh` sobre un modelo grande pasa de cinco minutos sin
despeinarse. Y aunque el proceso hubiera muerto, nadie sabe si la escritura
llego a aplicarse, asi que autorizar tampoco seria correcto.

Dos barreras, y las dos se prueban aqui:

- **no se autoriza** por antiguedad: `request_outcome_unknown`, cerrado, con
  `safe_to_retry=False` y una salida explicita;
- **el cierre es condicional**: un intento solo escribe su resultado si sigue
  siendo el dueno de la reserva.

Nada de esto depende del planificador ni de dormir a ver si sale: el tiempo se
falsifica envejeciendo el registro, que es la unica variable que importaba.
"""
from __future__ import annotations

import json
import time

import pytest

from horizun_pbi_mcp.services import idempotency
from horizun_pbi_mcp.services.idempotency import Store

PAYLOAD = {"operation": "pbi_open_and_refresh", "arguments": {"pbip": "X.pbip"}}


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "_idem")


def envejecer(store, request_id, segundos):
    """Retrasa `updated_at` en el archivo: `escribir` lo refrescaria."""
    f = store.root / f"{request_id}.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["updated_at"] = time.time() - segundos
    f.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


def matar_al_dueno(store, request_id):
    """Deja el registro apuntando a un proceso que no existe."""
    f = store.root / f"{request_id}.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["owner_pid"] = 2 ** 22          # fuera del rango de pid de Windows
    datos["owner_started"] = None
    f.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")


# ================================================= la carrera, entera =========
def test_nunca_se_autorizan_dos_mutaciones(store):
    """El escenario reportado, paso por paso, con A todavia viva.

    A se autoriza; el registro envejece por encima del umbral; B lo intenta;
    A termina despues. En ningun momento puede haber dos autorizaciones.
    """
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert a.hay_que_ejecutar, "el primero si se autoriza"

    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 60)

    with pytest.raises(idempotency.ResultadoDesconocidoError) as exc:
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)

    assert exc.value.details["safe_to_retry"] is False
    assert exc.value.details["owner_alive"] is True, (
        "A sigue viva: el diagnostico tiene que decirlo")

    # Y A, que era la unica autorizada, cierra sin problema.
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "n": 1},
                            attempt_id=a.attempt_id)
    assert idempotency.estado(store, "r1")["state"] == idempotency.SUCCEEDED


def test_una_operacion_legitima_de_mas_de_300s_no_se_pisa(store):
    """`pbi_open_and_refresh` pasa del umbral y esta perfectamente viva.

    Es el caso que convertia el umbral en un generador de duplicados: la
    operacion mas lenta del servidor es justo la que mas facil lo cruza.
    """
    a = idempotency.comenzar_intento(store, "r1", "pbi_open_and_refresh", PAYLOAD)
    envejecer(store, "r1", 600)                    # diez minutos refrescando

    with pytest.raises(idempotency.ResultadoDesconocidoError):
        idempotency.comenzar_intento(store, "r1", "pbi_open_and_refresh", PAYLOAD)

    # La operacion larga termina y su resultado se guarda tal cual.
    idempotency.terminar_ok(store, "r1", "pbi_open_and_refresh", PAYLOAD,
                            {"ok": True, "rows": 900_000},
                            attempt_id=a.attempt_id)
    guardado = idempotency.estado(store, "r1")
    assert guardado["state"] == idempotency.SUCCEEDED
    assert guardado["result"]["rows"] == 900_000

    # Y el reintento posterior reproduce, no vuelve a mutar.
    repetido = idempotency.comenzar_intento(store, "r1", "pbi_open_and_refresh",
                                            PAYLOAD)
    assert repetido.replay["idempotent_replay"] is True
    assert not repetido.hay_que_ejecutar


def test_el_dueno_muerto_tampoco_autoriza_solo(store):
    """Que A haya muerto no dice si el cambio se aplico.

    Se distingue del caso anterior en el DIAGNOSTICO, no en el permiso: los
    dos fallan cerrado.
    """
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 60)
    matar_al_dueno(store, "r1")

    with pytest.raises(idempotency.ResultadoDesconocidoError) as exc:
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert exc.value.details["owner_alive"] is False
    assert exc.value.details["safe_to_retry"] is False
    assert "YA NO EXISTE" in str(exc.value)
    assert "no dice si el cambio llego a aplicarse" in str(exc.value), (
        "el diagnostico no puede leerse como un permiso")


# ============================================ el cierre es condicional ========
def test_un_intento_caducado_no_pisa_el_registro_del_nuevo(store):
    """El paso 4 del escenario: A termina tarde, despues de que B empezara.

    Aunque alguien reabra la peticion —a mano, con la recuperacion
    explicita—, el resultado de A ya no puede escribirse encima.
    """
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 60)

    idempotency.descartar_en_vuelo(store, "r1", motivo="comprobado a mano")
    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert b.hay_que_ejecutar and b.attempt_id != a.attempt_id

    with pytest.raises(idempotency.IntentoCaducadoError) as exc:
        idempotency.terminar_ok(store, "r1", "op", PAYLOAD,
                                {"ok": True, "de": "A"}, attempt_id=a.attempt_id)
    assert exc.value.code == "idempotency_attempt_superseded"

    guardado = idempotency.estado(store, "r1")
    assert guardado["state"] == idempotency.IN_FLIGHT, (
        "el registro sigue siendo el de B, que es quien esta ejecutando")
    assert guardado["attempt_id"] == b.attempt_id

    # Y B si puede cerrar.
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "de": "B"},
                            attempt_id=b.attempt_id)
    assert idempotency.estado(store, "r1")["result"]["de"] == "B"


def test_un_intento_caducado_tampoco_escribe_el_fallo(store):
    a = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 60)
    idempotency.descartar_en_vuelo(store, "r1")
    b = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)

    with pytest.raises(idempotency.IntentoCaducadoError):
        idempotency.terminar_error(store, "r1", "op", PAYLOAD, {"ok": False},
                                   safe_to_retry=True, attempt_id=a.attempt_id)
    assert idempotency.estado(store, "r1")["attempt_id"] == b.attempt_id


def test_el_attempt_id_no_se_reutiliza_nunca(store):
    vistos = set()
    for _ in range(5):
        it = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
        assert it.attempt_id not in vistos, "un attempt_id no puede repetirse"
        vistos.add(it.attempt_id)
        idempotency.terminar_error(store, "r1", "op", PAYLOAD, {"ok": False},
                                   safe_to_retry=True, attempt_id=it.attempt_id)


# ============================================ recuperacion explicita ==========
def test_descartar_es_lo_unico_que_reabre(store):
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 60)

    r = idempotency.descartar_en_vuelo(store, "r1", motivo="el cambio no estaba")
    assert r["discarded"] is True and r["state"] == idempotency.FAILED
    est = idempotency.estado(store, "r1")
    assert est["safe_to_retry"] is False, (
        "se reabre, pero sin mentir sobre si era seguro")

    assert idempotency.comenzar_intento(store, "r1", "op", PAYLOAD).hay_que_ejecutar


def test_descartar_no_toca_lo_que_ya_termino(store):
    """No es un borrador de registros: solo saca de `in_flight`."""
    it = idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True},
                            attempt_id=it.attempt_id)

    r = idempotency.descartar_en_vuelo(store, "r1")
    assert r["discarded"] is False
    assert idempotency.estado(store, "r1")["state"] == idempotency.SUCCEEDED


def test_descartar_lo_que_no_existe_no_inventa_nada(store):
    assert idempotency.descartar_en_vuelo(store, "nada")["discarded"] is False


# ============================================ compatibilidad de registros =====
def test_un_registro_de_una_version_anterior_se_sigue_leyendo(store):
    """Sin `attempt_id` ni dueno: no puede romper una instalacion ya en uso."""
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "viejo.json").write_text(json.dumps({
        "request_id": "viejo", "operation": "op",
        "payload_fingerprint": idempotency.plan_contract.fingerprint_de(PAYLOAD),
        "state": idempotency.SUCCEEDED, "created_at": time.time(),
        "updated_at": time.time(), "result": {"ok": True, "v": 1},
    }), encoding="utf-8")

    it = idempotency.comenzar_intento(store, "viejo", "op", PAYLOAD)
    assert it.replay == {"ok": True, "v": 1, "idempotent_replay": True}


def test_cerrar_sin_attempt_id_sigue_funcionando(store):
    """La firma vieja se conserva: hay llamadas que no pasan el intento."""
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True})
    assert idempotency.estado(store, "r1")["state"] == idempotency.SUCCEEDED


# ============================================ el registro parcial =============
def test_un_corte_al_reservar_no_deja_un_registro_a_medias(store, monkeypatch):
    """`reservar` escribia directo sobre el descriptor del destino.

    Un corte a mitad dejaba JSON truncado en el archivo bueno. Ahora son dos
    pasos: el `O_EXCL` reclama el hueco y el contenido va por `durable_write`
    (tmp + fsync + replace), asi que el destino pasa de vacio a completo sin
    estados intermedios. Se provoca el corte justo en el commit.
    """
    import os as _os

    def replace_que_revienta(*_a, **_k):
        raise OSError("proceso interrumpido en el commit")

    monkeypatch.setattr(_os, "replace", replace_que_revienta)
    with pytest.raises(OSError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    monkeypatch.undo()

    f = store.root / "r1.json"
    assert f.exists(), "el hueco quedo reclamado, que es lo que impide dos altas"
    assert f.read_bytes() == b"", (
        "el destino no puede quedar con JSON a medias: o vacio o completo")
    assert not list(store.root.glob("*.tmp")), "no puede quedar un temporal"

    # Y ese registro incompleto BLOQUEA, que es lo correcto: si se murio
    # reservando, nadie sabe si la mutacion llego a empezar.
    with pytest.raises(idempotency.RegistroCorruptoError):
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)


def test_la_reserva_deja_el_registro_completo(store):
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    datos = json.loads((store.root / "r1.json").read_text(encoding="utf-8"))
    assert datos["attempt_id"] and datos["state"] == idempotency.IN_FLIGHT
    assert datos["owner_pid"] == __import__("os").getpid()
    assert not list(store.root.glob("*.tmp")), "no puede quedar un temporal"


# ============================================ ilegible != inexistente =========
def test_un_registro_que_no_se_puede_leer_no_es_uno_que_no_existe(store,
                                                                  monkeypatch):
    """Un fallo de disco no puede convertirse en permiso para mutar."""
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)

    real = type(store.root).read_text

    def read_text_que_falla(self, *a, **k):
        if self.name == "r1.json":
            raise OSError("disco ocupado")
        return real(self, *a, **k)

    monkeypatch.setattr(type(store.root), "read_text", read_text_que_falla)
    with pytest.raises(idempotency.RegistroIlegibleError) as exc:
        idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    assert exc.value.code == "idempotency_record_unreadable"


def test_tampoco_se_pisa_a_ciegas_lo_que_no_se_pudo_leer(store, monkeypatch):
    idempotency.comenzar_intento(store, "r1", "op", PAYLOAD)
    real = type(store.root).read_text

    def read_text_que_falla(self, *a, **k):
        if self.name == "r1.json":
            raise OSError("disco ocupado")
        return real(self, *a, **k)

    monkeypatch.setattr(type(store.root), "read_text", read_text_que_falla)
    with pytest.raises(idempotency.RegistroIlegibleError):
        store.escribir(idempotency.Registro(
            request_id="r1", operation="op", payload_fingerprint="fp",
            state=idempotency.SUCCEEDED))
