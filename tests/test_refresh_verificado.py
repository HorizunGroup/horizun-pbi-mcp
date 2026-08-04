"""El refresh dice cuantas filas cargo, o admite que no lo sabe.

`pbi_refresh_model` devolvia "ok" y la duracion. Pero un refresh puede terminar
bien y haber cargado CERO filas -credenciales que devuelven un conjunto vacio,
un filtro de fecha que no alcanza nada, un origen que cambio de esquema-, y el
servidor lo daba por bueno. Es exactamente lo que promete no hacer: no reportar
trabajo que no se verifico.

La regla que se vigila aqui, mas alla del conteo: **si no se puede contar, se
dice**. Devolver un cero inventado seria peor que no contar, porque un cero es
justo la senal de alarma.
"""
from __future__ import annotations

import contextlib

import pytest

from horizun_pbi_mcp.powerbi import refresh


class _Tabla:
    def __init__(self, nombre):
        self.Name = nombre

    def RequestRefresh(self, _rt):
        pass


class _Modelo:
    # "O'Brien" esta a proposito: un nombre con comilla construiria DAX
    # invalido si no se escapa al contar sus filas.
    def __init__(self, nombres=("Ventas", "Calendario", "O'Brien")):
        self.Tables = [_Tabla(n) for n in nombres]

    def RequestRefresh(self, _rt):
        pass

    def SaveChanges(self):
        pass


class _Sesion:
    active_pbip = None

    def require_active_model(self):
        return type("M", (), {"connection_string": "cs", "catalog": "cat"})()


@pytest.fixture
def motor(monkeypatch):
    """Sustituye TOM y ADOMD por dobles: aqui no se prueba el motor."""
    @contextlib.contextmanager
    def _lease(_s):
        yield object()

    @contextlib.contextmanager
    def _connect(_m):
        yield (object(), object(), _Modelo())

    monkeypatch.setattr(refresh, "lease_active_model", _lease)
    monkeypatch.setattr(refresh, "connect", _connect)
    monkeypatch.setattr(refresh, "load_tom",
                        lambda: type("T", (), {"RefreshType": type(
                            "R", (), {"Full": object()})})())

    class _Cliente:
        conteos = {"Ventas": 120, "Calendario": 366, "O'Brien": 7}
        falla_en = ()

        def __init__(self, *_a, **_k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_e):
            return False

        def execute_scalar(self, query):
            # Se desescapa la comilla igual que hace el motor: en DAX el
            # nombre 'O''Brien' ES "O'Brien". Un doble que parta por la
            # primera comilla leeria "O" y daria un falso negativo.
            crudo = query[query.index("'") + 1:query.rindex("'")]
            nombre = crudo.replace("''", "'")
            if nombre in self.falla_en:
                raise RuntimeError("la tabla no responde")
            return self.conteos.get(nombre, 0)

    import horizun_pbi_mcp.powerbi.adomd_client as ac
    monkeypatch.setattr(ac, "AdomdClient", _Cliente)
    return _Cliente


# ------------------------------------------------------------- el conteo ---
def test_el_refresh_dice_cuantas_filas_cargo(motor):
    salida = refresh.refresh_model(_Sesion())
    assert salida["status"] == "ok"
    assert salida["rows_by_table"] == {"Ventas": 120, "Calendario": 366,
                                      "O'Brien": 7}


def test_refrescar_una_tabla_solo_cuenta_esa(motor):
    salida = refresh.refresh_model(_Sesion(), tables=["Ventas"])
    assert salida["rows_by_table"] == {"Ventas": 120}


# -------------------------------------------------- cero filas es una alarma ---
def test_una_tabla_con_cero_filas_sale_avisada(motor):
    motor.conteos = {"Ventas": 0, "Calendario": 366, "O'Brien": 7}
    salida = refresh.refresh_model(_Sesion())

    assert salida["status"] == "ok", "el refresh SI funciono"
    assert salida["rows_by_table"]["Ventas"] == 0
    assert any("CERO filas" in a for a in salida["warnings"]), (
        "un refresh correcto que carga nada tiene que decirlo")
    assert "Ventas" in str(salida["warnings"])


def test_si_todas_traen_filas_no_hay_aviso(motor):
    salida = refresh.refresh_model(_Sesion())
    assert "warnings" not in salida


# ------------------------------------------------- no se inventa el numero ---
def test_si_una_tabla_no_se_puede_contar_se_dice(motor):
    motor.falla_en = ("Calendario",)
    salida = refresh.refresh_model(_Sesion())

    assert salida["rows_by_table"] == {"Ventas": 120, "O'Brien": 7}
    assert "Calendario" not in salida["rows_by_table"], (
        "mejor ausente que con un cero inventado: el cero es la alarma")
    assert any("Calendario" in a for a in salida["warnings"])


def test_si_no_hay_forma_de_contar_el_refresh_no_se_cae(monkeypatch, motor):
    """Contar es informacion extra; no puede tumbar un refresh que funciono."""
    class _Roto:
        def __init__(self, *_a, **_k):
            raise RuntimeError("ADOMD no disponible")

    import horizun_pbi_mcp.powerbi.adomd_client as ac
    monkeypatch.setattr(ac, "AdomdClient", _Roto)

    salida = refresh.refresh_model(_Sesion())
    assert salida["status"] == "ok"
    assert "rows_by_table" not in salida
    assert salida["warnings"], "tiene que admitir que no conto"


def test_el_nombre_de_tabla_con_comilla_no_rompe_la_consulta(motor):
    """Una comilla en el nombre construiria DAX invalido si no se escapa."""
    consultas = []
    original = motor.execute_scalar

    def espia(self, query):
        consultas.append(query)
        return 1

    motor.execute_scalar = espia
    refresh.refresh_model(_Sesion(), tables=["O'Brien"])
    motor.execute_scalar = original

    assert consultas and "O''Brien" in consultas[0], (
        f"la comilla debe ir escapada: {consultas}")
