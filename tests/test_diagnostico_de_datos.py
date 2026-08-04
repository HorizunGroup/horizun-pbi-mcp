"""Diagnostico de contenido: lo que rompe tableros y ningun metadato ve.

Los cuatro fallos que caza, y por que importan, estan en el docstring del
modulo. Aqui se vigilan las reglas de la casa:

1. **Ningun veredicto sin prueba**: cada hallazgo lleva su consulta DAX y
   muestras de los culpables.
2. **La severidad la decide el dueño**: un campo declarado critico en el brief
   escala a `error` citando SU porque; sin brief, severidades genericas y se
   dice.
3. **"No se comprobo" y "esta bien" no son lo mismo**: un chequeo que revienta
   sale en `skipped` con motivo, jamas se omite en silencio.

El motor DAX se sustituye por un doble que responde segun la consulta: aqui no
se prueba ADOMD (eso ya tiene sus tests), se prueba QUE se pregunta y que se
hace con la respuesta.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import data_diagnose as dd


# ------------------------------------------------------------- el modelo ---
def _modelo(**extra):
    base = {
        "tables": [
            {"name": "Avance", "columns": [
                {"name": "HRZ_COD_PRES", "data_type": "string"},
                {"name": "Fecha", "data_type": "dateTime"}]},
            {"name": "Presupuesto", "columns": [
                {"name": "HRZ_COD_PRES", "data_type": "string"}]},
            {"name": "Calendario", "columns": [
                {"name": "Date", "data_type": "dateTime"}]},
        ],
        "measures": [{"name": "CPI", "table": "Presupuesto"}],
        "relationships": [
            {"from_table": "Avance", "from_column": "HRZ_COD_PRES",
             "to_table": "Presupuesto", "to_column": "HRZ_COD_PRES",
             "is_active": True},
            {"from_table": "Avance", "from_column": "Fecha",
             "to_table": "Calendario", "to_column": "Date",
             "is_active": True},
        ],
    }
    base.update(extra)
    return base


class _MotorFalso:
    """Responde a cada consulta segun su forma. Registra lo preguntado."""

    def __init__(self, respuestas):
        #: [(subcadena_que_identifica, columnas, filas)]
        self.respuestas = respuestas
        self.consultas = []

    def run(self, _session, consulta, max_rows=None):
        self.consultas.append(consulta)
        for clave, columnas, filas in self.respuestas:
            if clave in consulta:
                return {"columns": columnas, "rows": filas}
        return {"columns": [], "rows": []}


@pytest.fixture
def motor(monkeypatch):
    def _instalar(respuestas):
        m = _MotorFalso(respuestas)
        from horizun_pbi_mcp.powerbi import dax_runner

        monkeypatch.setattr(dax_runner, "run_dax", m.run)
        return m
    return _instalar


_SIN_HALLAZGOS = [
    ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
     [[0, 0, 0]]),
    ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
    ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
]


# ------------------------------------------------------- claves huerfanas ---
def test_huerfanas_con_su_prueba_y_sus_muestras(motor):
    m = motor([
        # TOPN va PRIMERO: su consulta tambien contiene DISTINCT('Presupuesto'
        # y en este doble el primer match gana.
        ("TOPN", ["[HRZ_COD_PRES]"], [["D01-A2"], ["D01-A9"], ["D02-X1"]]),
        ("DISTINCT('Presupuesto'", ["[huerfanas]", "[filas_afectadas]",
                                    "[claves_en_blanco]"], [[3, 340, 12]]),
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo())
    hs = [h for h in salida["findings"] if h["rule"] == "claves_huerfanas"]
    assert len(hs) == 1, "solo la relacion con huerfanas reales"
    h = hs[0]
    assert h["evidence"]["orphan_keys"] == 3
    assert h["evidence"]["affected_rows"] == 340
    assert h["evidence"]["blank_keys"] == 12
    assert h["evidence"]["sample_orphans"] == ["D01-A2", "D01-A9", "D02-X1"], (
        "sin los culpables de muestra, el hallazgo no se puede investigar")
    assert "EVALUATE" in h["query"], "ningun veredicto sin su consulta"
    assert "cuadran de menos" in h["impact"]


def test_claves_en_blanco_solas_tambien_son_hallazgo(motor):
    motor([
        ("DISTINCT('Presupuesto'", ["[huerfanas]", "[filas_afectadas]",
                                    "[claves_en_blanco]"], [[0, 0, 25]]),
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo())
    hs = [h for h in salida["findings"] if h["rule"] == "claves_huerfanas"]
    assert len(hs) == 1 and hs[0]["evidence"]["blank_keys"] == 25


def test_sin_problemas_no_se_inventa_nada(motor):
    motor(_SIN_HALLAZGOS)
    salida = dd.diagnose(object(), _modelo())
    assert salida["findings"] == []
    assert salida["clean"] is True
    assert salida["checks_run"] > 0, "limpio PORQUE se comprobo, no por vacio"


# --------------------------------------------------------- grano duplicado ---
def test_grano_duplicado_con_las_claves_repetidas(motor):
    motor([
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ('"filas"', ["[filas]", "[claves]"], [[120, 100]]),
        ("ADDCOLUMNS", ["[HRZ_COD_PRES]", "[n]"], [["D01-A2", 3], ["D07-B1", 2]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo())
    hs = [h for h in salida["findings"] if h["rule"] == "grano_duplicado"]
    assert hs and hs[0]["evidence"]["duplicated_keys"] == 20
    assert {"key": "D01-A2", "count": 3} in hs[0]["evidence"]["sample_duplicates"]
    assert "se multiplica" in hs[0]["impact"]


# ------------------------------------------------------------- calendario ---
def test_huecos_de_calendario(motor):
    motor([
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[358, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo())
    hs = [h for h in salida["findings"] if h["rule"] == "calendario_con_huecos"]
    assert hs and hs[0]["evidence"]["days_missing"] == 7
    assert hs[0]["table"] == "Calendario"


def test_el_calendario_se_detecta_por_tipo_no_por_nombre(motor):
    """Una tabla de fechas llamada 'Periodos' tambien es calendario."""
    m = motor(_SIN_HALLAZGOS)
    modelo = _modelo()
    modelo["tables"][2]["name"] = "Periodos"
    modelo["relationships"][1]["to_table"] = "Periodos"
    dd.diagnose(object(), modelo)
    assert any("Periodos" in c and '"dias"' in c for c in m.consultas), (
        "el chequeo debe correr sobre la tabla de fechas se llame como se llame")


# ------------------------------------------------- la severidad es del dueño ---
def _brief(**extra):
    base = {"purpose": "x", "audience": "y",
            "critical_fields": [
                {"field": "Avance[HRZ_COD_PRES]", "why": "une con presupuesto"}]}
    base.update(extra)
    return base


def test_un_campo_critico_escala_a_error_citando_su_porque(motor):
    motor([
        ("DISTINCT('Presupuesto'", ["[huerfanas]", "[filas_afectadas]",
                                    "[claves_en_blanco]"], [[3, 340, 0]]),
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ("TOPN", ["[HRZ_COD_PRES]"], [["D01-A2"]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo(), brief=_brief())
    h = [x for x in salida["findings"] if x["rule"] == "claves_huerfanas"][0]
    assert h["severity"] == "error", "el dueño declaro ese campo critico"
    assert h["declared_critical"]["why"] == "une con presupuesto"
    assert salida["brief_applied"] is True


def test_sin_brief_la_severidad_es_generica_y_se_dice(motor):
    motor([
        ("DISTINCT('Presupuesto'", ["[huerfanas]", "[filas_afectadas]",
                                    "[claves_en_blanco]"], [[3, 340, 0]]),
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ("TOPN", ["[HRZ_COD_PRES]"], [["D01-A2"]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
    ])
    salida = dd.diagnose(object(), _modelo())
    h = [x for x in salida["findings"] if x["rule"] == "claves_huerfanas"][0]
    assert h["severity"] == "warning"
    assert "pbi_define_brief" in salida["note"]


def test_umbral_del_brief_violado(motor):
    motor([
        ('"huerfanas"', ["[huerfanas]", "[filas_afectadas]", "[claves_en_blanco]"],
         [[0, 0, 0]]),
        ('"filas"', ["[filas]", "[claves]"], [[10, 10]]),
        ('"dias"', ["[dias]", "[rango]"], [[365, 365]]),
        ('"vmin"', ["[vmin]", "[vmax]"], [[0.72, 0.98]]),
    ])
    brief = _brief(critical_fields=[
        {"field": "[CPI]", "why": "decision de intervencion", "min": 0.9}])
    salida = dd.diagnose(object(), _modelo(), brief=brief)
    hs = [h for h in salida["findings"] if h["rule"] == "umbral_del_brief_violado"]
    assert hs and hs[0]["severity"] == "error"
    assert "0.72" in hs[0]["impact"] and "0.9" in hs[0]["impact"]


def test_un_campo_critico_que_ya_no_existe_es_hallazgo_no_silencio(motor):
    motor(_SIN_HALLAZGOS)
    brief = _brief(critical_fields=[
        {"field": "Avance[ColumnaBorrada]", "why": "vital"}])
    salida = dd.diagnose(object(), _modelo(), brief=brief)
    hs = [h for h in salida["findings"]
          if h["rule"] == "campo_critico_inexistente"]
    assert hs and hs[0]["severity"] == "error"
    assert "ColumnaBorrada" in hs[0]["impact"]


# ------------------------------------------- no se comprobo != esta bien ---
def test_un_chequeo_que_revienta_sale_en_skipped(motor, monkeypatch):
    motor(_SIN_HALLAZGOS)

    def explota(*_a, **_k):
        raise RuntimeError("la tabla es DirectQuery y no responde")

    monkeypatch.setattr(dd, "_chequeo_huerfanas", explota)
    salida = dd.diagnose(object(), _modelo())
    assert salida["clean"] is False, "con chequeos saltados NO se declara limpio"
    assert any(s["check"] == "claves_huerfanas" and "DirectQuery" in s["reason"]
               for s in salida["skipped"])


# ----------------------------------------------------------------- escapes ---
def test_una_tabla_con_comilla_no_rompe_el_dax(motor):
    m = motor(_SIN_HALLAZGOS)
    modelo = _modelo()
    modelo["tables"][0]["name"] = "O'Brien"
    modelo["relationships"][0]["from_table"] = "O'Brien"
    dd.diagnose(object(), modelo)
    assert any("'O''Brien'" in c for c in m.consultas), (
        "la comilla del nombre debe ir duplicada en el DAX")


def test_el_filtro_de_tablas_acota_las_relaciones(motor):
    m = motor(_SIN_HALLAZGOS)
    dd.diagnose(object(), _modelo(), tables=["Calendario"])
    assert not any("Presupuesto" in c for c in m.consultas), (
        "la relacion Avance->Presupuesto no toca 'Calendario': fuera")
    assert any("Calendario" in c for c in m.consultas)
