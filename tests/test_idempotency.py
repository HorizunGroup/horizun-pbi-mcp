"""Fase B — idempotencia real de las mutaciones.

Antes de esto la garantia estaba documentada pero no implementada: nadie
llamaba a `comprobar_request`/`guardar_resultado`, y `guard()` inventaba un
`request_id` nuevo en cada llamada, asi que dos peticiones identicas del cliente
mutaban dos veces. `test_dos_llamadas_identicas_mutan_una_sola_vez` es la
regresion de ese defecto.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator
from services import idempotency
from services.idempotency import Store
from tests.fixtures import synthetic


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "_idem")


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


PAYLOAD = {"operation": "pbi_rename_page", "arguments": {"page": "A", "new_name": "B"}}


# ============================================================ el protocolo ====
def test_primera_peticion_deja_en_vuelo_y_manda_ejecutar(store):
    assert idempotency.comenzar(store, "r1", "op", PAYLOAD) is None
    assert idempotency.estado(store, "r1")["state"] == idempotency.IN_FLIGHT


def test_reintento_tras_exito_devuelve_lo_guardado(store):
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "applied": 3})

    repetido = idempotency.comenzar(store, "r1", "op", PAYLOAD)
    assert repetido == {"ok": True, "applied": 3, "idempotent_replay": True}
    assert idempotency.estado(store, "r1")["state"] == idempotency.SUCCEEDED


def test_mismo_id_payload_distinto_es_conflicto(store):
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True})

    otro = {"operation": "pbi_rename_page", "arguments": {"page": "A", "new_name": "Z"}}
    with pytest.raises(idempotency.IdempotencyConflictError) as exc:
        idempotency.comenzar(store, "r1", "op", otro)
    assert exc.value.code == "idempotency_conflict"


def test_en_vuelo_vivo_devuelve_request_in_progress(store, monkeypatch):
    monkeypatch.setattr(idempotency, "WAIT_SECONDS", 0.1)
    idempotency.comenzar(store, "r1", "op", PAYLOAD)

    with pytest.raises(idempotency.RequestInProgressError) as exc:
        idempotency.comenzar(store, "r1", "op", PAYLOAD)
    assert exc.value.code == "request_in_progress"


def test_en_vuelo_que_termina_durante_la_espera_devuelve_el_resultado(store, monkeypatch):
    """La espera es acotada, pero si el otro acaba a tiempo se aprovecha."""
    monkeypatch.setattr(idempotency, "WAIT_SECONDS", 1.0)
    idempotency.comenzar(store, "r1", "op", PAYLOAD)

    import threading

    def terminar():
        time.sleep(0.15)
        idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "n": 7})

    hilo = threading.Thread(target=terminar)
    hilo.start()
    try:
        salida = idempotency.comenzar(store, "r1", "op", PAYLOAD)
    finally:
        hilo.join()
    assert salida == {"ok": True, "n": 7, "idempotent_replay": True}


def test_en_vuelo_abandonado_se_reclama(store, monkeypatch):
    """Un proceso muerto no puede bloquear el request_id para siempre."""
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    reg = store.leer("r1")
    reg.updated_at = time.time() - idempotency.STALE_IN_FLIGHT_SECONDS - 10
    store.escribir(reg)
    # `escribir` refresca updated_at: se fuerza en el archivo directamente.
    f = store.root / "r1.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["updated_at"] = time.time() - idempotency.STALE_IN_FLIGHT_SECONDS - 10
    f.write_text(json.dumps(datos), encoding="utf-8")

    assert idempotency.comenzar(store, "r1", "op", PAYLOAD) is None, (
        "un in_flight abandonado debe poder reclamarse")


def test_fallo_dice_si_es_seguro_reintentar(store):
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "validation_error"},
                               safe_to_retry=True)
    est = idempotency.estado(store, "r1")
    assert est["state"] == idempotency.FAILED
    assert est["safe_to_retry"] is True
    # y se puede reintentar: vuelve a mandar ejecutar
    assert idempotency.comenzar(store, "r1", "op", PAYLOAD) is None


def test_compensado_es_siempre_seguro_de_reintentar(store):
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_error(store, "r1", "op", PAYLOAD,
                               {"ok": False, "error": "bulk_apply_failed"},
                               safe_to_retry=False, compensado=True)
    est = idempotency.estado(store, "r1")
    assert est["state"] == idempotency.COMPENSATED
    assert est["safe_to_retry"] is True


# =========================================================== persistencia ====
def test_el_registro_sobrevive_al_proceso(tmp_path):
    """Otro Store sobre el mismo directorio ve lo que dejo el anterior."""
    uno = Store(tmp_path / "_idem")
    idempotency.comenzar(uno, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(uno, "r1", "op", PAYLOAD, {"ok": True, "v": 1})

    dos = Store(tmp_path / "_idem")
    assert idempotency.comenzar(dos, "r1", "op", PAYLOAD)["idempotent_replay"] is True


def test_escritura_atomica_no_deja_registros_a_medias(store, monkeypatch):
    """Si el proceso muere escribiendo, el registro anterior sigue integro."""
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "v": 1})

    from services import txn as txn_service

    def revienta(*a, **k):
        raise OSError("proceso interrumpido")

    monkeypatch.setattr(txn_service, "durable_write", revienta)
    with pytest.raises(OSError):
        idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "v": 2})

    monkeypatch.undo()
    assert store.leer("r1").result == {"ok": True, "v": 1}
    assert not list(store.root.glob("*.tmp")), "no puede quedar un temporal"


def test_request_id_malicioso_no_escribe_fuera(store):
    """El request_id viene del cliente: nunca se concatena sin validar."""
    from powerbi.errors import PathSecurityError

    for veneno in ("../fuera", "..\\fuera", "a/b", "C:\\evil"):
        with pytest.raises((PathSecurityError, Exception)):
            store.leer(veneno)


def test_caducados_se_purgan(store, monkeypatch):
    idempotency.comenzar(store, "viejo", "op", PAYLOAD)
    f = store.root / "viejo.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["created_at"] = time.time() - idempotency.TTL_SECONDS - 10
    f.write_text(json.dumps(datos), encoding="utf-8")

    assert store.leer("viejo") is None, "un registro caducado no se reproduce"
    assert store.purgar() == 1


# ================================================ extremo a extremo por tool ==
@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session, session.require_active_pbip(), pbip.parent


@pytest.fixture
def mcp(proyecto, monkeypatch):
    """Servidor real, con la sesion del fixture. Se llama por el canal MCP."""
    import config as cfg
    from server import build_server

    session, _active, _raiz = proyecto
    monkeypatch.setattr(cfg, "_session", session)
    return build_server()


def llamar(mcp, nombre, **args):
    """Invocacion REAL por el canal de FastMCP; devuelve el dict de la tool."""
    import asyncio

    salida = asyncio.run(mcp.call_tool(nombre, args))
    payload = salida[1] if isinstance(salida, tuple) else salida
    if isinstance(payload, dict) and "result" in payload and "ok" not in payload:
        payload = payload["result"]
    return payload


def test_dos_llamadas_identicas_mutan_una_sola_vez(proyecto, mcp):
    """REGRESION: sin idempotencia conectada, la segunda renombraba otra vez."""
    _session, active, raiz = proyecto
    origen = pbir_reader.list_pages(active)[0]["display_name"]

    uno = llamar(mcp, "pbi_rename_page", page=origen, new_name="Renombrada",
                 request_id="rid-fijo")
    assert uno["ok"] is True, uno

    tras_primera = huella(raiz)
    dos = llamar(mcp, "pbi_rename_page", page=origen, new_name="Renombrada",
                 request_id="rid-fijo")

    assert dos.get("idempotent_replay") is True, (
        "la segunda llamada con el mismo request_id debe reproducir la primera")
    assert huella(raiz) == tras_primera, "el reintento no puede volver a escribir"


def test_mismo_request_id_con_otros_argumentos_es_rechazado(proyecto, mcp):
    _session, active, _raiz = proyecto
    origen = pbir_reader.list_pages(active)[0]["display_name"]

    llamar(mcp, "pbi_rename_page", page=origen, new_name="Uno", request_id="rid-x")
    salida = llamar(mcp, "pbi_rename_page", page="Uno", new_name="Dos",
                    request_id="rid-x")

    assert salida["ok"] is False
    assert salida["error"] == "idempotency_conflict"


def test_sin_request_id_no_hay_reproduccion(proyecto, mcp):
    """Es opt-in: sin id el cliente no pidio proteccion y no se le inventa."""
    _session, active, _raiz = proyecto
    origen = pbir_reader.list_pages(active)[0]["display_name"]

    uno = llamar(mcp, "pbi_rename_page", page=origen, new_name="Primera")
    assert uno["ok"] is True
    assert "idempotent_replay" not in uno


def test_un_fallo_informa_safe_to_retry(proyecto, mcp):
    salida = llamar(mcp, "pbi_rename_page", page="NoExiste", new_name="X",
                    request_id="rid-fallo")

    assert salida["ok"] is False
    assert salida["safe_to_retry"] is True, (
        "un fallo de validacion no escribio nada: reintentar es seguro")
