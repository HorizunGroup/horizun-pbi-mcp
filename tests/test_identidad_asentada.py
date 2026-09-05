"""Esperar a que la ventana MUESTRE el documento, y no confundir señales.

Dos fallos reales del mismo origen: `pbi_export_pbix` rechazo la ventana
porque su titulo decia `Sin titulo - Power BI Desktop` -treinta segundos
despues decia el nombre esperado y el mismo request funciono-, y
`pbi_validate_desktop_render` devolvio dos capturas identicas de 41.809 bytes
de un lienzo vacio con `frame_settled=true` y `data_loaded=true`. Que el
motor responda no significa que la ventana haya terminado de abrir; que la
imagen no cambie no significa que muestre el informe.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.powerbi import desktop_capture, desktop_identity as di
from horizun_pbi_mcp.powerbi import desktop_launcher
from horizun_pbi_mcp.services import pbix_export

OBJETIVO = Path("C:/proyectos/Demo.pbip")


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(di.time, "sleep", lambda s: None)


# ================================ 1) que dice un titulo ====================
@pytest.mark.parametrize("titulo, nombre", [
    ("Demo", "demo"),
    ("Demo - Power BI Desktop", "demo"),
    ("Demo - Power BI", "demo"),
    ("  Demo  ", "demo"),
    ("Sin título - Power BI Desktop", "sin título"),
])
def test_el_nombre_del_documento_no_incluye_el_sufijo_del_producto(titulo, nombre):
    assert di.nombre_de_documento(titulo) == nombre


@pytest.mark.parametrize("titulo, provisional", [
    ("Sin título - Power BI Desktop", True),
    ("Untitled - Power BI Desktop", True),
    ("", True),
    ("Demo - Power BI Desktop", False),
    ("Otro informe", False),
])
def test_un_titulo_provisional_es_cargando_no_otro_documento(titulo, provisional):
    assert di.titulo_provisional(titulo) is provisional


def test_clasificar_distingue_asentado_provisional_y_otro():
    assert di.clasificar_titulos(["Demo - Power BI Desktop"], OBJETIVO) == \
        di.IDENTIDAD_ASENTADA
    assert di.clasificar_titulos(["Sin título - Power BI Desktop"], OBJETIVO) == \
        di.IDENTIDAD_PROVISIONAL
    assert di.clasificar_titulos(["Otro - Power BI Desktop"], OBJETIVO) == \
        di.IDENTIDAD_OTRO_DOCUMENTO
    assert di.clasificar_titulos([], OBJETIVO) == di.IDENTIDAD_SIN_TITULO


def test_un_titulo_que_solo_contiene_el_nombre_sigue_sin_valer():
    assert di.clasificar_titulos(["Demo v2 - Power BI Desktop"], OBJETIVO) == \
        di.IDENTIDAD_OTRO_DOCUMENTO


# ============================ 2) el sondeo con tope ========================
def _titulos_en_secuencia(*lecturas):
    cola = list(lecturas)

    def _leer(pid):
        if len(cola) > 1:
            return cola.pop(0)
        return cola[0]
    return _leer


def test_un_titulo_provisional_se_espera_hasta_que_se_asienta():
    """El fallo real: rechazar a los 0 s lo que a los 30 s era correcto."""
    leer = _titulos_en_secuencia(["Sin título - Power BI Desktop"],
                                 ["Sin título - Power BI Desktop"],
                                 ["Demo - Power BI Desktop"])

    espera = di.esperar_identidad_de_ventana(1111, OBJETIVO, timeout=60,
                                             cada=0.01, titulos=leer)

    assert espera["settled"] is True
    assert espera["status"] == di.IDENTIDAD_ASENTADA
    assert espera["polls"] == 3
    assert espera["titles_observed"] == ["Sin título - Power BI Desktop",
                                         "Demo - Power BI Desktop"]


def test_un_titulo_estable_de_otro_documento_no_se_espera():
    """Esperar no lo va a convertir en el nuestro; y actuar seria escribir ahi."""
    leer = _titulos_en_secuencia(["Otro - Power BI Desktop"])

    espera = di.esperar_identidad_de_ventana(1111, OBJETIVO, timeout=60,
                                             cada=0.01, titulos=leer)

    assert espera["settled"] is False
    assert espera["status"] == di.IDENTIDAD_OTRO_DOCUMENTO
    assert espera["polls"] == 1, "siguio sondeando una respuesta definitiva"


def test_al_vencer_el_plazo_se_devuelve_lo_observado(monkeypatch):
    reloj = iter([0.0, 0.0, 1.0, 2.0, 3.0, 99.0, 99.0, 99.0])
    monkeypatch.setattr(di.time, "monotonic", lambda: next(reloj, 99.0))
    leer = _titulos_en_secuencia(["Sin título - Power BI Desktop"])

    espera = di.esperar_identidad_de_ventana(1111, OBJETIVO, timeout=5,
                                             cada=0.01, titulos=leer)

    assert espera["settled"] is False
    assert espera["status"] == di.IDENTIDAD_TIMEOUT
    assert espera["last_titles"] == ["Sin título - Power BI Desktop"]
    assert espera["polls"] >= 2


# ================= 3) la exportacion espera antes de juzgar ===============
class _Abierto:
    instance = {"pid": 999, "port": 55001}


def _identidad(titulo, *, path_match=False, project_path=None):
    return {"engine_pid": 999, "desktop_pid": 1111,
            "desktop_process_started": 1000.0,
            "desktop_window_title": titulo, "project_path": project_path,
            "path_match": path_match, "identity_confidence": "medium",
            "identity_evidence": []}


def test_la_exportacion_espera_a_que_el_titulo_se_asiente(monkeypatch):
    lecturas = iter([_identidad("Sin título - Power BI Desktop"),
                     _identidad("Demo - Power BI Desktop", path_match=True)])
    monkeypatch.setattr(di, "identify", lambda inst, target=None: next(lecturas))
    esperas = []
    monkeypatch.setattr(
        di, "esperar_identidad_de_ventana",
        lambda pid, obj, timeout=60.0, **k: esperas.append(timeout) or {
            "status": di.IDENTIDAD_ASENTADA, "settled": True, "polls": 4,
            "waited_seconds": 12.0})

    identidad = pbix_export._identidad_verificada(_Abierto(), OBJETIVO)  # noqa: SLF001

    assert identidad["path_match"] is True
    assert identidad["window_wait"]["settled"] is True
    assert esperas == [pbix_export.ESPERA_IDENTIDAD]


def test_la_exportacion_rechaza_al_instante_otro_documento_estable(monkeypatch):
    monkeypatch.setattr(di, "identify",
                        lambda inst, target=None: _identidad("Otro - Power BI"))
    monkeypatch.setattr(di, "esperar_identidad_de_ventana",
                        lambda *a, **k: pytest.fail("no habia que esperar"))

    with pytest.raises(pbix_export.PbixExportError) as fallo:
        pbix_export._identidad_verificada(_Abierto(), OBJETIVO)  # noqa: SLF001

    assert fallo.value.details["reason"] == "desktop_serves_other_document"


def test_un_documento_abierto_distinto_tampoco_se_espera(monkeypatch):
    monkeypatch.setattr(di, "identify", lambda inst, target=None: _identidad(
        "Sin título - Power BI Desktop", project_path="C:/otro/Otro.pbix"))
    monkeypatch.setattr(di, "esperar_identidad_de_ventana",
                        lambda *a, **k: pytest.fail("no habia que esperar"))

    with pytest.raises(pbix_export.PbixExportError) as fallo:
        pbix_export._identidad_verificada(_Abierto(), OBJETIVO)  # noqa: SLF001

    assert fallo.value.details["reason"] == "desktop_serves_other_document"


def test_si_el_titulo_nunca_se_asienta_se_dice_con_evidencia(monkeypatch):
    monkeypatch.setattr(di, "identify", lambda inst, target=None: _identidad(
        "Sin título - Power BI Desktop"))
    monkeypatch.setattr(di, "esperar_identidad_de_ventana", lambda *a, **k: {
        "status": di.IDENTIDAD_TIMEOUT, "settled": False, "polls": 9,
        "waited_seconds": 90.0,
        "last_titles": ["Sin título - Power BI Desktop"]})

    with pytest.raises(pbix_export.PbixExportError) as fallo:
        pbix_export._identidad_verificada(_Abierto(), OBJETIVO)  # noqa: SLF001

    assert fallo.value.details["reason"] == "desktop_window_not_settled"
    assert fallo.value.details["identity"]["window_wait"]["polls"] == 9


# ============ 4) el lanzador tolera el sufijo en la correlacion ==========
def test_la_correlacion_por_titulo_acepta_el_sufijo_del_producto(monkeypatch):
    monkeypatch.setattr(desktop_launcher.os, "name", "nt")
    ventana = SimpleNamespace(title="Demo - Power BI Desktop")
    monkeypatch.setattr(desktop_capture, "_enumerate_windows",
                        lambda pid: [ventana])

    resultado = desktop_launcher.coincidencias_por_titulo("Demo", [1111])

    assert resultado.pids == (1111,)


# ================== 5) la captura separa las cuatro señales ===============
def _opened():
    return desktop_launcher.OpenedPbix(
        pbix_path=r"C:\informes\Ventas.pbix",
        instance={"port": 51234}, desktop_pid=777, launched_by_us=True,
        waited_seconds=1.0, desktop_started=1234.0)


def _ventana(titulo):
    return desktop_capture.DesktopWindow(20, 777, titulo, "PBIDesktop", 4, 1)


def _captura_montada(monkeypatch, titulo, pixeles):
    monkeypatch.setattr(desktop_capture, "_assert_desktop_identity",
                        lambda pid, started: None)
    monkeypatch.setattr(desktop_capture, "_enumerate_windows",
                        lambda pid: [_ventana(titulo)])
    monkeypatch.setattr(desktop_capture, "_capture_window_bgra",
                        lambda hwnd: (4, 1, pixeles))
    monkeypatch.setattr(desktop_capture.time, "sleep", lambda s: None)


UNIFORME = b"\xff\xff\xff\x00" * 4
VARIADO = b"\xff\x00\x00\x00\x00\xff\x00\x00\x00\x00\xff\x00\x10\x20\x30\x00"


def test_un_fotograma_estable_no_se_declara_asentado_sin_identidad(
        monkeypatch, tmp_path):
    """Las dos capturas identicas de 41.809 bytes: estable, pero de la espera."""
    _captura_montada(monkeypatch, "Sin título - Power BI Desktop", UNIFORME)

    salida = desktop_capture.capture_opened(_opened(), timeout=1,
                                            output_dir=tmp_path,
                                            settle_seconds=1.0,
                                            identity_timeout=0.0)

    assert salida["identity_settled"] is False
    assert salida["frame_settled"] is False, (
        "una imagen que no cambia mientras carga no es un informe asentado")
    assert salida["frame_uniform"] is True
    assert salida["capture_representative"] is False
    assert "capture_warning" in salida
    assert salida["identity"]["status"] in (di.IDENTIDAD_TIMEOUT,
                                            di.IDENTIDAD_PROVISIONAL)


def test_con_identidad_y_pixeles_variados_la_captura_es_representativa(
        monkeypatch, tmp_path):
    _captura_montada(monkeypatch, "Ventas - Power BI Desktop", VARIADO)

    salida = desktop_capture.capture_opened(_opened(), timeout=1,
                                            output_dir=tmp_path,
                                            settle_seconds=1.0)

    assert salida["identity_settled"] is True
    assert salida["frame_settled"] is True
    assert salida["frame_uniform"] is False
    assert salida["capture_representative"] is True
    assert "capture_warning" not in salida


def test_una_pagina_uniforme_con_identidad_se_marca_pero_no_falla(
        monkeypatch, tmp_path):
    """Una pagina legitimamente vacia sale uniforme: es señal, no error."""
    _captura_montada(monkeypatch, "Ventas - Power BI Desktop", UNIFORME)

    salida = desktop_capture.capture_opened(_opened(), timeout=1,
                                            output_dir=tmp_path)

    assert salida["identity_settled"] is True
    assert salida["frame_uniform"] is True
    assert salida["capture_representative"] is False
    assert "solo color" in salida["capture_warning"]


def test_el_detector_de_lienzo_vacio_mira_los_pixeles_y_no_el_tamano():
    """Dos imagenes del MISMO tamaño y distinto contenido: dos veredictos."""
    assert len(UNIFORME) == len(VARIADO)
    assert desktop_capture.analizar_fotograma(4, 1, UNIFORME)["uniform"] is True
    assert desktop_capture.analizar_fotograma(4, 1, VARIADO)["uniform"] is False
    assert desktop_capture.analizar_fotograma(
        4, 1, VARIADO)["dominant_color_ratio"] < 0.5
