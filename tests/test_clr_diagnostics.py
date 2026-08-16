"""El estado del interop CLR: lo que `diagnostics()` dice y lo que se cree.

Dos defectos distintos vivian aqui, y ninguno lo habria visto la suite porque
NADIE probaba `_ensure_runtime` ni el check `clr` del health check.

1. `pbi_health_check` leia `clr_available` y `runtime`, dos claves que
   `diagnostics()` no produce. `bool(None)` es `False`: el check salia rojo en
   toda instalacion sana, con `detail: null`, y mandaba a diagnosticar un
   problema inexistente. Mientras tanto `pbi_list_desktop_models` reportaba
   `runtime_loaded: true` en el mismo proceso. Dos tools contradiciendose sobre
   el mismo estado.

2. `_ensure_runtime` trataba TODO `RuntimeError` de `pythonnet.load()` como "ya
   habia un runtime cargado". En pythonnet 3.x eso es exactamente al reves: el
   caso ya-cargado sale por `if _LOADED: return` SIN excepcion, y el
   `RuntimeError` queda reservado para los fallos de verdad -no se pudo crear
   el runtime, no se pudo inicializar `Python.Runtime.dll`-. El `except`
   convertia cada uno de esos fallos en un exito fingido, y como ademas hacia
   `return` dentro del bucle, el respaldo a `coreclr` era codigo muerto: una
   maquina sin .NET Framework nunca llegaba a intentar el otro runtime.

Las pruebas de `_ensure_runtime` sustituyen `pythonnet.load` porque el objetivo
es la LOGICA de decision, no el runtime de la maquina; una prueba que dependiera
del .NET instalado no podria distinguir un fallo de un entorno.
"""
from __future__ import annotations

import sys

import pytest

from horizun_pbi_mcp.powerbi import clr_bootstrap
from horizun_pbi_mcp.powerbi.errors import ClrNotAvailableError


@pytest.fixture(autouse=True)
def estado_limpio(monkeypatch):
    """Cada prueba arranca con el interop 'sin intentar todavia'.

    El estado es de modulo y el proceso de pytest es uno solo: sin esto, la
    primera prueba que cargue el runtime decidiria el resultado de las demas.
    """
    monkeypatch.setattr(clr_bootstrap, "_runtime_loaded", False)
    monkeypatch.setattr(clr_bootstrap, "_runtime_error", None, raising=False)
    yield


def _falso_load(fallos: dict):
    """`pythonnet.load` de mentira: falla en los runtimes que se le indiquen."""
    intentos = []

    def load(rt):
        intentos.append(rt)
        if rt in fallos:
            raise fallos[rt]

    load.intentos = intentos
    return load


def _instalar_load(monkeypatch, load):
    """`_ensure_runtime` hace `from pythonnet import load` DENTRO de la funcion."""
    modulo = sys.modules.setdefault("pythonnet", type(sys)("pythonnet"))
    monkeypatch.setattr(modulo, "load", load, raising=False)


# ------------------------------------------------- 1. el estado que se cree ---
def test_diagnostics_produce_las_claves_que_el_health_check_consume():
    """El defecto original en una linea: se leia lo que nadie escribia."""
    diag = clr_bootstrap.diagnostics()
    for clave in ("clr_state", "clr_detail", "runtime_loaded", "runtime_preference"):
        assert clave in diag, f"diagnostics() ya no produce '{clave}'"
    assert diag["clr_detail"], "el detalle nunca debe quedar vacio ni en None"


@pytest.mark.parametrize("cargado,error,esperado", [
    (False, None, "not_attempted"),
    (True, None, "loaded"),
    (False, "ClrNotAvailableError: no hay .NET", "failed"),
])
def test_los_tres_estados_del_interop(monkeypatch, cargado, error, esperado):
    """Cargado, fallado y 'todavia no se intento' son cosas distintas.

    Confundir las dos ultimas es lo que hacia permanente el aviso: el runtime
    se carga PEREZOSAMENTE, en la primera operacion contra un modelo, asi que
    un servidor recien arrancado esta legitimamente sin cargar.
    """
    monkeypatch.setattr(clr_bootstrap, "_runtime_loaded", cargado)
    monkeypatch.setattr(clr_bootstrap, "_runtime_error", error, raising=False)
    diag = clr_bootstrap.diagnostics()
    assert diag["clr_state"] == esperado
    assert diag["clr_detail"], "un estado sin explicacion no sirve para diagnosticar"
    if esperado == "failed":
        assert "no hay .NET" in diag["clr_detail"], (
            "el detalle de un fallo tiene que llevar la causa, no un rotulo")


# ------------------------------------------- 2. la decision sobre el runtime ---
def test_un_runtimeerror_no_se_toma_por_exito(monkeypatch):
    """El nucleo del segundo defecto: fallar no es 'ya estaba cargado'."""
    load = _falso_load({"netfx": RuntimeError("Failed to create a .NET runtime (netfx)"),
                        "coreclr": RuntimeError("No valid runtime selected")})
    _instalar_load(monkeypatch, load)

    with pytest.raises(ClrNotAvailableError):
        clr_bootstrap._ensure_runtime("netfx")
    assert clr_bootstrap._runtime_loaded is False, (
        "quedo marcado como cargado sin runtime: el error real aparecera mas "
        "tarde en clr.AddReference, culpando a la DLL equivocada")


def test_si_el_preferido_falla_se_intenta_el_otro(monkeypatch):
    """El respaldo a coreclr era codigo muerto: el `return` no lo dejaba correr."""
    load = _falso_load({"netfx": RuntimeError("Failed to create a .NET runtime (netfx)")})
    _instalar_load(monkeypatch, load)

    clr_bootstrap._ensure_runtime("netfx")
    assert load.intentos == ["netfx", "coreclr"]
    assert clr_bootstrap._runtime_loaded is True


def test_el_caso_ya_cargado_de_pythonnet_no_levanta_nada(monkeypatch):
    """Por que el `except` sobraba: `load()` vuelve limpio si ya estaba cargado.

    `pythonnet.load` empieza con `if _LOADED: return`. La rama que el codigo
    creia estar atendiendo nunca pasaba por una excepcion.
    """
    load = _falso_load({})
    _instalar_load(monkeypatch, load)

    clr_bootstrap._ensure_runtime("netfx")
    assert load.intentos == ["netfx"]
    assert clr_bootstrap._runtime_loaded is True


def test_un_fallo_deja_su_causa_registrada(monkeypatch):
    """Sin esto, el health check solo puede decir 'algo fallo'."""
    load = _falso_load({"netfx": RuntimeError("Failed to initialize Python.Runtime.dll"),
                        "coreclr": RuntimeError("No valid runtime selected")})
    _instalar_load(monkeypatch, load)

    with pytest.raises(ClrNotAvailableError):
        clr_bootstrap._ensure_runtime("netfx")

    diag = clr_bootstrap.diagnostics()
    assert diag["clr_state"] == "failed"
    assert "Python.Runtime.dll" in diag["clr_detail"] or \
           "No valid runtime" in diag["clr_detail"]
