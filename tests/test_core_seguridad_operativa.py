"""CORE-003 y CORE-005 — dos afirmaciones del servidor que no eran ciertas.

**CORE-003.** Tras un timeout de refresh, la respuesta dice dos cosas en el
mismo objeto: que el comando *puede seguir ejecutándose en Power BI Desktop*
(`cancel_confirmed: false`) y que reintentar es seguro (`safe_to_retry: true`).
Las dos no pueden ser verdad a la vez. Reintentar con ese consejo solapa dos
`SaveChanges()` sobre el mismo modelo, y el hilo del primero es `daemon=True`:
no hay forma de pararlo.

La causa es una lista incompleta. `_es_seguro_reintentar` decide por el código
de error, y `refresh_timeout` no estaba en ninguna de las dos listas, así que
caía en el `True` por defecto. Un clasificador que enumera lo peligroso y da por
seguro *todo lo demás* falla hacia el lado optimista cada vez que aparece un
error nuevo.

**CORE-005.** `JsonFormatter` redacta `extra_data` y deja crudos `msg` y `exc`.
`redact()` existe, sabe reconocer rutas y secretos, y se aplica al campo que
casi nunca los lleva. La última línea de una excepción sí los lleva a menudo:
`ValidationError` incluye `path` y `pbip_path`, así que la ruta completa —con el
nombre de usuario de Windows y, en un `.pbip` de cliente, el nombre del
cliente— acaba literal en `outputs/*.log`, que es justo el archivo que alguien
adjunta al pedir ayuda.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from horizun_pbi_mcp.services import telemetry
from horizun_pbi_mcp.tools import _common


# ============================================================================
# CORE-003 — `safe_to_retry` no puede contradecir a `cancel_confirmed`
# ============================================================================
def _salida_de_timeout(*, cancel_confirmed: bool) -> dict:
    """El envelope tal y como lo construye `failure()` para un RefreshTimeout."""
    return {
        "ok": False,
        "error": "refresh_timeout",
        "message": "El refresh supero los 30 s y se pidio cancelarlo.",
        "status": "error",
        "details": {
            "timeout_seconds": 30,
            "cancel_requested": True,
            "cancel_confirmed": cancel_confirmed,
            "sources_requiring_credentials": ["SQL/servidor"],
            "credentials_verified": False,
        },
    }


def test_un_timeout_sin_cancelacion_confirmada_no_es_seguro_de_reintentar():
    """El caso exacto del hallazgo: el motor no confirmo, el hilo sigue vivo."""
    salida = _salida_de_timeout(cancel_confirmed=False)

    assert _common._es_seguro_reintentar(salida) is False, (
        "el servidor aconseja reintentar un refresh que puede seguir "
        "ejecutandose: dos SaveChanges solapados sobre el mismo modelo")


def test_un_timeout_con_cancelacion_confirmada_si_se_puede_reintentar():
    """Contener no puede significar bloquear el caso bueno.

    Si el motor confirmo la cancelacion, el hilo termino y el modelo quedo como
    estaba: reintentar es exactamente lo que hay que hacer.
    """
    salida = _salida_de_timeout(cancel_confirmed=True)
    assert _common._es_seguro_reintentar(salida) is True


def test_la_contradiccion_es_inexpresable_venga_de_donde_venga():
    """No basta con listar `refresh_timeout`: la regla es sobre el HECHO.

    El criterio de cierre pide que la combinacion sea imposible de emitir, no
    que este parcheada para un codigo concreto. Cualquier error que declare
    `cancel_confirmed: false` afirma que algo sigue corriendo.
    """
    salida = {"ok": False, "error": "otro_error_cualquiera", "status": "error",
              "details": {"cancel_confirmed": False}}
    assert _common._es_seguro_reintentar(salida) is False


@pytest.mark.parametrize("codigo", [
    "refresh_timeout", "bulk_partially_applied", "rollback_incomplete",
    "unexpected",
])
def test_los_codigos_que_pueden_dejar_algo_a_medias_nunca_son_seguros(codigo):
    """Y sin `details`, tampoco: el silencio no acredita que nada siga vivo."""
    assert _common._es_seguro_reintentar({"error": codigo}) is False


def test_un_error_limpio_sigue_siendo_reintentable():
    """La lista negra no puede convertirse en 'nada es seguro'."""
    assert _common._es_seguro_reintentar(
        {"error": "validation_error", "status": "error"}) is True


# ============================================================================
# CORE-005 — ni `msg` ni `exc` pueden llevar rutas ni secretos sin redactar
# ============================================================================
RUTA = r"C:\Users\unaPersona\OneDrive\Cliente Confidencial\informe.pbip"
SECRETO = "pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _emitir(registro_fabrica, ruta_log: Path) -> dict:
    """Emite un evento por el formateador real y devuelve el JSON resultante."""
    formateador = telemetry.JsonFormatter()
    record = registro_fabrica()
    return json.loads(formateador.format(record))


def _record(msg: str, *, args=(), exc_info=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="horizun_pbi_mcp.prueba", level=logging.ERROR,
        pathname=__file__, lineno=1, msg=msg, args=args, exc_info=exc_info)


def test_una_ruta_en_el_mensaje_no_llega_cruda_al_log(tmp_path):
    evento = _emitir(lambda: _record("No se pudo abrir %s", args=(RUTA,)), tmp_path)

    assert "unaPersona" not in evento["msg"], (
        f"el nombre de usuario de Windows entro literal al log: {evento['msg']}")
    assert "Cliente Confidencial" not in evento["msg"], (
        f"el nombre del cliente entro literal al log: {evento['msg']}")
    # Redactar no puede significar borrar: el mensaje tiene que seguir sirviendo.
    assert "informe.pbip" in evento["msg"] or "pbip" in evento["msg"], (
        f"la redaccion se llevo tambien el dato util: {evento['msg']}")


def test_un_secreto_en_el_mensaje_no_llega_crudo_al_log(tmp_path):
    evento = _emitir(lambda: _record(f"Token rechazado: {SECRETO}"), tmp_path)
    assert SECRETO not in evento["msg"], (
        f"el token entro literal al log: {evento['msg']}")


def test_una_ruta_en_la_excepcion_no_llega_cruda_al_log(tmp_path):
    """El camino mas probable de todos, y el que estaba sin cubrir.

    `ValidationError` lleva `path` y `pbip_path` en su texto, asi que basta una
    validacion fallida sobre un proyecto de cliente.
    """
    try:
        raise ValueError(f"proyecto invalido: {RUTA}")
    except ValueError:
        import sys
        evento = _emitir(lambda: _record("fallo la operacion",
                                         exc_info=sys.exc_info()), tmp_path)

    assert "exc" in evento, "la prueba no llego a ejercitar el campo exc"
    assert "unaPersona" not in evento["exc"], (
        f"la ruta entro literal por la excepcion: {evento['exc']}")
    assert "Cliente Confidencial" not in evento["exc"], evento["exc"]


def test_un_secreto_en_la_excepcion_no_llega_crudo_al_log(tmp_path):
    try:
        raise RuntimeError(f"auth fallo con {SECRETO}")
    except RuntimeError:
        import sys
        evento = _emitir(lambda: _record("fallo", exc_info=sys.exc_info()),
                         tmp_path)
    assert SECRETO not in evento["exc"], evento["exc"]


def test_el_evento_sigue_siendo_json_de_una_linea(tmp_path):
    """Redactar no puede romper el formato: una linea, JSON valido."""
    formateador = telemetry.JsonFormatter()
    linea = formateador.format(_record("ruta %s", args=(RUTA,)))
    assert "\n" not in linea
    json.loads(linea)


def test_el_campo_data_sigue_redactandose(tmp_path):
    """La proteccion que ya existia no se pierde al añadir las otras dos."""
    formateador = telemetry.JsonFormatter()
    record = _record("sin datos")
    record.extra_data = {"password": "s3cr3t0", "ruta": RUTA}
    evento = json.loads(formateador.format(record))
    assert "s3cr3t0" not in json.dumps(evento["data"])
    assert "unaPersona" not in json.dumps(evento["data"])
