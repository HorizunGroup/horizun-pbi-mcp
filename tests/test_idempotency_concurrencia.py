"""La reserva de un `request_id` tiene que ser indivisible.

`comenzar()` hacia leer -> decidir -> escribir sin nada que lo hiciera atomico.
Dos llamadas simultaneas con el mismo `request_id` leian las dos que no habia
registro, las dos escribian `in_flight` y las dos ejecutaban la mutacion: justo
lo que esta pieza existe para impedir. Un cliente que reintenta por timeout es
quien abre esa ventana, asi que no es teorica.

Todas las pruebas de aqui son DETERMINISTAS: la carrera no se busca repitiendo
a ver si sale, se PROVOCA parando a un hilo dentro de la ventana exacta. Una
prueba de concurrencia que depende del planificador no prueba nada, y ademas
falla sola de vez en cuando.

Y el corolario del segundo defecto: un registro corrupto no se pisa. Se
comprueba byte a byte, que es la unica forma de afirmarlo.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from horizun_pbi_mcp.services import idempotency
from horizun_pbi_mcp.services.idempotency import Store

PAYLOAD = {"operation": "pbi_rename_page", "arguments": {"page": "A", "new_name": "B"}}

#: Margen para la cita entre hilos. Generoso a proposito: agotarlo solo puede
#: significar que la coordinacion se rompio, nunca que la maquina iba lenta.
TIMEOUT = 30.0


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "_idem")


# ==================================================== la carrera, entre hilos ==
#: Cuanto se mantiene ABIERTA la ventana entre leer y reservar. No es un
#: margen por si la maquina va lenta —de esos ya nos hemos quemado—: con el
#: arreglo el resultado no depende de este numero, porque el segundo hilo se
#: queda parado en el cerrojo pase lo que pase. Solo sirve para que la version
#: ANTERIOR falle siempre y no una de cada diez veces.
VENTANA = 0.3


def test_dos_hilos_a_la_vez_solo_uno_ejecuta(store):
    """El defecto exacto, provocado a mano.

    No se puede citar a los dos hilos DENTRO de la seccion critica: si la
    exclusion funciona, el segundo no llega nunca y la cita se rompe sola. Lo
    que se hace es mantener abierta la ventana del primero —se le para entre
    leer el registro y reservarlo— y comprobar que el segundo no puede
    colarse por ella.

    Con la version anterior los dos leian ausencia de registro y los dos
    devolvian `None`, que es permiso para mutar. Ahora uno reserva y el otro
    se lo encuentra en vuelo.
    """
    veredictos = []
    fallos = []
    ventana_abierta = threading.Event()
    real_leer = store.leer

    def leer_con_ventana(request_id):
        reg = real_leer(request_id)
        if not ventana_abierta.is_set():
            ventana_abierta.set()      # el primero ya leyo y aun no reservo
            time.sleep(VENTANA)        # ...y se queda ahi, con la ventana abierta
        return reg

    def llamar():
        try:
            veredictos.append(idempotency.comenzar(store, "r1", "op", PAYLOAD))
        except idempotency.RequestInProgressError:
            veredictos.append("en_vuelo")
        except Exception as exc:                      # pragma: no cover
            fallos.append(repr(exc))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(idempotency, "WAIT_SECONDS", 0.2)
        mp.setattr(store, "leer", leer_con_ventana)
        primero = threading.Thread(target=llamar, name="primero")
        primero.start()
        assert ventana_abierta.wait(TIMEOUT), "el primero no llego a leer"
        segundo = threading.Thread(target=llamar, name="segundo")
        segundo.start()
        for h in (primero, segundo):
            h.join(TIMEOUT)

    assert not fallos, f"un hilo reviento: {fallos}"
    assert not primero.is_alive() and not segundo.is_alive(), "algun hilo colgado"

    permisos = [v for v in veredictos if v is None]
    assert len(permisos) == 1, (
        f"exactamente UNO puede recibir permiso para mutar; veredictos: "
        f"{veredictos}")
    assert veredictos.count("en_vuelo") == 1, (
        "el segundo tiene que verlo en vuelo, no ejecutar tambien")
    assert idempotency.estado(store, "r1")["state"] == idempotency.IN_FLIGHT


def test_la_barrera_es_el_cerrojo_y_no_la_suerte(store):
    """Que el segundo ESPERE de verdad, no que llegue tarde por casualidad.

    Se comprueba que mientras un hilo tiene el cerrojo tomado, otro no puede
    entrar en la seccion critica. Sin esto, la prueba de arriba pasaria
    tambien con un `time.sleep` bien puesto.
    """
    dentro = threading.Event()
    puede_salir = threading.Event()
    entro_el_segundo = threading.Event()

    def primero():
        with idempotency._exclusion(store, "r1"):
            dentro.set()
            puede_salir.wait(TIMEOUT)

    def segundo():
        dentro.wait(TIMEOUT)
        with idempotency._exclusion(store, "r1"):
            entro_el_segundo.set()

    h1 = threading.Thread(target=primero)
    h2 = threading.Thread(target=segundo)
    h1.start()
    h2.start()
    try:
        assert dentro.wait(TIMEOUT), "el primero no llego a tomar el cerrojo"
        assert not entro_el_segundo.wait(0.3), (
            "el segundo entro en la seccion critica con el cerrojo tomado")
    finally:
        puede_salir.set()
        h1.join(TIMEOUT)
        h2.join(TIMEOUT)

    assert entro_el_segundo.is_set(), (
        "al soltarlo, el segundo tiene que poder entrar: un cerrojo que no se "
        "suelta es peor que no tenerlo")


def test_el_alta_es_atomica_aunque_el_cerrojo_no_se_aplique(store):
    """La garantia de ultimo recurso.

    Hay sistemas de archivos en red que ignoran los cerrojos. Ahi el alta
    sigue sin poder ganarla dos, porque `O_CREAT|O_EXCL` es atomico en el
    propio sistema de archivos. Se simula anulando el cerrojo.
    """
    import contextlib

    @contextlib.contextmanager
    def sin_cerrojo(_store, _rid):
        yield

    ganadores = []
    barrera = threading.Barrier(2, timeout=TIMEOUT)

    def intentar():
        reg = idempotency.Registro(request_id="r1", operation="op",
                                   payload_fingerprint="fp",
                                   state=idempotency.IN_FLIGHT)
        barrera.wait()
        ganadores.append(store.reservar(reg))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(idempotency, "_exclusion", sin_cerrojo)
        hilos = [threading.Thread(target=intentar) for _ in range(2)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join(TIMEOUT)

    assert ganadores.count(True) == 1, (
        f"solo uno puede crear el registro; resultados: {ganadores}")
    assert ganadores.count(False) == 1


def test_reservar_no_toca_un_registro_que_ya_existe(store):
    reg = idempotency.Registro(request_id="r1", operation="op",
                               payload_fingerprint="fp",
                               state=idempotency.IN_FLIGHT)
    assert store.reservar(reg) is True
    antes = (store.root / "r1.json").read_bytes()

    reg2 = idempotency.Registro(request_id="r1", operation="OTRA",
                                payload_fingerprint="otra",
                                state=idempotency.SUCCEEDED)
    assert store.reservar(reg2) is False
    assert (store.root / "r1.json").read_bytes() == antes, (
        "una reserva perdida no puede haber escrito nada")


# =================================================== la carrera, entre procesos ==
_HIJO = r"""
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from horizun_pbi_mcp.services import idempotency
from horizun_pbi_mcp.services.idempotency import Store

store = Store(sys.argv[2])
arranque = float(sys.argv[3])
# Los dos procesos entran a la vez: se citan por RELOJ DE PARED, que es lo
# unico compartido sin montar un canal entre ellos.
time.sleep(max(0.0, arranque - time.time()))
try:
    r = idempotency.comenzar(store, "rp", "op", {"a": 1})
    print(json.dumps({"veredicto": "ejecuta" if r is None else "reproduce"}))
except idempotency.RequestInProgressError:
    print(json.dumps({"veredicto": "en_vuelo"}))
except Exception as exc:
    print(json.dumps({"veredicto": "error", "detalle": repr(exc)}))
"""


def test_dos_procesos_a_la_vez_solo_uno_ejecuta(store, tmp_path):
    """El cerrojo tiene que valer TAMBIEN entre procesos.

    Un `threading.Lock` no cruza el limite del proceso, y dos clientes MCP
    sobre el mismo directorio de trabajo son dos procesos. Se lanzan dos de
    verdad, citados por reloj de pared.
    """
    guion = tmp_path / "hijo.py"
    guion.write_text(_HIJO, encoding="utf-8")
    src = str((__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))

    arranque = time.time() + 1.5
    procesos = [
        subprocess.Popen([sys.executable, str(guion), src, str(store.root),
                          str(arranque)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
        for _ in range(2)]
    salidas = []
    for p in procesos:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, f"el hijo fallo: {err}"
        salidas.append(json.loads(out.strip().splitlines()[-1]))

    veredictos = [s["veredicto"] for s in salidas]
    assert "error" not in veredictos, salidas
    assert veredictos.count("ejecuta") == 1, (
        f"exactamente un PROCESO puede mutar; veredictos: {veredictos}")


# ================================================ el JSON corrupto no se pisa ==
CORRUPTO = b'{"request_id": "r1", "state": "succ'


def test_un_registro_corrupto_falla_cerrado_y_no_habilita_la_mutacion(store):
    store.root.mkdir(parents=True, exist_ok=True)
    f = store.root / "r1.json"
    f.write_bytes(CORRUPTO)

    with pytest.raises(idempotency.RegistroCorruptoError) as exc:
        idempotency.comenzar(store, "r1", "op", PAYLOAD)

    assert exc.value.code == "idempotency_record_corrupt"
    assert "recovery" in exc.value.details, (
        "fallar cerrado sin decir como salir es un callejon")
    assert f.read_bytes() == CORRUPTO, "el archivo tiene que quedar INTACTO"


def test_un_registro_corrupto_no_se_sobreescribe_ni_al_terminar(store):
    """Ni siquiera guardando un resultado: la evidencia manda.

    Antes `leer` devolvia None ante un JSON roto, la llamada siguiente lo daba
    por inexistente y lo pisaba. Se perdia la unica prueba de que esa peticion
    habia pasado por aqui, justo cuando hacia falta para saber si el cambio se
    aplico.
    """
    store.root.mkdir(parents=True, exist_ok=True)
    f = store.root / "r1.json"
    f.write_bytes(CORRUPTO)

    with pytest.raises(idempotency.RegistroCorruptoError):
        idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True})
    assert f.read_bytes() == CORRUPTO

    with pytest.raises(idempotency.RegistroCorruptoError):
        idempotency.terminar_error(store, "r1", "op", PAYLOAD, {"ok": False},
                                   safe_to_retry=True)
    assert f.read_bytes() == CORRUPTO


def test_nadie_renombra_ni_borra_el_registro_corrupto(store):
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "r1.json").write_bytes(CORRUPTO)

    with pytest.raises(idempotency.RegistroCorruptoError):
        idempotency.comenzar(store, "r1", "op", PAYLOAD)
    store.purgar()

    assert [p.name for p in store.root.glob("*.json")] == ["r1.json"], (
        "no se crea un .bak, no se mueve y no se borra: lo decide una persona")
    assert (store.root / "r1.json").read_bytes() == CORRUPTO


def test_un_registro_sano_si_se_sobreescribe(store):
    """El guard no puede convertirse en un candado sobre lo normal."""
    idempotency.comenzar(store, "r1", "op", PAYLOAD)
    idempotency.terminar_ok(store, "r1", "op", PAYLOAD, {"ok": True, "v": 1})
    assert idempotency.estado(store, "r1")["result"] == {"ok": True, "v": 1}


def test_el_cerrojo_no_se_confunde_con_el_registro(store):
    """El archivo de cerrojo no puede hacer creer que la peticion existe."""
    with idempotency._exclusion(store, "r1"):
        pass
    assert store.leer("r1") is None, (
        "tomar el cerrojo no da de alta la peticion")
    assert (store.root / "r1.lock").exists()
    assert store.purgar() == 0, "purgar mira *.json: el cerrojo no es un registro"
