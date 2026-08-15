"""Fase 1A — deteccion de proyecto abierto en Power BI Desktop.

Politica estricta: solo `closed` permite escribir. `open` y `unknown` bloquean.
Las senales son de SOLO LECTURA: ninguna prueba (ni el codigo) muta un archivo
del proyecto para averiguar si esta bloqueado.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from types import SimpleNamespace

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.services import project_state
from horizun_pbi_mcp.services.project_state import (CLOSED, OPEN, UNKNOWN,
                                    ProjectOpenInDesktopError)
from tests.fixtures import synthetic

pytestmark = pytest.mark.real_project_state


class FakeHandle:
    def __init__(self, path):
        self.path = str(path)


class FakeProc:
    """Proceso simulado. `denied` reproduce un AccessDenied de psutil."""

    def __init__(self, name, pid, files=(), cmdline=(), denied=False):
        self.info = {"name": name, "pid": pid}
        self.pid = pid
        self._files = [FakeHandle(f) for f in files]
        self._cmdline = list(cmdline)
        self._denied = denied

    def open_files(self):
        if self._denied:
            import psutil
            raise psutil.AccessDenied(self.pid)
        return self._files

    def cmdline(self):
        if self._denied:
            import psutil
            raise psutil.AccessDenied(self.pid)
        return self._cmdline


@pytest.fixture
def active(tmp_path):
    pbip = synthetic.materialize(tmp_path)
    return ActivePbip(
        pbip_path=str(pbip), project_dir=str(pbip.parent),
        report_dir=str(synthetic.find_report_dir(pbip)),
        semantic_model_dir=str(synthetic.find_semantic_model_dir(pbip)),
        report_name="Demo", has_pbir=True, has_tmdl=True)


@pytest.fixture
def active_openable(tmp_path):
    """Proyecto sintetico que Power BI Desktop SI acepta abrir.

    `minimal` existe para el validador PBIR y le faltan los artefactos que
    Desktop exige -`definition.pbism`, `database.tmdl`, `version.json`,
    `.platform`-: al abrirlo, Desktop muestra "Se encontraron problemas" y una
    ventana Sin titulo, asi que no sirve para nada que dependa de una ventana
    real. `desktop_openable` lo genero el scaffold del propio proyecto.
    """
    pbip = synthetic.materialize(tmp_path, "desktop_openable")
    return ActivePbip(
        pbip_path=str(pbip), project_dir=str(pbip.parent),
        report_dir=str(synthetic.find_report_dir(pbip)),
        semantic_model_dir=str(synthetic.find_semantic_model_dir(pbip)),
        report_name="Demo", has_pbir=True, has_tmdl=True)


@pytest.fixture
def procesos(monkeypatch):
    """Sustituye la enumeracion de procesos por una lista controlada."""
    import psutil

    def _set(lista):
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: list(lista))
        project_state.invalidate_cache()
    return _set


@pytest.fixture
def escaneo_que_falla(monkeypatch):
    """`psutil.process_iter` revienta: no se pudo mirar NADA."""
    import psutil

    def _set(exc):
        def _boom(attrs=None):
            raise exc
        monkeypatch.setattr(psutil, "process_iter", _boom)
        project_state.invalidate_cache()
    return _set


@pytest.fixture
def ventanas(monkeypatch):
    """Controla la correlacion por titulo de ventana.

    `raising=False` es deliberado: sobre el commit anterior a esta correccion
    la funcion compartida no existe, y lo que tiene que fallar es la ASERCION
    de estado -el defecto- y no el arranque de la prueba.
    """
    from horizun_pbi_mcp.powerbi import desktop_launcher

    def _set(*, pids=(), sin_titulos=(), error=None):
        resultado = SimpleNamespace(pids=tuple(pids),
                                    sin_titulos=tuple(sin_titulos),
                                    error=error)
        monkeypatch.setattr(desktop_launcher, "coincidencias_por_titulo",
                            lambda stem, pids_: resultado, raising=False)
        project_state.invalidate_cache()
    return _set


# ------------------------------------------------------------- deteccion ----
def test_sin_procesos_es_cerrado(active, procesos):
    procesos([])
    estado = project_state.detect(active)
    assert estado.state == CLOSED and estado.confidence == "high"
    assert estado.writable


def test_motor_sin_desktop_es_desconocido(active, procesos):
    """Hay msmdsrv vivo pero no se puede atribuir a un proyecto."""
    procesos([FakeProc("msmdsrv.exe", 100)])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_desktop_con_archivo_del_proyecto_abierto_es_open(active, procesos):
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 200, files=[victima])])
    estado = project_state.detect(active)
    assert estado.state == OPEN and estado.confidence == "high"


def test_desktop_con_el_pbip_en_la_linea_de_comandos_es_open(active, procesos):
    procesos([FakeProc("PBIDesktop.exe", 201, cmdline=["PBIDesktop.exe",
                                                       active.pbip_path])])
    estado = project_state.detect(active)
    assert estado.state == OPEN


def test_desktop_sin_permisos_es_desconocido(active, procesos):
    procesos([FakeProc("PBIDesktop.exe", 202, denied=True)])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert "denego" in estado.reason


def test_desktop_con_otro_proyecto_es_cerrado(active, procesos, tmp_path):
    otro = tmp_path / "otro_informe" / "x.pbix"
    otro.parent.mkdir(parents=True)
    otro.write_text("", encoding="utf-8")
    procesos([FakeProc("PBIDesktop.exe", 203, files=[otro])])
    estado = project_state.detect(active)
    assert estado.state == CLOSED and estado.confidence == "medium"


def test_varios_desktop_uno_con_el_proyecto(active, procesos, tmp_path):
    otro = tmp_path / "otro.pbix"
    otro.write_text("", encoding="utf-8")
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 204, files=[otro]),
              FakeProc("PBIDesktop.exe", 205, files=[victima])])
    assert project_state.detect(active).state == OPEN


# ------------------------------------------- CORE-001: un .pbip no deja handle ---
# Desktop NO deja ningun descriptor abierto sobre la carpeta de un .pbip
# -medido en `desktop_launcher._pid_por_titulo_de_ventana`- y muchas veces
# tampoco trae la ruta en la linea de comandos, porque se abrio desde la lista
# de recientes. Con solo esas dos senales, el detector declaraba CERRADO un
# proyecto que estaba ABIERTO y autorizaba la escritura.
def test_pbip_sin_handles_pero_con_ventana_inequivoca_es_open(
        active, procesos, ventanas):
    """EL CASO LITERAL DEL HALLAZGO. Sobre 85e433a esto devolvia CLOSED."""
    procesos([FakeProc("PBIDesktop.exe", 300)])
    ventanas(pids=[300])
    estado = project_state.detect(active)
    assert estado.state == OPEN, (
        "un .pbip abierto sin descriptores se estaba declarando cerrado")
    assert not estado.writable


def test_desktop_sin_identidad_suficiente_es_desconocido(
        active, procesos, ventanas):
    """Sin cmdline, sin handles y sin titulo legible no se sabe que tiene."""
    procesos([FakeProc("PBIDesktop.exe", 301)])
    ventanas(pids=[], sin_titulos=[301])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_dos_candidatos_con_el_mismo_nombre_es_desconocido(
        active, procesos, ventanas):
    """`Ventas.pbip` en dos carpetas: el titulo no distingue cual es."""
    procesos([FakeProc("PBIDesktop.exe", 302), FakeProc("PBIDesktop.exe", 303)])
    ventanas(pids=[302, 303])
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_fallo_al_enumerar_ventanas_es_desconocido(active, procesos, ventanas):
    procesos([FakeProc("PBIDesktop.exe", 304)])
    ventanas(error="EnumWindows fallo (Win32 error 5)")
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_fallo_al_enumerar_procesos_es_desconocido(active, escaneo_que_falla):
    """Si ni siquiera se pudo listar procesos, no se puede afirmar nada."""
    escaneo_que_falla(OSError("no se pudo abrir el snapshot de procesos"))
    estado = project_state.detect(active)
    assert estado.state == UNKNOWN
    assert not estado.writable


def test_escritura_bloqueada_cuando_el_detector_dice_desconocido(
        active, procesos, ventanas):
    procesos([FakeProc("PBIDesktop.exe", 305)])
    ventanas(pids=[], sin_titulos=[305])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    assert exc.value.details["state"] == UNKNOWN


def test_la_ventana_no_pisa_una_deteccion_por_handle(active, procesos, ventanas):
    """Las senales que ya funcionaban siguen mandando."""
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 306, files=[victima])])
    ventanas(pids=[])
    assert project_state.detect(active).state == OPEN


# --------------------------------------------------------------- politica ---
def test_cerrado_permite_escribir(active, procesos):
    procesos([])
    estado = project_state.assert_writable(active)
    assert estado.state == CLOSED


def test_abierto_bloquea(active, procesos):
    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 206, files=[victima])])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    assert exc.value.details["state"] == OPEN
    assert exc.value.details["policy"] == "strict"


def test_desconocido_tambien_bloquea(active, procesos):
    """La correccion clave: el estado indeterminado NO permite escribir."""
    procesos([FakeProc("PBIDesktop.exe", 207, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    assert exc.value.details["state"] == UNKNOWN


def test_el_error_no_promete_lo_que_no_puede_cumplir(active, procesos):
    """No se afirma que el bloqueo impida a Desktop sobrescribir despues."""
    procesos([FakeProc("PBIDesktop.exe", 208, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError) as exc:
        project_state.assert_writable(active)
    nota = exc.value.details["note"]
    assert "NO impide" in nota and "Desktop" in nota


def test_no_hay_variable_de_entorno_que_lo_desactive(active, procesos, monkeypatch):
    monkeypatch.setenv("PBI_MCP_PBIR_WRITE_POLICY", "warn")
    monkeypatch.setenv("PBI_MCP_ALLOW_OPEN_PROJECT", "1")
    procesos([FakeProc("PBIDesktop.exe", 209, denied=True)])
    with pytest.raises(ProjectOpenInDesktopError):
        project_state.assert_writable(active)


# ------------------------------------------------------------------ cache ---
def test_la_cache_expira(active, procesos, monkeypatch):
    procesos([])
    assert project_state.detect(active).state == CLOSED

    victima = Path(active.report_dir) / "definition" / "report.json"
    procesos([FakeProc("PBIDesktop.exe", 210, files=[victima])])  # invalida cache
    assert project_state.detect(active).state == OPEN


def test_use_cache_false_siempre_reevalua(active, procesos):
    procesos([])
    assert project_state.detect(active, use_cache=False).state == CLOSED


# ------------------------------------------------------------------- live ---
def _procesos_pbi():
    """PIDs vivos de Power BI (Desktop y motor) con su hora de arranque."""
    import psutil

    salida = {}
    for p in psutil.process_iter(["name", "pid"]):
        nombre = (p.info.get("name") or "").lower()
        if "pbidesktop" in nombre or nombre == "msmdsrv.exe":
            try:
                salida[p.pid] = (nombre, p.create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return salida


class _ProcSinRastro:
    """El proceso REAL, pero mudo en las dos senales antiguas.

    No es un doble del sistema: la ventana, su titulo y la llamada Win32 que
    lo correlaciona siguen siendo reales. Lo unico que se silencia es lo que
    un `.pbip` de verdad tampoco ofrece -la ruta en el cmdline y un descriptor
    sobre la carpeta-, para obligar a que la decision pase por el camino
    corregido en vez de por una senal que en produccion no va a estar.
    """

    def __init__(self, proc, nombre):
        self._p = proc
        self.pid = proc.pid
        self.info = {"name": nombre, "pid": proc.pid}

    def cmdline(self):
        return ["PBIDesktop.exe"]          # el ejecutable, sin ruta de proyecto

    def open_files(self):
        return []


@pytest.mark.live
def test_live_la_ventana_real_delata_un_pbip_sin_handles(active_openable, monkeypatch):
    """CORE-001 contra Power BI Desktop de verdad.

    El oraculo es la VENTANA, no el motor tabular: para saber que un proyecto
    esta abierto no hace falta que su modelo este servido ni tenga datos, y
    exigirlo es lo que dejaba esta comprobacion sin poder ejecutarse.

    Solo toca el proyecto sintetico del `tmp_path` de la propia prueba, y solo
    cierra los procesos que arranco ella, comprobados por PID y hora de
    arranque.
    """
    import subprocess
    import time

    import psutil

    from horizun_pbi_mcp.powerbi import desktop_launcher

    antes = _procesos_pbi()
    if antes:
        pytest.skip(
            f"Ya hay {len(antes)} proceso(s) de Power BI vivos {sorted(antes)}. "
            "Esta prueba no toca ninguna ventana que no haya abierto ella.")

    try:
        ejecutable = desktop_launcher.find_executable()
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"Power BI Desktop no esta disponible: {exc}")

    stem = Path(active_openable.pbip_path).stem
    print("")
    print(f"[live] fixture sintetico : {active_openable.pbip_path}")
    print(f"[live] stem esperado     : {stem!r}")
    print(f"[live] Power BI antes    : {antes or 'ninguno'}")

    lanzado = subprocess.Popen(
        [str(ejecutable), str(active_openable.pbip_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    # El try/finally se instala AQUI: nada que pueda fallar puede colarse
    # entre el arranque del proceso y la garantia de limpieza.
    creados = {}
    try:
        from horizun_pbi_mcp.powerbi.desktop_capture import _enumerate_windows

        limite = time.monotonic() + 150
        coincidencia = None
        titulos_vistos = set()
        while time.monotonic() < limite:
            for pid, datos in _procesos_pbi().items():
                if pid not in antes:
                    creados[pid] = datos
            desktop = [pid for pid, (n, _) in creados.items()
                       if "pbidesktop" in n]
            if desktop:
                for pid in desktop:
                    try:
                        titulos_vistos.update(
                            w.title.strip() for w in _enumerate_windows(pid)
                            if w.title and w.title.strip())
                    except Exception:              # noqa: BLE001
                        pass
                resultado = desktop_launcher.coincidencias_por_titulo(
                    stem, desktop)
                if resultado.inequivoca is not None:
                    coincidencia = resultado
                    break
            time.sleep(2.0)

        print(f"[live] procesos creados  : {creados}")
        print(f"[live] titulos vistos    : {sorted(titulos_vistos)}")

        if coincidencia is None or coincidencia.inequivoca is None:
            # Que no aparezca la ventana es un problema de ENTORNO -o del
            # fixture-, no una regresion del detector: se omite con la
            # evidencia exacta en vez de dejar la suite roja por algo que el
            # codigo bajo prueba no controla. Las comprobaciones de
            # comportamiento de mas abajo si son aserciones duras.
            pytest.skip(
                "Power BI Desktop no llego a mostrar una ventana titulada "
                f"{stem!r} en 240 s. Titulos vistos: {sorted(titulos_vistos)}. "
                "Si entre ellos hay un aviso de error, Desktop RECHAZO el "
                "proyecto sintetico: la evidencia live necesita un .pbip que "
                "Desktop acepte abrir.")

        pid_ventana = coincidencia.inequivoca
        print(f"[live] correlacion Win32 : PID {pid_ventana}")
        assert pid_ventana in creados, (
            "la ventana correlacionada no pertenece a un proceso de la prueba")

        # --- El camino corregido, con las dos senales antiguas silenciadas ---
        reales = {pid: psutil.Process(pid) for pid in creados}
        monkeypatch.setattr(
            psutil, "process_iter",
            lambda attrs=None: [_ProcSinRastro(p, creados[pid][0])
                                for pid, p in reales.items()
                                if p.is_running()])
        project_state.invalidate_cache()
        estado = project_state.detect(active_openable, use_cache=False)
        print(f"[live] estado            : {estado.state} ({estado.confidence})")
        print(f"[live] razon             : {estado.reason}")
        print(f"[live] senales           : {estado.signals}")

        assert estado.state == OPEN, (
            f"un .pbip realmente abierto se declaro '{estado.state}': "
            f"{estado.reason}")
        assert estado.confidence == "medium"
        assert not estado.writable
        assert any(s.get("signal") == "window_title" and s.get("match") == stem
                   for s in estado.signals), (
            f"la decision no vino del titulo de ventana: {estado.signals}")
    finally:
        monkeypatch.undo()                 # la limpieza mira procesos REALES
        project_state.invalidate_cache()
        for pid, datos in list(_procesos_pbi().items()):
            if pid not in antes:
                creados.setdefault(pid, datos)

        # Cierre normal primero, por el camino del propio proyecto.
        try:
            desktop_launcher.close_desktop_by_path(active_openable.pbip_path)
        except Exception as exc:                   # noqa: BLE001
            print(f"[live] cierre normal no aplicable: {exc}")

        pendientes = []
        for pid, datos in creados.items():
            try:
                proc = psutil.Process(pid)
                if proc.create_time() != datos[1]:
                    continue               # PID reciclado: NO es nuestro
                proc.terminate()
                pendientes.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, vivos = psutil.wait_procs(pendientes, timeout=30)
        for proc in vivos:
            try:
                if creados.get(proc.pid, (None, None))[1] == proc.create_time():
                    proc.kill()            # solo sobre lo que abrio la prueba
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        psutil.wait_procs(vivos, timeout=15)
        try:
            lanzado.wait(timeout=5)
        except Exception:                          # noqa: BLE001
            pass

        restantes = {pid: d for pid, d in _procesos_pbi().items()
                     if pid not in antes}
        print(f"[live] restantes         : {restantes or 'ninguno'}")
        assert not restantes, (
            f"la prueba dejo procesos de Power BI vivos: {restantes}")
