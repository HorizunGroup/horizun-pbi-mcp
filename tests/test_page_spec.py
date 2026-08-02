"""Macrofase D — spec declarativo: schema, validacion, layout, diff y apply."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator, tmdl_reader
from powerbi.errors import ValidationError
from services import page_spec, project_state
from services import txn as txn_service
from services.page_spec import SpecValidationError
from tests.fixtures import synthetic


def spec_base(nombre="Resumen"):
    return {
        "schema_version": "1.0",
        "page": {"name": nombre, "width": 1280, "height": 720},
        "layout": {"preset": "executive", "gap": 16},
        "visuals": [
            {"type": "card", "title": "Importe", "fields": {"values": ["[TotalAmount]"]}},
            {"type": "card", "title": "Ratio", "fields": {"values": ["[Ratio Pct]"]}},
            {"type": "columnChart", "title": "Por ano",
             "fields": {"category": "Calendar[Year]", "values": ["[TotalAmount]"]}},
        ],
        "filters": [], "interactions": [],
    }


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    md = tmdl_reader.read_semantic_model(active)
    return active, md, pbip.parent, isolated_settings


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


# ================================================================= schema ====
def test_spec_valido():
    assert page_spec.validate_schema(spec_base()) == []


@pytest.mark.parametrize("mutacion,path_esperado", [
    (lambda s: s.pop("schema_version"), "$.schema_version"),
    (lambda s: s.update(schema_version="9.9"), "$.schema_version"),
    (lambda s: s.pop("page"), "$.page"),
    (lambda s: s["page"].pop("name"), "$.page.name"),
    (lambda s: s["page"].update(width=-5), "$.page.width"),
    (lambda s: s.update(visuals=[]), "$.visuals"),
    (lambda s: s.update(visuals="no soy lista"), "$.visuals"),
    (lambda s: s["visuals"][1].pop("type"), "$.visuals[1].type"),
    (lambda s: s["visuals"][1].update(type="inventado"), "$.visuals[1].type"),
    (lambda s: s["visuals"][0].update(fields="texto"), "$.visuals[0].fields"),
    (lambda s: s["visuals"][0].update(position={"x": "no"}), "$.visuals[0].position.x"),
    (lambda s: s.update(filters="no soy lista"), "$.filters"),
])
def test_los_errores_traen_json_path(mutacion, path_esperado):
    s = spec_base()
    mutacion(s)
    errores = page_spec.validate_schema(s)
    assert any(e["path"] == path_esperado for e in errores), \
        f"se esperaba un error en {path_esperado}, hubo {[e['path'] for e in errores]}"


def test_el_spec_debe_ser_un_objeto():
    assert page_spec.validate_schema("no soy un dict")[0]["path"] == "$"


# =========================================================== referencias =====
def test_referencias_validas(proyecto):
    _a, md, _p, _s = proyecto
    r = page_spec.resolve_references(spec_base(), md)
    assert r["resolved"] is True and r["errors"] == []
    assert len(r["references"]) == 4


def test_referencia_inexistente(proyecto):
    _a, md, _p, _s = proyecto
    s = spec_base()
    s["visuals"][0]["fields"]["values"] = ["[NoExiste]"]
    r = page_spec.resolve_references(s, md)
    assert r["resolved"] is False
    assert r["errors"][0]["path"] == "$.visuals[0].fields.values[0]"
    assert "hint" in r["errors"][0]


def test_referencia_ambigua_se_rechaza(proyecto):
    """Una columna con el mismo nombre en dos tablas no se resuelve sola."""
    _a, md, _p, _s = proyecto
    md["tables"].append({"name": "Otra", "columns": [{"name": "Year"}],
                         "measures": [], "measure_count": 0})
    s = spec_base()
    s["visuals"][2]["fields"]["category"] = "Year"
    r = page_spec.resolve_references(s, md)
    assert r["resolved"] is False
    assert "ambiguo" in r["errors"][0]["message"]


def test_sin_modelo_no_se_inventa_nada():
    r = page_spec.resolve_references(spec_base(), None)
    assert r["resolved"] is False and r["warnings"]


# ================================================================ layout =====
def test_el_layout_es_determinista(proyecto):
    active, md, _p, _s = proyecto
    a = page_spec.compile_spec(active, spec_base(), md, seed="s")
    b = page_spec.compile_spec(active, spec_base(), md, seed="s")
    assert a["positions"] == b["positions"]


def test_ids_deterministas_con_semilla(proyecto):
    active, md, _p, _s = proyecto
    a = page_spec.compile_spec(active, spec_base(), md, seed="fija")
    b = page_spec.compile_spec(active, spec_base(), md, seed="fija")
    assert ([v["visual"]["name"] for v in a["visuals"]]
            == [v["visual"]["name"] for v in b["visuals"]])


def test_ids_distintos_con_semillas_distintas(proyecto):
    active, md, _p, _s = proyecto
    a = page_spec.compile_spec(active, spec_base(), md, seed="uno")
    b = page_spec.compile_spec(active, spec_base(), md, seed="dos")
    assert ([v["visual"]["name"] for v in a["visuals"]]
            != [v["visual"]["name"] for v in b["visuals"]])


def test_sin_semilla_los_ids_son_aleatorios(proyecto):
    active, md, _p, _s = proyecto
    a = page_spec.compile_spec(active, spec_base(), md)
    b = page_spec.compile_spec(active, spec_base(), md)
    assert ([v["visual"]["name"] for v in a["visuals"]]
            != [v["visual"]["name"] for v in b["visuals"]])


def test_las_posiciones_explicitas_se_respetan(proyecto):
    active, md, _p, _s = proyecto
    s = spec_base()
    s.pop("layout")
    for i, v in enumerate(s["visuals"]):
        v["position"] = {"x": i * 300 + 20, "y": 20, "width": 250, "height": 150}
    c = page_spec.compile_spec(active, s, md)
    assert [p["x"] for p in c["positions"]] == [20, 320, 620]


def test_todas_las_posiciones_caben_en_el_lienzo(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md)
    for p in c["positions"]:
        assert p["x"] >= 0 and p["y"] >= 0
        assert p["x"] + p["width"] <= c["canvas"]["width"] + 1
        assert p["y"] + p["height"] <= c["canvas"]["height"] + 1


# =============================================================== compilar ====
def test_compilar_falla_con_esquema_invalido(proyecto):
    active, md, _p, _s = proyecto
    s = spec_base()
    s["visuals"][0].pop("type")
    with pytest.raises(SpecValidationError) as exc:
        page_spec.compile_spec(active, s, md)
    assert exc.value.code == "page_spec_invalid"
    assert exc.value.details["errors"][0]["path"] == "$.visuals[0].type"


def test_compilar_falla_con_referencia_rota(proyecto):
    active, md, _p, _s = proyecto
    s = spec_base()
    s["visuals"][0]["fields"]["values"] = ["[NoExiste]"]
    with pytest.raises(SpecValidationError):
        page_spec.compile_spec(active, s, md)


def test_compilar_no_escribe_nada(proyecto):
    active, md, project, settings = proyecto
    antes = huella(project)
    page_spec.compile_spec(active, spec_base(), md)
    assert huella(project) == antes
    assert list(settings.backups_dir.rglob("manifest.json")) == []


# ================================================================ preview ====
def test_el_preview_refleja_el_compilado(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md)
    html = page_spec.preview(active, c)
    for titulo in ("Importe", "Ratio", "Por ano"):
        assert titulo in html
    assert "TotalAmount" in html, "los campos deben verse en la maqueta"
    assert str(c["canvas"]["width"]) in html


def test_el_preview_no_escribe_al_proyecto(proyecto):
    active, md, project, _s = proyecto
    antes = huella(project)
    page_spec.preview(active, page_spec.compile_spec(active, spec_base(), md))
    assert huella(project) == antes


# =================================================================== diff ====
def test_diff_de_pagina_nueva(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md)
    d = page_spec.diff_against_page(active, c)
    assert d["change"] == "create" and d["page_exists"] is False
    assert len(d["added"]) == 3


def test_diff_tras_aplicar_no_ve_cambios(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    d = page_spec.diff_against_page(active, c, res["page_id"])
    assert d["change"] == "update" and d["added"] == [] and d["removed"] == []
    assert d["unchanged"] == 3


def test_diff_detecta_visuales_que_sobran(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    reducido = spec_base()
    reducido["visuals"] = reducido["visuals"][:1]
    c2 = page_spec.compile_spec(active, reducido, md, seed="x")
    d = page_spec.diff_against_page(active, c2, res["page_id"])
    assert len(d["removed"]) == 2


# ================================================================== apply ====
def test_apply_crea_la_pagina_en_una_transaccion(proyecto):
    active, md, project, settings = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    assert len(res["visuals_created"]) == 3
    assert "Resumen" in {p.get("display_name") for p in pbir_reader.list_pages(active)}
    journals = list(settings.backups_dir.rglob("manifest.json"))
    assert len(journals) == 1, "una sola transaccion para la pagina completa"
    manifest = json.loads(journals[0].read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 5, "page.json + pages.json + 3 visuales"


def test_apply_respeta_la_politica_estricta(proyecto, monkeypatch):
    active, md, project, settings = proyecto
    c = page_spec.compile_spec(active, spec_base(), md)
    antes = huella(project)
    monkeypatch.setattr(project_state, "detect",
                        lambda a, **k: project_state.ProjectOpenState(
                            project_state.OPEN, "high", "abierto"))
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        page_spec.apply_spec(active, c)
    assert huella(project) == antes
    assert list(settings.backups_dir.rglob("manifest.json")) == []


@pytest.mark.parametrize("indice", [0, 1, 2])
def test_fallo_al_escribir_un_visual_revierte_la_pagina(proyecto, monkeypatch, indice):
    active, md, project, _s = proyecto
    antes = huella(project)
    original = txn_service.durable_write
    c = {"n": 0}

    def fake(path, data, validator=None):
        if Path(path).name == "visual.json":
            if c["n"] == indice:
                c["n"] += 1
                raise OSError("fallo inyectado")
            c["n"] += 1
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)
    compilado = page_spec.compile_spec(active, spec_base(), md)
    with pytest.raises(Exception):
        page_spec.apply_spec(active, compilado)

    assert huella(project) == antes, "restauracion byte a byte"
    assert "Resumen" not in {p.get("display_name")
                             for p in pbir_reader.list_pages(active)}
    assert list(project.rglob("*.tmp")) == []


# =========================================================== verificacion ====
def test_verificar_pagina_generada(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    v = page_spec.validate_generated_page(active, res["page_id"], md)
    assert v["valid"] is True and v["visual_count"] == 3
    assert v["broken_references"] == []
    assert v["visuals_without_title"] == []


def test_la_verificacion_detecta_referencias_rotas(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    md["measures"] = [m for m in md["measures"] if m["name"] != "TotalAmount"]
    v = page_spec.validate_generated_page(active, res["page_id"], md)
    assert v["valid"] is False and v["broken_references"]


def test_la_verificacion_no_oculta_un_visual_ilegible(proyecto):
    active, md, _p, _s = proyecto
    c = page_spec.compile_spec(active, spec_base(), md, seed="x")
    res = page_spec.apply_spec(active, c)
    page_dir = pbir_reader.resolve_page_dir(active, res["page_id"])
    corrupto = page_dir / "visuals" / "visual_corrupto" / "visual.json"
    corrupto.parent.mkdir()
    corrupto.write_text("{no es json", encoding="utf-8")

    with pytest.raises(ValidationError) as exc:
        page_spec.validate_generated_page(active, res["page_id"], md)

    assert exc.value.details["unreadable_visuals"][0]["visual_id"] == \
        "visual_corrupto"


# ================================================================ presets ====
def test_todos_los_presets_estan_definidos():
    presets = page_spec.list_presets()
    assert {p["preset"] for p in presets} == {
        "executive", "financial", "sales", "operations", "evm", "detail"}
    for p in presets:
        assert p["description"] and p["blocks"]
        assert p["layout"] in ("grid", "dashboard", "executive_summary")


@pytest.mark.parametrize("preset", ["executive", "financial", "sales",
                                    "operations", "evm", "detail"])
def test_cada_preset_produce_una_pagina_valida(proyecto, preset):
    """Fixture -> spec -> compilado -> apply -> verificacion, por preset."""
    active, md, _p, _s = proyecto
    definicion = page_spec.PRESETS[preset]
    medidas = ["TotalAmount", "Ratio Pct"]
    visuals = []
    for bloque in definicion["blocks"]:
        for i in range(bloque["count"]):
            if bloque["role"] == "kpi" and i < len(medidas):
                visuals.append({"type": bloque["type"], "title": medidas[i],
                                "fields": {"values": [f"[{medidas[i]}]"]}})
            elif bloque["role"] != "kpi" and i == 0:
                visuals.append({"type": bloque["type"], "title": f"{preset} {bloque['role']}",
                                "fields": {"category": "Calendar[Year]",
                                           "values": ["[TotalAmount]"]}})
    s = {"schema_version": "1.0",
         "page": {"name": f"P {preset}", "width": 1280, "height": 720},
         "layout": {"preset": preset, "gap": 16},
         "visuals": visuals, "filters": [], "interactions": []}

    assert page_spec.validate_schema(s) == []
    c = page_spec.compile_spec(active, s, md, seed=preset)
    assert page_spec.preview(active, c), "el preset debe producir preview"
    res = page_spec.apply_spec(active, c)
    v = page_spec.validate_generated_page(active, res["page_id"], md)
    assert v["valid"] is True, f"el preset '{preset}' genero una pagina invalida"
    assert v["visual_count"] == len(visuals)
