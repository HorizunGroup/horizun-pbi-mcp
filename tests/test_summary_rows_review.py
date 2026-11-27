"""Summary filtering and counts must not depend on the inference sample."""
import pytest

from horizun_pbi_mcp.pbip import table_from_file
from tests.test_table_from_file import _xlsx


@pytest.mark.parametrize('fmt', ['csv', 'xlsx'])
@pytest.mark.parametrize('keep', [False, True])
def test_summary_rows_after_sample_are_counted_and_filtered(tmp_path, fmt, keep):
    rows = [['Code', 'Description', 'Amount']]
    rows += [[f'A{i}', 'Item', '1'] for i in range(250)]
    rows += [['TOTAL', '', '250'], ['', 'SUBTOTAL', '250'],
             ['', '', 'GRAN TOTAL'], [' ', '', '']]
    path = tmp_path / f'data.{fmt}'
    if fmt == 'csv':
        path.write_text('\n'.join(','.join(r) for r in rows), encoding='utf-8')
    else:
        _xlsx(path, 'Data', rows)
    profile = table_from_file.perfilar(path, keep_summary_rows=keep)
    assert profile['row_sample'] == 200
    summary = profile['summary_rows']
    assert summary['blank_rows'] == 1
    assert len(summary['total_rows']) == 3
    assert profile['file_row_counts'] == {
        'input': 254, 'excluded': 0 if keep else 4,
        'loaded': 254 if keep else 250}
    m = table_from_file.construir_m(profile)
    assert ('Table.SelectRows' in m) is (not keep)
    if not keep:
        assert '{"Code", "Description", "Amount"}' in m
        assert 'Text.Trim' in m
        assert m.index('Table.SelectRows') < m.index('Table.TransformColumnTypes')


def test_filter_is_present_even_when_no_summary_is_in_the_file(tmp_path):
    path = tmp_path / 'clean.csv'
    path.write_text('Code,Amount\nA,1\n', encoding='utf-8')
    profile = table_from_file.perfilar(path)
    assert 'Table.SelectRows' in table_from_file.construir_m(profile)
