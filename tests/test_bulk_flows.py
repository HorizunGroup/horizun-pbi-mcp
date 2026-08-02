"""Fase 1A.1 — los flujos PBIR por lotes son transacciones logicas completas.

Cubre los cuatro flujos multiarchivo:
  - pbi_create_page_from_spec   (page.json + pages.json + N visual.json)
  - pbi_arrange_visuals         (N visual.json)
  - pbi_generate_report_page    (page.json + pages.json + N visual.json)
  - pbi_create_html_visual      (report.json + visual.json)

Cada uno debe producir UN solo journal, verificarse por completo y revertir el
conjunto entero ante cualquier fallo, sin pisar cambios externos.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import page_builder, pbir_reader, pbir_writer, project_locator, visual_factory
from powerbi.errors import ValidationError
from services import project_state
from services import txn as txn_service
from services.txn import RollbackIncompleteError
from tests.fixtures import synthetic


# --------------------------------------------------------------- utilidades ---
def huella_proyecto(project: Path) -> dict:
    """sha256 de cada archivo del proyecto: permite comparar byte a byte."""
    out = {}
    for p in sorted(project.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(project)).replace("\\", "/")] = \
                hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def journals(backups: Path) -> list:
    return sorted(backups.rglob("manifest.json"))


def temporales(project: Path) -> list:
    return list(project.rglob("*.tmp"))


@pytest.fixture
def entorno(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, pbip.parent, isolated_settings.backups_dir


@pytest.fixture
def fallar_al_escribir(monkeypatch):
    """Inyecta un fallo en la escritura del N-esimo visual.json."""
    def _instalar(indice: int):
        original = txn_service.durable_write
        contador = {"n": 0}

        def fake(path, data, validator=None):
            p = Path(path)
            if p.name == "visual.json":
                if contador["n"] == indice:
                    contador["n"] += 1
                    raise OSError("fallo de escritura inyectado")
                contador["n"] += 1
            return original(path, data, validator)

        monkeypatch.setattr(txn_service, "durable_write", fake)
    return _instalar


SPEC = {
    "page_name": "Hoja Nueva",
    "canvas": {"width": 1280, "height": 720},
    "layout": "grid",
    "visuals": [
        {"type": "card", "title": "Uno", "fields": {"values": ["[TotalAmount]"]}},
        {"type": "card", "title": "Dos", "fields": {"values": ["[Ratio Pct]"]}},
        {"type": "columnChart", "title": "Tres",
         "fields": {"category": "Calendar[Year]", "values": ["[TotalAmount]"]}},
    ],
}
INDICE = {"TotalAmount": "Fact", "Ratio Pct": "Fact"}


# ============================================================ create_page_from_spec
def test_spec_exito_escribe_todo_en_una_transaccion(entorno):
    active, project, backups = entorno
    res = page_builder.create_page_from_spec(active, SPEC, INDICE)

    assert len(res["visuals_created"]) == 3
    paginas = {p["display_name"] for p in pbir_reader.list_pages(active)}
    assert "Hoja Nueva" in paginas
    visuales = pbir_reader.list_visuals(active, res["page_id"])
    assert len(visuales) == 3
    assert {v["title"] for v in visuales} == {"Uno", "Dos", "Tres"}
    # Las posiciones son las finales, no provisionales.
    assert all(v["position"]["width"] > 0 for v in visuales)

    assert len(journals(backups)) == 1, "una operacion logica -> un solo journal"
    manifest = json.loads(journals(backups)[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "committed"
    assert len(manifest["files"]) == 5, "page.json + pages.json + 3 visual.json"
    assert temporales(project) == []


def test_spec_con_visual_invalido_no_escribe_nada(entorno):
    """Fallo ANTES de escribir: ni pagina parcial ni journal innecesario."""
    active, project, backups = entorno
    antes = huella_proyecto(project)

    spec = json.loads(json.dumps(SPEC))
    spec["visuals"][1]["fields"] = {"values": ["Ventas[]"]}      # referencia invalida

    with pytest.raises(Exception):
        page_builder.create_page_from_spec(active, spec, INDICE)

    assert huella_proyecto(project) == antes, "proyecto identico byte a byte"
    assert "Hoja Nueva" not in {p["display_name"] for p in pbir_reader.list_pages(active)}
    assert journals(backups) == [], "no se abre journal si no se llega a escribir"
    assert temporales(project) == []


def test_spec_con_tipo_desconocido_no_escribe_nada(entorno):
    active, project, backups = entorno
    antes = huella_proyecto(project)
    spec = json.loads(json.dumps(SPEC))
    spec["visuals"][2]["type"] = "visualQueNoExiste"

    with pytest.raises(Exception):
        page_builder.create_page_from_spec(active, spec, INDICE)

    assert huella_proyecto(project) == antes
    assert journals(backups) == []


def test_spec_sin_type_se_rechaza_antes_de_escribir(entorno):
    active, project, backups = entorno
    antes = huella_proyecto(project)
    spec = json.loads(json.dumps(SPEC))
    del spec["visuals"][1]["type"]

    with pytest.raises(ValidationError):
        page_builder.create_page_from_spec(active, spec, INDICE)
    assert huella_proyecto(project) == antes
    assert journals(backups) == []


@pytest.mark.parametrize("indice,donde", [(0, "primero"), (1, "intermedio"), (2, "ultimo")])
def test_spec_fallo_durante_la_escritura_revierte_todo(entorno, fallar_al_escribir,
                                                       indice, donde):
    active, project, backups = entorno
    antes = huella_proyecto(project)
    fallar_al_escribir(indice)

    with pytest.raises(Exception):
        page_builder.create_page_from_spec(active, SPEC, INDICE)

    assert huella_proyecto(project) == antes, \
        f"estado inicial restaurado tras fallar el visual {donde}"
    assert "Hoja Nueva" not in {p["display_name"] for p in pbir_reader.list_pages(active)}
    assert temporales(project) == [], "cero temporales"
    manifest = json.loads(journals(backups)[0].read_text(encoding="utf-8"))
    assert manifest["status"] == "rolled_back"


# ================================================================ arrange_visuals
def _posiciones(n=2):
    return [{"visual_id": synthetic.CARD_TEMPLATE_ID, "x": 10, "y": 10,
             "width": 100, "height": 50},
            {"visual_id": synthetic.COLUMN_TEMPLATE_ID, "x": 200, "y": 10,
             "width": 300, "height": 200}][:n]


def test_arrange_exito_una_sola_transaccion(entorno):
    active, project, backups = entorno
    res = pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, _posiciones(),
                                          tool="pbi_arrange_visuals")
    assert res["moved"] == 2
    visuales = {v["id"]: v for v in pbir_reader.list_visuals(active, synthetic.PAGE_ID)}
    assert visuales[synthetic.CARD_TEMPLATE_ID]["position"]["x"] == 10
    assert visuales[synthetic.COLUMN_TEMPLATE_ID]["position"]["width"] == 300
    assert len(journals(backups)) == 1
    assert temporales(project) == []


def test_arrange_con_visual_inexistente_no_mueve_ninguno(entorno):
    """Se valida TODO antes de escribir: un id malo aborta el lote entero."""
    active, project, backups = entorno
    antes = huella_proyecto(project)
    posiciones = _posiciones() + [{"visual_id": "noexiste000000000000",
                                   "x": 0, "y": 0, "width": 10, "height": 10}]

    with pytest.raises(ValidationError):
        pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, posiciones,
                                        tool="pbi_arrange_visuals")

    assert huella_proyecto(project) == antes, "ninguna posicion aplicada"
    assert journals(backups) == []


@pytest.mark.parametrize("indice", [0, 1])
def test_arrange_fallo_al_escribir_revierte_el_lote(entorno, fallar_al_escribir, indice):
    active, project, backups = entorno
    antes = huella_proyecto(project)
    fallar_al_escribir(indice)

    with pytest.raises(Exception):
        pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, _posiciones(),
                                        tool="pbi_arrange_visuals")

    assert huella_proyecto(project) == antes, "cero posiciones parcialmente aplicadas"
    assert temporales(project) == []


def test_arrange_cambio_externo_en_archivo_ya_escrito_se_preserva(entorno, monkeypatch):
    """El cambio externo gana; la operacion reporta conflicto, no exito."""
    active, project, backups = entorno
    card = (project / "Demo.Report" / "definition" / "pages" / synthetic.PAGE_ID /
            "visuals" / synthetic.CARD_TEMPLATE_ID / "visual.json")

    original = txn_service.durable_write
    estado = {"n": 0}

    def fake(path, data, validator=None):
        p = Path(path)
        if p.name == "visual.json":
            r = original(path, data, validator)
            estado["n"] += 1
            if estado["n"] == 1:
                # Alguien de fuera modifica lo que acabamos de escribir, entre
                # la escritura y la verificacion posterior.
                card.write_text('{"EXTERNO":true}', encoding="utf-8")
            return r
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(RollbackIncompleteError) as exc:
        pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, _posiciones(),
                                        tool="pbi_arrange_visuals")

    assert json.loads(card.read_text(encoding="utf-8")) == {"EXTERNO": True}, \
        "no se pisa el cambio externo durante el rollback"
    assert exc.value.details["clean"] is False
    assert "rollback_conflict" in exc.value.details["by_outcome"]


def test_arrange_cambio_externo_antes_de_escribir_aborta(entorno, monkeypatch):
    active, project, backups = entorno
    columna = (project / "Demo.Report" / "definition" / "pages" / synthetic.PAGE_ID /
               "visuals" / synthetic.COLUMN_TEMPLATE_ID / "visual.json")
    card = (project / "Demo.Report" / "definition" / "pages" / synthetic.PAGE_ID /
            "visuals" / synthetic.CARD_TEMPLATE_ID / "visual.json")
    original_card = card.read_bytes()

    original = txn_service.durable_write
    hecho = {"v": False}

    def fake(path, data, validator=None):
        p = Path(path)
        if p.name == "visual.json" and not hecho["v"]:
            hecho["v"] = True
            # Cambio externo en un archivo AUN NO escrito de este lote.
            columna.write_text('{"EXTERNO":true}', encoding="utf-8")
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(Exception):
        pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, _posiciones(),
                                        tool="pbi_arrange_visuals")

    assert json.loads(columna.read_text(encoding="utf-8")) == {"EXTERNO": True}
    assert card.read_bytes() == original_card, "el ya escrito vuelve a su estado"


# ============================================================ generate_report_page
def test_generate_construye_con_posicion_final(entorno):
    """No se escribe con posicion provisional para reposicionar despues."""
    active, project, backups = entorno
    planificados = [{"visual": visual_factory.build_visual(
        active, "card", {"values": ["[TotalAmount]"]},
        {"x": 5, "y": 6, "width": 200, "height": 120}, "T", INDICE)["visual"],
        "meta": {"type": "card"}}]

    res = pbir_writer.create_page_with_visuals(
        active, "Generada", 1280, 720, planificados, tool="pbi_generate_report_page")

    visuales = pbir_reader.list_visuals(active, res["page_id"])
    assert visuales[0]["position"]["x"] == 5 and visuales[0]["position"]["y"] == 6
    assert len(journals(backups)) == 1, "una sola transaccion, sin reposicionar despues"
    manifest = json.loads(journals(backups)[0].read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 3


def test_generate_sin_visuales_mantiene_compatibilidad(entorno):
    """Compatibilidad observable: se sigue creando la pagina vacia con page_id."""
    active, project, backups = entorno
    res = pbir_writer.create_page_with_visuals(
        active, "Vacia", 1280, 720, [], tool="pbi_generate_report_page")
    assert res["page_id"] and res["created"] is True
    assert "Vacia" in {p["display_name"] for p in pbir_reader.list_pages(active)}
    assert len(journals(backups)) == 1


def test_pagina_existente_no_se_duplica_ni_abre_journal(entorno):
    active, project, backups = entorno
    res = pbir_writer.create_page_with_visuals(
        active, synthetic.PAGE_DISPLAY_NAME, 1280, 720, [], tool="t")
    assert res["created"] is False
    assert res["page_id"] == synthetic.PAGE_ID
    assert journals(backups) == []


# ============================================================== html visual ----
def test_html_visual_registra_y_escribe_en_una_transaccion(entorno):
    active, project, backups = entorno
    built = visual_factory.build_visual(
        active, "htmlContent", {"values": ["[TotalAmount]"]},
        {"x": 0, "y": 0, "width": 400, "height": 300}, "Panel", INDICE)

    res = pbir_writer.write_visual_with_registration(
        active, synthetic.PAGE_ID, pbir_writer.HTML_CONTENT_GUID,
        built["visual"], tool="pbi_create_html_visual")

    report = json.loads((project / "Demo.Report" / "definition" / "report.json")
                        .read_text(encoding="utf-8"))
    assert pbir_writer.HTML_CONTENT_GUID in report["publicCustomVisuals"]
    assert Path(res["file"]).exists()
    assert len(journals(backups)) == 1, "report.json + visual.json en un journal"
    manifest = json.loads(journals(backups)[0].read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 2


def test_html_visual_revierte_el_registro_si_falla_el_visual(entorno, monkeypatch):
    """Si falla el visual.json, report.json no puede quedar con el registro."""
    active, project, backups = entorno
    report_path = project / "Demo.Report" / "definition" / "report.json"
    antes = report_path.read_bytes()

    built = visual_factory.build_visual(
        active, "htmlContent", {"values": ["[TotalAmount]"]},
        {"x": 0, "y": 0, "width": 400, "height": 300}, "Panel", INDICE)

    original = txn_service.durable_write

    def fake(path, data, validator=None):
        if Path(path).name == "visual.json":
            raise OSError("fallo inyectado en el visual")
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fake)

    with pytest.raises(Exception):
        pbir_writer.write_visual_with_registration(
            active, synthetic.PAGE_ID, pbir_writer.HTML_CONTENT_GUID,
            built["visual"], tool="pbi_create_html_visual")

    assert report_path.read_bytes() == antes, \
        "el registro del custom visual se revierte byte a byte"
    assert temporales(project) == []


# ========================================================= politica Desktop ----
@pytest.mark.real_project_state
@pytest.mark.parametrize("estado", [project_state.OPEN, project_state.UNKNOWN])
def test_los_flujos_bulk_respetan_la_politica_estricta(entorno, monkeypatch, estado):
    active, project, backups = entorno
    antes = huella_proyecto(project)
    monkeypatch.setattr(
        project_state, "detect",
        lambda a, **kw: project_state.ProjectOpenState(estado, "high", "forzado"))

    with pytest.raises(project_state.ProjectOpenInDesktopError):
        page_builder.create_page_from_spec(active, SPEC, INDICE)
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        pbir_writer.update_visuals_bulk(active, synthetic.PAGE_ID, _posiciones(),
                                        tool="pbi_arrange_visuals")
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        pbir_writer.create_page_with_visuals(active, "X", 1280, 720, [], tool="t")

    assert huella_proyecto(project) == antes, "ningun flujo escribio nada"
    assert journals(backups) == []
