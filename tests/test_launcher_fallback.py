"""INSTALL-001, G4.x — despues de una actualizacion rota, N−1 tiene que SERVIR.

El ensayo anterior daba esto por bueno llamando a `healthcheck.verificar()`
sobre la carpeta de N−1 y comprobando que contestaba. Eso demuestra que N−1
arranca; no demuestra ningun fallback, porque quien elige que se ejecuta es el
lanzador, y el lanzador no miraba a N−1 en ningun momento:

    p = bootstrap.paths()
    if status ready de la VERSION actual and p["python"].is_file(): ...
    return bootstrap_server()

O sea que tras una actualizacion fallida, Codex y Claude recibian el MCP de
bootstrap con sus DOS tools -instalar y consultar estado- mientras la version
anterior seguia entera en disco con las 134. El fallback existia en el disco y
no existia en el codigo.

Estas pruebas ejecutan **el lanzador real como proceso**, le hablan MCP por
stdio -initialize, notifications/initialized, tools/list- y miran cuantas tools
contesta. Es la unica forma de responder a la pregunta que importa: *que recibe
el cliente*.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
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
    """El lanzador se orienta por HORIZUN_PBI_PLUGIN_DATA, como en produccion."""
    return dict(os.environ,
                HORIZUN_PBI_PLUGIN_DATA=str(raiz),
                HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL="1")


def _lanzar(raiz: Path, timeout: int = 120) -> dict:
    return runtime_falso.hablar_mcp(
        [sys.executable, str(LANZADOR)], env=_entorno(raiz), timeout=timeout)


def _instalar_bien(bootstrap, raiz: Path, version: str, monkeypatch, **kw) -> Path:
    """Una instalacion COMPLETA de `version`, con su promocion y su estado.

    Se sustituyen los pasos que descargan -pip, DLL, esquemas, validador- por
    la construccion del runtime falso, y el healthcheck por su veredicto. Todo
    lo demas -staging, promocion, journal, estado, limpieza- es el codigo real.
    """
    monkeypatch.setattr(bootstrap, "VERSION", version)
    creado: dict = {}

    def _run_falso(command, *, env, intentos=3):
        if "venv" in command:
            carpeta = Path(command[-1]).parent
            creado["python"] = runtime_falso.crear(carpeta, version=version, **kw)
            return
        if "pip" in command:
            # Si hubo siembra, `venv` no llego a ejecutarse: el staging trae el
            # runtime COPIADO de la version anterior, con su servidor. Aqui se
            # reescribe con la version que se esta instalando, que es lo que
            # hace de verdad `pip install` en este paso.
            creado["python"] = runtime_falso.escribir_stub(
                Path(command[0]), version=version,
                **{k: v for k, v in kw.items() if k != "sin_entry_points"})

    monkeypatch.setattr(bootstrap, "_run", _run_falso)
    monkeypatch.setattr(bootstrap._salud, "verificar", lambda *a, **k: {
        "ok": True, "fase": "completo", "tools": len(CONTRATO),
        "servidor": runtime_falso.SERVIDOR_REAL, "version": version})

    assert bootstrap.install(raiz, include_validator=False) == 0
    return creado["python"]


# ============================================================================
# El camino feliz, para que el resto signifique algo
# ============================================================================
def test_el_lanzador_real_sirve_las_tools_del_runtime_activo(bootstrap, raiz,
                                                             monkeypatch):
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)

    sesion = _lanzar(raiz)

    assert sesion["servidor"].get("name") == runtime_falso.SERVIDOR_REAL, sesion
    assert sesion["tools"] == CONTRATO, (
        f"el lanzador sirvio {len(sesion['tools'])} tools: {sesion['stderr'][:400]}")


def test_sin_ningun_runtime_el_lanzador_sirve_el_bootstrap(raiz):
    """Y se ve que es el bootstrap: dos tools y otro serverInfo."""
    sesion = _lanzar(raiz)
    assert sesion["servidor"].get("name") == "horizun-pbi-mcp-installer"
    assert sorted(sesion["tools"]) == ["pbi_install_runtime", "pbi_install_status"]


# ============================================================================
# Lo que faltaba: una actualizacion rota no puede costar las 134 tools
# ============================================================================
#: Cada punto donde una actualizacion se cae de verdad. Los tres primeros son
#: descargas, y el equipo tiene medida una carrera DNS que las tumba sola.
#:
#: El validador PBIR **no** esta en la lista, y no por descuido: INSTALL-002 lo
#: declara OPCIONAL, asi que un fallo suyo no debe tumbar la instalacion. Su
#: caso se comprueba aparte, exigiendo lo contrario que aqui.
FALLOS = ["pip", "dll", "schemas", "handshake", "promocion"]


@pytest.mark.parametrize("donde", FALLOS)
def test_si_la_actualizacion_falla_el_lanzador_sigue_sirviendo_n_menos_1(
        bootstrap, raiz, monkeypatch, donde):
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)

    # Ahora la actualizacion a 1.5.5, rota en `donde`.
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")

    pasos: list[str] = []

    def _run_roto(command, *, env, intentos=3):
        if "venv" in command:
            pasos.append("venv")
            runtime_falso.crear(Path(command[-1]).parent, version="1.5.5")
            return
        texto = " ".join(str(c) for c in command)
        etapa = ("pip" if "pip" in texto else
                 "dll" if "fetch_libs" in texto else
                 "schemas" if "fetch_pbir" in texto else
                 "validator" if "fetch_report_validator" in texto else "otro")
        pasos.append(etapa)
        if etapa == donde:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap, "_run", _run_roto)
    monkeypatch.setattr(bootstrap._salud, "verificar", lambda *a, **k: (
        {"ok": False, "fase": "tools-list", "tools": 2,
         "error": "solo registro 2 tools"} if donde == "handshake" else
        {"ok": True, "fase": "completo", "tools": len(CONTRATO),
         "servidor": runtime_falso.SERVIDOR_REAL, "version": "1.5.5"}))
    if donde == "promocion":
        def _promover_roto(*a, **k):
            raise bootstrap._promocion.PromocionError("no se pudo publicar")
        monkeypatch.setattr(bootstrap._promocion, "promover", _promover_roto)

    assert bootstrap.install(raiz, include_validator=True) == 1, (
        f"la instalacion no fallo en {donde}: {pasos}")

    # 1. El estado dice las DOS cosas a la vez.
    status = bootstrap.read_status(raiz)
    assert status["state"] == "failed"
    assert status["sirviendo"] == "last-known-good", status
    assert status["sirviendo_version"] == "1.5.4", status
    assert status["ultimo_intento"]["resultado"] == "failed"
    assert status["ultimo_intento"]["error"], "el fallo no dejo motivo"

    # 2. Y el LANZADOR REAL entrega las 134 tools de 1.5.4, no las dos del
    #    bootstrap. Esto es lo que ve el cliente.
    sesion = _lanzar(raiz)
    assert sesion["servidor"].get("name") == runtime_falso.SERVIDOR_REAL, (
        f"con el fallo en {donde} el cliente recibio el bootstrap: {sesion}")
    assert sesion["servidor"].get("version") == "1.5.4"
    assert sesion["tools"] == CONTRATO, (
        f"con el fallo en {donde} el cliente recibio {len(sesion['tools'])} "
        f"tools en vez de {len(CONTRATO)}")


def test_un_validador_roto_no_hace_falta_ningun_fallback(bootstrap, raiz,
                                                         monkeypatch):
    """El opcional falla y la actualizacion SIGUE: se sirve la version nueva.

    Es la otra cara de la misma moneda. Un fallback que se disparara aqui seria
    tan defectuoso como no tenerlo cuando toca: dejaria a la persona en la
    version vieja por un componente que el producto declara prescindible.
    """
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    # Se fuerza el preflight a "elegible" para no depender de que la maquina
    # tenga Node: lo que se prueba es el manejo del fallo, no el preflight.
    monkeypatch.setattr(bootstrap, "preflight_validador",
                        lambda incluir: (True, "eligible", "v20.0.0"))

    def _run(command, *, env, intentos=3):
        if "venv" in command:
            runtime_falso.crear(Path(command[-1]).parent, version="1.5.5")
            return
        if "pip" in command:
            runtime_falso.escribir_stub(Path(command[0]), version="1.5.5")
            return
        if "fetch_report_validator" in " ".join(str(c) for c in command):
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(bootstrap, "_run", _run)
    monkeypatch.setattr(bootstrap._salud, "verificar", lambda *a, **k: {
        "ok": True, "fase": "completo", "tools": len(CONTRATO),
        "servidor": runtime_falso.SERVIDOR_REAL, "version": "1.5.5"})

    assert bootstrap.install(raiz, include_validator=True) == 0

    status = bootstrap.read_status(raiz)
    assert status["state"] == "ready"
    assert status["sirviendo"] == "activo"
    assert status["validator"]["state"] == "failed_optional", status["validator"]

    sesion = _lanzar(raiz)
    assert sesion["servidor"].get("version") == "1.5.5", sesion
    assert sesion["tools"] == CONTRATO


def test_el_error_de_la_actualizacion_no_se_pierde_al_servir_n_menos_1(
        bootstrap, raiz, monkeypatch):
    """Servir N−1 no puede tapar que la actualizacion fallo.

    Los dos hechos conviven: `ultimo_intento` guarda el fallo con su error y
    `sirviendo` dice que hay 1.5.4 dando servicio. Antes solo cabia uno de los
    dos en `install-status.json`, y ganaba el que borraba al otro.
    """
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("la DNS otra vez")))

    bootstrap.install(raiz, include_validator=False)
    status = bootstrap.read_status(raiz)

    assert "la DNS otra vez" in status["ultimo_intento"]["error"]
    assert status["sirviendo_evidencia"]["version"] == "1.5.4"
    assert status["sirviendo_evidencia"]["tools"] == len(CONTRATO), (
        "lo que se va a servir se anota sin la evidencia de lo que se le "
        "comprobo: sin ella no se distingue de una carpeta cualquiera")
    assert status["sirviendo"] == "last-known-good"


def test_el_mensaje_de_fallo_no_promete_que_relanzar_reanuda(bootstrap, raiz,
                                                             monkeypatch):
    """Decia 'Relanzar REANUDA desde este paso'. No reanuda: descarta el staging.

    El precio de esa frase no es cosmetico: invita a reiniciar el cliente una y
    otra vez esperando que la instalacion continue sola.
    """
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("fallo")))
    bootstrap.install(raiz, include_validator=False)

    mensaje = bootstrap.read_status(raiz)["message"]
    assert "REANUDA" not in mensaje, mensaje
    assert "NO reanuda" in mensaje, mensaje
    assert "pbi_install_runtime" in mensaje, (
        "no dice como reintentar, que es lo unico que la persona necesita saber")


def test_relanzar_el_lanzador_no_reinstala_en_bucle(bootstrap, raiz, monkeypatch):
    """Un fallo no puede convertir cada arranque en una reinstalacion."""
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("fallo")))
    bootstrap.install(raiz, include_validator=False)

    entorno = dict(os.environ, HORIZUN_PBI_PLUGIN_DATA=str(raiz))
    entorno.pop("HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL", None)
    sesion = runtime_falso.hablar_mcp([sys.executable, str(LANZADOR)], env=entorno)

    assert sesion["servidor"].get("name") == "horizun-pbi-mcp-installer"
    # El status sigue en `failed`: nadie lo devolvio a `installing`, o sea que
    # no se lanzo otro instalador solo por arrancar.
    assert bootstrap.read_status(raiz)["state"] == "failed"


# ============================================================================
# G3.3 — un runtime YA promovido que se corrompe despues
# ============================================================================
@pytest.mark.parametrize("como", ["sin-interprete", "sin-entry-points"])
def test_un_runtime_promovido_que_se_corrompe_deja_de_anunciarse_ready(
        bootstrap, raiz, monkeypatch, como):
    """`ready` en disco no es una promesa perpetua: el disco cambia despues."""
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    _instalar_bien(bootstrap, raiz, "1.5.5", monkeypatch)

    assert bootstrap.read_status(raiz)["sirviendo"] == "activo"
    activo = bootstrap.paths(raiz)

    if como == "sin-interprete":
        activo["python"].unlink()
    else:
        for entrada in bootstrap._salud.entry_points(activo["runtime"]):
            entrada.unlink()

    status = bootstrap.read_status(raiz)
    assert status["state"] == "ready", "el status en disco no cambia solo"
    assert status["sirviendo"] == "last-known-good", (
        "se sigue anunciando como operativo un runtime que ya no arranca")
    assert status["sirviendo_version"] == "1.5.4"

    sesion = _lanzar(raiz)
    assert sesion["servidor"].get("version") == "1.5.4", sesion
    assert sesion["tools"] == CONTRATO


def test_un_activo_que_revienta_al_arrancar_cae_al_ultimo_bueno(bootstrap, raiz,
                                                                monkeypatch):
    """La corrupcion que NINGUNA comprobacion barata puede ver.

    El interprete esta, los entry points estan, y aun asi el servidor se muere
    al arrancar -le falta una dependencia transitiva, un antivirus se llevo un
    archivo-. Como no llego a escribir nada por stdout, las tuberias del
    cliente siguen limpias y el lanzador puede servirle N−1 por las mismas.
    """
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    _instalar_bien(bootstrap, raiz, "1.5.5", monkeypatch, muere=True)

    assert bootstrap.read_status(raiz)["sirviendo"] == "activo"

    sesion = _lanzar(raiz)

    assert sesion["servidor"].get("version") == "1.5.4", (
        f"no cayo al ultimo bueno: {sesion['stderr'][:400]}")
    assert sesion["tools"] == CONTRATO


# ============================================================================
# La evidencia manda: una carpeta con python.exe no es un N−1
# ============================================================================
def test_una_carpeta_con_interprete_pero_sin_evidencia_no_sirve_de_fallback(
        bootstrap, raiz, monkeypatch):
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    # Un runtime perfectamente arrancable... del que nadie ha comprobado nada.
    runtime_falso.crear(raiz / "1.5.4", version="1.5.4")

    seleccion = bootstrap.seleccionar_runtime(raiz)

    assert seleccion["modo"] == "ninguno", (
        f"eligio como N−1 una carpeta sin evidencia de haber pasado el "
        f"handshake: {seleccion}")


@pytest.mark.parametrize("estropear", [
    {"tools": 0},
    {"servidor": ""},
    {"version": ""},
    {"carpeta": "../fuera"},
    {"carpeta": ".staging-a-medias"},
])
def test_un_registro_incompleto_o_tramposo_no_se_elige(bootstrap, raiz,
                                                       monkeypatch, estropear):
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    runtime_falso.crear(raiz / "1.5.4", version="1.5.4")
    registro = bootstrap._estado.evidencia(
        "1.5.4", version="1.5.4", servidor=runtime_falso.SERVIDOR_REAL,
        tools=len(CONTRATO))
    registro.update(estropear)
    bootstrap._estado.escribir(raiz, dict(bootstrap._estado.vacio(),
                                          last_known_good=registro))

    assert bootstrap.seleccionar_runtime(raiz)["modo"] == "ninguno", estropear


def test_una_instalacion_anterior_sin_estado_se_adopta_comprobandola(
        bootstrap, raiz, monkeypatch):
    """La migracion, que es por donde el defecto reaparecia.

    Quien tenga 1.5.4 instalada por el instalador ANTERIOR a este estado no
    tiene `runtime-state.json`. Si la actualizacion a 1.5.5 falla y nadie ha
    adoptado lo que ya habia, el lanzador no encuentra fallback y sirve el
    bootstrap con dos tools, teniendo en disco una instalacion entera y sana.

    La adopcion no se hace por fe: se ejecuta el mismo handshake MCP que se le
    exige a cualquier runtime antes de promoverlo.
    """
    runtime_falso.crear(raiz / "1.5.4", version="1.5.4")
    assert bootstrap._estado.leer(raiz)["activo"] is None
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("la actualizacion se cae")))

    assert bootstrap.install(raiz, include_validator=False) == 1

    adoptado = bootstrap._estado.leer(raiz)["activo"]
    assert adoptado is not None, "no adopto la instalacion que ya estaba"
    assert adoptado["carpeta"] == "1.5.4"
    assert adoptado["tools"] == len(CONTRATO), (
        "la adopto sin medirla: eso es exactamente la suposicion que sobra")

    sesion = _lanzar(raiz)
    assert sesion["servidor"].get("version") == "1.5.4", sesion
    assert sesion["tools"] == CONTRATO


def test_no_se_adopta_una_carpeta_que_no_supera_el_handshake(bootstrap, raiz,
                                                             monkeypatch):
    """Adoptar por tener `python.exe` seria repetir el defecto con otro nombre."""
    runtime_falso.crear(raiz / "1.5.4", version="1.5.4", muere=True)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    monkeypatch.setattr(bootstrap, "_run", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("la actualizacion se cae")))

    bootstrap.install(raiz, include_validator=False)

    assert bootstrap._estado.leer(raiz)["activo"] is None, (
        "adopto un runtime que no llega a contestar")
    assert bootstrap.seleccionar_runtime(raiz)["modo"] == "ninguno"


def test_tras_una_promocion_buena_queda_UN_n_menos_1_que_arranca(bootstrap, raiz,
                                                                 monkeypatch):
    """No una carpeta que solo tenga `install-status.json`.

    Este es el fallo que destapo el ensayo real y no las pruebas unitarias: al
    actualizar de 1.5.4 a 1.5.5, la promocion conservaba como `.previous-` lo
    que hubiera en el destino -una carpeta recien creada con el status y nada
    mas- y la limpieza se llevaba 1.5.4, que era el unico runtime completo.
    Resultado: `ready`, un N−1 "conservado" y ni un interprete al que volver.
    """
    _instalar_bien(bootstrap, raiz, "1.5.4", monkeypatch)
    monkeypatch.setattr(bootstrap, "VERSION", "1.5.5")
    _instalar_bien(bootstrap, raiz, "1.5.5", monkeypatch)

    estado = bootstrap._estado.leer(raiz)
    lkg = estado["last_known_good"]
    assert lkg and lkg["version"] == "1.5.4", estado
    py = bootstrap._runtime_arrancable(raiz, lkg)
    assert py is not None and py.is_file(), (
        f"el N−1 conservado no tiene interprete: {lkg}")

    # Y sigue ahi despues de la limpieza: nadie se lo llevo por delante.
    assert (raiz / lkg["carpeta"]).is_dir()
    hermanas = [d.name for d in raiz.iterdir()
                if d.is_dir() and bootstrap._runtime_arrancable(
                    raiz, {"carpeta": d.name, "version": "x",
                           "servidor": "x", "tools": 1})]
    assert sorted(hermanas) == ["1.5.4", "1.5.5"], (
        f"quedaron mas runtimes arrancables de la cuenta: {hermanas}")
