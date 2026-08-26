"""Las ventanas y la secuencia del helper, sin abrir ninguna.

Complementa `test_helper_sin_com.py`: alli se prueba que decide cada paso,
aqui como encuentra sobre que actuar -que ventana es la principal, cual de
varios `#32770` es el cuadro de guardado, que es un modal y que es otro
guardado- y que la secuencia entera deja evidencia de cada fase.

`ventanas_de` y compania hablan con `user32` a traves de un callback, asi que
se sustituye `_user32()` por un doble y se enumeran ventanas de mentira con la
misma forma que las de Windows.
"""
from __future__ import annotations

import ctypes

import pytest

from horizun_pbi_mcp.powerbi import uia_helper
from tests.test_helper_sin_com import _UiaFalso


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)


class _Fn:
    def __init__(self, valor=0, efecto=None):
        self.valor, self.efecto = valor, efecto
        self.llamadas = []
        self.argtypes = self.restype = None

    def __call__(self, *a):
        self.llamadas.append(a)
        return self.efecto(*a) if self.efecto else self.valor


class _U32:
    def __init__(self, **funciones):
        self._f = dict(funciones)

    def __getattr__(self, nombre):
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        return self._f.setdefault(nombre, _Fn())


def _monta_ventanas(monkeypatch, ventanas, pid_de):
    def _enumerar(callback, lparam):
        for hwnd in ventanas:
            callback(hwnd, 0)
        return 1

    def _duenio(hwnd, puntero):
        puntero._obj.value = pid_de(hwnd)
        return 1

    def _clase(hwnd, buf, tam):
        buf.value = ventanas[hwnd]["class"]
        return len(buf.value)

    def _titulo(hwnd, buf, tam):
        buf.value = ventanas[hwnd]["title"]
        return len(buf.value)

    falso = _U32(EnumWindows=_Fn(efecto=_enumerar),
                 GetWindowThreadProcessId=_Fn(efecto=_duenio),
                 GetClassNameW=_Fn(efecto=_clase),
                 GetWindowTextW=_Fn(efecto=_titulo),
                 IsWindowVisible=_Fn(efecto=lambda h: ventanas[h]["visible"]))
    monkeypatch.setattr(uia_helper, "_user32", lambda: falso)
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    return falso


# ================= 1) que ventanas se ven, y de quien son ==================
def test_solo_se_ven_las_visibles_de_ESE_proceso(monkeypatch):
    ventanas = {
        11: {"class": "PBIDesktop", "title": "Demo", "visible": 1},
        12: {"class": "PBIDesktop", "title": "De otro", "visible": 1},
        13: {"class": "PBIDesktop", "title": "Oculta", "visible": 0},
    }
    _monta_ventanas(monkeypatch, ventanas,
                    pid_de=lambda h: 4321 if h in (11, 13) else 9999)

    vistas = uia_helper.ventanas_de(4321)

    assert [v["hwnd"] for v in vistas] == [11], (
        "se colo una ventana de otro proceso o una que no se ve")
    assert vistas[0]["title"] == "Demo"


def test_dos_ventanas_principales_no_se_desempatan_a_dedo(monkeypatch):
    """Con dos documentos abiertos no se adivina en cual teclear."""
    ventanas = {
        11: {"class": "PBIDesktop", "title": "Uno", "visible": 1},
        12: {"class": "PBIDesktop", "title": "Dos", "visible": 1},
    }
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._ventana_principal(4321)               # noqa: SLF001

    assert len(fallo.value.detalles["windows"]) == 2


def test_un_dialogo_no_cuenta_como_ventana_principal(monkeypatch):
    ventanas = {
        11: {"class": "PBIDesktop", "title": "Demo", "visible": 1},
        22: {"class": "#32770", "title": "Guardar como", "visible": 1},
    }
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    assert uia_helper._ventana_principal(4321)["hwnd"] == 11   # noqa: SLF001


def test_sin_ventana_con_titulo_no_se_sigue(monkeypatch):
    ventanas = {11: {"class": "PBIDesktop", "title": "   ", "visible": 1}}
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._ventana_principal(4321)               # noqa: SLF001
    assert fallo.value.detalles["windows"] == []


# ============ 2) el cuadro se reconoce por sus DOS controles ===============
def test_el_cuadro_de_guardado_necesita_nombre_Y_tipo(monkeypatch):
    """Un cuadro de mensaje tambien es `#32770`; no se le habla como si fuera."""
    ventanas = {
        50: {"class": "#32770", "title": "Aviso", "visible": 1},
        51: {"class": "#32770", "title": "Guardar como", "visible": 1},
        52: {"class": "PBIDesktop", "title": "Demo", "visible": 1},
    }
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    class _SoloEl51(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            if raiz != "elemento-51":
                return None
            return super().por_id(raiz, automation_id, tipo)

    hallado = uia_helper._esperar_cuadro(_SoloEl51(), 4321, 5)  # noqa: SLF001
    assert hallado["hwnd"] == 51


def test_si_el_cuadro_no_aparece_se_dice_el_plazo(monkeypatch):
    _monta_ventanas(monkeypatch, {}, pid_de=lambda h: 4321)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._esperar_cuadro(_UiaFalso(), 4321, 0.5)  # noqa: SLF001

    assert fallo.value.fase == "abrir_cuadro"
    assert fallo.value.detalles["timeout"] == 0.5


# =================== 3) modal no es "otro cuadro de guardado" =============
def test_un_segundo_guardado_no_se_reporta_como_modal(monkeypatch):
    """Si tiene desplegable de tipo es otro guardado, no algo que responder."""
    ventanas = {
        60: {"class": "#32770", "title": "Credenciales", "visible": 1},
        61: {"class": "#32770", "title": "Guardar como", "visible": 1},
        62: {"class": "PBIDesktop", "title": "Demo", "visible": 1},
    }
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    class _TipoSoloEnEl61(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            return "combo" if raiz == "elemento-61" else None

    modales = uia_helper._modales(_TipoSoloEnEl61(), 4321, [])  # noqa: SLF001
    assert [m["hwnd"] for m in modales] == [60]


def test_el_cuadro_que_se_esta_conduciendo_se_excluye(monkeypatch):
    ventanas = {60: {"class": "#32770", "title": "Guardar", "visible": 1}}
    _monta_ventanas(monkeypatch, ventanas, pid_de=lambda h: 4321)

    class _SinTipo(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            return None

    assert uia_helper._modales(_SinTipo(), 4321, [60]) == []   # noqa: SLF001


# ===================== 4) el cierre, con reloj de mentira =================
def test_esperar_cierre_devuelve_cuando_la_ventana_muere(monkeypatch):
    quedan = {"n": 2}

    def _es_ventana(h):
        quedan["n"] -= 1
        return quedan["n"] > 0

    monkeypatch.setattr(uia_helper, "_user32",
                        lambda: _U32(IsWindow=_Fn(efecto=_es_ventana)))
    assert uia_helper._esperar_cierre(22, 5) is True       # noqa: SLF001


def test_un_cuadro_que_no_se_cierra_se_reporta_sin_adornos(monkeypatch):
    monkeypatch.setattr(uia_helper, "_user32", lambda: _U32(IsWindow=_Fn(1)))
    assert uia_helper._esperar_cierre(22, 0.4) is False    # noqa: SLF001


# =============== 5) la secuencia entera, con el cuadro simulado ===========
def _secuencia_montada(monkeypatch, uia):
    monkeypatch.setattr(uia_helper, "Uia", lambda: uia)
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid, "create_time": 1.0})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Demo"})
    monkeypatch.setattr(uia_helper, "_enviar_teclas", lambda e: None)
    monkeypatch.setattr(uia_helper, "_esperar_cuadro",
                        lambda u, pid, plazo: {"hwnd": 22})
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real", lambda t: None)
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: False)
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: True)
    monkeypatch.setattr(uia_helper, "_modales", lambda u, pid, ex: [])


def test_la_secuencia_completa_deja_evidencia_de_cada_fase(monkeypatch):
    """`steps` es la evidencia: sin ella un fallo no se puede situar."""
    ruta = "C:\\entrega\\a.pbix"
    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)",
                    estado_tras=uia_helper.ESTADO_CERRADO, valor_nombre=ruta)
    _secuencia_montada(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "traer_al_frente", lambda h, p: True)

    salida = uia_helper.guardar_como({
        "desktop_pid": 4321, "desktop_started": 1.0, "out_path": ruta})

    assert salida["ok"] is True
    assert salida["commit_method"] == "invoke"
    assert salida["dialog_closed"] is True
    assert salida["filename_verified"] is True
    assert [p["phase"] for p in salida["steps"]] == [
        "identidad", "ventana", "abrir_cuadro", "cuadro", "tipo", "nombre",
        "guardar", "cierre"]


def test_si_no_se_puede_poner_desktop_al_frente_no_se_pulsa_F12(monkeypatch):
    """Una tecla enviada sin foco acaba en la ventana de otro programa."""
    _secuencia_montada(monkeypatch, _UiaFalso())
    monkeypatch.setattr(uia_helper, "traer_al_frente", lambda h, p: False)
    teclas = []
    monkeypatch.setattr(uia_helper, "_enviar_teclas",
                        lambda e: teclas.append(e))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321,
                                 "out_path": "C:\\x\\a.pbix"})

    assert fallo.value.fase == "abrir_cuadro"
    assert teclas == [], "se enviaron teclas sin tener el foco"


def test_un_modal_al_cerrar_viaja_en_la_respuesta(monkeypatch):
    """El servicio decide que hacer con el; el helper no lo cierra solo."""
    ruta = "C:\\entrega\\a.pbix"
    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)",
                    estado_tras=uia_helper.ESTADO_CERRADO, valor_nombre=ruta)
    _secuencia_montada(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "traer_al_frente", lambda h, p: True)
    monkeypatch.setattr(uia_helper, "_modales", lambda u, pid, ex: [
        {"hwnd": 99, "title": "Ya existe"}])

    salida = uia_helper.guardar_como({
        "desktop_pid": 4321, "desktop_started": 1.0, "out_path": ruta})

    assert salida["modals"] == [{"hwnd": 99, "title": "Ya existe"}]

# ============== 6) el primer plano: pedirlo bien y COMPROBARLO ============
def test_el_frente_se_pide_soltando_el_bloqueo_y_se_desengancha(monkeypatch):
    """`SetForegroundWindow` a secas lo ignora Windows, y sin hacer ruido.

    `AttachThreadInput` tiene que deshacerse pase lo que pase: dejar dos colas
    de entrada unidas afecta al escritorio entero, no solo a esta operacion.
    """
    estado = {"pid_al_frente": 9999}

    def _duenio(hwnd, puntero):
        puntero._obj.value = estado["pid_al_frente"]
        return 88                                   # id del hilo del frente

    def _al_frente(hwnd):
        estado["pid_al_frente"] = 4321
        return 1

    falso = _U32(GetForegroundWindow=_Fn(555),
                 GetWindowThreadProcessId=_Fn(efecto=_duenio),
                 AttachThreadInput=_Fn(1),
                 SetForegroundWindow=_Fn(efecto=_al_frente))
    monkeypatch.setattr(uia_helper, "_user32", lambda: falso)
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda *a, **k: _U32(GetCurrentThreadId=_Fn(7)))

    assert uia_helper.traer_al_frente(11, 4321) is True
    assert falso.AllowSetForegroundWindow.llamadas, (
        "sin renunciar al propio bloqueo, Windows ignora la peticion")
    enganches = falso.AttachThreadInput.llamadas
    assert len(enganches) == 2 and enganches[-1][-1] is False, (
        "la cola de entrada quedo enganchada a otro hilo")


def test_si_ya_esta_al_frente_no_se_toca_el_escritorio(monkeypatch):
    def _duenio(hwnd, puntero):
        puntero._obj.value = 4321
        return 88

    falso = _U32(GetForegroundWindow=_Fn(555),
                 GetWindowThreadProcessId=_Fn(efecto=_duenio))
    monkeypatch.setattr(uia_helper, "_user32", lambda: falso)
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda *a, **k: _U32(GetCurrentThreadId=_Fn(7)))

    assert uia_helper.traer_al_frente(11, 4321) is True
    assert falso.SetForegroundWindow.llamadas == []


def test_si_el_frente_no_se_cede_se_devuelve_False_sin_fingir(monkeypatch):
    """Pasa de verdad: sesion bloqueada, o RDP desconectado."""
    def _duenio(hwnd, puntero):
        puntero._obj.value = 9999
        return 88

    falso = _U32(GetForegroundWindow=_Fn(555),
                 GetWindowThreadProcessId=_Fn(efecto=_duenio),
                 AttachThreadInput=_Fn(0))
    monkeypatch.setattr(uia_helper, "_user32", lambda: falso)
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    monkeypatch.setattr(ctypes, "WinDLL",
                        lambda *a, **k: _U32(GetCurrentThreadId=_Fn(7)))

    assert uia_helper.traer_al_frente(11, 4321) is False
