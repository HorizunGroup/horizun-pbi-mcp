"""Crear una tabla del modelo a partir de un archivo, sin escribir M a mano.

Por que existe: hasta ahora, meter un CSV o un Excel en el modelo obligaba a
redactar la particion M y el TMDL a mano. Ahi nacieron las cinco trampas que
documenta `test_tmdl_validate.py`: propiedades fuera de sitio, tipos perdidos y
—la peor— una conversion numerica sin cultura que no da error y multiplica los
totales por cien.

La regla de diseño es que el generador **no pueda** cometerlas: infiere la
cultura del propio archivo, escribe el TMDL en orden y se valida a si mismo
antes de confirmar. Que sea imposible por construccion, no por disciplina.

Todo sintetico: los archivos se fabrican en tmp_path.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from pbip import model_author, project_locator, table_from_file
from services import tmdl_validate


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _assert_tom_abre(proyecto):
    import config

    definition = Path(proyecto.semantic_model_dir) / "definition"
    settings = config.get_settings()
    anterior = settings.libs_dir
    settings.libs_dir = config.PROJECT_ROOT / "libs"
    try:
        resultado = tmdl_validate.parse_with_tom(definition)
    except Exception as exc:  # pragma: no cover - depende de DLL locales
        pytest.skip(f"TmdlSerializer no disponible: {exc}")
    finally:
        settings.libs_dir = anterior
    assert resultado["parsed"] is True, resultado["error"]


# --------------------------------------------------------------------------
# Perfilado: tipos y separador decimal
# --------------------------------------------------------------------------

CSV_PUNTO = (
    "Documento,Fecha,Valor,Cantidad,Activo\n"
    "FE-001,2025-03-04,10527.52,3,true\n"
    "FE-002,2025-03-06,1795.40,12,false\n"
    "FE-003,2025-03-10,41239.53,7,true\n"
)

CSV_COMA = (
    "Documento;Fecha;Valor;Cantidad\n"
    "FE-001;04/03/2025;10527,52;3\n"
    "FE-002;06/03/2025;1795,40;12\n"
)


def _csv(tmp_path: Path, nombre: str, contenido: str, bom: bool = False) -> Path:
    ruta = tmp_path / nombre
    ruta.write_text(("﻿" if bom else "") + contenido, encoding="utf-8")
    return ruta


def test_infiere_tipos_de_un_csv(tmp_path):
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_PUNTO))

    tipos = {c["name"]: c["data_type"] for c in perfil["columns"]}
    assert tipos["Documento"] == "string"
    assert tipos["Valor"] == "double"
    assert tipos["Cantidad"] == "int64"
    assert tipos["Fecha"] == "dateTime"
    assert tipos["Activo"] == "boolean"
    assert perfil["format"] == "csv"
    assert perfil["delimiter"] == ","


def test_detecta_el_punto_como_separador_decimal(tmp_path):
    """La cultura se DEDUCE del archivo, no se asume.

    Asumir la del modelo es justo lo que multiplico unos costos por 91.
    """
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_PUNTO))
    assert perfil["decimal_separator"] == "."
    assert perfil["culture"] == "en-US"


def test_detecta_la_coma_como_separador_decimal(tmp_path):
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_COMA))
    assert perfil["delimiter"] == ";"
    assert perfil["decimal_separator"] == ","
    assert perfil["culture"] != "en-US"
    tipos = {c["name"]: c["data_type"] for c in perfil["columns"]}
    assert tipos["Valor"] == "double"
    assert tipos["Cantidad"] == "int64"


def test_el_bom_no_se_cuela_en_el_nombre_de_la_primera_columna(tmp_path):
    perfil = table_from_file.perfilar(
        _csv(tmp_path, "costos.csv", CSV_PUNTO, bom=True))
    assert perfil["columns"][0]["name"] == "Documento"
    assert perfil["encoding"] == 65001


def test_encabezado_multilinea_se_rechaza_sin_tocar_el_proyecto(
        proyecto, tmp_path):
    origen = _csv(tmp_path, "multilinea.csv", '"Bad\nName",Good\n1,2\n')
    definition = Path(proyecto.semantic_model_dir) / "definition"
    model_file = definition / "model.tmdl"
    model_before = model_file.read_bytes()
    existentes = {p.name: p.read_bytes()
                  for p in (definition / "tables").glob("*.tmdl")}

    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.agregar_tabla(
            proyecto, origen, table_name="BadHeader")

    assert "Bad" in exc.value.details["header"]
    assert "Name" in exc.value.details["header"]
    assert {c["codepoint"] for c in exc.value.details["controls"]} >= {
        "U+000A"}
    assert not (definition / "tables" / "BadHeader.tmdl").exists()
    assert model_file.read_bytes() == model_before
    assert {p.name: p.read_bytes()
            for p in (definition / "tables").glob("*.tmdl")} == existentes
    _assert_tom_abre(proyecto)


def test_una_columna_vacia_no_se_declara_numerica(tmp_path):
    ruta = _csv(tmp_path, "x.csv", "a,b\n1,\n2,\n")
    perfil = table_from_file.perfilar(ruta)
    tipos = {c["name"]: c["data_type"] for c in perfil["columns"]}
    assert tipos["a"] == "int64"
    assert tipos["b"] == "string"


# --------------------------------------------------------------------------
# La M generada
# --------------------------------------------------------------------------

def test_la_m_lleva_siempre_la_cultura_explicita(tmp_path):
    """El defecto que no da error: sin cultura, manda la del modelo."""
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_PUNTO))
    m = table_from_file.construir_m(perfil)

    assert "Table.TransformColumnTypes" in m
    assert '"en-US"' in m
    # La cultura va como TERCER argumento, cerrando la llamada.
    assert m.rstrip().count("in") >= 1
    assert 'Csv.Document' in m and "Encoding = 65001" in m


def test_la_m_de_un_csv_con_coma_decimal_no_usa_en_US(tmp_path):
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_COMA))
    m = table_from_file.construir_m(perfil)
    assert '"en-US"' not in m
    assert perfil["culture"] in m
    assert 'Delimiter = ";"' in m


def test_los_pasos_se_llaman_como_los_de_una_consulta_hecha_a_mano(tmp_path):
    """La consulta tiene que leerse como la que produce una persona.

    Quien abra el editor de Power Query va a ver estos pasos, y unos nombres
    inventados delatan que esto lo genero una maquina y estorban para editarlo.
    Son los mismos que usa Power BI en español: Origen, Encabezados promovidos,
    Tipo cambiado.
    """
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_PUNTO))
    m = table_from_file.construir_m(perfil)

    assert "    Origen = " in m
    assert '#"Encabezados promovidos"' in m
    assert '#"Tipo cambiado"' in m
    assert m.rstrip().endswith('#"Tipo cambiado"')


def test_el_excel_lleva_su_paso_de_navegacion(tmp_path):
    """Cargar el libro, navegar a la hoja, promover, tipar: los cuatro pasos."""
    ruta = _xlsx(tmp_path / "datos.xlsx", "Actividades",
                 [["CODIGO", "VALOR"], ["A-001", "330"]])
    m = table_from_file.construir_m(table_from_file.perfilar(ruta))

    assert "    Origen = Excel.Workbook" in m
    assert '#"Navegación"' in m or "Actividades_Sheet" in m
    assert '#"Encabezados promovidos"' in m
    assert '#"Tipo cambiado"' in m


def test_la_tabla_lleva_las_anotaciones_que_pone_power_bi(proyecto, tmp_path):
    """Sin PBI_ResultType la consulta se ve distinta a las demas del modelo."""
    origen = _csv(tmp_path, "costos.csv", CSV_PUNTO)
    r = table_from_file.agregar_tabla(proyecto, origen, table_name="T",
                                      dry_run=True)
    assert "annotation PBI_ResultType = Table" in r["tmdl"]


def test_las_comillas_se_escapan_en_los_literales_m(tmp_path):
    """En M una comilla dentro de un literal se duplica.

    Windows no admite `"` en un nombre de archivo, pero SI en el de una hoja de
    Excel, y una comilla sin escapar rompe la consulta entera.
    """
    perfil = table_from_file.perfilar(_csv(tmp_path, "costos.csv", CSV_PUNTO))
    perfil["format"] = "xlsx"
    perfil["sheet"] = 'Hoja "buena"'
    m = table_from_file.construir_m(perfil)
    assert 'Item = "Hoja ""buena"""' in m


# --------------------------------------------------------------------------
# Escritura en el proyecto: el generador se valida a si mismo
# --------------------------------------------------------------------------

def test_la_tabla_escrita_pasa_el_validador(proyecto, tmp_path):
    """La prueba que sostiene todo lo demas.

    Si lo que genera esta tool pudiera disparar cualquiera de las reglas de
    `pbi_validate_tmdl`, habriamos automatizado el error en vez de evitarlo.
    """
    origen = _csv(tmp_path, "costos.csv", CSV_PUNTO)
    r = table_from_file.agregar_tabla(proyecto, origen, table_name="CostosReales")

    assert r["table"] == "CostosReales"
    assert r["column_count"] == 5
    objetivos = {Path(f["path"]).name for f in r["transaction"]["files"]}
    assert objetivos == {"CostosReales.tmdl", "model.tmdl"}
    assert r["transaction"]["committed"] is True

    definition = Path(proyecto.semantic_model_dir) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert resultado["valid"] is True, resultado["findings"]
    assert resultado["findings"] == []


def test_importacion_revierte_tabla_y_ref_si_falla_model_tmdl(
        proyecto, tmp_path, monkeypatch):
    from services import txn

    origen = _csv(tmp_path, "costos.csv", CSV_PUNTO)
    definition = Path(proyecto.semantic_model_dir) / "definition"
    model_file = definition / "model.tmdl"
    table_file = definition / "tables" / "ImportAtomico.tmdl"
    model_before = model_file.read_bytes()
    original = txn.Transaction.write_text

    def falla_en_modelo(self, target, text):
        if Path(target).name == "model.tmdl":
            raise OSError("fallo importacion model.tmdl")
        return original(self, target, text)

    monkeypatch.setattr(txn.Transaction, "write_text", falla_en_modelo)
    with pytest.raises(OSError, match="fallo importacion"):
        table_from_file.agregar_tabla(
            proyecto, origen, table_name="ImportAtomico")

    assert not table_file.exists()
    assert model_file.read_bytes() == model_before


def test_el_nombre_por_defecto_sale_del_archivo(proyecto, tmp_path):
    r = table_from_file.agregar_tabla(
        proyecto, _csv(tmp_path, "Costos Reales.csv", CSV_PUNTO))
    assert r["table"] == "Costos Reales"


def test_no_pisa_una_tabla_existente_sin_permiso(proyecto, tmp_path):
    origen = _csv(tmp_path, "costos.csv", CSV_PUNTO)
    table_from_file.agregar_tabla(proyecto, origen, table_name="T")
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.agregar_tabla(proyecto, origen, table_name="T")
    assert "overwrite" in str(exc.value)


def test_importacion_no_pisa_otro_nombre_con_el_mismo_slug(
        proyecto, tmp_path):
    primera = model_author.create_calculated_table(
        proyecto, "A/B", "ROW(1)",
        columns=[{"name": "a", "data_type": "int64"}])
    tabla = Path(primera["file"])
    modelo = Path(proyecto.semantic_model_dir) / "definition" / "model.tmdl"
    tabla_before = tabla.read_bytes()
    modelo_before = modelo.read_bytes()
    origen = _csv(tmp_path, "datos.csv", "b\n1\n")

    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.agregar_tabla(
            proyecto, origen, table_name="A:B", overwrite=True)

    assert exc.value.details["rule"] == "table_file_collision"
    assert tabla.read_bytes() == tabla_before
    assert modelo.read_bytes() == modelo_before
    _assert_tom_abre(proyecto)


def test_dry_run_no_escribe_nada(proyecto, tmp_path):
    origen = _csv(tmp_path, "costos.csv", CSV_PUNTO)
    r = table_from_file.agregar_tabla(proyecto, origen, table_name="T",
                                      dry_run=True)
    assert r["dry_run"] is True
    assert r["tmdl"].startswith("table T")
    destino = Path(proyecto.semantic_model_dir) / "definition" / "tables" / "T.tmdl"
    assert not destino.exists()


def test_un_archivo_que_no_existe_lo_dice(proyecto, tmp_path):
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.agregar_tabla(proyecto, tmp_path / "fantasma.csv")
    assert "no existe" in str(exc.value).lower()


def test_un_formato_no_soportado_se_rechaza(proyecto, tmp_path):
    raro = tmp_path / "datos.parquet"
    raro.write_bytes(b"PAR1")
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.agregar_tabla(proyecto, raro)
    assert "parquet" in str(exc.value).lower()


# --------------------------------------------------------------------------
# Excel: sin dependencias externas
# --------------------------------------------------------------------------

def _xlsx(ruta: Path, hoja: str, filas: list) -> Path:
    """Escribe un .xlsx minimo con la libreria estandar (es un zip con XML)."""
    def celda(v, col, fila):
        ref = f"{chr(65 + col)}{fila}"
        return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'

    filas_xml = "".join(
        f'<row r="{i}">' + "".join(celda(v, j, i) for j, v in enumerate(f)) + "</row>"
        for i, f in enumerate(filas, start=1))
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
             f'/spreadsheetml/2006/main"><sheetData>{filas_xml}</sheetData></worksheet>')
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org'
          '/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org'
          f'/officeDocument/2006/relationships"><sheets><sheet name="{hoja}" '
          'sheetId="1" r:id="rId1"/></sheets></workbook>')
    rels = ('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats'
            '.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
            'schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
            ' Target="worksheets/sheet1.xml"/></Relationships>')
    ct = ('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
          'package/2006/content-types"><Default Extension="xml" ContentType='
          '"application/xml"/></Types>')
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return ruta


def test_perfila_un_xlsx_sin_dependencias(tmp_path):
    ruta = _xlsx(tmp_path / "datos.xlsx", "Actividades",
                 [["CODIGO", "VALOR"], ["A-001", "330"], ["A-002", "250"]])
    perfil = table_from_file.perfilar(ruta)

    assert perfil["format"] == "xlsx"
    assert perfil["sheet"] == "Actividades"
    assert [c["name"] for c in perfil["columns"]] == ["CODIGO", "VALOR"]


def test_la_m_de_excel_lee_la_hoja_por_nombre(tmp_path):
    ruta = _xlsx(tmp_path / "datos.xlsx", "Actividades",
                 [["CODIGO", "VALOR"], ["A-001", "330"]])
    m = table_from_file.construir_m(table_from_file.perfilar(ruta))
    assert 'Excel.Workbook' in m
    assert 'Item = "Actividades"' in m


def _xlsx_con_fechas(ruta: Path) -> Path:
    """Libro donde una columna son fechas: numeros de serie con formato 14.

    Excel no guarda fechas, guarda dias desde 1899-12-30 y aparte un formato
    que dice como pintarlos. Sin mirar el formato, una fecha se lee como un
    entero cualquiera.
    """
    sheet = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org'
             '/spreadsheetml/2006/main"><sheetData>'
             '<row r="1"><c r="A1" t="inlineStr"><is><t>Tarea</t></is></c>'
             '<c r="B1" t="inlineStr"><is><t>Comienzo</t></is></c></row>'
             '<row r="2"><c r="A2" t="inlineStr"><is><t>Zapatas</t></is></c>'
             '<c r="B2" s="1"><v>45715</v></c></row>'
             '<row r="3"><c r="A3" t="inlineStr"><is><t>Vigas</t></is></c>'
             '<c r="B3" s="1"><v>45716</v></c></row>'
             '</sheetData></worksheet>')
    styles = ('<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats'
              '.org/spreadsheetml/2006/main"><cellXfs count="2">'
              '<xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
    wb = ('<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org'
          '/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org'
          '/officeDocument/2006/relationships"><sheets><sheet name="Plan" '
          'sheetId="1" r:id="rId1"/></sheets></workbook>')
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return ruta


def test_una_fecha_de_excel_no_se_lee_como_entero(tmp_path):
    """El serial 45715 con formato 14 es una fecha, no el numero 45.715.

    Declararla int64 rompe la carga: Power Query devuelve una fecha y el TMDL
    dice que espera un entero.
    """
    perfil = table_from_file.perfilar(_xlsx_con_fechas(tmp_path / "plan.xlsx"))
    tipos = {c["name"]: c["data_type"] for c in perfil["columns"]}

    assert tipos["Tarea"] == "string"
    assert tipos["Comienzo"] == "dateTime"


def test_una_hoja_que_no_existe_lista_las_que_hay(tmp_path):
    ruta = _xlsx(tmp_path / "datos.xlsx", "Actividades", [["A"], ["1"]])
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.perfilar(ruta, sheet="Inventada")
    assert "Actividades" in str(exc.value)
