"""Fase 1A — acotacion de rutas. Todas estas pruebas FALLAN contra el commit 82bc6c9.

El "afuera" de cada escenario se crea DENTRO del tmp_path de pytest: ninguna
prueba apunta jamas a una ruta real del equipo, ni siquiera para demostrar un
fallo.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from config import ActivePbip
from pbip import pbir_reader, pbir_writer, project_locator
from powerbi.errors import PathSecurityError, ValidationError
from services import paths as safe_paths
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path):
    """Proyecto sintetico materializado + carpeta 'fuera del proyecto'."""
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    outside = synthetic.outside_marker_dir(tmp_path)
    return active, outside, tmp_path


# --------------------------------------------------------- identificadores ---
@pytest.mark.parametrize("valor,motivo", [
    ("../otro", "separador posix"),
    ("..\\otro", "separador windows"),
    ("../..\\mixto", "separadores mixtos"),
    ("..", "componente relativo"),
    (".", "componente relativo"),
    ("/absoluta", "absoluta posix"),
    ("\\absoluta", "absoluta windows"),
    ("C:/absoluta", "unidad absoluta"),
    ("C:carpeta", "relativa a unidad"),
    ("D:", "solo unidad"),
    ("\\\\servidor\\recurso", "UNC"),
    ("//servidor/recurso", "UNC posix"),
    ("\\\\?\\C:\\x", "ruta extendida"),
    ("\\\\.\\PhysicalDrive0", "ruta de dispositivo"),
    ("visual.json:stream", "ADS de NTFS"),
    ("CON", "dispositivo reservado"),
    ("con", "dispositivo reservado en minusculas"),
    ("NUL", "dispositivo reservado"),
    ("AUX.json", "dispositivo reservado con extension"),
    ("COM1", "puerto reservado"),
    ("LPT9", "puerto reservado"),
    ("nombre.", "punto final"),
    ("nombre ", "espacio final"),
    ("", "vacio"),
    ("a\x00b", "caracter de control"),
    ("a\nb", "salto de linea"),
])
def test_identificador_rechaza_sintaxis_peligrosa(valor, motivo):
    with pytest.raises(PathSecurityError):
        safe_paths.safe_identifier(valor, kind="id de prueba")


@pytest.mark.parametrize("valor", [
    "page01", "tmplcard0000000000", "visual.json",
    "Pagina_1", "hoja-2", "medida.tmdl", "Año2026",
])
def test_identificador_admite_nombres_legitimos(valor):
    assert safe_paths.safe_identifier(valor) == valor


def test_nombre_visible_admite_espacios_y_acentos():
    # Un displayName puede ser cualquier cosa razonable.
    assert safe_paths.assert_not_path_syntax("Resumen ejecutivo 2026") is not None
    assert safe_paths.assert_not_path_syntax("Análisis (final)") is not None


@pytest.mark.parametrize("valor", [
    "../fuera", "..\\fuera", "C:/x", "\\\\srv\\r", "a:b", "sub/dir",
])
def test_nombre_visible_rechaza_sintaxis_de_ruta(valor):
    with pytest.raises(PathSecurityError):
        safe_paths.assert_not_path_syntax(valor, kind="pagina")


# ------------------------------------------------------------- contencion ---
def test_contencion_acepta_dentro(tmp_path):
    base = tmp_path / "proj"
    (base / "sub").mkdir(parents=True)
    assert safe_paths.ensure_contained(base, base / "sub" / "x.json")


def test_contencion_rechaza_traversal(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    (tmp_path / "fuera").mkdir()
    with pytest.raises(PathSecurityError):
        safe_paths.ensure_contained(base, base / ".." / "fuera")


def test_contencion_rechaza_absoluta_de_otra_rama(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    otra = tmp_path / "otra"
    otra.mkdir()
    with pytest.raises(PathSecurityError):
        safe_paths.ensure_contained(base, otra / "x.json")


def test_contencion_es_insensible_a_mayusculas(tmp_path):
    """NTFS no distingue mayusculas: 'PROJ' y 'proj' son el mismo directorio."""
    base = tmp_path / "proj"
    (base / "sub").mkdir(parents=True)
    variante = Path(str(base).upper()) / "sub" / "x.json"
    if os.path.normcase("A") == os.path.normcase("a"):      # sistema insensible
        assert safe_paths.ensure_contained(base, variante)
    else:                                                   # pragma: no cover
        pytest.skip("sistema de archivos sensible a mayusculas")


def test_contencion_tolera_separadores_repetidos(tmp_path):
    base = tmp_path / "proj"
    (base / "sub").mkdir(parents=True)
    raro = Path(str(base) + os.sep + os.sep + "sub" + os.sep + "x.json")
    assert safe_paths.ensure_contained(base, raro)


def test_safe_join_valida_cada_componente(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    with pytest.raises(PathSecurityError):
        safe_paths.safe_join(base, "visuals", "..", "x.json")


def test_junction_que_apunta_fuera_es_rechazado(tmp_path):
    """Un enlace dentro del proyecto no puede usarse para salir de el."""
    base = tmp_path / "proj"
    base.mkdir()
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    enlace = base / "atajo"
    try:
        enlace.symlink_to(fuera, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("crear enlaces requiere privilegios en este entorno")
    with pytest.raises(PathSecurityError):
        safe_paths.ensure_contained(base, enlace / "x.json")


def test_revalidacion_detecta_cambio_entre_validacion_y_escritura(tmp_path):
    """El destino se vuelve a comprobar justo antes de escribir (anti-TOCTOU)."""
    base = tmp_path / "proj"
    (base / "sub").mkdir(parents=True)
    destino = base / "sub" / "x.json"
    safe_paths.ensure_contained(base, destino)          # 1a validacion: pasa

    # El directorio se sustituye por un enlace que sale del proyecto.
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    (base / "sub").rmdir()
    try:
        (base / "sub").symlink_to(fuera, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("crear enlaces requiere privilegios en este entorno")

    with pytest.raises(PathSecurityError):
        safe_paths.assert_still_contained(base, destino)


# ------------------------------------------- integracion con el escritor ----
@pytest.mark.parametrize("payload", [
    "../../../../FUERA_DEL_PROYECTO",
    "..\\..\\..\\..\\FUERA_DEL_PROYECTO",
    "../..\\../..\\FUERA_DEL_PROYECTO",
    "..",
])
def test_pagina_maliciosa_no_resuelve_fuera(proyecto, payload):
    active, outside, _ = proyecto
    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_reader.resolve_page_dir(active, payload)


def test_pagina_con_ruta_absoluta_no_resuelve_fuera(proyecto):
    active, outside, _ = proyecto
    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_reader.resolve_page_dir(active, str(outside))


def test_crear_visual_con_pagina_maliciosa_no_escribe_fuera(proyecto):
    active, outside, sandbox = proyecto
    antes = {p for p in outside.rglob("*")}
    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_writer.write_visual(active, "../../../../FUERA_DEL_PROYECTO",
                                 {"visual": {"visualType": "card"}})
    assert {p for p in outside.rglob("*")} == antes, \
        "no debe aparecer ningun archivo fuera del proyecto"


def test_visual_id_malicioso_no_escribe_fuera(proyecto):
    active, outside, _ = proyecto
    victima = outside / "inyectado"
    victima.mkdir(parents=True)
    original = '{"position":{"x":0,"y":0,"width":1,"height":1}}'
    (victima / "visual.json").write_text(original, encoding="utf-8")

    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_writer.update_visual_position(active, "page01", str(victima),
                                           99, 99, 99, 99)
    assert (victima / "visual.json").read_text(encoding="utf-8") == original, \
        "el archivo de fuera del proyecto no debe modificarse"


def test_visual_id_con_traversal_relativo_es_rechazado(proyecto):
    active, _outside, _ = proyecto
    with pytest.raises((PathSecurityError, ValidationError)):
        pbir_writer.update_visual_position(
            active, "page01", "../../../../FUERA_DEL_PROYECTO/x", 1, 2, 3, 4)


def test_pagina_legitima_sigue_funcionando(proyecto):
    """El endurecimiento no puede romper el uso normal."""
    active, _outside, _ = proyecto
    por_id = pbir_reader.resolve_page_dir(active, synthetic.PAGE_ID)
    por_nombre = pbir_reader.resolve_page_dir(active, synthetic.PAGE_DISPLAY_NAME)
    assert por_id == por_nombre
