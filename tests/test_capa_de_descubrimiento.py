"""Los cinco defectos de la capa de descubrimiento, con su comportamiento.

Todos salieron de una sesion real de ~40 llamadas: el motor de escritura
funcionaba y el tiempo se perdia averiguando como llamar a las cosas.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi import desktop_discovery
from horizun_pbi_mcp.powerbi.errors import ModelDiscoveryError, ValidationError
from horizun_pbi_mcp.pbip import pbir_writer, project_locator, tmdl_writer


# --------------------------------------------------------------------------
# 1. Lo que sale de una tool tiene que entrar en la siguiente
# --------------------------------------------------------------------------

@pytest.mark.parametrize("valor,esperado", [
    ("Data Source=localhost:56057", 56057),
    ("localhost:56057", 56057),
    ("Data Source=127.0.0.1:1234;", 1234),
])
def test_el_connection_string_del_listado_se_acepta(valor, esperado):
    assert desktop_discovery.puerto_de_connection_string(valor) == esperado


@pytest.mark.parametrize("valor", ["", "basura", "localhost:99999", "localhost"])
def test_un_connection_string_ilegible_se_rechaza_diciendo_que_se_espera(valor):
    with pytest.raises(ValidationError) as exc:
        desktop_discovery.puerto_de_connection_string(valor)
    assert "connection_string" in str(exc.value.details)


def test_port_y_connection_string_en_conflicto_no_se_adivinan():
    with pytest.raises(ValidationError, match="puertos distintos"):
        desktop_discovery.select_model(
            None, port=1111, connection_string="localhost:2222")


def test_con_varios_modelos_el_error_dice_que_parametro_usar(monkeypatch):
    """El mensaje anterior remitia a la tool que se acababa de llamar."""
    monkeypatch.setattr(desktop_discovery, "discover_instances", lambda: [
        {"port": 56057, "model_name": "Model", "table_count": 3,
         "tables_sample": ["Riesgos", "Fechas", "Areas"], "status": "ok"},
        {"port": 56100, "model_name": "Model", "table_count": 1,
         "tables_sample": ["Accesos"], "status": "ok"},
    ])

    with pytest.raises(ModelDiscoveryError) as exc:
        desktop_discovery.select_model(None)

    mensaje = str(exc.value)
    assert "port=" in mensaje and "56057" in mensaje and "56100" in mensaje
    instancias = exc.value.details["instances"]
    # Dos modelos llamados los dos "Model": los nombres de tabla son lo unico
    # que permite distinguirlos sin seleccionar cada uno y pedir un resumen.
    assert instancias[0]["tables_sample"] == ["Riesgos", "Fechas", "Areas"]
    assert instancias[0]["select_with"] == "pbi_select_model(port=56057)"


# --------------------------------------------------------------------------
# 2. Una escritura confirmada no puede reportar desenlace desconocido
# --------------------------------------------------------------------------

def test_una_medida_escrita_se_reporta_como_confirmada(session, sample_pbip):
    """`summary()` se llamaba DENTRO del `with`, antes de que corriera el commit.

    El resultado era `committed: false` y `by_outcome: {"unknown": [...]}` en
    la unica operacion que si habia funcionado, en un servidor cuyo contrato es
    no reportar trabajo que no se verifico.
    """
    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()

    txn = tmdl_writer.create_measure_pbip(
        activo, "Ventas", "Total Regresion", "SUM(Ventas[Importe])")["transaction"]

    assert txn["committed"] is True
    assert txn["rolled_back"] is False
    assert txn["clean"] is True
    assert "unknown" not in (txn.get("by_outcome") or {})


def test_una_pagina_escrita_tambien_se_reporta_como_confirmada(session, sample_pbip):
    """El defecto no era exclusivo de las medidas: era del patron de llamada."""
    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()

    txn = pbir_writer.create_page(activo, "Regresion")["transaction"]

    assert txn["committed"] is True
    assert "unknown" not in (txn.get("by_outcome") or {})


def test_un_fallo_sigue_reportandose_como_no_confirmado(session, sample_pbip,
                                                       monkeypatch):
    """Mover summary() fuera del with no puede convertir un fallo en exito."""
    from horizun_pbi_mcp.services import txn as txn_service

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()

    original = txn_service.Transaction.commit

    def commit_que_falla(self):
        raise OSError("corte inyectado al confirmar")

    monkeypatch.setattr(txn_service.Transaction, "commit", commit_que_falla)
    with pytest.raises(BaseException):
        tmdl_writer.create_measure_pbip(
            activo, "Ventas", "No Confirmada", "SUM(Ventas[Importe])")
    monkeypatch.setattr(txn_service.Transaction, "commit", original)

    # Y el TMDL quedo como estaba: el rollback hizo su trabajo.
    from horizun_pbi_mcp.pbip import tmdl_reader
    modelo = tmdl_reader.read_semantic_model(activo)
    nombres = [m["name"] for m in modelo.get("measures") or []]
    for tabla in modelo.get("tables") or []:
        nombres += [m["name"] for m in tabla.get("measures") or []]
    assert "No Confirmada" not in nombres


# --------------------------------------------------------------------------
# 3. "No se pudo leer" no es "no hay nada"
# --------------------------------------------------------------------------

def test_particiones_no_legibles_no_se_reportan_como_ausencia(session, monkeypatch):
    """Si la DMV del motor no se puede leer, es un error; no "no hay ninguna".

    Las particiones en vivo se leen de `TMSCHEMA_PARTITIONS`. Un fallo de esa
    lectura tiene que salir como fallo: una lista vacia con `supported=true`
    le diria a quien llama que el modelo no tiene particiones.
    """
    from horizun_pbi_mcp.powerbi.errors import DaxQueryError
    from horizun_pbi_mcp.services import live_query
    from horizun_pbi_mcp.tools import explore_tools

    capturado = {}

    class MCPFalso:
        def tool(self, *_a, **_k):
            def envoltorio(fn):
                capturado[fn.__name__] = fn
                return fn
            return envoltorio

    explore_tools.register(MCPFalso())

    def _no_se_puede(_session):
        raise DaxQueryError("la DMV no respondio")

    monkeypatch.setattr(live_query, "list_partitions", _no_se_puede)

    salida = capturado["pbi_list_partitions"](source="live")

    assert salida["ok"] is False
    assert salida["error"] == "dax_query_error"
    assert "partitions" not in salida, "un fallo no puede traer una lista vacia"
