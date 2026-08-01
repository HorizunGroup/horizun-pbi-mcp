"""Crear un .pbip desde cero, para poder partir solo de rutas de archivos.

Sin esto, `pbi_add_table_from_file` necesitaba un proyecto que ya existiera, asi
que "dame un tablero a partir de estos CSV" seguia empezando a mano en Power BI
Desktop. El esqueleto cierra ese hueco.

La prueba que sostiene el resto es la ultima: el proyecto recien creado tiene
que pasar los mismos validadores que cualquier otro. Un esqueleto que no abre
no es un punto de partida, es una trampa.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbip import pbip_scaffold, project_locator, table_from_file
from services import tmdl_validate


def test_crea_la_estructura_completa(tmp_path):
    r = pbip_scaffold.crear_proyecto(tmp_path, "Presupuesto")
    raiz = Path(r["project_dir"])

    assert (raiz / "Presupuesto.pbip").exists()
    assert (raiz / "Presupuesto.Report" / "definition.pbir").exists()
    assert (raiz / "Presupuesto.Report" / "definition" / "report.json").exists()
    assert (raiz / "Presupuesto.Report" / "definition" / "pages" / "pages.json").exists()
    assert (raiz / "Presupuesto.SemanticModel" / "definition.pbism").exists()
    assert (raiz / "Presupuesto.SemanticModel" / "definition" / "model.tmdl").exists()
    assert (raiz / "Presupuesto.SemanticModel" / "definition" / "database.tmdl").exists()
    assert (raiz / "Presupuesto.SemanticModel" / "definition" / "tables").is_dir()


def test_el_informe_apunta_al_modelo_por_ruta_relativa(tmp_path):
    """Una ruta absoluta ata el proyecto a esta maquina."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    pbir = json.loads(
        (Path(r["report_dir"]) / "definition.pbir").read_text(encoding="utf-8"))
    ruta = pbir["datasetReference"]["byPath"]["path"]

    assert ruta == "../Demo.SemanticModel"
    assert ":" not in ruta


def test_trae_una_pagina_para_que_el_informe_abra(tmp_path):
    """Power BI no abre un informe sin ninguna pagina."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    pages = json.loads((Path(r["report_dir"]) / "definition" / "pages" /
                        "pages.json").read_text(encoding="utf-8"))

    assert len(pages["pageOrder"]) == 1
    assert pages["activePageName"] == pages["pageOrder"][0]
    pagina = (Path(r["report_dir"]) / "definition" / "pages" /
              pages["pageOrder"][0] / "page.json")
    assert pagina.exists()
    assert json.loads(pagina.read_text(encoding="utf-8"))["width"] == 1280


def test_el_lienzo_se_puede_elegir(tmp_path):
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo", width=1920, height=1080)
    pages = json.loads((Path(r["report_dir"]) / "definition" / "pages" /
                        "pages.json").read_text(encoding="utf-8"))
    pagina = json.loads(((Path(r["report_dir"]) / "definition" / "pages" /
                          pages["pageOrder"][0] / "page.json")
                         ).read_text(encoding="utf-8"))
    assert (pagina["width"], pagina["height"]) == (1920, 1080)


def test_no_pisa_una_carpeta_existente_sin_permiso(tmp_path):
    pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    with pytest.raises(pbip_scaffold.ScaffoldError) as exc:
        pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    assert "overwrite" in str(exc.value)


def test_un_nombre_con_separadores_de_ruta_se_rechaza(tmp_path):
    """El nombre no puede decidir donde se escribe."""
    with pytest.raises(pbip_scaffold.ScaffoldError):
        pbip_scaffold.crear_proyecto(tmp_path, "..\\..\\fuera")


def test_el_modelo_recien_creado_es_valido(tmp_path):
    """Un esqueleto que no pasa el validador no sirve de punto de partida."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    definition = Path(r["semantic_model_dir"]) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert resultado["valid"] is True
    assert resultado["findings"] == []


def test_de_rutas_a_tablero_sin_tocar_power_bi(tmp_path, session):
    """El recorrido completo de la regla 1: solo con rutas de archivos.

    Crear el proyecto, abrirlo, cargarle dos archivos y que el resultado siga
    siendo valido. Es lo que antes obligaba a empezar a mano en Desktop.
    """
    csv = tmp_path / "costos.csv"
    csv.write_text("Codigo,Valor\nA-1,10527.52\nA-2,1795.40\n", encoding="utf-8")
    otro = tmp_path / "actividades.csv"
    otro.write_text("Codigo,Nombre\nA-1,Zapatas\nA-2,Vigas\n", encoding="utf-8")

    r = pbip_scaffold.crear_proyecto(tmp_path / "salida", "Obra")
    project_locator.open_project(session, r["pbip_path"])
    activo = session.require_active_pbip()

    table_from_file.agregar_tabla(activo, csv, table_name="Costos")
    table_from_file.agregar_tabla(activo, otro, table_name="Actividades")

    definition = Path(activo.semantic_model_dir) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert resultado["valid"] is True, resultado["findings"]

    modelo = (definition / "model.tmdl").read_text(encoding="utf-8-sig")
    assert "ref table Costos" in modelo
    assert "ref table Actividades" in modelo
