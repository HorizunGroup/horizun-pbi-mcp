"""Tipografia y rotulos al componer paginas: lo que se ve, no solo lo que valida.

Todo esto salio de mirar una portada terminada. El informe pasaba los dos
validadores y aun asi se veia mal, por tres motivos distintos:

1. Sobre cada texto aparecia impreso su nombre interno ("Titulo", "Subtitulo",
   "Nota de alcance del dato"). En un spec, `title` identifica al visual; en un
   elemento de composicion no es una etiqueta que nadie quiera ver.
2. Los textos salian cortados con barra de scroll, porque la altura quedaba por
   debajo del piso que Power BI necesita para ese tamano de fuente.
3. Las tarjetas repetian el nombre de la medida arriba y abajo, con el numero
   mas pequeno que su propia etiqueta.

Ninguno lo detecta un validador de esquema: el JSON es correcto. Por eso se
congelan aqui.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import project_locator, visual_factory


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _vco(salida):
    return salida["visual"]["visual"].get("visualContainerObjects", {})


def _muestra_titulo(salida) -> bool:
    t = _vco(salida).get("title")
    if not t:
        return False
    show = t[0].get("properties", {}).get("show")
    if show is None:
        return True  # sin propiedad explicita, Power BI lo muestra
    return show.get("expr", {}).get("Literal", {}).get("Value") == "true"


POS = {"x": 0, "y": 0, "width": 400, "height": 120}


# --------------------------------------------------------------------------
# 1. El nombre del visual no es una etiqueta
# --------------------------------------------------------------------------

def test_el_titulo_de_un_texto_no_se_imprime_encima(proyecto):
    """`title` identifica el visual en el spec; no es texto para el lienzo.

    Antes se imprimia "Titulo" sobre el titulo de la portada.
    """
    salida = visual_factory.build_visual(
        proyecto, "textbox", {}, POS, title="Titulo",
        options={"text": "Calidad de modelos BIM", "font_size": 30})

    assert _muestra_titulo(salida) is False


def test_se_puede_pedir_el_rotulo_a_proposito(proyecto):
    """Si alguien lo quiere visible, se pide explicitamente."""
    salida = visual_factory.build_visual(
        proyecto, "textbox", {}, POS, title="Nota legal",
        options={"text": "...", "show_title": True})

    assert _muestra_titulo(salida) is True


def test_una_imagen_tampoco_lleva_su_nombre_impreso(proyecto):
    """Un logo con la palabra 'Logo Acme' escrita encima."""
    salida = visual_factory.build_visual(
        proyecto, "image", {}, POS, title="Logo Acme",
        options={"resource": "acme-logo.png"})

    assert _muestra_titulo(salida) is False


def test_un_visual_de_datos_si_conserva_su_titulo(proyecto):
    """En un grafico el titulo SI es la etiqueta que explica que se ve."""
    salida = visual_factory.build_visual(
        proyecto, "card", {"values": ["[TotalAmount]"]}, POS,
        title="Modelos auditados")

    vco = _vco(salida)
    assert "title" in vco


def test_pedir_un_titulo_lo_hace_visible(proyecto):
    """Se escribia el texto y no `show`, y el defecto de una tarjeta es OCULTO.

    Resultado: se pedia un titulo, no fallaba nada, y en pantalla no habia
    titulo. Un rotulo que se pide y no aparece es peor que no poder pedirlo.
    """
    salida = visual_factory.build_visual(
        proyecto, "card", {"values": ["[TotalAmount]"]}, POS,
        title="Costo real ejecutado")

    assert _muestra_titulo(salida) is True


# --------------------------------------------------------------------------
# 2. La altura minima de un texto no es opinable
# --------------------------------------------------------------------------

def test_la_altura_de_un_texto_sube_al_piso_que_exige_el_tamano(proyecto):
    """Por debajo del piso, Power BI mete barra de scroll y corta el texto.

    La cuenta es la del validador oficial:
    max(18, ceil(pt * 25 / 16)) + padding arriba y abajo.
    """
    salida = visual_factory.build_visual(
        proyecto, "textbox", {}, {"x": 0, "y": 0, "width": 940, "height": 40},
        options={"text": "Calidad de modelos BIM", "font_size": 30})

    # 30pt -> ceil(46.875) = 47, + 8 + 8 = 63
    assert salida["visual"]["position"]["height"] >= 63
    assert any("altura" in w.lower() for w in salida["warnings"]), salida["warnings"]


def test_una_altura_suficiente_se_respeta(proyecto):
    salida = visual_factory.build_visual(
        proyecto, "textbox", {}, {"x": 0, "y": 0, "width": 940, "height": 200},
        options={"text": "x", "font_size": 12})

    assert salida["visual"]["position"]["height"] == 200
    assert not any("altura" in w.lower() for w in salida["warnings"])


def test_sin_tamano_declarado_se_usa_el_de_por_defecto(proyecto):
    """Sin `font_size` no se puede calcular un piso mayor que el minimo."""
    salida = visual_factory.build_visual(
        proyecto, "textbox", {}, {"x": 0, "y": 0, "width": 400, "height": 34},
        options={"text": "x"})
    assert salida["visual"]["position"]["height"] == 34


# --------------------------------------------------------------------------
# 3. Una tarjeta no tiene que decir dos veces lo mismo
# --------------------------------------------------------------------------

def test_la_tarjeta_puede_ocultar_la_etiqueta_repetida(proyecto):
    """Con titulo propio, la etiqueta de categoria repite el mismo texto."""
    salida = visual_factory.build_visual(
        proyecto, "card", {"values": ["[TotalAmount]"]}, POS,
        title="Modelos auditados",
        options={"show_category_label": False})

    objetos = salida["visual"]["visual"]["objects"]
    show = objetos["categoryLabels"][0]["properties"]["show"]
    assert show["expr"]["Literal"]["Value"] == "false"


def test_el_numero_de_la_tarjeta_puede_mandar(proyecto):
    """El dato tiene que pesar mas que su etiqueta."""
    salida = visual_factory.build_visual(
        proyecto, "card", {"values": ["[TotalAmount]"]}, POS,
        options={"value_font_size": 32, "bold_value": True})

    props = salida["visual"]["visual"]["objects"]["labels"][0]["properties"]
    assert props["fontSize"]["expr"]["Literal"]["Value"] == "32D"
    assert props["bold"]["expr"]["Literal"]["Value"] == "true"


def test_cardVisual_usa_los_grupos_value_y_label_del_catalogo(proyecto):
    """La tarjeta nueva no comparte los grupos de formato de la clasica.

    El CLI oficial y los visual.json exportados por Desktop enumeran `value`
    y `label` para `cardVisual`; `labels` y `categoryLabels` son del `card`
    clasico y sobreviven al esquema porque `objects` no valida sus claves.
    """
    salida = visual_factory.build_visual(
        proyecto, "cardVisual", {"values": ["[TotalAmount]"]}, POS,
        options={"show_category_label": False, "value_font_size": 32})

    objetos = salida["visual"]["visual"]["objects"]
    assert set(objetos) == {"label", "value"}
    assert objetos["label"][0]["properties"]["show"]["expr"]["Literal"]["Value"] == "false"
    assert objetos["value"][0]["properties"]["fontSize"]["expr"]["Literal"]["Value"] == "32D"


def test_cardVisual_usa_fontColor_y_no_el_color_de_la_tarjeta_clasica(proyecto):
    salida = visual_factory.build_visual(
        proyecto, "cardVisual", {"values": ["[TotalAmount]"]}, POS,
        options={"value_color": "#123456"})

    props = salida["visual"]["visual"]["objects"]["value"][0]["properties"]
    assert "fontColor" in props
    assert "color" not in props


@pytest.mark.parametrize("pedido,pbir", [
    ("roundedRectangle", "rectangleRounded"),
    ("triangle", "triangleIsoc"),
])
def test_formas_amigables_se_serializan_como_enum_oficial(proyecto, pedido, pbir):
    salida = visual_factory.build_visual(
        proyecto, "shape", {}, POS, options={"shape": pedido})
    valor = (salida["visual"]["visual"]["objects"]["shape"][0]["properties"]
             ["tileShape"]["expr"]["Literal"]["Value"])
    assert valor == f"'{pbir}'"


@pytest.mark.parametrize("pedido,pbir", [
    ("bookmark", "bookmarks"), ("info", "information"),
    ("question", "help"), ("resetFilters", "clearAllSlicers"),
    ("chevronRight", "rightArrow"), ("chevronLeft", "leftArrow"),
])
def test_iconos_amigables_se_serializan_como_enum_oficial(
        proyecto, pedido, pbir):
    salida = visual_factory.build_visual(
        proyecto, "actionButton", {}, POS,
        options={"action": "back", "icon": pedido})
    valor = (salida["visual"]["visual"]["objects"]["icon"][0]["properties"]
             ["shapeType"]["expr"]["Literal"]["Value"])
    assert valor == f"'{pbir}'"


@pytest.mark.parametrize("tipo", ["barChart", "columnChart"])
def test_el_visualType_es_exactamente_el_tipo_oficial_solicitado(proyecto, tipo):
    salida = visual_factory.build_visual(
        proyecto, tipo,
        {"category": ["Sales[Region]"], "values": ["[TotalAmount]"]}, POS)

    assert salida["actual_type"] == tipo
    assert salida["visual"]["visual"]["visualType"] == tipo


def test_sin_opciones_la_tarjeta_no_se_toca(proyecto):
    """No se inventa formato que nadie pidio."""
    salida = visual_factory.build_visual(
        proyecto, "card", {"values": ["[TotalAmount]"]}, POS, title="T")
    objetos = salida["visual"]["visual"].get("objects", {})
    assert "categoryLabels" not in objetos
    assert "labels" not in objetos
