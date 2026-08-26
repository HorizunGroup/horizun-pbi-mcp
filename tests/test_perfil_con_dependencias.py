"""El perfil de datos, cruzado con QUIEN usa cada columna.

Sin este cruce, `pbi_profile_data` devolvia veinte hallazgos ordenados por
severidad y nombre, y el que importaba -la columna vacia que alimenta cuatro
medidas- salia el septimo, entre dos columnas sueltas que no usa nadie.

Lo que estas pruebas defienden es la linea que no se puede cruzar: el uso
cambia el ORDEN y la EXPLICACION, nunca la severidad ni el veredicto. Y cuando
el analisis queda incompleto -sin informe que mirar, por ejemplo- se dice, en
vez de concluir que la columna no se usa en ninguna parte.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import data_profile as dp
from horizun_pbi_mcp.services import model_explorer


def _modelo() -> dict:
    return {
        "tables": [
            {"name": "Hechos", "columns": [
                {"name": "Importe", "data_type": "Double"},
                # Vacia y MUY usada: es la que tiene que salir primero.
                {"name": "Notas", "data_type": "String"},
                {"name": "Suelta", "data_type": "String"},
                {"name": "Clave", "data_type": "String"},
                {"name": "Doble", "data_type": "Double",
                 "column_type": "Calculated",
                 "expression": "Hechos[Importe] * 2"},
            ]},
            {"name": "Dim", "columns": [{"name": "Clave", "data_type": "String"}]},
        ],
        "measures": [
            {"name": "Total", "table": "Hechos",
             "expression": "SUM(hechos[importe])"},
            {"name": "Con Notas", "table": "Hechos",
             "expression": "CONCATENATEX(Hechos, Hechos[Notas], \", \")"},
        ],
        "relationships": [
            {"from_table": "Hechos", "from_column": "Clave",
             "to_table": "Dim", "to_column": "Clave"},
        ],
        "hierarchies": [
            {"name": "Jerarquia", "table": "Dim",
             "levels": [{"column": "Clave"}]},
        ],
    }


# ======================================================== el indice de uso ====
def test_el_indice_de_uso_cubre_las_cuatro_clases():
    uso = model_explorer.column_usage_index(_modelo())

    assert [m["measure"] for m in uso["Hechos[Notas]"]["measures"]] == ["Con Notas"]
    assert uso["Hechos[Importe]"]["calculated_columns"] == [{"column": "Hechos[Doble]"}]
    assert len(uso["Hechos[Clave]"]["relationships"]) == 1
    assert uso["Dim[Clave]"]["hierarchies"] == [
        {"hierarchy": "Jerarquia", "table": "Dim"}]
    assert uso["Hechos[Suelta]"] == {c: [] for c in model_explorer.CLASES_DE_USO}


def test_el_indice_resuelve_sin_distinguir_mayusculas():
    """`SUM(hechos[importe])` cuenta como uso de `Hechos[Importe]`."""
    uso = model_explorer.column_usage_index(_modelo())
    assert [m["measure"] for m in uso["Hechos[Importe]"]["measures"]] == ["Total"]


def test_el_indice_y_column_dependencies_dicen_lo_mismo():
    """Dos implementaciones divergentes serian dos verdades distintas."""
    modelo = _modelo()
    uso = model_explorer.column_usage_index(modelo)
    dep = model_explorer.column_dependencies(modelo, "Hechos", "Notas")

    assert [m["measure"] for m in dep["used_by_measures"]] == \
        [m["measure"] for m in uso["Hechos[Notas]"]["measures"]]


# ====================================================== el perfil cruzado =====
class _Motor:
    """Responde a cada consulta de perfil segun la columna que menciona."""

    def __init__(self, por_columna):
        self.por_columna = por_columna

    def run(self, _session, consulta, max_rows=None):
        for columna, datos in self.por_columna.items():
            if f"[{columna}]" in consulta:
                return {"columns": ["[filas]", "[vacios]", "[distintos]",
                                    "[minimo]", "[maximo]"],
                        "rows": [datos]}
        return {"columns": ["[filas]", "[vacios]", "[distintos]"],
                "rows": [[10, 0, 5]]}


@pytest.fixture
def perfil(monkeypatch):
    """Perfila el modelo sintetico con un motor DAX doble."""
    def _correr(por_columna, *, con_informe=None):
        from horizun_pbi_mcp.powerbi import dax_runner, model_reader

        motor = _Motor(por_columna)
        monkeypatch.setattr(dax_runner, "run_dax", motor.run)
        monkeypatch.setattr(model_reader, "read_model", lambda _s: _modelo())
        monkeypatch.setattr(dp, "_uso_en_el_informe",
                            lambda _s: con_informe or
                            {"checked": False, "reason": "sin PBIR en la prueba"})
        return dp.profile_model(object())
    return _correr


def _hallazgo(resultado, columna):
    return next(h for h in resultado["findings"] if h["column"] == columna)


def test_cada_hallazgo_lleva_su_contexto_de_uso(perfil):
    resultado = perfil({"Notas": [10, 10, 0, None, None]})
    h = _hallazgo(resultado, "Notas")

    assert h["rule"] == "columna_vacia"
    assert h["dependency_count"] == 1
    assert h["used_by_measures"] == ["Con Notas"]
    assert h["used_by_calculated_columns"] == []
    assert h["used_by_relationships"] == []
    assert h["used_by_hierarchies"] == []
    assert h["usage_status"] == "used"
    assert set(h["usage_scope"]) >= set(model_explorer.CLASES_DE_USO)


def test_la_columna_usada_sube_por_delante_de_la_que_no_usa_nadie(perfil):
    resultado = perfil({"Notas": [10, 10, 0, None, None],
                        "Suelta": [10, 10, 0, None, None]})
    columnas = [h["column"] for h in resultado["findings"]
                if h["rule"] == "columna_vacia"]

    assert columnas.index("Notas") < columnas.index("Suelta")


def test_una_columna_sin_uso_conocido_no_baja_de_severidad(perfil):
    """Menor impacto OBSERVADO no es menor severidad: el dato sigue roto.

    Y con el analisis incompleto ni siquiera se rebaja la prioridad: no se
    sabe lo suficiente como para decir que importa menos.
    """
    resultado = perfil({"Suelta": [10, 10, 0, None, None]})
    h = _hallazgo(resultado, "Suelta")

    assert h["severity"] == "warning"          # la de siempre para columna_vacia
    assert h["usage_priority"] == "unchanged"


def test_sin_informe_no_se_afirma_que_la_columna_no_se_use(perfil):
    resultado = perfil({"Suelta": [10, 10, 0, None, None]})
    h = _hallazgo(resultado, "Suelta")

    assert h["used_by_visuals"] == "not_checked"
    assert "visuals" not in h["usage_scope"]
    assert h["usage_scope_complete"] is False
    assert h["usage_status"] == "unknown"
    assert resultado["dependency_context"]["visuals_checked"] is False
    assert any("no se usa en el tablero" in w for w in resultado["warnings"])


def test_con_informe_legible_se_cuentan_los_visuales(perfil):
    informe = {"checked": True, "complete": True, "visuals_checked": 3,
               "by_column": {"hechos[suelta]": [{"page": "p1",
                                                 "visual_id": "v1"}]}}
    resultado = perfil({"Suelta": [10, 10, 0, None, None]},
                       con_informe=informe)
    h = _hallazgo(resultado, "Suelta")

    assert h["used_by_visuals"] == [{"page": "p1", "visual_id": "v1"}]
    assert "visuals" in h["usage_scope"]
    assert h["dependency_count"] == 1
    assert h["usage_status"] == "used"


def test_con_informe_completo_una_columna_sin_uso_si_puede_declararse(perfil):
    informe = {"checked": True, "complete": True, "visuals_checked": 3,
               "by_column": {}}
    resultado = perfil({"Suelta": [10, 10, 0, None, None]},
                       con_informe=informe)
    h = _hallazgo(resultado, "Suelta")

    assert h["usage_status"] == "not_found_in_model_dependencies"
    assert h["usage_scope_complete"] is True
    assert h["usage_priority"] == "lower_observed_impact"
    assert "no es prueba" in h["usage_note"]
    # Y aun asi la severidad es la misma: el dato sigue roto.
    assert h["severity"] == "warning"


def test_un_informe_a_medias_deja_el_estado_en_desconocido(perfil):
    """Con visuales ilegibles no se concluye nada sobre el no-uso."""
    informe = {"checked": True, "complete": False, "visuals_checked": 1,
               "by_column": {}, "unreadable_files": [{"file": "x"}]}
    resultado = perfil({"Suelta": [10, 10, 0, None, None]},
                       con_informe=informe)
    h = _hallazgo(resultado, "Suelta")

    assert h["usage_status"] == "unknown"
    assert h["usage_priority"] == "unchanged"


# ============================================== lo acotado sigue declarandose ==
def test_max_columns_se_reporta_con_su_motivo(monkeypatch):
    from horizun_pbi_mcp.powerbi import dax_runner, model_reader

    monkeypatch.setattr(dax_runner, "run_dax",
                        _Motor({}).run)
    monkeypatch.setattr(model_reader, "read_model", lambda _s: _modelo())
    monkeypatch.setattr(dp, "_uso_en_el_informe",
                        lambda _s: {"checked": False, "reason": "prueba"})

    resultado = dp.profile_model(object(), max_columns=2)

    assert resultado["checked_columns"] == 2
    assert resultado["omitted_columns"] == 4
    assert resultado["omission_reason"] == "max_columns"
    assert resultado["complete"] is False
    assert any("max_columns" in w for w in resultado["warnings"])
    # El campo historico no cambia de forma.
    assert resultado["skipped_columns"] == resultado["omitted_columns"]
