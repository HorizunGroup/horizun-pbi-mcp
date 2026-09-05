"""Consultas en vivo, alias de parametros y carpetas de proyecto.

Tres defectos de ergonomia vistos en uso real:

- `pbi_get_power_query` contestaba `no_active_pbip` con un modelo vivo
  delante, y `pbi_list_partitions` decia `supported=false` con `source='live'`.
  Las dos cosas estan en el motor (DMV `TMSCHEMA_PARTITIONS` y
  `TMSCHEMA_EXPRESSIONS`) y ahora se leen de ahi, distinguiendo particiones
  de expresiones compartidas y sin inventar lo que la DMV no trae.
- Nombres intuitivos (`project_path`, `object_name`) se rechazaban. Ahora son
  alias; y si llegan dos con valores distintos, hay conflicto, nunca un
  filtro ignorado en silencio.
- `pbi_open_in_desktop` con la CARPETA del proyecto decia «El archivo .pbix
  no existe». Ahora resuelve el unico `.pbip`, informa ambiguedad, y el error
  nombra el tipo real.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.config import ActiveModel
from horizun_pbi_mcp.powerbi import desktop_launcher
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import live_query, project_resolver
from horizun_pbi_mcp.tools._common import alias_unico, ruta_de_proyecto


class _Mcp:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def dec(fn):
            self.tools[fn.__name__] = fn
            return fn
        return dec


# --------------------------------------------------- un motor de mentira ---
FILAS = {
    "TMSCHEMA_TABLES": (["ID", "Name"], [[1, "Ventas"], [2, "Fechas"]]),
    "TMSCHEMA_PARTITIONS": (
        ["ID", "TableID", "Name", "Description", "Type", "Mode",
         "QueryDefinition", "State", "RefreshedTime"],
        [[10, 1, "Ventas", None, 4, 0, 'let\n  Origen = Csv.Document("v")\nin\n  Origen',
          "Ready", "2026-09-01 10:00:00"],
         [11, 2, "Fechas", None, 2, 0, "CALENDAR(DATE(2020,1,1), DATE(2030,12,31))",
          "Ready", None],
         [12, 1, "Ventas-2", None, 9, 7, None, "NoData", None]]),
    "TMSCHEMA_EXPRESSIONS": (
        ["ID", "Name", "Kind", "Expression", "Description"],
        [[20, "Ruta", 0, '"C:\\datos" meta [IsParameterQuery=true]', None]]),
}


class _AdomdFalso:
    consultas = []

    def __init__(self, connection_string, catalog=None, **kw):
        self.connection_string = connection_string

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def execute_reader(self, consulta, max_rows=None, max_bytes=None):
        _AdomdFalso.consultas.append(consulta)
        for nombre, (cols, filas) in FILAS.items():
            if nombre in consulta:
                return cols, [list(f) for f in filas], False, 1.0
        raise RuntimeError(f"DMV desconocida: {consulta}")


@pytest.fixture
def modelo_vivo(session, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.powerbi import adomd_client

    monkeypatch.setattr(adomd_client, "AdomdClient", _AdomdFalso)
    _AdomdFalso.consultas = []
    modelo = ActiveModel(host="localhost", port=50123,
                         connection_string="Data Source=localhost:50123",
                         catalog="Demo", pid=1, process_started=1.0)
    session.set_active_model(modelo)
    monkeypatch.setattr(type(session), "require_active_model",
                        lambda self, verify=True: modelo)
    monkeypatch.setattr(cfg, "_session", session)
    return session


# ================================ 1) particiones en vivo ===================
def test_las_particiones_se_leen_del_motor_con_su_tabla_tipo_y_modo(modelo_vivo):
    leido = live_query.list_partitions(modelo_vivo)

    por_nombre = {p["name"]: p for p in leido["partitions"]}
    assert por_nombre["Ventas"]["table"] == "Ventas"
    assert por_nombre["Ventas"]["source_type"] == "m"
    assert por_nombre["Ventas"]["mode"] == "import"
    assert por_nombre["Ventas"]["has_query"] is True
    assert "m" not in por_nombre["Ventas"], "el M entero no viaja en el listado"
    assert por_nombre["Fechas"]["source_type"] == "calculated"
    assert leido["partitions_supported"] is True
    assert [e["name"] for e in leido["expressions"]] == ["Ruta"]
    assert any("TMSCHEMA_EXPRESSIONS" in c for c in _AdomdFalso.consultas)


def test_un_tipo_o_modo_desconocido_no_se_inventa(modelo_vivo):
    leido = live_query.list_partitions(modelo_vivo)
    rara = next(p for p in leido["partitions"] if p["name"] == "Ventas-2")
    assert rara["source_type"] == "unknown(9)"
    assert rara["mode"] == "unknown(7)"
    assert rara["has_query"] is False and rara["query_sha256"] is None


def test_la_tool_de_particiones_en_vivo_ya_esta_soportada(modelo_vivo):
    from horizun_pbi_mcp.tools import explore_tools

    mcp = _Mcp()
    explore_tools.register(mcp)
    r = mcp.tools["pbi_list_partitions"](source="live")

    assert r["ok"] is True and r["supported"] is True
    assert r["count"] == 3 and r["source"] == "live"
    assert r["expressions"][0]["name"] == "Ruta"


# ================================ 2) M en vivo =============================
def test_get_power_query_lee_el_m_del_motor_con_sha(modelo_vivo):
    r = live_query.get_power_query(modelo_vivo, table="ventas", name="Ventas")

    assert r["kind"] == "partition" and r["source"] == "live"
    assert r["file"] is None
    assert r["m"].startswith("let")
    assert r["sha256"] == live_query.sha256(r["m"])
    assert r["m_engine_checked"] is False


def test_una_expresion_compartida_se_pide_por_kind(modelo_vivo):
    r = live_query.get_power_query(modelo_vivo, name="Ruta", kind="expression")
    assert r["kind"] == "expression" and r["table"] is None
    assert "IsParameterQuery" in r["m"]


def test_una_seleccion_ambigua_o_vacia_trae_candidatos(modelo_vivo):
    with pytest.raises(live_query.LiveQueryError) as fallo:
        live_query.get_power_query(modelo_vivo, table="Ventas")
    assert len(fallo.value.details["matches"]) == 2

    with pytest.raises(live_query.LiveQueryError) as fallo:
        live_query.get_power_query(modelo_vivo, name="Nada")
    assert fallo.value.details["candidates"]["expressions"] == [{"name": "Ruta"}]


def test_una_particion_sin_consulta_no_devuelve_una_cadena_vacia(modelo_vivo):
    with pytest.raises(live_query.LiveQueryError) as fallo:
        live_query.get_power_query(modelo_vivo, name="Ventas-2")
    assert fallo.value.details["source_type"] == "unknown(9)"


def test_la_tool_usa_el_motor_cuando_no_hay_pbip_activo(modelo_vivo):
    from horizun_pbi_mcp.tools import model_edit_tools

    mcp = _Mcp()
    model_edit_tools.register(mcp)
    assert modelo_vivo.active_pbip is None

    r = mcp.tools["pbi_get_power_query"](table="Ventas", object_name="Ventas")

    assert r["ok"] is True, r
    assert r["source"] == "live" and r["m"].startswith("let")


def test_la_tool_prefiere_el_pbip_activo_y_lo_dice(session, tmp_path, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.tools import model_edit_tools
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    model_edit_tools.register(mcp)

    r = mcp.tools["pbi_get_power_query"](table="Fact", name="Fact")

    assert r["ok"] is True, r
    assert r["source"] == "pbip" and r["file"]


def test_source_live_explicito_con_pbip_activo_va_al_motor(
        modelo_vivo, tmp_path):
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.tools import model_edit_tools
    from tests.fixtures import synthetic

    project_locator.open_project(modelo_vivo, str(synthetic.materialize(tmp_path)))
    mcp = _Mcp()
    model_edit_tools.register(mcp)

    r = mcp.tools["pbi_get_power_query"](name="Ruta", kind="expression",
                                         source="live")
    assert r["ok"] is True and r["source"] == "live"


# ================================ 3) alias =================================
def test_dos_alias_con_valores_distintos_son_un_conflicto():
    with pytest.raises(ValidationError) as fallo:
        alias_unico(name="A", object_name="B")
    assert fallo.value.details["reason"] == "alias_conflict"
    assert fallo.value.details["conflict"] == {"name": "A", "object_name": "B"}


def test_dos_alias_iguales_o_uno_vacio_no_son_conflicto():
    assert alias_unico(name="A", object_name="A") == "A"
    assert alias_unico(name="", object_name="B") == "B"
    assert alias_unico(name=None, object_name=None) is None


def test_name_y_object_name_distintos_no_devuelven_todo_el_modelo(modelo_vivo):
    from horizun_pbi_mcp.tools import model_edit_tools

    mcp = _Mcp()
    model_edit_tools.register(mcp)
    r = mcp.tools["pbi_get_power_query"](name="Ventas", object_name="Fechas")

    assert r["ok"] is False
    assert r["error"] == "validation_error"
    assert r["details"]["reason"] == "alias_conflict"


def test_project_path_vale_como_path_en_las_tools_de_desktop(
        session, tmp_path, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import dax_tools
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    dax_tools.register(mcp)
    recibido = {}
    monkeypatch.setattr(desktop_launcher, "close_desktop_by_path",
                        lambda p: recibido.update(path=p) or {"closed": True,
                                                              "was_open": True})

    r = mcp.tools["pbi_close_desktop"](project_path=str(pbip), confirm=True)

    assert r["ok"] is True
    assert Path(recibido["path"]) == pbip.resolve()


# ======================= 4) carpetas de proyecto ===========================
def test_una_carpeta_con_un_solo_pbip_se_resuelve(tmp_path):
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    assert ruta_de_proyecto(str(pbip.parent)) == pbip.resolve()
    assert desktop_launcher.resolver_documento(pbip.parent) == pbip.resolve()


def test_una_carpeta_con_dos_pbip_es_ambigua(tmp_path):
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    (pbip.parent / "Otro.pbip").write_text("{}", encoding="utf-8")

    with pytest.raises(project_resolver.AmbiguousProjectError) as fallo:
        ruta_de_proyecto(str(pbip.parent))
    assert sorted(fallo.value.details["candidates"]) == ["Demo.pbip", "Otro.pbip"]


def test_el_error_de_ruta_inexistente_nombra_el_tipo_real(tmp_path):
    with pytest.raises(desktop_launcher.DesktopNotFoundError) as fallo:
        desktop_launcher.resolver_documento(tmp_path / "NoExiste.pbip")
    assert ".pbip" in str(fallo.value) and ".pbix no existe" not in str(fallo.value)
    assert fallo.value.details["reason"] == "document_not_found"


def test_open_in_desktop_con_carpeta_abre_el_pbip(session, tmp_path, monkeypatch):
    from horizun_pbi_mcp import config as cfg
    from horizun_pbi_mcp.tools import dax_tools
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    monkeypatch.setattr(cfg, "_session", session)
    mcp = _Mcp()
    dax_tools.register(mcp)
    abiertos = []

    def _open(ruta, timeout=300, reuse_open=True):
        abiertos.append(ruta)
        raise desktop_launcher.DesktopNotFoundError("sin Desktop aqui")

    monkeypatch.setattr(desktop_launcher, "open_pbix", _open)

    r = mcp.tools["pbi_open_in_desktop"](str(pbip.parent))

    assert r["error"] == "desktop_not_found"
    assert abiertos == [str(pbip.resolve())], "no llego el .pbip resuelto"
