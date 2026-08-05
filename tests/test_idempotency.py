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
import threading
import time
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator
from horizun_pbi_mcp.services import idempotency
from horizun_pbi_mcp.services.idempotency import Store
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


#: Margen para la coordinacion entre hilos de la prueba de abajo. Es generoso a
#: proposito: en una maquina sana los eventos se cumplen en milisegundos, asi que
#: agotarlo solo puede significar que la coordinacion se rompio (un hilo que no
#: arranco, un evento que nadie senala), nunca que la maquina iba lenta.
TIMEOUT_SINCRONIZACION = 30.0


def test_en_vuelo_que_termina_durante_la_espera_devuelve_el_resultado(store, monkeypatch):
    """La espera es acotada, pero si el otro acaba a tiempo se aprovecha.

    Los dos hilos se coordinan por EVENTO, no por reloj. Antes el hilo que
    termina dormia 0.15s y se confiaba en que la espera de 1s no se agotara
    primero; con la suite completa por delante ese margen no siempre se cumplia
    y la prueba fallaba sin que la idempotencia tuviera nada que ver. Ahora:

    1. el hilo que termina no escribe hasta que el principal ha leido el
       `in_flight` —si escribiera antes, el principal contestaria por la via de
       reproduccion directa y esto dejaria de probar la espera—;
    2. el bucle de espera del principal no vuelve a mirar el registro hasta que
       el otro hilo ha acabado de escribirlo.

    Con esas dos barreras el resultado no depende de cuanto tarde nadie, y
    `WAIT_SECONDS` deja de ser una carrera contra la carga de la maquina.
    """
    monkeypatch.setattr(idempotency, "WAIT_SECONDS", 5.0)
    idempotency.comenzar(store, "r1", "op", PAYLOAD)

    leido_en_vuelo = threading.Event()      # el principal ya se comprometio a esperar
    resultado_guardado = threading.Event()  # el otro ya llamo a terminar_ok
    descoordinado = []                      # fallos de la cita entre hilos

    def terminar():
        try:
            if not leido_en_vuelo.wait(TIMEOUT_SINCRONIZACION):
                descoordinado.append(
                    "el hilo principal no llego a leer el in_flight en "
                    f"{TIMEOUT_SINCRONIZACION:g}s")
                return
            idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "n": 7})
        except Exception as exc:                  # pragma: no cover - diagnostico
            descoordinado.append(f"el hilo que termina la peticion reviento: {exc!r}")
        finally:
            resultado_guardado.set()   # pase lo que pase, el principal no se cuelga

    leer_real = store.leer
    primera_lectura = True   # todas salen del hilo principal: no hace falta lock
    principal = threading.current_thread()

    def leer_coordinado(request_id):
        """Convierte cada lectura del registro en una cita con el otro hilo.

        Solo las del hilo PRINCIPAL. `terminar_ok` tambien lee ahora —para
        comprobar que sigue siendo el dueno de la reserva antes de escribir— y
        si esa lectura entrara en la cita, el hilo que termina se quedaria
        esperandose a si mismo.
        """
        nonlocal primera_lectura
        if threading.current_thread() is not principal:
            return leer_real(request_id)
        if primera_lectura:
            primera_lectura = False
            registro = leer_real(request_id)
            leido_en_vuelo.set()       # ya vio el in_flight: el otro puede terminar
            return registro
        # Lecturas del bucle de espera: no se mira el registro hasta que el otro
        # hilo termino de escribirlo, tarde lo que tarde.
        if not resultado_guardado.wait(TIMEOUT_SINCRONIZACION):
            descoordinado.append(
                "el hilo que termina la peticion no guardo el resultado en "
                f"{TIMEOUT_SINCRONIZACION:g}s")
            resultado_guardado.set()   # no volver a esperar en cada vuelta
        return leer_real(request_id)

    monkeypatch.setattr(store, "leer", leer_coordinado)

    hilo = threading.Thread(target=terminar, name="termina-el-en-vuelo")
    hilo.start()
    try:
        salida = idempotency.comenzar(store, "r1", "op", PAYLOAD)
    except idempotency.RequestInProgressError as exc:
        # Se juzga despues: si la cita entre hilos fallo, la causa es esa y no
        # la idempotencia, y el mensaje debe decirlo.
        salida = f"RequestInProgressError: {exc}"
    finally:
        hilo.join(TIMEOUT_SINCRONIZACION)

    if hilo.is_alive():
        descoordinado.append("el hilo que termina la peticion no acabo")
    assert not descoordinado, (
        "fallo la SINCRONIZACION de la prueba, no la idempotencia: "
        + "; ".join(descoordinado))
    assert salida == {"ok": True, "n": 7, "idempotent_replay": True}


def test_en_vuelo_antiguo_no_se_reclama_solo(store):
    """Antes se reclamaba, y eso era la carrera.

    Un `in_flight` con mas de `STALE_IN_FLIGHT_SECONDS` se daba por abandonado
    y se AUTORIZABA otra ejecucion. "Antiguo" no prueba que el primero haya
    muerto —`pbi_open_and_refresh` pasa de cinco minutos sin despeinarse— y
    aunque hubiera muerto, nadie sabe si la escritura llego a aplicarse.

    La regresion viva de esa carrera esta en
    `tests/test_idempotency_intentos.py`; aqui se fija la regla.
    """
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    envejecer(store, "r1", idempotency.STALE_IN_FLIGHT_SECONDS + 10)

    with pytest.raises(idempotency.ResultadoDesconocidoError) as exc:
        idempotency.comenzar(store, "r1", "op", PAYLOAD)
    assert exc.value.code == "request_outcome_unknown"
    assert exc.value.details["safe_to_retry"] is False
    assert "recovery" in exc.value.details, "fallar cerrado sin salida es un muro"


def envejecer(store, request_id, segundos):
    """Retrasa `updated_at` en el archivo.

    `escribir` lo refresca, asi que no vale pasar por el Store: hay que tocar
    el JSON directamente.
    """
    f = store.root / f"{request_id}.json"
    datos = json.loads(f.read_text(encoding="utf-8"))
    datos["updated_at"] = time.time() - segundos
    f.write_text(json.dumps(datos), encoding="utf-8")


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

    from horizun_pbi_mcp.services import txn as txn_service

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
    from horizun_pbi_mcp.powerbi.errors import PathSecurityError

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
    import horizun_pbi_mcp.config as cfg
    from horizun_pbi_mcp.server import build_server

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
