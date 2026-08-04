"""Este repositorio es PUBLICO: nada de clientes ni del conocimiento interno.

Dos cosas distintas que no pueden aparecer aqui, y por motivos distintos:

1. **Nombres de clientes.** Un nombre propio no es un dato tecnico. No filtra
   cifras ni modelos, pero deja constancia publica de con quien se trabaja, y
   eso no es del repositorio: es del cliente. Aparecio de verdad -un logo y una
   razon social como cadenas de ejemplo en tests y comentarios- porque los
   ejemplos se escribieron copiando de un proyecto real.

2. **El CORE del equipo.** `HORIZUN CORE` es la base de conocimiento interna:
   viaja SOLO por el OneDrive de la empresa, contiene proyectos de clientes y
   su propia skill prohibe subirlo a un remoto publico. Hoy no hay ni una
   referencia -se verifico sobre la historia completa-, y este test existe para
   que siga asi: el riesgo no es lo que paso, es un `git add -A` distraido
   desde una sesion que tenia las dos cosas abiertas.

Se comprueba sobre los ficheros VERSIONADOS (`git ls-files`), que es
exactamente lo que se publica en GitHub y lo que viaja dentro del wheel.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Nombres de cliente que estuvieron y no deben volver. Se comparan sin
#: distinguir mayusculas: "Acme", "ACME Y CIA" y "acme-logo.png" son
#: el mismo problema.
CLIENTES = ("acme",)

#: Marcadores del CORE interno. Cualquiera de estos en un fichero versionado
#: significa que algo del arbol privado se copio al publico.
MARCADORES_CORE = (
    "HORIZUN CORE",
    "core.bundle",
    "_registro-sync",
    "horizun-jr@horizun.local",
    "OneDrive - Horizun",
    "HORIZUN-CORE-git",
)

#: Proyectos de cliente que viven en el CORE. Van aparte porque son palabras
#: comunes y se buscan como nombre propio completo.
PROYECTOS_CORE = (
    "Living Benevento", "MZ27B", "APU Macro", "Le Parc",
)

#: Este mismo fichero nombra lo prohibido para poder vigilarlo: excluirlo no es
#: hacer trampa, es la unica forma de que un guard se describa a si mismo.
EXCLUIDOS = {"tests/test_sin_datos_de_empresa.py"}


def _versionados() -> list[str]:
    salida = subprocess.run(["git", "ls-files"], cwd=str(REPO),
                            capture_output=True, text=True, timeout=120)
    if salida.returncode != 0:                       # pragma: no cover
        pytest.skip("git no disponible para listar los ficheros versionados")
    return [r for r in salida.stdout.splitlines()
            if r.strip() and r not in EXCLUIDOS]


def _texto(ruta: str) -> str:
    try:
        return (REPO / ruta).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):            # binarios: no aplican
        return ""


@pytest.fixture(scope="module")
def contenido() -> dict:
    return {r: _texto(r) for r in _versionados()}


@pytest.mark.parametrize("cliente", CLIENTES)
def test_ningun_nombre_de_cliente_en_el_repositorio(contenido, cliente):
    """Ni en codigo, ni en tests, ni en documentacion, ni en el CHANGELOG."""
    culpables = [r for r, t in contenido.items() if cliente in t.casefold()]
    assert not culpables, (
        f"'{cliente}' aparece en {culpables}. Este repositorio es publico: usa "
        "un nombre generico ('Acme') en los ejemplos.")


@pytest.mark.parametrize("marcador", MARCADORES_CORE)
def test_ninguna_referencia_al_core_interno(contenido, marcador):
    culpables = [r for r, t in contenido.items() if marcador.casefold() in t.casefold()]
    assert not culpables, (
        f"'{marcador}' aparece en {culpables}. El CORE del equipo es privado y "
        "viaja solo por el OneDrive de la empresa; nada suyo puede publicarse.")


@pytest.mark.parametrize("proyecto", PROYECTOS_CORE)
def test_ningun_proyecto_del_core_en_el_repositorio(contenido, proyecto):
    patron = re.compile(re.escape(proyecto), re.IGNORECASE)
    culpables = [r for r, t in contenido.items() if patron.search(t)]
    assert not culpables, (
        f"'{proyecto}' es un proyecto de cliente del CORE y aparece en "
        f"{culpables}.")


def test_no_se_versiona_la_marca_de_sincronizacion():
    """`.horizun/` la escribe el hook de sync del equipo en el cwd de turno.

    Es inocua por contenido (una fecha), pero delata la existencia del flujo
    interno y no pinta nada en un repositorio publico. Ya paso: el hook la creo
    aqui dentro y solo no se subio porque se excluyo a tiempo.
    """
    versionados = _versionados()
    marcas = [r for r in versionados if r.startswith(".horizun/")]
    assert not marcas, f"la marca de sync del equipo esta versionada: {marcas}"
