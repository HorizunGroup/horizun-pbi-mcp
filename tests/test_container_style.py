"""Fondo y borde del CONTENEDOR (`_aplicar_estilo_contenedor`).

Antes de esto, `options` solo sabia pintar el relleno de una FORMA
(`_build_shape`) o el numero de una TARJETA (`_aplicar_opciones_de_tarjeta`).
No habia forma de pedir un marco de color en un grafico, una tabla o una
matriz sin escribir `visualContainerObjects` a mano: `_sin_marco()` solo sabe
APAGARLO. Estas pruebas fijan el contrato de la funcion que lo prende.
"""
from __future__ import annotations

from typing import Any, Dict

from pbip import visual_factory


def test_sin_colores_no_toca_nada():
    """Sin 'background_color' ni 'border_color' no se inventa un marco."""
    vis: Dict[str, Any] = {"visualType": "card"}
    visual_factory._aplicar_estilo_contenedor(vis, {})
    assert "visualContainerObjects" not in vis


def test_solo_fondo_no_escribe_borde():
    vis: Dict[str, Any] = {}
    visual_factory._aplicar_estilo_contenedor(vis, {"background_color": "#F47920"})
    vco = vis["visualContainerObjects"]
    assert "background" in vco
    assert "border" not in vco


def test_solo_borde_sin_radio_no_escribe_radius():
    vis: Dict[str, Any] = {}
    visual_factory._aplicar_estilo_contenedor(vis, {"border_color": "#F47920"})
    props = vis["visualContainerObjects"]["border"][0]["properties"]
    assert "radius" not in props
    assert props["show"]["expr"]["Literal"]["Value"] == "true"


def test_transparencia_por_defecto_es_cero():
    vis: Dict[str, Any] = {}
    visual_factory._aplicar_estilo_contenedor(vis, {"background_color": "#123456"})
    props = vis["visualContainerObjects"]["background"][0]["properties"]
    assert props["transparency"]["expr"]["Literal"]["Value"] == "0.0D"


def test_no_pisa_el_titulo_ya_escrito():
    """El titulo lo pone otro paso de build_visual; este solo agrega fondo/borde."""
    vis: Dict[str, Any] = {"visualContainerObjects": {
        "title": [{"properties": {"show": {"expr": {"Literal": {"Value": "true"}}}}}]}}
    visual_factory._aplicar_estilo_contenedor(
        vis, {"background_color": "#123456", "border_color": "#654321",
              "border_radius": 12})
    vco = vis["visualContainerObjects"]
    assert "title" in vco and "background" in vco and "border" in vco
    assert vco["border"][0]["properties"]["radius"]["expr"]["Literal"]["Value"] == "12.0D"


def test_color_va_como_fill_solido_no_como_texto():
    """Un color mal escrito como _lit(str) en vez de fill produce JSON valido
    de esquema que Power BI simplemente ignora: por eso se comprueba la forma
    exacta, no solo que la clave exista."""
    vis: Dict[str, Any] = {}
    visual_factory._aplicar_estilo_contenedor(vis, {"background_color": "#F47920"})
    color = vis["visualContainerObjects"]["background"][0]["properties"]["color"]
    assert color == {"solid": {"color": {"expr": {"Literal": {"Value": "'#F47920'"}}}}}
