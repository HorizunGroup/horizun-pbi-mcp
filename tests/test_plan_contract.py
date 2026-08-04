"""Fase A — contrato unico y versionado de los planes.

El defecto que originó este modulo: `pbi_apply_page_spec(dry_run=True)` creaba
un plan sin `affected_files` y con una huella de ARGUMENTOS metida en el campo
de la huella de ESTADO. `pbi_apply_plan` moria con `KeyError: 'files'`, y de no
morir habria rechazado el plan siempre por huella distinta.

`test_page_spec_dry_run_luego_apply` falla contra el codigo anterior con ese
KeyError. El resto cubre cada forma de invalidar un plan.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator, tmdl_reader
from horizun_pbi_mcp.services import operations, plan_contract, planning
from tests.fixtures import synthetic


def spec_base(nombre="PlanTest"):
    return {
        "schema_version": "1.0",
        "page": {"name": nombre, "width": 1280, "height": 720},
        "layout": {"preset": "executive", "gap": 16},
        "visuals": [
            {"type": "card", "title": "Importe", "fields": {"values": ["[TotalAmount]"]}},
            {"type": "columnChart", "title": "Por ano",
             "fields": {"category": "Calendar[Year]", "values": ["[TotalAmount]"]}},
        ],
        "filters": [], "interactions": [],
    }


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    operations.registro().limpiar()
    return session, session.require_active_pbip(), pbip.parent


@pytest.fixture
def args_spec(session):
    """Argumentos del planificador de page spec, con el modelo ya leido."""
    def _hacer(spec):
        active = session.require_active_pbip()
        return {"spec": spec, "seed": "s1",
                "_model_data": tmdl_reader.read_semantic_model(active)}
    return _hacer


# ================================================== el defecto que se corrige ==
def test_page_spec_dry_run_luego_apply(proyecto, args_spec):
    """dry_run -> plan_token -> apply. Antes moria con KeyError: 'files'."""
    session, active, raiz = proyecto
    antes = huella(raiz)

    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    assert plan["plan_token"]
    assert plan["plan_version"] == plan_contract.PLAN_VERSION
    assert plan["changes"] > 0, "el plan deberia escribir page.json, pages.json y visuales"
    assert huella(raiz) == antes, "un dry_run no puede tocar el disco"

    res = planning.apply(session, plan["plan_token"])
    assert res["status"] == "applied"
    assert res["applied"] == plan["changes"]

    paginas = [p["display_name"] for p in pbir_reader.list_pages(active)]
    assert "PlanTest" in paginas
    assert len(pbir_reader.list_visuals(active, "PlanTest")) == 2


def test_el_plan_describe_exactamente_los_bytes_que_se_escriben(proyecto, args_spec):
    """El sobre no guarda el spec para recompilarlo: guarda el contenido final."""
    session, active, raiz = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    sobre = operations.registro().plan_por_token(plan["plan_token"])

    planeado = {e["path"]: plan_contract.contenido_como_texto(e)
                for e in sobre["affected_files"]}
    assert planeado, "el sobre debe traer affected_files"

    planning.apply(session, plan["plan_token"])
    for ruta, texto in planeado.items():
        assert Path(ruta).read_text(encoding="utf-8-sig") == texto, (
            f"lo escrito en {ruta} no es lo que el plan prometia")


# ============================================================ invalidaciones ==
def test_token_inexistente(proyecto):
    session, _, _ = proyecto
    with pytest.raises(operations.PlanNotFoundError):
        planning.apply(session, "plan_no_existe")


def test_token_vencido(proyecto, args_spec, monkeypatch):
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    sobre = operations.registro().plan_por_token(plan["plan_token"])
    sobre["expires_at"] = "2000-01-01T00:00:00+00:00"

    with pytest.raises(plan_contract.PlanExpiredError) as exc:
        planning.apply(session, plan["plan_token"])
    assert exc.value.code == "plan_expired"


def test_token_ya_consumido(proyecto, args_spec):
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    planning.apply(session, plan["plan_token"])

    with pytest.raises(operations.PlanNotFoundError):
        planning.apply(session, plan["plan_token"])


def test_fingerprint_obsoleto(proyecto, args_spec):
    """Si el proyecto cambia entre planificar y aplicar, el plan se rechaza."""
    session, active, raiz = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))

    sobre = operations.registro().plan_por_token(plan["plan_token"])
    destino = Path(sobre["affected_files"][0]["path"])
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text('{"intruso": true}', encoding="utf-8")

    with pytest.raises(operations.PlanTokenStaleError) as exc:
        planning.apply(session, plan["plan_token"])
    assert exc.value.code == "plan_token_stale"


def test_proyecto_distinto(proyecto, args_spec, tmp_path):
    """Un plan de un .pbip no puede aplicarse sobre otro."""
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))

    otro = synthetic.materialize(tmp_path / "otro")
    project_locator.open_project(session, str(otro))

    with pytest.raises(plan_contract.PlanProjectMismatchError) as exc:
        planning.apply(session, plan["plan_token"])
    assert exc.value.code == "plan_project_mismatch"


def test_payload_manipulado(proyecto, args_spec):
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    sobre = operations.registro().plan_por_token(plan["plan_token"])
    sobre["payload"]["spec"]["page"]["name"] = "OtraPagina"

    with pytest.raises(plan_contract.PlanPayloadTamperedError) as exc:
        planning.apply(session, plan["plan_token"])
    assert exc.value.code == "plan_payload_tampered"


def test_plan_de_una_operacion_enviado_a_otra(proyecto, args_spec):
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))

    with pytest.raises(plan_contract.PlanOperationMismatchError) as exc:
        planning.apply(session, plan["plan_token"],
                       expected_operation="hide_columns")
    assert exc.value.code == "plan_operation_mismatch"


def test_version_de_plan_no_soportada(proyecto, args_spec):
    """A3: un sobre viejo no se acepta 'como si tal cosa'."""
    session, _, _ = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    sobre = operations.registro().plan_por_token(plan["plan_token"])
    sobre["plan_version"] = 0

    with pytest.raises(plan_contract.PlanVersionUnsupportedError) as exc:
        planning.apply(session, plan["plan_token"])
    assert exc.value.code == "plan_version_unsupported"
    assert exc.value.details["supported"] == plan_contract.PLAN_VERSION


def test_sobre_sin_affected_files_se_rechaza():
    """El sobre legado (spec + seed, sin archivos) ya no cuela por el contrato."""
    legado = {"operation": "apply_page_spec", "spec": {}, "seed": ""}
    with pytest.raises(plan_contract.PlanVersionUnsupportedError):
        plan_contract.validate_envelope(legado)

    con_version = dict(legado, plan_version=plan_contract.PLAN_VERSION)
    with pytest.raises(plan_contract.PlanContractError) as exc:
        plan_contract.validate_envelope(con_version)
    assert "affected_files" in str(exc.value)


# ==================================================== reintento y rollback ====
def test_reintento_despues_de_exito(proyecto, args_spec):
    """Aplicar dos veces no duplica la pagina: el token ya no existe."""
    session, active, raiz = proyecto
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))
    planning.apply(session, plan["plan_token"])

    tras_aplicar = huella(raiz)
    with pytest.raises(operations.PlanNotFoundError):
        planning.apply(session, plan["plan_token"])
    assert huella(raiz) == tras_aplicar, "el reintento no puede escribir nada"

    paginas = [p["display_name"] for p in pbir_reader.list_pages(active)]
    assert paginas.count("PlanTest") == 1


def test_fallo_durante_apply_revierte_todo(proyecto, args_spec, monkeypatch):
    """Si una escritura falla a mitad, no queda ni un archivo del plan."""
    session, active, raiz = proyecto
    antes = huella(raiz)
    plan = planning.plan(session, "apply_page_spec", args_spec(spec_base()))

    from horizun_pbi_mcp.services import txn as txn_service

    original = txn_service.Transaction.write_json
    estado = {"n": 0}

    def falla_en_la_tercera(self, target, data):
        estado["n"] += 1
        if estado["n"] == 3:
            raise OSError("fallo inyectado en la tercera escritura")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", falla_en_la_tercera)

    with pytest.raises(Exception):
        planning.apply(session, plan["plan_token"])

    assert huella(raiz) == antes, (
        "tras el rollback el proyecto debe quedar byte a byte como estaba")
    assert "PlanTest" not in [p["display_name"] for p in pbir_reader.list_pages(active)]
