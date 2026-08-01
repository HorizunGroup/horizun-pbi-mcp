"""Lo que el spec acepta pero el escritor no sabe materializar se RECHAZA.

Origen: el esquema aceptaba `filters` e `interactions` (solo comprobaba que
fueran listas) y nadie los serializaba. Un spec con filtros producia una pagina
sin filtros y sin decirlo, y el usuario solo lo descubria abriendo el informe.

Hoy ambos SI se serializan (ver `test_filtros_e_interacciones.py`), asi que
`NO_SOPORTADO_AUN` esta vacio. Lo que se prueba aqui es el MECANISMO: mientras
quede algo sin implementar, tiene que fallar pronto y decir que propiedad es.
El mecanismo se ejercita inyectando una propiedad ficticia, no dependiendo de
que siga habiendo huecos.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator, tmdl_reader
from services import page_spec
from services.page_spec import UnsupportedSpecFeature
from tests.fixtures import synthetic


@pytest.fixture
def gate(monkeypatch):
    """Reactiva el mecanismo con una propiedad que de verdad no se soporta.

    Asi la prueba mide el mecanismo y no la lista de huecos del dia, que
    cambia segun se van implementando.
    """
    monkeypatch.setitem(page_spec.NO_SOPORTADO_AUN, "filters",
                        "propiedad ficticia para ejercitar el rechazo")
    monkeypatch.setitem(page_spec.NO_SOPORTADO_AUN, "interactions",
                        "propiedad ficticia para ejercitar el rechazo")
    return page_spec.NO_SOPORTADO_AUN


def spec_base(**extra):
    spec = {
        "schema_version": "1.0",
        "page": {"name": "ConFiltros", "width": 1280, "height": 720},
        "layout": {"preset": "executive", "gap": 16},
        "visuals": [
            {"type": "card", "title": "Importe", "fields": {"values": ["[TotalAmount]"]}},
        ],
        "filters": [], "interactions": [],
    }
    spec.update(extra)
    return spec


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


FILTRO = {"field": "Calendar[Year]", "operator": "in", "values": [2024]}
INTERACCION = {"source": "v1", "target": "v2", "type": "none"}


# ============================================== se rechaza, no se ignora ======
@pytest.mark.parametrize("clave,valor", [
    ("filters", [FILTRO]),
    ("interactions", [INTERACCION]),
])
def test_no_soportado_se_rechaza(clave, valor, gate):
    with pytest.raises(UnsupportedSpecFeature) as exc:
        page_spec.assert_soportado(spec_base(**{clave: valor}))
    assert exc.value.code == "unsupported_feature"
    assert clave in exc.value.details["properties"]


def test_el_error_identifica_la_propiedad_exacta(gate):
    spec = spec_base(filters=[FILTRO, FILTRO], interactions=[INTERACCION])
    with pytest.raises(UnsupportedSpecFeature) as exc:
        page_spec.assert_soportado(spec)

    rutas = [x["path"] for x in exc.value.details["unsupported"]]
    assert rutas == ["$.filters[0]", "$.filters[1]", "$.interactions[0]"]
    for entrada in exc.value.details["unsupported"]:
        assert entrada["reason"], "cada entrada debe decir POR QUE no se puede"


def test_listas_vacias_siguen_valiendo():
    """No romper el caso normal: sin filtros, no hay nada que rechazar."""
    page_spec.assert_soportado(spec_base())


def test_falla_antes_del_dry_run(proyecto, gate):
    """Se rechaza al compilar, asi que preview, diff, dry_run y apply fallan igual."""
    active, md, raiz = proyecto
    antes = huella(raiz)

    with pytest.raises(UnsupportedSpecFeature):
        page_spec.compile_spec(active, spec_base(filters=[FILTRO]), md)

    assert huella(raiz) == antes, "no puede escribir nada"


def test_un_spec_sin_filtros_sigue_creando_la_pagina(proyecto):
    """La comprobacion no puede bloquear el camino bueno."""
    active, md, raiz = proyecto
    compilado = page_spec.compile_spec(active, spec_base(), md)
    assert compilado["page_name"] == "ConFiltros"
    assert len(compilado["visuals"]) == 1


def test_la_tool_de_validacion_lo_reporta_como_etapa(proyecto, monkeypatch, gate):
    """pbi_validate_page_spec no puede decir 'valido' y luego fallar al aplicar."""
    import config as cfg
    from server import build_server
    import asyncio

    active, _md, _raiz = proyecto
    monkeypatch.setattr(cfg, "_session", cfg.get_session())
    mcp = build_server()

    salida = asyncio.run(mcp.call_tool(
        "pbi_validate_page_spec", {"spec": spec_base(filters=[FILTRO])}))
    payload = salida[1] if isinstance(salida, tuple) else salida
    if isinstance(payload, dict) and "result" in payload and "ok" not in payload:
        payload = payload["result"]

    assert payload["valid"] is False
    assert payload["stage"] == "unsupported_feature"
    assert payload["error"] == "unsupported_feature"
