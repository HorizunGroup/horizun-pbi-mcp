"""La capa Win32 del cuadro de guardado, sin abrir una sola ventana.

Todo lo que `Win32UIAdapter` hace contra Windows pasa por `_user32()`, asi que
se sustituye por un doble y se puede ejercitar la LOGICA: a que ventana se
apunta, cuando se declara que no se pudo poner al frente, como se reconoce el
cuadro entre varios `#32770`, y que se resuelve cada control por la terna
clase + identificador + de quien cuelga.

Es la parte que las pruebas `live` no pueden defender: alli hay una ventana de
verdad y no se pueden provocar los casos raros -el foco que no se cede, el
cuadro que nunca aparece, el desplegable que no ofrece `.pbix`- sin romper la
sesion de quien este delante.
"""
from __future__ import annotations

import ctypes

import pytest

from horizun_pbi_mcp.powerbi import desktop_ui


# ------------------------------------------------------------- el doble ----
class _Funcion:
    """Una funcion de user32 que acepta `argtypes` y devuelve lo que se le diga."""

    def __init__(self, valor=0, efecto=None):
        self.valor = valor
        self.efecto = efecto
        self.llamadas = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.llamadas.append(args)
        if self.efecto is not None:
            return self.efecto(*args)
        return self.valor


class _User32Falso:
    """Cualquier funcion existe y devuelve 0 salvo que se configure."""

    def __init__(self, **funciones):
        self._funciones = {n: f for n, f in funciones.items()}

    def __getattr__(self, nombre):
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        if nombre not in self._funciones:
            self._funciones[nombre] = _Funcion()
        return self._funciones[nombre]


def _ventana(hwnd, pid=4321, title="Demo - Power BI Desktop",
             class_name="PBIDesktopMainWindow"):
    return desktop_ui.Ventana(hwnd=hwnd, pid=pid, title=title,
                              class_name=class_name)


@pytest.fixture
def adapter():
    return desktop_ui.Win32UIAdapter()


# ===================== 1) a que ventana se le habla =========================
@pytest.fixture
def identidad_dada(monkeypatch):
    """Da por verificada la identidad del proceso, que se prueba aparte."""
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_assert_desktop_identity",
                        lambda pid, started: None)


def test_sin_verificar_la_identidad_no_se_conduce_la_ventana(adapter):
    """Un PID reciclado nos pondria a teclear en el proceso de otra persona.

    No hay `started` que comparar, asi que no se puede afirmar que ese PID sea
    el Desktop que abrimos: se falla cerrado antes de mirar ninguna ventana.
    """
    from horizun_pbi_mcp.powerbi.desktop_capture import DesktopCaptureError

    with pytest.raises(DesktopCaptureError) as fallo:
        adapter.ventana_principal(4321, None)
    assert fallo.value.details["reason"] == "desktop_identity_unverifiable"


def test_se_elige_la_ventana_principal_y_no_una_auxiliar(adapter, monkeypatch,
                                                         identidad_dada):
    """Desktop tiene varias ventanas del mismo pid; solo una es la del documento."""
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(10, title="", class_name="IME"),
        _ventana(11, title="Demo - Power BI Desktop"),
    ])
    elegida = adapter.ventana_principal(4321, 1000.0)
    assert elegida.hwnd == 11


def test_sin_ninguna_ventana_se_dice_que_no_se_puede_conducir(adapter,
                                                              monkeypatch,
                                                              identidad_dada):
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [])
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)
    with pytest.raises(desktop_ui.DesktopUIError) as fallo:
        adapter.ventana_principal(4321, 1000.0)
    assert fallo.value.details["reason"] == "desktop_window_not_ready"


# ================ 2) el foco: pedirlo bien, o no mandar teclas ==============
def test_si_ya_esta_al_frente_no_se_toca_nada(adapter, monkeypatch):
    falso = _User32Falso()
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(adapter, "_duenio_del_primer_plano",
                        lambda: (99, 4321, 7))

    assert adapter.enfocar(_ventana(11)) is True
    assert falso.SetForegroundWindow.llamadas == [], (
        "estaba al frente; no habia nada que pedir")


def test_el_foco_se_pide_soltando_el_bloqueo_y_se_desengancha_siempre(
        adapter, monkeypatch):
    """`SetForegroundWindow` lo ignora Windows si no se hace el baile completo.

    `AttachThreadInput` tiene que deshacerse pase lo que pase: dejar dos colas
    de entrada unidas afecta al resto del escritorio, no solo a esta operacion.
    """
    estado = {"frente": 1111}
    falso = _User32Falso(
        AttachThreadInput=_Funcion(1),
        SetForegroundWindow=_Funcion(efecto=lambda h: estado.__setitem__(
            "frente", 4321) or 1))
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: _User32Falso(
        GetCurrentThreadId=_Funcion(7)))
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)
    monkeypatch.setattr(adapter, "_duenio_del_primer_plano",
                        lambda: (9, estado["frente"], 88))

    assert adapter.enfocar(_ventana(11)) is True
    assert falso.AllowSetForegroundWindow.llamadas, (
        "sin renunciar al propio bloqueo, Windows ignora la peticion")
    enganches = falso.AttachThreadInput.llamadas
    assert len(enganches) == 2 and enganches[-1][-1] is False, (
        "la cola de entrada quedo enganchada")


def test_si_no_se_consigue_el_foco_NO_se_manda_ninguna_tecla(adapter,
                                                             monkeypatch):
    """Una tecla enviada sin foco acaba en la ventana de otro programa."""
    falso = _User32Falso()
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(adapter, "enfocar", lambda v: False)
    monkeypatch.setattr(adapter, "_duenio_del_primer_plano",
                        lambda: (5, 9999, 3))
    monkeypatch.setattr(desktop_ui, "_nombre_de_proceso", lambda pid: "chrome.exe")

    with pytest.raises(desktop_ui.DesktopUIError) as fallo:
        adapter.abrir_guardar_como(_ventana(11))

    detalles = fallo.value.details
    assert detalles["reason"] == "foreground_not_owned"
    assert detalles["foreground_process"] == "chrome.exe"
    assert falso.keybd_event.llamadas == []


def test_se_manda_UN_acelerador_y_no_los_dos_seguidos(adapter, monkeypatch):
    """Dos aceleradores seguidos dejan dos cuadros de guardado, uno sobre otro."""
    falso = _User32Falso()
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(adapter, "enfocar", lambda v: True)
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    adapter.abrir_guardar_como(_ventana(11))

    pulsaciones = [c[0] for c in falso.keybd_event.llamadas]
    assert pulsaciones == [0x7B, 0x7B], f"se enviaron {pulsaciones}, no solo F12"
    banderas = [c[2] for c in falso.keybd_event.llamadas]
    assert banderas == [0, 2], "falta soltar la tecla"


def test_una_combinacion_se_pulsa_y_se_suelta_al_reves(adapter, monkeypatch):
    falso = _User32Falso()
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    adapter._enviar_acelerador(("ctrl", "shift", "s"))    # noqa: SLF001

    orden = [(c[0], c[2]) for c in falso.keybd_event.llamadas]
    assert orden == [(0x11, 0), (0x10, 0), (0x53, 0),
                     (0x53, 2), (0x10, 2), (0x11, 2)]


# ============== 3) el cuadro se reconoce por sus CONTROLES ==================
def _arbol_de_cuadro_real():
    """El del cuadro moderno, medido: casi todo con id 0 y bajo contenedores."""
    return [
        {"hwnd": 10, "class": "FloatNotifySink", "id": 0, "parent": 1},
        {"hwnd": 11, "class": "ComboBox", "id": 0, "parent": 10},
        {"hwnd": 12, "class": "Edit", "id": 0x03E9, "parent": 11},
        {"hwnd": 20, "class": "FloatNotifySink", "id": 0, "parent": 1},
        {"hwnd": 21, "class": "ComboBox", "id": 0, "parent": 20},
        {"hwnd": 30, "class": "Button", "id": 1, "parent": 1},
        # La barra de direcciones: mismo patron, otro id.
        {"hwnd": 40, "class": "ComboBox", "id": 0, "parent": 1},
        {"hwnd": 41, "class": "Edit", "id": 0xA205, "parent": 40},
    ]


def test_el_arbol_de_controles_se_lee_entero(adapter, monkeypatch):
    """`EnumChildWindows` con su callback; `GetDlgItem` no sirve en este cuadro."""
    hijos = [(101, "ComboBox", 0, 1), (102, "Edit", 0x03E9, 101)]

    def _enumerar(padre, callback, lparam):
        for handle, _clase, _id, _pa in hijos:
            callback(handle, 0)
        return 1

    def _clase(handle, buffer, tam):
        buffer.value = next(c for h, c, _i, _p in hijos if h == handle
                            for c in [c])
        return len(buffer.value)

    falso = _User32Falso(
        EnumChildWindows=_Funcion(efecto=_enumerar),
        GetClassNameW=_Funcion(efecto=_clase),
        GetDlgCtrlID=_Funcion(efecto=lambda h: next(
            i for hh, _c, i, _p in hijos if hh == h)),
        GetParent=_Funcion(efecto=lambda h: next(
            p for hh, _c, _i, p in hijos if hh == h)))
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)

    arbol = adapter._descendientes(1)                     # noqa: SLF001
    assert [c["hwnd"] for c in arbol] == [101, 102]
    assert arbol[1]["class"] == "Edit" and arbol[1]["parent"] == 101


def test_el_combo_de_tipo_es_el_que_tiene_elementos_y_no_lleva_edit(
        adapter, monkeypatch):
    """El del nombre y el de la barra de direcciones llevan un `Edit` dentro."""
    monkeypatch.setattr(adapter, "_enviar",
                        lambda hwnd, msg, wparam=0, lparam=0:
                        3 if hwnd == 21 else 0)
    assert adapter._combo_de_tipo_en(_arbol_de_cuadro_real()) == 21  # noqa: SLF001


def test_un_combo_vacio_no_se_confunde_con_el_de_tipo(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "_enviar",
                        lambda hwnd, msg, wparam=0, lparam=0: 0)
    assert adapter._combo_de_tipo_en(_arbol_de_cuadro_real()) is None  # noqa: SLF001


def test_el_boton_guardar_cuelga_del_dialogo_y_no_de_un_contenedor(adapter):
    arbol = _arbol_de_cuadro_real()
    assert adapter._boton_guardar_en(arbol, dialogo=1) == 30   # noqa: SLF001
    assert adapter._boton_guardar_en(arbol, dialogo=999) is None  # noqa: SLF001


def test_un_cuadro_sin_sus_dos_controles_no_es_el_de_guardado(adapter,
                                                              monkeypatch):
    """Un cuadro de mensaje tambien es `#32770`; no se le habla como si fuera."""
    monkeypatch.setattr(adapter, "_descendientes", lambda hwnd: [
        {"hwnd": 2, "class": "Static", "id": 0, "parent": 1},
        {"hwnd": 3, "class": "Button", "id": 1, "parent": 1},
    ])
    assert adapter._es_cuadro_de_guardado(1) is False      # noqa: SLF001


def test_el_cuadro_de_guardado_se_reconoce_entre_varios_dialogos(adapter,
                                                                 monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(50, title="Mensaje", class_name="#32770"),
        _ventana(51, title="Guardar como", class_name="#32770"),
    ])
    monkeypatch.setattr(adapter, "_es_cuadro_de_guardado",
                        lambda hwnd: hwnd == 51)
    monkeypatch.setattr(adapter, "modales", lambda pid, excluir=(): [])
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    cuadro = adapter.esperar_dialogo_guardado(4321, timeout=5)
    assert cuadro.hwnd == 51


def test_si_no_aparece_se_dice_que_se_probo_y_que_se_vio(adapter, monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(50, title="Otra cosa", class_name="#32770")])
    monkeypatch.setattr(adapter, "_es_cuadro_de_guardado", lambda hwnd: False)
    monkeypatch.setattr(adapter, "modales", lambda pid, excluir=(): [])
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    with pytest.raises(desktop_ui.DesktopUIError) as fallo:
        adapter.esperar_dialogo_guardado(4321, timeout=0.5)

    detalles = fallo.value.details
    assert detalles["reason"] == "save_dialog_not_found"
    assert "f12" in " ".join(detalles["accelerators_tried"]).casefold()
    assert detalles["dialogs_seen"][0]["hwnd"] == 50


def test_un_modal_en_vez_del_cuadro_se_dice_como_modal(adapter, monkeypatch):
    """No es un plazo agotado: hay algo en pantalla esperando una respuesta."""
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [])
    monkeypatch.setattr(adapter, "modales", lambda pid, excluir=(): [
        desktop_ui.Modal(hwnd=77, title="Credenciales", text="",
                         kind="credentials", suggested_action="responde tu")])
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    with pytest.raises(desktop_ui.DesktopModalError) as fallo:
        adapter.esperar_dialogo_guardado(4321, timeout=5)
    assert fallo.value.details["modals"][0]["kind"] == "credentials"


# ================== 4) el cierre del cuadro y los modales ===================
def test_esperar_cierre_distingue_cerrado_de_seguir_abierto(adapter,
                                                            monkeypatch):
    quedan = {"n": 3}

    def _es_ventana(hwnd):
        quedan["n"] -= 1
        return quedan["n"] > 0

    falso = _User32Falso(IsWindow=_Funcion(efecto=_es_ventana))
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    assert adapter.esperar_cierre(_ventana(22, class_name="#32770"),
                                  timeout=5) is True


def test_un_cuadro_que_no_se_cierra_se_reporta_como_tal(adapter, monkeypatch):
    falso = _User32Falso(IsWindow=_Funcion(1))
    monkeypatch.setattr(desktop_ui, "_user32", lambda: falso)
    monkeypatch.setattr(desktop_ui.time, "sleep", lambda s: None)

    assert adapter.esperar_cierre(_ventana(22, class_name="#32770"),
                                  timeout=0.4) is False


# ====== 5) el camino Win32 se NIEGA a fingir que compromete algo ===========
@pytest.mark.parametrize("paso, llamada", [
    ("elegir_tipo", lambda a, v: a.elegir_tipo(v, ".pbix")),
    ("escribir_ruta", lambda a, v: a.escribir_ruta(v, r"C:\entrega\a.pbix")),
    ("confirmar", lambda a, v: a.confirmar(v)),
])
def test_los_pasos_por_mensajes_win32_se_niegan_a_correr(adapter, paso,
                                                         llamada):
    """Se midio: `CB_SETCURSEL` no avisa a la aplicacion y `BM_CLICK` no guarda.

    Conservar ese codigo "por si acaso" fue lo que hizo que el arreglo de
    verdad tardara tres intentos en encontrarse: se leia como si funcionara.
    Ahora dice en voz alta que no sirve y a donde ir.
    """
    with pytest.raises(desktop_ui.DesktopUIError) as fallo:
        llamada(adapter, _ventana(22, class_name="#32770"))

    assert fallo.value.details["reason"] == "win32_does_not_commit"
    assert fallo.value.details["use_instead"] == "save_as_completo"


def test_leer_los_tipos_ofrecidos_si_sigue_valiendo(adapter, monkeypatch):
    """Leer no es comprometer: `CB_GETLBTEXT` devuelve lo que el cuadro ofrece.

    Es la unica de las cuatro operaciones sobre el desplegable que sigue viva,
    y el runner del baseline manual la usa para enseñar que ofrece el cuadro.
    """
    import ctypes

    ofrecidos = ["Archivo de Power BI (*.pbix)",
                 "Archivos de proyecto Power BI (*.pbip)"]

    def _mensaje(hwnd, msg, wparam=0, lparam=0):
        if msg == desktop_ui.CB_GETCOUNT:
            return len(ofrecidos)
        if msg == desktop_ui.CB_GETLBTEXTLEN:
            return len(ofrecidos[wparam])
        if msg == desktop_ui.CB_GETLBTEXT:
            texto = ctypes.create_unicode_buffer(ofrecidos[wparam])
            ctypes.memmove(lparam, texto, ctypes.sizeof(texto))
            return len(ofrecidos[wparam])
        return 0

    monkeypatch.setattr(adapter, "_descendientes",
                        lambda hwnd: _arbol_de_cuadro_real())
    monkeypatch.setattr(adapter, "_combo_de_tipo_en", lambda arbol: 21)
    monkeypatch.setattr(adapter, "_enviar", _mensaje)

    assert adapter.tipos_de_archivo(
        _ventana(22, class_name="#32770")) == ofrecidos


def test_sin_desplegable_de_tipo_no_se_acepta_el_por_defecto(adapter,
                                                             monkeypatch):
    """El tipo por defecto del cuadro es `.pbip`: aceptarlo entrega un proyecto."""
    monkeypatch.setattr(adapter, "_descendientes", lambda hwnd: [])
    monkeypatch.setattr(adapter, "_combo_de_tipo_en", lambda arbol: None)

    with pytest.raises(desktop_ui.DesktopUIError) as fallo:
        adapter.tipos_de_archivo(_ventana(22, class_name="#32770"))

    assert fallo.value.details["reason"] == "file_type_combo_missing"


# ============ 5) el guardado real delega en el proceso aparte ===============
def test_el_adaptador_real_delega_en_el_helper_y_le_acota_el_plazo(
        adapter, monkeypatch):
    """El plazo del helper NO es el de toda la operacion.

    Mover un desplegable y pulsar un boton no lleva minutos; darle el
    presupuesto entero convertia un fallo de dos segundos en un cuarto de hora
    de espera.
    """
    from horizun_pbi_mcp.powerbi import desktop_helper

    recibido = {}

    def _ejecutar(peticion, *, timeout):
        recibido["peticion"] = peticion
        recibido["timeout"] = timeout
        return {"ok": True, "dialog_closed": True}

    monkeypatch.setattr(desktop_helper, "ejecutar", _ejecutar)

    adapter.save_as_completo(pid=4321, started=1000.0, destino="C:\\x\\a.pbix",
                             timeout=900.0)

    assert recibido["peticion"]["action"] == "save_as"
    assert recibido["peticion"]["out_path"] == "C:\\x\\a.pbix"
    assert recibido["timeout"] < 900.0, (
        "el helper heredo el plazo global en vez de su propio tope")
    assert recibido["timeout"] <= desktop_ui.LIMITE_HELPER + 60.0

# ================= 6) los modales, clasificados y redactados ==============
def test_los_modales_se_leen_clasificados_y_sin_el_cuadro_de_guardado(
        adapter, monkeypatch):
    """El cuadro que se esta conduciendo no es un modal que responder."""
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(50, title="Se necesitan credenciales", class_name="#32770"),
        _ventana(51, title="Guardar como", class_name="#32770"),
        _ventana(52, title="Demo", class_name="PBIDesktopMainWindow"),
    ])
    monkeypatch.setattr(adapter, "_es_cuadro_de_guardado",
                        lambda hwnd: hwnd == 51)
    monkeypatch.setattr(adapter, "_control", lambda hwnd, cid: 900)
    monkeypatch.setattr(adapter, "_texto", lambda hwnd: "Escribe tu usuario")

    modales = adapter.modales(4321)

    assert [m.hwnd for m in modales] == [50]
    assert modales[0].kind
    assert modales[0].suggested_action


def test_un_modal_excluido_no_se_reporta(adapter, monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_capture

    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(50, title="Aviso", class_name="#32770")])
    monkeypatch.setattr(adapter, "_es_cuadro_de_guardado", lambda hwnd: False)
    monkeypatch.setattr(adapter, "_control", lambda hwnd, cid: None)

    assert adapter.modales(4321, excluir=[50]) == []


def test_el_texto_de_un_modal_no_sale_con_rutas_personales(adapter,
                                                           monkeypatch):
    import os

    from horizun_pbi_mcp.powerbi import desktop_capture

    casa = os.path.expanduser("~")
    monkeypatch.setattr(desktop_capture, "_enumerate_windows", lambda pid: [
        _ventana(50, title="Error", class_name="#32770")])
    monkeypatch.setattr(adapter, "_es_cuadro_de_guardado", lambda hwnd: False)
    monkeypatch.setattr(adapter, "_control", lambda hwnd, cid: 900)
    monkeypatch.setattr(adapter, "_texto",
                        lambda hwnd: "No se pudo escribir en "
                                     + os.path.join(casa, "x.pbix"))

    modal = adapter.modales(4321)[0]
    assert casa not in modal.text
