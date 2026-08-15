"""CONTRACT-003 — los tres cambios ratificados, con sus regresiones.

Ratificados el 2026-08-15 con el alcance de
[`docs/audits/CONTRACT_003_RATIFICATION.md`](../docs/audits/CONTRACT_003_RATIFICATION.md),
**exclusivamente para 2.0.0**. Vienen de CORE-004(a)(b)(c) y estuvieron
registrados sin aplicar durante todo el ciclo anterior, que es como debe ser: el
contrato congelado no se rompe sin una firma.

| Cambio | Qué era | Qué es |
|---|---|---|
| 1 | `pbi_refresh_model` y `pbi_open_and_refresh`, destructivas **sin** `confirm` | `confirm: bool = False`, y sin él no se ejecuta |
| 2 | `pbi_apply_plan(confirm=True)` — un gate que venía abierto | `confirm=False` |
| 3 | `pbi_open_pbip_project` y `pbi_select_model` anotadas `readOnlyHint=true` | `session_write`: `readOnlyHint=false`, `idempotentHint=true` |

Lo que se exige aquí no es que el código haya cambiado —eso lo dice el golden—
sino que **el efecto haya cambiado**: sin `confirm` no pasa nada, con `confirm`
sí, el rechazo tiene código estable, y ninguna tool que escriba estado de sesión
se sigue anunciando como lectura.

Cada prueba se puede invertir: restaurar cualquiera de los tres defectos vuelve
roja la suite, y hay una prueba por defecto que lo comprueba sobre la firma y la
tabla reales, no sobre un comentario.
"""
from __future__ import annotations

import asyncio

import pytest

from horizun_pbi_mcp.tools import risk

#: Las tres operaciones que la ratificación cubre, con unos argumentos mínimos
#: que serían válidos si la confirmación no faltara.
OPERACIONES = [
    ("pbi_refresh_model", {}),
    ("pbi_open_and_refresh", {}),
    ("pbi_apply_plan", {"plan_token": "token-de-prueba"}),
]

#: Las dos que cambian el proyecto o el modelo activo de la sesión.
DE_SESION = ("pbi_open_pbip_project", "pbi_select_model")


@pytest.fixture(scope="module")
def servidor():
    from horizun_pbi_mcp.server import build_server

    return build_server()


def _llamar(mcp, nombre, args):
    respuesta = asyncio.run(mcp.call_tool(nombre, args))
    payload = respuesta[1] if isinstance(respuesta, tuple) else respuesta
    return payload.get("result", payload) if isinstance(payload, dict) else payload


# ==================== cambio 1 y 2: sin confirm, sin efecto ================

@pytest.mark.parametrize("nombre,args", OPERACIONES,
                         ids=[n for n, _ in OPERACIONES])
def test_sin_confirm_la_operacion_no_se_ejecuta(nombre, args, servidor,
                                                isolated_settings, monkeypatch):
    """Lo que importa no es que conteste mal: es que NO LLEGUE a hacer nada.

    Comprobar solo el código de error dejaría pasar una implementación que
    refresca y después se queja. Por eso se instrumenta la capa de abajo y se
    exige que no la toque nadie.
    """
    tocado = []

    from horizun_pbi_mcp.powerbi import refresh
    from horizun_pbi_mcp.services import planning

    monkeypatch.setattr(refresh, "refresh_model",
                        lambda *a, **k: tocado.append("refresh"))
    monkeypatch.setattr(planning, "apply",
                        lambda *a, **k: tocado.append("apply"))

    salida = _llamar(servidor, nombre, args)

    assert salida["ok"] is False, f"{nombre} se ejecutó sin confirmación"
    assert not tocado, (
        f"{nombre} llegó a {tocado[0]!r} sin `confirm`: contesta que no y hace "
        "que sí, que es peor que no haber puesto la guarda")


@pytest.mark.parametrize("nombre,args", OPERACIONES,
                         ids=[n for n, _ in OPERACIONES])
def test_el_rechazo_tiene_codigo_estable(nombre, args, servidor,
                                         isolated_settings):
    """Un cliente distingue «falta confirmar» de «se rompió» por el código."""
    salida = _llamar(servidor, nombre, args)
    assert salida["error"] == "validation_error", (
        f"{nombre} rechaza con {salida.get('error')!r}: el código de una "
        "confirmación que falta tiene que ser el mismo en las tres")
    assert "confirm" in salida["message"], (
        f"{nombre} no dice qué hacer: {salida['message'][:120]}")


def test_con_confirm_la_operacion_SI_se_ejecuta(servidor, isolated_settings,
                                                monkeypatch):
    """El reverso, y sin él lo de arriba se cumpliría rompiéndolo todo.

    Se sustituye la capa de abajo por un doble: lo que se comprueba es que la
    guarda **deja pasar**, no que un refresh real funcione.
    """
    from horizun_pbi_mcp.powerbi import refresh

    llamadas = []
    monkeypatch.setattr(refresh, "refresh_model",
                        lambda *a, **k: llamadas.append(a) or {"status": "ok"})

    salida = _llamar(servidor, "pbi_refresh_model", {"confirm": True})

    assert llamadas, "con confirm=true la operación no llegó a ejecutarse"
    assert salida["ok"] is True, salida


def test_apply_plan_no_aplica_al_OMITIR_confirm(servidor, isolated_settings,
                                                monkeypatch):
    """El cambio 2 en una línea: omitir el parámetro ya no aplica.

    Es el que más flujos existentes puede romper —omitir un parámetro con
    default es lo normal— y por eso tiene su propia prueba, además de la
    parametrizada.
    """
    from horizun_pbi_mcp.services import planning

    aplicados = []
    monkeypatch.setattr(planning, "apply", lambda *a, **k: aplicados.append(a))

    salida = _llamar(servidor, "pbi_apply_plan", {"plan_token": "t"})

    assert not aplicados, "se aplicó un plan sin confirmación explícita"
    assert salida["ok"] is False


def test_el_default_de_confirm_es_false_en_las_nueve(servidor):
    """El invariante que el cambio 2 restaura: ningún gate viene abierto."""
    abiertos = []
    for tool in servidor._tool_manager.list_tools():
        props = (tool.parameters or {}).get("properties") or {}
        if "confirm" not in props:
            continue
        if props["confirm"].get("default") is not False:
            abiertos.append(f"{tool.name}={props['confirm'].get('default')!r}")
    assert not abiertos, (
        f"tools con `confirm` que viene abierto: {abiertos}. Un gate con "
        "default true no es un gate")


def test_toda_destructiva_tiene_confirm_con_default_false(servidor):
    """La invariante nueva: `destructiveHint` implica `confirm=False`.

    Antes se comprobaba solo sobre `WRITE_DESTRUCTIVE` y por eso las dos de
    refresh —`WRITE_IRREVERSIBLE`— se escapaban. El oráculo es lo que el
    cliente ve en el handshake, no la tabla interna.
    """
    sin_gate = []
    for tool in servidor._tool_manager.list_tools():
        anotacion = risk.annotations_for(tool.name)
        if not anotacion.get("destructiveHint"):
            continue
        props = (tool.parameters or {}).get("properties") or {}
        if props.get("confirm", {}).get("default") is not False:
            sin_gate.append(tool.name)
    assert not sin_gate, (
        f"destructivas sin `confirm=False`: {sin_gate}")


# ==================== cambio 3: las de sesión no son lecturas ==============

@pytest.mark.parametrize("nombre", DE_SESION)
def test_las_de_sesion_ya_no_se_anuncian_de_solo_lectura(nombre):
    a = risk.annotations_for(nombre)
    assert a["readOnlyHint"] is False, (
        f"{nombre} sigue anunciándose como lectura, y cambia a qué apunta todo "
        "lo que venga después")


@pytest.mark.parametrize("nombre", DE_SESION)
def test_las_de_sesion_NO_son_destructivas(nombre):
    """Reclasificar no es asustar: no borran nada del usuario.

    Marcarlas destructivas habría hecho que un cliente pidiera confirmación
    para abrir un proyecto, que es la operación más cotidiana que hay.
    """
    assert risk.annotations_for(nombre)["destructiveHint"] is False


@pytest.mark.parametrize("nombre", DE_SESION)
def test_las_de_sesion_se_anuncian_idempotentes(nombre):
    assert risk.annotations_for(nombre)["idempotentHint"] is True


def test_idempotent_hint_solo_donde_repetir_deja_el_mismo_estado(servidor,
                                                                 isolated_settings,
                                                                 sample_pbip):
    """`idempotentHint` no se declara: se comprueba.

    Se abre el mismo proyecto dos veces y se exige que el estado resultante sea
    el mismo. Si alguna vez dejara de serlo —una pila de proyectos abiertos, un
    contador— la anotación pasaría a mentirle al cliente que reintenta sin
    preguntar.
    """
    primera = _llamar(servidor, "pbi_open_pbip_project", {"path": str(sample_pbip)})
    segunda = _llamar(servidor, "pbi_open_pbip_project", {"path": str(sample_pbip)})

    assert primera["ok"] is True and segunda["ok"] is True
    quitar = {"duration_ms", "request_id"}
    assert ({k: v for k, v in primera.items() if k not in quitar}
            == {k: v for k, v in segunda.items() if k not in quitar}), (
        "abrir dos veces el mismo proyecto no deja el mismo estado: "
        "`idempotentHint=true` estaría mintiendo")


def test_ninguna_tool_que_escriba_sesion_conserva_readonly():
    """El barrido: la clase nueva existe para que no queden rezagadas."""
    mentirosas = [n for n, clase in risk.RISK_BY_TOOL.items()
                  if clase == risk.SESSION_WRITE
                  and risk.annotations_for(n)["readOnlyHint"]]
    assert not mentirosas, mentirosas
    assert set(DE_SESION) <= {n for n, c in risk.RISK_BY_TOOL.items()
                              if c == risk.SESSION_WRITE}


def test_idempotente_no_se_reparte_a_lo_demas():
    """Solo la clase de sesión lo declara. Un `true` de más es una promesa rota."""
    otras = [n for n, clase in risk.RISK_BY_TOOL.items()
             if clase != risk.SESSION_WRITE
             and risk.annotations_for(n).get("idempotentHint")]
    assert not otras, f"anuncian idempotencia sin ser de sesión: {otras}"


# ==================== la mutación: restaurar el defecto rompe ==============

def test_restaurar_el_readonly_de_las_de_sesion_rompe(monkeypatch):
    """Cambio 3, invertido."""
    monkeypatch.setitem(risk.RISK_BY_TOOL, "pbi_open_pbip_project", risk.READ_ONLY)
    assert risk.annotations_for("pbi_open_pbip_project")["readOnlyHint"] is True
    with pytest.raises(AssertionError):
        test_ninguna_tool_que_escriba_sesion_conserva_readonly()


def test_restaurar_el_confirm_abierto_rompe(servidor, monkeypatch):
    """Cambio 2, invertido: un default `True` vuelve a abrir el gate."""
    tool = next(t for t in servidor._tool_manager.list_tools()
                if t.name == "pbi_apply_plan")
    props = (tool.parameters or {}).get("properties") or {}
    original = props["confirm"]["default"]
    props["confirm"]["default"] = True
    try:
        with pytest.raises(AssertionError):
            test_el_default_de_confirm_es_false_en_las_nueve(servidor)
    finally:
        props["confirm"]["default"] = original
