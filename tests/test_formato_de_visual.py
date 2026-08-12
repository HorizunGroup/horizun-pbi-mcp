"""Formato de visual desde el spec, y orden Z que no se pisa.

Dos defectos de la misma sesion:

1. El spec solo aceptaba `type`, `title`, `fields` y `position`. Para poner dos
   segmentadores en desplegable hubo que escribir a mano dentro de cada
   `visual.json`

       "objects": {"data": [{"properties": {"mode": {"expr": {"Literal":
           {"Value": "'Dropdown'"}}}}}]}

   Funciona -Desktop lo respeta- pero es justo la clase de edicion a mano que
   despues aparece como errores de esquema que nadie sabe de donde salieron.

2. Todos los visuales salian con `z: 0`, y despues `pbi_detect_layout_issues`
   avisaba de `layout_z_order_duplicated`: la herramienta generaba el problema
   que su propio auditor reportaba. Y no es solo ruido: con la Z empatada, cual
   visual queda encima de cual es indefinido.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import visual_factory
from horizun_pbi_mcp.powerbi.errors import VisualFactoryError
from horizun_pbi_mcp.services import page_spec


# ============================================================ orden Z ========
def test_cada_visual_recibe_una_z_distinta():
    posiciones = page_spec._con_z([{"x": 0, "y": 0}, {"x": 1, "y": 1},
                                   {"x": 2, "y": 2}])
    zetas = [p["z"] for p in posiciones]
    assert zetas == [0.0, 1.0, 2.0]
    assert len(set(zetas)) == len(zetas), "empatar la Z deja el orden indefinido"


def test_una_z_puesta_a_mano_se_respeta():
    """Quien la escribe esta ordenando capas a proposito."""
    posiciones = page_spec._con_z([{"x": 0, "z": 99}, {"x": 1}])
    assert posiciones[0]["z"] == 99
    assert posiciones[1]["z"] == 1.0


def test_el_auditor_ya_no_se_queja_de_lo_que_generamos():
    """El bucle completo: lo que produce el layout no dispara la regla."""
    from horizun_pbi_mcp.services import layout_doctor

    posiciones = page_spec._con_z([{"x": 0, "y": 0, "width": 10, "height": 10},
                                   {"x": 20, "y": 0, "width": 10, "height": 10}])
    visuales = [{"id": f"v{i}", "type": "card", "position": p}
                for i, p in enumerate(posiciones)]
    informe = layout_doctor.detect_issues(visuales, {"width": 1280, "height": 720})
    codigos = [h["rule"] for h in informe["issues"]]
    assert "layout_z_order_duplicated" not in codigos
    assert "layout_z_order_missing" not in codigos


# ========================================================= bloque format =====
def test_el_modo_del_segmentador_se_escribe_solo():
    vis = {"visualType": "slicer"}
    rutas = visual_factory._aplicar_formato(vis, {"mode": "Dropdown"})

    literal = vis["objects"]["data"][0]["properties"]["mode"]
    assert literal == {"expr": {"Literal": {"Value": "'Dropdown'"}}}
    assert rutas == [("objects", "data", "mode")]


def test_el_header_del_segmentador_se_apaga_desde_el_spec():
    """Ocultar el encabezado del campo era parche a mano en visual.json."""
    vis = {"visualType": "slicer"}
    rutas = visual_factory._aplicar_formato(vis, {"header": False})

    assert vis["objects"]["header"][0]["properties"]["show"] == {
        "expr": {"Literal": {"Value": "false"}}}
    assert rutas == [("objects", "header", "show")]


def test_las_etiquetas_de_datos_se_encienden_desde_el_spec():
    vis = {"visualType": "barChart"}
    visual_factory._aplicar_formato(vis, {"dataLabels": True})
    assert vis["objects"]["labels"][0]["properties"]["show"] == {
        "expr": {"Literal": {"Value": "true"}}}


def test_la_leyenda_y_su_posicion():
    vis = {}
    visual_factory._aplicar_formato(vis, {"legend": True, "legendPosition": "Top"})
    props = vis["objects"]["legend"][0]["properties"]
    assert props["show"]["expr"]["Literal"]["Value"] == "true"
    assert props["position"]["expr"]["Literal"]["Value"] == "'Top'"


# ------------------------------------------------------------- falla cerrado ---
def test_una_clave_desconocida_falla_en_vez_de_ignorarse():
    """Un formato que se pide y no se aplica es peor que un error."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory._aplicar_formato({}, {"colorDeFondoDelEje": "rojo"})
    assert "no es un formato conocido" in str(exc.value)
    assert "dataLabels" in str(exc.value.details["supported"])


def test_un_valor_fuera_del_enum_falla():
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory._aplicar_formato({}, {"mode": "Desplegable"})
    assert "Dropdown" in str(exc.value.details["allowed"])


# ------------------------------------------------------------- en el spec ---
def _spec(formato):
    return {
        "schema_version": "1.0",
        "page": {"name": "P", "width": 1280, "height": 720},
        "visuals": [{"type": "slicer", "title": "Filtro",
                     "fields": {"values": ["Calendar[Year]"]},
                     "format": formato}],
    }


def test_el_esquema_acepta_un_format_valido():
    assert page_spec.validate_schema(_spec({"mode": "Dropdown"})) == []


def test_el_esquema_rechaza_un_format_desconocido():
    errores = page_spec.validate_schema(_spec({"inventado": 1}))
    assert errores, "una clave desconocida tiene que salir en la validacion"
    assert errores[0]["path"] == "$.visuals[0].format.inventado"


def test_el_esquema_rechaza_un_format_que_no_es_objeto():
    errores = page_spec.validate_schema(_spec(["mode"]))
    assert errores and errores[0]["path"] == "$.visuals[0].format"


def test_format_y_options_llegan_juntos_a_la_fabrica():
    """En el spec son cosas distintas: uno viste el contenedor, otro el visual."""
    unido = page_spec._opciones_con_formato(
        {"options": {"background_color": "#FFF"}, "format": {"dataLabels": True}})
    assert unido["background_color"] == "#FFF"
    assert unido["format"] == {"dataLabels": True}


def test_sin_ninguno_de_los_dos_no_se_inventan_opciones():
    assert page_spec._opciones_con_formato({"type": "card"}) is None
