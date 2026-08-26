"""DAX no distingue mayusculas de minusculas; el resolvedor tampoco.

El caso que motivo esto es real: una medida escrita `SUM(Cronograma[Fecha])`
sobre un modelo cuya tabla se llama `CRONOGRAMA` y cuya columna se llama
`FECHA`. El motor lo resuelve sin pestanear -los identificadores de DAX y TOM
son insensibles a la caja- y `pbi_audit_project` lo denunciaba como
`measure_broken_reference`, con severidad ERROR y castigo en el puntaje.

Estas pruebas fijan las cinco condiciones del arreglo:

1. una columna cualificada resuelve aunque cambie la caja;
2. una medida referenciada con otra caja tambien;
3. lo que NO existe sigue apareciendo como hallazgo;
4. el falso positivo desaparece de `summary`, de `audit` y de su puntaje;
5. lo que se devuelve es el nombre CANONICO del modelo, no el escrito.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import model_audit, model_explorer


def _modelo() -> dict:
    """Modelo sintetico con nombres en MAYUSCULAS y DAX en otra caja."""
    return {
        "model": {"name": "Demo"},
        "tables": [
            {"name": "CRONOGRAMA", "columns": [
                {"name": "FECHA", "data_type": "dateTime"},
                {"name": "AVANCE", "data_type": "double"},
            ], "measure_count": 0},
            {"name": "Presupuesto", "columns": [
                {"name": "Monto", "data_type": "double"},
            ], "measure_count": 2},
        ],
        "measures": [
            {"name": "Costo Total", "table": "Presupuesto",
             "expression": "SUM(Presupuesto[Monto])", "format_string": "#,0"},
            # Aqui esta el caso: tabla y columna escritas con otra caja, y una
            # medida citada tambien con otra caja.
            {"name": "Avance Ponderado", "table": "Presupuesto",
             "expression": ("DIVIDE(SUM(Cronograma[Avance]), "
                            "COUNTROWS(cronograma), 0) * [costo total]"),
             "format_string": "0.0%"},
        ],
        "relationships": [
            {"name": "r1", "from_table": "Presupuesto", "from_column": "Monto",
             "to_table": "CRONOGRAMA", "to_column": "FECHA",
             "cross_filtering": "OneDirection"},
        ],
        "hierarchies": [],
        "roles": [],
    }


# ============================================================== 1) columnas ===
def test_una_columna_resuelve_aunque_cambie_la_caja():
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference("Cronograma[Fecha]", indice)

    assert r["exists"] is True
    assert r["kind"] == "column"


def test_devuelve_el_nombre_canonico_del_modelo_no_el_escrito():
    """Condicion 5: quien lea la respuesta tiene que poder copiarla al DAX."""
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference("cronograma[avance]", indice)

    assert r["ref"] == "CRONOGRAMA[AVANCE]"


# =============================================================== 2) medidas ===
def test_una_medida_resuelve_con_otra_capitalizacion():
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference("costo total", indice)

    assert r["exists"] is True
    assert r["kind"] == "measure"
    assert r["ref"] == "Costo Total"


def test_una_medida_cualificada_devuelve_los_dos_nombres_canonicos():
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference("presupuesto[COSTO TOTAL]", indice)

    assert r["exists"] is True
    assert r["ref"] == "Presupuesto[Costo Total]"


def test_una_columna_del_contexto_resuelve_sin_cualificar():
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference("fecha", indice,
                                         tabla_contexto="cronograma")

    assert r["ref"] == "CRONOGRAMA[FECHA]"


# ========================================================= 3) lo inexistente ==
@pytest.mark.parametrize("ref,motivo", [
    ("CRONOGRAMA[NoExiste]", "columna_inexistente"),
    ("TablaFantasma[Campo]", "tabla_inexistente"),
    ("MedidaQueNoExiste", "objeto_inexistente"),
])
def test_una_referencia_inexistente_sigue_sin_resolver(ref, motivo):
    indice = model_explorer.build_index(_modelo())
    r = model_explorer.resolve_reference(ref, indice)

    assert r["exists"] is False
    assert r["kind"] == "unknown"
    assert r["reason"] == motivo


def test_la_regla_sigue_denunciando_lo_que_de_verdad_falta():
    modelo = _modelo()
    modelo["measures"].append({
        "name": "Rota", "table": "Presupuesto",
        "expression": "SUM(Presupuesto[ColumnaBorrada])",
        "format_string": "#,0"})
    resultado = model_audit.audit(modelo, rules=["measure_broken_reference"])

    rotas = [h for h in resultado["findings"]
             if h["rule"] == "measure_broken_reference"]
    assert len(rotas) == 1
    assert rotas[0]["object"]["name"] == "Rota"


# ==================================================== 4) el falso positivo ====
def test_el_resumen_ya_no_inventa_referencias_rotas():
    s = model_explorer.summary(_modelo())

    assert s["broken_references"] == []
    assert s["reference_check"]["case_insensitive"] is True
    # Y no se presenta como comprobado contra el motor, porque no lo es.
    assert s["reference_check"]["engine_verified"] is False


def test_la_auditoria_del_modelo_ya_no_lo_reporta_ni_lo_penaliza():
    resultado = model_audit.audit(_modelo(), rules=["measure_broken_reference"])

    assert [h for h in resultado["findings"]
            if h["rule"] == "measure_broken_reference"] == []
    assert resultado["finding_count"] == 0
    assert resultado["score"] == 100


def test_las_dependencias_de_una_medida_resuelven_con_otra_caja():
    dep = model_explorer.measure_dependencies(_modelo(), "Avance Ponderado")

    assert dep["broken_references"] == []
    assert [m["ref"] for m in dep["depends_on"]["measures"]] == ["Costo Total"]
    assert [c["ref"] for c in dep["depends_on"]["columns"]] == ["CRONOGRAMA[AVANCE]"]


def test_la_medida_se_encuentra_escrita_con_otra_caja():
    dep = model_explorer.measure_dependencies(_modelo(), "avance ponderado")

    assert dep["measure"] == "Avance Ponderado"


def test_quien_usa_una_medida_se_detecta_sin_distinguir_caja():
    dep = model_explorer.measure_dependencies(_modelo(), "Costo Total")

    assert [u["measure"] for u in dep["used_by"]] == ["Avance Ponderado"]
    # Y por tanto deja de parecer una medida sin usar.
    assert dep["is_unused"] is False


def test_las_dependencias_de_columna_aceptan_otra_capitalizacion():
    dep = model_explorer.column_dependencies(_modelo(), "cronograma", "avance")

    assert dep["column"] == "CRONOGRAMA[AVANCE]"
    assert [m["measure"] for m in dep["used_by_measures"]] == ["Avance Ponderado"]


def test_la_relacion_se_reconoce_aunque_la_caja_no_coincida():
    dep = model_explorer.column_dependencies(_modelo(), "Cronograma", "Fecha")

    assert len(dep["used_by_relationships"]) == 1
    assert dep["is_unused"] is False


def test_get_object_acepta_cualquier_capitalizacion():
    obj = model_explorer.get_object(_modelo(), "column", "cronograma[fecha]")

    assert obj["object"]["name"] == "FECHA"


# ==================================================== ambiguedad: no se elige ==
def _modelo_ambiguo() -> dict:
    return {
        "tables": [
            {"name": "Ventas", "columns": [{"name": "Fecha"}]},
            {"name": "Compras", "columns": [{"name": "FECHA"}]},
            # Tabla de solo medidas: sin columna propia, una referencia suelta
            # desde aqui no tiene contexto que la desempate.
            {"name": "Metricas", "columns": [], "measure_count": 1},
        ],
        "measures": [], "relationships": [], "hierarchies": [], "roles": [],
    }


def test_una_coincidencia_ambigua_no_se_resuelve_en_silencio():
    indice = model_explorer.build_index(_modelo_ambiguo())
    r = model_explorer.resolve_reference("Fecha", indice)

    assert r["exists"] is False
    assert r["kind"] == "ambiguous"
    assert r["candidates"] == ["Compras[FECHA]", "Ventas[Fecha]"]


def test_una_referencia_ambigua_no_se_acusa_de_inexistente():
    """No falta el objeto: sobran candidatos. Decir 'no existe' manda a
    crear un duplicado del que ya hay dos."""
    modelo = _modelo_ambiguo()
    modelo["measures"] = [{"name": "M", "table": "Metricas",
                           "expression": "SUM([Fecha])", "format_string": "0"}]
    resultado = model_audit.audit(modelo, rules=["measure_broken_reference"])

    assert resultado["findings"] == []
    assert model_explorer.summary(modelo)["broken_references"] == []
    assert model_explorer.summary(modelo)["ambiguous_references"] == [
        {"measure": "M", "reference": "Fecha",
         "candidates": ["Compras[FECHA]", "Ventas[Fecha]"]}]
