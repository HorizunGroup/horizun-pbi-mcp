"""`pbi_open_in_desktop` devuelve el texto del dialogo de error, no un timeout.

Duelo de Power BI del 24-oct-2026: dos proyectos con una tabla calculada mal
escrita no abrian. Desktop mostraba a los ~6 s «Se encontraron problemas» con
el motivo exacto («La relacion "LB_Calendario" usa un Id. de columna 71 no
valido») y la tool esperaba los 300 s enteros para responder
`desktop_timeout` y "puede estar pidiendo credenciales".

Lo que se midio contra Desktop real, y se congela aqui:

- el dialogo es una ventana WinForms POSEIDA por la principal, del mismo pid
  que se lanzo, con titulo «Se encontraron problemas»;
- su cuerpo es un `Internet Explorer_Server`: Win32 no ve texto en sus hijas,
  solo UI Automation lo lee (proceso auxiliar, extra `export`).

Todo con dobles: ninguna prueba abre Desktop.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi import desktop_helper, desktop_launcher, uia_helper

PID = 4242
PRINCIPAL = 100
DIALOGO = 200
TEXTO_UIA = ("Se encontraron problemas Hay un problema con el contenido de "
             "definition en Power BI Project.\r\nLa relación \"LB_Calendario\" "
             "usa un Id. de columna 71 no válido. Copiar los detalles al "
             "Portapapeles Cerrar")


@pytest.fixture
def desktop_con_dialogo(monkeypatch):
    """Un Desktop lanzado que ya muestra «Se encontraron problemas»."""
    ventanas = [
        {"hwnd": PRINCIPAL, "class": "WindowsForms10.Window.8.app",
         "title": "Sin título - Power BI Desktop"},
        {"hwnd": DIALOGO, "class": "WindowsForms10.Window.20008.app",
         "title": "Se encontraron problemas"},
    ]
    monkeypatch.setattr(desktop_launcher, "_pids_de_desktop", lambda pid: [pid])
    monkeypatch.setattr(uia_helper, "ventanas_de",
                        lambda pid: ventanas if pid == PID else [])
    monkeypatch.setattr(uia_helper, "_propietaria",
                        lambda hwnd: PRINCIPAL if hwnd == DIALOGO else 0)
    monkeypatch.setattr(desktop_launcher, "_process_started", lambda pid: 1.0)
    # El motor nunca llega a servir el modelo.
    monkeypatch.setattr(desktop_launcher, "proceso_con_archivo_abierto",
                        lambda _p: None)
    monkeypatch.setattr(desktop_launcher, "_instancias_utiles", lambda: [])
    monkeypatch.setattr(desktop_launcher.time, "sleep", lambda _s: None)
    return ventanas


def _helper_que_lee(monkeypatch, texto=TEXTO_UIA):
    peticiones = []

    def ejecutar(peticion, *, timeout):
        peticiones.append(peticion)
        return {"ok": True, "phase": "done", "text": texto}

    monkeypatch.setattr(desktop_helper, "ejecutar", ejecutar)
    return peticiones


def test_falla_en_cuanto_aparece_el_dialogo_y_devuelve_su_texto(
        desktop_con_dialogo, monkeypatch):
    peticiones = _helper_que_lee(monkeypatch)

    with pytest.raises(desktop_launcher.DesktopLoadError) as fallo:
        desktop_launcher._esperar_instancia_nueva(            # noqa: SLF001
            set(), 300, "Mirador.pbip", launched_pid=PID)

    error = fallo.value
    assert error.code == "desktop_load_failed"
    assert 'usa un Id. de columna 71 no válido' in str(error)
    detalles = error.details
    assert detalles["dialog_title"] == "Se encontraron problemas"
    assert detalles["text_read"] is True
    # Sin el titulo repetido delante ni los botones detras.
    assert detalles["dialog_text"].startswith("Hay un problema con el contenido")
    assert detalles["dialog_text"].endswith("no válido.")
    assert "pbi_validate_tmdl" in detalles["next_step"]
    # Se leyo ESE dialogo, de ESE proceso, con su hora de arranque.
    assert peticiones == [{"action": "read_dialog", "desktop_pid": PID,
                           "hwnd": DIALOGO, "desktop_started": 1.0}]


def test_sin_comtypes_lo_dice_en_vez_de_inventar_el_texto(
        desktop_con_dialogo, monkeypatch):
    def sin_com(_peticion, *, timeout):
        raise desktop_helper.DesktopHelperUnavailable(
            "falta comtypes", details={"install": 'pip install "x[export]"'})

    monkeypatch.setattr(desktop_helper, "ejecutar", sin_com)

    with pytest.raises(desktop_launcher.DesktopLoadError) as fallo:
        desktop_launcher._esperar_instancia_nueva(            # noqa: SLF001
            set(), 300, "Mirador.pbip", launched_pid=PID)

    detalles = fallo.value.details
    assert detalles["text_read"] is False
    assert detalles["dialog_text"] is None
    assert detalles["install"] == 'pip install "x[export]"'
    assert "Se encontraron problemas" in str(fallo.value)


def test_la_ventana_principal_no_es_un_dialogo_aunque_diga_error(
        desktop_con_dialogo, monkeypatch):
    """Un proyecto llamado `errores` no es un dialogo de error."""
    desktop_con_dialogo[:] = [{"hwnd": PRINCIPAL, "class": "x",
                               "title": "Error de obra - Power BI Desktop"}]
    _helper_que_lee(monkeypatch)
    ticks = iter([0.0, 0.1, 0.2, 5.0])
    monkeypatch.setattr(desktop_launcher.time, "monotonic",
                        lambda: next(ticks, 5.0))

    with pytest.raises(desktop_launcher.DesktopTimeoutError):
        desktop_launcher._esperar_instancia_nueva(            # noqa: SLF001
            set(), 1, "Error de obra.pbip", launched_pid=PID)


def test_un_dialogo_de_credenciales_no_corta_la_espera(
        desktop_con_dialogo, monkeypatch):
    """Ese espera una respuesta de la persona: se sigue esperando y el
    timeout dice que dialogo habia abierto."""
    desktop_con_dialogo[1]["title"] = "Acceso a origen de datos"
    _helper_que_lee(monkeypatch)
    ticks = iter([0.0, 0.1, 0.2, 5.0])
    monkeypatch.setattr(desktop_launcher.time, "monotonic",
                        lambda: next(ticks, 5.0))

    with pytest.raises(desktop_launcher.DesktopTimeoutError) as fallo:
        desktop_launcher._esperar_instancia_nueva(            # noqa: SLF001
            set(), 1, "Mirador.pbip", launched_pid=PID)

    assert fallo.value.details["open_dialogs"] == ["Acceso a origen de datos"]
    assert "Acceso a origen de datos" in str(fallo.value)


def test_el_helper_lee_solo_un_dialogo_de_ese_proceso(monkeypatch):
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid, "create_time": 1.0})
    monkeypatch.setattr(uia_helper, "ventanas_de", lambda pid: [{"hwnd": 1}])

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.leer_dialogo({"desktop_pid": PID, "hwnd": DIALOGO})
    assert fallo.value.fase == "ventana"
