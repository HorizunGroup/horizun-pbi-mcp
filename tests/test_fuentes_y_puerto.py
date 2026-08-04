"""Fases 3 y 4: fuentes externas y el puerto del ecosistema.

**Fase 3 — la verdad por delante.** Generar el M para SQL u OData son cuatro
lineas; lo que duele son las credenciales y los niveles de privacidad, que
viven en la interfaz de Desktop y NO se guardan en el .pbip. Lo que se vigila
aqui es que el servidor no finja lo contrario: el aviso viaja SIEMPRE, y las
columnas se declaran (sin credenciales no hay esquema que leer, y las columnas
no se inventan — regla 5 de la casa).

**Fase 4 — contrato, no bus.** El puerto no conecta cuatro aplicaciones de
escritorio por API: define datasets normalizados con una llave compartida. Lo
que se vigila: que el chequeo de un archivo diga QUE comprobo y que NO, porque
unicidad y huerfanas exigen los datos completos; y que el chequeo del modelo
cierre el circulo devolviendo las llaves listas para el brief.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import table_from_source as tfs
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import port_contract as pc


_COLS = [{"name": "HRZ_COD_PRES", "type": "string"},
         {"name": "Importe", "type": "double"}]


# ======================================================== FASE 3: fuentes ===
def test_el_aviso_de_credenciales_viaja_siempre():
    r = tfs.agregar_tabla_desde_fuente(
        None, "sqlserver", "Presupuesto", _COLS,
        server="sql01", database="Obras", source_table="Presupuesto",
        dry_run=True)
    assert r["warnings"], "el aviso no es opcional"
    assert "Desktop" in r["warnings"][0] and "credenciales" in r["warnings"][0]
    assert "no puede verificar" in r["warnings"][0], (
        "hay que decir que este servidor NO comprueba la conexion")


def test_sin_columnas_declaradas_se_rechaza_con_el_motivo():
    with pytest.raises(ValidationError) as exc:
        tfs.agregar_tabla_desde_fuente(None, "sqlserver", "T", [],
                                       server="s", database="d",
                                       source_table="t", dry_run=True)
    assert "no se inventan" in str(exc.value)


def test_sqlserver_por_tabla_y_por_consulta_nativa():
    por_tabla = tfs.agregar_tabla_desde_fuente(
        None, "sqlserver", "T", _COLS, server="sql01", database="Obras",
        schema="dbo", source_table="Presupuesto", dry_run=True)["m"]
    assert 'Sql.Database("sql01", "Obras")' in por_tabla
    assert 'Item = "Presupuesto"' in por_tabla

    nativa = tfs.agregar_tabla_desde_fuente(
        None, "sqlserver", "T", _COLS, server="sql01", database="Obras",
        native_query="SELECT * FROM dbo.Presupuesto", dry_run=True)["m"]
    assert "Value.NativeQuery" in nativa
    assert "EnableFolding = true" in nativa, (
        "sin plegado el motor se trae la tabla entera y filtra en local")


def test_web_json_fija_la_cultura_en_us():
    """JSON escribe numeros sin cultura: dejar la del sistema es el bug del
    10527.52 que se vuelve diez millones."""
    m = tfs.agregar_tabla_desde_fuente(
        None, "web_json", "T", _COLS, url="https://api.x/v1/datos",
        json_path=["data", "rows"], dry_run=True)["m"]
    assert '"en-US"' in m
    assert "[data][rows]" in m


def test_odata_y_url_no_http_se_rechaza():
    m = tfs.agregar_tabla_desde_fuente(
        None, "odata", "T", _COLS, url="https://svc/odata/Presupuestos",
        dry_run=True)["m"]
    assert "OData.Feed" in m
    with pytest.raises(ValidationError):
        tfs.agregar_tabla_desde_fuente(None, "odata", "T", _COLS,
                                       url="ftp://svc/odata", dry_run=True)


def test_fuente_desconocida_lista_las_soportadas():
    with pytest.raises(ValidationError) as exc:
        tfs.agregar_tabla_desde_fuente(None, "oracle", "T", _COLS,
                                       dry_run=True)
    assert "sqlserver" in str(exc.value)


def test_tipo_de_columna_invalido_se_rechaza():
    with pytest.raises(ValidationError):
        tfs.agregar_tabla_desde_fuente(
            None, "odata", "T", [{"name": "X", "type": "guid"}],
            url="https://s/odata/X", dry_run=True)


def test_el_tmdl_declara_la_particion_y_las_columnas():
    r = tfs.agregar_tabla_desde_fuente(
        None, "odata", "Presupuesto", _COLS,
        url="https://svc/odata/Presupuestos", dry_run=True)
    tmdl = r["tmdl"]
    assert "table Presupuesto" in tmdl
    assert "column HRZ_COD_PRES" in tmdl and "dataType: string" in tmdl
    assert "partition Presupuesto = m" in tmdl and "mode: import" in tmdl
    assert "annotation PBI_ResultType = Table" in tmdl


# ========================================================= FASE 4: puerto ===
def _contrato():
    return {"datasets": [
        {"name": "Presupuesto", "key": "HRZ_COD_PRES",
         "emitted_by": "Excel APU",
         "columns": [{"name": "HRZ_COD_PRES", "type": "string"},
                     {"name": "Importe", "type": "double"}]},
        {"name": "Avance", "key": ["HRZ_COD_PRES"],
         "emitted_by": "Revit",
         "columns": [{"name": "HRZ_COD_PRES", "type": "string"},
                     {"name": "Cantidad", "type": "double"}]}]}


def test_un_dataset_sin_llave_se_rechaza():
    """Sin llave no hay contrato: es lo que permite cruzar el ecosistema."""
    with pytest.raises(ValidationError) as exc:
        pc.validate_contract({"datasets": [
            {"name": "X", "columns": [{"name": "A", "type": "string"}]}]})
    assert "cruzar" in str(exc.value)


def test_una_llave_que_no_esta_entre_sus_columnas_se_rechaza():
    with pytest.raises(ValidationError):
        pc.validate_contract({"datasets": [
            {"name": "X", "key": "NoExiste",
             "columns": [{"name": "A", "type": "string"}]}]})


def test_el_contrato_canonico_normaliza_la_llave_a_lista():
    c = pc.validate_contract(_contrato())
    assert c["datasets"][0]["key"] == ["HRZ_COD_PRES"]
    assert c["schema_version"] == "1.0"


def test_un_archivo_conforme_pasa(tmp_path):
    f = tmp_path / "presupuesto.csv"
    f.write_text("HRZ_COD_PRES,Importe\nD01-A2,1500.50\nD01-A3,220.00\n",
                 encoding="utf-8")
    r = pc.check_file(pc.validate_contract(_contrato()), "Presupuesto", str(f))
    assert r["conformant"] is True
    assert r["missing_columns"] == [] and r["missing_keys"] == []


def test_un_archivo_sin_la_llave_no_pasa_y_lo_nombra(tmp_path):
    f = tmp_path / "malo.csv"
    f.write_text("Codigo,Importe\nD01-A2,1500.50\n", encoding="utf-8")
    r = pc.check_file(pc.validate_contract(_contrato()), "Presupuesto", str(f))
    assert r["conformant"] is False
    assert "HRZ_COD_PRES" in r["missing_keys"]
    assert "HRZ_COD_PRES" in r["missing_columns"]


def test_columnas_extra_no_rompen_el_puerto(tmp_path):
    f = tmp_path / "extra.csv"
    f.write_text("HRZ_COD_PRES,Importe,Notas\nD01-A2,1500.50,ok\n",
                 encoding="utf-8")
    r = pc.check_file(pc.validate_contract(_contrato()), "Presupuesto", str(f))
    assert r["conformant"] is True, "traer de mas no incumple el contrato"
    assert r["extra_columns"] == ["Notas"]


def test_un_entero_satisface_un_double_declarado(tmp_path):
    """Todo entero es un decimal valido; al reves seria perdida de datos."""
    f = tmp_path / "enteros.csv"
    f.write_text("HRZ_COD_PRES,Importe\nD01-A2,1500\nD01-A3,220\n",
                 encoding="utf-8")
    r = pc.check_file(pc.validate_contract(_contrato()), "Presupuesto", str(f))
    assert r["type_mismatches"] == []
    assert r["conformant"] is True


def test_el_chequeo_de_archivo_declara_lo_que_NO_comprobo(tmp_path):
    """Unicidad y huerfanas exigen los datos completos: decirlo es la regla."""
    f = tmp_path / "p.csv"
    f.write_text("HRZ_COD_PRES,Importe\nD01-A2,1\n", encoding="utf-8")
    r = pc.check_file(pc.validate_contract(_contrato()), "Presupuesto", str(f))
    assert "pbi_diagnose_data" in r["not_checked"]
    assert "structure" in r["checked"]


def test_un_dataset_inexistente_lista_los_del_contrato(tmp_path):
    f = tmp_path / "p.csv"
    f.write_text("A\n1\n", encoding="utf-8")
    with pytest.raises(ValidationError) as exc:
        pc.check_file(pc.validate_contract(_contrato()), "NoExiste", str(f))
    assert "Presupuesto" in str(exc.value)


# ------------------------------------------------- el circulo que cierra ---
def test_el_modelo_conforme_devuelve_las_llaves_para_el_brief():
    modelo = {"tables": [
        {"name": "Presupuesto", "columns": [
            {"name": "HRZ_COD_PRES", "data_type": "string"},
            {"name": "Importe", "data_type": "double"}]},
        {"name": "Avance", "columns": [
            {"name": "HRZ_COD_PRES", "data_type": "string"},
            {"name": "Cantidad", "data_type": "double"}]}]}
    r = pc.check_model(pc.validate_contract(_contrato()), modelo)
    assert r["conformant"] is True
    campos = {c["field"] for c in r["suggested_critical_fields"]}
    assert campos == {"Presupuesto[HRZ_COD_PRES]", "Avance[HRZ_COD_PRES]"}
    assert "pbi_define_brief" in r["next"], (
        "el valor esta en encadenar puerto -> brief -> diagnostico")


def test_una_tabla_del_contrato_que_falta_en_el_modelo_se_reporta():
    modelo = {"tables": [{"name": "Presupuesto", "columns": [
        {"name": "HRZ_COD_PRES", "data_type": "string"},
        {"name": "Importe", "data_type": "double"}]}]}
    r = pc.check_model(pc.validate_contract(_contrato()), modelo)
    assert r["conformant"] is False
    avance = [d for d in r["datasets"] if d["dataset"] == "Avance"][0]
    assert avance["present"] is False
