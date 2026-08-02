"""Un informe que cita un campo que ya no existe en el modelo.

El esquema PBIR y la sintaxis TMDL pueden ser perfectos por separado; ninguno
de los dos sabe que hay DENTRO del otro archivo. `pbi_validate_pbip_project`
podia devolver 0 errores sobre un informe con una tarjeta que apuntaba a una
medida borrada: Desktop la resuelve en silencio a nada, sin excepcion ni
marca visible. Estas pruebas fijan el contrato de `reference_audit`, el
cruce que cierra ese hueco, y de su conexion en `project_locator.validate_project`.
"""
from __future__ import annotations

import copy
import json

import pytest

from pbip import project_locator, tmdl_reader
from services import reference_audit
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(tmp_path, session):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip()


def _visual_medida(entidad: str, propiedad: str) -> dict:
    """Un visual.json minimo de tarjeta que cita una medida, como los
    templates del fixture sintetico."""
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json",
        "name": "sonda0000000000000x",
        "position": {"x": 0, "y": 0, "z": 0, "width": 100, "height": 100, "tabOrder": 0},
        "visual": {
            "visualType": "card",
            "query": {"queryState": {"Values": {"projections": [{
                "field": {"Measure": {
                    "Expression": {"SourceRef": {"Entity": entidad}},
                    "Property": propiedad}},
                "queryRef": f"{entidad}.{propiedad}",
                "nativeQueryRef": propiedad,
            }]}}},
            "drillFilterOtherVisuals": True,
        },
    }


def _escribir_visual(active, visual: dict, nombre_carpeta: str = "sonda0000000000000x") -> None:
    destino = (synthetic.find_report_dir(active.pbip_path) / "definition" /
              "pages" / synthetic.PAGE_ID / "visuals" / nombre_carpeta)
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "visual.json").write_text(
        json.dumps(visual, ensure_ascii=False, indent=2), encoding="utf-8")


# =============================================================== unitarias ===
def test_proyecto_limpio_no_tiene_referencias_rotas(proyecto):
    """Los dos templates del fixture (medida y columna validas) no disparan
    ningun falso positivo: es la base de que el resto de pruebas signifique algo."""
    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    assert resultado["checked"] is True
    assert resultado["valid"] is True
    assert resultado["broken_references"] == []
    assert resultado["visuals_checked"] == 2  # los dos templates del fixture


def test_detecta_medida_inexistente(proyecto):
    _escribir_visual(proyecto, _visual_medida(
        synthetic.VALID_TABLE, synthetic.MISSING_MEASURE))
    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    assert resultado["valid"] is False
    rota = next(r for r in resultado["broken_references"]
               if r["property"] == synthetic.MISSING_MEASURE)
    assert rota["kind"] == "measure"
    assert rota["reason"] == "medida_inexistente"


def test_no_le_importa_de_que_tabla_diga_venir_una_medida():
    """Una medida se busca por NOMBRE en todo el modelo, no por la tabla que
    el visual declare: Desktop puede anclar el mismo query a cualquier tabla
    presente, y exigir que coincida con `measure['table']` del TMDL produciria
    falsos positivos constantes."""
    model_data = {"measures": [{"name": "TotalAmount", "table": "Fact"}],
                  "tables": [{"name": "Fact", "columns": []},
                             {"name": "Calendar", "columns": []}]}
    referencias = reference_audit._walk_field_refs({
        "field": {"Measure": {
            "Expression": {"SourceRef": {"Entity": "Calendar"}},
            "Property": "TotalAmount"}}})
    assert referencias == [("Measure", "Calendar", "TotalAmount")]
    # Y aun asi, la tabla equivocada no cuenta como referencia rota:
    assert "TotalAmount" in reference_audit._measure_names(model_data)


def test_detecta_tabla_inexistente_para_columna(proyecto):
    tabla, columna = synthetic.MISSING_COLUMN[:-1].split("[")
    visual = _visual_medida(tabla, columna)
    visual["visual"]["query"]["queryState"]["Values"]["projections"][0]["field"] = {
        "Column": {"Expression": {"SourceRef": {"Entity": tabla}}, "Property": columna}}
    _escribir_visual(proyecto, visual)

    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    rota = next(r for r in resultado["broken_references"] if r["table"] == tabla)
    assert rota["kind"] == "column"
    assert rota["reason"] == "tabla_inexistente"


def test_detecta_columna_inexistente_en_tabla_real(proyecto):
    """La tabla SI existe (`Fact`); la columna, no. Es el caso mas facil de
    confundir con un falso negativo si solo se comprobara la tabla."""
    visual = _visual_medida(synthetic.VALID_TABLE, "ColumnaQueNoExiste")
    visual["visual"]["query"]["queryState"]["Values"]["projections"][0]["field"] = {
        "Column": {"Expression": {"SourceRef": {"Entity": synthetic.VALID_TABLE}},
                   "Property": "ColumnaQueNoExiste"}}
    _escribir_visual(proyecto, visual)

    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    rota = next(r for r in resultado["broken_references"]
               if r["property"] == "ColumnaQueNoExiste")
    assert rota["reason"] == "columna_inexistente"


def test_el_alias_interno_de_un_filtro_no_produce_falsos_positivos(proyecto):
    """La mitad interna de un filtro referencia la tabla por ALIAS
    (`SourceRef.Source`, una letra), no por nombre. Si el cruce comparara esa
    letra contra nombres de tabla, CUALQUIER filtro dispararia una referencia
    rota inventada."""
    visual = _visual_medida(synthetic.VALID_TABLE, synthetic.VALID_MEASURE)
    visual["filterConfig"] = {"filters": [{
        "name": "filtro-sonda",
        "field": {"Column": {
            "Expression": {"SourceRef": {"Entity": "Calendar"}},
            "Property": "Year"}},
        "type": "Categorical",
        "filter": {
            "Version": 2,
            "From": [{"Name": "c", "Entity": "Calendar", "Type": 0}],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {
                    "Expression": {"SourceRef": {"Source": "c"}},
                    "Property": "Year"}}],
                "Values": [[{"Literal": {"Value": "2024L"}}]]}}}],
        },
    }]}
    _escribir_visual(proyecto, visual)

    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)
    assert resultado["valid"] is True


def test_un_filtro_que_apunta_a_una_columna_borrada_si_se_detecta(proyecto):
    """A diferencia del alias interno, el `field` de arriba del filtro SI usa
    el nombre real de la tabla: por eso un filtro roto se puede detectar
    igual que una tarjeta rota."""
    visual = _visual_medida(synthetic.VALID_TABLE, synthetic.VALID_MEASURE)
    visual["filterConfig"] = {"filters": [{
        "name": "filtro-roto",
        "field": {"Column": {
            "Expression": {"SourceRef": {"Entity": "Calendar"}},
            "Property": "ColumnaBorrada"}},
        "type": "Categorical",
    }]}
    _escribir_visual(proyecto, visual)

    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    assert resultado["valid"] is False
    rota = next(r for r in resultado["broken_references"]
               if r["property"] == "ColumnaBorrada")
    assert rota["reason"] == "columna_inexistente"


def test_archivo_illegible_se_reporta_sin_tumbar_la_comprobacion(proyecto):
    destino = (synthetic.find_report_dir(proyecto.pbip_path) / "definition" /
              "pages" / synthetic.PAGE_ID / "visuals" / "roto0000000000000000")
    destino.mkdir(parents=True, exist_ok=True)
    (destino / "visual.json").write_text("{ esto no es json", encoding="utf-8")

    model_data = tmdl_reader.read_semantic_model(proyecto)
    resultado = reference_audit.check_report_references(proyecto, model_data)

    assert resultado["checked"] is True  # no se cae por un archivo suelto
    assert len(resultado["unreadable_files"]) == 1
    assert resultado["valid"] is False  # illegible tampoco cuenta como "todo bien"


# ========================================================== extremo a extremo ===
def test_validate_project_detecta_medida_inexistente(session, proyecto):
    _escribir_visual(proyecto, _visual_medida(
        synthetic.VALID_TABLE, synthetic.MISSING_MEASURE))

    resultado = project_locator.validate_project(session)

    assert resultado["valid"] is False
    assert resultado["references"]["checked"] is True
    assert resultado["references"]["valid"] is False
    assert any("medida_inexistente" in w or synthetic.MISSING_MEASURE in w
              for w in resultado["warnings"])
    assert resultado["checks"]["references_valid"] is False


def test_validate_project_limpio_no_reporta_referencias_rotas(session, proyecto):
    """Un proyecto limpio de referencias puede seguir siendo `valid: False`
    por motivos AJENOS (el fixture minimo no trae .platform/version.json);
    lo que aqui se fija es que ESTE cruce, en concreto, no aporte ruido."""
    resultado = project_locator.validate_project(session)

    assert resultado["references"]["checked"] is True
    assert resultado["references"]["valid"] is True
    assert resultado["references"]["broken_references"] == []
    assert resultado["checks"]["references_valid"] is True
