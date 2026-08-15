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


#: Donde vive ahora la logica de los descargadores. `scripts/fetch_*.py` son
#: envoltorios de una linea desde INSTALL-005: el wheel no lleva `scripts/`, y
#: el comando que `pbi_health_check` recomienda tiene que existir en la misma
#: instalacion que da el diagnostico.
COMPLETADO = REPO / "src" / "horizun_pbi_mcp" / "completado"


def _cargar(nombre: str, base: Path | None = None):
    ruta = (base or SCRIPTS) / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_script_{nombre}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    # Cargarlos no ejecuta su main(): __name__ no es __main__.
    spec.loader.exec_module(modulo)
    return modulo


def test_el_manifiesto_de_esquemas_existe_donde_el_modulo_lo_busca():
    """La regresion exacta: la constante apuntaba al arbol anterior."""
    modulo = _cargar("esquemas", COMPLETADO)
    assert modulo.MANIFIESTO.is_file(), (
        f"completado/esquemas.py busca el manifiesto en {modulo.MANIFIESTO} "
        "y no existe. Si se movio el paquete, este modulo se quedo atras — "
        "es el que ejecutan el bootstrap del plugin, el CI y el README.")


def test_el_manifiesto_es_el_mismo_que_lee_el_servidor():
    """Dos rutas para el mismo archivo divergen; debe haber UNA."""
    modulo = _cargar("esquemas", COMPLETADO)
    del_servidor = (REPO / "src" / "horizun_pbi_mcp" / "services" / "schemas"
                    / "pbir_manifest.json")
    assert modulo.MANIFIESTO.resolve() == del_servidor.resolve(), (
        "El modulo escribiria el manifiesto donde el servidor no lee: "
        f"modulo={modulo.MANIFIESTO} servidor={del_servidor}")


def test_el_manifiesto_de_las_dll_viaja_con_el_codigo_que_lo_lee():
    """Estaba en `scripts/`, que el wheel no lleva.

    Un `pip install` se quedaba sin la lista de versiones y hashes, o sea sin
    poder completarse a si mismo: es la otra mitad de INSTALL-005.
    """
    modulo = _cargar("libs", COMPLETADO)
    assert modulo.MANIFIESTO.is_file(), modulo.MANIFIESTO
    assert modulo.MANIFIESTO.parent == COMPLETADO, (
        "el manifiesto de las DLL tiene que vivir junto al modulo que lo lee, "
        f"y esta en {modulo.MANIFIESTO.parent}")


@pytest.mark.parametrize("nombre", ["fetch_libs", "fetch_pbir_schemas",
                                    "fetch_report_validator"])
def test_el_envoltorio_de_scripts_sigue_apuntando_a_algo_que_existe(nombre):
    """Los invoca el instalador del plugin POR RUTA, y el README los documenta.

    Un envoltorio que importa un modulo que se renombro falla en el sitio
    exacto donde nadie esta mirando: durante una instalacion desatendida.
    """
    modulo = _cargar(nombre)
    assert callable(modulo.main), f"{nombre}.py no expone main()"


@pytest.mark.parametrize("script", ["make_mcp_config", "doctor"])
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
