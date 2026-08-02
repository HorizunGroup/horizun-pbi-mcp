"""Macrofase F — workflows de alto nivel, end-to-end sobre fixtures."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator, tmdl_reader
from powerbi.errors import ValidationError
from services import pbir_edit, project_state, report_audit, workflows
from tests.fixtures import synthetic

P = synthetic.PAGE_ID
CARD = synthetic.CARD_TEMPLATE_ID
ETAPAS = ["analisis", "plan", "preview"]


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, tmdl_reader.read_semantic_model(active), pbip.parent


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


def etapas_de(informe):
    return [e["stage"] for e in informe["stages"]]


# ================================================ workflow 1: dashboard ======
def test_dashboard_ejecutivo_end_to_end(proyecto):
    active, md, _p = proyecto
    w = workflows.build_executive_page(
        active, md, measures=["TotalAmount", "Ratio Pct"],
        category="Calendar[Year]", seed="t", dry_run=False)

    assert w["applied"] is True
    assert etapas_de(w) == ETAPAS + ["apply", "verificacion"]
    ver = w["stages"][-1]["result"]
    assert ver["valid"] is True and ver["broken_references"] == []
    assert "Resumen ejecutivo" in {p.get("display_name")
                                   for p in pbir_reader.list_pages(active)}


def test_dashboard_dry_run_no_escribe(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    w = workflows.build_dashboard(active, md, name="X",
                                  measures=["TotalAmount"], dry_run=True)
    assert w["applied"] is False
    assert etapas_de(w) == ETAPAS
    assert huella(project) == antes


def test_dashboard_con_medida_inexistente(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError) as exc:
        workflows.build_dashboard(active, md, name="X",
                                  measures=["NoExiste"], dry_run=False)
    assert "no existen" in exc.value.message
    assert huella(project) == antes


def test_dashboard_con_preset_desconocido(proyecto):
    active, md, _p = proyecto
    with pytest.raises(ValidationError):
        workflows.build_dashboard(active, md, name="X", measures=["TotalAmount"],
                                  preset="inventado")


def test_dashboard_sin_modelo(proyecto):
    active, _md, _p = proyecto
    with pytest.raises(ValidationError):
        workflows.build_dashboard(active, None, name="X", measures=["A"])


def test_pagina_evm_avisa_de_medidas_faltantes(proyecto):
    active, md, _p = proyecto
    w = workflows.build_evm_page(active, md, measures=["TotalAmount"],
                                 category="Calendar[Year]", dry_run=True)
    assert any("PV" in a for a in w["warnings"]), \
        "debe avisar de que faltan las medidas propias de EVM"


# ================================================ workflow 2: normalizar ====
def test_normalizar_informe_mejora_el_puntaje(proyecto):
    active, md, project = proyecto
    d = pbir_edit.duplicate_visual(active, P, CARD)
    ruta = pbir_edit._visual_file(active, P, d["visual_id"])
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["position"] = {"x": 1250, "y": 700, "width": 300, "height": 200, "z": 5}
    ruta.write_text(json.dumps(datos), encoding="utf-8")

    antes = report_audit.audit_project(active, md)["score"]
    w = workflows.normalize_report(active, md, dry_run=False)
    despues = report_audit.audit_project(active, md)["score"]

    assert w["applied"] is True and despues > antes
    assert etapas_de(w) == ETAPAS + ["apply", "verificacion"]
    assert w["stages"][-1]["result"]["improved"] is True


def test_normalizar_dry_run_no_escribe(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    w = workflows.normalize_report(active, md, dry_run=True)
    assert w["applied"] is False
    assert huella(project) == antes


def test_normalizar_con_layout_correcto_no_hace_nada(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    w = workflows.normalize_report(active, md, dry_run=False)
    assert w["applied"] is False and "ya cumple" in w["summary"]
    assert huella(project) == antes


# =========================================== workflow 3: reparar referencias ==
def _modelo_con_medida_renombrada(md):
    nuevo = json.loads(json.dumps(md))
    nuevo["measures"] = [m for m in nuevo["measures"] if m["name"] != "TotalAmount"]
    nuevo["measures"].append({"name": "ImporteTotal", "table": "Fact",
                              "expression": "SUM(Fact[Amount])"})
    return nuevo


def test_diagnostica_referencias_rotas_sin_repararlas(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    w = workflows.repair_broken_references(active, _modelo_con_medida_renombrada(md))
    assert w["applied"] is False
    rotas = w["stages"][0]["result"]["broken"]
    assert rotas and any(r["reference"] == "Fact[TotalAmount]" for r in rotas)
    assert huella(project) == antes, "sin mapping no se toca nada"
    assert "No se adivina" in " ".join(w["warnings"])


def test_repara_con_mapping(proyecto):
    active, md, _p = proyecto
    roto = _modelo_con_medida_renombrada(md)
    w = workflows.repair_broken_references(
        active, roto, mapping={"Fact[TotalAmount]": "Fact[ImporteTotal]"},
        dry_run=False)
    assert w["applied"] is True
    assert w["stages"][-1]["result"]["remaining_broken"] == 0
    despues = workflows.repair_broken_references(active, roto)
    assert despues["stages"][0]["result"]["broken_count"] == 0


def test_un_destino_inexistente_se_rechaza(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError) as exc:
        workflows.repair_broken_references(
            active, _modelo_con_medida_renombrada(md),
            mapping={"Fact[TotalAmount]": "Fact[TampocoExiste]"}, dry_run=False)
    assert "tampoco existe" in exc.value.message
    assert huella(project) == antes


def test_sin_referencias_rotas_no_hay_nada_que_hacer(proyecto):
    active, md, _p = proyecto
    w = workflows.repair_broken_references(active, md)
    assert "No hay referencias rotas" in w["summary"]


# ================================================================ entrega ====
def test_pre_entrega_produce_checklist(proyecto):
    active, md, _p = proyecto
    w = workflows.prepare_delivery(active, md)
    checklist = w["stages"][1]["result"]["checklist"]
    assert {c["check"] for c in checklist} >= {
        "sin errores", "paginas con contenido", "sin referencias rotas"}
    assert all("ok" in c and "detail" in c for c in checklist)


def test_pre_entrega_detecta_bloqueantes(proyecto):
    active, md, project = proyecto
    roto = _modelo_con_medida_renombrada(md)
    w = workflows.prepare_delivery(active, roto)
    assert "NO listo" in w["summary"]
    assert not w["stages"][1]["result"]["checklist"][0]["ok"]


def test_pre_entrega_dry_run_no_escribe(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    workflows.prepare_delivery(active, md, dry_run=True)
    assert huella(project) == antes


# ================================================================ comparar ===
def test_comparar_sin_modelo_en_vivo_lo_dice(proyecto, session):
    active, _md, _p = proyecto
    w = workflows.compare_live_to_pbip(session)
    assert w["applied"] is False
    assert "en vivo" in w["summary"]


# =========================================================== documentacion ===
def test_documentacion_tecnica(proyecto):
    active, md, _p = proyecto
    doc = workflows.generate_technical_documentation(active, md)
    for seccion in ("## Modelo semantico", "## Informe", "## Auditoria"):
        assert seccion in doc
    assert "TotalAmount" in doc and "```dax" in doc
    assert "Puntaje:" in doc


def test_la_documentacion_incluye_dependencias(proyecto):
    active, md, _p = proyecto
    doc = workflows.generate_technical_documentation(active, md)
    assert "Depende de:" in doc


# ============================================================== seguridad ====
@pytest.mark.real_project_state
def test_los_workflows_respetan_la_politica_estricta(proyecto, monkeypatch):
    active, md, project = proyecto
    antes = huella(project)
    monkeypatch.setattr(project_state, "detect",
                        lambda a, **k: project_state.ProjectOpenState(
                            project_state.OPEN, "high", "abierto"))
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        workflows.build_dashboard(active, md, name="X",
                                  measures=["TotalAmount"], dry_run=False)
    assert huella(project) == antes


def test_los_workflows_no_llaman_tools_decoradas():
    """Componen servicios: `guard()` convertiria los errores en datos."""
    import ast
    import pathlib

    fuente = pathlib.Path("src/services/workflows.py").read_text(encoding="utf-8")
    arbol = ast.parse(fuente)
    llamadas = {n.func.id for n in ast.walk(arbol)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    llamadas |= {n.func.attr for n in ast.walk(arbol)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not {c for c in llamadas if c.startswith("pbi_")}, \
        "un workflow no debe invocar tools decoradas"
