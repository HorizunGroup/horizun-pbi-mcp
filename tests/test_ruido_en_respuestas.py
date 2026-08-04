"""Menos ruido en las respuestas: cada dato una vez, cada aviso una vez.

El caso medido: UNA llamada a `pbi_apply_page_spec` con 14 visuales devolvia 14
copias literales de «No habia un visual de este tipo para clonar...» y cada
ruta de archivo TRES veces -en `transaction.files` (con sha256), en
`transaction.by_outcome` (reagrupada bajo "committed") y en
`side_effects[].files`-. Miles de tokens de la ventana del agente sin
informacion nueva.

Las dos reglas que se vigilan:

1. `by_outcome` solo aparece cuando DIAGNOSTICA: conflicto o rollback. En una
   transaccion limpia y confirmada era la tercera copia de cada ruta. Sus
   consumidores reales (tests de rollback, recovery) lo usan todos en el caso
   sucio, que se conserva intacto; y el manifiesto en disco siempre lleva todo.
2. Los avisos identicos se colapsan a «mensaje (×N)», conservando el orden de
   primera aparicion y el tipo lista-de-cadenas: quien busque un texto por
   `in` lo sigue encontrando.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import envelope
from horizun_pbi_mcp.services.txn import transaction
from tests.fixtures import synthetic


# ------------------------------------------------------ avisos duplicados ---
def test_catorce_avisos_identicos_se_vuelven_uno_con_su_cuenta():
    aviso = ("No habia un visual de este tipo para clonar; se genero una "
             "plantilla minima.")
    salida = envelope.success({"warnings": [aviso] * 14},
                              operation="op", request_id="r", duration_ms=1)
    assert salida["warnings"] == [f"{aviso} (×14)"]


def test_un_aviso_unico_queda_intacto():
    salida = envelope.success({"warnings": ["algo paso"]},
                              operation="op", request_id="r", duration_ms=1)
    assert salida["warnings"] == ["algo paso"]


def test_avisos_distintos_conservan_su_orden():
    salida = envelope.success({"warnings": ["b", "a", "b", "c", "a", "b"]},
                              operation="op", request_id="r", duration_ms=1)
    assert salida["warnings"] == ["b (×3)", "a (×2)", "c"]


def test_el_texto_sigue_siendo_buscable_por_in():
    """Los tests y agentes existentes buscan por subcadena: debe sobrevivir."""
    salida = envelope.success({"warnings": ["cargaron CERO filas"] * 3},
                              operation="op", request_id="r", duration_ms=1)
    assert any("CERO filas" in a for a in salida["warnings"])


def test_el_estado_warning_no_cambia_por_deduplicar():
    salida = envelope.success({"warnings": ["x"] * 5},
                              operation="op", request_id="r", duration_ms=1)
    assert salida["status"] == envelope.WARNING


def test_una_forma_inesperada_se_deja_intacta():
    """Mejor intacta que adivinada: no es una lista de cadenas, no se toca."""
    raro = [{"code": "w1"}, {"code": "w1"}]
    salida = envelope.success({"warnings": raro},
                              operation="op", request_id="r", duration_ms=1)
    assert salida["warnings"] == raro


# ------------------------------------------------- by_outcome solo si suma ---
def _visual_valido(nombre):
    return {
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/visualContainer/2.7.0/schema.json"),
        "name": nombre,
        "position": {"x": 0, "y": 0, "width": 100, "height": 100},
        "visual": {"visualType": "card"},
    }


@pytest.fixture
def entorno(tmp_path):
    pbip = synthetic.materialize(tmp_path)
    project = pbip.parent
    backups = tmp_path / "backups"
    backups.mkdir()
    target = (project / "Demo.Report" / "definition" / "pages" / "page01"
              / "visuals" / synthetic.CARD_TEMPLATE_ID / "visual.json")
    return project, backups, target


def test_una_transaccion_limpia_no_repite_las_rutas_en_by_outcome(entorno):
    import json

    project, backups, target = entorno
    # El contenido REAL del visual con un cambio trivial: el validador oficial
    # corre dentro de la transaccion y un documento minimo inventado introduce
    # un error nuevo y la revierte -con razon-.
    doc = json.loads(target.read_text(encoding="utf-8"))
    doc["position"]["x"] = doc["position"].get("x", 0) + 1
    with transaction(project, backups, [target], tool="prueba",
                     report_dir=project / "Demo.Report") as tx:
        tx.write_json(target, doc)
    resumen = tx.summary()

    assert resumen["clean"] is True and resumen["committed"] is True
    assert "by_outcome" not in resumen, (
        "en una transaccion limpia by_outcome es la tercera copia de cada ruta")
    assert resumen["files"], "el detalle autoritativo (files) se conserva"


def test_un_rollback_conserva_by_outcome_entero(entorno):
    project, backups, target = entorno
    with pytest.raises(RuntimeError):
        with transaction(project, backups, [target], tool="prueba",
                         report_dir=project / "Demo.Report") as tx:
            tx.write_json(target, _visual_valido(synthetic.CARD_TEMPLATE_ID))
            raise RuntimeError("fallo simulado")  # antes de validar: rollback
    resumen = tx.summary()

    assert resumen["committed"] is False
    assert "by_outcome" in resumen, (
        "cuando algo se deshizo, by_outcome es el diagnostico y debe estar")
