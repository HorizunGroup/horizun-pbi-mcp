"""Renombrar una medida sin romper el informe (#9 del reporte de campo).

El caso real: renombrar 8 medidas a nombres presentables obligo a reescribir el
TMDL completo y re-aplicar la pagina entera, porque cualquier referencia vieja
quedaba rota EN SILENCIO -el visual abre y sale vacio, sin error-.

Lo delicado no es renombrar: es decidir QUE referencias tocar. `[x]` sin
calificar dentro de una medida es una medida; dentro de una columna calculada
es una COLUMNA de su propia tabla; y `Tabla[x]` calificado puede ser una
columna homonima de otra tabla. Tocar de mas corrompe; tocar de menos en
silencio deja visuales vacios. La regla: reescribir solo lo inequivoco, y
DECIR lo que quedo fuera.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator, tmdl_reader
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import workflows
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    modelo = tmdl_reader.read_semantic_model(active, strict=False)
    return active, modelo, pbip.parent


def _fact_tmdl(raiz: Path) -> str:
    return (raiz / "Demo.SemanticModel" / "definition" / "tables"
            / "Fact.tmdl").read_text(encoding="utf-8")


# ------------------------------------------------------------------ plan ---
def test_el_dry_run_lista_todo_lo_que_va_a_tocar(proyecto):
    active, modelo, _ = proyecto
    r = workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Importe Total", dry_run=True)
    assert r["applied"] is False
    plan_modelo = next(e for e in r["stages"] if e["stage"] == "plan_modelo")
    assert plan_modelo["result"]["dax_references"] == 2, (
        "Ratio Pct referencia [TotalAmount] dos veces")
    plan_informe = next(e for e in r["stages"] if e["stage"] == "plan_informe")
    assert len(plan_informe["result"]["visuals"]) == 2, (
        "los dos visuales plantilla del fixture la usan")


def test_el_dry_run_no_escribe_nada(proyecto):
    active, modelo, raiz = proyecto
    antes = _fact_tmdl(raiz)
    workflows.rename_measure(active, modelo, table="Fact",
                             old_name="TotalAmount",
                             new_name="Importe Total", dry_run=True)
    assert _fact_tmdl(raiz) == antes


# ----------------------------------------------------------------- apply ---
def test_renombra_modelo_dax_y_visuales_en_una_pasada(proyecto):
    active, modelo, raiz = proyecto
    r = workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Importe Total", dry_run=False)
    assert r["applied"] is True

    tmdl = _fact_tmdl(raiz)
    assert "measure 'Importe Total' =" in tmdl, "la cabecera se renombro"
    assert "[TotalAmount]" not in tmdl, "las refs DAX de Ratio Pct se movieron"
    assert "[Importe Total]" in tmdl

    visuales = pbir_reader.list_visuals(active, synthetic.PAGE_ID, strict=True)
    refs = [m for v in visuales for m in v.get("measures", [])]
    assert "Fact[Importe Total]" in refs, "el card apunta al nombre nuevo"
    assert "Fact[TotalAmount]" not in refs

    verif = next(e for e in r["stages"] if e["stage"] == "verificacion")
    assert verif["result"]["renamed_verified"] is True
    assert verif["result"]["leftover_references"] == []
    assert not r.get("warnings")


def test_el_renombrado_se_verifica_releyendo_no_se_supone(proyecto):
    active, modelo, _ = proyecto
    r = workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Nuevo Nombre", dry_run=False)
    releido = tmdl_reader.read_semantic_model(active, strict=False)
    nombres = {m["name"] for m in releido["measures"]}
    assert "Nuevo Nombre" in nombres and "TotalAmount" not in nombres


# ----------------------------------------------- lo que NO se debe tocar ---
def test_una_referencia_calificada_no_se_reescribe():
    """`Tabla[x]` puede ser una columna homonima de otra tabla: no se adivina."""
    texto = "x := Fact[TotalAmount] + 'Fact'[TotalAmount] + [TotalAmount]"
    nuevo, n = workflows._reemplazar_ref_dax(texto, "TotalAmount", "Nuevo")
    assert n == 1, "solo la forma sin calificar"
    assert "Fact[TotalAmount]" in nuevo
    assert "'Fact'[TotalAmount]" in nuevo
    assert "[Nuevo]" in nuevo


def test_los_bloques_de_columna_quedan_intactos(proyecto):
    """En una columna calculada, `[x]` es una COLUMNA de su tabla. Se anade una
    columna calculada que usa [Amount] y se renombra una medida 'Amount' de
    OTRA tabla... no: el caso minimo es que el barrido de bloques NO incluya
    columnas."""
    lineas = [
        "table T",
        "",
        "\tmeasure M1 = [Viejo] + 1",
        "\t\tformatString: 0",
        "",
        "\tcolumn C1 = [Viejo] * 2",
        "\t\tdataType: double",
        "",
    ]
    bloques = workflows._bloques_de_medida(lineas)
    assert [b["name"] for b in bloques] == ["M1"], (
        "el bloque de la columna calculada no puede entrar al reemplazo")
    assert all(2 <= b["start"] < b["end"] <= 5 for b in bloques)


# ------------------------------------------------------------- colisiones ---
def test_renombrar_a_una_medida_existente_falla(proyecto):
    active, modelo, _ = proyecto
    with pytest.raises(ValidationError) as exc:
        workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Ratio Pct", dry_run=True)
    assert "unicos en" in str(exc.value)


def test_renombrar_a_una_columna_de_la_misma_tabla_falla(proyecto):
    """La leccion del preflight: se escribe bien y Desktop rechaza al ABRIR."""
    active, modelo, _ = proyecto
    with pytest.raises(ValidationError) as exc:
        workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Amount", dry_run=True)
    assert "COLUMNA" in str(exc.value)


def test_medida_inexistente_dice_las_disponibles(proyecto):
    active, modelo, _ = proyecto
    with pytest.raises(ValidationError) as exc:
        workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="NoExiste", new_name="X",
                                 dry_run=True)
    assert "TotalAmount" in str(exc.value.details.get("available", []))


# --------------------------------------------------- el barrido de restos ---
def test_una_referencia_en_un_bookmark_sale_avisada_no_silenciada(proyecto):
    active, modelo, raiz = proyecto
    marcadores = (raiz / "Demo.Report" / "definition" / "bookmarks")
    marcadores.mkdir(parents=True)
    (marcadores / "b1.json").write_text(json.dumps({
        "filtro": {"field": {"Measure": {
            "Expression": {"SourceRef": {"Entity": "Fact"}},
            "Property": "TotalAmount"}}}}), encoding="utf-8")

    r = workflows.rename_measure(active, modelo, table="Fact",
                                 old_name="TotalAmount",
                                 new_name="Importe Total", dry_run=False)
    assert any("bookmarks/b1.json" in a for a in r["warnings"]), (
        "una referencia que no se toco tiene que salir con su ubicacion")
    verif = next(e for e in r["stages"] if e["stage"] == "verificacion")
    assert "bookmarks/b1.json" in verif["result"]["leftover_references"]
