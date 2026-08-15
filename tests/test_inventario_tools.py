"""TEST-002 — G2.3 (inventario publicado) y G2.4 (un caso negativo por tool).

La auditoría midió lo que se podía medir sin trabajo: las 134 tools declaran su
`output_shape` y su clase de riesgo. Lo que no había era lo otro: **12 de 107
archivos de prueba ejecutaban algo por MCP de verdad**, y de casos negativos no
había recuento. Una tool puede estar declarada, anotada, clasificada y
documentada, y romperse en cuanto un cliente le manda algo que no espera.

Aquí se ejecutan las 134 **por `call_tool`**, cada una contra su propio caso
negativo, calculado a partir de su esquema —ver `tests/inventario_tools.py`—. No
es una tabla que promete cobertura: es la cobertura, corriendo.

Lo que se exige de un fallo, y es lo que separa esto de «no reventó»:

* **Nada de trazas.** Un error de entrada se contesta con un `ToolError` de
  validación o con un sobre `ok: false`; una excepción cualquiera que suba hasta
  el cliente es el defecto.
* **El sobre lleva código.** `ok: false` sin `error` deja al cliente sin nada que
  distinguir «no hay proyecto» de «el disco está lleno».
* **Un rechazo no es un éxito.** Es obvio y por eso se comprueba: si una tool
  contestara `ok: true` a una entrada inválida, el caso negativo no estaría
  midiendo nada.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests import inventario_tools as inv


@pytest.fixture(scope="module")
def servidor():
    """Un solo servidor para las 134: construirlo por tool costaría minutos."""
    from horizun_pbi_mcp.server import build_server

    return build_server()


@pytest.fixture(scope="module")
def filas(servidor):
    return inv.inventario(servidor)


def _llamar(mcp, nombre, args):
    """Devuelve `('rechazo', mensaje)` o `('payload', dict)`, nunca revienta."""
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        respuesta = asyncio.run(mcp.call_tool(nombre, args))
    except ToolError as exc:            # el rechazo de esquema de FastMCP
        return "rechazo", str(exc)
    except Exception as exc:                      # noqa: BLE001 — es el defecto
        return "excepcion", f"{type(exc).__name__}: {exc}"
    payload = respuesta[1] if isinstance(respuesta, tuple) else respuesta
    if isinstance(payload, dict) and "result" in payload:
        payload = payload["result"]
    return "payload", payload


def _nombres():
    """Los nombres de las tools SIN construir el servidor en la recolección.

    Parametrizar con el servidor obligaría a construirlo al recolectar, que es
    antes de que existan las fixtures de aislamiento. Se leen del golden del
    contrato, que es la misma lista y ya está congelada.
    """
    golden = json.loads((inv.REPO_ROOT / "tests" / "golden" / "tools_v1.json")
                        .read_text(encoding="utf-8"))
    return sorted(t["name"] for t in golden["tools"])


@pytest.mark.parametrize("nombre", _nombres())
def test_cada_tool_rechaza_su_caso_negativo(nombre, servidor, filas,
                                            isolated_settings):
    fila = next(f for f in filas if f["tool"] == nombre)
    caso = fila["negativo"]
    if caso["clase"] == "declarada":
        pytest.skip(f"excepción declarada en el inventario: {caso['motivo']}")

    clase, resultado = _llamar(servidor, nombre, caso["args"])

    assert clase != "excepcion", (
        f"{nombre} dejó subir una excepción hasta el cliente con "
        f"{caso['args']!r}: {resultado}")

    if caso["clase"] in ("falta_requerido", "tipo_invalido"):
        # Y **tiene** que ser rechazo, no un sobre. Si una tool dejara de exigir
        # un requerido, ejecutaría con defaults y contestaría `ok: false` por no
        # haber proyecto: verde por el motivo equivocado, que es justo la forma
        # de vacuidad contra la que existe este archivo.
        assert clase == "rechazo", (
            f"{nombre} no rechazó {caso['args']!r} en la validación: el "
            f"esquema ya no exige lo que decía exigir. Contestó: "
            f"{str(resultado)[:200]}")

    if clase == "rechazo":
        assert "validation error" in resultado.lower(), (
            f"{nombre} se rechazó, pero no por validación: {resultado[:200]}")
        return

    assert isinstance(resultado, dict), (
        f"{nombre} contestó algo que no es un sobre: {type(resultado)}")

    if caso["clase"] == "sin_modo_de_fallo":
        # La exención se comprueba: mientras conteste que sí sin proyecto, es
        # cierto que no depende de estado. El día que deje de contestarlo, la
        # exención se ha quedado sin justificación y hay que darle su negativo.
        assert resultado.get("ok") is True, (
            f"{nombre} está declarada «sin modo de fallo» y ha fallado sin "
            f"proyecto activo: la exención ya no vale. {str(resultado)[:200]}")
        return

    assert resultado.get("ok") is False, (
        f"{nombre} dio por bueno un caso negativo ({caso['clase']}): "
        f"{str(resultado)[:200]}")
    assert resultado.get("error"), (
        f"{nombre} contestó ok:false sin código de error, así que el cliente no "
        f"puede distinguir de qué falló: {str(resultado)[:200]}")


def test_el_inventario_cubre_las_134_sin_huecos(filas):
    assert len(filas) == 134
    assert len({f["tool"] for f in filas}) == 134
    sin_caso = [f["tool"] for f in filas if not f["negativo"].get("clase")]
    assert not sin_caso, sin_caso


def test_toda_excepcion_declarada_trae_motivo(filas):
    """G2.4 lo permite **declarado**, que no es lo mismo que en silencio."""
    declaradas = [f for f in filas if f["negativo"]["clase"] == "declarada"]
    sin_motivo = [f["tool"] for f in declaradas
                  if not (f["negativo"].get("motivo") or "").strip()]
    assert not sin_motivo, f"declaradas sin motivo: {sin_motivo}"
    assert len(declaradas) <= 10, (
        f"{len(declaradas)} excepciones declaradas: a partir de cierto número, "
        "«declarado» deja de ser una laguna y pasa a ser la norma")


def test_ninguna_tool_destructiva_se_ejecuta_a_ciegas(filas):
    """La red de seguridad de esta propia prueba.

    Las 130 que se ejecutan lo hacen porque su caso negativo se rechaza en la
    validación, o porque no tienen parámetros y no son destructivas. Si alguien
    clasifica una tool como destructiva y aun así acaba ejecutándose sin
    rechazo previo, es este assert el que tiene que sonar.
    """
    for f in filas:
        if f["riesgo"] != "destructiva":
            continue
        assert f["negativo"]["clase"] in ("falta_requerido", "tipo_invalido",
                                          "declarada"), (
            f"{f['tool']} es destructiva y su caso negativo la ejecutaría: "
            f"{f['negativo']['clase']}")


def test_el_documento_publicado_coincide_con_el_servidor(filas):
    """G2.3. Un inventario que se desincroniza miente con aspecto de verdad."""
    assert inv.DOC.is_file(), (
        "falta docs/INVENTARIO_TOOLS.md. Generalo con: "
        "python -m tests.inventario_tools")
    assert inv.DOC.read_text(encoding="utf-8") == inv.documento(filas), (
        "el inventario publicado no coincide con lo que declara el servidor. "
        "Regeneralo con: python -m tests.inventario_tools")


def test_el_recuento_del_documento_sale_de_contar(filas):
    """Los números del documento no pueden escribirse a mano, ni una vez."""
    texto = inv.documento(filas)
    ejecutadas = sum(1 for f in filas if f["negativo"]["clase"] != "declarada")
    sin_fallo = sum(1 for f in filas
                    if f["negativo"]["clase"] == "sin_modo_de_fallo")
    assert f"| Tools | **{len(filas)}** |" in texto
    assert f"| Ejecutadas por MCP en cada corrida | **{ejecutadas}** |" in texto
    assert (f"| Con caso negativo que las hace fallar | "
            f"**{ejecutadas - sin_fallo}** |") in texto
    assert (f"| Excepciones declaradas con motivo | "
            f"**{len(filas) - ejecutadas}** |") in texto


# ---------------------------------------------------------- el calculador ---

@pytest.mark.parametrize("spec,esperado", [
    ({"type": "string"}, "string"),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, "string"),
    ({"anyOf": [{"type": "null"}, {"type": "integer"}]}, "integer"),
    ({"anyOf": [{"items": {"type": "string"}, "type": "array"},
                {"type": "null"}]}, "array"),
    ({"default": None}, None),
])
def test_el_tipo_se_resuelve_a_traves_del_anyof(spec, esperado):
    """11 tools declaran su primer parámetro como `X | None`.

    Quedarse en «no tiene `type`» las habría dejado sin caso negativo por una
    cuestión de forma del esquema, no de fondo.
    """
    assert inv._tipo_declarado(spec) == esperado


def test_el_valor_invalido_nunca_es_del_tipo_esperado():
    assert inv._valor_invalido({"type": "string"}) == inv.VALOR_IMPOSIBLE
    assert isinstance(inv._valor_invalido({"type": "object"}), str)


def test_una_tool_nueva_sin_parametros_y_destructiva_se_declara_sola():
    """El caso que nadie prueba hasta que aparece."""
    caso = inv.caso_negativo("pbi_inventada", {}, destructiva=True)
    assert caso["clase"] == "declarada"
    assert "destructiva" in caso["motivo"]
