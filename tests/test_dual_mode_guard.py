"""Fase 1A.3 — `mode="both"` se rechaza ANTES de cualquier efecto.

La contradiccion que motiva esta fase:

    live -> necesita Power BI Desktop ABIERTO   (TOM habla con msmdsrv)
    pbip -> necesita Desktop CERRADO            (politica estricta)

No hay ningun estado del sistema en el que ambos destinos puedan escribirse con
seguridad en una sola llamada. Y la implementacion dual aplicaba primero `live`
y despues `pbip`, asi que con Desktop abierto el resultado era un estado PARCIAL
determinista: modelo en memoria cambiado, disco intacto.

Estas pruebas comprueban, para las SEIS tools duales, que `both` falla sin
producir ni un solo efecto: cero conexiones TOM, cero SaveChanges, cero archivos
tocados, cero journal, cero `.tmp`, cero entradas en el change log.
"""
from __future__ import annotations

import contextlib
import hashlib
from pathlib import Path

import pytest

from config import ActiveModel
from pbip import model_edit, project_locator, tmdl_writer
from powerbi import model_writer
from services import dual_mode, project_state
from services.dual_mode import DualModeNotAvailableError
from tests.fixtures import synthetic
from tools._common import guard
from tools import model_edit_tools

# Las seis tools con parametro `mode`.
TOOLS_DUALES = [
    "pbi_create_measure", "pbi_update_measure", "pbi_delete_measure",
    "pbi_set_column_visibility", "pbi_hide_columns",
    "pbi_set_relationship_direction",
]


# ------------------------------------------------------------------ dobles ---
class Contador:
    """Registra todo efecto observable: conexiones, SaveChanges y escrituras."""

    def __init__(self):
        self.conexiones = 0
        self.save_changes = 0
        self.escrituras = 0
        self.change_log = 0


@pytest.fixture
def espia(monkeypatch, tmp_path):
    """Instrumenta TOM, la escritura durable y el change log."""
    c = Contador()

    class FakeCol:
        def __init__(self, n):
            self.Name = n
            self.IsHidden = False

    class FakeTable:
        def __init__(self, n, cols):
            self.Name = n
            self.Columns = [FakeCol(x) for x in cols]
            self.Measures = _FakeMeasures()

    class _FakeMeasures:
        def Find(self, _n):
            return None

    class FakeModel:
        def __init__(self):
            self.Tables = [FakeTable("Fact", ["Amount", "FactID"]),
                           FakeTable("Calendar", ["Year"])]
            self.Relationships = []

        def SaveChanges(self):
            c.save_changes += 1

    @contextlib.contextmanager
    def fake_connect(_model):
        c.conexiones += 1
        yield (object(), object(), FakeModel())

    monkeypatch.setattr(model_writer, "connect", fake_connect)

    from services import txn as txn_service
    original = txn_service.durable_write

    def contar_escritura(path, data, validator=None):
        c.escrituras += 1
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", contar_escritura)

    from utils import change_log
    monkeypatch.setattr(change_log, "record_change",
                        lambda *a, **k: c.__setattr__("change_log", c.change_log + 1))
    for modulo in (model_edit, tmdl_writer, model_edit_tools):
        if hasattr(modulo, "record_change"):
            monkeypatch.setattr(modulo, "record_change",
                                lambda *a, **k: c.__setattr__(
                                    "change_log", c.change_log + 1))
    return c


@pytest.fixture
def sesion_realista(session, tmp_path):
    """Sesion con modelo activo Y proyecto activo: el escenario del usuario."""
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    session.set_active_model(ActiveModel(
        host="localhost", port=1234, connection_string="Data Source=localhost:1234",
        catalog="cat", database_name="cat", model_name="M",
        pid=1, process_started=1.0, session_fingerprint="fp"))
    return session, pbip.parent


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


# =========================================== la precondicion, en aislamiento ===
@pytest.mark.parametrize("modo", ["live", "pbip", "LIVE", " Pbip "])
def test_modos_de_un_solo_destino_se_permiten(modo):
    assert dual_mode.assert_mode_is_safely_executable(modo) in ("live", "pbip")


@pytest.mark.parametrize("modo", ["both", "BOTH", " Both "])
def test_both_se_rechaza(modo):
    with pytest.raises(DualModeNotAvailableError) as exc:
        dual_mode.assert_mode_is_safely_executable(modo)
    assert exc.value.code == "dual_mode_not_safely_available"


def test_el_mensaje_explica_las_cuatro_cosas():
    with pytest.raises(DualModeNotAvailableError) as exc:
        dual_mode.assert_mode_is_safely_executable("both")
    m = exc.value.message
    assert "ABIERTO" in m, "debe decir que live necesita Desktop abierto"
    assert "CERRADO" in m, "debe decir que pbip lo necesita cerrado"
    assert "incompatibles" in m, "debe decir por que no puede garantizarse"
    assert "mode='live'" in m and "mode='pbip'" in m, "debe indicar que elegir"
    assert exc.value.details["available_modes"] == ["live", "pbip"]


def test_modo_invalido_sigue_siendo_error_de_validacion():
    from powerbi.errors import ValidationError
    with pytest.raises(ValidationError):
        dual_mode.assert_mode_is_safely_executable("modo_inexistente")


def test_no_hay_bypass_por_variable_de_entorno(monkeypatch):
    for var in ("PBI_MCP_ALLOW_BOTH", "PBI_MCP_DUAL_MODE",
                "PBI_MCP_PBIR_WRITE_POLICY", "PBI_MCP_FORCE"):
        monkeypatch.setenv(var, "1")
    with pytest.raises(DualModeNotAvailableError):
        dual_mode.assert_mode_is_safely_executable("both")


def test_run_dual_no_ejecuta_los_dos_lados():
    llamadas = []
    dual_mode.run_dual("live", lambda: llamadas.append("live"),
                       lambda: llamadas.append("pbip"))
    assert llamadas == ["live"]
    llamadas.clear()
    dual_mode.run_dual("pbip", lambda: llamadas.append("live"),
                       lambda: llamadas.append("pbip"))
    assert llamadas == ["pbip"]


def test_run_dual_propaga_el_error_en_vez_de_marcar_inconsistente():
    """Antes, un fallo en un lado se convertia en `consistent: False`."""
    from powerbi.errors import ValidationError

    def explota():
        raise ValidationError("fallo del destino")

    with pytest.raises(ValidationError):
        dual_mode.run_dual("live", explota, lambda: None)


# ================================== las seis tools, sin un solo efecto ========
#: Argumentos minimos y validos para invocar cada tool dual con mode='both'.
ARGS_BOTH = {
    "pbi_create_measure": {"table": "Fact", "name": "M", "expression": "1",
                           "mode": "both"},
    "pbi_update_measure": {"table": "Fact", "name": "TotalAmount",
                           "expression": "2", "mode": "both"},
    "pbi_delete_measure": {"table": "Fact", "name": "TotalAmount",
                           "mode": "both", "confirm": True},
    "pbi_set_column_visibility": {"table": "Fact", "column": "Amount",
                                  "hidden": True, "mode": "both"},
    "pbi_hide_columns": {"columns": [{"table": "Fact", "column": "Amount"},
                                     {"table": "Calendar", "column": "Year"}],
                         "hidden": True, "mode": "both"},
    "pbi_set_relationship_direction": {"from_table": "Fact",
                                       "to_table": "Calendar",
                                       "direction": "both", "mode": "both"},
}


@pytest.fixture
def tools_registradas(sesion_realista, monkeypatch):
    """Servidor real con la sesion de prueba inyectada."""
    import sys

    sys.path.insert(0, "src")
    import config as cfg
    from server import build_server

    session, _project = sesion_realista
    monkeypatch.setattr(cfg, "_session", session)
    mcp = build_server()
    return mcp


@pytest.mark.parametrize("tool", TOOLS_DUALES)
def test_las_seis_tools_rechazan_both_sin_efectos(tool, sesion_realista, espia,
                                                  tools_registradas,
                                                  isolated_settings):
    """Invocacion REAL de cada tool por el canal de FastMCP, con mode='both'."""
    import asyncio
    import json

    session, project = sesion_realista
    antes = huella(project)

    resultado = asyncio.run(
        tools_registradas.call_tool(tool, ARGS_BOTH[tool]))
    # FastMCP devuelve (contenido, datos_estructurados); el dict esta en ambos.
    payload = resultado[1] if isinstance(resultado, tuple) else resultado
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]

    assert payload["ok"] is False, f"{tool}: no fallo con mode='both'"
    assert payload["error"] == "dual_mode_not_safely_available", \
        f"{tool}: fallo con otro codigo ({payload.get('error')})"

    assert espia.conexiones == 0, f"{tool}: abrio una conexion TOM"
    assert espia.save_changes == 0, f"{tool}: ejecuto SaveChanges"
    assert espia.escrituras == 0, f"{tool}: escribio algun archivo"
    assert espia.change_log == 0, f"{tool}: registro en el change log"
    assert huella(project) == antes, f"{tool}: modifico el proyecto"
    assert list(isolated_settings.backups_dir.rglob("manifest.json")) == [], \
        f"{tool}: creo un journal"
    assert list(project.rglob("*.tmp")) == [], f"{tool}: dejo un temporal"


def test_hide_columns_rechaza_both_sin_efectos(sesion_realista, espia,
                                               isolated_settings):
    """La tool con lote: el camino publico completo, de principio a fin."""
    session, project = sesion_realista
    antes = huella(project)

    with pytest.raises(DualModeNotAvailableError):
        model_edit_tools.hide_columns_service(
            session, [{"table": "Fact", "column": "Amount"},
                      {"table": "Calendar", "column": "Year"}], True, "both")

    assert espia.conexiones == 0
    assert espia.save_changes == 0
    assert espia.escrituras == 0
    assert espia.change_log == 0
    assert huella(project) == antes
    assert list(isolated_settings.backups_dir.rglob("manifest.json")) == []
    assert list(project.rglob("*.tmp")) == []


def test_la_respuesta_exterior_es_ok_false(sesion_realista, espia):
    """A traves de guard(), el rechazo llega como ok:false con codigo estable."""
    session, project = sesion_realista
    res = guard(lambda: model_edit_tools.hide_columns_service(
        session, [{"table": "Fact", "column": "Amount"}], True, "both"))
    assert res["ok"] is False
    assert res["error"] == "dual_mode_not_safely_available"
    assert "mode='live'" in res["message"]


@pytest.mark.parametrize("modo", ["live", "pbip"])
def test_los_modos_de_un_destino_siguen_funcionando(modo, sesion_realista, espia):
    """El bloqueo de `both` no puede romper el uso normal."""
    session, project = sesion_realista
    res = model_edit_tools.hide_columns_service(
        session, [{"table": "Fact", "column": "Amount"}], True, modo)
    assert res["mode"] == modo and res["count"] == 1
    if modo == "live":
        assert espia.save_changes == 1 and espia.conexiones == 1
    else:
        assert espia.escrituras > 0


# ==================== regresion: el escenario real que fallaba antes =========
@pytest.mark.real_project_state
def test_regresion_desktop_abierto_ya_no_deja_estado_parcial(
        sesion_realista, espia, monkeypatch, isolated_settings):
    """El escenario del usuario: Desktop ABIERTO, sesion viva, proyecto activo.

    ANTES de la Fase 1A.3, con este mismo estado:
      1. `live` se aplicaba (1 SaveChanges, columna oculta en memoria);
      2. `pbip` se bloqueaba con project_open_in_desktop;
      3. la operacion terminaba con `consistent: False` y estado PARCIAL.

    Verificado empiricamente sobre el codigo de 7adb725. Ahora: cero efectos.
    """
    session, project = sesion_realista
    antes = huella(project)

    # Desktop ABIERTO: el unico estado en el que `live` es posible, y en el que
    # `pbip` esta prohibido. Justo la combinacion contradictoria.
    monkeypatch.setattr(
        project_state, "detect",
        lambda a, **kw: project_state.ProjectOpenState(
            project_state.OPEN, "high", "Desktop abierto (necesario para TOM)"))

    with pytest.raises(DualModeNotAvailableError):
        model_edit_tools.hide_columns_service(
            session, [{"table": "Fact", "column": "Amount"}], True, "both")

    assert espia.save_changes == 0, \
        "antes se ejecutaba 1 SaveChanges y la columna quedaba oculta en memoria"
    assert espia.conexiones == 0, "ni siquiera se conecta a TOM"
    assert huella(project) == antes, "el disco tampoco se toca"
    assert list(isolated_settings.backups_dir.rglob("manifest.json")) == []


@pytest.mark.real_project_state
def test_el_rechazo_no_depende_del_estado_de_desktop(sesion_realista, espia,
                                                     monkeypatch):
    """`both` se rechaza igual con Desktop abierto, cerrado o desconocido."""
    session, project = sesion_realista
    for estado in (project_state.OPEN, project_state.CLOSED, project_state.UNKNOWN):
        monkeypatch.setattr(
            project_state, "detect",
            lambda a, e=estado, **kw: project_state.ProjectOpenState(
                e, "high", "forzado"))
        with pytest.raises(DualModeNotAvailableError):
            model_edit_tools.hide_columns_service(
                session, [{"table": "Fact", "column": "Amount"}], True, "both")
    assert espia.save_changes == 0 and espia.escrituras == 0


# ============================== el contrato de las seis tools se conserva =====
def test_las_seis_tools_siguen_aceptando_el_parametro_mode():
    """`both` se rechaza en tiempo de ejecucion, no se retira del esquema."""
    import asyncio
    import sys

    sys.path.insert(0, "src")
    from server import build_server

    tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
    for nombre in TOOLS_DUALES:
        assert nombre in tools, f"falta la tool {nombre}"
        props = tools[nombre].inputSchema["properties"]
        assert "mode" in props, f"{nombre} perdio el parametro mode"
        assert props["mode"].get("default") == "live", \
            f"{nombre} cambio el default de mode"


def test_las_descripciones_avisan_de_la_limitacion():
    import asyncio
    import sys

    sys.path.insert(0, "src")
    from server import build_server

    tools = {t.name: t for t in asyncio.run(build_server().list_tools())}
    for nombre in TOOLS_DUALES:
        desc = tools[nombre].description or ""
        assert "both" in desc and "deshabilitado" in desc, \
            f"{nombre} no avisa de que mode='both' esta deshabilitado"
