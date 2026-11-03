"""Colisiones de nombres: reintento accionable sin Desktop ni datos reales."""
from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.pbip import tmdl_writer
from horizun_pbi_mcp.powerbi import model_writer
from horizun_pbi_mcp.powerbi.errors import MeasureExistsError
from horizun_pbi_mcp.tools._common import guard
from horizun_pbi_mcp.tools import measure_tools
from tests.test_live_runtime_safety import (
    FakeColumn, FakeMeasure, FakeSession, FakeWriterModel, FakeWriterTable,
    install_writer_model,
)


@pytest.fixture
def active(tmp_path, isolated_settings):
    project = tmp_path / "project"
    tables = project / "Demo.SemanticModel" / "definition" / "tables"
    tables.mkdir(parents=True)
    (tables.parent / "model.tmdl").write_text("model Model\n", encoding="utf-8")
    (tables / "Choques.tmdl").write_text(
        "table Choques\n\tcolumn choques\n\t\tdataType: int64\n"
        "\tcolumn 'TOTAL Choques'\n\t\tdataType: int64\n", encoding="utf-8")
    (tables / "Otra.tmdl").write_text(
        "table Otra\n\tmeasure 'Choques (MEDIDA)' = 1\n", encoding="utf-8")
    return ActivePbip(pbip_path=str(project / "Demo.pbip"),
                      project_dir=str(project),
                      semantic_model_dir=str(tables.parent.parent), has_tmdl=True)


def _assert_suggestions(result):
    assert result["error"] == "measure_exists"
    assert result["details"]["rule"] == "measure_column_collision"
    suggestions = result["suggested_names"]
    assert 2 <= len(suggestions) <= 3
    assert len({n.casefold() for n in suggestions}) == len(suggestions)
    assert not {n.casefold() for n in suggestions} & {
        "choques", "total choques", "choques (medida)"}
    assert "sin preguntar al usuario" in result["message"]
    return suggestions


@pytest.mark.parametrize("operation", ["create_measure_pbip", "render_measure_change"])
@pytest.mark.parametrize("table", ["Choques", "choques"])
def test_pbip_colision_sugiere_nombres_libres_sin_escribir(active, operation, table):
    from pathlib import Path

    files = list(Path(active.semantic_model_dir).rglob("*.tmdl"))
    before = {p: p.read_bytes() for p in files}
    result = guard(lambda: getattr(tmdl_writer, operation)(active, table, "Choques", "1"))
    names = _assert_suggestions(result)
    assert {p: p.read_bytes() for p in files} == before
    for name in names:
        tmdl_writer.render_measure_change(active, table, name, "1")


def test_live_colision_se_detecta_antes_de_mutar(monkeypatch, isolated_settings):
    target = FakeWriterTable("Choques", columns=[
        FakeColumn("choques"), FakeColumn("TOTAL Choques")])
    model = FakeWriterModel([target, FakeWriterTable(
        "Otra", measures=[FakeMeasure("Choques (MEDIDA)", "1")])])
    install_writer_model(monkeypatch, model)
    monkeypatch.setattr(model_writer, "load_tom", lambda: pytest.fail("No debe cargar TOM"))
    result = guard(lambda: model_writer.create_measure(
        FakeSession(), "choques", "Choques", "1", overwrite=True))
    _assert_suggestions(result)
    assert model.save_calls == 0
    assert not target.Measures


def test_pbip_otra_columna_y_nombre_tabla_no_colisionan(active):
    # Las columnas son locales a su tabla; una tabla tampoco es una medida.
    text, _, action = tmdl_writer.render_measure_change(active, "Otra", "Choques", "1")
    assert action == "created"
    assert "measure Choques = 1" in text


def test_live_otra_columna_y_nombre_tabla_no_colisionan(monkeypatch, isolated_settings):
    target = FakeWriterTable("Otra")
    model = FakeWriterModel([target, FakeWriterTable(
        "Choques", columns=[FakeColumn("choques")])])
    install_writer_model(monkeypatch, model)
    monkeypatch.setattr(model_writer, "load_tom", lambda: SimpleNamespace(
        Measure=lambda: FakeMeasure("", "")))
    result = model_writer.create_measure(FakeSession(), "Otra", "Choques", "1")
    assert result["action"] == "created"
    assert model.save_calls == 1


def test_pbip_medidas_siguen_siendo_unicas_en_modelo(active):
    with pytest.raises(MeasureExistsError):
        tmdl_writer.render_measure_change(active, "Choques", "choques (medida)", "1")


@pytest.mark.parametrize("mode", ["pbip", "live"])
def test_tool_conserva_codigo_y_expone_sugerencias(active, session, monkeypatch, mode):
    tools = {}

    class MCP:
        def tool(self):
            def register(fn):
                tools[fn.__name__] = fn
                return fn
            return register

    session.set_active_pbip(active)
    monkeypatch.setattr(measure_tools, "get_session", lambda: session)
    if mode == "live":
        session = FakeSession()
        install_writer_model(monkeypatch, FakeWriterModel([
            FakeWriterTable("Choques", columns=[FakeColumn("choques"),
                                                 FakeColumn("TOTAL Choques")]),
            FakeWriterTable("Otra", measures=[FakeMeasure("Choques (MEDIDA)", "1")]),
        ]))
    measure_tools.register(MCP())
    result = tools["pbi_create_measure"]("Choques", "Choques", "1", mode=mode)
    _assert_suggestions(result)
    assert result["ok"] is False
