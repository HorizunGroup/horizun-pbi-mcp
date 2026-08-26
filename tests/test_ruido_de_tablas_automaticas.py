"""Las tablas de fecha automatica no pueden enterrar lo accionable.

Power BI crea una `LocalDateTable_<guid>` por CADA columna de fecha cuando
"Auto fecha y hora" esta activo, y cada una trae siete columnas calculadas.
Un modelo con veinte fechas produce 140 hallazgos `columna_calculada` que
nadie puede corregir de uno en uno -la unica accion posible es desactivar la
opcion- y que empujan fuera de la vista lo que si se arregla.

El caso de regresion es el que motivo esto: tres relaciones bidireccionales
tenian que seguir viendose, individualmente y por delante del resumen.
"""
from __future__ import annotations

from horizun_pbi_mcp.reporting import analyze_model_quality


def _columnas_automaticas() -> list:
    return [{"name": n, "column_type": "Calculated", "expression": "x"}
            for n in ("Date", "Year", "MonthNo", "Month", "QuarterNo",
                      "Quarter", "Day")]


def _modelo(auto_tablas: int = 3) -> dict:
    tablas = [
        {"name": "Hechos", "measure_count": 1, "columns": [
            {"name": "Importe"},
            {"name": "ClienteID"},          # id_visible: accionable
        ]},
        {"name": "Calendario", "measure_count": 0, "is_date_table": True,
         "columns": [{"name": "Date"}]},
        {"name": "Dim", "measure_count": 0, "columns": [{"name": "Clave"}]},
        # Sin relaciones y sin medidas: huerfana de verdad, y accionable.
        {"name": "Suelta", "measure_count": 0, "columns": [{"name": "Campo"}]},
    ]
    for i in range(auto_tablas):
        tablas.append({
            "name": f"LocalDateTable_0000000{i}-0000-0000-0000-000000000000",
            "measure_count": 0, "columns": _columnas_automaticas()})
    tablas.append({"name": "DateTableTemplate_11111111-1111-1111-1111-111111111111",
                   "measure_count": 0, "columns": _columnas_automaticas()})
    return {
        "tables": tablas,
        "measures": [{"name": "Total", "table": "Hechos",
                      "expression": "SUM(Hechos[Importe])",
                      "display_folder": "Base"}],
        "relationships": [
            {"name": f"rel{i}", "from_table": "Hechos", "to_table": "Dim",
             "cross_filtering": "BothDirections"} for i in range(3)
        ],
    }


def test_no_hay_un_hallazgo_por_cada_columna_automatica():
    resultado = analyze_model_quality(_modelo())

    calculadas = [i for i in resultado["issues"]
                  if i["category"] == "columna_calculada"]
    assert calculadas == [], \
        "las columnas de las tablas automaticas no se reportan una a una"


def test_hay_como_mucho_un_resumen_agregado():
    resultado = analyze_model_quality(_modelo())

    resumen = [i for i in resultado["issues"]
               if i["category"] == "tablas_de_fecha_automaticas"]
    assert len(resumen) == 1
    assert "4 tabla(s)" in resumen[0]["message"]      # 3 Local + 1 Template
    assert "28 columna(s)" in resumen[0]["message"]
    assert resumen[0]["severity"] == "info"


def test_el_resumen_dice_que_hacer():
    resultado = analyze_model_quality(_modelo())
    resumen = next(i for i in resultado["issues"]
                   if i["category"] == "tablas_de_fecha_automaticas")

    assert "Auto fecha y hora" in resumen["message"]


def test_las_tres_bidireccionales_siguen_apareciendo_una_a_una():
    """El caso de regresion, literal."""
    resultado = analyze_model_quality(_modelo())

    bidi = [i for i in resultado["issues"]
            if i["category"] == "relacion_bidireccional"]
    assert len(bidi) == 3
    assert [i["object"] for i in bidi] == ["rel0", "rel1", "rel2"]


def test_lo_accionable_va_antes_que_el_resumen_informativo():
    resultado = analyze_model_quality(_modelo())
    categorias = [i["category"] for i in resultado["issues"]]

    ultima_bidi = max(i for i, c in enumerate(categorias)
                      if c == "relacion_bidireccional")
    resumen = categorias.index("tablas_de_fecha_automaticas")
    assert ultima_bidi < resumen


def test_los_problemas_se_ordenan_por_severidad_y_luego_categoria():
    resultado = analyze_model_quality(_modelo())
    orden = {"error": 0, "warning": 1, "info": 2}
    claves = [(orden[i["severity"]], i["category"]) for i in resultado["issues"]]

    assert claves == sorted(claves)


def test_una_tabla_automatica_no_se_denuncia_como_huerfana():
    resultado = analyze_model_quality(_modelo())

    huerfanas = [i["object"] for i in resultado["issues"]
                 if i["category"] == "tabla_huerfana"]
    assert all(not str(o).startswith(("LocalDateTable_", "DateTableTemplate_"))
               for o in huerfanas)


def test_lo_accionable_del_modelo_de_verdad_sigue_estando():
    resultado = analyze_model_quality(_modelo())
    categorias = {i["category"] for i in resultado["issues"]}

    assert "id_visible" in categorias           # Hechos[ClienteID]
    assert "tabla_huerfana" in categorias       # Dim no se relaciona con nadie


def test_un_modelo_sin_tablas_automaticas_no_gana_hallazgos():
    resultado = analyze_model_quality(_modelo(auto_tablas=0))
    modelo_limpio = _modelo(auto_tablas=0)
    modelo_limpio["tables"] = [t for t in modelo_limpio["tables"]
                               if not t["name"].startswith("DateTableTemplate_")]

    sin_ninguna = analyze_model_quality(modelo_limpio)
    assert sin_ninguna["auto_date_tables"]["count"] == 0
    assert [i for i in sin_ninguna["issues"]
            if i["category"] == "tablas_de_fecha_automaticas"] == []
    # Con una sola plantilla si aparece el resumen, y una sola vez.
    assert resultado["auto_date_tables"]["count"] == 1
