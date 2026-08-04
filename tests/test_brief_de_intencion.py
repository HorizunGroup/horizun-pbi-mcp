"""El brief de intencion: para que existe el tablero, y quien lo consume.

La pieza un nivel por encima de todas las tools: el usuario sabe para que
quiere el tablero y no habia donde ponerlo. Las reglas que se vigilan:

1. **Vive junto al .pbip**, fuera de `.Report/`/`.SemanticModel/` — esos
   arboles los reescribe Desktop al guardar y un archivo ajeno alli puede
   desaparecer sin aviso.
2. **Sin proposito no hay brief.** El error lo dice con la instruccion de
   PREGUNTAR: un brief inventado por el agente fija con autoridad lo que
   nadie dijo.
3. **Los consumidores existen de verdad.** Un brief que nada lee es un
   formulario. La guia lo enseña, la propuesta lo adjunta con la vara para
   juzgar, y el sistema de diseño se recomienda desde `delivery`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import project_locator
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import brief as brief_service
from horizun_pbi_mcp.services import guide, proposals
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip(), session


def _brief_minimo(**extra):
    base = {"purpose": "Controlar el costo semanal de la obra",
            "audience": "Direccion de proyecto"}
    base.update(extra)
    return base


# ---------------------------------------------------------- donde vive ---
def test_el_brief_vive_junto_al_pbip_no_dentro_del_report(proyecto):
    active, _ = proyecto
    r = brief_service.write_brief(active, _brief_minimo())
    ruta = Path(r["path"])
    assert ruta.parent == Path(active.project_dir)
    assert ".Report" not in ruta.parts and ".SemanticModel" not in ruta.parts, (
        "Desktop reescribe esos arboles al guardar: el brief moriria alli")
    assert ruta.name == "pbi-brief.json"


def test_escribir_y_releer_es_idempotente_en_contenido(proyecto):
    active, _ = proyecto
    brief_service.write_brief(active, _brief_minimo(
        key_questions=["¿Vamos dentro del presupuesto?"],
        delivery="pantalla_sala"))
    leido = brief_service.read_brief(active)
    assert leido["purpose"] == "Controlar el costo semanal de la obra"
    assert leido["key_questions"] == ["¿Vamos dentro del presupuesto?"]
    assert leido["delivery"] == "pantalla_sala"


def test_reescribir_actualiza_y_lo_dice(proyecto):
    active, _ = proyecto
    r1 = brief_service.write_brief(active, _brief_minimo())
    r2 = brief_service.write_brief(active, _brief_minimo(
        purpose="Reporte mensual para el cliente"))
    assert r1["created"] is True and r2["updated"] is True
    assert brief_service.read_brief(active)["purpose"] == (
        "Reporte mensual para el cliente")


# ------------------------------------------------------- validacion dura ---
def test_sin_proposito_el_error_manda_preguntar():
    with pytest.raises(ValidationError) as exc:
        brief_service.validate_brief({"audience": "alguien"})
    assert "PREGUNTALO" in str(exc.value), (
        "el error debe empujar a preguntar al humano, no a inventar")


def test_sin_audiencia_tampoco_hay_brief():
    with pytest.raises(ValidationError):
        brief_service.validate_brief({"purpose": "algo"})


def test_delivery_desconocido_lista_las_opciones():
    with pytest.raises(ValidationError) as exc:
        brief_service.validate_brief(_brief_minimo(delivery="holograma"))
    assert "pantalla_sala" in str(exc.value)


def test_umbral_no_numerico_se_rechaza():
    with pytest.raises(ValidationError):
        brief_service.validate_brief(_brief_minimo(
            critical_fields=[{"field": "[CPI]", "min": "cero"}]))


def test_un_brief_corrupto_no_se_calla(proyecto):
    active, _ = proyecto
    brief_service.brief_path(active).write_text('{"cosa": 1}', encoding="utf-8")
    with pytest.raises(brief_service.BriefError):
        brief_service.read_brief(active)


# ----------------------------------------- delivery -> sistema de diseño ---
@pytest.mark.parametrize("delivery,sistema", [
    ("pantalla_sala", "sala"),
    ("escritorio", "informe"),
    ("lectura_pdf", "informe"),
    ("movil", "informe"),
])
def test_la_recomendacion_es_legibilidad_fisica(delivery, sistema):
    rec = brief_service.recommended_system(
        brief_service.validate_brief(_brief_minimo(delivery=delivery)))
    assert rec["system"] == sistema
    assert rec["why"], "una recomendacion sin motivo es una orden"


def test_sin_delivery_no_se_recomienda_nada():
    rec = brief_service.recommended_system(
        brief_service.validate_brief(_brief_minimo()))
    assert rec is None, "recomendar sin saber donde se lee seria adivinar"


# ----------------------------------------------------- los consumidores ---
def test_la_guia_enseña_el_proposito_cuando_existe(proyecto):
    active, session = proyecto
    brief_service.write_brief(active, _brief_minimo())
    s = guide.situacion(session)
    assert s["project"]["brief"]["purpose"] == (
        "Controlar el costo semanal de la obra")
    assert "Proposito declarado" in s["situation"]


def test_la_guia_sugiere_definirlo_cuando_falta(proyecto):
    _active, session = proyecto
    s = guide.situacion(session)
    assert s["project"]["brief"] is None
    assert any(p["tool"] == "pbi_get_brief" for p in s["next_steps"]), (
        "sin brief, el primer hueco es no saber para que es el tablero")


def test_la_propuesta_adjunta_la_vara_del_dueño(proyecto):
    active, _ = proyecto
    from horizun_pbi_mcp.pbip import tmdl_reader

    modelo = tmdl_reader.read_semantic_model(active, strict=False)
    el_brief = brief_service.validate_brief(_brief_minimo(
        key_questions=["¿Vamos dentro del presupuesto?"],
        non_goals=["Detalle por factura"],
        delivery="pantalla_sala"))
    salida = proposals.propose(modelo, brief=el_brief)
    assert salida["brief"]["key_questions"] == ["¿Vamos dentro del presupuesto?"]
    assert salida["brief"]["non_goals"] == ["Detalle por factura"]
    assert salida["recommended_design_system"]["system"] == "sala"
    assert "judge_against" in salida


def test_la_propuesta_sin_brief_lo_dice_no_lo_disimula(proyecto):
    active, _ = proyecto
    from horizun_pbi_mcp.pbip import tmdl_reader

    modelo = tmdl_reader.read_semantic_model(active, strict=False)
    salida = proposals.propose(modelo, brief=None)
    assert salida["brief"] is None
    assert "pbi_define_brief" in salida["hint"], (
        "proponer sin proposito es legitimo, pero no debe parecer lo mismo")
