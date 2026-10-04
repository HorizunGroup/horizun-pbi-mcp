"""Detalles que salieron al verificar en vivo el «Comite de obra».

1. "Ajustar a la pagina" se daba por verificado con un anuncio que repetia el
   MISMO nivel: «Informe ampliado a 100 %. 10 resultados» pasaba a «... 100 %.
   No se ha encontrado ningun resultado». Cambio el texto, no el zoom.
2. Un spec con `{"name": "comite", "displayName": "Comite de obra"}` creaba la
   pestaña «comite»: `displayName` se ignoraba en silencio.
3. `pbi_create_page_from_spec` no pasaba `options` de cada visual a la
   fabrica: se aceptaban y se perdian.
"""
from __future__ import annotations

import json
from pathlib import Path

from horizun_pbi_mcp.pbip import page_builder, project_locator
from horizun_pbi_mcp.powerbi import uia_helper
from horizun_pbi_mcp.services import page_spec
from horizun_pbi_mcp.tools.page_tools import normalizar_spec
from tests.test_navegacion_en_sesion_abierta import _UiaConCinta, _montar


# ======================================= 1) el zoom se juzga por NIVEL =====
class _CintaConAnuncios(_UiaConCinta):
    """Una cinta cuyo boton no expone estado: solo quedan los anuncios."""

    def __init__(self, antes, tras_pulsar):
        super().__init__()
        self.textos = list(antes)
        self.tras_pulsar = tras_pulsar

    def todos_de_tipo(self, raiz, tipo):
        if tipo == uia_helper.UIA_TIPO_TEXT:
            return list(self.textos)
        return super().todos_de_tipo(raiz, tipo)

    def nombre(self, e):
        return e if isinstance(e, str) else super().nombre(e)

    def invocar(self, elemento):
        if elemento is self.ajustar:
            self.textos = list(self.tras_pulsar)
        return "invoke"

    def estado_toggle(self, elemento):
        return None


def test_un_anuncio_con_el_mismo_nivel_no_verifica_el_zoom(monkeypatch):
    """Los textos de la medicion en vivo, tal cual."""
    _montar(monkeypatch, _CintaConAnuncios(
        ["Informe ampliado a 100 %. 10\xa0resultados"],
        ["Informe ampliado a 100 %. 10\xa0resultados",
         "Informe ampliado a 100 %. No se ha encontrado ningún resultado."]))

    salida = uia_helper.ajustar_a_pagina({"desktop_pid": 4321})

    assert salida["zoom_level_changed"] is False
    assert salida["verified"] is False


def test_un_anuncio_con_otro_nivel_si_lo_verifica(monkeypatch):
    _montar(monkeypatch, _CintaConAnuncios(
        ["Informe ampliado a 100 %. 10 resultados"],
        ["Informe ampliado a 100 %. 10 resultados", "Informe ampliado a 72 %"]))

    salida = uia_helper.ajustar_a_pagina({"desktop_pid": 4321})

    assert salida["zoom_level_changed"] is True
    assert salida["zoom_announcements_new"] == ["Informe ampliado a 72 %"]


# ================================ 2) displayName es el nombre de la pestaña =
def test_displayName_manda_sobre_name():
    assert page_spec.nombre_de_pestana(
        {"name": "comite", "displayName": "Comite de obra"}) == "Comite de obra"
    assert page_spec.nombre_de_pestana(
        {"name": "comite", "display_name": "Comite"}) == "Comite"
    assert page_spec.nombre_de_pestana({"name": "comite"}) == "comite"


def test_el_dialecto_nuevo_conserva_displayName_al_traducirse():
    traducido = normalizar_spec({"schema_version": "1.0",
                                 "page": {"name": "comite",
                                          "displayName": "Comite de obra"},
                                 "visuals": []})
    assert traducido["page_name"] == "Comite de obra"


def test_basta_con_displayName_para_validar():
    errores = page_spec.validate_schema({
        "schema_version": "1.0", "page": {"displayName": "Comite de obra"},
        "visuals": [{"type": "card", "fields": {"values": ["[TotalAmount]"]}}]})
    assert not [e for e in errores if e["path"] == "$.page.name"]


# ============ 3) pbi_create_page_from_spec entrega options a la fabrica =====
def test_la_pagina_lleva_su_displayName_y_las_opciones_llegan(session,
                                                             sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    spec = normalizar_spec({
        "schema_version": "1.0",
        "page": {"name": "comite", "displayName": "Comite de obra"},
        "visuals": [{"type": "card", "title": "Total",
                     "position": {"x": 16, "y": 16, "width": 300, "height": 130},
                     "fields": {"values": ["[TotalAmount]"]},
                     "options": {"background_color": "#112233"}}]})

    r = page_builder.create_page_from_spec(active, spec)

    pagina = Path(active.report_dir) / "definition" / "pages" / r["page_id"]
    meta = json.loads((pagina / "page.json").read_text(encoding="utf-8"))
    assert meta["displayName"] == "Comite de obra"
    visual = json.loads(next(pagina.glob("visuals/*/visual.json")).read_text(
        encoding="utf-8"))
    fondo = visual["visual"]["visualContainerObjects"]["background"][0]
    assert fondo["properties"]["color"]["solid"]["color"]["expr"]["Literal"][
        "Value"] == "'#112233'", "options se perdio por el camino"


# ======== 4) una pagina sin pintar no pasa por una captura con datos =======
# Medido en vivo: una captura tomada antes de que Desktop pintara la pagina
# salio como un lienzo vacio, sin avisos ni «(En blanco)», y la tool dijo
# data_loaded=true. Los titulos de los visuales son el testigo: Desktop los
# expone en UI Automation como grupos con ese nombre cuando estan en pantalla.
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from horizun_pbi_mcp.powerbi import (desktop_canvas, desktop_capture,  # noqa: E402
                                     desktop_discovery, desktop_launcher,
                                     desktop_navigation)
from horizun_pbi_mcp.powerbi import refresh as refresh_mod  # noqa: E402
from horizun_pbi_mcp.tools import dax_tools  # noqa: E402


def test_sin_los_titulos_en_pantalla_la_pagina_no_esta_pintada():
    r = desktop_canvas.clasificar_textos(["Archivo", "Inicio"],
                                         titulos_esperados=2, titulos_vistos=0)
    assert r["state"] == desktop_canvas.SIN_VISUALES
    r = desktop_canvas.clasificar_textos(["Archivo", "Total"],
                                         titulos_esperados=2, titulos_vistos=2)
    assert r["state"] == desktop_canvas.SIN_SENALES


def test_un_aviso_de_power_bi_manda_sobre_los_titulos():
    r = desktop_canvas.clasificar_textos(
        ["Algunas de las tablas tienen datos incompletos o no tienen datos."],
        titulos_esperados=2, titulos_vistos=0)
    assert r["state"] == desktop_canvas.NO_PINTADO


class _Grupo:
    def __init__(self, nombre, oculto=False):
        self.CurrentName = nombre
        self.oculto = oculto


class _UiaTitulos:
    def __init__(self, grupos):
        self.grupos = grupos

    def desde_hwnd(self, hwnd):
        return "raiz"

    def todos_de_tipo(self, raiz, tipo):
        return list(self.grupos) if tipo == 50026 else []

    def nombre(self, e):
        return e.CurrentName

    def fuera_de_pantalla(self, e):
        return e.oculto


def test_el_helper_cuenta_solo_titulos_visibles(monkeypatch):
    monkeypatch.setattr(uia_helper, "Uia", lambda: _UiaTitulos(
        [_Grupo("Cruces detectados"), _Grupo("Valor ganado", oculto=True)]))
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "x"})

    r = uia_helper.ACCIONES["read_canvas"]({
        "desktop_pid": 1, "expected_titles": ["cruces detectados",
                                              "Valor ganado"]})

    assert r["canvas"]["visual_titles_seen"] == 1
    assert r["canvas"]["state"] == desktop_canvas.SIN_VISUALES


def test_los_titulos_se_leen_del_visual_json(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    spec = normalizar_spec({"schema_version": "1.0", "page": {"name": "t"},
                            "visuals": [
                                {"type": "card", "title": "Con 'comilla'",
                                 "position": {"x": 0, "y": 0, "width": 200,
                                              "height": 120},
                                 "fields": {"values": ["[TotalAmount]"]}},
                                {"type": "card",
                                 "position": {"x": 220, "y": 0, "width": 200,
                                              "height": 120},
                                 "fields": {"values": ["[TotalAmount]"]}}]})
    r = page_builder.create_page_from_spec(active, spec)

    titulos = desktop_navigation.titulos_de_pagina(active.pbip_path,
                                                   r["page_id"])
    assert titulos == ["Con 'comilla'"]
    assert desktop_navigation.titulos_de_pagina(active.pbip_path, None) is None


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def dec(fn):
            self.tools[fn.__name__] = fn
            return fn
        return dec


@pytest.fixture
def render(monkeypatch, isolated_settings):
    mcp = _Mcp()
    dax_tools.register(mcp)
    abierto = desktop_launcher.OpenedPbix(
        pbix_path=r"C:\informes\Comite.pbix", instance={"port": 1},
        desktop_pid=777, launched_by_us=True, waited_seconds=1.0,
        desktop_started=1.0)
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: abierto)
    monkeypatch.setattr(desktop_launcher, "close", lambda o: {"closed": True})
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda p: None)
    monkeypatch.setattr(desktop_discovery, "select_model",
                        lambda *a, **k: SimpleNamespace(to_dict=dict))
    monkeypatch.setattr(refresh_mod, "refresh_model",
                        lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(dax_tools, "_estado_de_datos",
                        lambda *a, **k: {"data_loaded": True})
    monkeypatch.setattr(desktop_canvas, "INTERVALO", 0.0)
    monkeypatch.setattr(desktop_navigation, "titulos_de_pagina",
                        lambda doc, page: ["Cruces", "Valor"])
    monkeypatch.setattr(
        desktop_capture, "capture_opened",
        lambda value, **kw: {"path": "c.png", "identity_settled": True,
                             "frame_uniform": False,
                             "capture_representative": True})
    return mcp


def _lecturas(monkeypatch, vistos):
    serie = iter(vistos)
    ultimo = {"n": 0}

    def _leer(pid, started, timeout, titulos=None):
        try:
            ultimo["n"] = next(serie)
        except StopIteration:
            pass
        return {"ok": True, "canvas": desktop_canvas.clasificar_textos(
            ["Archivo"], titulos_esperados=len(titulos or []),
            titulos_vistos=ultimo["n"])}

    monkeypatch.setattr(desktop_canvas, "_leer_con_helper", _leer)


def test_se_espera_a_que_la_pagina_se_pinte(render, monkeypatch):
    _lecturas(monkeypatch, [0, 1, 2])

    r = render.tools["pbi_validate_desktop_render"]("Comite.pbix")

    assert r["data_loaded"] is True
    assert r["canvas"]["state"] == desktop_canvas.SIN_SENALES
    assert r["canvas"]["wait_after_refresh"]["reads"] == 3


def test_una_pagina_que_no_se_pinta_no_es_una_captura_con_datos(render,
                                                                 monkeypatch):
    _lecturas(monkeypatch, [0])

    r = render.tools["pbi_validate_desktop_render"]("Comite.pbix",
                                                    capture_timeout=1)

    assert r["data_loaded"] is False and r["model_data_loaded"] is True
    assert r["canvas"]["state"] == desktop_canvas.SIN_VISUALES
    assert r["capture"]["capture_representative"] is False
    assert any("aun no pinto la pagina" in w for w in r["warnings"])
