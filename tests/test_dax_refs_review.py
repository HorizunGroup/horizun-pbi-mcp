"""Static checks must defer when virtual columns cannot be resolved offline."""
import pytest

from horizun_pbi_mcp.services import dax_refs


def index():
    return dax_refs.indice_desde_tablas([
        {"name": "Sales", "columns": [{"name": "Amount"}],
         "measures": [{"name": "Total"}]}])


@pytest.mark.parametrize("expression", [
    'SUMX(GENERATESERIES(1, 10), [Value])',
    'SUMX({1, 2}, [Value])',
    'VAR t = GENERATESERIES(1, 10) RETURN SUMX(t, [Value])',
    'VAR t = {1, 2} RETURN SUMX(t, [Value])',
    'SUMX(SELECTCOLUMNS(GENERATESERIES(1, 10), "New", [Value]), [New])',
    'SUMX(ADDCOLUMNS(GENERATESERIES(1, 10), "New", [Value]*2), [New])',
    'SUMX(SUMMARIZE(Sales, Sales[Amount], "New", [Total]), [New])',
    'SUMX(CALENDAR(DATE(2020,1,1), DATE(2020,1,2)), [Date])',
])
def test_virtual_columns_are_not_rejected(expression):
    assert dax_refs.revisar(expression, index()) == []


def test_explicit_model_column_errors_remain_certain():
    assert dax_refs.revisar('SUMX(GENERATESERIES(1,2), Sales[Missing])', index())[0][
        "rule"] == "dax_unknown_column"


def test_unknown_reference_without_virtual_context_is_still_rejected():
    assert dax_refs.revisar('[Missing] + 1', index())[0]["rule"] == 'dax_unknown_reference'


@pytest.mark.parametrize("expression", [
    'COUNTROWS(Inexistente)', 'SUMX(Inexistente, Sales[Amount])',
    'COUNTROWS(FILTER(Inexistente, Sales[Amount] > 0))',
    'VAR t = GENERATESERIES(1, 10) RETURN COUNTROWS(Inexistente)',
])
def test_unknown_bare_table_is_rejected(expression):
    assert dax_refs.revisar(expression, index()) == [{
        "rule": "dax_unknown_table", "reference": "Inexistente",
        "message": "La tabla 'Inexistente' no existe en el modelo."}]


@pytest.mark.parametrize("expression", [
    'COUNTROWS(Sales)', 'VAR t = {1, 2} RETURN COUNTROWS(t)',
    'COUNTROWS(GENERATESERIES(1, 10))',
    'COUNTROWS(FILTER(Sales, Sales[Amount] > 0))',
    'DATEDIFF(DATE(2020,1,1), DATE(2020,1,2), DAY)',
])
def test_table_check_does_not_guess(expression):
    assert dax_refs.revisar(expression, index()) == []


