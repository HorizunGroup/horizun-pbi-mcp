"""Fase E1/H6 — el destino de replace_visual_field se valida contra el modelo.

Antes escribia CUALQUIER referencia: una errata dejaba el visual apuntando a un
campo inexistente, que Power BI no dibuja y que solo se descubre al abrir el
informe. Y conservaba el tipo de nodo del campo viejo, asi que una medida podia
acabar dentro de un nodo `Column` solo porque el nombre encajaba.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator, tmdl_reader
from horizun_pbi_mcp.services import pbir_edit
from horizun_pbi_mcp.services.pbir_edit import FieldNotFoundError
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
    return active, tmdl_reader.read_semantic_model(active), pbip.parent


def _un_visual(active):
    """Primera pagina y primer visual con proyecciones."""
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["display_name"]):
            if v.get("measures") or v.get("columns"):
                return p["display_name"], v
    pytest.skip("el fixture no tiene ningun visual con campos")


# ================================================= E1: el campo debe existir ==
def test_campo_inexistente_se_rechaza(proyecto):
    active, md, raiz = proyecto
    page, visual = _un_visual(active)
    viejo = (visual.get("measures") or visual.get("columns"))[0]
    antes = huella(raiz)

    with pytest.raises(FieldNotFoundError) as exc:
        pbir_edit.replace_visual_field(active, page, visual["id"], viejo,
                                       "[NoExisteEstaMedida]", md)
    assert exc.value.code == "field_not_found"
    assert huella(raiz) == antes, "no puede escribir nada al rechazar"


def test_tabla_inexistente_se_rechaza_nombrandola(proyecto):
    active, md, raiz = proyecto
    page, visual = _un_visual(active)
    viejo = (visual.get("measures") or visual.get("columns"))[0]

    with pytest.raises(FieldNotFoundError) as exc:
        pbir_edit.replace_visual_field(active, page, visual["id"], viejo,
                                       "TablaFantasma[Campo]", md)
    assert exc.value.details["table"] == "TablaFantasma"
    assert "available_tables" in exc.value.details


def test_sin_modelo_no_se_inventa_validacion(proyecto):
    """Sin model_data no se puede validar; se dice, no se finge."""
    active, _md, raiz = proyecto
    page, visual = _un_visual(active)
    viejo = (visual.get("measures") or visual.get("columns"))[0]

    # Sin modelo la validacion se omite: el comportamiento anterior se conserva
    # para no romper a quien ya llamaba sin el.
    assert pbir_edit._validar_destino("[LoQueSea]", None) is None   # noqa: SLF001


# ============================================ H6: columna no es lo mismo que medida ==
def test_no_se_pone_una_medida_donde_va_una_columna(proyecto):
    active, md, raiz = proyecto
    indice_col = None
    for p in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, p["display_name"]):
            if v.get("columns"):
                indice_col = (p["display_name"], v, v["columns"][0])
                break
        if indice_col:
            break
    if not indice_col:
        pytest.skip("el fixture no tiene ningun visual con columnas")

    page, visual, columna = indice_col
    medida = next(iter(md.get("measures") or []), None)
    if not medida:
        pytest.skip("el modelo sintetico no tiene medidas")

    antes = huella(raiz)
    with pytest.raises(FieldNotFoundError) as exc:
        pbir_edit.replace_visual_field(active, page, visual["id"], columna,
                                       f"[{medida['name']}]", md)
    assert exc.value.details["field_kind"] == "measure"
    assert exc.value.details["node_kind"] == "Column"
    assert huella(raiz) == antes


def test_una_sustitucion_valida_sigue_funcionando(proyecto):
    """La validacion no puede romper el caso bueno."""
    active, md, raiz = proyecto
    page, visual = _un_visual(active)

    medidas = [m["name"] for m in md.get("measures") or []]
    if len(medidas) < 2 or not visual.get("measures"):
        pytest.skip("hacen falta dos medidas y un visual que use una")

    viejo = visual["measures"][0]          # ya viene cualificada tal cual
    nombre_viejo = viejo.split("[")[-1].rstrip("]")
    nueva = next((m for m in medidas if m != nombre_viejo), None)
    if nueva is None:
        pytest.skip("no hay una segunda medida distinta")

    r = pbir_edit.replace_visual_field(active, page, visual["id"],
                                       viejo, f"[{nueva}]", md)
    assert r["count"] >= 1
    refrescado = [v for v in pbir_reader.list_visuals(active, page)
                  if v["id"] == visual["id"]][0]
    assert any(nueva in m for m in refrescado.get("measures", []))
