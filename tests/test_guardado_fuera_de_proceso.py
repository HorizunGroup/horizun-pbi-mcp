"""El guardado conducido desde OTRO proceso, y por que tuvo que ser asi.

Estas pruebas cubren los tres defectos que hicieron que la exportacion pareciera
imposible durante toda una tanda de intentos, y que no eran de Power BI:

1. El adaptador real no ofrecia el guardado completo. La implementacion habia
   quedado escrita dentro del `Protocol` -que nadie hereda-, asi que el
   servicio miraba `hasattr(...)`, veia que no, y se iba por el camino Win32
   que NO compromete el tipo. El combo decia `.pbix` y salia un `.pbip`.
2. Las estructuras `INPUT` de `SendInput` se creaban en cada llamada, asi que
   ctypes veia dos clases distintas con el mismo nombre y el nombre del archivo
   no llegaba a teclearse nunca.
3. La espera de la escritura usaba el plazo global de la operacion. Un guardado
   que no iba a ocurrir jamas se pasaba un cuarto de hora mirando una carpeta
   vacia en vez de decir lo que pasaba.

Nada de esto abre una ventana: la parte que toca Windows se ejercita en las
pruebas `live`.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import desktop_helper, desktop_ui
from horizun_pbi_mcp.services import pbix_export

RAIZ = Path(__file__).resolve().parent.parent


# ============ 1) el adaptador REAL es el que tiene que ofrecerlo =============
def test_el_adaptador_real_ofrece_el_guardado_completo():
    """Declararlo en el `Protocol` no lo implementa en nadie.

    `AdaptadorUI` es un Protocol y `Win32UIAdapter` no lo hereda: un metodo con
    cuerpo escrito ahi dentro es codigo muerto que ademas engaña, porque leerlo
    da la impresion de que el adaptador lo tiene.
    """
    adapter = desktop_ui.Win32UIAdapter()
    assert hasattr(adapter, "save_as_completo")
    assert callable(adapter.save_as_completo)


def test_el_protocolo_solo_declara_y_el_adaptador_implementa():
    """El Protocol describe la forma; los cuerpos viven en el adaptador.

    Se comprueba sobre el ARBOL sintactico y no buscando `...` al final del
    texto: los cuerpos son ahora su docstring -equivalente, y ademas dice para
    que sirve cada metodo-, asi que un `endswith("...")` daria verde para
    cualquier cosa terminada en puntos suspensivos.
    """
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(desktop_ui.AdaptadorUI))
    clase = arbol.body[0]

    for nodo in clase.body:
        if not isinstance(nodo, ast.FunctionDef) or nodo.name.startswith("_"):
            continue
        cuerpo = [s for s in nodo.body
                  if not (isinstance(s, ast.Expr)
                          and isinstance(s.value, ast.Constant)
                          and isinstance(s.value.value, str))]
        assert cuerpo == [], (
            f"{nodo.name} tiene implementacion dentro del Protocol; ahi no la "
            "hereda nadie, asi que es codigo muerto que ademas engaña al leerlo")


# ================= 2) el servicio PREFIERE el proceso aparte =================
class _EspiaConHelper:
    """Adaptador que ofrece el guardado completo y delata lo granular."""

    def __init__(self, destino: Path):
        self.destino = destino
        self.completo = 0
        self.granular = []
        self.recibido = None

    def save_as_completo(self, *, pid, started, destino, extension=".pbix",
                         timeout=180.0):
        self.completo += 1
        self.recibido = {"pid": pid, "destino": destino,
                         "extension": extension, "timeout": timeout}
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
        Path(destino).write_bytes(b"PK\x03\x04datos")
        return {"file_type_selected": "Archivo de Power BI (*.pbix)",
                "commit_method": "invoke", "dialog_closed": True,
                "steps": [], "modals": []}

    # Lo granular NO debe usarse cuando existe el guardado completo.
    def ventana_principal(self, pid, started):
        self.granular.append("ventana_principal")
        raise AssertionError("no deberia recorrerse el camino paso a paso")

    def elegir_tipo(self, dialogo, extension):
        self.granular.append("elegir_tipo")
        raise AssertionError("CB_SETCURSEL no compromete el tipo")

    def escribir_ruta(self, dialogo, ruta):
        self.granular.append("escribir_ruta")
        raise AssertionError("no deberia escribirse por mensajes Win32")

    def confirmar(self, dialogo):
        self.granular.append("confirmar")
        raise AssertionError("no deberia confirmarse por BM_CLICK")

    def modales(self, pid, *, excluir=()):
        return []


def test_el_servicio_usa_el_proceso_aparte_y_no_el_camino_win32(tmp_path):
    destino = tmp_path / "Entrega.pbix"
    espia = _EspiaConHelper(destino)

    resultado = pbix_export._guardar_como(                # noqa: SLF001
        espia, pid=4321, started=1000.0, destino=destino, timeout=30)

    assert espia.completo == 1
    assert espia.granular == []
    assert resultado["commit_method"] == "invoke"
    assert resultado["write_wait"]["stable"] is True


def test_el_camino_paso_a_paso_sigue_disponible_para_el_doble(tmp_path):
    """Sin `save_as_completo` se recorre lo granular: es lo que prueba la suite."""

    class _SinHelper:
        def __init__(self):
            self.pasos = []

        def ventana_principal(self, pid, started):
            self.pasos.append("ventana")
            return desktop_ui.Ventana(hwnd=11, pid=pid, title="Demo",
                                      class_name="X")

        def modales(self, pid, *, excluir=()):
            return []

        def abrir_guardar_como(self, ventana):
            self.pasos.append("abrir")

        def esperar_dialogo_guardado(self, pid, *, timeout):
            return desktop_ui.Ventana(hwnd=22, pid=pid, title="Guardar como",
                                      class_name="#32770")

        def tipos_de_archivo(self, dialogo):
            return ["Archivo de Power BI (*.pbix)"]

        def elegir_tipo(self, dialogo, extension):
            self.pasos.append("tipo")
            return "Archivo de Power BI (*.pbix)"

        def escribir_ruta(self, dialogo, ruta):
            self.pasos.append("ruta")
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
            Path(ruta).write_bytes(b"PK\x03\x04datos")

        def confirmar(self, dialogo):
            self.pasos.append("confirmar")

        def esperar_cierre(self, dialogo, *, timeout):
            return True

    doble = _SinHelper()
    assert not hasattr(doble, "save_as_completo")
    pbix_export._guardar_como(                            # noqa: SLF001
        doble, pid=1, started=None, destino=tmp_path / "a.pbix", timeout=5)
    assert doble.pasos == ["ventana", "abrir", "tipo", "ruta", "confirmar"]


# ============== 3) el tipo se juzga por el ARCHIVO, no por el combo ==========
def test_el_texto_del_combo_no_basta_si_salio_un_proyecto(tmp_path):
    """Que el desplegable diga `.pbix` no es que se guardara un `.pbix`."""

    class _DiceQueSiPeroGuardaProyecto(_EspiaConHelper):
        def save_as_completo(self, *, pid, started, destino, extension=".pbix",
                             timeout=180.0):
            proyecto = Path(str(destino) + ".pbip")
            proyecto.parent.mkdir(parents=True, exist_ok=True)
            proyecto.write_text("{}", encoding="utf-8")
            return {"file_type_selected": "Archivo de Power BI (*.pbix)",
                    "commit_method": "invoke", "dialog_closed": True,
                    "steps": [], "modals": []}

    destino = tmp_path / "Entrega.pbix"
    with pytest.raises(pbix_export.PbixWrongFormatError) as fallo:
        pbix_export._guardar_como(                        # noqa: SLF001
            _DiceQueSiPeroGuardaProyecto(destino), pid=1, started=None,
            destino=destino, timeout=3, origen=None)

    assert fallo.value.details["reason"] == "saved_in_wrong_format"
    assert not destino.exists()


def test_un_archivo_en_la_carpeta_del_pbip_se_dice_donde_quedo(tmp_path):
    """Desktop guarda donde le indique la ruta; si solo hay nombre, manda otra."""
    origen = tmp_path / "proyecto" / "Demo.pbip"
    origen.parent.mkdir(parents=True)
    origen.write_text("{}", encoding="utf-8")
    destino = tmp_path / "entrega" / "Demo.pbix"
    destino.parent.mkdir()
    (origen.parent / "Demo.pbix").write_bytes(b"PK\x03\x04")

    with pytest.raises(pbix_export.PbixWrongFormatError) as fallo:
        pbix_export.esperar_escritura_terminada(destino, timeout=2, gracia=1,
                                                origen=origen)

    detalles = fallo.value.details
    assert detalles["reason"] == "saved_in_source_folder"
    assert detalles["found"].endswith("Demo.pbix")


def test_no_se_rastrea_el_disco_buscando_un_nombre_parecido(tmp_path):
    """Solo dos carpetas: la pedida y la del proyecto. Ni una mas."""
    origen = tmp_path / "proyecto" / "Demo.pbip"
    origen.parent.mkdir(parents=True)
    otra = tmp_path / "otra_ejecucion"
    otra.mkdir()
    (otra / "Demo.pbix").write_bytes(b"PK\x03\x04")       # de OTRA corrida

    hallado = pbix_export.artefacto_extraviado(
        tmp_path / "entrega" / "Demo.pbix", origen)
    assert hallado is None


def test_si_destino_y_origen_son_la_misma_carpeta_no_se_reporta_extravio(
        tmp_path):
    origen = tmp_path / "Demo.pbip"
    origen.write_text("{}", encoding="utf-8")
    (tmp_path / "Demo.pbix").write_bytes(b"PK\x03\x04")
    assert pbix_export.artefacto_extraviado(tmp_path / "Demo.pbix",
                                            origen) is None


def test_un_cuadro_que_sigue_abierto_se_dice_asi_y_no_como_archivo_ausente(
        tmp_path):
    """Dos fallos distintos merecen dos explicaciones distintas.

    Si el cuadro no se cerro, decir "el archivo nunca aparecio" manda a mirar
    la carpeta cuando lo que hay que mirar es la pantalla.
    """

    class _NoCierra(_EspiaConHelper):
        def save_as_completo(self, *, pid, started, destino, extension=".pbix",
                             timeout=180.0):
            return {"file_type_selected": "Archivo de Power BI (*.pbix)",
                    "commit_method": "invoke", "dialog_closed": False,
                    "steps": [], "modals": []}

    destino = tmp_path / "Entrega.pbix"
    with pytest.raises(pbix_export.PbixExportError) as fallo:
        pbix_export._guardar_como(                        # noqa: SLF001
            _NoCierra(destino), pid=1, started=None, destino=destino,
            timeout=3)
    assert fallo.value.details["reason"] == "save_dialog_still_open"


def test_un_modal_del_helper_se_propaga_como_modal(tmp_path):
    """Un cuadro visible es la explicacion, no un plazo agotado."""

    class _ConModal(_EspiaConHelper):
        def save_as_completo(self, *, pid, started, destino, extension=".pbix",
                             timeout=180.0):
            return {"file_type_selected": "Archivo de Power BI (*.pbix)",
                    "commit_method": "invoke", "dialog_closed": True,
                    "steps": [],
                    "modals": [{"kind": "overwrite_confirm",
                                "title": "Confirmar guardado"}]}

    with pytest.raises(desktop_ui.DesktopModalError) as fallo:
        pbix_export._guardar_como(                        # noqa: SLF001
            _ConModal(tmp_path / "a.pbix"), pid=1, started=None,
            destino=tmp_path / "a.pbix", timeout=3)
    assert fallo.value.details["modals"][0]["kind"] == "overwrite_confirm"


# ================= 4) los dos plazos NO son el mismo plazo ==================
def test_esperar_no_se_come_el_presupuesto_de_toda_la_operacion(tmp_path):
    """Un archivo que nunca aparece se cuenta pronto, no en 15 minutos.

    Antes se le pasaba el `timeout` global -900 s en la prueba live- a la
    espera de aparicion, y un guardado fallido bloqueaba la sesion entera.
    """
    inicio = time.monotonic()
    espera = pbix_export.esperar_escritura_terminada(
        tmp_path / "NoVaAVenir.pbix", timeout=900, gracia=1.0)
    transcurrido = time.monotonic() - inicio

    assert espera["stable"] is False
    assert espera["appeared"] is False
    assert transcurrido < 30, (
        f"espero {transcurrido:.0f} s con un plazo de gracia de 1 s")
    assert "nunca aparecio" in espera["wait_reason"]


def test_un_archivo_que_sigue_creciendo_si_agota_el_plazo_largo(tmp_path):
    """La paciencia es para lo que ya aparecio: un modelo grande tarda."""
    destino = tmp_path / "Creciendo.pbix"
    destino.write_bytes(b"x")
    parar = threading.Event()

    def _crecer():
        while not parar.is_set():
            with destino.open("ab") as fh:
                fh.write(b"x" * 512)
            time.sleep(0.05)

    hilo = threading.Thread(target=_crecer, daemon=True)
    hilo.start()
    try:
        espera = pbix_export.esperar_escritura_terminada(
            destino, timeout=3, gracia=0.5)
    finally:
        parar.set()
        hilo.join(timeout=5)

    assert espera["stable"] is False
    assert espera["appeared"] is True
    assert "no dejo de crecer" in espera["wait_reason"]


# ============ 5) un helper colgado se TERMINA; no queda nada vivo ============
def test_un_helper_que_no_responde_se_termina_de_verdad(monkeypatch):
    """`join(timeout)` deja el hilo dentro de COM. Matar el proceso no."""
    monkeypatch.setenv("PYTHONPATH", str(RAIZ))
    monkeypatch.setattr(desktop_helper, "MODULO_HELPER",
                        "tests.fixtures.helper_que_se_cuelga")
    monkeypatch.setattr(desktop_helper, "comtypes_disponible",
                        lambda: {"available": True})

    hilos_antes = threading.active_count()
    inicio = time.monotonic()
    with pytest.raises(desktop_helper.DesktopHelperTimeout) as fallo:
        desktop_helper.ejecutar({"action": "dormir"}, timeout=3)
    transcurrido = time.monotonic() - inicio

    assert transcurrido < 30
    assert fallo.value.code == "desktop_helper_timeout"
    assert "No queda ninguna llamada COM viva" in str(fallo.value)
    # Lo importante: el plazo lo impuso el sistema, no un hilo que sigue ahi.
    assert threading.active_count() <= hilos_antes


def test_el_helper_solo_declara_las_acciones_que_sabe_hacer():
    from horizun_pbi_mcp.powerbi import uia_helper

    assert sorted(uia_helper.ACCIONES) == ["save_as"]


def test_una_respuesta_que_no_es_json_se_dice_tal_cual(monkeypatch):
    def _falso(*a, **k):
        return subprocess.CompletedProcess(a, 0, stdout="esto no es json",
                                           stderr="")

    monkeypatch.setattr(desktop_helper, "comtypes_disponible",
                        lambda: {"available": True})
    monkeypatch.setattr(subprocess, "run", _falso)
    with pytest.raises(desktop_helper.DesktopHelperError) as fallo:
        desktop_helper.ejecutar({"action": "save_as"}, timeout=5)
    assert "no es JSON" in str(fallo.value)


# ================ 6) SendInput: una sola definicion de INPUT ================
@pytest.mark.skipif(sys.platform != "win32", reason="SendInput es de Windows")
def test_las_estructuras_de_entrada_son_siempre_las_mismas_clases():
    """ctypes compara tipos por identidad de clase, no por forma.

    Recrearlas en cada llamada daba `incompatible types, INPUT instance
    instead of INPUT instance`: dos clases distintas con el mismo nombre. El
    sintoma era que el nombre del archivo no se tecleaba nunca.
    """
    from horizun_pbi_mcp.powerbi import uia_helper

    primera = uia_helper._estructuras_input()             # noqa: SLF001
    segunda = uia_helper._estructuras_input()             # noqa: SLF001
    assert primera[0] is segunda[0]
    assert primera[1] is segunda[1]
    assert primera[2] is segunda[2]

    # Y lo que de verdad importa: un evento hecho aparte entra en el array.
    INPUT = primera[0]
    evento = uia_helper._tecla(0x41)                      # noqa: SLF001
    (INPUT * 1)(evento)                                   # no debe reventar


@pytest.mark.skipif(sys.platform != "win32", reason="SendInput es de Windows")
def test_si_windows_no_acepta_los_eventos_se_dice(monkeypatch):
    """Aceptar menos eventos de los enviados no lanza nada por si solo."""
    from horizun_pbi_mcp.powerbi import uia_helper

    class _User32Tacano:
        class SendInput:
            argtypes = None
            restype = None

            def __call__(self, n, arreglo, tamano):
                return 0                                  # no acepto ninguno

        def __init__(self):
            self.SendInput = _User32Tacano.SendInput()

    monkeypatch.setattr(uia_helper, "_user32", _User32Tacano)
    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._enviar_teclas([uia_helper._tecla(0x41)])  # noqa: SLF001
    assert "acepto 0 de 1" in str(fallo.value)


# ================ 7) coherencia: COM no se toca en el servidor ==============
def test_el_modulo_del_servidor_no_importa_comtypes():
    """Importar comtypes fija el apartamento del hilo y rompe pythonnet."""
    fuente = Path(desktop_ui.__file__).read_text(encoding="utf-8")
    assert "import comtypes" not in fuente
    assert "CreateObject" not in fuente


def test_comprobar_la_disponibilidad_no_importa_comtypes():
    """`find_spec` responde sin ejecutar el modulo. Es la diferencia."""
    estado = desktop_helper.comtypes_disponible()
    assert set(estado) >= {"available"}
    if not estado["available"]:
        assert "install" in estado
    assert "comtypes" not in sys.modules


def test_sin_el_extra_instalado_se_explica_como_instalarlo(monkeypatch):
    monkeypatch.setattr(desktop_helper, "comtypes_disponible",
                        lambda: {"available": False,
                                 "reason": "falta el paquete 'comtypes'",
                                 "install": 'pip install "horizun-pbi-mcp[export]"',
                                 "detail": "hace falta UI Automation"})
    with pytest.raises(desktop_helper.DesktopHelperUnavailable) as fallo:
        desktop_helper.ejecutar({"action": "save_as"}, timeout=5)
    assert fallo.value.code == "desktop_helper_unavailable"
    assert "horizun-pbi-mcp[export]" in str(fallo.value)
    assert fallo.value.details["capability"] == "pbix_export"
