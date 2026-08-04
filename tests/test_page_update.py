"""Fases C2-C4 — actualizar una pagina existente, no crear otra.

`diff_against_page()` sabia decir `change="update"`, pero `apply_spec()`
llamaba siempre a `create_page_with_visuals()`, que ante una pagina con el
mismo nombre devolvia `created: False` y no tocaba nada. Aplicar un spec sobre
una pagina existente NO HACIA NADA y decia que todo habia ido bien.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator, tmdl_reader
from horizun_pbi_mcp.services import operations, page_spec, page_update, planning
from horizun_pbi_mcp.services.page_update import PageConflict
from tests.fixtures import synthetic


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    operations.registro().limpiar()
    return session, active, tmdl_reader.read_semantic_model(active), pbip.parent


def spec(nombre="Nueva", visuales=None):
    return {
        "schema_version": "1.0",
        "page": {"name": nombre, "width": 1280, "height": 720},
        "layout": {"preset": "executive", "gap": 16},
        "visuals": visuales if visuales is not None else [
            {"type": "card", "title": "Importe",
             "fields": {"values": ["[TotalAmount]"]}}],
        "filters": [], "interactions": [],
    }


def compilar(active, md, s, seed="s1"):
    return page_spec.compile_spec(active, s, md, seed=seed)


def pagina_existente(active):
    return pbir_reader.list_pages(active)[0]


# =================================================== los cuatro desenlaces ====
def test_create_cuando_no_existe(proyecto):
    _s, active, md, _r = proyecto
    plan = page_update.planificar(active, compilar(active, md, spec("NoExiste")))
    assert plan["change"] == page_update.CREATE


def test_no_change_cuando_ya_coincide(proyecto):
    _s, active, md, raiz = proyecto
    s = spec("Igualita")
    page_spec.apply_spec(active, compilar(active, md, s))
    antes = huella(raiz)

    r = page_spec.apply_spec(active, compilar(active, md, s))
    assert r["change"] == page_update.NO_CHANGE
    assert r["applied"] == 0
    assert huella(raiz) == antes, "no_change no puede escribir"


def test_update_conserva_el_id_de_la_pagina(proyecto):
    """REGRESION: antes esto no hacia nada y decia que si."""
    _s, active, md, _r = proyecto
    existente = pagina_existente(active)
    id_antes = existente["name"]

    s = spec(existente["display_name"], visuales=[
        {"type": "card", "title": "Nuevo KPI",
         "fields": {"values": ["[TotalAmount]"]}}])
    r = page_spec.apply_spec(active, compilar(active, md, s))

    assert r["change"] == page_update.UPDATE
    assert r["page_id"] == id_antes, "el id de la pagina NO puede cambiar"
    paginas = [p["name"] for p in pbir_reader.list_pages(active)]
    assert paginas.count(id_antes) == 1, "se duplico la pagina"


def test_conflicto_si_el_nombre_no_es_univoco(proyecto):
    _s, active, md, _r = proyecto
    from horizun_pbi_mcp.services import pbir_edit

    origen = pagina_existente(active)["display_name"]
    pbir_edit.duplicate_page(active, origen, "Duplicada")

    copia = [p for p in pbir_reader.list_pages(active)
             if p["display_name"] == "Duplicada"][0]
    ruta = Path(pbir_reader.pages_dir(active)) / copia["name"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["displayName"] = origen
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8", newline="\r\n")

    with pytest.raises(PageConflict) as exc:
        page_update.resolver_pagina(active, origen)
    assert len(exc.value.details["candidates"]) == 2


# ============================================ que se conserva y que no ========
def test_conserva_el_id_de_un_visual_que_no_cambia(proyecto):
    _s, active, md, _r = proyecto
    existente = pagina_existente(active)
    visuales = pbir_reader.list_visuals(active, existente["name"])
    v0 = visuales[0]

    s = spec(existente["display_name"], visuales=[
        {"type": v0["type"], "title": v0.get("title"),
         "fields": {"values": list(v0.get("measures") or [])[:1]}},
        {"type": "card", "title": "Anadido",
         "fields": {"values": ["[TotalAmount]"]}}])
    plan = page_update.planificar(active, compilar(active, md, s))

    conservados = set(plan["kept"]) | set(plan["updated"])
    assert v0["id"] in conservados, (
        "el visual equivalente debe conservar su id, no regenerarse")


def test_merge_no_borra_lo_que_el_spec_no_menciona(proyecto):
    _s, active, md, _r = proyecto
    existente = pagina_existente(active)
    n_antes = len(pbir_reader.list_visuals(active, existente["name"]))

    s = spec(existente["display_name"], visuales=[
        {"type": "card", "title": "Solo este",
         "fields": {"values": ["[TotalAmount]"]}}])
    r = page_spec.apply_spec(active, compilar(active, md, s),
                             sync_mode=page_update.MERGE)

    assert r["removed"] == []
    assert r["not_removed"], "merge debe decir QUE conservo"
    n_despues = len(pbir_reader.list_visuals(active, existente["name"]))
    assert n_despues >= n_antes, "merge no puede reducir el numero de visuales"


def test_replace_si_borra_los_ausentes(proyecto):
    _s, active, md, _r = proyecto
    existente = pagina_existente(active)

    s = spec(existente["display_name"], visuales=[
        {"type": "card", "title": "El unico",
         "fields": {"values": ["[TotalAmount]"]}}])
    r = page_spec.apply_spec(active, compilar(active, md, s),
                             sync_mode=page_update.REPLACE)

    assert r["removed"], "replace debe eliminar los ausentes del spec"
    assert len(pbir_reader.list_visuals(active, existente["name"])) == 1


def test_sync_mode_invalido_se_rechaza(proyecto):
    _s, active, md, _r = proyecto
    with pytest.raises(PageConflict):
        page_update.planificar(active, compilar(active, md, spec()),
                               sync_mode="borrar_todo")


# ================================================ plan, replay y rollback =====
def test_dry_run_no_escribe_y_luego_aplica(proyecto):
    session, active, md, raiz = proyecto
    existente = pagina_existente(active)
    antes = huella(raiz)

    args = {"spec": spec(existente["display_name"], visuales=[
        {"type": "card", "title": "Por plan",
         "fields": {"values": ["[TotalAmount]"]}}]),
        "seed": "s1", "sync_mode": "merge", "_model_data": md}
    plan = planning.plan(session, "apply_page_spec", args)

    assert plan["meta"]["change"] == page_update.UPDATE
    assert huella(raiz) == antes, "un dry_run no puede tocar el disco"

    r = planning.apply(session, plan["plan_token"])
    assert r["status"] == "applied"


def test_token_obsoleto_si_cambia_la_pagina(proyecto):
    session, active, md, _r = proyecto
    existente = pagina_existente(active)
    args = {"spec": spec(existente["display_name"], visuales=[
        {"type": "card", "title": "X",
         "fields": {"values": ["[TotalAmount]"]}}]),
        "seed": "s1", "sync_mode": "merge", "_model_data": md}
    plan = planning.plan(session, "apply_page_spec", args)

    # El plan solo escribe archivos NUEVOS, asi que el cambio externo consiste
    # en que alguien cree uno de ellos: pasar de ausente a presente invalida la
    # huella igual que modificar uno existente.
    sobre = operations.registro().plan_por_token(plan["plan_token"])
    destino = Path(sobre["affected_files"][0]["path"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text('{"intruso": true}', encoding="utf-8")

    with pytest.raises(operations.PlanTokenStaleError):
        planning.apply(session, plan["plan_token"])


def test_replay_no_duplica(proyecto):
    _s, active, md, raiz = proyecto
    existente = pagina_existente(active)
    s = spec(existente["display_name"], visuales=[
        {"type": "card", "title": "Replay",
         "fields": {"values": ["[TotalAmount]"]}}])

    page_spec.apply_spec(active, compilar(active, md, s))
    tras_uno = huella(raiz)
    n1 = len(pbir_reader.list_visuals(active, existente["name"]))

    r = page_spec.apply_spec(active, compilar(active, md, s))
    assert r["change"] == page_update.NO_CHANGE
    assert huella(raiz) == tras_uno
    assert len(pbir_reader.list_visuals(active, existente["name"])) == n1


def test_fallo_intermedio_revierte_todo(proyecto, monkeypatch):
    _s, active, md, raiz = proyecto
    existente = pagina_existente(active)
    antes = huella(raiz)

    from horizun_pbi_mcp.services import txn as txn_service

    original = txn_service.Transaction.write_json
    estado = {"n": 0}

    def falla(self, target, data):
        estado["n"] += 1
        if estado["n"] == 2:
            raise OSError("fallo inyectado")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", falla)
    s = spec(existente["display_name"], visuales=[
        {"type": "card", "title": "A", "fields": {"values": ["[TotalAmount]"]}},
        {"type": "card", "title": "B", "fields": {"values": ["[TotalAmount]"]}},
        {"type": "card", "title": "C", "fields": {"values": ["[TotalAmount]"]}}])

    with pytest.raises(Exception):
        page_spec.apply_spec(active, compilar(active, md, s))
    assert huella(raiz) == antes, "el rollback debe dejarlo byte a byte igual"


def test_los_filtros_de_pagina_llegan_al_page_json(proyecto):
    """Antes se rechazaban por no saber serializarlos; ahora se escriben."""
    _s, active, md, _r = proyecto
    s = spec()
    s["filters"] = [{"field": "Calendar[Year]", "values": [2024]}]
    compilado = compilar(active, md, s)

    config = compilado["page_filter_config"]
    filtro = config["filters"][0]
    assert filtro["type"] == "Categorical"
    assert filtro["field"]["Column"]["Property"] == "Year"
    # dentro de la consulta del filtro la tabla va por ALIAS, no por nombre
    consulta = filtro["filter"]
    assert consulta["From"][0]["Entity"] == "Calendar"
    origen = (consulta["Where"][0]["Condition"]["In"]["Expressions"][0]
              ["Column"]["Expression"]["SourceRef"])
    assert "Source" in origen and "Entity" not in origen
