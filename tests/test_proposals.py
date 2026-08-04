"""Propuestas de tablero: clasificar el modelo y sugerir, no esperar ordenes."""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import proposals


def modelo(**extra):
    base = {
        "tables": [{
            "name": "qa",
            "columns": [
                {"name": "modelo", "data_type": "string"},
                {"name": "disciplina", "data_type": "string"},
                {"name": "semaforo", "data_type": "string"},
                {"name": "puntaje", "data_type": "int64"},
                {"name": "fecha", "data_type": "dateTime"},
                {"name": "dias_pendiente", "data_type": "int64"},
                {"name": "oculta", "data_type": "string", "is_hidden": True},
                {"name": "RowNumber-x", "data_type": "int64",
                 "column_type": "RowNumber"},
            ]}],
        "measures": [{"table": "qa", "name": "Total"}],
    }
    base.update(extra)
    return base


def test_los_tipos_se_comparan_sin_distinguir_mayusculas():
    """TMDL escribe 'int64' y TOM 'Int64'. Compararlos tal cual dejaba TODAS
    las columnas sin clasificar segun de donde se leyera el modelo."""
    c = proposals.clasificar(modelo())
    assert ("qa", "puntaje") in c["numeric"]
    assert ("qa", "modelo") in c["categories"]

    con_tom = modelo(tables=[{"name": "qa", "columns": [
        {"name": "puntaje", "data_type": "Int64"},
        {"name": "modelo", "data_type": "String"}]}])
    c2 = proposals.clasificar(con_tom)
    assert ("qa", "puntaje") in c2["numeric"]
    assert ("qa", "modelo") in c2["categories"]


def test_una_fecha_se_decide_por_tipo_no_por_el_nombre():
    """'dias_pendiente' contiene 'dia' y es un entero: proponer una linea
    temporal sobre el seria un disparate dicho con seguridad."""
    c = proposals.clasificar(modelo())
    assert ("qa", "fecha") in c["dates"]
    assert ("qa", "dias_pendiente") not in c["dates"]
    assert ("qa", "dias_pendiente") in c["numeric"]


def test_las_columnas_ocultas_y_tecnicas_no_se_proponen():
    c = proposals.clasificar(modelo())
    todos = c["dates"] + c["status"] + c["categories"] + c["numeric"]
    assert not [p for p in todos if p[1] == "oculta"]
    assert not [p for p in todos if p[1].startswith("RowNumber")]


def test_las_tablas_de_fecha_automatica_se_ignoran():
    m = modelo(tables=[{"name": "LocalDateTable_abc", "columns": [
        {"name": "Date", "data_type": "dateTime"}]}])
    assert proposals.clasificar(m)["dates"] == []


def test_una_familia_de_columnas_se_detecta_por_prefijo():
    """Diez columnas 'm_*' son metricas comparables: piden una matriz."""
    m = modelo(tables=[{"name": "qa", "columns": [
        {"name": f"m_{i}", "data_type": "int64"} for i in range(10)]}])
    familias = proposals.clasificar(m)["families"]
    assert familias["qa"]["prefix"] == "m"
    assert len(familias["qa"]["columns"]) == 10


def test_menos_de_cuatro_columnas_no_son_una_familia():
    m = modelo(tables=[{"name": "qa", "columns": [
        {"name": f"m_{i}", "data_type": "int64"} for i in range(3)]}])
    assert proposals.clasificar(m)["families"] == {}


def test_un_modelo_sin_medidas_lo_avisa_como_bloqueo():
    """Sin medidas todo visual cae en sumas implicitas y miente."""
    r = proposals.propose(modelo(measures=[]))
    assert any("NINGUNA medida" in b for b in r["blockers"])


def test_cada_propuesta_trae_su_porque_y_un_spec_aplicable():
    r = proposals.propose(modelo())
    assert r["proposals"], "un modelo con estado, medidas y categorias da propuestas"
    for p in r["proposals"]:
        assert p["why"], "una propuesta sin motivo no se puede juzgar"
        assert p["spec"]["schema_version"] == "1.0"
        assert p["spec"]["visuals"]


def test_las_propuestas_solo_usan_campos_que_existen():
    r = proposals.propose(modelo())
    reales = {"qa[modelo]", "qa[disciplina]", "qa[semaforo]", "qa[puntaje]",
              "qa[fecha]", "qa[dias_pendiente]", "qa[Total]"}
    for p in r["proposals"]:
        for v in p["spec"]["visuals"]:
            for refs in (v.get("fields") or {}).values():
                for ref in refs:
                    assert ref in reales, f"campo inventado: {ref}"


def test_sin_modelo_no_se_inventa_nada():
    r = proposals.propose(None)
    assert r["proposals"] == []
    assert "No hay modelo" in r["reason"]
