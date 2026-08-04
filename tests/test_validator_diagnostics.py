"""Los diagnosticos del validador oficial tienen que LLEGAR a la respuesta.

El caso real: tras editar dos `visual.json` a mano, todas las respuestas traian

    "validation_report": {"validation_status": "failed", "errors": 8,
                          "preexisting_diagnostics": 8, "blocks": false}

y nunca se listaba ni uno: ni regla, ni archivo, ni ruta JSON, ni mensaje. Ocho
errores que no se pueden leer no se pueden corregir; lo unico que quedaba era
entregar el proyecto sabiendo que estaba mal.

Dos agujeros distintos, y hacen falta los dos arreglados:

1. `Resultado.to_envelope()` no incluia `diagnostics` en absoluto.
2. La comparacion antes/despues devolvia `preexisting_diagnostics` como un
   RECUENTO. Como los preexistentes no bloquean, nadie los listaba nunca.
"""
from __future__ import annotations

from pathlib import Path

from horizun_pbi_mcp.services import report_validator as rv


def _diag(code="PBIR_X", sev="error", file="pages/p/visual.json",
          path="$.objects", message=""):
    return rv.Diagnostico(code=code, severity=sev, file=file, path=path,
                          message=message)


# ------------------------------------------------ el detalle llega al cliente ---
def test_el_envelope_lleva_los_diagnosticos():
    res = rv.Resultado(status="failed", errors=2,
                       diagnostics=[_diag(), _diag(code="PBIR_Y")])
    envelope = res.to_envelope()
    assert envelope["errors"] == 2
    assert len(envelope["diagnostics"]) == 2, (
        "el recuento sin el detalle es lo que dejaba errores incorregibles")
    assert envelope["diagnostics"][0]["code"] == "PBIR_X"
    assert envelope["diagnostics"][0]["file"] == "pages/p/visual.json"
    assert envelope["diagnostics"][0]["path"] == "$.objects"


def test_los_preexistentes_se_listan_no_solo_se_cuentan():
    """No bloquean, y justo por eso nadie los listaba."""
    antes = [_diag(code=f"PBIR_{i}") for i in range(8)]
    comp = rv.comparar(antes, list(antes))

    assert comp["preexisting_diagnostics"] == 8, "el recuento sigue siendo un numero"
    assert len(comp["preexisting_diagnostics_detail"]) == 8
    assert {d["code"] for d in comp["preexisting_diagnostics_detail"]} == {
        f"PBIR_{i}" for i in range(8)}


def test_el_recuento_previo_sigue_siendo_un_numero():
    """Cambiarle el tipo habria roto a cualquier cliente que ya lo lea."""
    comp = rv.comparar([_diag()], [_diag()])
    assert isinstance(comp["preexisting_diagnostics"], int)


# --------------------------------------------------------------- el mensaje ---
def test_el_mensaje_del_cli_se_conserva():
    datos = {"diagnostics": {"PBIR_BAD": {
        "severity": "error",
        "items": [{"file": "pages/p/visual.json", "jsonPath": "$.objects.data",
                   "message": "Unexpected property 'mode'"}]}}}
    diags = rv._normalizar(datos, Path("."))
    assert len(diags) == 1
    assert diags[0].message == "Unexpected property 'mode'"
    assert diags[0].to_dict()["message"] == "Unexpected property 'mode'"


def test_sin_mensaje_no_se_inventa_la_clave():
    datos = {"diagnostics": {"PBIR_BAD": {
        "severity": "error", "items": [{"file": "a.json", "jsonPath": "$"}]}}}
    diags = rv._normalizar(datos, Path("."))
    assert diags[0].message == ""
    assert "message" not in diags[0].to_dict()


def test_el_mensaje_del_cli_pasa_por_la_redaccion(monkeypatch):
    """Viene de un proceso externo y arrastra rutas absolutas."""
    llamadas = []
    original = rv.redaction.texto

    def espia(texto):
        llamadas.append(texto)
        return original(texto)

    monkeypatch.setattr(rv.redaction, "texto", espia)
    datos = {"diagnostics": {"C": {"severity": "error", "items": [
        {"file": "a.json", "message": r"fallo en C:\Users\alguien\informe"}]}}}
    rv._normalizar(datos, Path("."))
    assert llamadas, "el mensaje del CLI debe redactarse antes de devolverlo"


# ------------------------------------------------------ la clave no cambia ---
def test_el_mensaje_no_entra_en_la_clave_de_comparacion():
    """Si entrara, actualizar el CLI haria parecer nuevos los mismos defectos."""
    antes = [_diag(message="texto viejo del validador")]
    despues = [_diag(message="texto nuevo tras actualizar el CLI")]
    comp = rv.comparar(antes, despues)

    assert comp["new_diagnostics"] == [], (
        "el mismo defecto con otro texto no puede contar como nuevo")
    assert comp["blocks"] is False
