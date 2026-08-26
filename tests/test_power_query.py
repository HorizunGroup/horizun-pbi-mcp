"""Leer y editar Power Query (M) sin romper el TMDL que lo contiene.

En un `.pbip` el M no tiene archivo propio: vive dentro del TMDL, en la
`partition` de cada tabla y en `expressions.tmdl`. Eso obliga a dos cosas que
estas pruebas vigilan:

1. **Se reemplaza el BLOQUE entero, localizado por su estructura.** Nada de
   expresiones regulares: un `re.sub` que acierta con una consulta falla con
   la siguiente que tenga un `in` dentro de una cadena.
2. **No se promete lo que no se comprobo.** Que el TMDL parsee no dice NADA
   sobre si la consulta carga: no hay motor M fuera de Power BI Desktop.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import project_locator
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import power_query as pq
from tests.fixtures import synthetic

M_NUEVO = """let
    Origen = Table.FromRows({{1, "a"}}),
    Filtrado = Table.SelectRows(Origen, each [Column1] > 0)
in
    Filtrado"""


@pytest.fixture
def proyecto(tmp_path, session):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip()


@pytest.fixture
def con_expresion(proyecto):
    """Añade un `expressions.tmdl` con una consulta y un parametro."""
    definicion = synthetic.find_semantic_model_dir(proyecto.pbip_path) / "definition"
    (definicion / "expressions.tmdl").write_text(
        "expression 'Consulta Base' =\n"
        "\t\tlet\n"
        "\t\t\tOrigen = Table.FromRows({})\n"
        "\t\tin\n"
        "\t\t\tOrigen\n"
        "\tlineageTag: 33333333-3333-3333-3333-333333333301\n"
        "\tannotation PBI_ResultType = Table\n"
        "\n"
        "expression RutaDatos = \"C:\\datos\" meta [IsParameterQuery=true]\n"
        "\tlineageTag: 33333333-3333-3333-3333-333333333302\n",
        encoding="utf-8")
    return proyecto


def _archivo_tabla(active, nombre="Fact"):
    return (synthetic.find_semantic_model_dir(active.pbip_path)
            / "definition" / "tables" / f"{nombre}.tmdl")


# ================================================================== lectura ===
def test_lista_las_particiones_del_proyecto(proyecto):
    inventario = pq.list_objects(proyecto)

    nombres = {(p["table"], p["name"]) for p in inventario["partitions"]}
    assert ("Fact", "Fact") in nombres
    assert ("Calendar", "Calendar") in nombres
    assert inventario["complete"] is True


def test_lee_la_m_de_una_particion(proyecto):
    salida = pq.get_power_query(proyecto, table="Fact")

    assert salida["kind"] == "partition"
    assert salida["table"] == "Fact"
    assert salida["m"].startswith("let")
    assert "Table.FromRows({})" in salida["m"]
    assert salida["sha256"] == pq.sha256(salida["m"])
    assert salida["file"].endswith("tables/Fact.tmdl")


def test_la_lectura_no_promete_que_la_consulta_funcione(proyecto):
    salida = pq.get_power_query(proyecto, table="Fact")

    assert salida["m_engine_checked"] is False
    assert "Nadie lo ha ejecutado" in salida["note"]


def test_lee_una_expresion_con_nombre(con_expresion):
    salida = pq.get_power_query(con_expresion, name="Consulta Base",
                                kind="expression")

    assert salida["kind"] == "expression"
    assert salida["m"] == ('let\n\tOrigen = Table.FromRows({})\nin\n\tOrigen')
    assert salida["table"] is None


def test_lee_una_expresion_en_una_sola_linea(con_expresion):
    salida = pq.get_power_query(con_expresion, name="RutaDatos",
                                kind="expression")

    assert salida["m"].startswith('"C:\\datos"')


# ============================================== seleccion inequivoca o error ==
def test_un_objeto_inexistente_enseña_los_candidatos(proyecto):
    with pytest.raises(PowerBIMCPError) as exc:
        pq.get_power_query(proyecto, table="NoExiste")

    candidatos = exc.value.details["candidates"]
    assert {p["table"] for p in candidatos["partitions"]} == {"Fact", "Calendar"}
    assert "elige por 'table'+'name'" in exc.value.message


def test_una_seleccion_ambigua_no_elige_por_su_cuenta(proyecto):
    with pytest.raises(PowerBIMCPError) as exc:
        pq.get_power_query(proyecto)          # sin table ni name: hay dos

    assert "ambigua" in exc.value.message
    assert len(exc.value.details["matches"]) == 2


def test_un_kind_invalido_se_rechaza_con_las_opciones(proyecto):
    with pytest.raises(PowerBIMCPError) as exc:
        pq.get_power_query(proyecto, name="Fact", kind="query")

    assert exc.value.details["valid"] == list(pq.TIPOS)


# ================================================================== dry-run ===
def test_dry_run_no_toca_nada(proyecto, isolated_settings):
    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()

    salida = pq.update_power_query(proyecto, M_NUEVO, table="Fact")

    assert salida["dry_run"] is True and salida["applied"] is False
    assert archivo.read_bytes() == antes
    assert salida["preview"]
    assert salida["parse_checked"] is False
    assert list(isolated_settings.backups_dir.rglob("*")) == []


def test_el_dry_run_es_el_valor_por_defecto(proyecto):
    import inspect

    assert inspect.signature(pq.update_power_query).parameters[
        "dry_run"].default is True


# ================================================================ escritura ===
def test_actualiza_una_particion_y_la_relee(proyecto):
    salida = pq.update_power_query(proyecto, M_NUEVO, table="Fact",
                                   dry_run=False)

    assert salida["applied"] is True
    assert salida["reread_verified"] is True
    assert pq.get_power_query(proyecto, table="Fact")["m"] == M_NUEVO
    # El resto del archivo sigue entero: medidas, columnas y anotacion.
    texto = _archivo_tabla(proyecto).read_text(encoding="utf-8-sig")
    assert "measure TotalAmount" in texto
    assert "annotation PBI_ResultType = Table" in texto
    assert "column FactID" in texto


def test_la_escritura_no_promete_que_la_consulta_cargue(proyecto):
    salida = pq.update_power_query(proyecto, M_NUEVO, table="Fact",
                                   dry_run=False)

    assert salida["m_engine_checked"] is False
    assert salida["refresh_checked"] is False
    assert "nada se refresco" in salida["note"]


def test_actualiza_una_expresion_con_nombre(con_expresion):
    salida = pq.update_power_query(con_expresion, M_NUEVO,
                                   name="Consulta Base", kind="expression",
                                   dry_run=False)

    assert salida["applied"] is True
    assert pq.get_power_query(con_expresion, name="Consulta Base",
                              kind="expression")["m"] == M_NUEVO
    # Las propiedades de la expresion y la SIGUIENTE expresion sobreviven.
    texto = (synthetic.find_semantic_model_dir(con_expresion.pbip_path)
             / "definition" / "expressions.tmdl").read_text(encoding="utf-8-sig")
    assert "lineageTag: 33333333-3333-3333-3333-333333333301" in texto
    assert "expression RutaDatos" in texto


def test_actualiza_una_expresion_que_estaba_en_una_linea(con_expresion):
    salida = pq.update_power_query(con_expresion, M_NUEVO, name="RutaDatos",
                                   kind="expression", dry_run=False)

    assert salida["applied"] is True
    assert pq.get_power_query(con_expresion, name="RutaDatos",
                              kind="expression")["m"] == M_NUEVO
    assert pq.get_power_query(con_expresion, name="Consulta Base",
                              kind="expression")["m"].startswith("let")


# ============================================================== hash obsoleto =
def test_un_hash_obsoleto_rechaza_la_escritura(proyecto):
    original = pq.get_power_query(proyecto, table="Fact")
    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, M_NUEVO, table="Fact",
                              expected_sha256="0" * 64, dry_run=False)

    assert exc.value.details["actual_sha256"] == original["sha256"]
    assert archivo.read_bytes() == antes


def test_el_hash_vigente_deja_pasar(proyecto):
    actual = pq.get_power_query(proyecto, table="Fact")["sha256"]

    salida = pq.update_power_query(proyecto, M_NUEVO, table="Fact",
                                   expected_sha256=actual, dry_run=False)

    assert salida["applied"] is True
    assert salida["previous_sha256"] == actual


# ================================================= nada a medias, nunca ======
def test_un_tmdl_con_errores_nuevos_revierte_byte_a_byte(proyecto, monkeypatch):
    from horizun_pbi_mcp.services import tmdl_validate

    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()
    llamadas = {"n": 0}
    real = tmdl_validate.validate

    def _validate(definicion, use_tom=True):
        llamadas["n"] += 1
        salida = real(definicion, use_tom=False)
        if llamadas["n"] > 1:                  # la de DESPUES de escribir
            salida = dict(salida)
            salida["findings"] = list(salida["findings"]) + [{
                "rule": "tmdl_parse_failed", "severity": "error",
                "object": {"kind": "model", "name": "Fact",
                           "file": str(archivo)}}]
        return salida

    monkeypatch.setattr(tmdl_validate, "validate", _validate)

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, M_NUEVO, table="Fact", dry_run=False)

    assert "que antes no tenia" in exc.value.message
    assert archivo.read_bytes() == antes


def test_un_fallo_al_confirmar_restaura_byte_a_byte(proyecto, monkeypatch):
    from horizun_pbi_mcp.services import txn as txn_service

    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()

    def _commit_roto(self):
        raise OSError("disco lleno al escribir el manifiesto")

    monkeypatch.setattr(txn_service.Transaction, "commit", _commit_roto)

    with pytest.raises(Exception):
        pq.update_power_query(proyecto, M_NUEVO, table="Fact", dry_run=False)

    assert archivo.read_bytes() == antes


def test_un_tmdl_ilegible_no_se_sobrescribe_ni_se_omite(proyecto, monkeypatch):
    """Un archivo que no se puede leer no desaparece del inventario.

    Si se omitiera en silencio, `Fact` pareceria una tabla sin particion y la
    siguiente escritura crearia una segunda encima de la que no se pudo ver.
    """
    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()
    real = pq._leer

    def _leer(path):
        if path.name == "Fact.tmdl":
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "invalido")
        return real(path)

    monkeypatch.setattr(pq, "_leer", _leer)

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, M_NUEVO, table="Fact", dry_run=False)

    candidatos = exc.value.details["candidates"]
    assert candidatos["complete"] is False
    assert candidatos["unreadable_files"][0]["file"].endswith("Fact.tmdl")
    assert archivo.read_bytes() == antes


def test_una_m_vacia_se_rechaza(proyecto):
    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, "   ", table="Fact", dry_run=False)

    assert "vacia" in exc.value.message


# ====================================================== las puertas de escritura
@pytest.mark.real_project_state
def test_desktop_abierto_bloquea_la_escritura(proyecto, monkeypatch):
    from horizun_pbi_mcp.services import project_state

    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()
    monkeypatch.setattr(
        project_state, "detect",
        lambda active, **kw: project_state.ProjectOpenState(
            project_state.OPEN, "high", "Desktop tiene el proyecto abierto"))

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, M_NUEVO, table="Fact", dry_run=False)

    assert exc.value.code == "project_open_in_desktop"
    assert archivo.read_bytes() == antes


def test_una_ruta_fuera_del_proyecto_se_rechaza(proyecto, tmp_path,
                                                monkeypatch):
    """Aunque el modelo apunte fuera, la escritura queda confinada."""
    fuera = synthetic.outside_marker_dir(tmp_path, "MODELO_FUERA")
    (fuera / "tables").mkdir(parents=True, exist_ok=True)
    (fuera / "tables" / "Fuera.tmdl").write_text(
        "table Fuera\n"
        "\tpartition Fuera = m\n"
        "\t\tmode: import\n"
        "\t\tsource =\n"
        "\t\t\t\tlet\n"
        "\t\t\t\t\tOrigen = 1\n"
        "\t\t\t\tin\n"
        "\t\t\t\t\tOrigen\n", encoding="utf-8")
    monkeypatch.setattr(pq, "_definicion", lambda _a: fuera)
    antes = (fuera / "tables" / "Fuera.tmdl").read_bytes()

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, M_NUEVO, table="Fuera", dry_run=False)

    assert exc.value.code == "path_security_error"
    assert (fuera / "tables" / "Fuera.tmdl").read_bytes() == antes


def test_una_m_con_una_credencial_no_se_escribe(proyecto):
    archivo = _archivo_tabla(proyecto)
    antes = archivo.read_bytes()
    token = "Ab3xQ9zR7tL2mN8kP5wV1yH4"           # sintetico, no autentica nada
    m = ('let\n'
         f'    Origen = Web.Contents("https://x", [Headers=[apiKey="{token}"]])\n'
         'in\n    Origen')

    with pytest.raises(PowerBIMCPError) as exc:
        pq.update_power_query(proyecto, m, table="Fact", dry_run=False)

    assert exc.value.details["security_scan"]["status"] == "blocked"
    assert token not in str(exc.value.to_dict())
    assert archivo.read_bytes() == antes


# ========================================================== la clasificacion ==
@pytest.mark.parametrize("tool,clase", [
    ("pbi_get_power_query", "read_only"),
    ("pbi_update_power_query", "write_reversible"),
])
def test_las_tools_nuevas_estan_clasificadas(tool, clase):
    from horizun_pbi_mcp.tools import risk

    assert risk.RISK_BY_TOOL[tool] == clase
