"""INSTALL-012 — dos servidores MCP no pueden escribir en el mismo stdout.

El defecto. `plugin_launcher.main()` ejecutaba el runtime activo heredándole el
stdio del cliente y, si terminaba con código distinto de cero **antes de 20
segundos**, arrancaba N−1 sobre esa misma conexión. El comentario decía «como no
llegó a escribir nada por stdout, las tuberías del cliente siguen limpias». Eso
no se medía en ninguna parte: el hijo hereda stdout directamente, así que el
launcher no ve un solo byte de lo que escribe. La duración de un proceso no dice
nada sobre lo que llegó a emitir.

La consecuencia es peor que un fallo: un runtime que responde `initialize` y se
muere a los dos segundos hacía que el cliente recibiera, en el mismo canal,
respuestas de **dos servidores distintos** —dos `serverInfo`, dos respuestas
para el mismo `id`—. Un cliente MCP no tiene forma de detectar eso; se queda con
la primera y sigue hablando con la segunda.

La corrección elegida es **preflight**: se verifica el runtime en un proceso
aparte, con tuberías propias, y solo se le entrega el stdio del cliente a uno
que ya haya demostrado que habla MCP. Una vez entregado, no se arranca nada más
sobre esa conexión, pase lo que pase. Es la arquitectura (a) de las dos que el
encargo admite, y se eligió sobre el proxy porque no añade un salto de tuberías
durante toda la sesión.

Estas pruebas ejecutan el lanzador real y miran el canal como lo ve el cliente.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_falso                                          # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
LANZADOR = RAIZ / "scripts" / "plugin_launcher.py"
CONTRATO = runtime_falso.nombres_del_contrato()


@pytest.fixture
def bootstrap():
    spec = importlib.util.spec_from_file_location(
        f"_bs_{uuid.uuid4().hex}", RAIZ / "scripts" / "plugin_bootstrap.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def raiz(tmp_path):
    d = tmp_path / "datos"
    d.mkdir()
    return d


def _entorno(raiz: Path) -> dict:
    return dict(os.environ, HORIZUN_PBI_PLUGIN_DATA=str(raiz),
                HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL="1")


def _lanzar(raiz: Path) -> dict:
    return runtime_falso.hablar_mcp([sys.executable, str(LANZADOR)],
                                    env=_entorno(raiz), timeout=180)


def _instalar(bootstrap, raiz: Path, version: str, monkeypatch, **kw) -> None:
    """Instalación completa de `version` con el runtime falso indicado."""
    monkeypatch.setattr(bootstrap, "VERSION", version)

    def _run(command, *, env, intentos=3):
        if "venv" in command:
            runtime_falso.crear(Path(command[-1]).parent, version=version, **kw)
        elif "pip" in command:
            runtime_falso.escribir_stub(
                Path(command[0]), version=version,
                **{k: v for k, v in kw.items() if k != "sin_entry_points"})

    monkeypatch.setattr(bootstrap, "_run", _run)
    monkeypatch.setattr(bootstrap._salud, "verificar", lambda *a, **k: {
        "ok": True, "fase": "completo", "tools": len(CONTRATO),
        "servidor": runtime_falso.SERVIDOR_REAL, "version": version})
    assert bootstrap.install(raiz, include_validator=False) == 0


#: Cada forma en que un runtime puede ensuciar el canal antes de morirse, y
#: todas en menos de los 20 segundos que el umbral viejo daba por «limpios».
ROTURAS = {
    "initialize-y-muere": {"muere_tras_initialize": True},
    "linea-json-a-medias": {"linea_parcial": True},
    "stdout-no-json": {"basura": True, "muere_tras_initialize": True},
    "tools-list-y-muere": {"muere_tras_tools": True},
}


@pytest.mark.parametrize("rotura", sorted(ROTURAS))
def test_un_activo_que_ensucia_el_canal_no_provoca_dos_servidores(
        bootstrap, raiz, monkeypatch, rotura):
    """La prueba central de INSTALL-012.

    El activo emite bytes y se muere deprisa. Pase lo que pase, el cliente NO
    puede acabar viendo respuestas de dos servidores en el mismo canal.
    """
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch, **ROTURAS[rotura])

    sesion = _lanzar(raiz)
    señales = runtime_falso.mezcla(sesion)

    assert not señales["ids_repetidos"], (
        f"[{rotura}] el cliente recibio dos respuestas para el mismo id: "
        f"{señales['ids_repetidos']}. Han hablado dos servidores.")
    assert len(señales["identidades"]) <= 1, (
        f"[{rotura}] dos serverInfo en la misma conexion: "
        f"{señales['identidades']}")
    assert not señales["lineas_no_json"], (
        f"[{rotura}] llegaron bytes que no son JSON-RPC: "
        f"{señales['lineas_no_json'][:3]}")


@pytest.mark.parametrize("rotura", sorted(ROTURAS))
def test_y_ademas_se_sirve_n_menos_1_entero(bootstrap, raiz, monkeypatch, rotura):
    """No mezclar no puede lograrse dejando al cliente sin servicio.

    El preflight detecta el runtime roto ANTES de entregarle el canal, así que
    N−1 lo recibe limpio y el cliente ve una sesión normal de 134 tools.
    """
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch, **ROTURAS[rotura])

    sesion = _lanzar(raiz)

    assert sesion["servidor"].get("version") == "1.5.4", (
        f"[{rotura}] no se sirvio N-1: {sesion['stderr'][:300]}")
    assert sesion["tools"] == CONTRATO, f"[{rotura}] {len(sesion['tools'])} tools"


def test_el_lanzador_no_se_come_las_peticiones_del_cliente(bootstrap, raiz,
                                                           monkeypatch):
    """El preflight usa tuberías PROPIAS; el stdin del cliente no se toca.

    Si el lanzador leyera del stdin del cliente para decidir, las peticiones ya
    consumidas no llegarían al servidor elegido y habría que reproducirlas. No
    se reproduce nada porque no se consume nada: las tres peticiones se envían
    antes de que el lanzador decida, y las tres se contestan.
    """
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch, muere_tras_initialize=True)

    sesion = _lanzar(raiz)

    assert sesion["initialize"] is not None, "se perdio el initialize del cliente"
    assert sesion["tools"] == CONTRATO, "se perdio el tools/list del cliente"


def test_sin_alternativa_el_activo_roto_no_se_sirve_a_medias(bootstrap, raiz,
                                                             monkeypatch):
    """Sin N−1 al que caer, se sirve el bootstrap: un solo servidor, siempre."""
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch, muere_tras_initialize=True)

    sesion = _lanzar(raiz)
    señales = runtime_falso.mezcla(sesion)

    assert not señales["ids_repetidos"], señales
    assert len(señales["identidades"]) <= 1, señales
    assert sesion["servidor"].get("name") == "horizun-pbi-mcp-installer", sesion


def test_el_lanzador_no_decide_por_tiempo(bootstrap, raiz, monkeypatch):
    """La forma, además del comportamiento.

    Un umbral temporal es exactamente la clase de oráculo que este hallazgo
    prohíbe: mide cuánto duró el proceso y de ahí deduce lo que escribió.
    """
    fuente = LANZADOR.read_text(encoding="utf-8")
    assert "SEGUNDOS_DE_ARRANQUE" not in fuente, (
        "el lanzador vuelve a deducir la limpieza del canal por la duracion "
        "del proceso")
    assert "monotonic" not in fuente, (
        "el lanzador vuelve a cronometrar al runtime para decidir")


# ============================================================================
# G3.3 literal — tras corromper el runtime, `state` no puede seguir en `ready`
# ============================================================================
CORRUPCIONES = ("sin-interprete", "sin-entry-point", "sin-import",
                "responde-a-medias")


def _corromper(bootstrap, raiz: Path, como: str) -> None:
    p = bootstrap.paths(raiz)
    if como == "sin-interprete":
        p["python"].unlink()
    elif como == "sin-entry-point":
        bootstrap._salud.entry_points(p["runtime"])[0].unlink()
    elif como == "sin-import":
        # El caso que ninguna comprobacion estructural puede ver: el interprete
        # y los entry points siguen ahi, y lo que falta es el paquete.
        import shutil
        import subprocess
        sp = subprocess.run(
            [str(p["python"]), "-c",
             "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        shutil.rmtree(Path(sp) / "horizun_pbi_mcp")
    elif como == "responde-a-medias":
        runtime_falso.escribir_stub(p["python"], version="2.0.0",
                                    muere_tras_initialize=True)


@pytest.mark.parametrize("como", CORRUPCIONES)
def test_tras_corromper_el_activo_el_estado_deja_de_ser_ready(
        bootstrap, raiz, monkeypatch, como):
    """G3.3 al pie de la letra: «corromper el runtime y exigir state != ready».

    La entrega anterior cambiaba `sirviendo` a last-known-good y dejaba
    `state` en `ready`. Eso contradice el gate: el campo que un cliente mira
    para saber si esto funciona seguía diciendo que sí sobre un runtime que ya
    no arranca.
    """
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch)
    assert bootstrap.read_status(raiz)["state"] == "ready"

    _corromper(bootstrap, raiz, como)
    if como in ("sin-import", "responde-a-medias"):
        # Corrupcion invisible a simple vista: la descubre el preflight, que es
        # justo lo que hace el lanzador antes de entregar el canal.
        _lanzar(raiz)

    status = bootstrap.read_status(raiz)

    assert status["state"] != "ready", (
        f"[{como}] el estado sigue diciendo ready sobre un runtime corrompido")
    assert status["state"] == "degraded", status["state"]
    assert status["ready"] is False


@pytest.mark.parametrize("como", CORRUPCIONES)
def test_el_estado_degradado_no_se_contradice_consigo_mismo(
        bootstrap, raiz, monkeypatch, como):
    """Cuatro cosas a la vez y ninguna borra a la otra."""
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch)
    _corromper(bootstrap, raiz, como)
    if como in ("sin-import", "responde-a-medias"):
        _lanzar(raiz)

    status = bootstrap.read_status(raiz)

    assert status["degradacion"], f"[{como}] no dice por que esta degradado"
    assert status["degradacion"]["carpeta"] == "2.0.0"
    assert status["degradacion"]["motivo"], "la causa esta vacia"
    assert status["sirviendo"] == "last-known-good", status
    assert status["sirviendo_version"] == "1.5.4", status
    # El ultimo intento de instalacion NO se pierde: son hechos distintos.
    assert status["ultimo_intento"]["resultado"] == "ok"
    assert status["ultimo_intento"]["version"] == "2.0.0"
    assert status["estado_instalacion"] == "ready", (
        "se perdio el resultado del ultimo intento al degradar el activo")


def test_la_degradacion_no_se_escribe_si_el_lock_es_de_otro(
        bootstrap, raiz, monkeypatch):
    """Degradar toca `runtime-state.json`, que es del ciclo de vida.

    Si hay una instalación en curso, el dueño del cerrojo está reescribiendo
    ese archivo. Escribir encima desde el lanzador sería la carrera que
    INSTALL-011 acaba de cerrar por el otro lado.
    """
    import json as _json
    import subprocess
    import time as _time

    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch)
    _corromper(bootstrap, raiz, "sin-import")

    ajeno = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        lock = bootstrap.paths(raiz)["lock"]
        lock.write_text(_json.dumps({
            "pid": ajeno.pid, "token": "del-dueno", "started": _time.time(),
            "proc_creado": bootstrap._cerrojos.creacion_de_proceso(ajeno.pid)}),
            encoding="utf-8")
        antes = (raiz / bootstrap._estado.NOMBRE).read_text(encoding="utf-8")

        sesion = _lanzar(raiz)

        assert (raiz / bootstrap._estado.NOMBRE).read_text(encoding="utf-8") == antes, (
            "el lanzador escribio el estado mientras otro proceso tenia el cerrojo")
        assert lock.read_text(encoding="utf-8").count("del-dueno") == 1
        # Y aun asi sirve N-1: no poder ANOTAR la degradacion no puede
        # significar servir un runtime que se acaba de medir que no arranca.
        assert sesion["servidor"].get("version") == "1.5.4", sesion
    finally:
        ajeno.kill()
        ajeno.wait(timeout=30)

    # Liberado el cerrojo, el siguiente arranque si la anota. No se pierde: se
    # aplaza, que es lo unico que se puede hacer sin pisar al dueño.
    lock.unlink()
    _lanzar(raiz)
    assert bootstrap.read_status(raiz)["state"] == "degraded"
    assert bootstrap._estado.leer(raiz)["degradado"]["carpeta"] == "2.0.0"


def test_una_instalacion_buena_limpia_la_degradacion(bootstrap, raiz, monkeypatch):
    """Reinstalar es la salida, y tiene que borrar la marca."""
    _instalar(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "2.0.0")
    _instalar(bootstrap, raiz, "2.0.0", monkeypatch, muere_tras_initialize=True)
    _lanzar(raiz)
    assert bootstrap.read_status(raiz)["state"] == "degraded"

    _instalar(bootstrap, raiz, "2.0.0", monkeypatch)          # ahora sana

    status = bootstrap.read_status(raiz)
    assert status["state"] == "ready", status
    assert status["degradacion"] is None
    assert status["sirviendo"] == "activo"
