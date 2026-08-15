"""`mode='auto'`: elegir destino mirando el estado, sin cambiar el default.

El problema: el default de `mode` es `live`, que exige Power BI Desktop
ABIERTO. Pero escribir el modelo exige Desktop CERRADO, y `both` esta
bloqueado. En el flujo que documenta `pbi_create_pbip_project` -construir un
tablero desde cero- el default fallaba SIEMPRE.

El default NO se cambia: cambiar un valor por defecto rompe el contrato
congelado y altera el comportamiento de clientes ya configurados sin que lo
pidan. Se anade `auto` como valor nuevo, que es aditivo.

La regla de resolucion es la del sistema, no una preferencia, y se resuelve en
`assert_mode_is_safely_executable` -o sea, ANTES de producir ningun efecto-,
que es lo que hace que esa funcion sirva para algo.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import dual_mode


class _Sesion:
    """Doble que modela lo que `resolver_auto` consulta de verdad.

    `viva=False` reproduce la sesion FANTASMA: `active_model` relleno -la
    sesion se restaura de outputs/session.json al arrancar- pero el Desktop al
    que apunta ya no existe, asi que verificarla lanza.
    """

    def __init__(self, modelo=None, pbip=None, viva=True):
        self.active_model = modelo
        self.active_pbip = pbip
        self._viva = viva

    def require_active_model(self):
        if self.active_model is None or not self._viva:
            raise RuntimeError("stale_session: ese Desktop ya no existe")
        return self.active_model


# ------------------------------------------------------------- se acepta ---
def test_auto_es_un_modo_valido():
    assert dual_mode.normalize_mode("auto") == "auto"
    assert "auto" in dual_mode.VALID_MODES


def test_el_default_sigue_siendo_live():
    """Cambiarlo romperia el contrato congelado."""
    assert dual_mode.normalize_mode(None) == "live"
    assert dual_mode.normalize_mode("") == "live"


def test_un_modo_inventado_sigue_fallando():
    with pytest.raises(ValidationError) as exc:
        dual_mode.normalize_mode("automatico")
    assert "auto" in str(exc.value)


# ---------------------------------------------------------- se resuelve ---
def test_con_sesion_viva_resuelve_a_live():
    sesion = _Sesion(modelo=object())
    assert dual_mode.resolver_auto(sesion) == "live"


def test_con_proyecto_cerrado_resuelve_a_pbip(monkeypatch):
    monkeypatch.setattr(dual_mode, "AUTO", "auto")  # no-op, fija el import
    from horizun_pbi_mcp.services import project_state

    monkeypatch.setattr(project_state, "detect", lambda _a: {"state": "closed"})
    sesion = _Sesion(pbip=object())
    assert dual_mode.resolver_auto(sesion) == "pbip"


def test_si_los_dos_son_posibles_gana_live(monkeypatch):
    """`live` es lo inmediato y no toca archivos."""
    from horizun_pbi_mcp.services import project_state

    monkeypatch.setattr(project_state, "detect", lambda _a: {"state": "closed"})
    sesion = _Sesion(modelo=object(), pbip=object())
    assert dual_mode.resolver_auto(sesion) == "live"


def test_una_sesion_guardada_pero_muerta_no_resuelve_a_live(monkeypatch):
    """La regresion del revisor: `active_model` relleno no es `live` viable.

    El escenario tipico de `auto` es exactamente este: se trabajo en live, se
    cerro Desktop -el estado REQUERIDO para escribir pbip- y se pide crear una
    medida. Resolver `live` por la sesion fantasma acabaria en
    StaleSessionError ordenando reabrir, cuando `pbip` era viable y era lo que
    el estado real permitia. `auto` promete mirar el estado REAL: se verifica,
    no se mira si el campo esta relleno.
    """
    from horizun_pbi_mcp.services import project_state

    monkeypatch.setattr(project_state, "detect", lambda _a: {"state": "closed"})
    sesion = _Sesion(modelo=object(), pbip=object(), viva=False)
    assert dual_mode.resolver_auto(sesion) == "pbip"


# ------------------------------------------------------- falla explicando ---
def test_sin_nada_abierto_dice_que_ejecutar():
    with pytest.raises(ValidationError) as exc:
        dual_mode.resolver_auto(_Sesion())
    mensaje = str(exc.value)
    assert "pbi_select_model" in mensaje and "pbi_open_pbip_project" in mensaje


def test_con_el_proyecto_abierto_en_desktop_dice_las_dos_salidas(monkeypatch):
    """El caso que mas confunde: hay proyecto, pero no se puede escribir."""
    from horizun_pbi_mcp.services import project_state

    monkeypatch.setattr(project_state, "detect", lambda _a: {"state": "open"})
    with pytest.raises(ValidationError) as exc:
        dual_mode.resolver_auto(_Sesion(pbip=object()))
    mensaje = str(exc.value)
    assert "pbi_select_model" in mensaje
    assert "cierra desktop" in mensaje.lower()


# ------------------------------------------- se resuelve antes de todo ---
def test_la_precondicion_resuelve_auto(monkeypatch):
    from horizun_pbi_mcp.services import project_state

    monkeypatch.setattr(project_state, "detect", lambda _a: {"state": "closed"})
    resuelto = dual_mode.assert_mode_is_safely_executable(
        "auto", _Sesion(pbip=object()))
    assert resuelto == "pbip", "run_dual solo entiende live|pbip"


def test_auto_sin_sesion_no_adivina():
    """Antes que elegir un destino a ciegas, se falla."""
    with pytest.raises(ValidationError):
        dual_mode.assert_mode_is_safely_executable("auto")


def test_both_sigue_bloqueado_con_sesion():
    with pytest.raises(dual_mode.DualModeNotAvailableError):
        dual_mode.assert_mode_is_safely_executable("both", _Sesion(modelo=object()))


def test_auto_se_resuelve_con_lo_que_detect_devuelve_de_verdad():
    """`detect` devuelve un ProjectOpenState, NO un dict.

    Las pruebas de arriba lo doblaban con `{"state": "closed"}`, asi que
    ninguna veia que `estado.get(...)` reventaba con AttributeError contra el
    objeto real -y salia como `unexpected`- en el unico camino que `auto`
    existe para resolver.
    """
    from horizun_pbi_mcp.services import project_state

    real = project_state.ProjectOpenState(
        project_state.CLOSED, "high", "Desktop no tiene el proyecto abierto")
    assert not hasattr(real, "get"), "si esto cambia, revisa los dobles"

    class _SesionConEstadoReal(_Sesion):
        pass

    import horizun_pbi_mcp.services.dual_mode as dm

    original = project_state.detect
    project_state.detect = lambda _a, **_k: real
    try:
        assert dm.resolver_auto(_SesionConEstadoReal(pbip=object())) == "pbip"
    finally:
        project_state.detect = original
