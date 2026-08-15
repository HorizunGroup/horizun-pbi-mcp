"""TEST-004: las pruebas live necesitan las DLL reales; el resto, no.

`isolated_settings` apunta `libs_dir` a un `tmp_path` vacio. Es lo correcto
para la suite unitaria -nadie deberia depender de lo que haya instalado en la
maquina de quien ejecuta- pero deja sin ADOMD a cualquier prueba que necesite
el motor tabular: `desktop_discovery` no puede leer `catalog` ni `table_count`,
toda instancia queda descartada, y la espera agota su plazo aunque Power BI
Desktop este sirviendo el modelo. Medido: 90/300 s de timeout donde el flujo
real termina en 10,9 s.

Estas pruebas fijan las dos mitades del arreglo: que `live_settings` preste las
DLL, y que no se las preste a nadie mas.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp import config

from tests.conftest import LIBS_MINIMAS, libs_reales


def test_isolated_settings_sigue_aislando_las_libs(isolated_settings, tmp_path):
    """La suite unitaria no puede depender de la maquina de quien la ejecuta."""
    assert isolated_settings.libs_dir == tmp_path / "libs"
    assert libs_reales() not in Path(isolated_settings.libs_dir).parents
    assert Path(isolated_settings.libs_dir) != libs_reales()


def test_live_settings_apunta_a_las_libs_reales(live_settings):
    assert Path(live_settings.libs_dir) == libs_reales()


def test_live_settings_exige_las_dll_minimas(live_settings):
    """Si la fixture no salto, las DLL que carga clr_bootstrap estan."""
    faltan = [d for d in LIBS_MINIMAS
              if not (Path(live_settings.libs_dir) / d).is_file()]
    assert not faltan, faltan


def test_live_settings_mantiene_lo_mutable_en_tmp_path(live_settings, tmp_path):
    """Presta las DLL en lectura; no presta donde se escribe."""
    for atributo in ("outputs_dir", "backups_dir"):
        ruta = Path(getattr(live_settings, atributo))
        assert tmp_path in ruta.parents or ruta == tmp_path, (
            f"{atributo} apunta fuera del temporal: {ruta}")
    assert Path(live_settings.libs_dir) not in (tmp_path,)


def test_live_settings_restaura_el_singleton(live_settings):
    """Durante la prueba el singleton es el de la fixture."""
    assert config._settings is live_settings


def test_una_prueba_posterior_no_hereda_live_settings(isolated_settings,
                                                      tmp_path):
    """Sin esto, una live dejaria las DLL reales a las que vengan detras."""
    assert config._settings is isolated_settings
    assert Path(isolated_settings.libs_dir) == tmp_path / "libs"


def test_sin_dll_el_skip_es_inmediato_y_dice_como_repararlo(monkeypatch,
                                                            tmp_path):
    """El fallo caro era el timeout mudo; este tiene que ser barato y claro."""
    import time

    from tests import conftest as cft

    vacio = tmp_path / "libs_vacias"
    vacio.mkdir()
    monkeypatch.setattr(cft, "libs_reales", lambda: vacio)

    from _pytest.outcomes import Skipped

    inicio = time.monotonic()
    try:
        gen = cft.live_settings.__wrapped__(tmp_path, monkeypatch)
        next(gen)
    except Skipped as exc:
        mensaje = str(exc)
    else:
        pytest.fail("la fixture no salto pese a no haber DLL")
    duracion = time.monotonic() - inicio

    assert "fetch_libs.py" in mensaje, (
        "el skip no dice como reparar la instalacion")
    assert str(vacio) in mensaje
    assert duracion < 1.0, f"el skip tardo {duracion:.2f}s; debe ser inmediato"
