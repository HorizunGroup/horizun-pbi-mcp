"""Regresiones de la auditoria independiente de la rama de carreras de UI.

Cada prueba reproduce un hallazgo CONFIRMADO por comportamiento -no por
simbolos nuevos-: contra la implementacion anterior fallan por aserciones,
no por atributos ausentes. Se agrupan por hallazgo:

1. Guardar se repetia con un modal abierto (podia confirmar un reemplazo).
2. Un COMError de UIA no se reintentaba ni limpiaba el cuadro.
3. El foco se comprobaba por proceso, no por cuadro ni campo.
4. El presupuesto del helper no cuadraba con su plazo de proceso.
5. La recuperacion de sesion adoptaba otro documento; la nota se arrastraba.
6. `pbi_capabilities` no recuperaba; `source` invalido se aceptaba.
7. `same_window_followed` con el mismo nombre; `opened_path_verified` por titulo.
8. Titulo "Power BI Desktop" a secas y plantilla `.pbit` sin esquema.

Despues, los grupos 9-20 recogen lo medido contra Desktop real y la
revision adversarial; 21-22 cierran la rama: que demuestra el zoom y
que una prueba live no pise la sesion del usuario.
"""
from __future__ import annotations

import _ctypes

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.powerbi import desktop_discovery as dd
from horizun_pbi_mcp.powerbi import desktop_identity as di
from horizun_pbi_mcp.powerbi import desktop_launcher as dl
from horizun_pbi_mcp.powerbi import desktop_ui, uia_helper
from horizun_pbi_mcp.services import pbix_export
from horizun_pbi_mcp.tools._common import guard
from tests.test_helper_sin_com import _Elemento, _UiaFalso


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


# ================ 1) Guardar no se repite con un modal delante ============
def _guardar_montado(monkeypatch, modales):
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: False)
    monkeypatch.setattr(uia_helper, "_modales", lambda u, pid, ex: list(modales))
    pulsaciones = []
    monkeypatch.setattr(uia_helper, "_confirmar",
                        lambda u, h, p: pulsaciones.append(1) or
                        {"commit_method": "dynamic_click", "attempts": []})
    return pulsaciones


def test_con_un_modal_abierto_no_se_vuelve_a_pulsar_guardar(monkeypatch, tmp_path):
    modal = {"hwnd": 99, "title": "Confirmar Guardar como",
             "kind": "confirm_replace", "owned_by_dialog": True}
    pulsaciones = _guardar_montado(monkeypatch, [modal])

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0)

    assert len(pulsaciones) == 1, "se pulso Guardar con un dialogo delante"
    assert salida["blocking_modals"][0]["hwnd"] == 99
    assert salida["overwrite_confirmed"] is None


def test_sin_overwrite_el_reemplazo_no_se_acepta_ni_siendo_propio(
        monkeypatch, tmp_path):
    modal = {"hwnd": 99, "title": "Confirmar Guardar como",
             "kind": "confirm_replace", "owned_by_dialog": True}
    _guardar_montado(monkeypatch, [modal])
    monkeypatch.setattr(uia_helper, "_aceptar_reemplazo",
                        lambda *a: pytest.fail("acepto un reemplazo sin permiso"))

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0,
        overwrite=False)

    assert salida["blocking_modals"]


def test_con_overwrite_solo_se_acepta_el_reemplazo_del_propio_cuadro(
        monkeypatch, tmp_path):
    propio = {"hwnd": 99, "title": "Confirmar Guardar como",
              "kind": "confirm_replace", "owned_by_dialog": True}
    ajeno = {"hwnd": 77, "title": "Confirmar Guardar como",
             "kind": "confirm_replace", "owned_by_dialog": False}
    aceptados = []

    class _Uia(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            if automation_id == uia_helper.AUTOMATION_ID_SI:
                return _Elemento("Si", automation_id)
            return super().por_id(raiz, automation_id, tipo)

        def invocar(self, elemento):
            aceptados.append(elemento.CurrentAutomationId)
            return "invoke"

    # Con el propio: se acepta y se espera el cierre.
    _guardar_montado(monkeypatch, [propio])
    cierres = iter([False, True, True, True])
    monkeypatch.setattr(uia_helper, "_esperar_cierre",
                        lambda h, p: next(cierres, True))
    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _Uia(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0,
        overwrite=True)
    assert salida["overwrite_confirmed"]["accepted"] is True
    assert aceptados == [uia_helper.AUTOMATION_ID_SI]

    # Con uno AJENO: ni con overwrite se toca.
    aceptados.clear()
    _guardar_montado(monkeypatch, [ajeno])
    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _Uia(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0,
        overwrite=True)
    assert aceptados == []
    assert salida["overwrite_confirmed"] is None
    assert salida["blocking_modals"][0]["hwnd"] == 77


def test_la_pertenencia_al_cuadro_se_decide_por_la_cadena_de_propietarias(
        monkeypatch):
    duenios = {99: 22, 77: 11, 22: 0, 11: 0}
    monkeypatch.setattr(uia_helper, "_propietaria", lambda h: duenios.get(h, 0))
    assert uia_helper._pertenece_al_cuadro(99, 22) is True   # noqa: SLF001
    assert uia_helper._pertenece_al_cuadro(77, 22) is False  # noqa: SLF001


def test_los_dialogos_wpf_se_ven_si_los_posee_la_principal(monkeypatch):
    """La plantilla y las credenciales son ventanas WPF, no `#32770`."""
    ventanas = [
        {"hwnd": 11, "class": "HwndWrapper[PBIDesktop.exe;;x]", "title": "Demo"},
        {"hwnd": 33, "class": "HwndWrapper[PBIDesktop.exe;;y]",
         "title": "Exportar una plantilla"},
        {"hwnd": 22, "class": "#32770", "title": "Guardar como"},
    ]
    monkeypatch.setattr(uia_helper, "ventanas_de", lambda pid: ventanas)
    monkeypatch.setattr(uia_helper, "_propietaria",
                        lambda h: {33: 11}.get(h, 0))

    class _Uia(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            return "combo" if raiz == "elemento-22" else None

    modales = uia_helper._modales(_Uia(), 4321, [22, 11])  # noqa: SLF001

    assert [m["hwnd"] for m in modales] == [33]
    assert modales[0]["owned_by_dialog"] is False


# ============= 2) COMError transitorio: reintento; el resto, no ===========
def _com(hresult):
    return _ctypes.COMError(hresult, "fallo COM", None)


def test_un_elemento_no_disponible_se_reintenta_y_termina_bien():
    class _Caduca(_UiaFalso):
        def __init__(self):
            super().__init__(valor_tipo="Archivo de Power BI (*.pbix)",
                             estado_tras=uia_helper.ESTADO_CERRADO)
            self.n = 0

        def items(self, combo):
            self.n += 1
            if self.n == 1:
                raise _com(-2147220991)        # 0x80040201 ELEMENTNOTAVAILABLE
            return self.opciones

    uia = _Caduca()
    paso = uia_helper._elegir_tipo(uia, 22, ".pbix")        # noqa: SLF001

    assert paso["attempts_total"] == 2
    assert paso["attempts"][0]["reason"] == "ui_element_gone"


def test_un_com_error_desconocido_es_definitivo_pero_con_fase():
    class _Rota(_UiaFalso):
        def items(self, combo):
            raise _com(-2147467259)             # 0x80004005 E_FAIL

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(_Rota(), 22, ".pbix")       # noqa: SLF001

    assert fallo.value.transitoria is False
    assert fallo.value.detalles["reason"] == "ui_com_error"
    assert fallo.value.detalles["attempts_total"] == 1


def test_un_error_de_programacion_no_se_disfraza_de_transitorio():
    class _Bug(_UiaFalso):
        def items(self, combo):
            raise KeyError("bug")

    with pytest.raises(KeyError):
        uia_helper._elegir_tipo(_Bug(), 22, ".pbix")        # noqa: SLF001


def test_un_fallo_inesperado_en_la_secuencia_tambien_limpia_el_cuadro(monkeypatch):
    from tests.test_ventanas_del_helper import _secuencia_montada

    class _Bug(_UiaFalso):
        def expandir(self, combo):
            raise KeyError("bug")

    _secuencia_montada(monkeypatch, _Bug())
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: True)
    limpiezas = []
    monkeypatch.setattr(uia_helper, "_cancelar_cuadro",
                        lambda u, h, p: limpiezas.append(h) or {"attempted": True})

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321, "out_path": r"C:\x\a.pbix"})

    assert limpiezas == [22]
    assert fallo.value.detalles["reason"] == "unexpected"
    assert "KeyError" in str(fallo.value)


# ================= 3) el foco: en ESTE cuadro y en ESTE campo =============
def test_con_el_foco_en_la_ventana_principal_del_mismo_proceso_no_se_teclea(
        monkeypatch):
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t: tecleado.append(t))
    # El primer plano es del proceso, pero no es el cuadro.
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: False)
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: True)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_UiaFalso(), 22, r"C:\x\a.pbix", 4321)  # noqa: SLF001

    assert tecleado == []
    assert fallo.value.detalles["reason"] == "focus_lost"


def test_si_el_foco_no_queda_en_el_campo_no_se_teclea(monkeypatch):
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t: tecleado.append(t))
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: True)

    class _FocoFuera(_UiaFalso):
        def foco_dentro_de(self, elemento):
            return False

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_FocoFuera(), 22, r"C:\x\a.pbix", 4321)  # noqa: SLF001

    assert tecleado == []
    assert fallo.value.detalles["reason"] == "field_focus_lost"


# ================= 4) el presupuesto cuadra con el plazo ==================
def test_el_presupuesto_del_helper_cabe_en_su_plazo_de_proceso(monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_helper

    recibido = {}
    monkeypatch.setattr(desktop_helper, "ejecutar",
                        lambda peticion, *, timeout: recibido.update(
                            peticion=peticion, timeout=timeout) or {"ok": True})

    desktop_ui.Win32UIAdapter().save_as_completo(
        pid=1, started=1.0, destino=r"C:\x\a.pbix", timeout=600.0,
        overwrite=True)

    p = recibido["peticion"]
    assert p["overwrite"] is True
    assert p["dialog_timeout"] + desktop_ui.PRESUPUESTO_FASES + p["save_timeout"] \
        < recibido["timeout"], "el helper moriria antes de agotar su presupuesto"
    assert p["save_timeout"] >= 20.0


# ========== 5) la recuperacion no cambia de documento; la nota no se arrastra
def _instancia(port, *, pid=200, catalog="Demo"):
    return {"host": "localhost", "port": port,
            "connection_string": f"Data Source=localhost:{port}",
            "catalog": catalog, "database_name": catalog, "model_name": "Model",
            "table_count": 3, "tables_sample": ["Ventas"], "status": "ok",
            "warnings": [], "pid": pid, "create_time": 2000.0 + pid,
            "session_fingerprint": f"fp-{port}-{pid}"}


@pytest.fixture
def caducada(session, monkeypatch):
    viejo = ActiveModel(host="localhost", port=50000,
                        connection_string="Data Source=localhost:50000",
                        catalog="Demo", pid=100, process_started=1000.0)
    monkeypatch.setattr(dd, "discover_instances", lambda: [])
    session.set_active_model(viejo)
    session._invalidate_model_verification()             # noqa: SLF001
    return session


@pytest.fixture
def con_proyecto(caducada, tmp_path):
    from horizun_pbi_mcp.pbip import project_locator
    from tests.fixtures import synthetic

    project_locator.open_project(caducada, str(synthetic.materialize(tmp_path)))
    return caducada


def test_lectura_con_proyecto_activo_sigue_la_unica_instancia_viva(
        con_proyecto, monkeypatch):
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50999, pid=300, catalog="Otro")])
    monkeypatch.setattr(di, "identify", lambda inst, target=None: {
        "desktop_pid": 300, "desktop_window_title": "OtroInforme",
        "path_match": False, "identity_confidence": "medium",
        "identity_evidence": []})

    modelo = con_proyecto.require_active_model()

    assert modelo.port == 50999
    nota = con_proyecto.consume_recovery()
    assert nota["document_evidence"]["path_match"] is False
    assert nota["rule"] == "unica instancia viva y verificable, como pbi_select_model"


def test_mutacion_con_proyecto_activo_no_cambia_de_documento(
        con_proyecto, monkeypatch):
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50999, pid=300, catalog="Otro")])
    monkeypatch.setattr(di, "identify", lambda inst, target=None: {
        "desktop_pid": 300, "desktop_window_title": "OtroInforme",
        "path_match": False, "identity_confidence": "high",
        "identity_evidence": []})

    with pytest.raises(dd.StaleSessionError) as fallo:
        with con_proyecto.active_model_lease():
            pass

    assert fallo.value.details["recovery"] == "document_mismatch"
    assert con_proyecto.active_model.port == 50000, "cambio de documento"
    assert con_proyecto.consume_recovery() is None


def test_con_proyecto_activo_y_ventana_del_proyecto_se_reconecta(
        con_proyecto, monkeypatch):
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50123, pid=300)])
    monkeypatch.setattr(di, "identify", lambda inst, target=None: {
        "desktop_pid": 300, "desktop_window_title": "Demo",
        "path_match": True, "identity_confidence": "medium",
        "identity_evidence": []})

    modelo = con_proyecto.require_active_model()

    assert modelo.port == 50123
    nota = con_proyecto.consume_recovery()
    assert nota["document_evidence"]["path_match"] is True


def test_sin_proyecto_una_mutacion_no_se_redirige_a_ciegas(caducada, monkeypatch):
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50123, pid=300)])

    with pytest.raises(dd.StaleSessionError) as fallo:
        with caducada.active_model_lease():
            pass

    assert fallo.value.details["recovery"] == "explicit_selection_required"
    assert caducada.active_model.port == 50000


def test_sin_proyecto_una_lectura_si_se_reconecta(caducada, monkeypatch):
    monkeypatch.setattr(dd, "discover_instances",
                        lambda: [_instancia(50123, pid=300)])
    assert caducada.require_active_model().port == 50123


def test_la_nota_no_se_arrastra_tras_una_excepcion_no_tipada(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg

    monkeypatch.setattr(cfg, "_session", session)

    def _revienta():
        session.note_recovery({"recovered": True, "selected": {"port": 1}})
        raise RuntimeError("boom")

    primera = guard(_revienta, operation="pbi_uno")
    segunda = guard(lambda: {"x": 1}, operation="pbi_dos")

    assert primera["session_recovery"]["selected"]["port"] == 1
    assert "session_recovery" not in segunda


def test_una_nota_previa_a_la_llamada_no_se_atribuye_a_ella(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg

    monkeypatch.setattr(cfg, "_session", session)
    session.note_recovery({"recovered": True, "selected": {"port": 9}})
    salida = guard(lambda: {"x": 1}, operation="pbi_dos")
    assert "session_recovery" not in salida


# ============ 6) capabilities recupera; source invalido se rechaza ========
def test_capabilities_recupera_la_sesion_y_lo_declara(caducada, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import ops_tools

    monkeypatch.setattr(cfg, "_session", caducada)
    nueva = _instancia(50123, pid=300)
    monkeypatch.setattr(dd, "discover_instances", lambda: [nueva])
    mcp = _Mcp()
    ops_tools.register(mcp)

    r = mcp.tools["pbi_capabilities"]()

    assert r["ok"] is True
    assert r["session_recovery"]["selected"]["port"] == 50123
    assert caducada.active_model.port == 50123


def test_source_invalido_en_particiones_se_rechaza(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import explore_tools

    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    explore_tools.register(mcp)
    r = mcp.tools["pbi_list_partitions"](source="foo")
    assert r["error"] == "validation_error"


# ============ 7) estado final: lo que no se demuestra no se afirma ========
def test_con_el_mismo_nombre_no_se_afirma_seguimiento_ni_se_abre_otra_ventana(
        monkeypatch, tmp_path):
    from tests.test_exportacion_pbix import _Abierto

    origen = tmp_path / "Demo.pbip"
    destino = tmp_path / "salida" / "Demo.pbix"
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto", lambda p: None)
    monkeypatch.setattr(dl, "open_pbix",
                        lambda *a, **k: pytest.fail("abrio otra ventana con dudas"))
    monkeypatch.setattr(dl, "_process_started", lambda pid: 1000.0)
    monkeypatch.setattr(di, "esperar_identidad_de_ventana",
                        lambda *a, **k: pytest.fail("el titulo no distingue"))
    monkeypatch.setattr(di, "identify", lambda inst, target=None: {
        "desktop_pid": 4321, "desktop_window_title": "Demo",
        "path_match": None, "identity_confidence": "low",
        "identity_evidence": []})
    monkeypatch.setattr(dd, "select_model",
                        lambda s, port=None, **k: type("M", (), {
                            "to_dict": lambda self: {"port": port}})())

    estado = pbix_export._estado_final(                   # noqa: SLF001
        None, abierto=_Abierto(origen), destino=destino, leave_open=True,
        timeout=5, origen=origen)

    assert estado["same_window_followed"] is False
    assert estado["reopened"] is False
    assert estado["opened_path_verified"] is False
    assert estado["window_follow"]["status"] == "inconclusive"
    assert estado["desktop_session"]["document"] is None
    assert len(estado["desktop_session"]["document_candidates"]) == 2


def test_una_plantilla_no_reapunta_la_ventana(monkeypatch, tmp_path):
    from tests.test_exportacion_pbix import _Abierto

    origen = tmp_path / "Demo.pbip"
    destino = tmp_path / "salida" / "Demo.pbit"
    monkeypatch.setattr(dl, "proceso_con_archivo_abierto",
                        lambda p: pytest.fail("no hay seguimiento que mirar"))
    monkeypatch.setattr(dl, "_process_started", lambda pid: 1000.0)
    monkeypatch.setattr(di, "identify", lambda inst, target=None: {
        "desktop_pid": 4321, "desktop_window_title": "Demo",
        "path_match": True, "identity_confidence": "medium",
        "identity_evidence": []})
    monkeypatch.setattr(dd, "select_model",
                        lambda s, port=None, **k: type("M", (), {
                            "to_dict": lambda self: {"port": port}})())

    estado = pbix_export._estado_final(                   # noqa: SLF001
        None, abierto=_Abierto(origen), destino=destino, leave_open=True,
        timeout=5, formato="pbit", origen=origen)

    assert estado["opened_path_verified"] is False
    assert estado["desktop_session"]["document"] == str(origen)
    assert estado["window_follow"]["status"] == "not_applicable"


def test_la_exportacion_registra_la_sesion_para_cerrarla_por_ruta(session, monkeypatch):
    session.recordar_exportacion({"desktop_pid": 7, "desktop_started": 1.0,
                                  "document": r"C:\x\Demo.pbix"})
    assert session.exportacion_de(r"C:\x\Demo.pbix")["desktop_pid"] == 7
    assert session.exportacion_de(r"C:\x\Otro.pbix") is None


# ================= 8) titulos del producto y esquema de plantilla ==========
def test_una_ventana_del_producto_a_secas_es_provisional(tmp_path):
    assert di.clasificar_titulos(["Sin título - Power BI Desktop",
                                  "Power BI Desktop"],
                                 tmp_path / "Demo.pbip") == di.IDENTIDAD_PROVISIONAL
    assert di.clasificar_titulos(["Otro - Power BI Desktop", "Power BI Desktop"],
                                 tmp_path / "Demo.pbip") == di.IDENTIDAD_PROVISIONAL


def test_otro_documento_sin_titulos_provisionales_sigue_siendo_rechazo(tmp_path):
    assert di.clasificar_titulos(["Otro - Power BI Desktop", "Credenciales"],
                                 tmp_path / "Demo.pbip") == di.IDENTIDAD_OTRO_DOCUMENTO


def test_una_plantilla_sin_esquema_de_un_proyecto_con_modelo_no_vale(tmp_path):
    import zipfile

    destino = tmp_path / "Demo.pbit"
    with zipfile.ZipFile(destino, "w") as zf:
        zf.writestr("Report/definition/report.json", "{}")

    with pytest.raises(pbix_export.PbixExportNotVerified) as fallo:
        pbix_export._inspeccionar_plantilla(destino, espera_modelo=True)  # noqa: SLF001
    assert fallo.value.details["reason"] == "template_without_model_schema"

    resumen = pbix_export._inspeccionar_plantilla(destino, espera_modelo=False)  # noqa: SLF001
    assert resumen["has_model_schema"] is False


# ============ 9) lo que enseño Power BI Desktop real (inspeccion live) =====
def test_la_pestana_de_cinta_se_llama_ver_en_espanol():
    """Comprobado contra Desktop real: el arbol UIA publica "Ver".

    Exigir "Vista" dejaba el camino de respaldo del zoom sin encontrar la
    pestaña. "Vista de informe" y "Vista de tabla" son botones de modo, no
    la pestaña de la cinta.
    """
    assert uia_helper.NOMBRE_PESTANA_VISTA.search("Ver")
    assert uia_helper.NOMBRE_PESTANA_VISTA.search("View")
    assert not uia_helper.NOMBRE_PESTANA_VISTA.search("Vista de informe")


def test_una_pagina_que_se_llama_como_una_pestana_de_cinta_no_se_elige(
        monkeypatch):
    """Contra Desktop real la cinta y las paginas son el MISMO tipo control.

    En una ventana con "Inicio", "Ver", "Ayuda" y "Page 1" como TabItem, una
    pagina llamada "Ver" produce dos candidatas: elegir la primera activaria
    la cinta y `IsSelected` diria que si.
    """
    from tests.test_navegacion_en_sesion_abierta import _UiaConPestanas, _montar

    uia = _UiaConPestanas(["Inicio", "Ver", "Ayuda", "Ver"])
    _montar(monkeypatch, uia)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.seleccionar_pagina({"desktop_pid": 4321, "page_name": "Ver"})

    assert fallo.value.detalles["reason"] == "page_tab_ambiguous"
    assert fallo.value.detalles["matches"] == 2
    assert uia.selecciones == [], "activo una pestaña de la cinta"


def test_la_ventana_principal_de_desktop_no_es_un_modal(monkeypatch):
    """Es WinForms y no tiene propietaria: comprobado en vivo.

    `WindowsForms10.Window.8.app...` con `owner=0`. Sin la regla del
    propietario, la propia ventana del informe se contaria como un dialogo
    que bloquea el guardado.
    """
    ventanas = [
        {"hwnd": 11, "class": "WindowsForms10.Window.8.app.0.37828af_r6_ad1",
         "title": "Demo"},
        {"hwnd": 22, "class": "#32770", "title": "Guardar como"},
    ]
    monkeypatch.setattr(uia_helper, "ventanas_de", lambda pid: ventanas)
    monkeypatch.setattr(uia_helper, "_propietaria", lambda h: 0)

    class _Uia(_UiaFalso):
        def por_id(self, raiz, automation_id, tipo):
            return "combo" if raiz == "elemento-22" else None

    assert uia_helper._modales(_Uia(), 4321, [22]) == []  # noqa: SLF001


# ===== 10) `ValuePattern.SetValue` es COSMETICO en el campo del nombre =====
# Comprobado contra Power BI Desktop real: se pidio guardar en
# `...\salida\Entregable_<nombre largo>.pbix`, UI Automation releyo esa ruta
# entera -`filename_verified: true`- y el cuadro guardo `Demo.pbix` en la
# carpeta del PROYECTO, con su nombre y su carpeta por defecto. Es el mismo
# fallo que `CB_SETCURSEL` con el tipo: cambia lo que se LEE, no lo que la
# aplicacion usa. Por eso ahora manda la lectura del `Edit` de Win32.
def test_ninguna_lectura_del_campo_sustituye_al_tecleo(monkeypatch):
    """El caso exacto medido en vivo: todo dice que si, y el cuadro guarda mal.

    `SetValue` devolvia exito, UIA releia la ruta entera y `WM_GETTEXT`
    tambien -es el mismo `Edit`-, y aun asi Power BI Desktop guardo con su
    nombre y su carpeta por defecto. Si alguien vuelve a aceptar esa via, la
    ruta no se teclea y esta prueba lo dice.
    """
    ruta = r"C:\entrega\Informe.pbix"
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: tecleado.append(t))
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: True)
    # Aunque Win32 confirme el texto ANTES de teclear, no vale de nada.
    monkeypatch.setattr(uia_helper, "_nombre_comprometido", lambda hwnd, r: True)

    class _UiaMentirosa(_UiaFalso):
        def __init__(self):
            super().__init__(valor_nombre=ruta)
            self.fijados = []

        def fijar_valor(self, elemento, texto):
            self.fijados.append(texto)
            return True

    uia = _UiaMentirosa()
    paso = uia_helper._escribir_ruta(uia, 22, ruta, 4321)   # noqa: SLF001

    assert uia.fijados == [], "se uso SetValue para el nombre"
    assert tecleado == [ruta], "no se tecleo la ruta"
    assert paso["method"] == "keyboard"


def test_el_tecleo_afloja_el_ritmo_en_cada_intento():
    """Las cadencias van de mas a menos: 40, 16 y 8 eventos por tanda."""
    tandas = [c[0] for c in uia_helper.CADENCIAS_TECLEO]
    pausas = [c[1] for c in uia_helper.CADENCIAS_TECLEO]
    assert tandas == sorted(tandas, reverse=True)
    assert pausas == sorted(pausas)
    # Hay mas intentos que cadencias: los ultimos repiten la mas lenta.
    assert len(uia_helper.CADENCIAS_TECLEO) <= uia_helper.INTENTOS_NOMBRE


def test_un_guardado_con_otro_nombre_en_la_carpeta_del_proyecto_se_explica(
        tmp_path):
    """`Demo.pbix` junto al proyecto tras pedir otra ruta: no es "no aparecio"."""
    origen = tmp_path / "proyecto" / "Demo.pbip"
    origen.parent.mkdir(parents=True)
    origen.write_text("{}", encoding="utf-8")
    destino = tmp_path / "salida" / "Entregable_largo.pbix"
    extraviado = origen.parent / "Demo.pbix"
    extraviado.write_bytes(b"PK\x03\x04")

    # Sin `desde` se conserva la regla vieja: solo el MISMO nombre.
    assert pbix_export.artefacto_extraviado(destino, origen) is None

    hallado = pbix_export.artefacto_extraviado(destino, origen, desde=0.0)
    assert hallado["found"] == str(extraviado)
    assert hallado["same_name"] is False
    assert hallado["requested_name"] == "Entregable_largo.pbix"


def test_un_archivo_viejo_de_otra_ejecucion_no_se_reporta(tmp_path):
    import os
    import time

    origen = tmp_path / "proyecto" / "Demo.pbip"
    origen.parent.mkdir(parents=True)
    origen.write_text("{}", encoding="utf-8")
    viejo = origen.parent / "Demo.pbix"
    viejo.write_bytes(b"PK")
    antiguo = time.time() - 3600
    os.utime(viejo, (antiguo, antiguo))

    assert pbix_export.artefacto_extraviado(
        tmp_path / "salida" / "Otro.pbix", origen, desde=time.time()) is None


# ====== 11) UI Automation devolviendo NADA no puede tumbar la operacion ====
def test_una_coleccion_vacia_de_uia_es_una_lista_vacia():
    """`FindAll` devuelve un puntero NULO cuando no encuentra nada.

    Tocarlo revienta con `ValueError: NULL COM pointer access`. Paso contra
    Power BI Desktop real buscando el boton del dialogo de plantilla, con el
    archivo YA guardado, y tumbo la exportacion entera.
    """
    class _Nulo:
        def __bool__(self):
            return False

        def __getattr__(self, nombre):
            raise ValueError("NULL COM pointer access")

    assert uia_helper._coleccion(lambda: _Nulo()) == []       # noqa: SLF001
    assert uia_helper._coleccion(lambda: None) == []          # noqa: SLF001

    def _revienta():
        raise ValueError("NULL COM pointer access")

    assert uia_helper._coleccion(_revienta) == []             # noqa: SLF001


def test_una_coleccion_con_elementos_se_materializa():
    class _Coleccion:
        Length = 2

        def GetElement(self, i):
            return f"e{i}"

    assert uia_helper._coleccion(lambda: _Coleccion()) == ["e0", "e1"]  # noqa: SLF001


def test_un_fallo_despues_de_guardar_no_se_queda_sin_fase(monkeypatch):
    """Con el archivo escrito, un error posterior tiene que decir donde fue."""
    from tests.test_ventanas_del_helper import _secuencia_montada

    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)",
                    estado_tras=uia_helper.ESTADO_CERRADO,
                    valor_nombre=r"C:\x\a.pbix")
    _secuencia_montada(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: True)
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: True)

    def _revienta(u, pid, ex):
        raise ValueError("NULL COM pointer access")

    monkeypatch.setattr(uia_helper, "_modales", _revienta)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321, "out_path": r"C:\x\a.pbix"})

    assert fallo.value.fase == "cierre"
    assert fallo.value.detalles["reason"] == "unexpected_after_save"
    assert fallo.value.detalles["dialog_closed"] is True
    assert [p["phase"] for p in fallo.value.detalles["steps"]][-1] == "guardar"


def test_el_dialogo_de_plantilla_de_desktop_real_se_reconoce():
    """Titulo y clase medidos en vivo: `Exportar una plantilla`, WinForms."""
    assert uia_helper.NOMBRE_DIALOGO_PLANTILLA.search("Exportar una plantilla")
    clase = "WindowsForms10.Window.20008.app.0.37828af_r6_ad1"
    assert clase.casefold() != uia_helper.CLASE_DIALOGO.casefold(), (
        "no es un dialogo comun: buscarlo solo entre `#32770` no lo encuentra")


# ===== 12) el puntero NULO de `FindFirst` no es `None` ====================
class _PunteroNulo:
    """Como devuelve comtypes un `FindFirst` sin resultados."""

    def __bool__(self):
        return False

    def __getattr__(self, nombre):
        raise ValueError("NULL COM pointer access")


def test_por_id_devuelve_None_cuando_no_hay_control():
    """`FindFirst` sin resultados da un puntero NULO que no es `None`.

    El dialogo de plantilla de Power BI Desktop no pone AutomationId a sus
    botones: medido en vivo, `Aceptar`, `Cancelar` y `Cerrar` con id vacio.
    `por_id(..., "1")` devolvia ese nulo, el `is None` de quien llamaba no lo
    veia, y el `Invoke` moria con `ValueError: NULL COM pointer access` con
    el archivo ya guardado.
    """
    class _Uia(uia_helper.Uia):
        def __init__(self):
            self.auto = _Automation()

    class _Automation:
        def CreateAndCondition(self, a, b):
            return "cond"

        def CreatePropertyCondition(self, prop, valor):
            return "prop"

    class _Raiz:
        def FindFirst(self, alcance, condicion):
            return _PunteroNulo()

    assert _Uia().por_id(_Raiz(), "1", uia_helper.UIA_TIPO_BUTTON) is None


def test_invocar_no_usa_un_patron_nulo():
    """Un patron no soportado tambien llega como puntero NULO."""
    class _Elemento:
        def GetCurrentPattern(self, patron):
            return _PunteroNulo()

    class _Uia(uia_helper.Uia):
        def __init__(self):
            self.modulo = None

    with pytest.raises(uia_helper.HelperError) as fallo:
        _Uia().invocar(_Elemento())

    assert fallo.value.detalles["reason"] == "element_not_invokable"


def test_el_boton_del_dialogo_de_plantilla_se_busca_por_nombre():
    """Sin AutomationId, el unico asidero es el nombre accesible."""
    assert uia_helper.NOMBRE_BOTON_ACEPTAR.search("Aceptar")
    assert uia_helper.NOMBRE_BOTON_ACEPTAR.search("OK")
    assert not uia_helper.NOMBRE_BOTON_ACEPTAR.search("Cancelar")


# ===== 13) el zoom: que cuenta como evidencia y que no ===================
# Medido contra Power BI Desktop real: "Ajustar a la pagina" es un `Button`
# que solo expone `Invoke` y `LegacyIAccessible`; no hay estado que releer.
# Lo que SI publica Desktop al cambiar el zoom es un anuncio de nivel
# ("Informe ampliado a 72 %"), y eso es especifico. Un cambio de pixeles no
# lo es: tambien lo produce abrir la cinta o terminar de pintar los datos.
class _AdaptadorZoom:
    def __init__(self, *, verified=False, anuncios=()):
        self.verified = verified
        self.anuncios = list(anuncios)

    def ajustar_a_pagina(self, *, pid, started, timeout=30.0):
        return {"verified": self.verified, "via": "invoke",
                "path": ["fit_to_page"], "state_after": None,
                "zoom_announcements_before": [],
                "zoom_announcements_new": self.anuncios,
                "zoom_level_changed": bool(self.anuncios),
                "verification_reason": ("el control no expone Toggle ni "
                                        "SelectionItem")}


def _opened_nav(tmp_path):
    from horizun_pbi_mcp.powerbi import desktop_launcher as dl_

    return dl_.OpenedPbix(str(tmp_path / "Demo.pbip"), {"port": 1}, 4321,
                          False, 0.0, desktop_started=1.0)


def test_el_zoom_se_demuestra_con_el_anuncio_de_nivel(monkeypatch, tmp_path):
    """La señal especifica: Desktop publica el nivel nuevo."""
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    miradas = []
    monkeypatch.setattr(nav, "huella_de_ventana",
                        lambda o: miradas.append(1) or "x")
    monkeypatch.setattr(nav.time, "sleep",
                        lambda s: pytest.fail("comparo pixeles sin necesitarlo"))

    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom(
                        anuncios=["Informe ampliado a 72 %"]))["fit_to_page"]

    assert r["verified"] is True
    assert r["verified_by"] == "zoom_level_announced"
    assert r["visual_change"] is None
    assert len(miradas) == 1, "solo la huella previa, que se toma siempre"
    assert r["zoom_level_announced"] == ["Informe ampliado a 72 %"]
    assert r["reason"] is None


def test_un_cambio_de_pixeles_NO_verifica_el_zoom(monkeypatch, tmp_path):
    """El punto de la auditoria: la ventana cambia por muchas razones.

    Abrir la cinta para llegar al control ya la cambia. Que cambie no dice
    que el modo de vista sea "ajustar a la pagina".
    """
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    huellas = iter(["antes", "despues"])
    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: next(huellas, "z"))
    monkeypatch.setattr(nav.time, "sleep", lambda s: None)

    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom())["fit_to_page"]

    assert r["verified"] is False, "los pixeles no pueden verificar el zoom"
    assert r["verified_by"] is None
    assert r["visual_change"] is True
    assert "no identifica el modo de vista" in r["reason"]
    assert "frame_changed" not in r, "el nombre viejo prometia de mas"


def test_sin_cambio_no_se_afirma_que_ya_estuviera_ajustada(monkeypatch,
                                                           tmp_path):
    """Tampoco al reves: la ausencia de cambio no demuestra nada."""
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: "igual")
    monkeypatch.setattr(nav.time, "sleep", lambda s: None)

    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom())["fit_to_page"]

    assert r["verified"] is False
    assert r["visual_change"] is False
    # Se ofrecen las DOS posibilidades y se dice que no se distinguen; lo que
    # no se hace es quedarse con la tranquilizadora.
    assert "No se puede distinguir" in r["reason"]
    assert "la accion no llego" in r["reason"]


def test_el_estado_del_control_manda_sobre_todo(monkeypatch, tmp_path):
    """Si el control dice que quedo activo, no hace falta nada mas."""
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    miradas = []
    monkeypatch.setattr(nav, "huella_de_ventana",
                        lambda o: miradas.append(1) or "x")
    monkeypatch.setattr(nav.time, "sleep",
                        lambda s: pytest.fail("comparo pixeles sin necesitarlo"))

    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom(verified=True))["fit_to_page"]

    assert r["verified"] is True and r["verified_by"] == "control_state"
    assert r["visual_change"] is None
    assert len(miradas) == 1


def test_sin_poder_mirar_la_ventana_se_dice_que_no_se_pudo(monkeypatch,
                                                           tmp_path):
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: None)

    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom())["fit_to_page"]

    assert r["verified"] is False and r["visual_change"] is None
    assert "no se pudo comparar" in r["reason"]


def test_el_anuncio_de_zoom_no_se_confunde_con_las_plantillas():
    """La ventana lleva textos fijos que tambien citan un porcentaje."""
    R = uia_helper.ANUNCIO_DE_ZOOM
    assert R.search("Informe ampliado a 72 %").group(1) == "72"
    assert R.search("Report zoomed to 72%").group(1) == "72"
    # Estos aparecen SIEMPRE, antes y despues: por eso se comparan listas.
    assert R.search("Informe ampliado a 100 %. 10 resultados")
    assert not R.search("Grafico de barras 100 % apiladas")
    assert not R.search("Nivel de zoom. Haga clic para abrir el cuadro")


def test_solo_cuenta_el_anuncio_NUEVO(monkeypatch):
    """Lo constante se cancela: solo lo que aparece es evidencia."""
    class _Uia(_UiaFalso):
        def __init__(self, textos):
            super().__init__()
            self.textos = textos

        def todos_de_tipo(self, raiz, tipo):
            return list(self.textos)

        def nombre(self, elemento):
            return elemento

    fijos = ["Informe ampliado a 100 %. 10 resultados", "Nivel de zoom"]
    assert uia_helper._anuncios_de_zoom(_Uia(fijos), None) == [  # noqa: SLF001
        "Informe ampliado a 100 %. 10 resultados"]
    con_nuevo = uia_helper._anuncios_de_zoom(                     # noqa: SLF001
        _Uia(fijos + ["Informe ampliado a 72 %"]), None)
    assert [a for a in con_nuevo
            if a not in ["Informe ampliado a 100 %. 10 resultados"]] == [
        "Informe ampliado a 72 %"]


# ===== 14) tras aceptar el reemplazo NO se vuelve a pulsar Guardar ========
def test_aceptado_el_reemplazo_se_espera_el_cierre_sin_pulsar_otra_vez(
        monkeypatch, tmp_path):
    """Medido en vivo: con overwrite el cuadro pide confirmacion.

    Aceptarla es lo correcto; volver a pulsar Guardar despues cae sobre un
    cuadro que ya esta guardando.
    """
    modal = {"hwnd": 99, "title": "Confirmar Guardar como",
             "kind": "confirm_replace", "owned_by_dialog": True}
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    # El cuadro no se cierra al primer intento; tras aceptar el reemplazo, si.
    cierres = iter([False, True, True, True])
    monkeypatch.setattr(uia_helper, "_esperar_cierre",
                        lambda h, p: next(cierres, True))
    monkeypatch.setattr(uia_helper, "_modales", lambda u, pid, ex: [modal])
    monkeypatch.setattr(uia_helper, "_aceptar_reemplazo",
                        lambda u, m: {"accepted": True, "hwnd": m["hwnd"],
                                      "via": "invoke", "modal_closed": True})
    pulsaciones = []
    monkeypatch.setattr(uia_helper, "_confirmar",
                        lambda u, h, p: pulsaciones.append(1) or
                        {"commit_method": "invoke", "attempts": []})

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0,
        overwrite=True)

    assert len(pulsaciones) == 1, "se pulso Guardar tras aceptar el reemplazo"
    assert salida["overwrite_confirmed"]["accepted"] is True
    assert salida["commit_evidence"]["after_overwrite_confirm"] is True
    assert salida["dialog_closed"] is True


# ===== 15) perder el foco a mitad NO sigue tecleando =====================
def test_el_tecleo_se_corta_en_cuanto_el_foco_se_va(monkeypatch):
    """Medido con dos ventanas de Desktop peleandose por el primer plano.

    Sin guardia, el resto de las pulsaciones salia igual y acababa en la
    ventana que hubiera robado el foco.
    """
    tandas = []
    monkeypatch.setattr(uia_helper, "_enviar_teclas",
                        lambda eventos: tandas.append(len(eventos)))
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    llamadas = {"n": 0}

    def _guardia():
        llamadas["n"] += 1
        return llamadas["n"] <= 2          # el foco se va en la tercera tanda

    with pytest.raises(uia_helper.FocoPerdido) as fallo:
        uia_helper.escribir_texto_real("0123456789" * 6, tanda=8, pausa=0,
                                       guardia=_guardia)

    assert len(tandas) == 2, "siguio tecleando sin foco"
    assert fallo.value.escritos == 8       # 2 tandas de 8 eventos = 8 letras
    assert fallo.value.total == 60


def test_sin_guardia_se_teclea_entero():
    """El comportamiento de siempre cuando no hay a quien preguntar."""
    enviados = []
    original = uia_helper._enviar_teclas                  # noqa: SLF001
    uia_helper._enviar_teclas = lambda e: enviados.append(len(e))  # noqa: SLF001
    try:
        uia_helper.escribir_texto_real("abcdef", tanda=4, pausa=0)
    finally:
        uia_helper._enviar_teclas = original              # noqa: SLF001
    assert sum(enviados) == 12                            # 6 letras x 2 eventos


def test_el_foco_perdido_a_mitad_es_transitorio_y_dice_cuanto_llego(monkeypatch):
    ruta = r"C:\entrega\Informe_de_ruta_muy_larga.pbix"
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: True)

    def _teclear(texto, *, tanda=40, pausa=0.01, guardia=None):
        raise uia_helper.FocoPerdido(11, len(texto))

    monkeypatch.setattr(uia_helper, "escribir_texto_real", _teclear)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_UiaFalso(), 22, ruta, 4321)  # noqa: SLF001

    assert fallo.value.transitoria is True
    assert fallo.value.detalles["reason"] == "focus_lost_mid_typing"
    assert fallo.value.detalles["typed"] == 11
    assert fallo.value.detalles["expected_len"] == len(ruta)
    assert fallo.value.detalles["attempts_total"] == uia_helper.INTENTOS_NOMBRE


def test_el_error_de_uia_bajo_contencion_se_reintenta():
    """0x80131509: medido robando el foco durante la escritura."""
    assert 0x80131509 in uia_helper.HRESULT_TRANSITORIOS
    traducido = uia_helper._error_com_como_helper(          # noqa: SLF001
        _com(-2146233079), "nombre")
    assert traducido.transitoria is True
    assert traducido.detalles["reason"] == "ui_element_gone"


# ===== 16) abrir el cuadro tambien es una fase que se reintenta ===========
def test_si_el_foco_se_va_antes_del_acelerador_no_se_manda_F12(monkeypatch):
    """Medido en vivo: 1 de 5 exportaciones moria en "no aparecio el cuadro".

    Entre traer la ventana al frente y soltar F12 se colaba la otra ventana,
    y la tecla acababa alli.
    """
    from tests.test_ventanas_del_helper import _secuencia_montada

    uia = _UiaFalso()
    _secuencia_montada(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: True)
    monkeypatch.setattr(uia_helper, "_primer_plano_es_de", lambda pid: False)
    teclas = []
    monkeypatch.setattr(uia_helper, "_enviar_teclas",
                        lambda e: teclas.append(e))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321, "out_path": r"C:\x\a.pbix"})

    assert teclas == [], "se envio F12 sin tener el primer plano"
    assert fallo.value.fase == "abrir_cuadro"
    assert fallo.value.detalles["reason"] == "foreground_lost_before_accelerator"
    assert fallo.value.detalles["attempts_total"] == uia_helper.INTENTOS_POR_FASE


def test_el_cuadro_que_no_aparece_se_reintenta_y_termina_abriendo(monkeypatch):
    from tests.test_ventanas_del_helper import _secuencia_montada

    ruta = r"C:\x\a.pbix"
    uia = _UiaFalso(valor_tipo="Archivo de Power BI (*.pbix)",
                    estado_tras=uia_helper.ESTADO_CERRADO, valor_nombre=ruta)
    _secuencia_montada(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p, **kw: True)
    monkeypatch.setattr(uia_helper, "_primer_plano_es_de", lambda pid: True)
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro", lambda h: True)
    intentos = {"n": 0}

    def _esperar(u, pid, plazo):
        intentos["n"] += 1
        if intentos["n"] < 2:
            raise uia_helper.HelperError(
                "abrir_cuadro", "No aparecio el cuadro de guardado en el plazo.",
                transitoria=True, reason="save_dialog_not_found")
        return {"hwnd": 22}

    monkeypatch.setattr(uia_helper, "_esperar_cuadro", _esperar)

    salida = uia_helper.guardar_como({"desktop_pid": 4321, "out_path": ruta})

    assert salida["ok"] is True
    assert intentos["n"] == 2
    fases = {p["phase"]: p for p in salida["steps"]}
    assert fases["abrir_cuadro"]["attempts_total"] == 2
    assert fases["abrir_cuadro"]["attempts"][0]["reason"] == "save_dialog_not_found"


# ===== 17) el limite REAL de la guardia de foco ===========================
def test_lo_que_puede_escaparse_es_UNA_tanda_no_cero(monkeypatch):
    """La guardia acota el fragmento; no lo elimina.

    Entre consultar el foco y que `SendInput` entregue la tanda hay una
    ventana temporal que Windows no deja cerrar. Esta prueba fija el limite
    para que nadie lo describa como imposibilidad: como mucho se escapa una
    tanda, y despues la escritura se detiene.
    """
    enviadas = []
    monkeypatch.setattr(uia_helper, "_enviar_teclas",
                        lambda e: enviadas.append(len(e)))
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    # El foco esta bien al comprobar y se pierde justo despues, ya enviada
    # esa tanda: es el peor caso que el mecanismo permite.
    veces = {"n": 0}

    def _guardia():
        veces["n"] += 1
        return veces["n"] <= 1

    with pytest.raises(uia_helper.FocoPerdido) as fallo:
        uia_helper.escribir_texto_real("x" * 100, tanda=40, pausa=0,
                                       guardia=_guardia)

    assert enviadas == [40], "se entrego mas de una tanda sin foco"
    assert fallo.value.escritos == 20        # 40 eventos = 20 caracteres
    maximo = max(t for t, _p in uia_helper.CADENCIAS_TECLEO) // 2
    assert fallo.value.escritos <= maximo


def test_la_cadencia_lenta_acota_mas_el_fragmento():
    """La ultima cadencia deja escapar 4 caracteres, no 20."""
    lenta = uia_helper.CADENCIAS_TECLEO[-1][0]
    assert lenta // 2 == 4


def test_ctrl_a_tampoco_se_envia_sin_foco(monkeypatch):
    """La primera pulsacion que sale es `Ctrl+A`, y tambien va protegida."""
    ruta = r"C:\entrega\Informe.pbix"
    pulsado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo",
                        lambda: pulsado.append("ctrl+a"))
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t, **kw: pulsado.append("texto"))
    # Pasa la comprobacion de entrada y se pierde justo antes del Ctrl+A.
    focos = iter([True, False, True, False, True, False,
                  True, False, True, False, True, False])
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro",
                        lambda h: next(focos, False))

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_UiaFalso(), 22, ruta, 4321)  # noqa: SLF001

    assert pulsado == [], "se envio Ctrl+A sin tener el foco"
    assert fallo.value.detalles["reason"] == "focus_lost_before_select_all"


def test_cada_reintento_vuelve_a_exigir_cuadro_Y_campo(monkeypatch):
    """El reintento no se salta ninguna de las dos comprobaciones."""
    ruta = r"C:\entrega\Informe.pbix"
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real", lambda t, **kw: None)
    cuadro, campo = [], []
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro",
                        lambda h: cuadro.append(h) or True)

    class _SinFocoEnCampo(_UiaFalso):
        def foco_dentro_de(self, elemento):
            campo.append(1)
            return False

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_SinFocoEnCampo(), 22, ruta, 4321)  # noqa: SLF001

    intentos = uia_helper.INTENTOS_NOMBRE
    assert fallo.value.detalles["reason"] == "field_focus_lost"
    assert len(campo) == intentos, "algun reintento no comprobo el campo"
    assert len(cuadro) >= intentos, "algun reintento no comprobo el cuadro"


# ===== 18) pagina vs cinta: se distinguen por DONDE viven =================
# Medido contra Power BI Desktop real. La pestaña de cinta "Ver" tiene
# AutomationId 'view' y cuelga de `ms-OverflowSet` dentro del grupo
# `tablist`; la pagina "Ver" tiene id vacio y su contenedor de seleccion es
# `carouselScrollPane`, dentro de `explorationNavigationContent`. Son clases
# CSS del lienzo web, no texto traducido.
class _Pestana:
    def __init__(self, nombre, *, aid="", contenedor="", ancestros=()):
        self.CurrentName = nombre
        self.CurrentAutomationId = aid
        self._contenedor = contenedor
        self._ancestros = list(ancestros)
        self.seleccionada = False


class _UiaConCintaYPaginas(_UiaFalso):
    """Como el arbol real: cinta y paginas comparten el tipo TabItem."""

    def __init__(self, pestanas):
        super().__init__()
        self.pestanas = pestanas
        self.selecciones = []

    def todos_de_tipo(self, raiz, tipo):
        return list(self.pestanas) if tipo == uia_helper.UIA_TIPO_TABITEM else []

    def nombre(self, e):
        return e.CurrentName

    def clase(self, e):
        return e if isinstance(e, str) else ""

    def contenedor_de_seleccion(self, e):
        return e._contenedor or None

    def ancestros(self, e, niveles=4):
        return list(e._ancestros)

    def seleccionar(self, e):
        self.selecciones.append(e.CurrentName)
        e.seleccionada = True
        return "selection_item"

    def esta_seleccionado(self, e):
        return e.seleccionada


def _arbol_real():
    return [
        _Pestana("Inicio", aid="home", contenedor="ms-OverflowSet root-137",
                 ancestros=["ms-OverflowSet root-137", "ms-FocusZone css-136"]),
        _Pestana("Ver", aid="view", contenedor="ms-OverflowSet root-137",
                 ancestros=["ms-OverflowSet root-137", "ms-FocusZone css-136"]),
        _Pestana("Page 1", contenedor="carouselScrollPane",
                 ancestros=["carouselScrollPane",
                            "editing explorationNavigationContent unselectable"]),
        _Pestana("Ver", contenedor="carouselScrollPane",
                 ancestros=["carouselScrollPane",
                            "editing explorationNavigationContent unselectable"]),
    ]


def test_una_pagina_llamada_como_la_cinta_SI_se_puede_elegir(monkeypatch):
    """Ya no se rechaza: el contenedor la distingue."""
    from tests.test_navegacion_en_sesion_abierta import _montar

    uia = _UiaConCintaYPaginas(_arbol_real())
    _montar(monkeypatch, uia)

    salida = uia_helper.seleccionar_pagina({"desktop_pid": 4321,
                                            "page_name": "Ver"})

    assert salida["verified"] is True
    assert salida["disambiguated_by"] == "page_tab_container"
    assert uia.selecciones == ["Ver"]
    seleccionadas = [p for p in uia.pestanas if p.seleccionada]
    assert [p.CurrentAutomationId for p in seleccionadas] == [""], (
        "se activo la pestaña de la CINTA, que lleva AutomationId 'view'")
    assert salida["container"]["selection_container_class"] == "carouselScrollPane"


def test_una_pagina_sin_homonimos_no_necesita_desambiguar(monkeypatch):
    from tests.test_navegacion_en_sesion_abierta import _montar

    uia = _UiaConCintaYPaginas(_arbol_real())
    _montar(monkeypatch, uia)

    salida = uia_helper.seleccionar_pagina({"desktop_pid": 4321,
                                            "page_name": "Page 1"})

    assert salida["verified"] is True
    assert salida["disambiguated_by"] is None


def test_si_el_filtro_no_deja_una_sola_se_sigue_rechazando(monkeypatch):
    """Dos paginas homonimas en el carrusel: no hay criterio, no se elige."""
    from tests.test_navegacion_en_sesion_abierta import _montar

    dos = [_Pestana("Ver", contenedor="carouselScrollPane"),
           _Pestana("Ver", contenedor="carouselScrollPane")]
    uia = _UiaConCintaYPaginas(dos)
    _montar(monkeypatch, uia)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.seleccionar_pagina({"desktop_pid": 4321, "page_name": "Ver"})

    assert fallo.value.detalles["reason"] == "page_tab_ambiguous"
    assert fallo.value.detalles["matches_in_page_carousel"] == 2
    assert uia.selecciones == []


def test_sin_poder_leer_el_contenedor_no_se_adivina(monkeypatch):
    """Un arbol que no expone clases deja la ambiguedad como estaba."""
    from tests.test_navegacion_en_sesion_abierta import _UiaConPestanas, _montar

    uia = _UiaConPestanas(
        ["Ver", "Ver"], expone_contenedor=False)    # sin clase ni contenedor
    _montar(monkeypatch, uia)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.seleccionar_pagina({"desktop_pid": 4321, "page_name": "Ver"})

    assert fallo.value.detalles["reason"] == "page_tab_ambiguous"
    assert fallo.value.detalles["matches_in_page_carousel"] == 0
    assert uia.selecciones == []


def test_el_patron_del_carrusel_no_depende_del_idioma():
    """Se filtra por CLASE CSS, no por el nombre traducido del grupo."""
    R = uia_helper.CLASE_PESTANAS_DE_PAGINA
    assert R.search("carouselScrollPane")
    assert R.search("editing explorationNavigationContent unselectable")
    assert not R.search("ms-OverflowSet root-137")
    assert not R.search("ms-FocusZone css-136")


def test_fijar_valor_se_niega_a_correr():
    """No se deja como si funcionara: leerla disponible costo caro una vez."""
    class _Uia(uia_helper.Uia):
        def __init__(self):
            pass

    with pytest.raises(uia_helper.HelperError) as fallo:
        _Uia().fijar_valor(object(), "C:/x/a.pbix")

    assert fallo.value.detalles["reason"] == "set_value_does_not_commit"
    assert fallo.value.transitoria is False


# ===== 19) recuperar el foco DEL CUADRO, no del proceso ==================
def test_traer_al_frente_exacto_no_se_conforma_con_el_proceso(monkeypatch):
    """El defecto: la ventana principal del mismo Desktop tenia el foco.

    `traer_al_frente` devolvia True sin tocar nada -el primer plano era del
    pid- y el llamador concluia "no se pudo recuperar el foco". La
    recuperacion no llegaba a intentarse justo en su caso.
    """
    import ctypes

    estado = {"frente": 11, "intentos": 0}          # 11 = ventana principal

    class _Fn:
        def __init__(self, efecto):
            self.efecto = efecto
            self.argtypes = self.restype = None

        def __call__(self, *a):
            return self.efecto(*a)

    def _duenio(hwnd, puntero):
        puntero._obj.value = 4321                   # mismo proceso siempre
        return 7

    def _set_foreground(h):
        estado["intentos"] += 1
        estado["frente"] = 22                       # ahora si, el cuadro
        return 1

    class _U32:
        def __init__(self):
            self.GetForegroundWindow = _Fn(lambda: estado["frente"])
            self.GetWindowThreadProcessId = _Fn(_duenio)
            self.SetForegroundWindow = _Fn(_set_foreground)

        def __getattr__(self, n):
            return _Fn(lambda *a: 1)

    monkeypatch.setattr(uia_helper, "_user32", lambda: _U32())
    monkeypatch.setattr(ctypes, "byref", lambda x: type("P", (), {"_obj": x})())
    monkeypatch.setattr(uia_helper, "_raiz_de", lambda h: h)
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: _U32())

    # Sin `exacto`, se da por hecho: el foco es del proceso.
    estado.update(frente=11, intentos=0)
    assert uia_helper.traer_al_frente(22, 4321) is True
    assert estado["intentos"] == 0, "no intento nada y dijo que si"

    # Con `exacto`, se exige ESA ventana y se actua.
    estado.update(frente=11, intentos=0)
    assert uia_helper.traer_al_frente(22, 4321, exacto=True) is True
    assert estado["intentos"] >= 1, "no intento traer el cuadro al frente"


def test_el_cuadro_se_recupera_con_exacto(monkeypatch):
    """`_escribir_ruta` pide la ventana concreta, no cualquiera del pid."""
    ruta = r"C:\entrega\Informe.pbix"
    pedidos = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real", lambda t, **kw: None)
    estado = {"ok": False}
    monkeypatch.setattr(uia_helper, "_primer_plano_es_el_cuadro",
                        lambda h: estado["ok"])

    def _frente(h, p, *, exacto=False):
        pedidos.append(exacto)
        estado["ok"] = True
        return True

    monkeypatch.setattr(uia_helper, "traer_al_frente", _frente)

    paso = uia_helper._escribir_ruta(_UiaFalso(valor_nombre=ruta), 22,  # noqa: SLF001
                                     ruta, 4321)

    assert paso["filename_verified"] is True
    assert pedidos == [True], "pidio el foco sin exigir ESTA ventana"


# ===== 20) la ventana de medicion del zoom empieza al pulsar ==============
def test_el_anuncio_se_mide_desde_el_invoke_no_desde_la_entrada(monkeypatch):
    """Llegar al control puede costar segundos, y la pagina anuncia sola.

    Si la foto de referencia se toma al entrar, un anuncio provocado por la
    navegacion previa contaria como prueba del pulsado.
    """
    from tests.test_navegacion_en_sesion_abierta import _UiaConCinta, _montar

    class _AnunciaAlNavegar(_UiaConCinta):
        def __init__(self):
            super().__init__(directo=False)
            self.textos = ["Informe ampliado a 100 %. 3 resultados"]

        def todos_de_tipo(self, raiz, tipo):
            if tipo == uia_helper.UIA_TIPO_TEXT:
                return list(self.textos)
            return super().todos_de_tipo(raiz, tipo)

        def nombre(self, e):
            return e if isinstance(e, str) else super().nombre(e)

        def invocar(self, elemento):
            # Al desplegar el menu, la pagina anuncia su nivel actual.
            if elemento is self.menu:
                self.textos.append("Informe ampliado a 55 %")
            return super().invocar(elemento)

    uia = _AnunciaAlNavegar()
    _montar(monkeypatch, uia)

    salida = uia_helper.ajustar_a_pagina({"desktop_pid": 4321})

    assert salida["zoom_level_changed"] is False, (
        "conto como prueba un anuncio que provoco la propia navegacion")
    assert "Informe ampliado a 55 %" in salida["zoom_announcements_before"]
    assert salida["zoom_announcements_at_entry"] == [
        "Informe ampliado a 100 %. 3 resultados"]


# ===== 21) `verified` del zoom no puede leerse como "modo aplicado" ========
# El grupo 13 fijo QUE cuenta como evidencia. Este fija hasta donde llega esa
# evidencia en la respuesta publica, que es lo que un cliente MCP lee.
def test_el_anuncio_de_nivel_no_se_publica_como_prueba_del_modo(monkeypatch,
                                                                tmp_path):
    """El anuncio demuestra que el zoom CAMBIO al pulsar, y nada mas.

    "Ajustar al ancho" tambien anunciaria un nivel nuevo, asi que la
    respuesta tiene que declarar ella misma el alcance de su `verified`.
    """
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: "x")
    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom(
                        anuncios=["Informe ampliado a 86 %"]))["fit_to_page"]

    assert r["verified"] is True
    dicho = r.get("verified_means")
    assert dicho and "no que el modo" in dicho, (
        "publica `verified` sin decir hasta donde llega")
    assert "Ajustar al ancho" in dicho


def test_solo_el_estado_del_control_identifica_el_modo(monkeypatch, tmp_path):
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: "x")
    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom(verified=True))["fit_to_page"]

    assert r["verified_by"] == "control_state"
    assert "identifica el modo" in (r.get("verified_means") or "")


def test_sin_evidencia_no_se_afirma_nada_del_modo(monkeypatch, tmp_path):
    """Ni "ya estaba ajustada" ni "no llego": no se pueden distinguir."""
    from horizun_pbi_mcp.powerbi import desktop_navigation as nav

    monkeypatch.setattr(nav, "huella_de_ventana", lambda o: "x")
    monkeypatch.setattr(nav.time, "sleep", lambda s: None)
    r = nav.navegar(_opened_nav(tmp_path), fit_to_page=True,
                    adapter=_AdaptadorZoom())["fit_to_page"]

    assert r["verified"] is False
    assert r["verified_by"] is None
    assert r.get("verified_means", "ausente") is None
    assert r["visual_change"] is False
    assert "ya estaba ajustada o si la accion no llego" in r["reason"], (
        "afirmo una de las dos causas sin poder distinguirlas")


def test_la_descripcion_publica_no_promete_el_modo_de_vista():
    """Aguas abajo, lo que un cliente MCP lee es la ficha de la tool.

    De nada sirve que el codigo sea preciso si la descripcion publicada dice
    que el zoom "se verifica" a secas.
    """
    from horizun_pbi_mcp.tools import dax_tools

    mcp = _Mcp()
    dax_tools.register(mcp)
    ficha = mcp.tools["pbi_validate_desktop_render"].__doc__ or ""

    assert "se VERIFICA el resultado" not in ficha
    assert "verified_means" in ficha
    assert "no que el modo resultante" in ficha


# ===== 22) una prueba live no puede pisar la sesion del usuario ===========
def test_una_prueba_live_puede_aislar_su_session_json(monkeypatch, tmp_path):
    """El incidente de esta rama: los scripts live escribieron `session.json`.

    `project_locator.open_project()` persiste el proyecto activo, asi que un
    script de prueba deja apuntando la sesion a su fixture temporal. La via
    de aislamiento no es nueva -`HORIZUN_PBI_MCP_OUTPUTS_DIR` ya existia-,
    pero nadie la ejercitaba: sin esta prueba, un cambio en la resolucion de
    rutas volveria a llevar la sesion de las pruebas a la del usuario.
    """
    from horizun_pbi_mcp import config

    del_usuario = tmp_path / "outputs_del_usuario"
    del_usuario.mkdir()
    suyo = del_usuario / "session.json"
    suyo.write_text('{"active_model": null, "active_pbip": null}',
                    encoding="utf-8")
    antes = suyo.read_bytes()

    aislada = tmp_path / "outputs_de_la_prueba"
    monkeypatch.setenv("HORIZUN_PBI_MCP_OUTPUTS_DIR", str(aislada))
    monkeypatch.setattr(config, "_settings", None)

    ajustes = config.Settings.load()
    ajustes.ensure_dirs()
    assert ajustes.outputs_dir == aislada

    sesion = config.Session(ajustes)
    sesion.set_active_pbip(config.ActivePbip(
        pbip_path=str(tmp_path / "Sintetico.pbip"),
        project_dir=str(tmp_path)))

    assert (aislada / "session.json").is_file(), "no escribio donde se le dijo"
    assert suyo.read_bytes() == antes, "toco la sesion del usuario"


def test_el_prefijo_antiguo_tambien_aisla(monkeypatch, tmp_path):
    """Quien tenga el nombre viejo en su entorno no se queda sin aislamiento."""
    from horizun_pbi_mcp import config

    monkeypatch.delenv("HORIZUN_PBI_MCP_OUTPUTS_DIR", raising=False)
    monkeypatch.setenv("PBI_MCP_OUTPUTS_DIR", str(tmp_path / "vieja"))
    monkeypatch.setattr(config, "_settings", None)

    assert config.Settings.load().outputs_dir == tmp_path / "vieja"
