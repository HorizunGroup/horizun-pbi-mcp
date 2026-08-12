"""Tema completo del llamante, fuentes corporativas y sustitucion avisada.

El caso real (#6 del reporte de campo): para poner tipografia corporativa habia
que dejar que la tool escribiera su JSON y luego SOBRESCRIBIRLO a mano,
confiando en que report.json siguiera apuntando al mismo archivo. Y el riesgo
no avisado: una segunda llamada a pbi_apply_theme se comia esas ediciones en
silencio.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.pbip import pbip_scaffold, theme
from horizun_pbi_mcp.pbip.theme import ThemeError
from horizun_pbi_mcp.utils.json_utils import read_json


# ------------------------------------------------------------ theme_json ---
def test_un_tema_completo_se_respeta_tal_cual():
    propio = {"name": "Corporativo", "dataColors": ["#111111", "#222222"],
              "propiedadRara": {"loQueSea": 1}}
    construido = theme.build_theme(theme_json=propio)
    assert construido["dataColors"] == ["#111111", "#222222"]
    assert construido["propiedadRara"] == {"loQueSea": 1}, (
        "lo que los presets no conocen tiene que sobrevivir intacto")


def test_theme_json_no_muta_el_objeto_del_llamante():
    propio = {"name": "X", "dataColors": ["#111111"]}
    theme.build_theme(theme_json=propio, fonts={"title": "Segoe UI"})
    assert "textClasses" not in propio


def test_theme_json_vacio_o_no_dict_se_rechaza():
    with pytest.raises(ThemeError):
        theme.build_theme(theme_json={})
    with pytest.raises(ThemeError):
        theme.build_theme(theme_json="no soy un dict")  # type: ignore[arg-type]


def test_data_colors_se_aplican_encima_del_theme_json():
    construido = theme.build_theme(theme_json={"name": "X"},
                                   data_colors=["#ABCDEF"])
    assert construido["dataColors"] == ["#ABCDEF"]


# ----------------------------------------------------------------- fonts ---
def test_las_fuentes_van_a_las_clases_de_texto_reales():
    """'body' es la clase 'label' en el esquema de temas: la traduccion es
    nuestra responsabilidad, no del usuario."""
    construido = theme.build_theme(preset=sorted(theme.PRESETS)[0],
                                   fonts={"title": "Georgia",
                                          "body": "Segoe UI",
                                          "callout": "Arial Black"})
    clases = construido["textClasses"]
    assert clases["title"]["fontFace"] == "Georgia"
    assert clases["label"]["fontFace"] == "Segoe UI"
    assert clases["callout"]["fontFace"] == "Arial Black"


def test_una_clase_de_fuente_desconocida_falla_con_las_validas():
    with pytest.raises(ThemeError) as exc:
        theme.build_theme(fonts={"subtitulo": "Arial"})
    assert "body" in str(exc.value)


def test_una_fuente_vacia_se_rechaza():
    with pytest.raises(ThemeError):
        theme.build_theme(fonts={"title": "   "})


def test_fonts_sobre_un_preset_no_pierde_el_resto_del_tema():
    base = theme.build_theme(preset=sorted(theme.PRESETS)[0])
    con_fuente = theme.build_theme(preset=sorted(theme.PRESETS)[0],
                                   fonts={"title": "Georgia"})
    assert con_fuente["dataColors"] == base["dataColors"]


# ----------------------------------------------- sustitucion con aviso ---
@pytest.fixture
def proyecto(tmp_path, session, isolated_settings):
    r = pbip_scaffold.crear_proyecto(tmp_path, "Tema")
    activo = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Tema.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Tema",
        has_pbir=True, has_tmdl=True)
    session.set_active_pbip(activo)
    return activo


def test_reaplicar_el_mismo_tema_no_avisa(proyecto):
    tema = theme.build_theme(preset=sorted(theme.PRESETS)[0])
    theme.apply_theme(proyecto, dict(tema))
    salida = theme.apply_theme(proyecto, dict(tema))
    assert "warnings" not in salida, (
        "reaplicar identico no debe gritar: no se pierde nada")


# ------------------------------------------------------------ patch ---
def test_patch_fusiona_dicts_en_profundidad_y_reemplaza_listas():
    base = {"name": "X", "dataColors": ["#111111", "#222222"],
            "visualStyles": {"*": {"*": {"title": [{"show": True}],
                                         "background": [{"show": False}]}}}}
    parche = {"dataColors": ["#333333"],
              "visualStyles": {"*": {"*": {"title": [{"show": False}]}}}}

    resultado = theme.patch_theme(base, parche)

    assert resultado["dataColors"] == ["#333333"], "las listas se reemplazan"
    estilos = resultado["visualStyles"]["*"]["*"]
    assert estilos["title"] == [{"show": False}]
    assert estilos["background"] == [{"show": False}], (
        "lo no mencionado por el patch sobrevive")
    assert base["dataColors"] == ["#111111", "#222222"], "no muta la base"


def test_patch_sobre_el_tema_aplicado_no_reenvia_el_tema_entero(proyecto):
    theme.apply_theme(proyecto, theme.build_theme(preset=sorted(theme.PRESETS)[0]))
    base = theme.current_theme(proyecto)
    assert base is not None

    parcheado = theme.patch_theme(
        base, {"textClasses": {"title": {"fontFace": "Fuente Corporativa"}}})
    theme.apply_theme(proyecto, parcheado)

    vigente = theme.current_theme(proyecto)
    assert vigente["textClasses"]["title"]["fontFace"] == "Fuente Corporativa"
    assert vigente["dataColors"] == base["dataColors"], (
        "el resto del tema quedo como estaba")


def test_sustituir_un_tema_editado_a_mano_avisa_y_donde_recuperarlo(proyecto):
    """El riesgo real: las ediciones manuales se perdian en silencio."""
    primero = theme.apply_theme(
        proyecto, theme.build_theme(preset=sorted(theme.PRESETS)[0]))
    # alguien edita el JSON a mano (tipografia corporativa, un color...)
    archivo = Path(primero["file"])
    editado = read_json(archivo)
    editado["textClasses"] = {"title": {"fontFace": "Fuente Corporativa"}}
    archivo.write_text(__import__("json").dumps(editado), encoding="utf-8")

    segundo = theme.apply_theme(
        proyecto, theme.build_theme(preset=sorted(theme.PRESETS)[-1]))

    assert any("DISTINTO" in a for a in segundo.get("warnings", [])), (
        "sustituir un tema con contenido distinto tiene que avisarse")
    assert any("backup" in a or "recuperala" in a
               for a in segundo["warnings"]), (
        "el aviso debe decir DONDE esta la version anterior")
