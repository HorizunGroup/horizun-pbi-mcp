"""Fixtures compartidas: proyecto .pbip sintetico y Session aislada."""
import json

import pytest

import config
from config import ActivePbip, Session, Settings


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_project_state: la prueba ejercita el detector real de "
        "services.project_state y no debe recibir el estado forzado.")
    config.addinivalue_line(
        "markers",
        "local_fixture: requiere el fixture local de compatibilidad "
        "(tests/fixtures/local/), que no se versiona.")
    config.addinivalue_line(
        "markers",
        "packaging: construye un wheel y lo instala en un venv temporal. "
        "Lenta. Excluir con: -m 'not packaging'.")
    config.addinivalue_line(
        "markers",
        "live_validator: ejecuta el CLI oficial de Microsoft. Requiere Node y "
        "el paquete instalado (python scripts/fetch_report_validator.py). Se "
        "omite sola si no esta. Ejecutar con: python -m pytest -m live_validator.")
    config.addinivalue_line(
        "markers",
        "live: consulta el motor real de Power BI Desktop. Solo lectura, nunca "
        "destructiva. Se omite sola si no hay ninguna instancia sirviendo un "
        "modelo. Para ejecutarla: abre un .pbix o .pbip en Desktop y lanza "
        "`python -m pytest -m live`.")


def make_settings(tmp_path) -> Settings:
    return Settings(
        libs_dir=tmp_path / "libs",
        outputs_dir=tmp_path / "outputs",
        backups_dir=tmp_path / "backups",
        max_rows=100,
        command_timeout=30,
        dotnet_runtime="netfx",
        log_level="INFO",
        log_file=None,
        default_pbip=None,
    )


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Settings de prueba, tambien inyectadas como singleton global."""
    settings = make_settings(tmp_path)
    settings.ensure_dirs()
    monkeypatch.setattr(config, "_settings", settings)
    return settings


@pytest.fixture(autouse=True)
def proyecto_cerrado(monkeypatch, request):
    """Fuerza el estado 'cerrado' del proyecto en todas las pruebas.

    Sin esto, la suite fallaria o pasaria segun si quien la ejecuta tiene Power
    BI Desktop abierto en ese momento. Las pruebas que SI quieren ejercitar el
    detector se marcan con `@pytest.mark.real_project_state`.
    """
    from services import project_state

    project_state.invalidate_cache()
    if "real_project_state" in request.keywords:
        yield
        project_state.invalidate_cache()
        return

    monkeypatch.setattr(
        project_state, "detect",
        lambda active, **kwargs: project_state.ProjectOpenState(
            project_state.CLOSED, "high", "estado forzado por las pruebas"))
    yield
    project_state.invalidate_cache()


@pytest.fixture
def session(isolated_settings):
    return Session(isolated_settings)


@pytest.fixture
def sample_pbip(tmp_path):
    """Crea un .pbip minimo (PBIR + TMDL) y devuelve la ruta del .pbip."""
    proj = tmp_path / "proj"
    rep = proj / "MyReport.Report"
    pages = rep / "definition" / "pages"
    (pages / "pg1" / "visuals").mkdir(parents=True)
    (rep / "definition.pbir").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                   "definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": "../MyReport.SemanticModel"}},
    }), encoding="utf-8")
    (pages / "pages.json").write_text(json.dumps({
        "pageOrder": ["pg1"], "activePageName": "pg1"}), encoding="utf-8")
    (rep / "definition" / "report.json").write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/"
                   "definition/report/2.0.0/schema.json",
        "themeCollection": {}, "publicCustomVisuals": []}), encoding="utf-8")
    (pages / "pg1" / "page.json").write_text(json.dumps({
        "name": "pg1", "displayName": "P1", "width": 1280, "height": 720}), encoding="utf-8")

    sm = proj / "MyReport.SemanticModel"
    tables = sm / "definition" / "tables"
    tables.mkdir(parents=True)
    # `ref table` no es decorativo: sin esa linea la tabla esta en disco pero
    # no forma parte del modelo. Un fixture sin ella no representa un .pbip
    # real y dejaria pasar justo ese fallo.
    (sm / "definition" / "model.tmdl").write_text(
        "model Model\n\tculture: es-ES\n\nref table Ventas\n", encoding="utf-8")
    (tables / "Ventas.tmdl").write_text(
        "table Ventas\n"
        "\tmeasure Total = SUM(Ventas[Monto])\n"
        "\t\tformatString: #,0\n"
        "\t\tlineageTag: 00000000-0000-0000-0000-000000000001\n"
        "\tcolumn Monto\n"
        "\t\tdataType: double\n"
        "\t\tsummarizeBy: sum\n"
        "\t\tsourceColumn: Monto\n",
        encoding="utf-8")

    pbip = proj / "MyReport.pbip"
    pbip.write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/"
                   "pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": "MyReport.Report"}}],
    }), encoding="utf-8")
    return pbip
