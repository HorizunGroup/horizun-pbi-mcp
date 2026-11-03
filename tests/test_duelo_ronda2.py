"""Lo que el duelo ronda 2 encontro en los logs de los modelos asequibles.

Cada grupo fallaba antes del arreglo con el sintoma que se vio en la corrida:

1. El perfil `local` no traia lo que el encargo pedia (Power Query, tablas y
   columnas calculadas, borrar paginas, cerrar Desktop, `pbi_start_here`).
2. `no_active_model` mandaba a tools ocultas en vez de a `mode='pbip'`.
3. Medidas con tablas sin comillas o medidas sin corchetes se escribian en
   `pbip` y el validador las daba por buenas.
4. «El archivo no existe» no decia que archivos habia.
5. La fila TOTAL del Excel entraba a la tabla.
6. `pbi_create_pbip_project` anidaba el proyecto y dejaba una pagina vacia
   que `pbi_create_page_from_spec` no llenaba (devolvia ok sin hacer nada).

Todo sintetico, en tmp_path.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import (page_builder, pbip_scaffold, pbir_reader,
                                  project_locator, table_from_file, tmdl_writer)
from horizun_pbi_mcp.powerbi.errors import NoActiveModelError, ValidationError
from horizun_pbi_mcp.services import tmdl_validate
from horizun_pbi_mcp.tool_profiles import LOCAL_DESCRIPTIONS, PROFILE_ENV

from tests.test_table_from_file import _xlsx


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


@pytest.fixture
def perfil_local(monkeypatch, isolated_settings, session):
    from horizun_pbi_mcp import config, tool_profiles
    monkeypatch.setattr(config, "_session", session)
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    anterior = tool_profiles.active_profile()
    yield
    tool_profiles.set_active_profile(anterior)


# ------------------------------------------------------------------ 1 ---
ENCARGO_DEL_DUELO = {
    "pbi_start_here", "pbi_health_check", "pbi_get_power_query",
    "pbi_update_power_query", "pbi_create_calculated_table",
    "pbi_create_calculated_column", "pbi_delete_page", "pbi_close_desktop",
}


def test_el_perfil_local_trae_lo_que_pedia_el_encargo(perfil_local):
    from horizun_pbi_mcp.server import build_server
    nombres = {t.name for t in asyncio.run(build_server(profile="local").list_tools())}
    assert ENCARGO_DEL_DUELO <= nombres
    assert nombres == set(LOCAL_DESCRIPTIONS)


def test_start_here_en_local_solo_nombra_tools_del_perfil(perfil_local):
    from horizun_pbi_mcp.server import build_server
    server = build_server(profile="local")
    salida = asyncio.run(server.call_tool("pbi_start_here", {}))[1]["result"]
    nombradas = {p["tool"] for p in salida["next_steps"]}
    nombradas |= {s for t in salida["common_tasks"] for s in t["steps"]}
    assert nombradas and nombradas <= set(LOCAL_DESCRIPTIONS)
    assert salida["profile"] == "local"


# ------------------------------------------------------------------ 2 ---
def test_sin_modelo_activo_en_local_no_nombra_tools_ocultas(perfil_local, session):
    from horizun_pbi_mcp import tool_profiles
    tool_profiles.set_active_profile("local")
    with pytest.raises(NoActiveModelError) as exc:
        session.require_active_model()
    assert "mode='pbip'" in exc.value.message
    assert "pbi_select_model" not in exc.value.message
    assert "pbi_list_desktop_models" not in exc.value.message
    tool_profiles.set_active_profile("full")
    with pytest.raises(NoActiveModelError) as exc:
        session.require_active_model()
    assert "pbi_select_model" in exc.value.message


def test_la_pista_de_pbip_va_primero(proyecto, session, monkeypatch):
    from horizun_pbi_mcp.services import dual_mode
    monkeypatch.setattr(dual_mode, "_pbip_escribible", lambda s: True)
    exc = dual_mode._con_pista_de_pbip(NoActiveModelError("sin modelo"), session)
    assert exc.message.startswith("Repite esta misma llamada con mode='pbip'")


# ------------------------------------------------------------------ 3 ---
def _tabla(proyecto, nombre, contenido):
    tablas = Path(proyecto.semantic_model_dir) / "definition" / "tables"
    (tablas / f"{nombre}.tmdl").write_text(contenido, encoding="utf-8")
    modelo = Path(proyecto.semantic_model_dir) / "definition" / "model.tmdl"
    cita = f"'{nombre}'" if " " in nombre else nombre
    modelo.write_text(modelo.read_text(encoding="utf-8") + f"ref table {cita}\n",
                      encoding="utf-8")


TABLA_CON_ESPACIOS = (
    "table 'Presupuesto Obra'\n"
    "\tcolumn 'Valor total'\n"
    "\t\tdataType: double\n"
    "\t\tsummarizeBy: sum\n"
    "\t\tsourceColumn: Valor total\n")


@pytest.mark.parametrize("dax,regla", [
    ("SUMX(Presupuesto Obra, 'Presupuesto Obra'[Valor total])",
     "dax_unquoted_table_name"),
    ("Total * 2", "dax_measure_without_brackets"),
    ("SUM(Ventas[Inexistente])", "dax_unknown_column"),
    ("SUM(Compras[Monto])", "dax_unknown_table"),
    ("COUNTROWS(Inexistente)", "dax_unknown_table"),
    ("[Total] − 1", "dax_typographic_character"),
    ("[Fantasma] + 1", "dax_unknown_reference"),
])
def test_una_medida_que_no_puede_evaluarse_no_se_escribe(proyecto, dax, regla):
    _tabla(proyecto, "Presupuesto Obra", TABLA_CON_ESPACIOS)
    archivo = Path(proyecto.semantic_model_dir) / "definition" / "tables" / "Ventas.tmdl"
    antes = archivo.read_bytes()
    with pytest.raises(ValidationError) as exc:
        tmdl_writer.create_measure_pbip(proyecto, "Ventas", "Nueva", dax)
    assert regla in {p["rule"] for p in exc.value.details["dax_problems"]}
    assert archivo.read_bytes() == antes


@pytest.mark.parametrize("dax", [
    "SUMX('Presupuesto Obra', 'Presupuesto Obra'[Valor total])",
    "[Total] * 2",
    "VAR x = [Total] RETURN x + 1",
    'SUMX(ADDCOLUMNS(Ventas, "Doble", Ventas[Monto] * 2), [Doble])',
    '// Total sin corchetes en un comentario\n[Total] & " Total"',
    "CALCULATE([Total], Ventas[Monto] > 0)",
])
def test_una_medida_correcta_se_escribe(proyecto, dax):
    _tabla(proyecto, "Presupuesto Obra", TABLA_CON_ESPACIOS)
    res = tmdl_writer.create_measure_pbip(proyecto, "Ventas", "Nueva", dax)
    assert res["action"] == "created"


def test_generated_columns_can_be_written_in_pbip(proyecto):
    dax = 'SUMX(GENERATESERIES(1, 10), [Value])'
    res = tmdl_writer.create_measure_pbip(proyecto, "Ventas", "Series", dax)
    assert res["action"] == "created"
    archivo = Path(proyecto.semantic_model_dir) / "definition" / "tables" / "Ventas.tmdl"
    assert dax in archivo.read_text(encoding="utf-8-sig")


def test_el_validador_rechaza_el_modelo_que_escribio_qwen4(proyecto):
    """La forma exacta de las medidas del duelo, con datos inventados."""
    _tabla(proyecto, "Presupuesto Obra", TABLA_CON_ESPACIOS.replace(
        "\tcolumn 'Valor total'",
        "\tmeasure BAC = SUMX(Presupuesto Obra, Presupuesto Obra[Valor total])\n"
        "\tmeasure EAC = BAC + 1\n"
        "\tcolumn 'Valor total'"))
    definition = Path(proyecto.semantic_model_dir) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert resultado["valid"] is False
    reglas = {f["evidence"]["rule"] for f in resultado["findings"]
              if f["rule"] == "tmdl_dax_reference"}
    assert reglas == {"dax_unquoted_table_name", "dax_measure_without_brackets"}


def test_el_validador_no_acusa_el_fixture_de_siempre(proyecto):
    definition = Path(proyecto.semantic_model_dir) / "definition"
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert not [f for f in resultado["findings"] if f["rule"] == "tmdl_dax_reference"]


def test_cadenas_y_corchetes_escapados_no_engañan_al_lector():
    from horizun_pbi_mcp.services import dax_refs
    toks = dax_refs.tokens('"a ""[x]"" b" & \'O\'\'Neil\'[c]]d]')
    assert [(t, v) for t, v, *_ in toks] == [
        ("string", 'a "[x]" b'), ("op", "&"), ("table", "O'Neil"), ("bracket", "c]d")]


# ------------------------------------------------------------------ 4 ---
def test_un_archivo_que_no_existe_lista_los_que_si_hay(tmp_path):
    datos = tmp_path / "datos"
    datos.mkdir()
    (datos / "presupuesto_real.csv").write_text("a\n1\n", encoding="utf-8")
    (datos / "notas.docx").write_bytes(b"x")
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.perfilar(datos / "presupuesto.csv")
    assert exc.value.details["available_files"] == ["presupuesto_real.csv"]
    assert "presupuesto_real.csv" in exc.value.message
    assert "notas.docx" not in exc.value.message


# ------------------------------------------------------------------ 5 ---
FILAS_CON_TOTAL = [
    ["Presupuesto de obra"],
    [],
    ["Codigo", "Capitulo", "Valor"],
    ["1.01", "Cimentacion", "100"],
    ["2.01", "Estructura", "250"],
    [],
    ["TOTAL PRESUPUESTO", "", "350"],
]


def test_la_fila_total_y_las_vacias_del_excel_se_quitan_y_se_avisa(tmp_path):
    ruta = _xlsx(tmp_path / "presupuesto.xlsx", "Hoja", FILAS_CON_TOTAL)
    perfil = table_from_file.perfilar(ruta, skip_rows=2)
    assert perfil["summary_rows"]["total_rows"] == [
        {"column": "Codigo", "value": "TOTAL PRESUPUESTO"}]
    assert perfil["summary_rows"]["blank_rows"] == 1
    m = table_from_file.construir_m(perfil)
    assert '#"Filas vacias y totales quitadas" = Table.SelectRows(' in m
    assert 'Text.StartsWith(t, "TOTAL")' in m
    assert any("TOTAL PRESUPUESTO" in a for a in perfil["warnings"])
    # El tipo se infiere sin la fila TOTAL: el codigo sigue siendo texto y el
    # valor, numero.
    tipos = {c["name"]: c["data_type"] for c in perfil["columns"]}
    assert tipos["Valor"] != "string"


def test_keep_summary_rows_las_conserva(tmp_path):
    ruta = _xlsx(tmp_path / "presupuesto.xlsx", "Hoja", FILAS_CON_TOTAL)
    perfil = table_from_file.perfilar(ruta, skip_rows=2, keep_summary_rows=True)
    assert "Filas vacias y totales quitadas" not in table_from_file.construir_m(perfil)
    assert any("CONSERVAN" in a for a in perfil["warnings"])


def test_un_csv_sin_totales_produce_la_misma_m_de_antes(tmp_path):
    ruta = tmp_path / "limpio.csv"
    ruta.write_text("Documento,Valor\nFE-1,10.5\nFE-2,3.25\n", encoding="utf-8")
    perfil = table_from_file.perfilar(ruta)
    assert "summary_rows" not in perfil
    assert "Table.SelectRows" not in table_from_file.construir_m(perfil)


# ------------------------------------------------------------------ 6 ---
def test_subfolder_false_crea_el_pbip_en_la_carpeta_pedida(tmp_path):
    carpeta = tmp_path / "Competidor"
    (carpeta / "datos").mkdir(parents=True)
    (carpeta / "datos" / "x.csv").write_text("a\n1\n", encoding="utf-8")
    res = pbip_scaffold.crear_proyecto(carpeta, "Mirador", subfolder=False)
    assert Path(res["pbip_path"]) == (carpeta / "Mirador.pbip").resolve()
    assert (carpeta / "Mirador.Report").is_dir()
    assert (carpeta / "Mirador.SemanticModel").is_dir()
    assert (carpeta / "datos" / "x.csv").read_text(encoding="utf-8") == "a\n1\n"
    assert not [p for p in carpeta.iterdir() if p.name.startswith(".hz_stage_")]


def test_out_dir_con_extension_pbip_tampoco_anida(tmp_path):
    res = pbip_scaffold.crear_proyecto(tmp_path / "Mirador.pbip", "Mirador")
    assert Path(res["pbip_path"]) == (tmp_path / "Mirador.pbip").resolve()


def test_subfolder_false_no_reemplaza_nada(tmp_path):
    pbip_scaffold.crear_proyecto(tmp_path, "Mirador", subfolder=False)
    antes = (tmp_path / "Mirador.pbip").read_bytes()
    with pytest.raises(pbip_scaffold.ScaffoldError):
        pbip_scaffold.crear_proyecto(tmp_path, "Mirador", subfolder=False,
                                     overwrite=True)
    assert (tmp_path / "Mirador.pbip").read_bytes() == antes


def test_por_defecto_sigue_creando_la_subcarpeta(tmp_path):
    res = pbip_scaffold.crear_proyecto(tmp_path, "Mirador")
    assert Path(res["pbip_path"]) == (tmp_path / "Mirador" / "Mirador.pbip").resolve()


SPEC_TARJETA = {"page_name": "Resumen ejecutivo", "layout": "grid",
                "visuals": [{"type": "card", "title": "Total",
                             "fields": {"Values": ["Ventas[Total]"]}}]}


def test_la_pagina_inicial_vacia_se_llena_con_el_spec(proyecto, session, tmp_path):
    res = pbip_scaffold.crear_proyecto(tmp_path / "nuevo", "Mirador",
                                       page_name="Resumen ejecutivo",
                                       subfolder=False)
    project_locator.open_project(session, res["pbip_path"])
    activo = session.require_active_pbip()
    _tabla(activo, "Ventas", (
        "table Ventas\n"
        "\tmeasure Total = SUM(Ventas[Monto])\n"
        "\tcolumn Monto\n\t\tdataType: double\n\t\tsourceColumn: Monto\n"))
    salida = page_builder.create_page_from_spec(activo, SPEC_TARJETA,
                                                {"Total": "Ventas"})
    assert salida["filled_existing_page"] is True
    assert len(salida["visuals_created"]) == 1
    paginas = pbir_reader.list_pages(activo)
    assert [p["display_name"] for p in paginas] == ["Resumen ejecutivo"]
    # Con visuales ya no se toca: se dice, en vez de responder ok sin hacer nada.
    with pytest.raises(ValidationError, match="ya existe y tiene 1 visuales"):
        page_builder.create_page_from_spec(activo, SPEC_TARJETA, {"Total": "Ventas"})
