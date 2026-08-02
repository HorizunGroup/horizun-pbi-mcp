"""Un '.xls' que en realidad es HTML: el caso normal en exportaciones de ERP.

Nacio de un cargador de datos real (SINCO) cuyos '.xls' resultaron ser tablas
HTML -- `Excel.Workbook` fallaba en seco sobre ellos. La forma correcta era
`Web.Page(Text.FromBinary(...))`, con dos trampas que estas pruebas fijan:

1. Las celdas con `colspan` repiten su valor en cada columna que abarcan (es
   el mismo criterio que usa `Web.Page` al aplanar una fila); sin repetirlo,
   una fila de titulo de 1 celda con colspan=9 desalinea las 8 columnas
   siguientes de TODA la tabla.
2. `Web.Page` NO nombra "Column1", "Column2"... cuando no hay <th>: le pone
   un nombre propio deducido del contenido, y ese nombre no se puede predecir
   desde Python. Fijar los nombres por POSICION (`Table.FromRows` +
   `Table.ToRows`) es lo que hace que `Table.TransformColumnTypes` encuentre
   columnas que de verdad existen en tiempo de refresco.

Todo sintetico: los archivos se fabrican en tmp_path.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pbip import project_locator, table_from_file
from pbip.table_from_file import TableFromFileError, _TablaHTML
from services import tmdl_validate


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _html(tmp_path: Path, nombre: str, cuerpo: str, *,
         charset: str = "iso-8859-1", encoding: str = "cp1252") -> Path:
    ruta = tmp_path / nombre
    doc = (f'<html><head><meta http-equiv="Content-Type" content="text/html; '
          f'charset={charset}"></head><body>{cuerpo}</body></html>')
    ruta.write_bytes(doc.encode(encoding))
    return ruta


#: Una tabla como la de un reporte SINCO real: titulo con colspan, filas
#: "No." de detalle, SIN <th> en ningun lado.
TABLA_REPORTE = (
    '<table id="registrosAprobacion">'
    '<tr><td colspan="3">ACME Y CIA S.A.</td></tr>'
    '<tr><td colspan="3">Proyecto TRI D - ATRIO DE PANCE</td></tr>'
    '<tr><td>Capítulo</td><td>Valor</td><td>Porcentaje</td></tr>'
    '<tr><td>No. D003 CIMENTACIÓN</td><td>458.942,72</td><td>4,01%</td></tr>'
    '<tr><td>No. D004 ESTRUCTURA</td><td>2.079.300,06</td><td>18,17%</td></tr>'
    '</table>'
)


# --------------------------------------------------------------------------
# La firma real decide, no la extension
# --------------------------------------------------------------------------

def test_xls_con_contenido_html_se_perfila_como_html(tmp_path):
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    perfil = table_from_file.perfilar(ruta)
    assert perfil["format"] == "html"


def test_xls_binario_ole2_se_rechaza_con_mensaje_claro(tmp_path):
    ruta = tmp_path / "real.xls"
    ruta.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32)
    with pytest.raises(TableFromFileError) as exc:
        table_from_file.perfilar(ruta)
    assert "OLE2" in str(exc.value) or "97-2003" in str(exc.value)
    assert exc.value.details.get("detected") == "ole2"


def test_xls_que_en_realidad_es_zip_se_lee_como_xlsx(tmp_path):
    """Un .xlsx real, solo que alguien lo guardo con extension .xls."""
    ruta = tmp_path / "renombrado.xls"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types '
            'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheets>'
            '<sheet name="Hoja1" sheetId="1" r:id="rId1" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/'
            '2006/relationships"/></sheets></workbook>')
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><sheetData>'
            '<row><c t="inlineStr"><is><t>Documento</t></is></c>'
            '<c t="inlineStr"><is><t>Valor</t></is></c></row>'
            '<row><c t="inlineStr"><is><t>FE-001</t></is></c>'
            '<c><v>1500</v></c></row>'
            '</sheetData></worksheet>')
    perfil = table_from_file.perfilar(ruta)
    assert perfil["format"] == "xlsx"
    assert {c["name"] for c in perfil["columns"]} == {"Documento", "Valor"}


# --------------------------------------------------------------------------
# El colspan y el nombrado de columnas
# --------------------------------------------------------------------------

def test_colspan_repite_el_valor_en_cada_columna_que_abarca():
    """Sin repetir, una fila de titulo con colspan=3 desalinearia las 2
    columnas siguientes de toda la tabla."""
    parser = _TablaHTML()
    parser.feed('<table id="t"><tr><td colspan="3">Titulo</td></tr>'
               '<tr><td>a</td><td>b</td><td>c</td></tr></table>')
    filas = parser.tablas[0]["rows"]
    assert filas[0] == ["Titulo", "Titulo", "Titulo"]
    assert filas[1] == ["a", "b", "c"]


def test_columnas_resultantes_del_reporte_sin_th(tmp_path):
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    perfil = table_from_file.perfilar(ruta)
    # 3 columnas (Capitulo/Valor/Porcentaje), sin promover: Column1..3.
    assert len(perfil["columns"]) == 3
    assert [c["name"] for c in perfil["columns"]] == ["Column1", "Column2", "Column3"]


def test_sin_th_no_se_promueve_la_fila_de_titulo(tmp_path):
    """La fila 1 real de la tabla ('ACME Y CIA S.A.' con colspan=3) NO
    puede terminar siendo el nombre de una columna: es el titulo del reporte,
    no un encabezado."""
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    perfil = table_from_file.perfilar(ruta)
    nombres = [c["name"] for c in perfil["columns"]]
    assert "ACME Y CIA S.A." not in nombres
    assert perfil["promote_headers"] is False


def test_con_th_si_se_promueve(tmp_path):
    tabla = ('<table id="t1"><tr><th>Documento</th><th>Valor</th></tr>'
            '<tr><td>FE-001</td><td>1500.50</td></tr></table>')
    ruta = _html(tmp_path, "con_headers.xls", tabla, charset="utf-8", encoding="utf-8")
    perfil = table_from_file.perfilar(ruta)
    assert perfil["promote_headers"] is True
    assert [c["name"] for c in perfil["columns"]] == ["Documento", "Valor"]
    assert perfil["row_sample"] == 1  # la fila de <th> no cuenta como dato


def test_espacio_duro_se_limpia_de_las_celdas():
    """\\xa0 (espacio duro) es el caracter que en la sesion de origen
    producia 'CimentaciÃ³n' con signos de interrogacion cuando no se
    limpiaba antes de comparar/mostrar el texto."""
    parser = _TablaHTML()
    parser.feed('<table id="t"><tr><td>Texto&nbsp;con&nbsp;espacio</td></tr></table>')
    celda = parser.tablas[0]["rows"][0][0]
    assert celda == "Texto con espacio"
    assert "\xa0" not in celda


# --------------------------------------------------------------------------
# Seleccion de tabla cuando hay varias
# --------------------------------------------------------------------------

def test_con_id_explicito_elige_esa_tabla_aunque_no_sea_la_mas_grande(tmp_path):
    doc = ('<table id="pequena"><tr><td>A</td></tr></table>'
          '<table id="grande"><tr><td>X</td></tr><tr><td>Y</td></tr>'
          '<tr><td>Z</td></tr></table>')
    ruta = _html(tmp_path, "varias.xls", doc, charset="utf-8", encoding="utf-8")

    perfil = table_from_file.perfilar(ruta, table_id="pequena")
    assert perfil["table_id"] == "pequena"
    assert perfil["row_sample"] == 1


def test_sin_id_elige_la_mas_grande_y_avisa(tmp_path):
    doc = ('<table><tr><td>A</td></tr></table>'
          '<table><tr><td>X</td></tr><tr><td>Y</td></tr><tr><td>Z</td></tr></table>')
    ruta = _html(tmp_path, "sin_id.xls", doc, charset="utf-8", encoding="utf-8")

    perfil = table_from_file.perfilar(ruta)
    assert perfil["row_sample"] == 3
    assert perfil["warnings"], "debe avisar que la eleccion fue por tamano, sin id"


def test_id_inexistente_lista_los_disponibles(tmp_path):
    doc = '<table id="real1"><tr><td>A</td></tr></table>'
    ruta = _html(tmp_path, "un_id.xls", doc, charset="utf-8", encoding="utf-8")

    with pytest.raises(TableFromFileError) as exc:
        table_from_file.perfilar(ruta, table_id="fantasma")
    assert exc.value.details["available_ids"] == ["real1"]


# --------------------------------------------------------------------------
# Codificacion
# --------------------------------------------------------------------------

def test_detecta_windows_1252_por_el_meta_charset(tmp_path):
    tabla = '<table id="t1"><tr><td>Cimentación</td><td>1</td></tr></table>'
    ruta = _html(tmp_path, "cp1252.xls", tabla, charset="iso-8859-1", encoding="cp1252")
    perfil = table_from_file.perfilar(ruta)
    assert perfil["encoding"] == 1252  # TextEncoding.Windows, no Utf8


def test_detecta_utf8_por_el_meta_charset(tmp_path):
    tabla = '<table id="t1"><tr><td>Cimentación</td><td>1</td></tr></table>'
    ruta = _html(tmp_path, "utf8.xls", tabla, charset="utf-8", encoding="utf-8")
    perfil = table_from_file.perfilar(ruta)
    assert perfil["encoding"] == 65001


# --------------------------------------------------------------------------
# La consulta M
# --------------------------------------------------------------------------

def test_la_m_decodifica_antes_de_llamar_a_web_page(tmp_path):
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    perfil = table_from_file.perfilar(ruta)
    m = table_from_file.construir_m(perfil)

    assert "Web.Page(Text.FromBinary(File.Contents(" in m
    assert "[Id = \"registrosAprobacion\"]" in m
    # Los nombres de columna se fijan por POSICION, no se confia en como
    # Web.Page nombre las columnas cuando no hay <th>.
    assert 'Table.FromRows(Table.ToRows(' in m
    assert '"Column1"' in m and '"Column2"' in m and '"Column3"' in m
    # Sin <th>, promover encabezados aqui inventaria uno: no debe aparecer.
    assert "PromoteHeaders" not in m


def test_la_m_quita_la_fila_de_titulo_cuando_si_hay_th(tmp_path):
    tabla = ('<table id="t1"><tr><th>Documento</th><th>Valor</th></tr>'
            '<tr><td>FE-001</td><td>1500</td></tr></table>')
    ruta = _html(tmp_path, "con_headers.xls", tabla, charset="utf-8", encoding="utf-8")
    perfil = table_from_file.perfilar(ruta)
    m = table_from_file.construir_m(perfil)

    assert "Table.Skip(" in m  # se descarta la fila de <th> antes de tipar
    assert '"Documento"' in m and '"Valor"' in m


def test_sin_id_la_m_selecciona_por_posicion(tmp_path):
    doc = '<table><tr><td>A</td></tr></table>'
    ruta = _html(tmp_path, "sin_id.xls", doc, charset="utf-8", encoding="utf-8")
    perfil = table_from_file.perfilar(ruta)
    m = table_from_file.construir_m(perfil)
    assert "Origen{0}[Data]" in m


# --------------------------------------------------------------------------
# Extremo a extremo: la tabla escrita abre de verdad
# --------------------------------------------------------------------------

def test_la_tabla_html_escrita_pasa_el_validador(proyecto, tmp_path):
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    r = table_from_file.agregar_tabla(proyecto, ruta, table_name="ReporteSinco")

    assert r["format"] == "html"
    assert r["table_id"] == "registrosAprobacion"
    assert r["column_count"] == 3

    definition = Path(proyecto.semantic_model_dir) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert resultado["valid"] is True, resultado["findings"]
    assert resultado["findings"] == []


def test_dry_run_no_escribe_nada(proyecto, tmp_path):
    ruta = _html(tmp_path, "reporte.xls", TABLA_REPORTE)
    r = table_from_file.agregar_tabla(
        proyecto, ruta, table_name="Sonda", dry_run=True)

    assert r["dry_run"] is True
    assert "Web.Page" in r["m"]
    tabla_tmdl = Path(proyecto.semantic_model_dir) / "definition" / "tables" / "Sonda.tmdl"
    assert not tabla_tmdl.exists()


def test_html_puro_htm_tambien_se_reconoce(tmp_path):
    ruta = _html(tmp_path, "reporte.htm", TABLA_REPORTE)
    perfil = table_from_file.perfilar(ruta)
    assert perfil["format"] == "html"
