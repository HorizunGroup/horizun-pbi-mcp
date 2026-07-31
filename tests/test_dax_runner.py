"""Pruebas de dax_runner y reporting (sin requerir modelo vivo).

Las pruebas que necesitan Power BI Desktop abierto se marcan como skip.
"""
import pytest

from powerbi import dax_runner
from powerbi.errors import NoActiveModelError, ValidationError
from reporting import analyze_model_quality, document_model_markdown
from utils.validation import validate_dax_query


def test_run_dax_without_active_model(session):
    with pytest.raises(NoActiveModelError):
        dax_runner.run_dax(session, 'EVALUATE ROW("a", 1)')


def test_validate_dax_query_empty():
    with pytest.raises(ValidationError):
        validate_dax_query("")


def test_quality_flags_missing_calendar_and_folder():
    model_data = {
        "model": {"name": "M", "culture": "es-ES"},
        "tables": [
            {"name": "Ventas", "is_hidden": False, "column_count": 2,
             "measure_count": 1, "is_date_table": False,
             "columns": [
                 {"name": "ClienteID", "data_type": "Int64", "is_hidden": False,
                  "column_type": "Data", "summarize_by": "none"},
             ]},
        ],
        "measures": [
            {"name": "Total", "table": "Ventas", "expression": "SUM(Ventas[x])",
             "display_folder": None, "format_string": None, "description": None},
        ],
        "relationships": [
            {"name": "r1", "from_table": "A", "to_table": "B",
             "cross_filtering": "BothDirections", "is_active": False},
        ],
    }
    q = analyze_model_quality(model_data)
    cats = {i["category"] for i in q["issues"]}
    assert "sin_calendario" in cats
    assert "medida_sin_carpeta" in cats
    assert "relacion_bidireccional" in cats
    assert "relacion_inactiva" in cats
    assert "id_visible" in cats
    assert q["has_calendar_table"] is False


def test_document_model_markdown_contains_sections():
    model_data = {
        "model": {"name": "MiModelo", "culture": "es-ES"},
        "tables": [{"name": "Ventas", "is_hidden": False, "column_count": 0,
                    "measure_count": 0, "is_date_table": False, "columns": []}],
        "measures": [], "relationships": [], "hierarchies": [], "roles": [],
    }
    md = document_model_markdown(model_data)
    assert "# Documentacion del modelo" in md
    assert "## Tablas" in md
    assert "MiModelo" in md


def _hay_modelo_local() -> bool:
    """Si hay una instancia de Power BI Desktop con un modelo servido."""
    try:
        from powerbi.desktop_discovery import discover_local_models

        return bool(discover_local_models())
    except Exception:                                   # noqa: BLE001
        return False


@pytest.mark.live
@pytest.mark.skipif(not _hay_modelo_local(),
                    reason="No hay ninguna instancia local de Power BI Desktop "
                           "sirviendo un modelo. Para ejecutarla: abre un .pbix "
                           "o .pbip en Power BI Desktop y repite "
                           "`python -m pytest -m live`.")
def test_run_dax_live():
    """DAX de solo lectura contra el motor real. No muta nada.

    Antes era `pass` bajo un skip incondicional: no comprobaba nada ni podia
    ejecutarse jamas. Ahora se salta solo si de verdad no hay motor.
    """
    from powerbi.desktop_discovery import discover_local_models
    from powerbi.dax_runner import run_query

    modelo = discover_local_models()[0]
    resultado = run_query(modelo, 'EVALUATE ROW("ok", 1)')

    assert resultado["row_count"] == 1
    assert resultado["rows"][0][next(iter(resultado["rows"][0]))] == 1
