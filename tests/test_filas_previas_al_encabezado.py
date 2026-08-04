"""Exports con basura antes del encabezado: el patron mas comun de un ERP.

El caso real: un export de un lector de asistencia trae en la fila 1 el titulo
del reporte y seis celdas vacias; el encabezado de verdad esta en la fila 2. La
carga fallaba con «Hay 6 columna(s) sin nombre en la fila de encabezados» y
obligaba a escribir la particion M y el TMDL a mano -exactamente lo que esta
herramienta existe para evitar-.

Lo que se vigila aqui, ademas de que cargue:

- Que la consulta M **tambien** salte esas filas. Si el perfil leyera los
  encabezados de la fila 2 y la M siguiera promoviendo la 1, el TMDL
  describiria unas columnas y el refresco cargaria otras: una tabla que abre y
  no trae nada. Es el fallo silencioso que este cambio podria haber
  introducido, asi que es el que mas prueba tiene.
- Que la eleccion se DIGA. Elegir una fila distinta a la 1 sin avisar es
  decidir por el usuario a sus espaldas.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import table_from_file as tff
from horizun_pbi_mcp.pbip.table_from_file import TableFromFileError


@pytest.fixture
def csv_con_titulo(tmp_path):
    """Fila 1: titulo del reporte y celdas vacias. Fila 2: el encabezado."""
    ruta = tmp_path / "asistencia.csv"
    ruta.write_text(
        "Reporte de asistencia,,,\n"
        "Empleado,Fecha,Entrada,Salida\n"
        "Ana,2026-08-01,08:00,17:00\n"
        "Luis,2026-08-01,08:15,17:05\n",
        encoding="utf-8")
    return ruta


# ------------------------------------------------------------ autodeteccion ---
def test_el_titulo_del_reporte_ya_no_rompe_la_carga(csv_con_titulo):
    perfil = tff.perfilar(csv_con_titulo)
    assert [c["name"] for c in perfil["columns"]] == [
        "Empleado", "Fecha", "Entrada", "Salida"]
    assert perfil["header_row"] == 1


def test_se_avisa_de_la_fila_elegida(csv_con_titulo):
    """Elegir en silencio es decidir por el usuario a sus espaldas."""
    perfil = tff.perfilar(csv_con_titulo)
    assert perfil["warnings"], "debe decir que no uso la fila 1"
    assert "fila 2" in perfil["warnings"][0]
    assert "skip_rows" in perfil["warnings"][0], (
        "el aviso tiene que decir como corregirlo si acerto mal")


def test_un_csv_normal_sigue_usando_la_fila_1(tmp_path):
    ruta = tmp_path / "limpio.csv"
    ruta.write_text("A,B\n1,2\n3,4\n", encoding="utf-8")
    perfil = tff.perfilar(ruta)
    assert perfil["header_row"] == 0
    assert perfil["warnings"] == []
    assert [c["name"] for c in perfil["columns"]] == ["A", "B"]


def test_el_delimitador_se_busca_mas_alla_de_la_fila_1(tmp_path):
    """La fila de titulo no lleva separadores: mirarla sola caia en la coma."""
    ruta = tmp_path / "puntoycoma.csv"
    ruta.write_text("Reporte mensual\nA;B;C\n1;2;3\n", encoding="utf-8")
    perfil = tff.perfilar(ruta)
    assert perfil["delimiter"] == ";"
    assert [c["name"] for c in perfil["columns"]] == ["A", "B", "C"]


# ------------------------------------------------------------------ explicito ---
def test_skip_rows_manda_sobre_la_heuristica(csv_con_titulo):
    """Quien mira el archivo sabe mas que cualquier deteccion."""
    perfil = tff.perfilar(csv_con_titulo, skip_rows=2)
    assert perfil["header_row"] == 2
    assert perfil["columns"][0]["name"] == "Ana"


def test_skip_rows_cero_obliga_a_la_fila_1_aunque_no_sirva(csv_con_titulo):
    """Forzar la fila 1 se respeta, y si esa fila no vale se dice por que.

    No se «corrige» en silencio volviendo a la deteccion: quien pasa
    `skip_rows=0` esta afirmando algo sobre su archivo, y si se equivoca merece
    el error concreto -columnas sin nombre- y no una tabla distinta de la que
    pidio.
    """
    with pytest.raises(TableFromFileError) as exc:
        tff.perfilar(csv_con_titulo, skip_rows=0)
    assert "sin nombre" in str(exc.value)


def test_saltarse_el_archivo_entero_falla_con_un_mensaje_util(csv_con_titulo):
    with pytest.raises(TableFromFileError) as exc:
        tff.perfilar(csv_con_titulo, skip_rows=99)
    assert "sin encabezados" in str(exc.value)
    assert exc.value.details["rows"] == 4


# ------------------------------------------------- la M tiene que ir a juego ---
def test_la_consulta_M_salta_las_mismas_filas(csv_con_titulo):
    """El fallo silencioso que este cambio podria haber introducido.

    Si el perfil lee los encabezados de la fila 2 y la M promueve la 1, el
    TMDL describe unas columnas y el refresco carga otras: la tabla abre y no
    trae nada. No lo detecta ninguna validacion de esquema.
    """
    perfil = tff.perfilar(csv_con_titulo)
    m = tff.construir_m(dict(perfil, culture="es-ES"))
    assert "Table.Skip" in m, "la M no salta la fila de titulo"
    assert m.index("Table.Skip") < m.index("Table.PromoteHeaders"), (
        "hay que saltar ANTES de promover, o se promueve el titulo")


def test_sin_filas_que_saltar_la_M_no_lleva_skip(tmp_path):
    ruta = tmp_path / "limpio.csv"
    ruta.write_text("A,B\n1,2\n", encoding="utf-8")
    perfil = tff.perfilar(ruta)
    m = tff.construir_m(dict(perfil, culture="es-ES"))
    assert "Table.Skip" not in m


def test_la_M_salta_exactamente_las_filas_del_perfil(csv_con_titulo):
    perfil = tff.perfilar(csv_con_titulo, skip_rows=2)
    m = tff.construir_m(dict(perfil, culture="es-ES"))
    assert "Table.Skip(Origen, 2)" in m


# --------------------------------------------------------------- la heuristica ---
@pytest.mark.parametrize("fila,esperado", [
    (["A", "B", "C"], True),
    (["A", "", "C"], False),           # hueco: Power BI no puede cargarla
    (["A", "B", "A"], False),          # repetido: tampoco
    (["A", "B", "a"], False),          # repetido salvo mayusculas
    ([], False),
])
def test_que_cuenta_como_fila_de_encabezado(fila, esperado):
    assert tff._fila_es_encabezado(fila) is esperado


def test_si_ninguna_fila_convence_se_queda_en_la_primera(tmp_path):
    """Sin candidata clara no se inventa una: se conserva lo de siempre."""
    ruta = tmp_path / "raro.csv"
    ruta.write_text("A,,C\n1,2,3\n", encoding="utf-8")
    perfil_filas = [["A", "", "C"], ["1", "2", "3"]]
    # la fila 2 sirve como encabezado, asi que se elige esa
    assert tff._elegir_fila_de_encabezado(perfil_filas, None) == 1
    # pero si ninguna sirve, se vuelve a la 0
    assert tff._elegir_fila_de_encabezado([["A", "", "C"]], None) == 0


# ================================================== regresiones del revisor ===
# Cuatro defectos que la primera version de esta funcionalidad introdujo y que
# la suite no veia. Los encontro una revision adversarial con reproducciones
# reales; cada uno tiene aqui su test de no-repeticion.

def test_punto_y_coma_con_coma_decimal_no_confunde_al_detector(tmp_path):
    """El CSV latinoamericano tipico: ';' delimita y ',' es el DECIMAL.

    Contar apariciones (el maximo por linea) dejaba a la coma empatar y ganar
    por orden alfabetico de _DELIMITADORES: el archivo entero se partia por
    los decimales, la fila 1 quedaba de una celda, se descartaba como
    encabezado y una fila de DATOS acababa promovida. Sin error, tabla basura.
    """
    ruta = tmp_path / "latam.csv"
    ruta.write_text("precio;cantidad\n1234,56;3\n1795,40;12\n", encoding="utf-8")
    perfil = tff.perfilar(ruta)
    assert perfil["delimiter"] == ";"
    assert [c["name"] for c in perfil["columns"]] == ["precio", "cantidad"]
    assert perfil["header_row"] == 0


def test_una_fila_torcida_no_descalifica_al_encabezado(tmp_path):
    """Un campo de mas por una coma sin entrecomillar -el clasico del export-.

    El ancho de referencia era el MAXIMO de las primeras filas: una sola fila
    rota subia la vara, el encabezado legitimo quedaba descalificado por
    "corto", y la propia fila rota acababa promovida a encabezado con todos
    los datos reales descartados. El ancho correcto es la MODA: lo que la
    tabla ES, no lo que una fila rota aparenta.
    """
    ruta = tmp_path / "ragged.csv"
    ruta.write_text("Nombre,Ciudad,Nota\nAna,Bogota,5\nLuis,Cali,4,extra\n",
                    encoding="utf-8")
    perfil = tff.perfilar(ruta)
    assert perfil["header_row"] == 0
    assert [c["name"] for c in perfil["columns"]] == ["Nombre", "Ciudad", "Nota"]


def test_xlsx_con_fila_vacia_omitida_del_xml(tmp_path):
    """Excel no escribe <row> para una fila vacia; Excel.Workbook SI la trae.

    Titulo en la fila fisica 1, la 2 vacia (ausente del XML), encabezado en la
    3. Si el perfil contara filas PRESENTES, header_row seria 1 y el
    Table.Skip de la M -que salta filas FISICAS- promoveria la fila vacia: la
    tabla abre y no refresca. Los huecos se materializan leyendo el atributo
    `r`, y asi el mismo indice sirve para el perfil y para la M.
    """
    import zipfile as zf

    def celda(v, col, fila):
        return (f'<c r="{chr(65 + col)}{fila}" t="inlineStr">'
                f"<is><t>{v}</t></is></c>")

    filas_xml = (
        f'<row r="1">{celda("Reporte de horas", 0, 1)}</row>'
        # la fila 2 NO se escribe: asi la genera Excel cuando esta vacia
        f'<row r="3">{celda("Empleado", 0, 3)}{celda("Horas", 1, 3)}</row>'
        f'<row r="4">{celda("Ana", 0, 4)}{celda("8", 1, 4)}</row>')
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.'
             'openxmlformats.org/spreadsheetml/2006/main">'
             f"<sheetData>{filas_xml}</sheetData></worksheet>")
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats'
          '.org/spreadsheetml/2006/main" xmlns:r="http://schemas.'
          'openxmlformats.org/officeDocument/2006/relationships"><sheets>'
          '<sheet name="Hoja1" sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.'
            'openxmlformats.org/package/2006/relationships"><Relationship '
            'Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument'
            '/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>")
    ruta = tmp_path / "hueco.xlsx"
    with zf.ZipFile(ruta, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   "</Types>")
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)

    perfil = tff.perfilar(ruta)
    assert [c["name"] for c in perfil["columns"]] == ["Empleado", "Horas"]
    assert perfil["header_row"] == 2, (
        "header_row debe ser el indice FISICO (fila 3 -> indice 2), el mismo "
        "que saltara Table.Skip en el refresco")
    m = tff.construir_m(dict(perfil, culture="es-ES"))
    assert "Table.Skip(#\"Navegación\", 2)" in m


def test_los_avisos_de_encabezado_llegan_a_la_respuesta_de_la_tool(
        tmp_path, session, sample_pbip):
    """El perfil avisaba y agregar_tabla tiraba el aviso para csv/xlsx.

    La tool promete en su docstring «se dice cual se eligio en warnings», y
    la respuesta salia SIN ese campo: la unica senal de que se saltaron filas
    se perdia justo antes de llegar a quien la necesita.
    """
    from horizun_pbi_mcp.pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    proyecto = session.require_active_pbip()

    ruta = tmp_path / "asistencia.csv"
    ruta.write_text("Reporte de asistencia,,,\n"
                    "Empleado,Fecha,Entrada,Salida\n"
                    "Ana,2026-08-01,08:00,17:00\n", encoding="utf-8")
    r = tff.agregar_tabla(proyecto, ruta, table_name="Asistencia", dry_run=True)
    assert r.get("warnings"), "el aviso del encabezado no llego a la respuesta"
    assert any("fila 2" in a for a in r["warnings"])
    assert r["header_row"] == 1
