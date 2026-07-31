"""Macrofase E — auditoria integral y correcciones seleccionables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator, tmdl_reader
from powerbi.errors import ValidationError
from services import pbir_edit, project_state, report_audit
from tests.fixtures import synthetic

P = synthetic.PAGE_ID
CARD = synthetic.CARD_TEMPLATE_ID


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, tmdl_reader.read_semantic_model(active), pbip.parent


def degradar(active, project):
    """Introduce a proposito un visual sin titulo y fuera del lienzo."""
    d = pbir_edit.duplicate_visual(active, P, CARD)
    ruta = pbir_edit._visual_file(active, P, d["visual_id"])
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["visual"]["visualContainerObjects"].pop("title", None)
    datos["position"] = {"x": 1200, "y": 650, "width": 300, "height": 200, "z": 9}
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    return d["visual_id"]


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


# ======================================================== auditoria informe ===
def test_audita_el_informe(proyecto):
    active, md, _p = proyecto
    r = report_audit.audit_report(active, md)
    assert r["page_count"] == 1
    assert isinstance(r["findings"], list)


def test_detecta_visual_sin_titulo(proyecto):
    active, md, project = proyecto
    vid = degradar(active, project)
    r = report_audit.audit_report(active, md)
    sin_titulo = [h for h in r["findings"]
                  if h["rule"] == "report_visual_without_title"]
    assert sin_titulo and sin_titulo[0]["object"]["id"] == vid
    assert sin_titulo[0]["auto_fix_available"] is True


def test_detecta_pagina_vacia(proyecto):
    active, md, _p = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Vacia")
    for v in pbir_reader.list_visuals(active, dp["page_id"]):
        pbir_edit.delete_visual(active, dp["page_id"], v["id"], confirm=True)
    r = report_audit.audit_report(active, md)
    assert any(h["rule"] == "report_page_empty" for h in r["findings"])


def test_detecta_referencia_rota_en_visual(proyecto):
    active, md, _p = proyecto
    md["measures"] = [m for m in md["measures"] if m["name"] != "TotalAmount"]
    r = report_audit.audit_report(active, md)
    rotas = [h for h in r["findings"] if h["rule"] == "report_broken_field_reference"]
    assert rotas and rotas[0]["severity"] == "error"
    assert "TotalAmount" in rotas[0]["evidence"]["missing_reference"]


def test_detecta_visual_duplicado(proyecto):
    active, md, project = proyecto
    pbir_edit.duplicate_visual(active, P, CARD)
    r = report_audit.audit_report(active, md)
    assert any(h["rule"] == "report_duplicate_visual" for h in r["findings"])


def test_detecta_lienzos_inconsistentes(proyecto):
    active, md, project = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Otra")
    ruta = project / "Demo.Report" / "definition" / "pages" / dp["page_id"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8"))
    datos["width"] = 1920
    ruta.write_text(json.dumps(datos), encoding="utf-8")
    r = report_audit.audit_report(active, md)
    assert any(h["rule"] == "report_inconsistent_canvas" for h in r["findings"])


def test_detecta_medida_sin_uso_en_ningun_sitio(proyecto):
    active, md, _p = proyecto
    md["measures"].append({"name": "Huerfana", "table": "Fact", "expression": "1"})
    r = report_audit.audit_report(active, md)
    huerfanas = [h for h in r["findings"]
                 if h["rule"] == "report_measure_unused_anywhere"]
    assert any(h["object"]["name"] == "Huerfana" for h in huerfanas)


# ======================================================== auditoria integral ===
def test_auditoria_integral_combina_dominios(proyecto):
    active, md, _p = proyecto
    a = report_audit.audit_project(active, md)
    assert 0 <= a["score"] <= 100
    assert a["by_domain"], "debe puntuar por dominio"
    assert all(0 <= d["score"] <= 100 for d in a["by_domain"].values())
    assert a["executive_summary"]


def test_cada_hallazgo_conserva_su_forma(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    for h in report_audit.audit_project(active, md)["findings"]:
        assert h["rule"] and h["severity"] in ("info", "warning", "error")
        assert h["domain"] and isinstance(h["evidence"], dict)
        assert len(h["recommendation"]) > 20
        assert isinstance(h["auto_fix_available"], bool)


def test_la_prioridad_pone_los_errores_primero(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    prioridad = report_audit.audit_project(active, md)["priority"]
    severidades = [h["severity"] for h in prioridad]
    orden = {"error": 0, "warning": 1, "info": 2}
    assert severidades == sorted(severidades, key=lambda s: orden[s])


def test_filtrar_por_severidad(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    a = report_audit.audit_project(active, md, min_severity="warning")
    assert all(h["severity"] in ("warning", "error") for h in a["findings"])


def test_la_auditoria_no_escribe_nada(proyecto):
    active, md, project = proyecto
    antes = huella(project)
    report_audit.audit_project(active, md)
    assert huella(project) == antes


# ================================================================ autofixes ===
def test_no_existe_arreglar_todo(proyecto):
    active, md, _p = proyecto
    a = report_audit.audit_project(active, md)
    with pytest.raises(ValidationError) as exc:
        report_audit.plan_fixes(active, a, [])
    assert "arreglar todo" in exc.value.message


def test_regla_sin_autofix_se_rechaza(proyecto):
    active, md, _p = proyecto
    a = report_audit.audit_project(active, md)
    with pytest.raises(ValidationError):
        report_audit.plan_fixes(active, a, ["measure_possibly_unused"])


def test_el_plan_no_escribe(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    antes = huella(project)
    a = report_audit.audit_project(active, md)
    plan = report_audit.plan_fixes(active, a, ["report_visual_without_title"])
    assert plan["planned"] is True and plan["actions"]
    assert huella(project) == antes


def test_el_plan_se_acota_por_objeto(proyecto):
    active, md, project = proyecto
    vid = degradar(active, project)
    a = report_audit.audit_project(active, md)
    todo = report_audit.plan_fixes(active, a, ["report_visual_without_title"])
    acotado = report_audit.plan_fixes(active, a, ["report_visual_without_title"],
                                      objects=["id_inexistente"])
    assert todo["action_count"] >= 1
    assert acotado["action_count"] == 0, "acotar por objeto debe filtrar"


def test_aplicar_corrige_y_mejora_el_puntaje(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    antes = report_audit.audit_project(active, md)

    plan = report_audit.plan_fixes(
        active, antes, ["report_visual_without_title", "layout_out_of_canvas"])
    r = report_audit.apply_fixes(active, plan["actions"])
    assert r["applied"] == len(plan["actions"]) and r["failed"] == 0

    despues = report_audit.audit_project(active, md)
    assert despues["score"] > antes["score"], "el puntaje debe mejorar"
    assert despues["finding_count"] < antes["finding_count"]
    assert not any(h["rule"] == "report_visual_without_title"
                   for h in despues["findings"])
    assert not any(h["rule"] == "layout_out_of_canvas" for h in despues["findings"])


def test_una_accion_fallida_no_detiene_las_demas(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    a = report_audit.audit_project(active, md)
    plan = report_audit.plan_fixes(active, a, ["report_visual_without_title"])
    acciones = plan["actions"] + [{"rule": "x", "action": "accion_inventada",
                                   "page": P, "reason": "-"}]
    r = report_audit.apply_fixes(active, acciones)
    assert r["applied"] >= 1 and r["failed"] == 1
    assert r["warnings"], "el fallo debe reportarse, no ocultarse"


@pytest.mark.real_project_state
def test_los_autofixes_respetan_la_politica_estricta(proyecto, monkeypatch):
    active, md, project = proyecto
    degradar(active, project)
    a = report_audit.audit_project(active, md)
    plan = report_audit.plan_fixes(active, a, ["report_visual_without_title"])
    antes = huella(project)
    monkeypatch.setattr(project_state, "detect",
                        lambda x, **k: project_state.ProjectOpenState(
                            project_state.OPEN, "high", "abierto"))
    r = report_audit.apply_fixes(active, plan["actions"])
    assert r["applied"] == 0 and r["failed"] >= 1
    assert huella(project) == antes, "con Desktop abierto no se escribe nada"


# ================================================================== salidas ===
def test_markdown(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    texto = report_audit.to_markdown(report_audit.audit_project(active, md))
    assert "Puntaje global" in texto and "| Dominio | Puntaje |" in texto
    assert "Recomendacion" in texto


def test_html(proyecto):
    active, md, project = proyecto
    degradar(active, project)
    doc = report_audit.to_html(report_audit.audit_project(active, md))
    assert doc.startswith("<!doctype html>") and "</html>" in doc
    assert "prefers-color-scheme" in doc, "debe verse en claro y oscuro"


def test_el_html_escapa_el_contenido(proyecto):
    active, md, _p = proyecto
    a = report_audit.audit_project(active, md)
    a["priority"] = [{"rule": "<script>alert(1)</script>", "severity": "info",
                      "object": {"name": "<b>x</b>"}, "evidence": {},
                      "recommendation": "<i>y</i>", "auto_fix_available": False}]
    doc = report_audit.to_html(a)
    assert "<script>alert(1)</script>" not in doc
    assert "&lt;script&gt;" in doc


def test_catalogo_de_autofixes():
    assert report_audit.AUTOFIXES
    for regla, datos in report_audit.AUTOFIXES.items():
        assert datos["description"] and datos["target"]
