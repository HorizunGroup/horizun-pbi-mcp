"""A2: un refresh cuyo origen espera credenciales no puede colgar la sesion.

Un refresh lanzado por XMLA no puede mostrar el dialogo de credenciales de
Power BI Desktop. Si el origen no las tiene guardadas, el motor espera sin
fin: medido, 1800 s hasta que el cliente MCP abortó. No hay ventana modal que
cerrar -se comprobó que Desktop seguía `Responding: True`-, así que el corte
tiene que venir de aquí.

Estas pruebas no tocan Power BI: sustituyen el modelo TOM por un doble cuyo
`SaveChanges()` bloquea, que es exactamente la condición que se quiere cubrir.
"""
from __future__ import annotations

import threading
import time

import pytest

from horizun_pbi_mcp.powerbi import refresh
from horizun_pbi_mcp.powerbi.errors import RefreshTimeoutError, ValidationError


class ModeloQueSeCuelga:
    """Doble de un modelo TOM cuyo refresh nunca termina por si solo."""

    def __init__(self, expresiones=()):
        self.liberado = threading.Event()
        self.cancelado = threading.Event()
        self.Tables = []
        self.Expressions = [type("E", (), {"Expression": e})() for e in expresiones]

    def RequestRefresh(self, _tipo):  # noqa: N802 - nombre de la API de TOM
        self.refresh_pedido = True

    def SaveChanges(self):  # noqa: N802 - nombre de la API de TOM
        # Espera indefinidamente, como el motor ante un origen sin credenciales.
        self.liberado.wait(timeout=30)


class ServidorQueCancela:
    def __init__(self, modelo, obedece=True):
        self._modelo = modelo
        self._obedece = obedece

    def CancelCommand(self):  # noqa: N802 - nombre de la API de AMO
        self._modelo.cancelado.set()
        if self._obedece:
            self._modelo.liberado.set()


def test_un_refresh_colgado_corta_en_su_plazo_y_no_espera_para_siempre():
    modelo = ModeloQueSeCuelga()
    servidor = ServidorQueCancela(modelo)

    inicio = time.perf_counter()
    with pytest.raises(RefreshTimeoutError) as exc:
        refresh._guardar_con_plazo(servidor, modelo, timeout_seconds=1)
    transcurrido = time.perf_counter() - inicio

    assert transcurrido < 10, f"tardo {transcurrido:.1f}s: no corto en el plazo"
    assert modelo.cancelado.is_set(), "no se pidio la cancelacion al motor"
    assert exc.value.code == "refresh_timeout"


def test_el_error_dice_que_origenes_piden_credenciales():
    """El mensaje tiene que llevar a donde esta el problema, no solo fallar."""
    modelo = ModeloQueSeCuelga(expresiones=[
        'let Origen = SharePoint.Files("https://contoso.sharepoint.com/sites/x", '
        '[ApiVersion = 15]) in Origen',
        'let O = Sql.Database("servidor", "base") in O',
    ])
    servidor = ServidorQueCancela(modelo)

    with pytest.raises(RefreshTimeoutError) as exc:
        refresh._guardar_con_plazo(servidor, modelo, timeout_seconds=1)

    origenes = exc.value.details["sources_requiring_credentials"]
    assert any("SharePoint.Files" in o for o in origenes)
    assert any("contoso.sharepoint.com" in o for o in origenes)
    assert any("Sql.Database" in o for o in origenes)
    assert "Cuenta" in str(exc.value)


def test_no_se_afirma_haber_comprobado_las_credenciales():
    """Enumerar los origenes que las piden no es verificar que las tengan.

    Desktop guarda las credenciales en su propio almacen y TOM no lo expone.
    Decir 'este origen no tiene credenciales' seria inventarse la comprobacion.
    """
    modelo = ModeloQueSeCuelga(expresiones=['Web.Contents("https://x.invalid")'])
    with pytest.raises(RefreshTimeoutError) as exc:
        refresh._guardar_con_plazo(
            ServidorQueCancela(modelo), modelo, timeout_seconds=1)

    assert exc.value.details["credentials_verified"] is False


def test_se_distingue_la_cancelacion_confirmada_de_la_que_no_lo_esta():
    """Si el motor no obedece, hay que decir que el comando sigue corriendo."""
    modelo = ModeloQueSeCuelga()
    terco = ServidorQueCancela(modelo, obedece=False)

    refresh._GRACIA_TRAS_CANCELAR = 0.3          # margen corto para la prueba
    try:
        with pytest.raises(RefreshTimeoutError) as exc:
            refresh._guardar_con_plazo(terco, modelo, timeout_seconds=1)
    finally:
        refresh._GRACIA_TRAS_CANCELAR = 15.0
        modelo.liberado.set()

    assert exc.value.details["cancel_confirmed"] is False
    assert "puede seguir ejecutandose" in str(exc.value)


def test_un_refresh_normal_no_paga_nada_por_el_plazo():
    """El camino feliz no cambia: ni error, ni retraso, ni cancelacion."""
    class ModeloRapido:
        def __init__(self):
            self.guardado = False

        def SaveChanges(self):  # noqa: N802
            self.guardado = True

    modelo = ModeloRapido()
    servidor = ServidorQueCancela(ModeloQueSeCuelga())

    refresh._guardar_con_plazo(servidor, modelo, timeout_seconds=600)

    assert modelo.guardado is True


def test_el_error_del_motor_se_propaga_tal_cual_a_traves_del_hilo():
    """Un fallo real de refresh no puede quedar tapado por el plazo."""
    class ModeloQueFalla:
        def SaveChanges(self):  # noqa: N802
            raise RuntimeError("credenciales rechazadas por el origen")

    with pytest.raises(RuntimeError, match="credenciales rechazadas"):
        refresh._guardar_con_plazo(
            ServidorQueCancela(ModeloQueSeCuelga()), ModeloQueFalla(),
            timeout_seconds=5)


def test_timeout_cero_desactiva_el_plazo_y_no_lanza_hilo():
    """Escotilla para un refresh legitimamente larguisimo."""
    class ModeloRapido:
        def __init__(self):
            self.hilo = None

        def SaveChanges(self):  # noqa: N802
            self.hilo = threading.current_thread().name

    modelo = ModeloRapido()
    refresh._guardar_con_plazo(None, modelo, timeout_seconds=0)

    assert modelo.hilo == threading.current_thread().name, (
        "con plazo desactivado el refresh debe correr en el hilo del llamante")


@pytest.mark.parametrize("valor", ["dos minutos", None.__class__, 86_401])
def test_un_timeout_invalido_se_rechaza_antes_de_conectar(session, valor):
    with pytest.raises(ValidationError):
        refresh.refresh_model(session, "full", None, valor)


def test_el_conector_sin_url_tambien_se_reporta():
    """Un conector nombrado sin literal de URL no puede desaparecer del informe."""
    modelo = ModeloQueSeCuelga(expresiones=["let x = Odbc.DataSource(cadena) in x"])
    with pytest.raises(RefreshTimeoutError) as exc:
        refresh._guardar_con_plazo(
            ServidorQueCancela(modelo), modelo, timeout_seconds=1)

    assert "Odbc.DataSource" in exc.value.details["sources_requiring_credentials"]


def test_end_to_end_refresh_model_corta_por_la_via_publica(session, monkeypatch):
    """La via publica -no solo el seam interno- deja de colgarse.

    Contra el codigo anterior esta prueba NO falla con un error: se queda
    colgada, que es exactamente el defecto (1800 s medidos). Por eso el doble
    libera el bloqueo a los 30 s como red de seguridad de la suite.
    """
    import contextlib

    modelo = ModeloQueSeCuelga(expresiones=[
        'SharePoint.Files("https://contoso.sharepoint.com/sites/x")'])
    servidor = ServidorQueCancela(modelo)

    @contextlib.contextmanager
    def lease_falso(_session):
        yield modelo

    @contextlib.contextmanager
    def connect_falso(_model):
        yield (servidor, None, modelo)

    monkeypatch.setattr(refresh, "lease_active_model", lease_falso)
    monkeypatch.setattr(refresh, "connect", connect_falso)
    monkeypatch.setattr(refresh, "load_tom",
                        lambda: type("T", (), {"RefreshType": type(
                            "R", (), {"Full": object()})()})())

    inicio = time.perf_counter()
    with pytest.raises(RefreshTimeoutError):
        refresh.refresh_model(session, "full", None, 1)
    assert time.perf_counter() - inicio < 10
