"""Fase H1-H4 — limites de DAX y redaccion de datos sensibles.

Defectos que reproducen estas pruebas:

- los limites (`max_rows`, `max_bytes`, `timeout_seconds`) se pasaban por
  `int()` sin validar: un 0 devolvia cero filas en silencio, un negativo
  llegaba al motor y un valor absurdo se aceptaba;
- `ConnectionFailedError` metia la connection string ENTERA en `details`;
- `DaxQueryError` metia 2000 caracteres de la consulta, y el mensaje del motor
  suele repetirla completa;
- la exportacion decia "resultado completo" cuando ya venia truncado.
"""
from __future__ import annotations

import json

import pytest

from powerbi.errors import ValidationError
from services import redaction
from utils import validation


# =========================================================== H1: los limites ==
@pytest.mark.parametrize("nombre,tope", [
    ("max_rows", validation.MAX_ROWS_PERMITIDO),
    ("max_bytes", validation.MAX_BYTES_PERMITIDO),
    ("timeout_seconds", validation.MAX_TIMEOUT_PERMITIDO),
])
@pytest.mark.parametrize("malo", [0, -1, -1000])
def test_cero_y_negativos_se_rechazan(nombre, tope, malo):
    with pytest.raises(ValidationError) as exc:
        validation.validate_limit(malo, nombre, tope)
    assert exc.value.details["parameter"] == nombre


@pytest.mark.parametrize("nombre,tope", [
    ("max_rows", validation.MAX_ROWS_PERMITIDO),
    ("max_bytes", validation.MAX_BYTES_PERMITIDO),
    ("timeout_seconds", validation.MAX_TIMEOUT_PERMITIDO),
])
def test_valores_desproporcionados_se_rechazan(nombre, tope):
    with pytest.raises(ValidationError) as exc:
        validation.validate_limit(tope + 1, nombre, tope)
    assert exc.value.details["max"] == tope


@pytest.mark.parametrize("malo", ["100", None if False else [], {}, 1.5, True])
def test_tipos_invalidos_se_rechazan(malo):
    with pytest.raises(ValidationError):
        validation.validate_limit(malo, "max_rows", 1000)


def test_none_significa_por_defecto():
    assert validation.validate_limit(None, "max_rows", 1000) is None


def test_valores_validos_pasan():
    assert validation.validate_limit(500, "max_rows", 1000) == 500
    assert validation.validate_limit(1000, "max_rows", 1000) == 1000
    assert validation.validate_limit(10.0, "max_rows", 1000) == 10


def test_run_dax_valida_antes_de_conectar(session, monkeypatch):
    """Un limite invalido no puede llegar a abrir una conexion."""
    from powerbi import dax_runner

    abiertas = []

    class NoDeberiaAbrirse:
        def __init__(self, *a, **k):
            abiertas.append(1)

    monkeypatch.setattr(dax_runner, "AdomdClient", NoDeberiaAbrirse)
    with pytest.raises(ValidationError):
        dax_runner.run_dax(session, 'EVALUATE ROW("a",1)', max_rows=0)
    assert abiertas == [], "se conecto al motor con un limite invalido"


# ================================================ H3/H4: nada de secretos ====
CS = ("Data Source=localhost:52321;Initial Catalog=abc-123;"
      "Password=SuperSecreto1;User ID=usuario@ejemplo.invalido")


def test_la_connection_string_no_sale_entera():
    salida = redaction.connection_string(CS)
    assert "localhost:52321" in salida, "el destino si es util para diagnosticar"
    for secreto in ("SuperSecreto1", "usuario@ejemplo.invalido", "abc-123"):
        assert secreto not in salida, f"se filtro {secreto!r}"


def test_una_ruta_como_data_source_se_oculta():
    salida = redaction.connection_string(r"Data Source=C:\Users\ejemplo\Informe.pbix")
    assert "ejemplo" not in salida and "Informe" not in salida


def test_el_mensaje_del_motor_no_repite_la_consulta():
    consulta = 'EVALUATE FILTER(Clientes, Clientes[NIF] = "12345678Z")'
    mensaje = f"Query (1,1) error near: {consulta}"
    salida = redaction.texto(mensaje, query=consulta)

    assert "12345678Z" not in salida, "la consulta llevaba un literal identificable"
    assert redaction.OCULTO in salida
    assert "error near" in salida, "el diagnostico debe conservarse"


def test_los_tokens_se_ocultan():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u"
    assert jwt not in redaction.texto(f"auth failed: {jwt}")


def test_la_ruta_personal_se_sustituye():
    import os

    home = os.path.expanduser("~")
    salida = redaction.rutas(f"no se encontro {home}\\Documentos\\x.pbip")
    assert home not in salida
    assert salida.startswith("no se encontro ~")


def test_la_consulta_solo_deja_un_prefijo_corto():
    larga = 'EVALUATE ' + 'X' * 5000
    d = redaction.dax(larga)
    assert d["length"] == len(larga)
    assert len(d["preview"]) < 200
    assert d["truncated"] is True


def test_detalles_limpia_las_claves_conocidas():
    salida = redaction.detalles({"connection_string": CS,
                                 "query": "EVALUATE " + "Y" * 3000,
                                 "port": 52321})
    assert "SuperSecreto1" not in json.dumps(salida)
    assert salida["port"] == 52321, "lo que no es sensible no se toca"
    assert salida["query"]["length"] == 3009


def test_el_cliente_adomd_no_filtra_en_sus_errores(monkeypatch):
    """Los dos puntos de fuga reales, en su sitio."""
    import inspect

    from powerbi import adomd_client

    fuente = inspect.getsource(adomd_client)
    assert "details={\"connection_string\": self.connection_string}" not in fuente, (
        "vuelve a filtrarse la connection string entera")
    assert 'details={"query": query[:2000]}' not in fuente, (
        "vuelve a filtrarse el texto de la consulta")
    assert "redaction.connection_string" in fuente
    assert "redaction.dax" in fuente


# ============================================ H2: la exportacion es honesta ===
def test_la_exportacion_declara_si_esta_truncada(isolated_settings):
    from powerbi import dax_runner

    ruta = dax_runner._exportar(                       # noqa: SLF001
        'EVALUATE Tabla', ["a"], [[1], [2]],
        {"truncated_by_rows": True, "truncated_by_bytes": False})
    datos = json.loads(open(ruta, encoding="utf-8").read())

    assert datos["complete"] is False
    assert datos["truncated"] is True
    assert "TRUNCADO" in datos["note"]
    assert isinstance(datos["query"], dict), "no se vuelca el texto de la consulta"


def test_la_exportacion_completa_lo_dice(isolated_settings):
    from powerbi import dax_runner

    ruta = dax_runner._exportar(                       # noqa: SLF001
        'EVALUATE Tabla', ["a"], [[1]],
        {"truncated_by_rows": False, "truncated_by_bytes": False})
    datos = json.loads(open(ruta, encoding="utf-8").read())

    assert datos["complete"] is True
    assert datos["truncated"] is False
