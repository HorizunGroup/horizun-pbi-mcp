"""Macrofase B — exploracion del modelo, dependencias y auditoria semantica."""
from __future__ import annotations

import pytest

from pbip import project_locator, tmdl_reader
from powerbi.errors import MeasureNotFoundError, TableNotFoundError, ValidationError
from services import model_audit, model_explorer
from tests.fixtures import synthetic


@pytest.fixture
def modelo(session, tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return tmdl_reader.read_semantic_model(session.require_active_pbip())


# ========================================================== referencias DAX ===
@pytest.mark.parametrize("expr,columnas,sueltas", [
    ("SUM(Fact[Amount])", ["Fact[Amount]"], []),
    ("SUM('Mi Tabla'[Campo])", ["Mi Tabla[Campo]"], []),
    ("[TotalAmount] * 2", [], ["TotalAmount"]),
    ("DIVIDE([A], [B])", [], ["A", "B"]),
    ("Fact[Amount] + [Otra]", ["Fact[Amount]"], ["Otra"]),
    ("CALCULATE([M], Fact[X] = 1)", ["Fact[X]"], ["M"]),
])
def test_extraccion_de_referencias(expr, columnas, sueltas):
    refs = model_explorer.extract_references(expr)
    assert refs["columns"] == sorted(columnas)
    assert refs["unqualified"] == sorted(sueltas)


@pytest.mark.parametrize("expr", [
    'IF(1=1, "Fact[Amount]", 0)',
    '"[MedidaFalsa]"',
    '// Fact[Amount]\n1',
    '-- [Medida]\n1',
    '/* Fact[Amount] */ 1',
    'CONCATENATE("texto con [corchetes]", "y Fact[Col]")',
])
def test_las_referencias_dentro_de_literales_no_cuentan(expr):
    refs = model_explorer.extract_references(expr)
    assert refs["columns"] == [] and refs["unqualified"] == [], \
        f"se conto una referencia que vive en una cadena o comentario: {refs}"


def test_expresion_vacia():
    assert model_explorer.extract_references(None) == {"columns": [], "unqualified": []}


# =============================================================== resumen =====
def test_resumen(modelo):
    s = model_explorer.summary(modelo)
    assert s["counts"]["tables"] == 2
    assert s["counts"]["measures"] == 2
    assert s["counts"]["relationships"] == 1
    assert s["counts"]["hidden_columns"] == 1
    assert s["disconnected_tables"] == []
    assert s["broken_references"] == []


def test_el_resumen_detecta_referencias_rotas(modelo):
    modelo["measures"].append({"name": "Rota", "table": "Fact",
                               "expression": "SUM(TablaFantasma[X])"})
    s = model_explorer.summary(modelo)
    assert s["broken_references"] == [{"measure": "Rota",
                                       "reference": "TablaFantasma[X]"}]


def test_el_resumen_detecta_tablas_desconectadas(modelo):
    modelo["tables"].append({"name": "Suelta", "columns": [], "measures": [],
                             "measure_count": 0, "column_count": 0})
    assert "Suelta" in model_explorer.summary(modelo)["disconnected_tables"]


# ========================================================== dependencias =====
def test_dependencias_directas(modelo):
    d = model_explorer.measure_dependencies(modelo, "Ratio Pct")
    assert [m["ref"] for m in d["depends_on"]["measures"]] == ["TotalAmount"]
    assert d["is_leaf"] is False


def test_dependencias_inversas(modelo):
    d = model_explorer.measure_dependencies(modelo, "TotalAmount")
    assert [u["measure"] for u in d["used_by"]] == ["Ratio Pct"]
    assert d["is_unused"] is False


def test_medida_sin_uso(modelo):
    d = model_explorer.measure_dependencies(modelo, "Ratio Pct")
    assert d["is_unused"] is True


def test_dependencias_transitivas(modelo):
    modelo["measures"].append({"name": "Nivel3", "table": "Fact",
                               "expression": "[Ratio Pct] * 100"})
    d = model_explorer.measure_dependencies(modelo, "Nivel3")
    alcanzadas = {t["measure"] for t in d["transitive_measures"]}
    assert {"Ratio Pct", "TotalAmount"} <= alcanzadas


def test_la_profundidad_limita_el_recorrido(modelo):
    modelo["measures"].append({"name": "Nivel3", "table": "Fact",
                               "expression": "[Ratio Pct] * 100"})
    d = model_explorer.measure_dependencies(modelo, "Nivel3", profundidad=1)
    assert all(t["depth"] <= 1 for t in d["transitive_measures"])


def test_un_ciclo_no_cuelga(modelo):
    modelo["measures"] = [
        {"name": "A", "table": "Fact", "expression": "[B] + 1"},
        {"name": "B", "table": "Fact", "expression": "[A] + 1"},
    ]
    d = model_explorer.measure_dependencies(modelo, "A")
    assert any(t["measure"] == "B" for t in d["transitive_measures"])


def test_referencia_rota_en_dependencias(modelo):
    modelo["measures"].append({"name": "Rota", "table": "Fact",
                               "expression": "[NoExiste] + 1"})
    d = model_explorer.measure_dependencies(modelo, "Rota")
    assert d["broken_references"] and d["broken_references"][0]["ref"] == "NoExiste"


def test_medida_inexistente(modelo):
    with pytest.raises(MeasureNotFoundError):
        model_explorer.measure_dependencies(modelo, "NoExiste")


def test_dependencias_de_columna(modelo):
    c = model_explorer.column_dependencies(modelo, "Fact", "Amount")
    assert [m["measure"] for m in c["used_by_measures"]] == ["TotalAmount"]
    assert c["is_unused"] is False


def test_columna_usada_por_una_relacion(modelo):
    c = model_explorer.column_dependencies(modelo, "Fact", "DateKey")
    assert len(c["used_by_relationships"]) == 1
    assert c["is_unused"] is False


def test_columna_sin_uso(modelo):
    c = model_explorer.column_dependencies(modelo, "Fact", "FactID")
    assert c["is_unused"] is True


def test_columna_inexistente(modelo):
    with pytest.raises(TableNotFoundError):
        model_explorer.column_dependencies(modelo, "Fact", "NoExiste")


# ================================================================ busqueda ===
def test_busqueda_por_nombre(modelo):
    r = model_explorer.search(modelo, "amount")
    tipos = {h["kind"] for h in r["results"]}
    assert {"column", "measure"} <= tipos


def test_busqueda_dentro_del_dax(modelo):
    r = model_explorer.search(modelo, "CALCULATE")
    assert any(h["matched_in"] == "expression" for h in r["results"])


def test_busqueda_filtrada_por_tipo(modelo):
    r = model_explorer.search(modelo, "a", kinds=["table"])
    assert all(h["kind"] == "table" for h in r["results"])


def test_busqueda_vacia_se_rechaza(modelo):
    with pytest.raises(ValidationError):
        model_explorer.search(modelo, "   ")


def test_get_object(modelo):
    m = model_explorer.get_object(modelo, "measure", "TotalAmount")
    assert m["kind"] == "measure" and m["object"]["table"] == "Fact"
    assert m["references"]["columns"] == ["Fact[Amount]"]

    c = model_explorer.get_object(modelo, "column", "Calendar[Year]")
    assert c["object"]["table"] == "Calendar"


def test_get_object_inexistente_sugiere_alternativas(modelo):
    with pytest.raises(ValidationError) as exc:
        model_explorer.get_object(modelo, "measure", "NoExiste")
    assert exc.value.details["available"]


# =============================================================== auditoria ===
def test_las_reglas_tienen_identificador_estable():
    reglas = model_audit.reglas_disponibles()
    ids = [r["rule"] for r in reglas]
    assert len(ids) == len(set(ids)), "hay identificadores repetidos"
    assert all(r["severity"] in ("info", "warning", "error") for r in reglas)
    assert all(r["domain"] for r in reglas)


def test_cada_hallazgo_trae_evidencia_y_recomendacion(modelo):
    a = model_audit.audit(modelo)
    for h in a["findings"]:
        assert h["rule"] and h["severity"] and h["domain"]
        assert isinstance(h["evidence"], dict) and h["evidence"] != {}
        assert len(h["recommendation"]) > 20, "la recomendacion debe ser accionable"
        assert isinstance(h["auto_fix_available"], bool)


def test_la_severidad_del_hallazgo_coincide_con_su_regla(modelo):
    catalogo = {r["rule"]: r["severity"] for r in model_audit.reglas_disponibles()}
    for h in model_audit.audit(modelo)["findings"]:
        assert h["severity"] == catalogo[h["rule"]], \
            f"la regla {h['rule']} reporta una severidad que no es la suya"


def test_referencia_rota_es_error(modelo):
    modelo["measures"].append({"name": "Rota", "table": "Fact",
                               "expression": "SUM(NoExiste[X])"})
    a = model_audit.audit(modelo)
    rotas = [h for h in a["findings"] if h["rule"] == "measure_broken_reference"]
    assert rotas and rotas[0]["severity"] == "error"
    assert rotas[0]["evidence"]["missing_reference"] == "NoExiste[X]"


def test_relacion_bidireccional_se_detecta(modelo):
    modelo["relationships"][0]["cross_filtering"] = "BothDirections"
    a = model_audit.audit(modelo)
    assert any(h["rule"] == "relationship_bidirectional" for h in a["findings"])


def test_medida_sin_formato_se_detecta(modelo):
    modelo["measures"][0]["format_string"] = None
    a = model_audit.audit(modelo)
    hallazgo = [h for h in a["findings"] if h["rule"] == "measure_without_format"]
    assert hallazgo and hallazgo[0]["auto_fix_available"] is True


def test_columna_id_visible_se_detecta(modelo):
    a = model_audit.audit(modelo)
    ids = [h for h in a["findings"] if h["rule"] == "column_id_visible"]
    assert {h["object"]["name"] for h in ids} == {"Fact[FactID]", "Fact[DateKey]"}


def test_tabla_de_fechas_evita_el_hallazgo(modelo):
    """El fixture tiene 'Calendar': no debe avisar de falta de calendario."""
    a = model_audit.audit(modelo)
    assert not any(h["rule"] == "model_no_date_table" for h in a["findings"])


def test_sin_tabla_de_fechas_si_avisa(modelo):
    for t in modelo["tables"]:
        t["name"] = t["name"].replace("Calendar", "Dim1")
    for m in modelo["measures"]:
        m["expression"] = (m.get("expression") or "").replace("Calendar", "Dim1")
    a = model_audit.audit(modelo)
    assert any(h["rule"] == "model_no_date_table" for h in a["findings"])


def test_filtrar_por_regla(modelo):
    a = model_audit.audit(modelo, rules=["measure_without_description"])
    assert a["rules_run"] == ["measure_without_description"]
    assert all(h["rule"] == "measure_without_description" for h in a["findings"])


def test_filtrar_por_severidad_minima(modelo):
    modelo["relationships"][0]["cross_filtering"] = "BothDirections"
    a = model_audit.audit(modelo, min_severity="warning")
    assert all(h["severity"] in ("warning", "error") for h in a["findings"])


def test_el_puntaje_baja_con_los_hallazgos(modelo):
    """Con el MISMO conjunto de reglas, mas incumplimientos = menos puntaje.

    Antes esta prueba auditaba con una regla y luego con dos. Con el puntaje
    absoluto colaba, pero mide mal: al cambiar el conjunto de reglas cambia el
    divisor, y ya no se comparan dos estados del modelo sino dos auditorias
    distintas. El conjunto se mantiene fijo a ambos lados.
    """
    reglas = ["model_no_rls", "measure_broken_reference"]
    limpio = model_audit.audit(modelo, rules=reglas)["score"]

    modelo["measures"].append({"name": "Rota", "table": "Fact",
                               "expression": "SUM(NoExiste[X])"})
    con_error = model_audit.audit(modelo, rules=reglas)["score"]
    assert con_error < limpio


def test_una_regla_que_falla_no_tumba_la_auditoria(modelo, monkeypatch):
    def explota(md, idx):
        raise RuntimeError("regla defectuosa")

    original = model_audit._POR_ID["model_no_rls"].fn
    model_audit._POR_ID["model_no_rls"].fn = explota
    try:
        a = model_audit.audit(modelo)
        assert a["rule_errors"], "el fallo de la regla debe reportarse"
        assert a["finding_count"] >= 0, "las demas reglas siguen ejecutandose"
    finally:
        model_audit._POR_ID["model_no_rls"].fn = original


def test_auditoria_de_modelo_vacio():
    a = model_audit.audit({"tables": [], "measures": [], "relationships": []})
    assert a["finding_count"] >= 1, "al menos deberia avisar de que no hay calendario"
    assert a["score"] <= 100
