"""Fase 1A — clasificador de DAX de solo lectura. Politica fail-closed.

El clasificador NO es un parser de DAX: reconoce un conjunto cerrado de formas
y rechaza el resto, incluido lo que probablemente seria inofensivo. Estas
pruebas fijan esa frontera en ambos sentidos: lo que debe pasar y lo que no.
"""
from __future__ import annotations

import pytest

from services import dax_guard
from services.dax_guard import DaxNotReadOnlyError


def permitida(q: str) -> bool:
    return dax_guard.classify(q).allowed


def motivo(q: str) -> str:
    return dax_guard.classify(q).reason or ""


# ------------------------------------------------------------- permitidas ---
@pytest.mark.parametrize("query,forma", [
    ('EVALUATE ROW("ok", 1)', "evaluate"),
    ("evaluate row(\"ok\", 1)", "evaluate"),
    ("   \n\t EVALUATE Ventas", "evaluate"),
    ("EVALUATE TOPN(10, Ventas)", "evaluate"),
    ("EVALUATE 'Tabla Con Espacios'", "evaluate"),
    ("EVALUATE Ventas ORDER BY Ventas[Monto] DESC", "evaluate"),
    ('DEFINE MEASURE Ventas[M] = 1 EVALUATE ROW("v", Ventas[M])', "define_evaluate"),
    ("DEFINE\n  VAR x = 1\nEVALUATE ROW(\"v\", 1)", "define_evaluate"),
    ("SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS", "dmv_select"),
    ("SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES", "dmv_select"),
    ("select * from $SYSTEM.DISCOVER_SESSIONS", "dmv_select"),
])
def test_formas_reconocidas_se_permiten(query, forma):
    c = dax_guard.classify(query)
    assert c.allowed, f"deberia permitirse: {c.reason}"
    assert c.form == forma


def test_varios_evaluate_se_permiten():
    q = 'EVALUATE ROW("a", 1)\nEVALUATE ROW("b", 2)'
    assert permitida(q)


def test_bom_no_impide_la_clasificacion():
    assert permitida('﻿EVALUATE ROW("ok", 1)')


def test_unicode_en_identificadores():
    assert permitida("EVALUATE 'Año Fiscal'")
    assert permitida("EVALUATE Categoría")


def test_comentario_inicial_no_impide_la_clasificacion():
    assert permitida('// nota\nEVALUATE ROW("ok", 1)')
    assert permitida('-- nota\nEVALUATE ROW("ok", 1)')
    assert permitida('/* nota */ EVALUATE ROW("ok", 1)')
    assert permitida('/* varias\n lineas */\nEVALUATE ROW("ok", 1)')


# ----------------------------- palabras peligrosas dentro de literales -------
# El nucleo de la correccion pedida: una palabra dentro de una cadena o de un
# comentario NO puede cambiar la clasificacion.
@pytest.mark.parametrize("query", [
    'EVALUATE ROW("drop table x", 1)',
    'EVALUATE ROW("DELETE FROM Ventas", 1)',
    'EVALUATE ROW("CREATE ALTER DROP", 1)',
    'EVALUATE FILTER(Ventas, Ventas[Nota] = "DROP TABLE")',
    '// DROP TABLE Ventas\nEVALUATE ROW("ok", 1)',
    '-- CREATE TABLE algo\nEVALUATE ROW("ok", 1)',
    '/* REFRESH BACKUP RESTORE */ EVALUATE ROW("ok", 1)',
    "EVALUATE 'DROP TABLE'",
    'EVALUATE ROW("v", Ventas[DROP TABLE])',
    'EVALUATE ROW("comilla ""escapada"" con DROP", 1)',
])
def test_palabra_peligrosa_en_literal_o_comentario_no_afecta(query):
    c = dax_guard.classify(query)
    assert c.allowed, f"la palabra vive en un literal/comentario: {c.reason}"


def test_system_dentro_de_cadena_no_convierte_en_dmv():
    q = 'EVALUATE ROW("origen", "$SYSTEM.TMSCHEMA_TABLES")'
    c = dax_guard.classify(q)
    assert c.allowed and c.form == "evaluate"


def test_evaluate_dentro_de_cadena_no_habilita_una_sentencia_prohibida():
    q = 'DROP TABLE Ventas -- EVALUATE ROW("ok",1)'
    assert not permitida(q)
    assert "DROP" in motivo(q)


# ------------------------------------------------------------- rechazadas ---
@pytest.mark.parametrize("query,fragmento", [
    ("DROP TABLE Ventas", "DROP"),
    ("CREATE TABLE X", "CREATE"),
    ("ALTER MEASURE X", "ALTER"),
    ("DELETE FROM Ventas", "DELETE"),
    ("REFRESH Ventas", "REFRESH"),
    ("BACKUP DATABASE X", "BACKUP"),
    ("RESTORE DATABASE X", "RESTORE"),
    ("CALL SomeProcedure()", "CALL"),
])
def test_sentencias_de_modificacion_se_rechazan(query, fragmento):
    c = dax_guard.classify(query)
    assert not c.allowed
    assert fragmento in (c.reason or "")


def test_xmla_se_rechaza():
    q = '<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine"/>'
    c = dax_guard.classify(q)
    assert not c.allowed and "XMLA" in (c.reason or "")


def test_define_sin_evaluate_se_rechaza():
    c = dax_guard.classify("DEFINE MEASURE Ventas[M] = 1")
    assert not c.allowed
    assert "EVALUATE" in (c.reason or "")


def test_punto_y_coma_se_rechaza_por_ambiguo():
    c = dax_guard.classify('EVALUATE ROW("a",1); EVALUATE ROW("b",2)')
    assert not c.allowed and ";" in (c.reason or "")


def test_consulta_vacia_o_solo_comentarios_se_rechaza():
    assert not permitida("")
    assert not permitida("   \n  ")
    assert not permitida("// solo un comentario")
    assert not permitida("/* solo un comentario */")


@pytest.mark.parametrize("query", [
    'EVALUATE ROW("sin cerrar, 1)',          # cadena abierta
    "EVALUATE 'Tabla sin cerrar",            # identificador citado abierto
    "EVALUATE Ventas[Columna sin cerrar",    # corchete abierto
    "/* comentario sin cerrar EVALUATE ROW(1)",
])
def test_delimitador_sin_cerrar_se_rechaza(query):
    c = dax_guard.classify(query)
    assert not c.allowed
    assert "sin cerrar" in (c.reason or "")


@pytest.mark.parametrize("query", [
    "EVALUATEX ROW(1)",          # token concatenado, no es EVALUATE
    "MYEVALUATE ROW(1)",
    "SELECTX * FROM $SYSTEM.X",
])
def test_tokens_concatenados_no_cuentan_como_palabra_clave(query):
    assert not permitida(query)


@pytest.mark.parametrize("query", [
    "SELECT * FROM Ventas",                       # no es DMV
    "SELECT * FROM $SYSTEMX.FOO",                 # prefijo enganoso
    "SELECT * FROM $SYSTEM",                      # sin rowset
    "SELECT * FROM $SYSTEM.",                     # sin rowset
    "SELECT 1",                                   # sin FROM
])
def test_dmv_enganosas_se_rechazan(query):
    c = dax_guard.classify(query)
    assert not c.allowed


def test_mezcla_evaluate_y_select_se_rechaza():
    assert not permitida('EVALUATE ROW("a",1) SELECT * FROM $SYSTEM.X')


def test_no_empieza_por_forma_reconocida():
    c = dax_guard.classify("ROW(1)")
    assert not c.allowed and "no empieza por" in (c.reason or "")


def test_selectcolumns_no_se_confunde_con_select():
    """SELECTCOLUMNS y SELECTEDVALUE son funciones DAX legitimas."""
    assert permitida('EVALUATE SELECTCOLUMNS(Ventas, "m", Ventas[Monto])')
    assert permitida('EVALUATE ROW("v", SELECTEDVALUE(Ventas[Monto]))')


# ------------------------------------------------------------ assert/error ---
def test_assert_read_only_lanza_con_motivo_accionable():
    with pytest.raises(DaxNotReadOnlyError) as exc:
        dax_guard.assert_read_only("DROP TABLE Ventas")
    assert exc.value.code == "dax_not_read_only"
    assert "DROP" in exc.value.message
    assert exc.value.details["policy"] == "read_only_fail_closed"
    assert "EVALUATE ..." in exc.value.details["allowed_forms"]


def test_assert_read_only_deja_pasar_lo_permitido():
    c = dax_guard.assert_read_only('EVALUATE ROW("ok", 1)')
    assert c.allowed and c.form == "evaluate"


def test_no_existe_escape_por_variable_de_entorno(monkeypatch):
    """En 1A la politica no se puede desactivar desde el entorno."""
    monkeypatch.setenv("PBI_MCP_DAX_ALLOW_UNCLASSIFIED", "1")
    monkeypatch.setenv("PBI_MCP_DAX_ALLOW_ALL", "1")
    with pytest.raises(DaxNotReadOnlyError):
        dax_guard.assert_read_only("DROP TABLE Ventas")


# ------------------------------------------------------------- el escaner ---
def test_escaner_cuenta_literales_y_comentarios():
    sc = dax_guard.scan('EVALUATE ROW("a", 1) // nota')
    assert sc.error is None
    assert sc.literals == 1 and sc.comments == 1


def test_escaner_respeta_escape_por_duplicado():
    sc = dax_guard.scan('EVALUATE ROW("dice ""hola""", 1)')
    assert sc.error is None
    assert "hola" not in sc.residual


def test_comentario_de_bloque_no_anida():
    """DAX cierra en el primer */; el resto vuelve a ser codigo."""
    assert permitida('/* a /* b */ EVALUATE ROW("ok", 1)')
