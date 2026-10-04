"""`pbi_validate_desktop_render` no dice "datos cargados" sobre visuales en blanco.

Caso real («Comite de obra», 1-oct-2026): con `refresh=true` la tool abrio el
.pbip, refresco por XMLA y capturo DOS segundos despues. El motor tenia filas
(`data_loaded: true`), pero la ventana mostraba «(En blanco)» en las seis
tarjetas, las graficas vacias y dos avisos de Power BI. La respuesta fue
`status: success`. Los textos de abajo son los de esa captura.

Y en la misma corrida, una sesion abierta por `pbi_open_and_refresh` -del
propio servidor- se trato como ventana del usuario: no se aplico "Ajustar a
la pagina" y la captura salio cortada.

Todo con dobles: lo que expone de verdad el arbol de UI Automation de Desktop
sobre esos avisos esta pendiente de medir en una maquina con Desktop.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.powerbi import (desktop_canvas, desktop_capture,
                                     desktop_launcher, desktop_navigation,
                                     uia_helper)
from horizun_pbi_mcp.tools import dax_tools

AVISO_RELACIONES = ("Se han modificado una o varias relaciones y es necesario "
                    "actualizar manualmente.")
AVISO_TABLAS = "Algunas de las tablas tienen datos incompletos o no tienen datos."
TEXTOS_CAPTURA_EN_BLANCO = [
    "Archivo", "Inicio", AVISO_RELACIONES, AVISO_TABLAS,
    "(En blanco)", "SPI Project", "(En blanco)", "SPI recalculado",
    "(En blanco)", "Avance ganado %", "(En blanco)", "Cruces reales",
    "(En blanco)", "Cruces en ruta critica", "(En blanco)",
    "Desviacion valor %", "Cruces por paquete", "tarea", "fin_lb",
]
TEXTOS_CAPTURA_CON_DATOS = [
    "Archivo", "Inicio", "0,818", "SPI Project", "373", "Cruces reales",
    "PAQ-ARQ-MEP", "Valor modelo", "Valor presupuesto",
]


# ============================ 1) clasificacion ==============================
def test_la_captura_real_en_blanco_se_clasifica_como_no_pintada():
    r = desktop_canvas.clasificar_textos(TEXTOS_CAPTURA_EN_BLANCO)

    assert r["state"] == desktop_canvas.NO_PINTADO
    assert r["blank_values"] == 6
    assert [a["kind"] for a in r["desktop_warnings"]] == [
        "relationships_need_refresh", "tables_without_data"]


def test_los_avisos_en_ingles_tambien_cuentan():
    aviso = ("One or more relationships have been modified and need to be "
             "refreshed manually.")
    r = desktop_canvas.clasificar_textos([aviso, "(Blank)"])
    assert r["state"] == desktop_canvas.NO_PINTADO
    assert r["blank_values"] == 1


def test_la_captura_con_datos_no_tiene_senales_de_blanco():
    r = desktop_canvas.clasificar_textos(TEXTOS_CAPTURA_CON_DATOS)
    assert r["state"] == desktop_canvas.SIN_SENALES
    assert r["blank_values"] == 0 and r["desktop_warnings"] == []


def test_un_blanco_sin_aviso_se_avisa_pero_no_se_decide():
    """Una medida que devuelve BLANK() es legitima: no es 'no pintado'."""
    r = desktop_canvas.clasificar_textos(["0,818", "(En blanco)"])
    assert r["state"] == desktop_canvas.VALORES_EN_BLANCO


def test_sin_textos_no_se_afirma_nada():
    assert desktop_canvas.clasificar_textos([])["state"] == desktop_canvas.DESCONOCIDO


def test_la_clasificacion_no_devuelve_el_texto_del_informe():
    r = desktop_canvas.clasificar_textos(TEXTOS_CAPTURA_CON_DATOS)
    assert "0,818" not in repr(r) and "PAQ-ARQ-MEP" not in repr(r)


# ============================ 2) espera tras refresh ========================
def test_la_espera_termina_cuando_power_bi_deja_de_avisar():
    estados = iter([desktop_canvas.NO_PINTADO, desktop_canvas.NO_PINTADO,
                    desktop_canvas.SIN_SENALES])
    reloj = iter(range(100))

    r = desktop_canvas.esperar_lienzo(
        object(), plazo=30, leer=lambda _o: {"state": next(estados)},
        reloj=lambda: float(next(reloj)), dormir=lambda s: None)

    assert r["state"] == desktop_canvas.SIN_SENALES
    assert r["reads"] == 3 and r["wait_exhausted"] is False


def test_si_el_plazo_se_agota_con_avisos_se_devuelve_tal_cual():
    reloj = iter(range(0, 1000, 10))
    r = desktop_canvas.esperar_lienzo(
        object(), plazo=25,
        leer=lambda _o: {"state": desktop_canvas.NO_PINTADO},
        reloj=lambda: float(next(reloj)), dormir=lambda s: None)

    assert r["state"] == desktop_canvas.NO_PINTADO
    assert r["wait_exhausted"] is True


def test_un_lienzo_ilegible_no_hace_esperar():
    r = desktop_canvas.esperar_lienzo(
        object(), plazo=30,
        leer=lambda _o: {"state": desktop_canvas.DESCONOCIDO},
        dormir=lambda s: pytest.fail("no hay nada que esperar"))
    assert r["reads"] == 1


# ============================ 3) el helper =================================
class _Texto:
    def __init__(self, nombre):
        self.CurrentName = nombre


class _UiaLienzo:
    def __init__(self, textos):
        self.textos = [self._elemento(t) for t in textos]

    @staticmethod
    def _elemento(texto):
        return _Texto(texto)

    def desde_hwnd(self, hwnd):
        return "raiz"

    def todos_de_tipo(self, raiz, tipo):
        return list(self.textos) if tipo == uia_helper.UIA_TIPO_TEXT else []

    def nombre(self, elemento):
        return elemento.CurrentName


def test_el_helper_lee_la_ventana_y_solo_devuelve_la_clasificacion(monkeypatch):
    monkeypatch.setattr(uia_helper, "Uia",
                        lambda: _UiaLienzo(TEXTOS_CAPTURA_EN_BLANCO))
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Comite"})

    r = uia_helper.ACCIONES["read_canvas"]({"desktop_pid": 4321})

    assert r["ok"] is True
    assert r["canvas"]["state"] == desktop_canvas.NO_PINTADO
    assert "SPI Project" not in repr(r), "el texto del informe salio del helper"


class _TextoOculto(_Texto):
    def __init__(self, nombre, oculto):
        super().__init__(nombre)
        self.oculto = oculto


class _UiaConOcultos(_UiaLienzo):
    """Recibe pares (texto, oculto)."""

    @staticmethod
    def _elemento(par):
        texto, oculto = par
        return _TextoOculto(texto, oculto)

    def fuera_de_pantalla(self, elemento):
        return elemento.oculto


def test_un_aviso_que_ya_no_se_ve_no_cuenta(monkeypatch):
    """Medido en vivo: tras refrescar, la barra «datos incompletos» deja de
    verse pero sigue en el arbol con IsOffscreen=1. Contarla declaro en
    blanco una ventana que pintaba los datos."""
    monkeypatch.setattr(uia_helper, "Uia", lambda: _UiaConOcultos([
        (AVISO_TABLAS, True), ("0,818", False), ("SPI Project", False)]))
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Comite"})

    r = uia_helper.ACCIONES["read_canvas"]({"desktop_pid": 4321})

    assert r["canvas"]["state"] == desktop_canvas.SIN_SENALES
    assert r["canvas"]["offscreen_skipped"] == 1


def test_un_aviso_visible_si_cuenta(monkeypatch):
    monkeypatch.setattr(uia_helper, "Uia", lambda: _UiaConOcultos([
        (AVISO_TABLAS, False), ("SPI Project", False)]))
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Comite"})

    r = uia_helper.ACCIONES["read_canvas"]({"desktop_pid": 4321})
    assert r["canvas"]["state"] == desktop_canvas.NO_PINTADO


def test_la_captura_cambia_el_dpi_solo_del_hilo_y_lo_restaura(monkeypatch):
    """Medido en vivo (150 %): sin pixeles fisicos, PrintWindow dejaba en el
    bitmap solo la esquina superior izquierda de la ventana maximizada."""
    llamadas = []

    def falsa(contexto):
        llamadas.append(contexto.value)
        return 0x11 if len(llamadas) == 1 else 0x22

    import ctypes
    monkeypatch.setattr(ctypes.windll.user32, "SetThreadDpiAwarenessContext",
                        falsa, raising=False)
    monkeypatch.setattr(desktop_capture, "_capturar_en_pixeles_fisicos",
                        lambda hwnd: (2582, 1550, b""))

    assert desktop_capture._capture_window_bgra(1)[:2] == (2582, 1550)
    # (DPI_AWARENESS_CONTEXT)-4 viaja como puntero: sin signo en c_void_p.
    assert llamadas[0] == ctypes.c_void_p(desktop_capture._DPI_POR_MONITOR_V2).value
    assert llamadas[1] == 0x11, "no se restauro el contexto previo del hilo"


def test_leer_lienzo_nunca_lanza(monkeypatch):
    abierto = SimpleNamespace(desktop_pid=4321, desktop_started=1.0)
    # La fixture autouse ya hace fallar al helper: se traduce a 'unknown'.
    r = desktop_canvas.leer_lienzo(abierto)
    assert r["state"] == desktop_canvas.DESCONOCIDO and r["available"] is False


# ============================ 4) la tool ===================================
class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def dec(fn):
            self.tools[fn.__name__] = fn
            return fn
        return dec


def _opened(*, launched_by_us=True):
    return desktop_launcher.OpenedPbix(
        pbix_path=r"C:\informes\Comite.pbix",
        instance={"port": 64403, "table_count": 5}, desktop_pid=777,
        launched_by_us=launched_by_us, waited_seconds=14.0,
        desktop_started=1234.0)


@pytest.fixture
def render(monkeypatch, isolated_settings):
    from horizun_pbi_mcp.powerbi import desktop_discovery
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    mcp = _Mcp()
    dax_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened())
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda o: {"closed": True})
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda p: None)
    monkeypatch.setattr(desktop_discovery, "select_model",
                        lambda *a, **k: SimpleNamespace(to_dict=dict))
    monkeypatch.setattr(refresh_mod, "refresh_model",
                        lambda *a, **k: {"status": "ok"})
    # El motor SI tiene filas: es exactamente lo que paso en la corrida.
    monkeypatch.setattr(dax_tools, "_estado_de_datos",
                        lambda *a, **k: {"data_loaded": True})
    monkeypatch.setattr(desktop_canvas, "INTERVALO", 0.0)
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **kw: {"path": "c.png", "desktop_pid": 777,
                             "identity_settled": True, "frame_uniform": False,
                             "capture_representative": True})
    return mcp


def _lienzo_fijo(monkeypatch, textos_por_lectura):
    lecturas = iter(textos_por_lectura)
    ultimo = {"t": None}

    def _leer(pid, started, timeout, titulos=None):
        try:
            ultimo["t"] = next(lecturas)
        except StopIteration:
            pass
        return {"ok": True,
                "canvas": desktop_canvas.clasificar_textos(ultimo["t"])}

    monkeypatch.setattr(desktop_canvas, "_leer_con_helper", _leer)


def test_visuales_en_blanco_no_son_datos_cargados(render, monkeypatch):
    """La regresion de la corrida: motor con filas, ventana en blanco."""
    _lienzo_fijo(monkeypatch, [TEXTOS_CAPTURA_EN_BLANCO])

    r = render.tools["pbi_validate_desktop_render"](
        "Comite.pbix", refresh=True, confirm=True, capture_timeout=1)

    assert r["ok"] is True
    assert r["data_loaded"] is False, "dijo datos cargados con visuales en blanco"
    assert r["model_data_loaded"] is True
    assert r["canvas"]["state"] == desktop_canvas.NO_PINTADO
    assert r["canvas"]["wait_after_refresh"]["wait_exhausted"] is True
    assert r["capture"]["capture_representative"] is False
    assert any("EN BLANCO" in w and "actualizar manualmente" in w
               for w in r["warnings"])


def test_tras_el_refresh_se_espera_a_que_la_ventana_repinte(render, monkeypatch):
    _lienzo_fijo(monkeypatch, [TEXTOS_CAPTURA_EN_BLANCO,
                               TEXTOS_CAPTURA_EN_BLANCO,
                               TEXTOS_CAPTURA_CON_DATOS])

    r = render.tools["pbi_validate_desktop_render"](
        "Comite.pbix", refresh=True, confirm=True)

    assert r["data_loaded"] is True
    assert r["canvas"]["state"] == desktop_canvas.SIN_SENALES
    assert r["canvas"]["wait_after_refresh"]["reads"] == 3
    assert not any("EN BLANCO" in w for w in r.get("warnings", []))


def test_sin_lectura_del_lienzo_se_mantiene_lo_que_dice_el_motor(render):
    """La fixture autouse deja el lienzo ilegible: no se inventa un blanco."""
    r = render.tools["pbi_validate_desktop_render"]("Comite.pbix")

    assert r["data_loaded"] is True
    assert r["canvas"]["state"] == desktop_canvas.DESCONOCIDO


# ===================== 5) "Ajustar a la pagina" en sesion abierta ==========
@pytest.fixture
def sesion_abierta(render, monkeypatch):
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(launched_by_us=False))
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda p: 777)
    navegaciones = []
    monkeypatch.setattr(
        desktop_navigation, "navegar",
        lambda opened, page=None, fit_to_page=False, adapter=None:
        navegaciones.append(fit_to_page) or {
            "fit_to_page": {"verified": True}})
    return navegaciones


def test_la_ventana_que_abrio_este_servidor_se_ajusta_sin_confirm_reuse(
        render, sesion_abierta, monkeypatch):
    monkeypatch.setattr(desktop_launcher, "lanzada_por_este_servidor",
                        lambda pid: pid == 777)

    r = render.tools["pbi_validate_desktop_render"]("Comite.pbix")

    assert sesion_abierta == [True], "no se aplico 'Ajustar a la pagina'"
    assert r["navigation"]["authorized_by"] == "launched_by_this_server"


def test_refresh_con_confirm_autoriza_el_ajuste(render, sesion_abierta,
                                                monkeypatch):
    monkeypatch.setattr(desktop_launcher, "lanzada_por_este_servidor",
                        lambda pid: False)

    r = render.tools["pbi_validate_desktop_render"](
        "Comite.pbix", refresh=True, confirm=True)

    assert sesion_abierta == [True]
    assert r["navigation"]["authorized_by"] == "refresh_confirm"


def test_la_ventana_del_usuario_sigue_sin_tocarse(render, sesion_abierta,
                                                  monkeypatch):
    monkeypatch.setattr(desktop_launcher, "lanzada_por_este_servidor",
                        lambda pid: False)

    r = render.tools["pbi_validate_desktop_render"]("Comite.pbix")

    assert sesion_abierta == []
    assert any("confirm_reuse" in w for w in r["warnings"])


def test_un_pid_reciclado_no_hereda_la_autorizacion(monkeypatch):
    monkeypatch.setattr(desktop_launcher, "_LANZADAS", {(777, 1000.0)})
    monkeypatch.setattr(desktop_launcher, "_process_started",
                        lambda pid: 1000.4)
    assert desktop_launcher.lanzada_por_este_servidor(777) is True

    monkeypatch.setattr(desktop_launcher, "_process_started",
                        lambda pid: 5000.0)
    assert desktop_launcher.lanzada_por_este_servidor(777) is False
    assert desktop_launcher.lanzada_por_este_servidor(778) is False
