"""PBIP -> PBIX por `Guardar como`, y nada dado por hecho.

Microsoft no publica API para convertir el formato. Lo que se automatiza aqui
es el flujo oficial de Power BI Desktop, y lo que estas pruebas defienden no
es la mecanica de teclear -eso lo ejercita la prueba `live`- sino todo lo que
la rodea, que es donde estan los defectos caros:

- que se conduzca la ventana CORRECTA y no la que aparecio;
- que el tipo de archivo se elija y no se herede (guardar como `.pbit`
  produce una plantilla vacia con aspecto de entregable);
- que un dialogo visible no acabe reportado como timeout;
- que "el dialogo se cerro" no se confunda con "el archivo se guardo";
- que un fallo restaure el destino y no cambie el proyecto activo.

Windows UI Automation vive detras de un adaptador inyectable. Aqui se usa un
doble: la suite no abre ventanas.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import desktop_ui
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import pbix_export, project_resolver
from tests.fixtures import synthetic
from tests.test_pbix_convert import _escribir_pbix, _layout

PID_DESKTOP = 4321
ARRANQUE = 1000.0


# ------------------------------------------------------------- los dobles ----
class _AdaptadorFalso:
    """Simula el cuadro de guardado. Registra lo que se le pidio."""

    def __init__(self, *, tipos=None, modales_al_esperar=(),
                 modales_al_cerrar=(), crea_archivo=True, cierra=True,
                 dialogo_aparece=True, contenido=None):
        self.tipos = list(tipos) if tipos is not None else [
            "Archivos de plantilla de Power BI (*.pbit)",
            "Archivo de Power BI Desktop (*.pbix)",
        ]
        self._modales_al_esperar = list(modales_al_esperar)
        self._modales_al_cerrar = list(modales_al_cerrar)
        self.crea_archivo = crea_archivo
        self.cierra = cierra
        self.dialogo_aparece = dialogo_aparece
        self.contenido = contenido
        self.tipo_elegido = None
        self.ruta_escrita = None
        self.confirmado = False
        self.acelerador = 0

    # -- protocolo ---------------------------------------------------------
    def ventana_principal(self, pid, started):
        return desktop_ui.Ventana(hwnd=11, pid=pid, title="Demo - Power BI",
                                  class_name="PBIDesktopMainWindow")

    def enfocar(self, ventana):
        return True

    def abrir_guardar_como(self, ventana):
        self.acelerador += 1

    def esperar_dialogo_guardado(self, pid, *, timeout):
        if not self.dialogo_aparece:
            raise desktop_ui.DesktopUIError(
                "No aparecio el cuadro de 'Guardar como' en el plazo.",
                details={"reason": "save_dialog_not_found"})
        return desktop_ui.Ventana(hwnd=22, pid=pid, title="Guardar como",
                                  class_name="#32770")

    def tipos_de_archivo(self, dialogo):
        return list(self.tipos)

    def elegir_tipo(self, dialogo, extension):
        objetivo = extension.casefold()
        for texto in self.tipos:
            if objetivo in texto.casefold():
                self.tipo_elegido = texto
                return texto
        raise desktop_ui.DesktopUIError(
            f"El cuadro de guardado no ofrece '{extension}'.",
            details={"available": list(self.tipos),
                     "reason": "file_type_not_offered"})

    def escribir_ruta(self, dialogo, ruta):
        self.ruta_escrita = ruta

    def confirmar(self, dialogo):
        self.confirmado = True
        if self.crea_archivo and self.ruta_escrita:
            destino = Path(self.ruta_escrita)
            destino.parent.mkdir(parents=True, exist_ok=True)
            if self.contenido is not None:
                destino.write_bytes(self.contenido)
            else:
                _escribir_pbix(destino, layout=_layout())

    def esperar_cierre(self, dialogo, *, timeout):
        return self.cierra

    def modales(self, pid, *, excluir=()):
        if self.confirmado:
            return list(self._modales_al_cerrar)
        return list(self._modales_al_esperar)


class _Abierto:
    """Doble de `OpenedPbix`."""

    def __init__(self, ruta, *, launched_by_us=True, port=55001):
        self.pbix_path = str(ruta)
        self.desktop_pid = PID_DESKTOP
        self.launched_by_us = launched_by_us
        self.waited_seconds = 0.0
        self.desktop_started = ARRANQUE
        self.instance = {"pid": 999, "port": port, "host": "localhost",
                         "connection_string": f"Data Source=localhost:{port}",
                         "catalog": "Demo", "status": "ok"}


def _modal(kind="credentials", titulo="Credenciales"):
    return desktop_ui.Modal(hwnd=99, title=titulo, text="texto redactado",
                            kind=kind, suggested_action="haz algo")


@pytest.fixture
def entorno(tmp_path, session, monkeypatch, isolated_settings):
    """Proyecto sintetico activo y toda la capa Desktop sustituida."""
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.powerbi import (desktop_discovery, desktop_identity,
                                         desktop_launcher)
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))

    # Por defecto NADIE tiene el proyecto abierto: que lo este es un caso
    # concreto -y exige confirm_reuse-, no el punto de partida.
    estado = {"abierto_por": None, "identidad": None,
              "sigue_abierto": None}

    def _open_pbix(ruta, timeout=300, reuse_open=True):
        estado["abierto_por"] = str(ruta)
        return _Abierto(ruta)

    def _identify(instancia, target=None):
        if estado["identidad"] is not None:
            return dict(estado["identidad"])
        return {"engine_pid": instancia.get("pid"),
                "desktop_pid": PID_DESKTOP,
                "desktop_process_started": ARRANQUE,
                "desktop_window_title": "Demo - Power BI",
                "project_path": str(target) if target else None,
                "path_match": True, "identity_confidence": "high",
                "identity_evidence": []}

    monkeypatch.setattr(desktop_launcher, "open_pbix", _open_pbix)
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda ruta: PID_DESKTOP
                        if estado["sigue_abierto"]
                        and project_resolver.misma_ruta(
                            ruta, estado["sigue_abierto"]) else None)
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda abierto, force=False: {"closed": True,
                                                      "pid": PID_DESKTOP})
    monkeypatch.setattr(desktop_identity, "identify", _identify)
    monkeypatch.setattr(refresh_mod, "estado_de_datos",
                        lambda *a, **k: {"data_loaded": True})
    monkeypatch.setattr(refresh_mod, "refresh_model",
                        lambda *a, **k: {"duration_ms": 10})
    monkeypatch.setattr(
        desktop_discovery, "select_model",
        lambda s, port=None, **k: type("M", (), {
            "to_dict": lambda self: {"port": port}})())
    return {"pbip": pbip, "estado": estado, "session": session,
            "tmp": tmp_path}


def _exportar(entorno, adapter, **kw):
    kw.setdefault("out_path", str(entorno["tmp"] / "salida" / "Demo.pbix"))
    # Timeout corto: el doble escribe el archivo al instante, y el de
    # produccion (600 s) es el plazo de un guardado real de Power BI.
    kw.setdefault("timeout", 5)
    # Tras guardar, la ventana pasa a servir el .pbix: es lo que hace Desktop.
    entorno["estado"]["sigue_abierto"] = kw["out_path"]
    return pbix_export.export(entorno["session"], adapter=adapter, **kw)


# ================================ 7) el tipo se ELIGE, nunca se hereda ========
def test_el_dialogo_por_defecto_en_pbit_se_fuerza_a_pbix(entorno):
    adapter = _AdaptadorFalso()          # el primero de la lista es .pbit

    salida = _exportar(entorno, adapter)

    assert ".pbix" in adapter.tipo_elegido
    assert ".pbit" not in adapter.tipo_elegido
    assert salida["file_type_selected"] == adapter.tipo_elegido
    assert salida["saved_as_verified"] is True


def test_si_no_ofrecen_pbix_no_se_guarda_con_otro_tipo(entorno):
    adapter = _AdaptadorFalso(tipos=["Plantilla de Power BI (*.pbit)"])

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.details["reason"] == "file_type_not_offered"
    assert adapter.confirmado is False


# ================== 6) una instancia que sirve otro documento se rechaza ======
def test_una_ventana_con_otro_pbit_es_rechazada(entorno):
    entorno["estado"]["identidad"] = {
        "engine_pid": 1, "desktop_pid": PID_DESKTOP,
        "desktop_process_started": ARRANQUE,
        "desktop_window_title": "Otro - Power BI",
        "project_path": str(entorno["tmp"] / "Otro.pbit"),
        "path_match": False, "identity_confidence": "high",
        "identity_evidence": []}
    adapter = _AdaptadorFalso()

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.details["reason"] == "desktop_serves_other_document"
    assert adapter.acelerador == 0, "no se toco la ventana equivocada"


def test_sin_identificar_la_ventana_no_se_conduce(entorno):
    entorno["estado"]["identidad"] = {
        "engine_pid": None, "desktop_pid": None,
        "desktop_process_started": None, "desktop_window_title": None,
        "project_path": None, "path_match": None,
        "identity_confidence": "unknown", "identity_evidence": []}

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso())

    assert "sin saber cual es" in exc.value.message


# ============================== 8) nada de coordenadas =======================
def test_la_automatizacion_no_usa_coordenadas():
    """Un clic en (412, 588) depende del DPI, del monitor y del idioma."""
    fuente = Path(desktop_ui.__file__).read_text(encoding="utf-8")

    for prohibido in ("SetCursorPos", "mouse_event", "SendInput(",
                      "GetCursorPos", "ClientToScreen", "MapWindowPoints"):
        assert prohibido not in fuente, f"aparece {prohibido}"


def test_el_protocolo_no_admite_puntos_de_pantalla():
    import inspect

    for nombre, metodo in inspect.getmembers(desktop_ui.AdaptadorUI,
                                             inspect.isfunction):
        if nombre.startswith("_"):
            continue
        parametros = set(inspect.signature(metodo).parameters)
        assert not parametros & {"x", "y", "punto", "coordenada", "coords"}, \
            f"{nombre} acepta coordenadas"


def test_los_controles_se_resuelven_por_clase_id_y_jerarquia():
    """Los identificadores son los del cuadro REAL, medidos contra Desktop.

    El cuadro moderno no responde a `GetDlgItem(cmb1)`: sus controles cuelgan
    de contenedores intermedios y casi todos llevan id 0. Lo que los
    identifica es la terna clase + id + de quien cuelgan.
    """
    adapter = desktop_ui.Win32UIAdapter()
    arbol = [
        {"hwnd": 10, "class": "FloatNotifySink", "id": 0, "parent": 1},
        {"hwnd": 11, "class": "ComboBox", "id": 0, "parent": 10},
        {"hwnd": 12, "class": "Edit", "id": 0x03E9, "parent": 11},
        {"hwnd": 20, "class": "FloatNotifySink", "id": 0, "parent": 1},
        {"hwnd": 21, "class": "ComboBox", "id": 0, "parent": 20},
        {"hwnd": 30, "class": "Button", "id": 1, "parent": 1},
        # La barra de direcciones: mismo patron, otro id. No debe confundirse.
        {"hwnd": 40, "class": "ComboBox", "id": 0xA205, "parent": 1},
        {"hwnd": 41, "class": "Edit", "id": 0xA205, "parent": 40},
    ]

    assert adapter._edicion_de_nombre_en(arbol) == 12
    assert adapter._boton_guardar_en(arbol, dialogo=1) == 30
    assert desktop_ui.IDC_NOMBRE_EDIT == 0x03E9
    assert desktop_ui.IDOK == 1


def test_f12_es_el_acelerador_que_funciona():
    """Medido contra Power BI Desktop: `Ctrl+Shift+S` no abre nada."""
    assert desktop_ui.Win32UIAdapter.ACELERADORES_GUARDAR_COMO[0] == ("f12",)
    assert ("ctrl", "shift", "s") in         desktop_ui.Win32UIAdapter.ACELERADORES_GUARDAR_COMO


# ============================ 9 y 10) el destino existente ===================
def test_overwrite_false_preserva_el_destino(entorno):
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"entregable anterior")

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso(), out_path=str(destino))

    assert "ya existe" in exc.value.message
    assert destino.read_bytes() == b"entregable anterior"


def test_overwrite_false_ni_siquiera_abre_desktop(entorno):
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"x")

    with pytest.raises(PowerBIMCPError):
        _exportar(entorno, _AdaptadorFalso(), out_path=str(destino))

    assert entorno["estado"]["abierto_por"] is None


def test_overwrite_true_respalda_y_restaura_si_falla(entorno,
                                                     isolated_settings):
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"entregable anterior")
    # El guardado "ocurre" pero deja basura: la verificacion lo cazara.
    adapter = _AdaptadorFalso(contenido=b"")

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter, out_path=str(destino), overwrite=True)

    assert destino.read_bytes() == b"entregable anterior", "no se restauro"
    respaldos = list((isolated_settings.backups_dir / "pbix_export").glob("*"))
    assert respaldos, "no se creo respaldo del destino que se iba a reemplazar"
    assert exc.value.details.get("restore", {}).get("restored") is True


def test_si_la_restauracion_falla_se_dice_y_no_se_esconde(entorno,
                                                          monkeypatch):
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(b"entregable anterior")
    monkeypatch.setattr(pbix_export, "_restaurar",
                        lambda previo: {"restored": False,
                                        "error": "disco lleno",
                                        "action_required": "recuperala a mano"})

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso(contenido=b""),
                  out_path=str(destino), overwrite=True)

    assert exc.value.code == "pbix_restore_failed"
    assert "Requiere intervencion" in exc.value.message


# ================= 11) no hay exito sin archivo inspeccionable ===============
def test_sin_archivo_no_hay_exito(entorno):
    """Que el dialogo se cierre no es que el archivo exista.

    Power BI Desktop cierra el cuadro y escribe DESPUES, asi que aqui se
    espera a la escritura; si nunca aparece nada, el fallo dice exactamente
    eso en vez de fingir que se guardo algo ilegible.
    """
    adapter = _AdaptadorFalso(crea_archivo=False)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.details["reason"] == "save_did_not_finish"
    assert exc.value.details["appeared"] is False
    assert "no se pudo ver terminar" in exc.value.message


def test_un_archivo_a_medio_escribir_no_se_da_por_bueno(tmp_path):
    """Existir no es haber terminado: un .pbix a medias no se acepta."""
    destino = tmp_path / "creciendo.pbix"
    destino.write_bytes(b"x" * 1024)

    class _QueCrece:
        """Cada consulta del tamano devuelve uno mayor."""

        def __init__(self):
            self.n = 1

        def __call__(self, *_a, **_k):
            self.n += 1
            destino.write_bytes(b"x" * (self.n * 1024))
            return True

    import builtins

    original = Path.is_file
    crece = _QueCrece()
    try:
        Path.is_file = lambda self: (crece() if self == destino
                                     else original(self))
        espera = pbix_export.esperar_escritura_terminada(destino, timeout=3)
    finally:
        Path.is_file = original

    assert espera["stable"] is False
    assert espera["appeared"] is True
    assert "no dejo de crecer" in espera["wait_reason"]


def test_un_archivo_estable_si_se_acepta(tmp_path):
    destino = tmp_path / "quieto.pbix"
    destino.write_bytes(b"x" * 2048)

    espera = pbix_export.esperar_escritura_terminada(destino, timeout=10)

    assert espera["stable"] is True
    assert espera["size"] == 2048


def test_un_archivo_que_no_se_puede_inspeccionar_no_es_entregable(entorno):
    adapter = _AdaptadorFalso(contenido=b"esto no es un zip")

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.code == "pbix_export_not_verified"
    assert exc.value.details["checks"]["size"] > 0


def test_un_archivo_de_antes_no_cuenta_como_guardado(entorno, monkeypatch):
    """`mtime` anterior a esta ejecucion: lo que hay ahi no lo escribimos."""
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    destino.parent.mkdir(parents=True)
    _escribir_pbix(destino, layout=_layout())
    viejo = time.time() - 86_400
    import os

    os.utime(destino, (viejo, viejo))
    adapter = _AdaptadorFalso(crea_archivo=False)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter, out_path=str(destino), overwrite=True)

    assert exc.value.code == "pbix_export_not_verified"
    assert "ANTERIOR a esta ejecucion" in exc.value.message


def test_la_respuesta_minima_esta_completa(entorno):
    salida = _exportar(entorno, _AdaptadorFalso())

    for clave in ("source_pbip", "output_pbix", "output_sha256",
                  "output_size", "saved_as_verified", "opened_path_verified"):
        assert clave in salida, f"falta {clave}"
    assert salida["output_size"] > 0
    assert len(salida["output_sha256"]) == 64
    assert salida["saved_as_verified"] is True


# ========================= 12 y 13) el estado final ==========================
def test_leave_open_deja_abierto_exactamente_el_pbix(entorno):
    salida = _exportar(entorno, _AdaptadorFalso(), leave_open=True)

    assert salida["final_state"]["leave_open"] is True
    assert salida["opened_path_verified"] is True
    assert salida["final_state"]["same_window_followed"] is True
    assert salida["final_state"]["selected"] is True


def test_si_la_ventana_no_sigue_al_pbix_se_abre_el_entregable(entorno):
    # La ventana se queda en el .pbip: Desktop no siempre reapunta. Y como
    # el proyecto YA estaba abierto, hace falta permiso explicito.
    entorno["estado"]["sigue_abierto"] = str(entorno["pbip"])
    salida = pbix_export.export(
        entorno["session"], adapter=_AdaptadorFalso(),
        out_path=str(entorno["tmp"] / "salida" / "Demo.pbix"),
        leave_open=True, confirm_reuse=True, timeout=5)

    assert salida["final_state"]["reopened"] is True
    assert salida["final_state"]["opened_path_verified"] is True


def test_leave_open_false_no_cierra_ventanas_del_usuario(entorno,
                                                          monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_launcher

    cerradas = []
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda abierto, force=False: cerradas.append(abierto)
                        or {"closed": True})
    # La sesion NO la abrimos nosotros: es del usuario.
    monkeypatch.setattr(desktop_launcher, "open_pbix",
                        lambda ruta, timeout=300, reuse_open=True:
                        _Abierto(ruta, launched_by_us=False))

    salida = _exportar(entorno, _AdaptadorFalso(), leave_open=False)

    assert cerradas == [], "se cerro una ventana que no abrimos"
    assert salida["final_state"]["closed"]["closed"] is False
    assert "del usuario" in salida["final_state"]["closed"]["reason"]


def test_leave_open_false_si_cierra_lo_que_abrimos(entorno, monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_launcher

    cerradas = []
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda abierto, force=False: cerradas.append(abierto)
                        or {"closed": True})

    _exportar(entorno, _AdaptadorFalso(), leave_open=False)

    assert len(cerradas) == 1


# ========================= 14) un modal NO es un timeout =====================
def test_un_modal_produce_diagnostico_no_timeout(entorno):
    adapter = _AdaptadorFalso(dialogo_aparece=False,
                              modales_al_esperar=[_modal("credentials")])

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.code == "desktop_modal"
    assert "no es que se haya agotado el tiempo" in exc.value.message
    assert exc.value.details["modals"][0]["kind"] == "credentials"
    assert exc.value.details["modals"][0]["suggested_action"]


def test_sin_modal_un_dialogo_ausente_si_es_lo_que_es(entorno):
    adapter = _AdaptadorFalso(dialogo_aparece=False)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.details["reason"] == "save_dialog_not_found"


def test_un_modal_tras_confirmar_detiene_el_guardado(entorno):
    adapter = _AdaptadorFalso(modales_al_cerrar=[_modal("save_failed")],
                              crea_archivo=False)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, adapter)

    assert exc.value.code == "desktop_modal"
    assert "No se cierra automaticamente" in exc.value.message


@pytest.mark.parametrize("titulo,texto,esperado", [
    ("Credenciales", "Escribe tu usuario", "credentials"),
    ("Error", "No se pudo cargar el origen", "data_load_error"),
    ("Power BI", "¿Quieres guardar los cambios?", "unsaved_changes"),
    ("Confirmar Guardar como", "El archivo ya existe", "confirm_replace"),
    ("Error", "La ruta es demasiado larga", "path_too_long"),
    ("Error", "El archivo esta en uso por otro programa", "file_locked"),
    ("Error", "No se pudo guardar el archivo", "save_failed"),
    ("Aviso", "Algo distinto", "unknown"),
])
def test_los_modales_se_clasifican(titulo, texto, esperado):
    clase, accion = desktop_ui.clasificar_modal(titulo, texto)

    assert clase == esperado
    assert accion


def test_el_texto_de_un_modal_se_redacta():
    import os

    casa = os.path.expanduser("~")
    salida = desktop_ui.redactar(f"No se pudo abrir {casa}\\Informes\\x.pbix")

    assert casa not in salida
    assert "~" in salida


# ============ 15) un fallo no cambia el proyecto activo en silencio ==========
def test_un_fallo_no_cambia_el_proyecto_activo(entorno):
    antes = entorno["session"].require_active_pbip().pbip_path

    with pytest.raises(PowerBIMCPError):
        _exportar(entorno, _AdaptadorFalso(crea_archivo=False))

    assert entorno["session"].require_active_pbip().pbip_path == antes


def test_un_preflight_fallido_no_toca_la_sesion(entorno):
    antes = entorno["session"].require_active_pbip().pbip_path

    with pytest.raises(PowerBIMCPError):
        pbix_export.export(entorno["session"], adapter=_AdaptadorFalso(),
                           timeout=5,
                           out_path=str(entorno["tmp"] / "malo.pbit"))

    assert entorno["session"].require_active_pbip().pbip_path == antes


# ================================= la ventana ajena ==========================
def test_una_ventana_del_usuario_exige_confirm_reuse(entorno, monkeypatch):
    from horizun_pbi_mcp.powerbi import desktop_launcher

    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda ruta: PID_DESKTOP)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso())

    assert exc.value.details["reason"] == "desktop_session_belongs_to_user"
    assert "confirm_reuse=true" in exc.value.message


# ===================================== el refresco ===========================
def test_refresh_required_no_exporta_si_no_pudo_refrescar(entorno,
                                                          monkeypatch):
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    def _explota(*a, **k):
        raise PowerBIMCPError("el origen no responde")

    monkeypatch.setattr(refresh_mod, "refresh_model", _explota)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso(), refresh="required")

    assert "no se exporta un .pbix presentandolo como entregable" \
        in exc.value.message


def test_refresh_auto_declara_el_fallo_sin_ocultarlo(entorno, monkeypatch):
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    def _explota(*a, **k):
        raise PowerBIMCPError("el origen no responde")

    monkeypatch.setattr(refresh_mod, "refresh_model", _explota)

    salida = _exportar(entorno, _AdaptadorFalso(), refresh="auto")

    assert salida["refresh_requested"] == "auto"
    assert salida["refresh_checked"] is True
    assert salida["refresh_succeeded"] is False
    assert any("NO se afirma" in w for w in salida["warnings"])


def test_refresh_skip_no_refresca_y_lo_dice(entorno, monkeypatch):
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    llamadas = []
    monkeypatch.setattr(refresh_mod, "refresh_model",
                        lambda *a, **k: llamadas.append(1) or {})

    salida = _exportar(entorno, _AdaptadorFalso(), refresh="skip")

    assert llamadas == []
    assert salida["refresh_checked"] is False
    assert any("skip" in w for w in salida["warnings"])


def test_un_refresh_invalido_se_rechaza(entorno):
    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso(), refresh="quizas")

    assert exc.value.details["valid"] == list(pbix_export.MODOS_REFRESH)


# ================================ el preflight ==============================
def test_una_ruta_demasiado_larga_se_para_antes_de_abrir(entorno):
    largo = entorno["tmp"] / ("x" * 200) / "Demo.pbix"

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _AdaptadorFalso(), out_path=str(largo))

    assert "caracteres mas corta" in exc.value.message
    assert entorno["estado"]["abierto_por"] is None


def test_exportar_exige_un_pbip_no_un_pbix(entorno, tmp_path):
    origen = _escribir_pbix(tmp_path / "Suelto.pbix", layout=_layout())

    with pytest.raises(PowerBIMCPError) as exc:
        pbix_export.export(entorno["session"], pbip_path=str(origen),
                           adapter=_AdaptadorFalso(), timeout=5,
                           out_path=str(tmp_path / "out.pbix"))

    assert "pbi_prepare_project" in exc.value.message


# =========================== finalize_delivery ==============================
def test_finalize_delivery_entrega_de_extremo_a_extremo(entorno):
    destino = entorno["tmp"] / "salida" / "Demo.pbix"
    entorno["estado"]["sigue_abierto"] = str(destino)
    salida = pbix_export.finalize_delivery(
        entorno["session"], path=str(entorno["pbip"]),
        out_path=str(destino), adapter=_AdaptadorFalso())

    assert salida["delivered"] is True
    assert salida["format"] == "pbix"
    assert salida["saved_as_verified"] is True
    assert salida["prepare"]["path_match"] is True


def test_finalize_delivery_rechaza_un_formato_que_no_existe(entorno):
    """`pbit` ya se produce de verdad; lo que sigue sin fingirse es lo demas."""
    with pytest.raises(PowerBIMCPError) as exc:
        pbix_export.finalize_delivery(entorno["session"], format="docx",
                                      adapter=_AdaptadorFalso())

    assert exc.value.details["valid"] == ["pbix", "pbit"]


def test_los_defaults_de_la_tool_nueva_son_los_pedidos():
    import inspect

    from horizun_pbi_mcp.tools import workflow_tools

    registradas = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                registradas[fn.__name__] = fn
                return fn
            return deco

    workflow_tools.register(_Mcp())
    firma = inspect.signature(registradas["pbi_finalize_delivery"])

    assert firma.parameters["format"].default == "pbix"
    assert firma.parameters["leave_open"].default is True
    assert firma.parameters["refresh"].default == "auto"
    assert firma.parameters["overwrite"].default is False


@pytest.mark.parametrize("tool", ["pbi_export_pbix", "pbi_finalize_delivery",
                                  "pbi_prepare_project"])
def test_ninguna_tool_nueva_es_de_solo_lectura(tool):
    from horizun_pbi_mcp.tools import risk

    assert risk.RISK_BY_TOOL[tool] not in risk.CLASES_DE_LECTURA
    assert risk.annotations_for(tool)["readOnlyHint"] is False


# ==================================== live ==================================
#: La prueba live abre una VENTANA real. Se exige activarla a proposito: que la
#: maquina tenga Power BI Desktop instalado no es permiso para ponerse a abrir
#: ventanas en mitad de una suite.
LIVE_EXPORT = (os.environ.get("PBI_MCP_LIVE_EXPORT", "").strip().casefold()
               in {"1", "true", "si", "yes"})

RAZON_LIVE = (
    "Abre una ventana REAL de Power BI Desktop, asi que esta desactivada por "
    "defecto. Para ejecutarla en PowerShell:\n"
    '    $env:PBI_MCP_LIVE_EXPORT = "1"\n'
    "    try { python -m pytest -vv -s -m live tests/test_exportacion_pbix.py }\n"
    "    finally { Remove-Item Env:PBI_MCP_LIVE_EXPORT "
    "-ErrorAction SilentlyContinue }")


def _hay_desktop() -> bool:
    if os.name != "nt":
        return False
    try:
        from horizun_pbi_mcp.powerbi import desktop_launcher

        desktop_launcher.find_executable()
        return True
    except Exception:                                     # noqa: BLE001
        return False


def censo_desktop() -> dict:
    """{pid: create_time} de todos los `PBIDesktop.exe` de ahora mismo.

    La hora de arranque va en el censo a proposito: Windows recicla PIDs, y un
    PID que "ya estaba" puede ser en realidad un proceso nuevo con el numero
    de uno muerto. Sin ese segundo dato, la limpieza podria cerrar la ventana
    de otra persona creyendo que es la suya.
    """
    import psutil

    censo = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info.get("name") or "").casefold() == "pbidesktop.exe":
                censo[proc.info["pid"]] = float(proc.create_time())
        except Exception:                                 # noqa: BLE001
            continue
    return censo


def _nuestros_procesos(antes: dict, sandbox: Path) -> list:
    """Procesos de Desktop que NO estaban antes y viven en nuestro sandbox.

    Se exige que la linea de comandos apunte dentro de `tmp_path`. Un proceso
    nuevo que no lo demuestre no se toca: pudo abrirlo el usuario mientras la
    prueba corria, y cerrar eso seria justo lo que esta prohibido.
    """
    import psutil

    sandbox_norm = str(sandbox).casefold()
    nuestros = []
    for pid, creado in censo_desktop().items():
        if pid in antes and abs(antes[pid] - creado) < 1.0:
            continue                                      # preexistente
        try:
            proc = psutil.Process(pid)
            linea = " ".join(str(c) for c in (proc.cmdline() or []))
        except Exception:                                 # noqa: BLE001
            continue
        if sandbox_norm in linea.casefold():
            nuestros.append((pid, creado))
    return nuestros


def cerrar_solo_lo_nuestro(antes: dict, sandbox: Path) -> list:
    """Cierra unicamente lo que lanzo la prueba. Revalida antes de matar."""
    import psutil

    cerrados = []
    for pid, creado in _nuestros_procesos(antes, sandbox):
        try:
            proc = psutil.Process(pid)
            # Tres comprobaciones ANTES de terminar nada: que siga siendo
            # Desktop, que sea el mismo arranque y que siga en el sandbox.
            if (proc.name() or "").casefold() != "pbidesktop.exe":
                continue
            if abs(float(proc.create_time()) - creado) > 1.0:
                continue
            linea = " ".join(str(c) for c in (proc.cmdline() or []))
            if str(sandbox).casefold() not in linea.casefold():
                continue
            hijos = proc.children(recursive=True)
            for objetivo in [*hijos, proc]:
                try:
                    objetivo.terminate()
                except Exception:                         # noqa: BLE001
                    continue
            psutil.wait_procs([*hijos, proc], timeout=30)
            cerrados.append(pid)
        except Exception:                                 # noqa: BLE001
            continue
    return cerrados


@pytest.mark.live
@pytest.mark.skipif(not (LIVE_EXPORT and _hay_desktop()), reason=RAZON_LIVE)
def test_live_pbip_a_pbix_por_el_servicio(tmp_path, session, live_settings):
    """El camino completo contra Power BI Desktop REAL, por el servicio.

    Ejercita `Win32UIAdapter`: abre el proyecto sintetico, lo guarda como
    `.pbix` conduciendo la interfaz, lo inspecciona y comprueba que la ventana
    que queda abierta sirve el archivo generado.

    Todo ocurre dentro de `tmp_path`. La limpieza va en `finally` y cierra
    EXCLUSIVAMENTE los procesos que esta prueba lanzo, revalidando su
    identidad justo antes de terminarlos.
    """
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.powerbi import desktop_launcher

    antes = censo_desktop()
    print(f"\n[live] procesos PBIDesktop preexistentes: {sorted(antes)}")

    pbip = synthetic.materialize(tmp_path, name="desktop_openable")
    project_locator.open_project(session, str(pbip))
    destino = tmp_path / "Entregable.pbix"
    creados = []

    try:
        salida = pbix_export.export(session, out_path=str(destino),
                                    refresh="skip", leave_open=True,
                                    timeout=900)

        creados = _nuestros_procesos(antes, tmp_path)
        print(f"[live] procesos lanzados por la prueba: {creados}")
        print(f"[live] tipo de archivo elegido: {salida.get('file_type_selected')}")

        assert salida["saved_as_verified"] is True
        assert salida["verification"]["extension"] == ".pbix"
        assert salida["output_size"] > 0
        assert len(salida["output_sha256"]) == 64
        assert destino.is_file()
        # La ventana que queda abierta sirve el PBIX generado, no el .pbip.
        assert salida["opened_path_verified"] is True
        abierto_ahora = desktop_launcher.proceso_con_archivo_abierto(destino)
        assert abierto_ahora, "el .pbix generado deberia quedar abierto"
    finally:
        cerrados = cerrar_solo_lo_nuestro(antes, tmp_path)
        print(f"[live] cerrados en finally: {cerrados}")
        despues = censo_desktop()
        nuevos = [p for p in despues
                  if p not in antes or abs(despues[p] - antes.get(p, 0)) > 1.0]
        print(f"[live] procesos nuevos que sobreviven: {nuevos}")
        assert not _nuestros_procesos(antes, tmp_path), (
            "quedo vivo un proceso de Desktop lanzado por la prueba")


@pytest.mark.live
@pytest.mark.skipif(not (LIVE_EXPORT and _hay_desktop()), reason=RAZON_LIVE)
def test_live_finalize_delivery_por_la_tool_publica(tmp_path, session,
                                                    live_settings,
                                                    monkeypatch):
    """La aceptacion: el workflow PUBLICO que va a usar un LLM.

    Probar solo el servicio interno deja fuera justo lo que un cliente ve: el
    envelope de `guard()`, el nombre de los campos y el `ok`. Esta prueba
    atraviesa `call_tool` como cualquier otra llamada MCP.
    """
    import asyncio

    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.powerbi import desktop_launcher
    from horizun_pbi_mcp.server import build_server

    antes = censo_desktop()
    print(f"\n[live] procesos PBIDesktop preexistentes: {sorted(antes)}")

    pbip = synthetic.materialize(tmp_path, name="desktop_openable")
    destino = tmp_path / "EntregaPublica.pbix"
    monkeypatch.setattr(cfg, "_session", session)

    try:
        respuesta = asyncio.run(build_server().call_tool(
            "pbi_finalize_delivery",
            {"path": str(pbip), "format": "pbix", "refresh": "skip",
             "leave_open": True, "out_path": str(destino),
             "overwrite": False}))
        payload = respuesta[1] if isinstance(respuesta, tuple) else respuesta
        if isinstance(payload, dict) and "result" in payload:
            payload = payload["result"]

        print(f"[live] ok={payload.get('ok')} "
              f"saved={payload.get('saved_as_verified')} "
              f"opened={payload.get('opened_path_verified')}")

        # -- lo que un cliente ve, en la forma en que lo ve ------------------
        assert payload["ok"] is True, payload.get("message")
        assert project_resolver.misma_ruta(payload["source_pbip"], pbip)
        assert project_resolver.misma_ruta(payload["output_pbix"], destino)
        assert payload["saved_as_verified"] is True
        assert payload["opened_path_verified"] is True
        assert payload["verification"]["extension"] == ".pbix"
        assert payload["output_size"] > 0
        assert len(payload["output_sha256"]) == 64
        assert payload["pbix_summary"]["report_format"] != "none"
        assert payload["delivered"] is True

        # -- el modelo activo es el del PBIX generado ------------------------
        activo = payload["final_state"].get("active_model") or {}
        assert payload["final_state"]["selected"] is True, payload["final_state"]
        assert activo.get("port"), "no quedo un modelo activo"
        assert desktop_launcher.proceso_con_archivo_abierto(destino), (
            "el entregable deberia quedar abierto antes de la limpieza")
    finally:
        cerrados = cerrar_solo_lo_nuestro(antes, tmp_path)
        print(f"[live] cerrados en finally: {cerrados}")
        assert not _nuestros_procesos(antes, tmp_path), (
            "quedo vivo un proceso de Desktop lanzado por la prueba")


@pytest.mark.live
@pytest.mark.skipif(not (LIVE_EXPORT and _hay_desktop()), reason=RAZON_LIVE)
def test_live_la_limpieza_no_toca_lo_preexistente(tmp_path):
    """La red de seguridad de las dos de arriba, comprobada aparte.

    Si `cerrar_solo_lo_nuestro` pudiera cerrar una ventana que ya estaba, las
    otras dos pruebas serian peligrosas de ejecutar. Aqui se comprueba con el
    censo real: sin procesos nuevos en el sandbox, no cierra nada.
    """
    antes = censo_desktop()

    assert cerrar_solo_lo_nuestro(antes, tmp_path) == []
    assert censo_desktop().keys() == antes.keys()


# ============= el desenlace que Power BI produce de verdad ===================
def test_si_desktop_guarda_un_proyecto_se_dice_lo_que_paso(entorno):
    """Medido contra Power BI Desktop real: con un .pbip abierto, `Guardar
    como` produce `X.pbix.pbip` mas sus carpetas, aunque el desplegable de
    tipo diga `.pbix`.

    Sin esta deteccion el fallo se reportaba como "el archivo no esta en el
    destino": cierto e inutil. La persona mira la carpeta, ve tres cosas con
    su nombre y no entiende nada.
    """
    destino = entorno["tmp"] / "salida" / "Demo.pbix"

    class _GuardaProyecto(_AdaptadorFalso):
        def confirmar(self, dialogo):
            self.confirmado = True
            base = Path(self.ruta_escrita)
            base.parent.mkdir(parents=True, exist_ok=True)
            Path(str(base) + ".pbip").write_text("{}", encoding="utf-8")
            Path(str(base) + ".Report").mkdir(exist_ok=True)
            Path(str(base) + ".SemanticModel").mkdir(exist_ok=True)

    with pytest.raises(PowerBIMCPError) as exc:
        _exportar(entorno, _GuardaProyecto(), out_path=str(destino))

    assert exc.value.code == "pbix_wrong_format"
    assert exc.value.details["reason"] == "saved_in_wrong_format"
    assert any(f.endswith(".pbip") for f in exc.value.details["found"])
    assert "no es un entregable" in exc.value.message
    assert not destino.exists()


def test_un_guardado_correcto_no_dispara_esa_deteccion(entorno):
    salida = _exportar(entorno, _AdaptadorFalso())

    assert salida["saved_as_verified"] is True
    assert pbix_export.artefacto_de_otro_formato(
        Path(salida["output_pbix"])) is None
