"""Fase H5 — el puntaje de auditoria debe ser comparable entre informes.

El puntaje anterior era `100 - (errores*10 + avisos*3 + infos*1)`. Como
penaliza el numero ABSOLUTO de hallazgos, y los hallazgos crecen con el tamano
del informe, medía sobre todo el tamano: el PB4 real (21 tablas, 353 columnas)
sacaba 0 en el informe por acumulacion de hallazgos informativos, y una vez en
cero ni empeorar ni mejorar se notaba.

`test_el_puntaje_no_depende_del_tamano` y `test_no_llega_a_cero_por_acumular_infos`
fallan con aquella formula.
"""
from __future__ import annotations

import copy

import pytest

from horizun_pbi_mcp.services import model_audit, scoring


def hallazgo(regla, severidad="warning"):
    return {"rule": regla, "severity": severidad, "domain": "modelo"}


REGLAS = [{"rule": "r_error", "severity": "error"},
          {"rule": "r_aviso", "severity": "warning"},
          {"rule": "r_info", "severity": "info"}]


# ================================================== la formula, en abstracto ==
def test_sin_hallazgos_es_cien():
    assert scoring.compute([], REGLAS, 100)["score"] == 100


def test_todo_incumplido_al_maximo_es_cero():
    hallazgos = ([hallazgo("r_error", "error")] * 50
                 + [hallazgo("r_aviso", "warning")] * 50
                 + [hallazgo("r_info", "info")] * 50)
    assert scoring.compute(hallazgos, REGLAS, 50)["score"] == 0


def test_una_sola_regla_no_puede_hundir_el_puntaje():
    """Tope por regla: mil hallazgos de UNA regla no valen mas que su peso."""
    ruidosa = [hallazgo("r_info", "info")] * 1000
    r = scoring.compute(ruidosa, REGLAS, 10)

    peso_total = sum(scoring.peso_de(x["severity"]) for x in REGLAS)
    peso_info = scoring.peso_de("info")
    minimo = int(round(100 * (1 - peso_info / peso_total)))
    assert r["score"] == minimo, (
        "una regla informativa que dispara mucho no puede costar mas que su peso")
    assert r["score"] > 0


def test_la_severidad_pesa():
    """El mismo numero de hallazgos duele mas si son errores."""
    con_info = scoring.compute([hallazgo("r_info", "info")] * 5, REGLAS, 100)["score"]
    con_error = scoring.compute([hallazgo("r_error", "error")] * 5, REGLAS, 100)["score"]
    assert con_error < con_info


def test_el_divisor_cuenta_las_reglas_que_no_disparan():
    """Cumplir muchas reglas debe notarse: si no, el divisor seria el problema."""
    una_mala = [hallazgo("r_aviso", "warning")]
    pocas = scoring.compute(una_mala, REGLAS[:2], 100)["score"]
    muchas = scoring.compute(una_mala, REGLAS + [
        {"rule": f"ok{i}", "severity": "warning"} for i in range(10)], 100)["score"]
    assert muchas > pocas


def test_la_densidad_importa():
    """Cinco incumplimientos sobre 10 objetos es peor que sobre 1000."""
    denso = scoring.compute([hallazgo("r_aviso")] * 5, REGLAS, 10)["score"]
    disperso = scoring.compute([hallazgo("r_aviso")] * 5, REGLAS, 1000)["score"]
    assert denso < disperso


def test_el_desglose_justifica_el_puntaje():
    r = scoring.compute([hallazgo("r_error", "error")] * 3, REGLAS, 20)
    assert r["rules_applicable"] == 3
    assert r["rules_triggered"] == 1
    assert r["objects_evaluated"] == 20
    peor = r["per_rule"][0]
    assert peor["rule"] == "r_error" and peor["findings"] == 3
    assert sum(d["penalty"] for d in r["per_rule"]) == pytest.approx(r["penalty"])


# ====================================== lo que exigia H5, sobre el motor real ==
def modelo_de(n_tablas: int, con_medida_rota: bool) -> dict:
    m = {
        "tables": [{"name": f"T{i}",
                    "columns": [{"name": f"C{j}"} for j in range(10)]}
                   for i in range(n_tablas)],
        "measures": [{"name": "Total", "table": "T0",
                      "expression": "SUM(T0[C0])"}],
        "relationships": [], "roles": [],
    }
    if con_medida_rota:
        m["measures"].append({"name": "Rota", "table": "T0",
                              "expression": "SUM(NoExiste[X])"})
    return m


def test_el_puntaje_no_depende_del_tamano():
    """Mismo defecto en modelos de 1, 10 y 40 tablas: puntajes comparables.

    Con la formula anterior el modelo grande acumulaba hallazgos informativos
    por cada tabla y columna y se iba al suelo, de modo que dos informes no se
    podian comparar.
    """
    reglas = ["model_no_rls", "measure_broken_reference"]
    puntajes = [model_audit.audit(modelo_de(n, True), rules=reglas)["score"]
                for n in (1, 10, 40)]

    assert max(puntajes) - min(puntajes) <= 15, (
        f"el puntaje varia demasiado con el tamano: {puntajes}")


def test_no_llega_a_cero_por_acumular_infos():
    """Un modelo grande y solo con avisos informativos no puede puntuar 0."""
    grande = modelo_de(40, False)
    r = model_audit.audit(grande)
    solo_informativos = all(h["severity"] == "info" for h in r["findings"])
    if not solo_informativos:
        pytest.skip("el modelo sintetico dispara reglas no informativas")
    assert r["score"] > 0


def test_la_auditoria_expone_como_calculo_el_puntaje():
    r = model_audit.audit(modelo_de(5, True))
    assert r["score_detail"]["method"] == "compliance_weighted_v2"
    assert r["score_detail"]["rules_applicable"] >= r["score_detail"]["rules_triggered"]
    assert r["score"] == r["score_detail"]["score"]
