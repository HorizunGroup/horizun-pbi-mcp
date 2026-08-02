"""Conversion .pbix -> .pbip: lectura del contenedor y traduccion a PBIR.

Las equivalencias que se comprueban aqui no son inventadas: se derivaron
comparando un informe real guardado por Power BI Desktop en los dos formatos
(heredado y PBIR). Por eso las pruebas afirman valores concretos —"Direction 2
es Descending"— y no solo que la conversion no reviente.

Nada de esto necesita Power BI Desktop: la mitad del modelo se prueba aparte,
marcada como `live`.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from pbip import layout_to_pbir, pbix_reader, pbix_to_pbip
from powerbi.errors import PowerBIMCPError
from services import pbir_schema, report_validator


# --------------------------------------------------------------- utilidades --
def _u16(data) -> bytes:
    """Codifica como lo hace Power BI en las partes de texto del .pbix."""
    texto = data if isinstance(data, str) else json.dumps(data)
    return texto.encode("utf-16-le")


def _visual(nombre, tipo="clusteredColumnChart", **extra):
    config = {
        "name": nombre,
        "layouts": [{"id": 0, "position": {"x": 10, "y": 20, "z": 3000,
                                           "width": 400, "height": 300,
                                           "tabOrder": 3000}}],
        "singleVisual": {
            "visualType": tipo,
            "projections": {"Category": [{"queryRef": "Ventas.Region"}],
                            "Y": [{"queryRef": "Sum(Ventas.Importe)"}]},
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": "v", "Entity": "Ventas", "Type": 0}],
                "Select": [
                    {"Column": {"Expression": {"SourceRef": {"Source": "v"}},
                                "Property": "Region"},
                     "Name": "Ventas.Region",
                     "NativeReferenceName": "Region"},
                    {"Aggregation": {
                        "Expression": {"Column": {
                            "Expression": {"SourceRef": {"Source": "v"}},
                            "Property": "Importe"}},
                        "Function": 0},
                     "Name": "Sum(Ventas.Importe)"},
                ],
                "OrderBy": [{"Direction": 2, "Expression": {
                    "Column": {"Expression": {"SourceRef": {"Source": "v"}},
                               "Property": "Region"}}}],
            },
            "hasDefaultSort": True,
            "objects": {"labels": [{"properties": {}}]},
            "vcObjects": {"title": [{"properties": {}}]},
            "drillFilterOtherVisuals": True,
        },
    }
    config["singleVisual"].update(extra)
    return {"x": 10, "y": 20, "z": 3000, "width": 400, "height": 300,
            "tabOrder": 3000, "config": json.dumps(config),
            "filters": "[]"}


def _layout(secciones=None, config_extra=None, **raiz):
    config = {"version": "5.68", "activeSectionIndex": 0,
              "themeCollection": {"baseTheme": {
                  "name": "CY24SU10", "type": 2,
                  "version": {"visual": "2.4.0", "report": "3.0.0",
                              "page": "2.3.0"}}}}
    config.update(config_extra or {})
    layout = {
        "id": 0,
        "config": json.dumps(config),
        "layoutOptimization": 0,
        "publicCustomVisuals": [],
        "sections": secciones if secciones is not None else [{
            "name": "pagina0000000000000a",
            "displayName": "Resumen",
            "filters": "[]",
            "ordinal": 0,
            "config": "{}",
            "displayOption": 1,
            "width": 1280,
            "height": 720,
            "visualContainers": [_visual("visual00000000000001")],
        }],
    }
    layout.update(raiz)
    return layout


def _escribir_pbix(ruta, *, layout=None, pbir=None, assets=None,
                   con_modelo=True, connections=None):
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Version", _u16("1.28"))
        zf.writestr("Settings", _u16({"Version": 4}))
        zf.writestr("Metadata", _u16({"Version": 5}))
        zf.writestr("Connections", json.dumps(
            connections or {"Version": 3}).encode("utf-8"))
        if layout is not None:
            zf.writestr("Report/Layout", _u16(layout))
        for nombre, contenido in (pbir or {}).items():
            zf.writestr(f"Report/definition/{nombre}",
                        json.dumps(contenido).encode("utf-8"))
        for nombre, contenido in (assets or {}).items():
            datos = (json.dumps(contenido).encode("utf-8")
                     if isinstance(contenido, (dict, list)) else contenido)
            zf.writestr(f"Report/{nombre}", datos)
        if con_modelo:
            zf.writestr("DataModel", _u16(
                "This backup was created using XPress9 compression" + "." * 200))
    return ruta


@pytest.fixture
def pbix_heredado(tmp_path):
    return _escribir_pbix(tmp_path / "Informe.pbix", layout=_layout())


def _convertir(layout):
    return layout_to_pbir.convert_layout(layout)


def _visual_convertido(resultado):
    clave = next(k for k in resultado.files if k.endswith("visual.json"))
    return resultado.files[clave]


# ------------------------------------------------------ lectura del .pbix ----
def test_lee_informe_heredado_y_detecta_modelo(pbix_heredado):
    contents = pbix_reader.read_pbix(pbix_heredado)
    assert contents.report_format == "layout"
    assert contents.has_data_model is True
    assert contents.summary()["page_count"] == 1


def test_detecta_pbir_embebido(tmp_path):
    ruta = _escribir_pbix(tmp_path / "Moderno.pbix", pbir={
        "report.json": {"$schema": "x"},
        "pages/pages.json": {"pageOrder": ["p1"]},
        "pages/p1/page.json": {"name": "p1"},
    })
    contents = pbix_reader.read_pbix(ruta)
    assert contents.report_format == "pbir"
    assert contents.summary()["page_count"] == 1


@pytest.mark.parametrize("parte", [
    "Report/definition/../../../fuera.json",
    "Report/StaticResources/RegisteredResources/../fuera.json",
    "Report/CustomVisuals/paquete/archivo.json:stream",
])
def test_rechaza_rutas_inseguras_dentro_del_pbix(tmp_path, parte):
    ruta = tmp_path / "RutaInsegura.pbix"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Report/Layout", _u16(_layout()))
        zf.writestr(parte, b"no debe salir del staging")

    with pytest.raises(pbix_reader.PbixReadError) as exc:
        pbix_reader.read_pbix(ruta)

    assert exc.value.details["part"] == parte
    assert not (tmp_path / "fuera.json").exists()


def test_rechaza_partes_que_chocan_por_mayusculas_en_windows(tmp_path):
    ruta = tmp_path / "Colision.pbix"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Report/definition/report.json", b"{}")
        zf.writestr("Report/definition/REPORT.JSON", b"{}")

    with pytest.raises(pbix_reader.PbixReadError) as exc:
        pbix_reader.read_pbix(ruta)

    assert len(exc.value.details["parts"]) == 2


def test_informe_con_conexion_en_vivo_no_lleva_modelo(tmp_path):
    ruta = _escribir_pbix(tmp_path / "Thin.pbix", layout=_layout(),
                          con_modelo=False,
                          connections={"Version": 3, "RemoteArtifacts": [
                              {"DatasetId": "abc"}]})
    contents = pbix_reader.read_pbix(ruta)
    assert contents.is_thin_report is True
    assert contents.remote_artifacts == [{"DatasetId": "abc"}]


def test_texto_mal_codificado_avisa_pero_no_aborta(tmp_path):
    """Un caracter roto dentro de un texto no debe tumbar toda la conversion."""
    ruta = tmp_path / "Roto.pbix"
    layout = _layout()
    layout["sections"][0]["displayName"] = "TituloZ"
    crudo = bytearray(_u16(layout))
    # 'Z' es 5a 00 en UTF-16LE; se cambia por un sustituto alto suelto, que es
    # invalido pero deja intacta la estructura del JSON.
    posicion = crudo.index(b"Z\x00")
    crudo[posicion:posicion + 2] = b"\x00\xd8"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Report/Layout", bytes(crudo))
    contents = pbix_reader.read_pbix(ruta)
    assert contents.layout is not None
    assert contents.layout["sections"][0]["displayName"].startswith("Titulo")
    assert any("codificacion" in w for w in contents.warnings)


def test_pbix_sin_informe_da_error_claro(tmp_path):
    ruta = tmp_path / "Vacio.pbix"
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Version", _u16("1.28"))
    with pytest.raises(PowerBIMCPError) as exc:
        pbix_to_pbip.convert(ruta, tmp_path / "out")
    assert "no contiene ningun informe" in exc.value.message


# ------------------------------------------- traduccion de consulta y orden --
def test_alias_de_tabla_se_resuelve_a_nombre_de_entidad():
    """El heredado referencia la tabla por alias; PBIR, por nombre."""
    visual = _visual_convertido(_convertir(_layout()))
    campo = visual["visual"]["query"]["queryState"]["Category"]["projections"][0]["field"]
    assert campo["Column"]["Expression"]["SourceRef"] == {"Entity": "Ventas"}


def test_proyeccion_toma_la_expresion_del_prototype_query():
    """PBIR fusiona `projections` y `prototypeQuery.Select` en un solo objeto."""
    visual = _visual_convertido(_convertir(_layout()))
    y = visual["visual"]["query"]["queryState"]["Y"]["projections"][0]
    assert y["queryRef"] == "Sum(Ventas.Importe)"
    assert y["field"]["Aggregation"]["Function"] == 0
    assert y["field"]["Aggregation"]["Expression"]["Column"][
        "Expression"]["SourceRef"] == {"Entity": "Ventas"}
    assert y["nativeQueryRef"] == "Sum(Ventas.Importe)"


def test_native_query_ref_se_conserva():
    visual = _visual_convertido(_convertir(_layout()))
    cat = visual["visual"]["query"]["queryState"]["Category"]["projections"][0]
    assert cat["nativeQueryRef"] == "Region"


def test_column_properties_pasa_alias_y_formato_a_la_proyeccion():
    layout = _layout()
    contenedor = layout["sections"][0]["visualContainers"][0]
    config = json.loads(contenedor["config"])
    config["singleVisual"]["columnProperties"] = {
        "Sum(Ventas.Importe)": {
            "displayName": "Importe total",
            "formatString": "$ #,0.00",
        },
    }
    contenedor["config"] = json.dumps(config)

    visual = _visual_convertido(_convertir(layout))
    medida = visual["visual"]["query"]["queryState"]["Y"]["projections"][0]

    assert medida["displayName"] == "Importe total"
    assert medida["format"] == "$ #,0.00"


def test_orden_descendente_es_direction_2():
    """Direction 1/2 -> Ascending/Descending, verificado contra un par real."""
    visual = _visual_convertido(_convertir(_layout()))
    orden = visual["visual"]["query"]["sortDefinition"]
    assert orden["sort"][0]["direction"] == "Descending"
    assert orden["isDefaultSort"] is True
    assert orden["sort"][0]["field"]["Column"][
        "Expression"]["SourceRef"] == {"Entity": "Ventas"}


def test_campo_sin_entrada_en_select_se_reconstruye_y_avisa():
    layout = _layout()
    config = json.loads(layout["sections"][0]["visualContainers"][0]["config"])
    config["singleVisual"]["prototypeQuery"]["Select"] = []
    layout["sections"][0]["visualContainers"][0]["config"] = json.dumps(config)
    resultado = _convertir(layout)
    visual = _visual_convertido(resultado)
    # La referencia simple se puede reconstruir; la agregacion no.
    assert "Category" in visual["visual"]["query"]["queryState"]
    assert "Y" not in visual["visual"]["query"]["queryState"]
    assert any("no estaba en prototypeQuery.Select" in w
               for w in resultado.warnings)
    assert any("Sum(Ventas.Importe)" in d["what"] for d in resultado.dropped)


# ------------------------------------------------ formato de pagina y visual --
def test_display_option_1_es_fit_to_page():
    pagina = _convertir(_layout()).files["pages/pagina0000000000000a/page.json"]
    assert pagina["displayOption"] == "FitToPage"
    assert pagina["displayName"] == "Resumen"
    assert pagina["width"] == 1280


def test_pagina_oculta_se_lee_del_config_de_la_seccion():
    layout = _layout()
    layout["sections"][0]["config"] = json.dumps({"visibility": 1})
    pagina = _convertir(layout).files["pages/pagina0000000000000a/page.json"]
    assert pagina["visibility"] == "HiddenInViewMode"


def test_objects_y_vc_objects_cambian_de_sitio():
    visual = _visual_convertido(_convertir(_layout()))
    assert "labels" in visual["visual"]["objects"]
    assert "title" in visual["visual"]["visualContainerObjects"]
    assert visual["visual"]["drillFilterOtherVisuals"] is True


def test_posicion_sale_del_layout_del_visual():
    visual = _visual_convertido(_convertir(_layout()))
    assert visual["position"] == {"x": 10, "y": 20, "z": 3000,
                                  "height": 300, "width": 400, "tabOrder": 3000}


def test_grupo_de_visuales_saca_is_hidden_al_contenedor():
    """`isHidden` describe al contenedor, no al grupo: el esquema lo prohibe ahi."""
    contenedor = {"x": 0, "y": 0, "z": 0, "width": 100, "height": 100,
                  "config": json.dumps({
                      "name": "grupo000000000000001",
                      "layouts": [{"id": 0, "position": {"x": 0, "y": 0, "z": 0,
                                                         "width": 100,
                                                         "height": 100}}],
                      "singleVisualGroup": {"displayName": "Panel",
                                            "groupMode": 0, "isHidden": True}})}
    layout = _layout()
    layout["sections"][0]["visualContainers"] = [contenedor]
    visual = _visual_convertido(_convertir(layout))
    assert visual["visualGroup"] == {"displayName": "Panel",
                                     "groupMode": "ScaleMode"}
    assert visual["isHidden"] is True


def test_expansiones_sin_identidad_se_podan():
    """El heredado escribe `identityValues: null`; PBIR espera una lista."""
    layout = _layout()
    contenedor = layout["sections"][0]["visualContainers"][0]
    config = json.loads(contenedor["config"])
    config["singleVisual"]["expansionStates"] = [{
        "roles": ["Rows"],
        "root": {"identityValues": None, "children": [
            {"identityValues": [{"Literal": {"Value": "'A'"}}]},
            {"identityValues": None},
        ]},
    }]
    contenedor["config"] = json.dumps(config)
    raiz = _visual_convertido(_convertir(layout))["visual"]["expansionStates"][0]["root"]
    assert "identityValues" not in raiz
    assert len(raiz["children"]) == 1


def test_dos_visuales_con_el_mismo_nombre_no_chocan():
    layout = _layout()
    layout["sections"][0]["visualContainers"] = [
        _visual("repetido000000000001"), _visual("repetido000000000001")]
    resultado = _convertir(layout)
    nombres = {k for k in resultado.files if k.endswith("visual.json")}
    assert len(nombres) == 2
    assert any("se renombro" in w for w in resultado.warnings)


def test_visuales_que_solo_difieren_en_mayusculas_no_chocan_en_windows():
    layout = _layout()
    layout["sections"][0]["visualContainers"] = [
        _visual("VisualA"), _visual("visuala")]

    resultado = _convertir(layout)
    nombres = {k.casefold() for k in resultado.files
               if k.endswith("visual.json")}

    assert len(nombres) == 2
    assert any("se renombro" in w for w in resultado.warnings)


# ------------------------------------------------------------- filtros -------
def test_filtro_renombra_expression_a_field_y_destipa_how_created():
    layout = _layout()
    layout["sections"][0]["filters"] = json.dumps([{
        "name": "filtro00000000000001",
        "expression": {"Column": {"Expression": {"SourceRef": {"Entity": "Ventas"}},
                                  "Property": "Region"}},
        "type": "Categorical",
        "howCreated": 1,
        "cachedValueItems": [{"se": "descarta"}],
    }])
    pagina = _convertir(layout).files["pages/pagina0000000000000a/page.json"]
    filtro = pagina["filterConfig"]["filters"][0]
    assert filtro["field"]["Column"]["Property"] == "Region"
    assert filtro["howCreated"] == "User"
    assert "expression" not in filtro and "cachedValueItems" not in filtro


def test_filtro_sin_nombre_recibe_uno_estable():
    layout = _layout()
    layout["sections"][0]["filters"] = json.dumps([{
        "expression": {"Column": {"Expression": {"SourceRef": {"Entity": "V"}},
                                  "Property": "C"}}, "type": "Categorical"}])
    primero = _convertir(layout).files["pages/pagina0000000000000a/page.json"]
    segundo = _convertir(layout).files["pages/pagina0000000000000a/page.json"]
    nombre = primero["filterConfig"]["filters"][0]["name"]
    assert len(nombre) == 20
    assert nombre == segundo["filterConfig"]["filters"][0]["name"]


def test_orden_personalizado_de_filtros_se_conserva_en_cada_scope():
    layout = _layout(config_extra={"filterSortOrder": 3})
    layout["sections"][0]["config"] = json.dumps({"filterSortOrder": 2})
    visual = layout["sections"][0]["visualContainers"][0]
    config_visual = json.loads(visual["config"])
    config_visual["singleVisual"]["filterSortOrder"] = 1
    visual["config"] = json.dumps(config_visual)

    resultado = _convertir(layout)
    pagina = resultado.files["pages/pagina0000000000000a/page.json"]
    visual_convertido = _visual_convertido(resultado)

    assert resultado.files["report.json"]["filterConfig"][
        "filterSortOrder"] == "Custom"
    assert pagina["filterConfig"]["filterSortOrder"] == "Descending"
    assert visual_convertido["filterConfig"][
        "filterSortOrder"] == "Ascending"


def test_ids_de_filtro_son_unicos_en_todo_el_informe():
    """Layout permite repetir IDs por scope; PBIR los exige globales."""
    filtro = {
        "name": "filtro_repetido",
        "expression": {"Column": {
            "Expression": {"SourceRef": {"Entity": "Ventas"}},
            "Property": "Region",
        }},
        "type": "Categorical",
    }
    layout = _layout()
    layout["filters"] = json.dumps([filtro])
    layout["sections"][0]["filters"] = json.dumps([filtro])
    layout["sections"][0]["visualContainers"][0]["filters"] = json.dumps(
        [filtro])

    resultado = _convertir(layout)
    pagina = resultado.files["pages/pagina0000000000000a/page.json"]
    visual = _visual_convertido(resultado)
    nombres = [
        resultado.files["report.json"]["filterConfig"]["filters"][0]["name"],
        pagina["filterConfig"]["filters"][0]["name"],
        visual["filterConfig"]["filters"][0]["name"],
    ]

    assert len(set(nombres)) == 3
    assert nombres[0] == "filtro_repetido"
    assert sum("identificador global" in aviso
               for aviso in resultado.warnings) == 2


def test_interacciones_visuales_numericas_pasan_a_enum_pbir():
    layout = _layout()
    layout["sections"][0]["config"] = json.dumps({"relationships": [
        {"source": "origen", "target": "filtro", "type": 1},
        {"source": "origen", "target": "resalta", "type": 2},
        {"source": "origen", "target": "ninguno", "type": 3},
    ]})

    pagina = _convertir(layout).files[
        "pages/pagina0000000000000a/page.json"]

    assert [r["type"] for r in pagina["visualInteractions"]] == [
        "DataFilter", "HighlightFilter", "NoFilter"]


# ----------------------------------------------------- informe y recursos ----
def test_ajustes_numericos_pasan_a_cadena_y_los_obsoletos_se_van():
    layout = _layout(config_extra={"settings": {
        "exportDataMode": 1, "queryLimitOption": 6,
        "useEnhancedTooltips": True, "useNewFilterPaneExperience": True}})
    ajustes = _convertir(layout).files["report.json"]["settings"]
    assert ajustes["exportDataMode"] == "AllowSummarizedAndUnderlying"
    assert ajustes["queryLimitOption"] == "Auto"
    assert ajustes["useEnhancedTooltips"] is True
    assert "useNewFilterPaneExperience" not in ajustes


def test_default_drill_legacy_pasa_a_settings():
    ajustes = _convertir(_layout(config_extra={
        "defaultDrillFilterOtherVisuals": False,
    })).files["report.json"]["settings"]

    assert ajustes["defaultDrillFilterOtherVisuals"] is False


def test_tema_siempre_declara_version_aunque_el_origen_no_la_tenga():
    layout = _layout(config_extra={"themeCollection": {
        "baseTheme": {"name": "CY24SU10", "type": 2},
        "customTheme": {"name": "Mio.json", "type": 1,
                        "version": {"visual": "2.4.0", "report": "3.0.0",
                                    "page": "2.3.0"}}}})
    resultado = _convertir(layout)
    temas = resultado.files["report.json"]["themeCollection"]
    assert temas["baseTheme"]["reportVersionAtImport"]["visual"] == "2.4.0"
    assert temas["customTheme"]["type"] == "RegisteredResources"
    assert any("sin version registrada" in w for w in resultado.warnings)


def test_paquetes_de_recursos_cambian_los_enums_numericos():
    layout = _layout(resourcePackages=[
        {"resourcePackage": {"name": "SharedResources", "type": 2, "items": [
            {"type": 202, "path": "BaseThemes/CY24SU10.json",
             "name": "CY24SU10"}], "disabled": False}},
        {"resourcePackage": {"name": "RegisteredResources", "type": 1, "items": [
            {"type": 100, "path": "logo.png", "name": "logo.png"}],
            "disabled": False}},
    ])
    paquetes = _convertir(layout).files["report.json"]["resourcePackages"]
    assert paquetes[0]["type"] == "SharedResources"
    assert paquetes[0]["items"][0]["type"] == "BaseTheme"
    assert paquetes[1]["items"][0]["type"] == "Image"


def test_marcadores_heredados_se_separan_en_archivo_e_indice():
    estado = {"version": "1.3", "activeSection": "pagina0000000000000a",
              "sections": {"pagina0000000000000a": {
                  "visualContainers": {}}}}
    layout = _layout(config_extra={"bookmarks": [{
        "name": "grupo1", "displayName": "Escenarios", "children": [{
            "name": "b1", "displayName": "Vista inicial",
            "options": {"targetVisualNames": []},
            "explorationState": estado,
        }],
    }]})
    resultado = _convertir(layout)
    marcador = resultado.files["bookmarks/b1.bookmark.json"]
    indice = resultado.files["bookmarks/bookmarks.json"]

    assert marcador["explorationState"] == estado
    assert marcador["displayName"] == "Vista inicial"
    assert indice["items"] == [{
        "name": "grupo1", "displayName": "Escenarios", "children": ["b1"]}]
    assert not resultado.dropped


def test_marcador_sin_estado_se_declara_perdido():
    resultado = _convertir(
        _layout(config_extra={"bookmarks": [{"name": "b1"}]}))
    assert any("explorationState" in d["what"] for d in resultado.dropped)
    assert "bookmarks/bookmarks.json" not in resultado.files


def test_orden_de_paginas_respeta_el_ordinal():
    layout = _layout(secciones=[
        {"name": "segunda00000000000b", "displayName": "B", "ordinal": 1,
         "filters": "[]", "config": "{}", "displayOption": 1,
         "width": 1280, "height": 720, "visualContainers": []},
        {"name": "primera00000000000a", "displayName": "A", "ordinal": 0,
         "filters": "[]", "config": "{}", "displayOption": 1,
         "width": 1280, "height": 720, "visualContainers": []},
    ])
    metadatos = _convertir(layout).files["pages/pages.json"]
    assert metadatos["pageOrder"] == ["primera00000000000a", "segunda00000000000b"]
    assert metadatos["activePageName"] == "segunda00000000000b"


def test_paginas_que_solo_difieren_en_mayusculas_no_se_sobrescriben():
    base = {"displayName": "Pagina", "filters": "[]", "config": "{}",
            "displayOption": 1, "width": 1280, "height": 720,
            "visualContainers": []}
    layout = _layout(secciones=[
        {**base, "name": "PaginaA", "ordinal": 0},
        {**base, "name": "paginaa", "ordinal": 1},
    ])

    resultado = _convertir(layout)
    orden = resultado.files["pages/pages.json"]["pageOrder"]

    assert len({nombre.casefold() for nombre in orden}) == 2
    assert len([k for k in resultado.files if k.endswith("/page.json")]) == 2
    assert any("colision en Windows" in w for w in resultado.warnings)


# ----------------------------------------------- salida contra los esquemas --
def test_todo_lo_generado_valida_contra_los_esquemas_oficiales(tmp_path,
                                                               pbix_heredado):
    resultado = pbix_to_pbip.convert(pbix_heredado, tmp_path / "out",
                                     include_model=False)
    proyecto = Path(resultado.project_dir)
    comprobados = 0
    for archivo in list(proyecto.rglob("*.json")) + list(proyecto.rglob("*.pbir")):
        datos = json.loads(archivo.read_text(encoding="utf-8-sig"))
        try:
            pbir_schema.validar(datos, archivo=archivo)
        except pbir_schema.SchemaUnsupported:
            continue
        comprobados += 1
    assert comprobados >= 4


# ------------------------------------------------------ estructura del .pbip --
def test_convert_arma_el_proyecto_completo(tmp_path, pbix_heredado):
    resultado = pbix_to_pbip.convert(pbix_heredado, tmp_path / "out",
                                     include_model=False)
    proyecto = Path(resultado.project_dir)
    assert (proyecto / "Informe.pbip").exists()
    assert (proyecto / "Informe.Report" / ".platform").exists()
    assert (proyecto / "Informe.Report" / "definition" / "report.json").exists()
    assert resultado.report_source == "layout_converted"
    assert resultado.pages == 1 and resultado.visuals == 1

    pbir = json.loads((proyecto / "Informe.Report" / "definition.pbir")
                      .read_text(encoding="utf-8-sig"))
    # Aunque el modelo no se exporte, el informe debe apuntar a su carpeta:
    # si no, el definition.pbir queda invalido.
    assert pbir["datasetReference"]["byPath"]["path"] == "../Informe.SemanticModel"


def test_pbir_embebido_se_copia_sin_reinterpretar(tmp_path, monkeypatch):
    # Este fixture usa deliberadamente un esquema ficticio para comprobar
    # copia byte a byte; la aceptacion oficial se cubre con informes completos.
    monkeypatch.setattr(pbix_to_pbip, "_validar_informe_convertido",
                        lambda _ruta: {"checked": False, "reason": "fixture"})
    original = {"$schema": "https://ejemplo/report", "themeCollection": {}}
    ruta = _escribir_pbix(tmp_path / "Moderno.pbix", pbir={
        "report.json": original,
        "pages/pages.json": {"pageOrder": ["p1"]},
        "pages/p1/page.json": {"name": "p1"},
    })
    resultado = pbix_to_pbip.convert(ruta, tmp_path / "out", include_model=False)
    destino = (Path(resultado.project_dir) / "Moderno.Report" / "definition"
               / "report.json")
    assert json.loads(destino.read_text(encoding="utf-8-sig")) == original
    assert resultado.report_source == "pbir_copied"


def test_informe_en_vivo_exige_cadena_de_conexion(tmp_path):
    ruta = _escribir_pbix(tmp_path / "Thin.pbix", layout=_layout(),
                          con_modelo=False)
    with pytest.raises(PowerBIMCPError) as exc:
        pbix_to_pbip.convert(ruta, tmp_path / "out")
    assert "conexion en vivo" in exc.value.message

    resultado = pbix_to_pbip.convert(
        ruta, tmp_path / "out2",
        dataset_connection_string="Data Source=powerbi://ejemplo")
    pbir = json.loads((Path(resultado.project_dir) / "Thin.Report"
                       / "definition.pbir").read_text(encoding="utf-8-sig"))
    assert pbir["datasetReference"]["byConnection"]["connectionString"]


def test_no_sobrescribe_sin_permiso(tmp_path, pbix_heredado):
    pbix_to_pbip.convert(pbix_heredado, tmp_path / "out", include_model=False)
    with pytest.raises(PowerBIMCPError) as exc:
        pbix_to_pbip.convert(pbix_heredado, tmp_path / "out", include_model=False)
    assert "overwrite" in exc.value.message


def test_respuesta_del_modelo_no_apunta_al_staging_eliminado(
        tmp_path, pbix_heredado, monkeypatch):
    monkeypatch.setattr(
        pbix_to_pbip, "_validar_informe_convertido",
        lambda _ruta: {"checked": False, "reason": "fixture"})

    def exportar(_pbix, destino, nombre, _contents, **_kwargs):
        definicion = Path(destino) / f"{nombre}.SemanticModel" / "definition"
        definicion.mkdir(parents=True)
        (definicion / "model.tmdl").write_text(
            "model Model\n", encoding="utf-8")
        return {
            "semantic_model_dir": str(definicion.parent),
            "path": str(definicion),
            "status": "exported",
            "warnings": [],
            "written": [f"{nombre}.SemanticModel/definition/model.tmdl"],
        }

    monkeypatch.setattr(pbix_to_pbip, "_exportar_modelo", exportar)
    resultado = pbix_to_pbip.convert(
        pbix_heredado, tmp_path / "out", include_model=True)

    assert ".hz_stage_" not in resultado.model["path"]
    assert Path(resultado.model["path"]).is_dir()
    assert resultado.model["path"] == str(
        Path(resultado.semantic_model_dir) / "definition")
    assert len(resultado.files_written) == resultado.publication["files"]


def test_exportar_modelo_declara_el_diagrama_que_escribe(
        tmp_path, pbix_heredado, monkeypatch):
    with zipfile.ZipFile(pbix_heredado, "a") as zf:
        zf.writestr("DiagramLayout", _u16({"version": "1.0", "diagrams": []}))
    contents = pbix_reader.read_pbix(pbix_heredado)

    from powerbi import desktop_launcher, tmdl_export

    abierto = SimpleNamespace(
        launched_by_us=True, waited_seconds=0.1,
        instance={
            "host": "localhost", "port": 50000,
            "connection_string": "Data Source=localhost:50000",
            "catalog": "modelo", "database_name": "modelo",
            "model_name": "modelo", "pid": 123, "create_time": 1.0,
        })
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *_a, **_k: abierto)
    monkeypatch.setattr(desktop_launcher, "close", lambda _a: {"closed": True})

    def exportar(_modelo, destino, overwrite=True):
        Path(destino).mkdir(parents=True)
        (Path(destino) / "model.tmdl").write_text(
            "model Model\n", encoding="utf-8")
        return {"files": ["model.tmdl"], "file_count": 1,
                "path": str(destino)}

    monkeypatch.setattr(tmdl_export, "export_to_tmdl", exportar)
    monkeypatch.setattr(tmdl_export, "rename_database", lambda *_a, **_k: None)

    resultado = pbix_to_pbip._exportar_modelo(  # noqa: SLF001
        pbix_heredado, tmp_path / "stage", "Demo", contents,
        timeout=10, close_after=True, reuse_open=False)

    relativo = "Demo.SemanticModel/diagramLayout.json"
    assert relativo in resultado["written"]
    assert (tmp_path / "stage" / relativo).is_file()


@pytest.mark.parametrize("nombre", ["CON", "nul.json", "AUX"])
def test_nombre_de_proyecto_reservado_se_rechaza_sin_escribir(
        tmp_path, pbix_heredado, nombre):
    with pytest.raises(pbix_to_pbip.PbixConversionError):
        pbix_to_pbip.convert(
            pbix_heredado, tmp_path / "out", include_model=False,
            project_name=nombre)

    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("reservado", ["CON", "nul.json", "AUX"])
def test_layout_no_materializa_ids_de_pagina_o_visual_reservados(reservado):
    layout = _layout()
    layout["sections"][0]["name"] = reservado
    config = json.loads(layout["sections"][0]["visualContainers"][0]["config"])
    config["name"] = reservado
    layout["sections"][0]["visualContainers"][0]["config"] = json.dumps(config)

    resultado = layout_to_pbir.convert_layout(layout)

    assert resultado.files["pages/pages.json"]["pageOrder"] == ["pagina0"]
    assert "pages/pagina0/visuals/visual0/visual.json" in resultado.files


def test_active_section_index_booleano_no_se_interpreta_como_indice_uno():
    paginas = []
    for indice in range(2):
        pagina = _layout()["sections"][0]
        pagina["name"] = f"pagina{indice}"
        pagina["displayName"] = f"Pagina {indice}"
        pagina["ordinal"] = indice
        paginas.append(pagina)

    resultado = layout_to_pbir.convert_layout(
        _layout(secciones=paginas, config_extra={"activeSectionIndex": True}))

    assert resultado.files["pages/pages.json"]["activePageName"] == "pagina0"


def test_fallo_tardio_no_publica_parcial_ni_toca_el_anterior(
        tmp_path, pbix_heredado, monkeypatch):
    salida = tmp_path / "out"
    primero = pbix_to_pbip.convert(
        pbix_heredado, salida, include_model=False)
    raiz = Path(primero.project_dir)
    testigo = raiz / "testigo.usuario"
    testigo.write_bytes(b"contenido anterior")

    def falla(_report_dir):
        raise pbix_to_pbip.PbixConversionError("fallo inyectado tardio")

    monkeypatch.setattr(pbix_to_pbip, "_validar_informe_convertido", falla)
    with pytest.raises(pbix_to_pbip.PbixConversionError):
        pbix_to_pbip.convert(
            pbix_heredado, salida, include_model=False, overwrite=True)

    assert testigo.read_bytes() == b"contenido anterior"
    assert not list(salida.glob(".hz_stage_*"))


def test_overwrite_reemplaza_residuos_con_backup(tmp_path, pbix_heredado):
    salida = tmp_path / "out"
    primero = pbix_to_pbip.convert(pbix_heredado, salida, include_model=False)
    raiz = Path(primero.project_dir)
    residuo = raiz / "Informe.Report" / "definition" / "residuo.json"
    residuo.write_text('{"viejo": true}', encoding="utf-8")

    segundo = pbix_to_pbip.convert(
        pbix_heredado, salida, include_model=False, overwrite=True)

    assert not residuo.exists()
    journal = Path(segundo.publication["transaction"]["journal"])
    assert (journal / "files" / "Informe.Report" / "definition"
            / "residuo.json").read_text(encoding="utf-8") == '{"viejo": true}'


@pytest.mark.real_project_state
def test_conversion_no_reemplaza_un_proyecto_abierto(
        tmp_path, pbix_heredado, monkeypatch):
    from services import project_state

    salida = tmp_path / "out"
    primero = pbix_to_pbip.convert(pbix_heredado, salida, include_model=False)
    raiz = Path(primero.project_dir)
    antes = {p.relative_to(raiz).as_posix(): p.read_bytes()
             for p in raiz.rglob("*") if p.is_file()}
    monkeypatch.setattr(
        project_state, "detect",
        lambda _active, **_kw: project_state.ProjectOpenState(
            project_state.OPEN, "high", "abierto por la prueba"))

    with pytest.raises(project_state.ProjectOpenInDesktopError):
        pbix_to_pbip.convert(
            pbix_heredado, salida, include_model=False, overwrite=True)

    despues = {p.relative_to(raiz).as_posix(): p.read_bytes()
               for p in raiz.rglob("*") if p.is_file()}
    assert despues == antes
    assert not list(salida.glob(".hz_stage_*"))


def test_ruta_demasiado_larga_se_rechaza_antes_de_escribir(tmp_path,
                                                           pbix_heredado):
    """Power BI Desktop no abre un .pbip con rutas de 260 o mas caracteres."""
    hondo = tmp_path / ("x" * 120) / ("y" * 120)
    with pytest.raises(PowerBIMCPError) as exc:
        pbix_to_pbip.convert(pbix_heredado, hondo, include_model=False)
    assert "no cabe en la ruta de destino" in exc.value.message
    assert not hondo.exists()


def test_find_pbix_acepta_archivo_y_carpeta(tmp_path, pbix_heredado):
    assert pbix_to_pbip.find_pbix(pbix_heredado) == [pbix_heredado]
    assert pbix_to_pbip.find_pbix(tmp_path) == [pbix_heredado]
    anidado = tmp_path / "sub"
    anidado.mkdir()
    otro = _escribir_pbix(anidado / "Otro.pbix", layout=_layout())
    assert pbix_to_pbip.find_pbix(tmp_path, recursive=True) == sorted(
        [pbix_heredado, otro])


def test_lote_sigue_tras_un_fallo(tmp_path):
    bueno = _escribir_pbix(tmp_path / "Bueno.pbix", layout=_layout())
    malo = tmp_path / "Malo.pbix"
    malo.write_bytes(b"esto no es un zip")
    resultado = pbix_to_pbip.convert_many([bueno, malo], tmp_path / "out",
                                          include_model=False)
    assert resultado["ok_count"] == 1 and resultado["failed_count"] == 1
    assert resultado["failed"][0]["source"] == str(malo)


# ------------------------------------------------------------------ live -----
def _hay_modelo_local() -> bool:
    try:
        from powerbi import desktop_discovery

        return any(i.get("status") == "ok" and (i.get("table_count") or 0) > 0
                   for i in desktop_discovery.discover_instances())
    except Exception:                                   # noqa: BLE001
        return False


@pytest.mark.live
@pytest.mark.skipif(not _hay_modelo_local(),
                    reason="No hay ninguna instancia local de Power BI Desktop "
                           "sirviendo un modelo con tablas. Para ejecutarla: "
                           "abre un .pbix o .pbip en Power BI Desktop y repite "
                           "`python -m pytest -m live`.")
def test_export_tmdl_live(tmp_path):
    """Serializa a TMDL el modelo abierto. Solo lee: no toca el modelo."""
    from config import ActiveModel
    from powerbi import desktop_discovery, tmdl_export

    # La condicion de `skipif` se evalua al RECOLECTAR y este cuerpo vuelve a
    # buscar: si Desktop se cierra entre las dos cosas —en una suite de varios
    # minutos, pasa— un `next()` sin defecto revienta con un StopIteration
    # pelado que no explica nada. La precondicion se comprueba donde se usa.
    instancia = next((i for i in desktop_discovery.discover_instances()
                      if i.get("status") == "ok" and (i.get("table_count") or 0) > 0),
                     None)
    if instancia is None:
        pytest.skip("Power BI Desktop dejo de servir el modelo despues de la "
                    "recoleccion de la suite.")
    modelo = ActiveModel(host=instancia["host"], port=instancia["port"],
                         connection_string=instancia["connection_string"],
                         catalog=instancia.get("catalog"))
    destino = tmp_path / "definition"
    detalle = tmdl_export.export_to_tmdl(modelo, destino)

    assert detalle["table_count"] >= 1
    assert (destino / "model.tmdl").exists()
    assert (destino / "database.tmdl").exists()
    assert any(f.startswith("tables/") for f in detalle["files"])

    # El nombre que sirve Desktop es el del espacio de trabajo, no el del informe.
    assert tmdl_export.rename_database(destino, "Mi Proyecto") is True
    assert "database 'Mi Proyecto'" in (destino / "database.tmdl").read_text(
        encoding="utf-8")


def test_pbir_copiado_se_repara_si_le_falta_lo_obligatorio(tmp_path,
                                                            monkeypatch):
    """Copiar fiel un report.json que Desktop no abre no es fidelidad."""
    monkeypatch.setattr(pbix_to_pbip, "_validar_informe_convertido",
                        lambda _ruta: {"checked": False, "reason": "fixture"})
    ruta = _escribir_pbix(tmp_path / "Incompleto.pbix", pbir={
        "report.json": {"$schema": "https://ejemplo/report",
                        "themeCollection": {"baseTheme": {"name": "CY24SU10"}}},
        "pages/pages.json": {"pageOrder": ["p1"]},
        "pages/p1/page.json": {"name": "p1"},
    })
    resultado = pbix_to_pbip.convert(ruta, tmp_path / "out", include_model=False)
    informe = json.loads((Path(resultado.project_dir) / "Incompleto.Report"
                          / "definition" / "report.json")
                         .read_text(encoding="utf-8-sig"))
    tema = informe["themeCollection"]["baseTheme"]
    assert tema["reportVersionAtImport"] == {"visual": "1.0.0", "page": "1.0.0",
                                             "report": "1.0.0"}
    assert tema["type"] == "SharedResources"
    assert any("reportVersionAtImport" in w for w in resultado.warnings)


def test_pbir_embebido_alinea_carpetas_con_names_internos():
    """Regresion real: alias de carpeta causaba PBIR_PAGE_JSON_MISSING."""
    partes = {
        "pages/Resumen/page.json": json.dumps(
            {"name": "e4e96d500dbe0121"}).encode(),
        "pages/Resumen/visuals/Titulo/visual.json": json.dumps(
            {"name": "037dab71763e90d8"}).encode(),
        "pages/pages.json": json.dumps(
            {"pageOrder": ["e4e96d500dbe0121"]}).encode(),
    }
    avisos = []

    salida = pbix_to_pbip._normalizar_rutas_pbir(partes, avisos)

    assert "pages/e4e96d500dbe0121/page.json" in salida
    assert ("pages/e4e96d500dbe0121/visuals/037dab71763e90d8/"
            "visual.json") in salida
    assert salida["pages/pages.json"] == partes["pages/pages.json"]
    assert any("carpeta(s) de pagina" in aviso for aviso in avisos)
    assert any("carpeta(s) de visual" in aviso for aviso in avisos)


def test_alinear_rutas_pbir_bloquea_colisiones():
    partes = {
        "pages/A/page.json": json.dumps({"name": "pagina"}).encode(),
        "pages/pagina/page.json": json.dumps({"name": "pagina"}).encode(),
    }

    with pytest.raises(pbix_to_pbip.PbixConversionError) as exc:
        pbix_to_pbip._normalizar_rutas_pbir(partes, [])

    assert exc.value.details["target"] == "pages/pagina/page.json"


def test_pbir_completo_se_copia_sin_tocar(tmp_path, monkeypatch):
    """La reparacion no debe alterar un informe que ya venia bien."""
    monkeypatch.setattr(pbix_to_pbip, "_validar_informe_convertido",
                        lambda _ruta: {"checked": False, "reason": "fixture"})
    informe = {"$schema": "https://ejemplo/report", "themeCollection": {
        "baseTheme": {"name": "CY24SU10", "type": "SharedResources",
                      "reportVersionAtImport": {"visual": "2.4.0",
                                                "page": "2.3.0",
                                                "report": "3.0.0"}}}}
    ruta = _escribir_pbix(tmp_path / "Completo.pbix", pbir={
        "report.json": informe,
        "pages/pages.json": {"pageOrder": ["p1"]},
        "pages/p1/page.json": {"name": "p1"},
    })
    resultado = pbix_to_pbip.convert(ruta, tmp_path / "out", include_model=False)
    destino = (Path(resultado.project_dir) / "Completo.Report" / "definition"
               / "report.json")
    assert destino.read_bytes() == json.dumps(informe).encode("utf-8")
    assert not [w for w in resultado.warnings if "tema" in w or "Version" in w]


def test_tema_heredado_renombrado_iguala_el_name_interno(tmp_path):
    """Regresion real: el CLI devolvia PBIR_THEME_FILE_NAME_MISMATCH."""
    nombre = "TemaExportado123.json"
    layout = _layout(
        config_extra={"themeCollection": {
            "baseTheme": {"name": "CY24SU10", "type": 2,
                          "version": {"visual": "2.4.0", "report": "3.0.0",
                                      "page": "2.3.0"}},
            "customTheme": {"name": nombre, "type": 1,
                            "version": {"visual": "2.4.0", "report": "3.0.0",
                                        "page": "2.3.0"}},
        }},
        resourcePackages=[{"resourcePackage": {
            "name": "RegisteredResources", "type": 1, "disabled": False,
            "items": [{"type": 201, "path": nombre, "name": nombre}],
        }}])
    pbix = _escribir_pbix(
        tmp_path / "Tema.pbix", layout=layout,
        assets={f"StaticResources/RegisteredResources/{nombre}": {
            "name": "NombreAnterior", "dataColors": ["#112233"]}})

    resultado = pbix_to_pbip.convert(pbix, tmp_path / "out", include_model=False)
    tema = json.loads((Path(resultado.report_dir) / "StaticResources"
                       / "RegisteredResources" / nombre)
                      .read_text(encoding="utf-8-sig"))

    assert tema["name"] == nombre
    assert resultado.report_validation["checked"] is True
    assert any("internamente otro nombre" in w for w in resultado.warnings)


def test_repara_solo_propiedades_de_tema_marcadas_por_cli(tmp_path):
    reporte = tmp_path / "Audit.Report"
    tema_path = (reporte / "StaticResources" / "RegisteredResources"
                 / "antiguo.json")
    tema_path.parent.mkdir(parents=True)
    tema_path.write_text(json.dumps({
        "name": "antiguo.json",
        "visualStyles": {
            "*": {"*": {
                "general": [{"responsive": False, "keep": True}],
                "outspacePane": [{"show": True}],
            }},
            "barChart": {"*": {"outspacePane": [{"show": False}]}},
        },
    }), encoding="utf-8")
    archivo = "StaticResources/RegisteredResources/antiguo.json"
    diagnosticos = [
        report_validator.Diagnostico(
            code="PBIR_THEME_VISUAL_PROP_UNKNOWN", severity="error",
            file=archivo, path="general.responsive"),
        report_validator.Diagnostico(
            code="PBIR_FORMATTING_OBJECT_UNKNOWN", severity="error",
            file=archivo, path="visualStyles.*.*.outspacePane"),
    ]

    cambios = pbix_to_pbip._reparar_temas_obsoletos(reporte, diagnosticos)

    tema = json.loads(tema_path.read_text(encoding="utf-8-sig"))
    assert cambios == 3
    assert tema["visualStyles"]["*"]["*"]["general"] == [{"keep": True}]
    assert "outspacePane" not in tema["visualStyles"]["*"]["*"]
    assert "outspacePane" not in tema["visualStyles"]["barChart"]["*"]


@pytest.mark.skipif(not report_validator.estado()["available"],
                    reason="requiere el CLI oficial de informes")
def test_un_informe_que_el_cli_rechaza_no_se_publica(tmp_path):
    """Una conversion no puede devolver exito con PBIR_ROLE_REQUIRED_MISSING."""
    visual = _visual("solo_medida", tipo="clusteredColumnChart")
    config = json.loads(visual["config"])
    config["singleVisual"]["projections"].pop("Category")
    config["singleVisual"]["prototypeQuery"]["Select"] = [
        config["singleVisual"]["prototypeQuery"]["Select"][1]]
    visual["config"] = json.dumps(config)
    layout = _layout()
    layout["sections"][0]["visualContainers"] = [visual]
    pbix = _escribir_pbix(tmp_path / "Invalido.pbix", layout=layout)

    with pytest.raises(pbix_to_pbip.PbixConversionError) as exc:
        pbix_to_pbip.convert(pbix, tmp_path / "out", include_model=False)

    assert any(d["code"] == "PBIR_ROLE_REQUIRED_MISSING"
               for d in exc.value.details["diagnostics"])
    assert not (tmp_path / "out" / "Invalido").exists()
    assert not list((tmp_path / "out").glob(".hz_stage_*"))
