"""Pruebas de dax_runner y reporting (sin requerir modelo vivo).

Las que necesitan Power BI Desktop abierto llevan la marca `live` y se omiten
solas cuando no hay ningun motor sirviendo un modelo.
"""
import pytest

from powerbi import dax_runner, desktop_discovery
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


def _instancia_servida():
    """La primera instancia de Desktop que sirve un modelo con tablas, o None.

    Sin Desktop abierto `discover_instances()` devuelve `[]`; no lanza. Por eso
    aqui no hay `try/except`: "no hay motor" es una lista vacia, y cualquier
    excepcion es un fallo de verdad que debe verse.

    Eso es justo lo que fallaba antes. El helper importaba
    `discover_local_models` y `run_query` —nombres que ya no existen— dentro de
    un `except Exception: return False`, asi que el ImportError se leia como "no
    hay Desktop abierto" y la prueba salia SKIPPED incluso con un modelo
    cargado: no se ejecuto nunca. Ahora el import es de nivel de modulo (arriba,
    junto a `dax_runner`), asi que renombrar un nombre rompe la recoleccion de
    la suite en vez de disfrazarse de omision.
    """
    return next((i for i in desktop_discovery.discover_instances()
                 if i.get("status") == "ok" and (i.get("table_count") or 0) > 0),
                None)


def test_la_ruta_live_usa_la_api_que_existe():
    """Vigila las firmas que solo toca la prueba `live`.

    `run_dax` ya lo ejercita `test_run_dax_without_active_model`, pero
    `select_model(..., port=)` solo se usa dentro del cuerpo `live`, que no
    corre sin Desktop. Sin esta comprobacion, renombrarlo volveria a pasar
    inadvertido en cualquier maquina sin Power BI abierto.
    """
    import inspect

    assert list(inspect.signature(dax_runner.run_dax).parameters)[:2] == [
        "session", "query"]
    assert "port" in inspect.signature(
        desktop_discovery.select_model).parameters


@pytest.mark.live
@pytest.mark.skipif(_instancia_servida() is None,
                    reason="No hay ninguna instancia local de Power BI Desktop "
                           "sirviendo un modelo con tablas. Para ejecutarla: "
                           "abre un .pbix o .pbip en Power BI Desktop y repite "
                           "`python -m pytest -m live`.")
def test_run_dax_live(isolated_settings):
    """DAX de solo lectura contra el motor real. No muta nada."""
    from config import Session, Settings

    # Settings aisladas (la session.json de la prueba va a tmp_path, no a las
    # outputs/ del usuario) pero con las DLL de verdad: `libs_dir` del fixture
    # apunta a un tmp_path vacio y sin ADOMD.NET no hay motor al que preguntar.
    isolated_settings.libs_dir = Settings.load().libs_dir
    sesion = Session(isolated_settings)

    instancia = _instancia_servida()
    modelo = desktop_discovery.select_model(sesion, port=instancia["port"])
    assert sesion.active_model is modelo

    resultado = dax_runner.run_dax(sesion, 'EVALUATE ROW("ok", 1)')

    # `rows` son listas posicionales, no diccionarios: el valor se busca por
    # indice de columna. La version anterior indexaba la fila con una clave.
    assert resultado["row_count"] == 1
    assert resultado["rows"] == [[1]]
    assert "ok" in resultado["columns"][0]
    assert resultado["truncated"] is False
    assert resultado["query_form"] == "evaluate"
    assert resultado["port"] == instancia["port"]
