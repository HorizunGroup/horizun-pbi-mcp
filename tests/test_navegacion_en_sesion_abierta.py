"""Elegir pagina y zoom en una ventana ABIERTA, sin tocar `pages.json`.

Fotografiar una pagina concreta exigia cerrar, escribir `activePageName`,
reabrir y refrescar: cuarenta segundos por vuelta. Ahora, con la sesion
abierta, la pestaña y "Ajustar a la pagina" se eligen en la propia interfaz
y se VERIFICAN. Lo que no se pudo demostrar se declara: la captura de una
pagina no demostrada es un error, no una foto de otra pagina.

Todo con dobles. Lo que hace Power BI Desktop de verdad con estas acciones
esta pendiente de validar en una maquina con Desktop.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import (desktop_capture, desktop_helper,
                                     desktop_launcher, desktop_navigation,
                                     uia_helper)
from horizun_pbi_mcp.powerbi.errors import ValidationError
from tests.test_helper_sin_com import _UiaFalso


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def dec(fn):
            self.tools[fn.__name__] = fn
            return fn
        return dec


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    monkeypatch.setattr(uia_helper, "ESPERA_INTERFAZ", 0.05)


# ============================ 1) el helper: pestañas =======================
class _Pestana:
    def __init__(self, nombre, seleccionable=True):
        self.CurrentName = nombre
        self.seleccionable = seleccionable
        self.seleccionada = False


class _UiaConPestanas(_UiaFalso):
    def __init__(self, nombres, *, expone_estado=True):
        super().__init__()
        self.pestanas = [_Pestana(n) for n in nombres]
        self.expone_estado = expone_estado
        self.selecciones = []

    def todos_de_tipo(self, raiz, tipo):
        return list(self.pestanas) if tipo == uia_helper.UIA_TIPO_TABITEM else []

    def nombre(self, elemento):
        return elemento.CurrentName

    def seleccionar(self, elemento):
        self.selecciones.append(elemento.CurrentName)
        for p in self.pestanas:
            p.seleccionada = p is elemento
        return "selection_item"

    def esta_seleccionado(self, elemento):
        return elemento.seleccionada if self.expone_estado else None


def _montar(monkeypatch, uia):
    monkeypatch.setattr(uia_helper, "Uia", lambda: uia)
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid, "create_time": 1.0})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Demo"})


def test_la_pestana_se_selecciona_y_se_verifica(monkeypatch):
    uia = _UiaConPestanas(["Portada", "Detalle"])
    _montar(monkeypatch, uia)

    salida = uia_helper.seleccionar_pagina({"desktop_pid": 4321,
                                            "page_name": "detalle"})

    assert salida["ok"] is True and salida["verified"] is True
    assert salida["via"] == "selection_item"
    assert uia.selecciones == ["Detalle"]
    assert salida["tabs_seen"] == ["Portada", "Detalle"]


def test_sin_estado_de_seleccion_no_se_afirma_la_pagina(monkeypatch):
    uia = _UiaConPestanas(["Portada"], expone_estado=False)
    _montar(monkeypatch, uia)

    salida = uia_helper.seleccionar_pagina({"desktop_pid": 4321,
                                            "page_name": "Portada"})

    assert salida["ok"] is True
    assert salida["verified"] is False
    assert "IsSelected" in salida["verification_reason"]


def test_una_pestana_inexistente_se_dice_con_las_que_hay(monkeypatch):
    _montar(monkeypatch, _UiaConPestanas(["Portada"]))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.seleccionar_pagina({"desktop_pid": 4321,
                                       "page_name": "Nada"})

    assert fallo.value.detalles["reason"] == "page_tab_not_found"
    assert fallo.value.detalles["tabs_seen"] == ["Portada"]
    assert fallo.value.detalles["attempts_total"] == 3


# ============================= 2) el helper: zoom ==========================
class _Control:
    def __init__(self, nombre, toggle=0):
        self.CurrentName = nombre
        self.toggle = toggle


class _UiaConCinta(_UiaFalso):
    def __init__(self, *, directo=True):
        super().__init__()
        self.ajustar = _Control("Ajustar a la página")
        self.vista = _Control("Vista")
        self.menu = _Control("Vista de página")
        self.directo = directo
        self.menu_abierto = False

    def nombre(self, elemento):
        return elemento.CurrentName

    def por_nombre(self, raiz, patron, tipos=None):
        candidatos = [self.vista, self.menu]
        if self.directo or self.menu_abierto:
            candidatos.append(self.ajustar)
        return [c for c in candidatos if patron.search(c.CurrentName)]

    def seleccionar(self, elemento):
        return "selection_item"

    def invocar(self, elemento):
        if elemento is self.menu:
            self.menu_abierto = True
        if elemento is self.ajustar:
            self.ajustar.toggle = 1
        return "invoke"

    def estado_toggle(self, elemento):
        return elemento.toggle if elemento is self.ajustar else None

    def esta_seleccionado(self, elemento):
        return None


def test_ajustar_a_pagina_se_activa_y_se_verifica_por_toggle(monkeypatch):
    uia = _UiaConCinta()
    _montar(monkeypatch, uia)

    salida = uia_helper.ajustar_a_pagina({"desktop_pid": 4321})

    assert salida["verified"] is True and salida["state_after"] is True
    assert salida["path"] == ["fit_to_page"]


def test_si_no_esta_a_la_vista_se_abre_la_cinta_de_vista(monkeypatch):
    uia = _UiaConCinta(directo=False)
    _montar(monkeypatch, uia)

    salida = uia_helper.ajustar_a_pagina({"desktop_pid": 4321})

    assert salida["verified"] is True
    assert salida["path"] == ["view_tab", "page_view_menu", "fit_to_page"]


def test_sin_control_de_zoom_se_dice(monkeypatch):
    class _Nada(_UiaFalso):
        def por_nombre(self, raiz, patron, tipos=None):
            return []

    _montar(monkeypatch, _Nada())
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.ajustar_a_pagina({"desktop_pid": 4321})
    assert fallo.value.detalles["reason"] == "fit_to_page_control_not_found"


# ======================= 3) resolver la pagina del .pbip ===================
@pytest.fixture
def proyecto(session, tmp_path, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.pbip import project_locator
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    monkeypatch.setattr(cfg, "_session", session)
    return pbip


def test_el_id_de_pagina_se_traduce_al_nombre_visible(proyecto):
    r = desktop_navigation.nombre_visible_de_pagina(proyecto, "page01")
    assert r["display_name"] == "Pagina Uno"
    assert r["resolved_from"] == "page_id" and r["page_id"] == "page01"

    r = desktop_navigation.nombre_visible_de_pagina(proyecto, "pagina uno")
    assert r["page_id"] == "page01" and r["resolved_from"] == "display_name"


def test_una_pagina_inexistente_se_rechaza(proyecto):
    with pytest.raises(ValidationError):
        desktop_navigation.nombre_visible_de_pagina(proyecto, "Nada")


def test_en_un_pbix_el_nombre_se_usa_tal_cual(tmp_path):
    r = desktop_navigation.nombre_visible_de_pagina(tmp_path / "x.pbix", "Portada")
    assert r["display_name"] == "Portada" and r["resolved_from"] is None


# ========================= 4) el servicio de navegacion ====================
class _AdaptadorNav:
    def __init__(self, *, verificada=True, falla=False):
        self.verificada = verificada
        self.falla = falla
        self.llamadas = []

    def seleccionar_pagina(self, *, pid, started, page_name, timeout=30.0):
        self.llamadas.append(("page", page_name))
        if self.falla:
            raise desktop_helper.DesktopHelperError(
                "no hay pestañas", details={"reason": "page_tab_not_found"})
        return {"verified": self.verificada, "via": "selection_item",
                "selection_state": self.verificada,
                "verification_reason": None if self.verificada else "x",
                "tabs_seen": ["Pagina Uno"]}

    def ajustar_a_pagina(self, *, pid, started, timeout=30.0):
        self.llamadas.append(("fit", None))
        return {"verified": self.verificada, "via": "invoke",
                "path": ["fit_to_page"], "state_after": self.verificada,
                "verification_reason": None if self.verificada else "y"}


def _opened(pbip, *, launched=False):
    return desktop_launcher.OpenedPbix(
        pbix_path=str(pbip), instance={"port": 5, "connection_string": "x",
                                       "catalog": "Demo"},
        desktop_pid=4321, launched_by_us=launched, waited_seconds=0.0,
        desktop_started=1000.0)


def test_navegar_traduce_la_pagina_y_cuenta_sus_visuales(proyecto):
    adapter = _AdaptadorNav()
    r = desktop_navigation.navegar(_opened(proyecto), page="page01",
                                   fit_to_page=True, adapter=adapter)

    assert adapter.llamadas == [("page", "Pagina Uno"), ("fit", None)]
    assert r["page"]["verified"] is True
    assert isinstance(r["page"]["visual_count"], int)
    assert r["fit_to_page"]["verified"] is True
    assert r["pages_json_touched"] is False


def test_un_helper_que_falla_no_tumba_la_navegacion_sino_que_la_declara(proyecto):
    r = desktop_navigation.navegar(_opened(proyecto), page="page01",
                                   adapter=_AdaptadorNav(falla=True))
    assert r["page"]["verified"] is False
    assert r["page"]["error"] == "desktop_helper_failed"


# ============================ 5) las tools ===============================
def _render_montado(monkeypatch, proyecto):
    from horizun_pbi_mcp.tools import dax_tools

    mcp = _Mcp()
    dax_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda p: 4321)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(proyecto))
    monkeypatch.setattr(desktop_capture, "capture_opened",
                        lambda *a, **k: {"path": "c.png", "bytes": 10,
                                         "identity_settled": True,
                                         "frame_uniform": False})
    monkeypatch.setattr(dax_tools, "_estado_de_datos",
                        lambda i: {"data_loaded": True})
    return mcp


def test_con_sesion_abierta_la_pagina_se_elige_en_la_ventana(proyecto, monkeypatch):
    mcp = _render_montado(monkeypatch, proyecto)
    pages_json = next(Path(proyecto).parent.rglob("pages.json"))
    antes = pages_json.read_bytes()
    usado = {}
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda opened, page=None, fit_to_page=False, adapter=None:
                        usado.update(page=page, fit=fit_to_page) or {
                            "page": {"verified": True, "visual_count": 1},
                            "fit_to_page": {"verified": True}})

    r = mcp.tools["pbi_validate_desktop_render"](str(proyecto), page="page01",
                                                 confirm_reuse=True)

    assert r["ok"] is True
    assert r["navigation"]["page"]["verified"] is True
    assert usado == {"page": "page01", "fit": True}
    assert pages_json.read_bytes() == antes, "se toco pages.json con Desktop abierto"


def test_una_pagina_no_demostrada_no_produce_una_captura(proyecto, monkeypatch):
    mcp = _render_montado(monkeypatch, proyecto)
    capturas = []
    monkeypatch.setattr(desktop_capture, "capture_opened",
                        lambda *a, **k: capturas.append(1))
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda opened, page=None, fit_to_page=False, adapter=None: {
                            "page": {"verified": False, "reason": "sin IsSelected"}})

    r = mcp.tools["pbi_validate_desktop_render"](str(proyecto), page="page01",
                                                 fit_to_page=False,
                                                 confirm_reuse=True)

    assert r["ok"] is False
    assert r["details"]["reason"] == "desktop_open_page_unverified"
    assert capturas == [], "se capturo otra pagina en silencio"


def test_sin_confirm_reuse_no_se_navega_en_la_ventana_del_usuario(
        proyecto, monkeypatch):
    """Una captura que solo debia observar no mueve la ventana ajena."""
    mcp = _render_montado(monkeypatch, proyecto)
    llamadas = []
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda *a, **k: llamadas.append(k) or {})

    r = mcp.tools["pbi_validate_desktop_render"](str(proyecto))
    assert r["ok"] is True
    assert llamadas == [], "se navego sin confirm_reuse"
    assert any("confirm_reuse" in w for w in r["warnings"])

    r = mcp.tools["pbi_validate_desktop_render"](str(proyecto), page="page01")
    assert r["ok"] is False
    assert r["details"]["reason"] == "desktop_open_page_needs_confirm"
    assert llamadas == []


def test_un_zoom_no_demostrado_degrada_a_aviso(proyecto, monkeypatch):
    mcp = _render_montado(monkeypatch, proyecto)
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda opened, page=None, fit_to_page=False, adapter=None: {
                            "fit_to_page": {"verified": False,
                                            "reason": "sin Toggle"}})

    r = mcp.tools["pbi_validate_desktop_render"](str(proyecto),
                                                 confirm_reuse=True)

    assert r["ok"] is True
    assert any("Ajustar a la pagina" in w for w in r["warnings"])


def test_open_and_refresh_acepta_page_y_declara_lo_no_demostrado(
        proyecto, monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_discovery, refresh
    from horizun_pbi_mcp.tools import refresh_tools

    mcp = _Mcp()
    refresh_tools.register(mcp)
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda *a, **k: _opened(proyecto))
    monkeypatch.setattr(desktop_discovery, "select_model",
                        lambda s, port=None, **k: type("M", (), {
                            "to_dict": lambda self: {"port": port}})())
    monkeypatch.setattr(refresh, "refresh_model",
                        lambda *a, **k: {"status": "ok"})
    monkeypatch.setattr(desktop_navigation, "navegar",
                        lambda opened, page=None, fit_to_page=False, adapter=None: {
                            "page": {"verified": False, "reason": "sin pestañas"}})

    r = mcp.tools["pbi_open_and_refresh"](str(proyecto), confirm=True,
                                          page="page01")

    assert r["ok"] is True
    assert r["navigation"]["page"]["verified"] is False
    assert any("no se pudo demostrar" in w for w in r["warnings"])
