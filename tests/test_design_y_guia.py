"""Capa de diseno y punto de entrada.

Dos huecos del mismo tipo: tener las piezas no es lo mismo que saber usarlas.

De la capa de diseno lo que se puede comprobar sin renderizar es la GEOMETRIA
—que todo caiga sobre la rejilla, que nada se salga del lienzo, que dos paginas
del mismo sistema esten alineadas entre si— y que lo compuesto ABRA. Que se
VEA bien sigue exigiendo mirarlo, y eso no lo prueba nadie aqui.

Del punto de entrada, lo unico que de verdad importa: que las tools que nombra
EXISTAN. Una guia que manda llamar a algo inexistente es peor que no tenerla.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import ActivePbip
from pbip import pbip_scaffold, table_from_file, theme, tmdl_reader, tmdl_writer
from services import design, guide, page_spec, report_validator, tmdl_validate
from services.design import DesignError

from tests.test_generadores_abren import requiere_oraculos  # noqa: F401


# ================================================================= fixtures ===
@pytest.fixture
def proyecto(tmp_path, session, isolated_settings):
    r = pbip_scaffold.crear_proyecto(tmp_path, "Dis")
    active = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Dis.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Dis",
        has_pbir=True, has_tmdl=True)
    session.set_active_pbip(active)

    datos = tmp_path / "datos"
    datos.mkdir()
    (datos / "Ventas.csv").write_text(
        "Mes,Zona,Producto,Unidades,Importe\n"
        "2026-01,Norte,Cemento,120,1450.75\n"
        "2026-02,Sur,Acero,85,2310.40\n", encoding="utf-8")
    table_from_file.agregar_tabla(active, datos / "Ventas.csv", "Ventas")
    for nombre, expr in [("Importe Total", "SUM(Ventas[Importe])"),
                         ("Unidades Total", "SUM(Ventas[Unidades])"),
                         ("Ticket Medio",
                          "DIVIDE([Importe Total],[Unidades Total])")]:
        tmdl_writer.create_measure_pbip(active, "Ventas", nombre, expr,
                                        format_string="#,0.00")
    return active, tmdl_reader.read_semantic_model(active), session


def _pagina_completa(sistema="informe"):
    return dict(
        title="Resumen de ventas", subtitle="Ejercicio 2026",
        kpis=["[Importe Total]", "[Unidades Total]", "[Ticket Medio]"],
        hero={"type": "lineChart", "title": "Evolucion",
              "category": "Ventas[Mes]", "values": ["[Importe Total]"]},
        supports=[{"type": "barChart", "title": "Por zona",
                   "category": "Ventas[Zona]", "values": ["[Importe Total]"]}],
        detail={"title": "Detalle",
                "values": ["Ventas[Zona]", "[Importe Total]"]})


# =============================================== la rejilla, que es el nucleo ==
@pytest.mark.parametrize("sistema", sorted(design.SISTEMAS))
def test_la_rejilla_llena_el_ancho_exacto(sistema):
    """Las 12 columnas mas sus medianiles tienen que dar el ancho util.

    Si no cuadra, cada pagina acaba con un margen distinto a la derecha y la
    incoherencia se ve aunque nadie sepa nombrarla.
    """
    t = design.tokens(sistema)
    g, lienzo = t["grid"], t["canvas"]
    todo = design.columna(sistema, 0, g["columns"])

    assert todo["x"] == g["margin"]
    assert todo["x"] + todo["width"] == lienzo["width"] - g["margin"]


@pytest.mark.parametrize("sistema", sorted(design.SISTEMAS))
@pytest.mark.parametrize("bloques", [2, 3, 4, 6])
def test_una_fila_repartida_no_deja_hueco_al_final(sistema, bloques):
    """El resto se reparte; no se abandona a la derecha."""
    t = design.tokens(sistema)
    g = t["grid"]
    piezas = [design.columna(sistema, b["start"], b["span"])
              for b in design._reparto(g["columns"], bloques)]

    assert piezas[0]["x"] == g["margin"]
    assert (piezas[-1]["x"] + piezas[-1]["width"]
            == t["canvas"]["width"] - g["margin"])


def test_pedir_mas_columnas_de_las_que_hay_se_acusa():
    with pytest.raises(DesignError):
        design.columna("informe", 0, 13)
    with pytest.raises(DesignError):
        design.columna("informe", 11, 2)


def test_no_caben_trece_indicadores_en_doce_columnas():
    """Encogerlos hasta que no se lean no es resolver el problema."""
    with pytest.raises(DesignError) as exc:
        design.componer("informe", title="X", kpis=["[A]"] * 13)
    assert "13" in str(exc.value)


# ============================================ la composicion, sin renderizar ===
@pytest.mark.parametrize("sistema", sorted(design.SISTEMAS))
def test_nada_se_sale_del_lienzo(sistema):
    spec = design.componer(sistema, **_pagina_completa())
    lienzo = design.SISTEMAS[sistema]["canvas"]
    margen = design.SISTEMAS[sistema]["grid"]["margin"]

    for v in spec["visuals"]:
        p = v["position"]
        assert p["x"] >= margen, v["id"]
        assert p["y"] >= margen, v["id"]
        assert p["x"] + p["width"] <= lienzo["width"] - margen, v["id"]
        assert p["y"] + p["height"] <= lienzo["height"] - margen + 1, v["id"]


@pytest.mark.parametrize("sistema", sorted(design.SISTEMAS))
def test_ningun_visual_se_solapa_con_otro(sistema):
    """Dos visuales encima no dan error en ningun sitio, y se ven fatal."""
    spec = design.componer(sistema, **_pagina_completa())
    cajas = [(v["id"], v["position"]) for v in spec["visuals"]]

    for i, (id_a, a) in enumerate(cajas):
        for id_b, b in cajas[i + 1:]:
            separados = (a["x"] + a["width"] <= b["x"]
                         or b["x"] + b["width"] <= a["x"]
                         or a["y"] + a["height"] <= b["y"]
                         or b["y"] + b["height"] <= a["y"])
            assert separados, f"'{id_a}' y '{id_b}' se solapan en '{sistema}'"


def test_el_titulo_tiene_el_alto_que_su_tamano_exige():
    """Por debajo del piso, Power BI corta el texto y mete barra de scroll."""
    from pbip import visual_factory

    for sistema in design.SISTEMAS:
        spec = design.componer(sistema, title="Un titulo")
        titulo = spec["visuals"][0]
        tam = design.SISTEMAS[sistema]["tipografia"]["titulo"]
        assert titulo["position"]["height"] >= visual_factory.piso_de_texto(tam)


def test_el_color_del_texto_sale_del_tema_no_de_una_constante():
    """Es el punto entero de la capa: color y geometria, una sola fuente.

    Un titulo con un color fijo se vuelve ilegible en cuanto el sistema es
    oscuro, y ese fallo no lo ve ningun validador.
    """
    for sistema, s in design.SISTEMAS.items():
        spec = design.componer(sistema, title="T")
        esperado = theme.PRESETS[s["theme"]]["tema"]["foreground"]
        assert spec["visuals"][0]["options"]["color"] == esperado


def test_la_composicion_es_determinista():
    a = design.componer("informe", **_pagina_completa())
    b = design.componer("informe", **_pagina_completa())
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_el_spec_trae_las_posiciones_puestas():
    """Si faltara una, `page_spec` recalcularia el layout y tiraria la rejilla."""
    spec = design.componer("sala", **_pagina_completa())
    for v in spec["visuals"]:
        assert set(v["position"]) >= {"x", "y", "width", "height"}, v["id"]
    assert "layout" not in spec


# ================================================= contra los oraculos reales ==
@pytest.mark.abre
@requiere_oraculos
@pytest.mark.parametrize("sistema", sorted(design.SISTEMAS))
def test_lo_que_compone_la_capa_de_diseno_abre(proyecto, sistema):
    """La prueba que de verdad importa: ¿esto lo abre Power BI?"""
    active, md, _ = proyecto
    design.aplicar(active, sistema)

    spec = design.componer(sistema, **_pagina_completa(sistema))
    page_spec.apply_spec(active, page_spec.compile_spec(active, spec, md))

    res = report_validator.validar_informe(Path(active.report_dir))
    errores = [d for d in res.diagnostics if d.severity == "error"]
    assert errores == [], [f"{d.code} {d.path}" for d in errores]

    v = tmdl_validate.validate(
        Path(active.semantic_model_dir) / "definition", use_tom=True)
    assert v["parsed"] is True


@pytest.mark.abre
@requiere_oraculos
def test_una_pagina_sin_protagonista_tambien_abre(proyecto):
    """Solo indicadores y detalle: el hueco del medio no puede romper nada."""
    active, md, _ = proyecto
    spec = design.componer(
        "informe", title="Solo cifras",
        kpis=["[Importe Total]", "[Unidades Total]"],
        detail={"values": ["Ventas[Zona]", "[Importe Total]"]})
    page_spec.apply_spec(active, page_spec.compile_spec(active, spec, md))

    res = report_validator.validar_informe(Path(active.report_dir))
    assert [d for d in res.diagnostics if d.severity == "error"] == []


# ========================================================= punto de entrada ====
def test_la_guia_solo_nombra_tools_que_existen():
    """Una guia que manda llamar a algo inexistente es peor que no tenerla.

    Es la misma clase de defecto que `cardVisual`: algo anunciado que no se
    puede usar. Aqui se comprueba contra el contrato congelado.
    """
    import re

    from tests import contract_utils

    reales = {t["name"] for t in contract_utils.snapshot_from_server()["tools"]}
    texto = Path(guide.__file__).read_text(encoding="utf-8")
    nombradas = set(re.findall(r"pbi_[a-z0-9_]+", texto))

    assert nombradas <= reales, sorted(nombradas - reales)


def test_cada_tarea_frecuente_es_una_secuencia_de_tools_reales():
    from tests import contract_utils

    reales = {t["name"] for t in contract_utils.snapshot_from_server()["tools"]}
    for tarea in guide.TAREAS:
        assert tarea["steps"], tarea["task"]
        faltan = [s for s in tarea["steps"] if s not in reales]
        assert not faltan, f"'{tarea['task']}' nombra {faltan}"


def test_sin_proyecto_la_guia_dice_como_conseguir_uno(session):
    r = guide.situacion(session)
    assert r["project"] is None
    tools = [p["tool"] for p in r["next_steps"]]
    assert "pbi_open_pbip_project" in tools
    assert "pbi_create_pbip_project" in tools


def test_cada_paso_explica_por_que_toca_ahora(proyecto):
    """Un paso sin motivo es una orden, y una orden no se puede saltar con criterio."""
    _, _, session = proyecto
    for paso in guide.situacion(session)["next_steps"]:
        assert paso["why"].strip(), paso["tool"]
        assert len(paso["why"]) > 30, paso["tool"]


def test_la_guia_distingue_el_modelo_vacio_de_uno_montado(tmp_path, session,
                                                          isolated_settings):
    """El consejo util depende del estado; si no, es un folleto."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Vacio")
    active = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Vacio.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Vacio",
        has_pbir=True, has_tmdl=True)
    session.set_active_pbip(active)

    vacio = guide.situacion(session)
    assert "pbi_add_table_from_file" in [p["tool"] for p in vacio["next_steps"]]

    datos = tmp_path / "d"
    datos.mkdir()
    (datos / "V.csv").write_text("A,B\n1,2\n", encoding="utf-8")
    table_from_file.agregar_tabla(active, datos / "V.csv", "V")
    con_tabla = guide.situacion(session)
    assert "pbi_create_measure" in [p["tool"] for p in con_tabla["next_steps"]]

    tmdl_writer.create_measure_pbip(active, "V", "Total", "SUM(V[A])")
    con_medida = guide.situacion(session)
    assert "pbi_compose_page" in [p["tool"] for p in con_medida["next_steps"]]


def test_una_pagina_vacia_no_cuenta_como_pagina_hecha(proyecto):
    """El esqueleto trae una pagina vacia: decir "ya tienes una" hace desconfiar."""
    _, _, session = proyecto
    r = guide.situacion(session)

    assert r["project"]["pages"] >= 1
    assert r["project"]["visuals"] == 0
    assert "vacias" in r["situation"]
    assert "pbi_compose_page" in [p["tool"] for p in r["next_steps"]]
