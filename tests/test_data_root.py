"""Donde caen `outputs/` y `backups/` cuando nadie los configura.

El defecto que cierran: las rutas por defecto se resolvian relativas a la raiz
del REPOSITORIO, calculada desde la posicion del propio `config.py`. Eso
funciona mientras se ejecuta desde un clon. Instalado con `pip`, `config.py`
vive en `site-packages/horizun_pbi_mcp/`, y esa supuesta "raiz" es el arbol de
librerias del entorno virtual: los respaldos de los proyectos Power BI del
usuario acababan en `...\\Lib\\site-packages\\backups` y desaparecian en la
siguiente reinstalacion. Un respaldo que se borra solo no es un respaldo.

Lo que se comprueba aqui es el comportamiento en los dos entornos, sin tener
que instalar nada: `running_from_checkout()` es la unica pieza que decide, y se
la puede poner en cualquiera de los dos estados moviendo `PROJECT_ROOT`.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from horizun_pbi_mcp import config


#: Estas dos pruebas describen el comportamiento **desde el clon**, y el
#: workflow de release corre la suite entera contra los BYTES INSTALADOS. Ahi
#: `running_from_checkout()` es `False` con toda la razon, y exigir lo contrario
#: convierte una prueba correcta en un rojo que bloquea la publicacion sin que
#: nada este mal. Lo destapo el propio release de 2.0.0.
#:
#: No se excluyen por nombre en el workflow: se omiten solas cuando el paquete
#: que se esta probando no es el del repositorio. Una prueba que sabe cuando no
#: aplica es mejor que una lista de exclusiones en un YAML que nadie mira.
desde_el_clon = pytest.mark.skipif(
    not config.running_from_checkout(),
    reason="el paquete bajo prueba no es el del clon (bytes instalados): la "
           "propiedad «desde el clon» no aplica")


@pytest.fixture
def instalado(monkeypatch, tmp_path):
    """Simula el paquete instalado: sin el pyproject.toml del repositorio."""
    falso_site_packages = tmp_path / "env" / "Lib" / "site-packages"
    falso_site_packages.mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_ROOT", falso_site_packages)
    return falso_site_packages


# ------------------------------------------------------- deteccion del clon ---
@desde_el_clon
def test_desde_el_repositorio_se_reconoce_el_clon():
    assert config.running_from_checkout() is True
    assert config.data_root() == config.PROJECT_ROOT


def test_instalado_no_se_confunde_con_un_clon(instalado):
    assert config.running_from_checkout() is False


def test_un_pyproject_ajeno_no_cuela_como_clon(monkeypatch, tmp_path):
    """En un monorepo de otro, o si copian el paquete dentro de otro proyecto,
    hay un `pyproject.toml` que no habla de nosotros."""
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "otra-cosa"\nversion = "1.0"\n', encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert config.running_from_checkout() is False


def test_sin_src_tampoco_es_un_clon(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "horizun-pbi-mcp"\n', encoding="utf-8")
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    assert config.running_from_checkout() is False


# --------------------------------------------------- raiz de datos instalado ---
def test_instalado_los_datos_no_caen_en_site_packages(instalado, monkeypatch):
    """El fallo concreto: respaldos dentro del arbol de librerias."""
    monkeypatch.setenv("LOCALAPPDATA", str(instalado.parent.parent / "AppData"))
    raiz = config.data_root()
    assert "site-packages" not in str(raiz)
    assert raiz.name == "horizun-pbi-mcp"


@pytest.mark.skipif(os.name != "nt", reason="ruta especifica de Windows")
def test_en_windows_usa_localappdata(instalado, monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config.data_root() == tmp_path / "Local" / "horizun-pbi-mcp"


@pytest.mark.skipif(os.name != "nt", reason="ruta especifica de Windows")
def test_en_windows_sin_localappdata_cae_al_perfil(instalado, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    raiz = config.data_root()
    assert raiz == Path.home() / "AppData" / "Local" / "horizun-pbi-mcp"


def test_sin_perfil_de_usuario_no_revienta(instalado, monkeypatch):
    """El caso que mato al servidor instalado en un entorno sin variables.

    `Path.home()` LANZA `RuntimeError` si no hay `USERPROFILE` ni `HOME`, y
    esta funcion se llama al arrancar: la excepcion mataba el proceso antes del
    primer mensaje del protocolo. Lo detecto el test de empaquetado, que lanza
    el servidor instalado con el entorno vaciado a proposito.
    """
    for var in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "_home_o_temporal", lambda: None)

    raiz = config.data_root()          # no debe lanzar
    assert raiz.name == "horizun-pbi-mcp"
    assert raiz.parent.is_dir(), "la raiz de reserva tiene que existir de verdad"


# ------------------------------------------------------------ el env manda ---
def test_las_variables_de_entorno_siguen_mandando(instalado, monkeypatch, tmp_path):
    """Quien configura una ruta la sigue teniendo, instalado o no."""
    destino = tmp_path / "mis_backups"
    monkeypatch.setenv("HORIZUN_PBI_MCP_BACKUPS_DIR", str(destino))
    monkeypatch.setattr(config, "_settings", None)
    ajustes = config.Settings.load()
    assert ajustes.backups_dir == destino


@desde_el_clon
def test_desde_el_clon_las_rutas_no_se_mueven(monkeypatch):
    """Quien ya lo usa desde el repositorio no tiene que migrar nada."""
    monkeypatch.delenv("HORIZUN_PBI_MCP_OUTPUTS_DIR", raising=False)
    monkeypatch.delenv("PBI_MCP_OUTPUTS_DIR", raising=False)
    monkeypatch.delenv("HORIZUN_PBI_MCP_BACKUPS_DIR", raising=False)
    monkeypatch.delenv("PBI_MCP_BACKUPS_DIR", raising=False)
    ajustes = config.Settings.load()
    assert ajustes.outputs_dir == config.PROJECT_ROOT / "outputs"
    assert ajustes.backups_dir == config.PROJECT_ROOT / "backups"
