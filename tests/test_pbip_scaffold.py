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


def test_escribe_los_archivos_que_el_validador_oficial_exige(tmp_path):
    """`.platform` y `definition/version.json` no son opcionales.

    Sin ellos el TMDL parsea, el validador propio dice que todo esta bien, y
    Power BI Desktop abre una ventana 'Sin titulo' con el modelo vacio: ni
    carga ni explica por que. El validador oficial de Microsoft los reporta
    como PBIR_PLATFORM_MISSING y PBIR_VERSION_MISSING.
    """
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    informe = Path(r["report_dir"])
    modelo = Path(r["semantic_model_dir"])

    assert (informe / ".platform").exists()
    assert (informe / "definition" / "version.json").exists()
    # El modelo semantico tambien lleva el suyo.
    assert (modelo / ".platform").exists()

    plataforma = json.loads((informe / ".platform").read_text(encoding="utf-8"))
    assert plataforma["metadata"]["type"] == "Report"
    assert plataforma["metadata"]["displayName"] == "Demo"
    assert plataforma["config"]["logicalId"]

    del_modelo = json.loads((modelo / ".platform").read_text(encoding="utf-8"))
    assert del_modelo["metadata"]["type"] == "SemanticModel"
    # Dos artefactos distintos no pueden compartir identidad.
    assert del_modelo["config"]["logicalId"] != plataforma["config"]["logicalId"]


def test_el_esqueleto_se_revisa_con_el_validador_oficial(tmp_path):
    """El generador comprueba lo que escribe, si el CLI esta disponible.

    Si no lo esta se dice (`checked: False`) en vez de darlo por bueno.
    """
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    revision = r["report_validation"]

    assert "checked" in revision
    if revision["checked"]:
        assert revision["status"] in ("passed", "passed_with_warnings")
    else:
        assert revision["reason"]


def test_el_tema_base_lleva_reportVersionAtImport(tmp_path):
    """Power BI lo exige, y sin el se niega a abrir el informe.

    El mensaje es literal: "La propiedad necesaria 'reportVersionAtImport' no
    se incluyo en la propiedad /themeCollection/baseTheme de report.json".
    Ningun validador de esquema lo detectaba.
    """
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    reporte = json.loads((Path(r["report_dir"]) / "definition" / "report.json")
                         .read_text(encoding="utf-8"))
    base = reporte["themeCollection"]["baseTheme"]

    assert "reportVersionAtImport" in base
    # Tiene que describir lo que este generador escribe de verdad.
    assert set(base["reportVersionAtImport"]) == {"report", "page", "visual"}


def test_el_tema_base_es_propio_no_el_de_microsoft(tmp_path):
    """Copiar CY26SU05.json a un repositorio Apache-2.0 no es nuestro."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    reporte = json.loads((Path(r["report_dir"]) / "definition" / "report.json")
                         .read_text(encoding="utf-8"))
    assert reporte["themeCollection"]["baseTheme"]["name"] != "CY26SU05"


def test_no_declara_un_tema_que_no_puede_resolver(tmp_path):
    """Declarar un tema sin su archivo revienta Power BI al auto-guardar.

    `report.json` decia `baseTheme: CY26SU05` pero no se escribia
    `StaticResources/SharedResources/BaseThemes/CY26SU05.json` ni el
    `resourcePackages` que lo resuelve. Desktop lo busca, obtiene null y lanza
    NullReferenceException en GetEnhancedReportDocument. El modelo cargaba
    bien: fallaba solo la vista del informe.

    O se declara con su archivo, o no se declara. Un esqueleto no puede
    prometer un recurso que no trae.
    """
    r = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    definicion = Path(r["report_dir"]) / "definition"
    reporte = json.loads((definicion / "report.json").read_text(encoding="utf-8"))

    declarados = set()
    for paquete in reporte.get("resourcePackages", []):
        for item in paquete.get("items", []):
            declarados.add(item.get("name"))

    for clave, tema in (reporte.get("themeCollection") or {}).items():
        nombre = tema.get("name")
        assert nombre in declarados, (
            f"themeCollection.{clave} declara '{nombre}' y no hay ningun "
            f"resourcePackage que lo resuelva: {declarados}")

    # Y lo declarado tiene que existir en disco.
    for paquete in reporte.get("resourcePackages", []):
        carpeta = Path(r["report_dir"]) / "StaticResources" / paquete["name"]
        for item in paquete.get("items", []):
            assert (carpeta / item["path"]).exists(), \
                f"declarado y ausente: {paquete['name']}/{item['path']}"


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


def test_fallo_tardio_no_deja_un_proyecto_parcial(tmp_path, monkeypatch):
    """Antes se escribian once archivos antes de ejecutar el validador."""
    def falla(_report_dir):
        raise pbip_scaffold.ScaffoldError("fallo inyectado de validacion")

    monkeypatch.setattr(pbip_scaffold, "_revisar_informe", falla)
    with pytest.raises(pbip_scaffold.ScaffoldError):
        pbip_scaffold.crear_proyecto(tmp_path, "Demo")

    assert not (tmp_path / "Demo").exists()
    assert not list(tmp_path.glob(".hz_stage_*"))


def test_overwrite_reemplaza_el_arbol_entero_y_respalda_lo_anterior(tmp_path):
    primero = pbip_scaffold.crear_proyecto(tmp_path, "Demo")
    raiz = Path(primero["project_dir"])
    residuo = raiz / "Demo.Report" / "definition" / "pages" / "pagina_vieja"
    residuo.mkdir()
    (residuo / "page.json").write_text('{"viejo": true}', encoding="utf-8")

    segundo = pbip_scaffold.crear_proyecto(tmp_path, "Demo", overwrite=True)

    assert not residuo.exists(), "overwrite mezclo paginas viejas con el proyecto nuevo"
    journal = Path(segundo["publication"]["transaction"]["journal"])
    respaldo = (journal / "files" / "Demo.Report" / "definition" / "pages"
                / "pagina_vieja" / "page.json")
    assert respaldo.read_text(encoding="utf-8") == '{"viejo": true}'


def test_lienzo_no_positivo_falla_antes_de_crear_carpetas(tmp_path):
    with pytest.raises(pbip_scaffold.ScaffoldError):
        pbip_scaffold.crear_proyecto(tmp_path, "Demo", width=0)
    assert not (tmp_path / "Demo").exists()
    assert not list(tmp_path.glob(".hz_stage_*"))


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
