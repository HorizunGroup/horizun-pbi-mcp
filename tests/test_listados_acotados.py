"""Inventarios acotables: `detail='summary'` y filtro por tabla.

El defecto que cierran: `pbi_list_tables` devolvia SIEMPRE todas las columnas de
todas las tablas. Medido sobre un proyecto real de siete tablas son ~28.000
caracteres —unos 7.000 tokens— en una sola respuesta, y un modelo corporativo de
cuarenta tablas se come una parte grande de la ventana de contexto antes de
haber hecho nada. No habia forma de pedir menos.

Lo que estas pruebas vigilan, ademas del recorte:

- que `full` siga devolviendo exactamente lo de antes (el contrato esta
  congelado: `full` es el valor por defecto justo para eso);
- que un nombre de tabla equivocado FALLE con los nombres reales delante, en vez
  de devolver una lista vacia que se lee como «el modelo esta vacio»;
- que una tabla real y sin medidas devuelva cero medidas SIN error, que es un
  caso legitimo y no un nombre mal escrito.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi.errors import TableNotFoundError, ValidationError
from horizun_pbi_mcp.services import model_explorer


@pytest.fixture
def modelo():
    """Modelo normalizado minimo. Estas vistas son puras: no tocan disco."""
    return {
        "tables": [
            {"name": "Ventas", "is_hidden": False, "column_count": 2,
             "measure_count": 2,
             "columns": [{"name": "Monto", "data_type": "decimal"},
                         {"name": "Fecha", "data_type": "dateTime"}]},
            {"name": "Calendario", "is_hidden": False, "column_count": 1,
             "measure_count": 0,
             "columns": [{"name": "Fecha", "data_type": "dateTime"}]},
        ],
        "measures": [
            {"name": "Total", "table": "Ventas", "expression": "SUM(Ventas[Monto])",
             "format_string": "#,0", "display_folder": None, "description": None},
            {"name": "Media", "table": "Ventas", "expression": "AVERAGE(Ventas[Monto])",
             "format_string": None, "display_folder": "KPI", "description": "x"},
        ],
    }


# ------------------------------------------------------ el contrato de antes ---
def test_full_sigue_devolviendo_las_columnas(modelo):
    """`full` es el valor por defecto y no puede haber cambiado de forma."""
    salida = model_explorer.tables_view(modelo)
    assert salida["count"] == 2
    assert salida["tables"] == modelo["tables"]
    assert "total_tables" not in salida  # solo aparece cuando el filtro recorta


def test_full_sigue_devolviendo_el_dax(modelo):
    salida = model_explorer.measures_view(modelo)
    assert salida["measures"] == modelo["measures"]
    assert all("expression" in m for m in salida["measures"])


# ------------------------------------------------------------------ resumen ---
def test_summary_quita_las_columnas_pero_conserva_el_recuento(modelo):
    salida = model_explorer.tables_view(modelo, detail="summary")
    assert salida["count"] == 2
    assert [t["name"] for t in salida["tables"]] == ["Ventas", "Calendario"]
    assert all("columns" not in t for t in salida["tables"])
    assert salida["tables"][0]["column_count"] == 2
    assert salida["tables"][0]["measure_count"] == 2


def test_summary_de_medidas_quita_el_dax_y_deja_lo_util(modelo):
    salida = model_explorer.measures_view(modelo, detail="summary")
    assert all("expression" not in m for m in salida["measures"])
    assert salida["measures"][1] == {
        "name": "Media", "table": "Ventas", "format_string": None,
        "display_folder": "KPI", "description": "x"}


def test_el_resumen_pesa_mucho_menos_a_escala_real():
    """El recorte es el motivo de existir de esto: si no ahorra, sobra.

    Se mide sobre un modelo del tamano que causo el problema —diez tablas de
    quince columnas, la forma de un `.pbip` real— y no sobre uno de juguete: con
    dos columnas por tabla el ahorro es del 43% y la prueba no diria nada. El
    ahorro crece con el numero de columnas, que es justo lo que se recorta.
    """
    import json

    modelo = {"tables": [
        {"name": f"Tabla{i}", "is_hidden": False, "column_count": 15,
         "measure_count": 3,
         "columns": [{"name": f"Columna{j}", "data_type": "string",
                      "is_hidden": False, "summarize_by": "none",
                      "source_column": f"Columna{j}", "display_folder": None,
                      "column_type": "Data"} for j in range(15)]}
        for i in range(10)], "measures": []}

    pesa = lambda d: len(json.dumps(d, default=str))          # noqa: E731
    completo = pesa(model_explorer.tables_view(modelo))
    resumido = pesa(model_explorer.tables_view(modelo, detail="summary"))
    assert resumido < completo * 0.2, (
        f"summary ({resumido}) deberia pesar menos del 20% que full "
        f"({completo}); ahorro real {100 - 100 * resumido // completo}%")


def test_summary_sin_column_count_lo_calcula():
    """Los modelos leidos en vivo no siempre traen los recuentos precalculados."""
    modelo = {"tables": [{"name": "T", "columns": [{"name": "A"}, {"name": "B"}],
                          "measures": [{"name": "M"}]}], "measures": []}
    salida = model_explorer.tables_view(modelo, detail="summary")
    assert salida["tables"][0]["column_count"] == 2
    assert salida["tables"][0]["measure_count"] == 1


# ------------------------------------------------------------------- filtro ---
def test_filtro_por_tabla(modelo):
    salida = model_explorer.tables_view(modelo, tables=["Ventas"])
    assert salida["count"] == 1
    assert salida["total_tables"] == 2
    assert salida["tables"][0]["name"] == "Ventas"


def test_el_filtro_no_distingue_mayusculas(modelo):
    salida = model_explorer.tables_view(modelo, tables=["vEnTaS"])
    assert [t["name"] for t in salida["tables"]] == ["Ventas"]


def test_filtro_de_medidas_por_tabla(modelo):
    salida = model_explorer.measures_view(modelo, tables=["Ventas"])
    assert salida["count"] == 2
    assert salida["total_measures"] == 2


def test_una_tabla_real_sin_medidas_no_es_un_error(modelo):
    """'Calendario' existe y no tiene medidas: cero, sin excepcion."""
    salida = model_explorer.measures_view(modelo, tables=["Calendario"])
    assert salida["count"] == 0
    assert salida["measures"] == []


@pytest.mark.parametrize("vista", ["tables_view", "measures_view"])
def test_una_tabla_inexistente_falla_y_dice_cuales_hay(modelo, vista):
    """Devolver [] haria pensar que el modelo esta vacio. Tiene que fallar."""
    with pytest.raises(TableNotFoundError) as exc:
        getattr(model_explorer, vista)(modelo, tables=["Vantas"])
    assert "Vantas" in str(exc.value)
    assert exc.value.details["available_tables"] == ["Calendario", "Ventas"]


@pytest.mark.parametrize("vista", ["tables_view", "measures_view"])
def test_un_detalle_inventado_falla(modelo, vista):
    with pytest.raises(ValidationError) as exc:
        getattr(model_explorer, vista)(modelo, detail="breve")
    assert "full" in str(exc.value) and "summary" in str(exc.value)


# ------------------------------------------------------ las tools de verdad ---
def test_las_tools_exponen_los_parametros_nuevos():
    """Sin esto el servicio existiria pero el cliente MCP no podria pedirlo."""
    import asyncio
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parent.parent / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from horizun_pbi_mcp.server import build_server

    tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
    for nombre in ("pbi_list_tables", "pbi_list_measures"):
        props = tools[nombre].inputSchema["properties"]
        assert "detail" in props, f"{nombre} sin parametro detail"
        assert "tables" in props, f"{nombre} sin parametro tables"
        assert props["detail"].get("default") == "full", (
            f"{nombre}: el defecto debe seguir siendo 'full' (contrato congelado)")
