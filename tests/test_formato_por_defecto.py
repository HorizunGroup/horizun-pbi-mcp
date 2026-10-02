"""Una plantilla minima sale con formato de informe, no con el de fabrica.

Caso real («Comite de obra», 1-oct-2026): el proyecto se creo con
`pbi_create_pbip_project` y no habia ningun visual que clonar, asi que los
doce salieron de la plantilla minima. Validaban, pero los titulos eran notas
grises de 9 pt, sin marco, y cada tarjeta repetia su titulo en la etiqueta de
categoria («SPI (Project)» encima, «SPI Project» debajo).

Ahora llevan el acabado de los temas del repo: titulo de 12 pt en
seminegrita con la tinta del tema y marco con fondo y borde suave. Lo que
pide quien llama gana; lo que el tema ya gobierna no se pisa; y el informe
sigue pasando el validador oficial.

Y la etiqueta de la tarjeta que repite el titulo se apaga (decision de
Pablo, 2026-10-01); `options.show_category_label=true` la conserva.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.pbip import pbip_scaffold, theme, visual_factory
from horizun_pbi_mcp.pbip import table_from_file, tmdl_reader, tmdl_writer
from horizun_pbi_mcp.services import page_spec, report_validator

POS = {"x": 16, "y": 16, "width": 300, "height": 120}
CSV = ("Paquete,Tarea,Cruces,Valor\n"
       "PAQ-A,T1,10,1.5\nPAQ-B,T2,4,2.25\n")


@pytest.fixture
def proyecto(tmp_path, session, isolated_settings):
    """Un proyecto recien creado por el servidor: tema base minimo, sin visuales."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Comite", culture="es-CO")
    active = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Comite.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Comite",
        has_pbir=True, has_tmdl=True)
    session.set_active_pbip(active)
    datos = tmp_path / "datos"
    datos.mkdir()
    (datos / "Cruces.csv").write_text(CSV, encoding="utf-8")
    table_from_file.agregar_tabla(active, datos / "Cruces.csv", "Cruces")
    tmdl_writer.create_measure_pbip(active, "Cruces", "Total cruces",
                                    "SUM(Cruces[Cruces])", format_string="0")
    return active


def _props(vis, scope, grupo):
    return vis["visual"][scope][grupo][0]["properties"]


def _valor(prop):
    return prop["expr"]["Literal"]["Value"]


def test_la_tarjeta_sale_con_titulo_marco_y_sin_etiqueta_repetida(proyecto):
    r = visual_factory.build_visual(
        proyecto, "card", {"values": ["[Total cruces]"]}, POS,
        title="Cruces reales")
    vis = r["visual"]

    titulo = _props(vis, "visualContainerObjects", "title")
    assert _valor(titulo["fontSize"]) == "12D"
    assert _valor(titulo["bold"]) == "true"
    # La tinta del tema REAL del informe (HorizunBase), no un color inventado.
    assert _valor(titulo["fontColor"]["solid"]["color"]) == "'#252423'"
    assert _valor(_props(vis, "visualContainerObjects", "border")["show"]) == "true"
    assert _valor(_props(vis, "objects", "categoryLabels")["show"]) == "false"
    assert r["default_style"]["theme"] == "HorizunBase"
    assert "plantilla minima" in r["origin"]


def test_lo_que_pide_quien_llama_gana(proyecto):
    r = visual_factory.build_visual(
        proyecto, "card", {"values": ["[Total cruces]"]}, POS,
        title="Cruces", options={"background_color": "#112233",
                                 "show_category_label": False})
    vis = r["visual"]

    fondo = _props(vis, "visualContainerObjects", "background")
    assert _valor(fondo["color"]["solid"]["color"]) == "'#112233'"
    assert _valor(_props(vis, "objects", "categoryLabels")["show"]) == "false"


def test_sin_titulo_no_se_inventa_un_titulo(proyecto):
    r = visual_factory.build_visual(
        proyecto, "barChart",
        {"category": ["Cruces[Paquete]"], "values": ["[Total cruces]"]}, POS)
    assert "title" not in r["visual"]["visual"]["visualContainerObjects"]


def test_lo_que_gobierna_el_tema_no_se_pisa(proyecto, monkeypatch):
    """Con un tema completo del repo aplicado, el titulo lo pone el tema."""
    claro = theme.build_theme("claro")
    monkeypatch.setattr(theme, "current_theme", lambda active: claro)

    r = visual_factory.build_visual(
        proyecto, "barChart",
        {"category": ["Cruces[Paquete]"], "values": ["[Total cruces]"]}, POS,
        title="Cruces por paquete")
    contenedor = r["visual"]["visual"]["visualContainerObjects"]

    assert set(_props(r["visual"], "visualContainerObjects", "title")) == {
        "text", "show"}
    assert "background" not in contenedor and "border" not in contenedor
    assert set(r["default_style"]["theme_governs"]) == {
        "title", "background", "border"}


def test_un_visual_clonado_no_recibe_el_formato_por_defecto(proyecto):
    """El clon conserva el estilo de su plantilla: no se le superpone nada."""
    primero = visual_factory.build_visual(
        proyecto, "card", {"values": ["[Total cruces]"]}, POS, title="A")
    plantilla = (Path(proyecto.report_dir) / "definition" / "pages" / "p1"
                 / "visuals" / "v1" / "visual.json")
    plantilla.parent.mkdir(parents=True)
    plantilla.write_text(json.dumps({**primero["visual"], "name": "v1"}),
                         encoding="utf-8")
    meta = Path(proyecto.report_dir) / "definition" / "pages"
    (meta / "p1" / "page.json").write_text(json.dumps({
        "name": "p1", "displayName": "p1", "width": 1280, "height": 720}),
        encoding="utf-8")
    paginas = json.loads((meta / "pages.json").read_text(encoding="utf-8"))
    paginas.setdefault("pageOrder", []).append("p1")
    (meta / "pages.json").write_text(json.dumps(paginas), encoding="utf-8")

    r = visual_factory.build_visual(
        proyecto, "card", {"values": ["[Total cruces]"]}, POS, title="B")

    assert r["origin"].startswith("clonado de")
    assert "default_style" not in r


# ----------------------------------------------- el validador oficial lo acepta
def _hay_cli() -> bool:
    try:
        return bool(report_validator.estado()["available"])
    except Exception:                                        # pragma: no cover
        return False


@pytest.mark.abre
def test_la_pagina_del_comite_con_formato_por_defecto_pasa_el_validador(proyecto):
    if not _hay_cli():
        pytest.skip("hace falta el CLI oficial")
    md = tmdl_reader.read_semantic_model(proyecto)
    visuales = [
        {"type": "card", "title": "Cruces reales",
         "position": {"x": 16, "y": 16, "width": 196, "height": 110},
         "fields": {"values": ["[Total cruces]"]}},
        {"type": "barChart", "title": "Cruces por paquete",
         "position": {"x": 16, "y": 142, "width": 616, "height": 390},
         "fields": {"category": ["Cruces[Paquete]"],
                    "values": ["[Total cruces]"]}},
        {"type": "clusteredColumnChart", "title": "Valor por paquete",
         "position": {"x": 648, "y": 142, "width": 616, "height": 390},
         "fields": {"category": ["Cruces[Paquete]"],
                    "values": ["[Total cruces]"]}},
        {"type": "tableEx", "title": "Cronograma",
         "position": {"x": 16, "y": 548, "width": 760, "height": 260},
         "fields": {"values": ["Cruces[Tarea]", "Cruces[Paquete]"]}},
    ]
    spec = {"schema_version": "1.0",
            "page": {"name": "comite", "displayName": "Comite de obra"},
            "visuals": visuales}
    page_spec.apply_spec(proyecto, page_spec.compile_spec(proyecto, spec, md))

    res = report_validator.validar_informe(Path(proyecto.report_dir))
    assert res.status != report_validator.UNAVAILABLE, res.detail
    errores = [d for d in res.diagnostics if d.severity == "error"]
    assert errores == [], [f"{d.code} {d.path} {d.file}" for d in errores]

    escritos = list((Path(proyecto.report_dir) / "definition" / "pages")
                    .glob("*/visuals/*/visual.json"))
    assert len(escritos) == 4
    for archivo in escritos:
        vis = json.loads(archivo.read_text(encoding="utf-8"))["visual"]
        titulo = vis["visualContainerObjects"]["title"][0]["properties"]
        assert titulo["fontSize"]["expr"]["Literal"]["Value"] == "12D", archivo
