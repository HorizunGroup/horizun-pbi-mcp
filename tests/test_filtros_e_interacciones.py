"""Filtros de informe/pagina/visual e interacciones entre visuales.

La trampa de los filtros PBIR es que tienen dos mitades con reglas distintas:
`field` referencia la tabla por NOMBRE y la consulta interna la referencia por
ALIAS. Escribir el nombre en los dos sitios produce un filtro que Power BI
ignora sin decir nada, que es justo lo que estas pruebas evitan.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import filter_builder as fb
from horizun_pbi_mcp.pbip.filter_builder import FilterBuildError


# ------------------------------------------------------------- filtros -------
def test_el_campo_va_por_nombre_y_la_consulta_por_alias():
    f = fb.build_filter({"field": "Calendar[Year]", "values": [2024, 2025]})

    assert f["field"]["Column"]["Expression"]["SourceRef"] == {"Entity": "Calendar"}
    consulta = f["filter"]
    assert consulta["From"] == [{"Name": "c", "Entity": "Calendar", "Type": 0}]
    dentro = (consulta["Where"][0]["Condition"]["In"]["Expressions"][0]
              ["Column"]["Expression"]["SourceRef"])
    assert dentro == {"Source": "c"}


def test_los_valores_llevan_el_sufijo_de_tipo_del_motor():
    f = fb.build_filter({"field": "T[C]", "values": [2024, "texto", 1.5, None]})
    valores = [v[0]["Literal"]["Value"] for v in f["filter"]["Where"][0]
               ["Condition"]["In"]["Values"]]
    assert valores == ["2024L", "'texto'", "1.5D", "null"]


def test_excluir_envuelve_la_condicion_en_un_not():
    f = fb.build_filter({"field": "T[C]", "values": ["X"], "exclude": True})
    assert "Not" in f["filter"]["Where"][0]["Condition"]


def test_el_nombre_es_estable_entre_construcciones():
    """PBIR exige nombre; derivarlo del contenido hace el spec reproducible."""
    a = fb.build_filter({"field": "T[C]", "values": ["X"]})
    b = fb.build_filter({"field": "T[C]", "values": ["X"]})
    assert a["name"] == b["name"] and len(a["name"]) == 20


def test_un_filtro_sin_valores_declara_el_campo_sin_acotar():
    """Es lo que escribe Power BI cuando el campo esta en el panel sin filtrar."""
    f = fb.build_filter({"field": "T[C]", "type": "Advanced"})
    assert "filter" not in f
    assert f["type"] == "Advanced"


def test_raw_permite_pasar_una_consulta_ya_construida():
    crudo = {"Version": 2, "From": [], "Where": []}
    f = fb.build_filter({"field": "T[C]", "type": "Advanced", "raw": crudo})
    assert f["filter"] is crudo


def test_referencia_de_campo_mal_formada_se_rechaza():
    for malo in ("T.C", "T[", "[C]x", ""):
        with pytest.raises(FilterBuildError):
            fb.build_filter({"field": malo, "values": ["x"]})


def test_tipo_de_filtro_desconocido_se_rechaza():
    with pytest.raises(FilterBuildError) as exc:
        fb.build_filter({"field": "T[C]", "type": "AlgoRaro", "values": ["x"]})
    assert "Categorical" in str(exc.value)


def test_categorico_sin_valores_lo_dice():
    with pytest.raises(FilterBuildError) as exc:
        fb.build_categorical("T", "C", [])
    assert "al menos un valor" in str(exc.value)


def test_filter_config_vacio_es_none():
    assert fb.build_filter_config([]) is None


# -------------------------------------------------------- interacciones ------
def test_interaccion_basica():
    r = fb.build_interactions([{"source": "v1", "target": "v2", "type": "NoFilter"}],
                              ["v1", "v2"])
    assert r == [{"source": "v1", "target": "v2", "type": "NoFilter"}]


def test_interaccion_a_un_visual_inexistente_se_rechaza():
    """No falla al abrir: simplemente no hace nada. Encontrarlo despues es caro."""
    with pytest.raises(FilterBuildError) as exc:
        fb.build_interactions([{"source": "v1", "target": "fantasma"}], ["v1", "v2"])
    assert "fantasma" in str(exc.value)


def test_tipo_de_interaccion_desconocido_se_rechaza():
    with pytest.raises(FilterBuildError):
        fb.build_interactions([{"source": "a", "target": "b", "type": "Teletransporte"}])


def test_interaccion_incompleta_se_rechaza():
    with pytest.raises(FilterBuildError):
        fb.build_interactions([{"source": "a"}])


# ---------------------------------------------------- integrado en el spec ---
def test_el_visual_lleva_sus_propios_filtros(sample_pbip, session):
    from horizun_pbi_mcp.pbip import project_locator, tmdl_reader
    from horizun_pbi_mcp.services import page_spec

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    modelo = tmdl_reader.read_semantic_model(activo)

    spec = {"schema_version": "1.0",
            "page": {"name": "P", "width": 1280, "height": 720},
            "visuals": [{"type": "card", "fields": {"values": ["Ventas[Total]"]},
                         "position": {"x": 0, "y": 0, "width": 200, "height": 100},
                         "filters": [{"field": "Ventas[Region]", "values": ["Sur"]}]}]}
    comp = page_spec.compile_spec(activo, spec, modelo, seed="p")
    filtros = comp["visuals"][0]["visual"]["filterConfig"]["filters"]
    assert filtros[0]["field"]["Column"]["Property"] == "Region"
