"""Los defaults congelados no se tocan, aunque tentaran.

`pbi_list_tables` y `pbi_list_measures` devuelven `detail="full"` por defecto,
y eso pesa: un modelo corporativo llena media ventana de contexto en una sola
llamada. La tentacion es cambiar el default a `summary`.

No se puede: el default forma parte del contrato congelado y hay clientes
configurados contra el. La respuesta correcta ya esta escrita en el propio
docstring —empezar por `summary`— y la capacidad compacta que hacia falta se
anadio donde SI cabia: `pbi_audit_project(compact=true)`, aditiva y con
default compatible.
"""
from __future__ import annotations

import inspect

import pytest

from horizun_pbi_mcp.services import model_explorer
from horizun_pbi_mcp.tools import audit_tools, documentation_tools


def _tools(modulo):
    registradas = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                registradas[fn.__name__] = fn
                return fn
            return deco

    modulo.register(_Mcp())
    return registradas


@pytest.mark.parametrize("nombre", ["pbi_list_tables", "pbi_list_measures"])
def test_el_default_de_detail_sigue_siendo_full(nombre):
    firma = inspect.signature(_tools(documentation_tools)[nombre])

    assert firma.parameters["detail"].default == "full"
    assert firma.parameters["detail"].annotation in (str, "str")
    assert firma.parameters["source"].default == "live"


@pytest.mark.parametrize("nombre", ["pbi_list_tables", "pbi_list_measures"])
def test_el_docstring_recomienda_empezar_por_summary(nombre):
    """La guia va en la descripcion, que es lo que lee el cliente."""
    doc = _tools(documentation_tools)[nombre].__doc__ or ""

    assert "detail='summary'" in doc
    assert "compatibilidad" in doc


@pytest.mark.parametrize("vista", [model_explorer.tables_view,
                                   model_explorer.measures_view])
def test_el_servicio_tampoco_cambio_su_default(vista):
    assert inspect.signature(vista).parameters["detail"].default == \
        model_explorer.DETALLE_COMPLETO


def test_la_experiencia_compacta_se_anadio_donde_si_cabia():
    """Aditiva y con default compatible, no cambiando un default congelado."""
    firma = inspect.signature(_tools(audit_tools)["pbi_audit_project"])

    assert firma.parameters["compact"].default is False
