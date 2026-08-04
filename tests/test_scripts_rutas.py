"""Las rutas del repo que declaran los scripts EXISTEN de verdad.

El bug que motiva esto: al mover todo el codigo al paquete unico
`horizun_pbi_mcp`, `scripts/fetch_pbir_schemas.py` siguio apuntando al arbol
viejo (`src/services/schemas/...`). Ningun test lo ejecutaba, asi que la suite
quedo en verde mientras fallaban los TRES sitios que si lo ejecutan: el
bootstrap del plugin (instalacion marcada `failed`), el paso de esquemas del CI
y la instruccion de instalacion manual del README. Y con `--update` habria
resucitado el arbol viejo, escribiendo donde el servidor ya no lee.

La leccion es general: un script que declara rutas del repositorio como
constantes se desfasa en silencio en cualquier reorganizacion. Aqui se cargan
esos modulos y se comprueba que cada ruta declarada apunte a algo que existe.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def _cargar(nombre: str):
    ruta = SCRIPTS / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{nombre}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    # Los scripts insertan src/ en sys.path por su cuenta; cargarlos no ejecuta
    # su main() porque __name__ no es __main__.
    spec.loader.exec_module(modulo)
    return modulo


def test_el_manifiesto_de_esquemas_existe_donde_el_script_lo_busca():
    """La regresion exacta: la constante apuntaba al arbol anterior."""
    modulo = _cargar("fetch_pbir_schemas")
    assert modulo.MANIFIESTO.is_file(), (
        f"fetch_pbir_schemas.py busca el manifiesto en {modulo.MANIFIESTO} "
        "y no existe. Si se movio el paquete, este script se quedo atras — "
        "es el que ejecutan el bootstrap del plugin, el CI y el README.")


def test_el_manifiesto_es_el_mismo_que_lee_el_servidor():
    """Dos rutas para el mismo archivo divergen; debe haber UNA."""
    modulo = _cargar("fetch_pbir_schemas")
    del_servidor = (REPO / "src" / "horizun_pbi_mcp" / "services" / "schemas"
                    / "pbir_manifest.json")
    assert modulo.MANIFIESTO.resolve() == del_servidor.resolve(), (
        "El script escribiria el manifiesto donde el servidor no lee: "
        f"script={modulo.MANIFIESTO} servidor={del_servidor}")


@pytest.mark.parametrize("script", ["fetch_libs", "make_mcp_config", "doctor"])
def test_las_rutas_de_repo_declaradas_existen(script):
    """Barrido: toda constante Path del modulo que caiga dentro del repo y
    parezca fuente (src/, scripts/) tiene que existir."""
    modulo = _cargar(script)
    rotas = []
    for nombre in dir(modulo):
        valor = getattr(modulo, nombre)
        if not isinstance(valor, Path):
            continue
        try:
            valor.relative_to(REPO)
        except ValueError:
            continue  # fuera del repo (destinos de descarga, temp): no aplica
        partes = valor.parts
        if "src" in partes or "scripts" in partes:
            if not valor.exists():
                rotas.append(f"{script}.{nombre} -> {valor}")
    assert not rotas, f"Constantes de ruta que apuntan a lo que ya no existe: {rotas}"
