"""Cambiar de sistema de diseno dejaba las paginas ya escritas atras.

Dos hallazgos ALTO de una sesion real que son el mismo defecto visto por dos
lados: **el estado cambio y lo ya escrito se quedo como estaba, en silencio.**

1. Se compuso en `sala` (1920x1080), se cambio a `informe` (1280x720) y se
   recompuso con `merge`. Los visuales de la composicion anterior se
   conservaron FUERA del lienzo: no se ven al abrir, pero viajan al render y a
   la publicacion, y solo aparecen si a alguien se le ocurre correr
   `pbi_detect_layout_issues`.
2. No habia camino de vuelta: cambiar de sistema obligaba a recomponer cada
   pagina a mano.

Y el detalle que no estaba dicho en ninguna parte: el color del texto SI sale
del tema, pero se cuece AL COMPONER y queda literal en el `visual.json`. Un
titulo compuesto en oscuro queda blanco sobre blanco al pasar a claro. Nada
falla; simplemente no se lee.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.services import page_update, reflow


# ============================================================ hallazgo 3 =====
class TestMergeAvisaDeLosQueQuedanFuera:
    """Antes del dano, con la cuenta hecha y con las dos salidas."""

    @staticmethod
    def _sobrante(vid, x, y, w=400, h=300):
        return {"id": vid, "position": {"x": x, "y": y, "width": w, "height": h}}

    def test_los_de_fuera_del_lienzo_nuevo_se_cuentan_y_se_nombran(self):
        r = page_update._avisar_de_los_que_quedan_fuera(
            [self._sobrante("v1", 1500, 100),      # se sale por la derecha
             self._sobrante("v2", 100, 900),       # se sale por abajo
             self._sobrante("v3", 100, 100)],      # cabe
            {"width": 1920, "height": 1080},
            {"width": 1280, "height": 720})

        assert r["ids"] == ["v1", "v2"], "v3 cabe y no debe acusarse"
        aviso = r["warnings"][0]
        assert "1920x1080" in aviso and "1280x720" in aviso, (
            "hay que decir de que lienzo a cual, o el aviso no explica nada")
        assert "sync_mode='replace'" in aviso and "pbi_reflow_pages" in aviso, (
            "un aviso sin salida es un callejon: van las DOS salidas")
        assert "render" in aviso or "publicacion" in aviso, (
            "hay que decir la CONSECUENCIA: son invisibles pero se publican")

    def test_si_todo_cabe_no_se_inventa_un_aviso(self):
        r = page_update._avisar_de_los_que_quedan_fuera(
            [self._sobrante("v1", 10, 10)],
            {"width": 1280, "height": 720}, {"width": 1280, "height": 720})
        assert r == {"ids": [], "warnings": []}

    def test_con_replace_no_hay_nada_que_avisar(self):
        """`replace` los borra: avisar de lo que se va a eliminar es ruido."""
        assert page_update._avisar_de_los_que_quedan_fuera(
            [], {"width": 1920, "height": 1080},
            {"width": 1280, "height": 720}) == {"ids": [], "warnings": []}

    def test_sin_cambio_de_lienzo_se_avisa_igual_pero_sin_culparlo(self):
        r = page_update._avisar_de_los_que_quedan_fuera(
            [self._sobrante("v1", 1200, 10)],
            {"width": 1280, "height": 720}, {"width": 1280, "height": 720})
        assert r["ids"] == ["v1"], "estar fuera es un problema aunque nada cambie"
        assert "pasa de" not in r["warnings"][0], (
            "no se puede culpar a un cambio de lienzo que no hubo")


# ------------------------------------------- el caso entero, sobre disco -----
@pytest.fixture
def proyecto_compuesto_en_sala(session, tmp_path, isolated_settings):
    """Una pagina compuesta en 1920x1080, tal como quedo en la sesion real."""
    import json

    from horizun_pbi_mcp.pbip import pbir_reader, project_locator, tmdl_reader
    from horizun_pbi_mcp.services import operations, page_spec
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    operations.registro().limpiar()
    md = tmdl_reader.read_semantic_model(active)

    spec_sala = {"schema_version": "1.0",
                 "page": {"name": "Control", "width": 1920, "height": 1080},
                 "layout": {"preset": "executive", "gap": 16},
                 "visuals": [{"type": "card", "title": "Avance",
                              "fields": {"values": ["[TotalAmount]"]}}],
                 "filters": [], "interactions": []}
    page_spec.apply_spec(active, page_spec.compile_spec(active, spec_sala, md))

    # Se empuja el visual a la derecha del lienzo grande: ahi cabe, y en
    # 1280x720 ya no. Es exactamente lo que hace una composicion en sala.
    pagina = next(p for p in pbir_reader.list_pages(active)
                  if p.get("display_name") == "Control")
    visual = pbir_reader.list_visuals(active, pagina["name"])[0]
    ruta = Path(visual["file"])
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["position"] = {"x": 1500, "y": 800, "width": 380, "height": 240,
                         "z": 0, "tabOrder": 0}
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    return session, active, md


def test_el_aviso_llega_hasta_la_respuesta_de_la_tool(proyecto_compuesto_en_sala,
                                                      monkeypatch):
    """El defecto recurrente de esta casa: el servidor lo sabe y no lo dice.

    Se recompone la misma pagina en `informe` (1280x720) con otro visual, asi
    que el de la composicion en sala sobra. Con `merge` se conserva —y se
    queda fuera del lienzo—. El aviso tiene que salir por la tool, no
    quedarse en el planificador: `pbi_apply_page_spec` hacia
    `resultado["warnings"] = compilado["warnings"]`, una ASIGNACION que
    borraba los avisos de la escritura justo antes de devolverlos.
    """
    import asyncio

    import horizun_pbi_mcp.config as cfg
    from horizun_pbi_mcp.server import build_server

    sesion, _active, _md = proyecto_compuesto_en_sala
    monkeypatch.setattr(cfg, "_session", sesion)

    spec_informe = {"schema_version": "1.0",
                    "page": {"name": "Control", "width": 1280, "height": 720},
                    "layout": {"preset": "executive", "gap": 16},
                    "visuals": [{"type": "card", "title": "Costo",
                                 "fields": {"values": ["[TotalAmount]"]}}],
                    "filters": [], "interactions": []}

    salida = asyncio.run(build_server().call_tool(
        "pbi_apply_page_spec", {"spec": spec_informe, "sync_mode": "merge"}))
    cuerpo = salida[1] if isinstance(salida, tuple) else salida
    if isinstance(cuerpo, dict) and "result" in cuerpo:
        cuerpo = cuerpo["result"]

    avisos = " ".join(cuerpo.get("warnings") or [])
    assert "FUERA del lienzo" in avisos, (
        f"el aviso no llego a la respuesta de la tool: {cuerpo.get('warnings')}")
    assert "1920x1080" in avisos and "1280x720" in avisos
    assert "sync_mode='replace'" in avisos and "pbi_reflow_pages" in avisos
    assert cuerpo.get("out_of_bounds_kept"), "y con los ids, para poder actuar"


def test_el_planificador_hace_la_cuenta_y_no_solo_la_funcion_suelta(
        proyecto_compuesto_en_sala):
    """Que la funcion sepa contar no sirve si nadie la llama.

    Este test recorre `page_update.planificar` de verdad: si se quita la
    llamada dentro de `_planificar_update`, la funcion sigue pasando sus
    propios tests y este es el que acusa.
    """
    from horizun_pbi_mcp.services import page_spec

    _s, active, md = proyecto_compuesto_en_sala
    spec_informe = {"schema_version": "1.0",
                    "page": {"name": "Control", "width": 1280, "height": 720},
                    "layout": {"preset": "executive", "gap": 16},
                    "visuals": [{"type": "card", "title": "Costo",
                                 "fields": {"values": ["[TotalAmount]"]}}],
                    "filters": [], "interactions": []}
    plan = page_update.planificar(
        active, page_spec.compile_spec(active, spec_informe, md),
        sync_mode=page_update.MERGE)

    assert plan["out_of_bounds_kept"], (
        "el visual de la composicion en sala queda fuera y nadie lo dijo")
    assert plan["out_of_bounds_kept"] == plan["not_removed"]
    assert any("FUERA del lienzo" in a for a in plan["warnings"])


def test_con_replace_el_aviso_desaparece_porque_el_problema_tambien(
        proyecto_compuesto_en_sala):
    from horizun_pbi_mcp.services import page_spec

    _s, active, md = proyecto_compuesto_en_sala
    spec_informe = {"schema_version": "1.0",
                    "page": {"name": "Control", "width": 1280, "height": 720},
                    "layout": {"preset": "executive", "gap": 16},
                    "visuals": [{"type": "card", "title": "Costo",
                                 "fields": {"values": ["[TotalAmount]"]}}],
                    "filters": [], "interactions": []}
    plan = page_update.planificar(
        active, page_spec.compile_spec(active, spec_informe, md),
        sync_mode=page_update.REPLACE)

    assert plan["removed"], "replace si los borra"
    assert plan["warnings"] == [], "no hay nada de que avisar si se eliminan"


# ============================================================ hallazgo 4 =====
class TestReflujo:
    """El camino de vuelta: reescalar y recolorear lo ya escrito."""

    def test_reescalar_es_proporcional(self):
        nueva = reflow._reescalar({"x": 960, "y": 540, "width": 480, "height": 270},
                                  1280 / 1920, 720 / 1080,
                                  {"width": 1280.0, "height": 720.0})
        assert nueva == {"x": 640.0, "y": 360.0, "width": 320.0, "height": 180.0}

    def test_nada_puede_quedar_fuera_del_lienzo_nuevo(self):
        """El punto entero del reflujo: acotar, no arrastrar el problema."""
        nueva = reflow._reescalar({"x": 1900, "y": 1000, "width": 800, "height": 600},
                                  1.0, 1.0, {"width": 1280.0, "height": 720.0})
        assert nueva["x"] + nueva["width"] <= 1280.0
        assert nueva["y"] + nueva["height"] <= 720.0

    def test_un_visual_mas_grande_que_el_lienzo_se_encoge_no_se_descarta(self):
        nueva = reflow._reescalar({"x": 0, "y": 0, "width": 3000, "height": 2000},
                                  1.0, 1.0, {"width": 1280.0, "height": 720.0})
        assert nueva["width"] == 1280.0 and nueva["height"] == 720.0

    def test_el_z_y_el_tab_order_sobreviven(self):
        """Reordenar la pila al reescalar cambiaria que tapa a que."""
        nueva = reflow._reescalar({"x": 0, "y": 0, "width": 10, "height": 10,
                                   "z": 7000, "tabOrder": 3},
                                  1.0, 1.0, {"width": 1280.0, "height": 720.0})
        assert nueva["z"] == 7000 and nueva["tabOrder"] == 3

    def test_nada_desaparece_por_redondeo(self):
        """Un visual fino no puede quedar en 0 de ancho al encoger."""
        nueva = reflow._reescalar({"x": 0, "y": 0, "width": 2, "height": 2},
                                  0.05, 0.05, {"width": 1280.0, "height": 720.0})
        assert nueva["width"] >= 1.0 and nueva["height"] >= 1.0


class TestRecolorear:
    """Lo que deja los titulos blancos sobre blanco."""

    def test_el_color_del_textbox_vive_en_los_textruns(self):
        data = {"visual": {"objects": {"general": [{"properties": {"paragraphs": [
            {"textRuns": [{"value": "Titulo",
                           "textStyle": {"color": "#FFFFFF",
                                         "fontSize": "28pt"}}]}]}}]}}}

        assert reflow._recolorear(data, "#1A1A1A") is True
        run = data["visual"]["objects"]["general"][0]["properties"][
            "paragraphs"][0]["textRuns"][0]
        assert run["textStyle"]["color"] == "#1A1A1A"
        assert run["textStyle"]["fontSize"] == "28pt", (
            "recolorear no puede llevarse por delante el resto del estilo")

    def test_no_se_inventa_formato_donde_no_lo_habia(self):
        data = {"visual": {"objects": {"general": [{"properties": {}}]}}}
        assert reflow._recolorear(data, "#1A1A1A") is False
        assert data["visual"]["objects"]["general"][0]["properties"] == {}

    def test_un_visual_sin_objects_no_revienta(self):
        assert reflow._recolorear({"visual": {}}, "#1A1A1A") is False
        assert reflow._recolorear({}, "#1A1A1A") is False


def test_solo_se_recolorean_los_decorativos():
    """Un grafico toma su color del tema al renderizar; tocarlo seria pisar
    una decision del usuario. El texto compuesto no: ese esta cocido."""
    assert set(reflow.DECORATIVOS_CON_COLOR) == {"textbox", "shape"}


def test_aplicar_un_sistema_que_no_existe_falla_antes_de_tocar_nada():
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    with pytest.raises(ValidationError) as exc:
        reflow.planificar(object(), "no_existe")
    assert "no_existe" in str(exc.value)


def test_el_plan_no_escribe_nada(monkeypatch, tmp_path):
    """`dry_run` por defecto: el reflujo mueve TODA la pagina, y eso se mira
    antes de hacerlo."""
    import inspect

    fuente = inspect.getsource(reflow.aplicar)
    assert "if dry_run:" in fuente
    assert fuente.index("if dry_run:") < fuente.index("assert_escritura_pbir"), (
        "el corte por dry_run tiene que ir ANTES de la puerta de escritura")

    firma = inspect.signature(reflow.aplicar)
    assert firma.parameters["dry_run"].default is True, (
        "el defecto seguro es no escribir")


def test_se_escribe_en_una_sola_transaccion():
    """Si falla la tercera pagina, las dos primeras no pueden quedar
    reflujadas y el resto no: media pagina en cada lienzo es peor que nada."""
    import inspect

    fuente = inspect.getsource(reflow.aplicar)
    assert fuente.count("project_transaction") == 1
    assert fuente.index("for pagina in plan") < fuente.index("project_transaction"), (
        "hay que compilar TODAS las paginas antes de abrir la transaccion")


def test_aplicar_un_sistema_avisa_de_las_paginas_que_deja_atras(
        proyecto_compuesto_en_sala):
    """El aviso que convierte un defecto silencioso en uno que se ve.

    Aplicar un sistema cambia el TEMA del informe y nada mas: las paginas ya
    escritas se quedan con el lienzo viejo. Antes eso no se decia y el
    desajuste se descubria abriendo el informe.
    """
    from horizun_pbi_mcp.services import design

    _s, active, _md = proyecto_compuesto_en_sala      # la pagina esta en 1920x1080
    salida = design.aplicar(active, "informe")        # 1280x720

    desajustadas = salida.get("pages_with_other_canvas") or []
    assert desajustadas, (
        "la pagina sigue en 1920x1080 y aplicar 'informe' no lo dijo")
    assert any(p.get("canvas") == {"width": 1920.0, "height": 1080.0}
               for p in desajustadas), "hay que decir en QUE lienzo se quedaron"
    assert any("pbi_reflow_pages" in a for a in (salida.get("warnings") or [])), (
        "hay que decir con QUE se arregla, no solo que esta mal")


def test_aplicar_un_sistema_no_avisa_cuando_no_hay_nada_desajustado(
        session, tmp_path, isolated_settings):
    """El aviso tiene que significar algo: si el lienzo ya coincide, callar."""
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.services import design
    from tests.fixtures import synthetic

    # El proyecto sintetico trae sus paginas en 1280x720, que es el lienzo de
    # `informe`: aplicarlo no deja ninguna atras.
    project_locator.open_project(session, str(synthetic.materialize(tmp_path)))
    salida = design.aplicar(session.require_active_pbip(), "informe")

    assert not (salida.get("pages_with_other_canvas") or []), (
        "avisar de lo que ya esta bien entrena a ignorar los avisos")
    assert not any("pbi_reflow_pages" in a
                   for a in (salida.get("warnings") or []))
