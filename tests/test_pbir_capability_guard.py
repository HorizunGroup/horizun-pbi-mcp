"""Fase E2 — el capability check de PBIR bloquea, no informa.

`report_capabilities` calculaba `supported_version` y NADIE lo miraba: el
servidor reescribia igual un informe de un formato que no entiende. Y ademas
declaraba soportado un informe SIN version, contradiciendo cualquier chequeo
posterior.

Escribir con la estructura equivocada corrompe el .pbip de una forma que Power
BI no siempre sabe explicar, asi que la politica es fail-closed: si no se puede
identificar el formato, no se escribe.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator
from services import pbir_edit
from services.pbir_edit import PbirVersionUnsupported
from tests.fixtures import synthetic


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, pbip.parent


def poner_version(active, valor):
    """Reescribe la version declarada en definition.pbir."""
    f = Path(active.report_dir) / "definition.pbir"
    datos = json.loads(f.read_text(encoding="utf-8-sig"))
    if valor is None:
        datos.pop("version", None)
    else:
        datos["version"] = valor
    f.write_text(json.dumps(datos, indent=2), encoding="utf-8")


def una_pagina(active):
    return pbir_reader.list_pages(active)[0]["display_name"]


# ================================================== la version soportada pasa ==
def test_la_version_soportada_permite_escribir(proyecto):
    active, raiz = proyecto
    assert pbir_edit.leer_version_pbir(active) == "4.0"
    r = pbir_edit.rename_page(active, una_pagina(active), "Renombrada")
    assert r["changed"] is True
    assert r["display_name"] == "Renombrada"


def test_report_capabilities_coincide_con_el_guard(proyecto):
    """Antes decia soportado con version None y el guard habria bloqueado."""
    active, _raiz = proyecto
    caps = pbir_edit.report_capabilities(active)
    assert caps["supported_version"] is True
    assert caps["writable"] is True

    poner_version(active, None)
    caps = pbir_edit.report_capabilities(active)
    assert caps["supported_version"] is False, (
        "sin version no puede declararse soportado: el guard bloquea")


# ================================================== lo desconocido se bloquea ==
@pytest.mark.parametrize("version,regla", [
    ("5.0", "version_desconocida_se_bloquea"),
    ("3.0", "version_desconocida_se_bloquea"),
    ("", "version_desconocida_se_bloquea"),
    (None, "sin_version_se_bloquea"),
])
def test_version_no_soportada_bloquea_la_escritura(proyecto, version, regla):
    active, raiz = proyecto
    poner_version(active, version)
    antes = huella(raiz)

    with pytest.raises(PbirVersionUnsupported) as exc:
        pbir_edit.rename_page(active, una_pagina(active), "NoDeberia")

    assert exc.value.code == "pbir_version_unsupported"
    assert exc.value.details["rule"] == regla
    assert huella(raiz) == antes, "no puede escribir nada al bloquear"


def test_sin_definition_pbir_se_bloquea(proyecto):
    """Carpetas parecidas a PBIR no son un PBIR que sepamos editar."""
    active, raiz = proyecto
    (Path(active.report_dir) / "definition.pbir").unlink()
    antes = huella(raiz)

    with pytest.raises(PbirVersionUnsupported) as exc:
        pbir_edit.rename_page(active, una_pagina(active), "NoDeberia")
    assert exc.value.details["rule"] == "sin_version_se_bloquea"
    assert huella(raiz) == antes


def test_definition_pbir_corrupto_se_bloquea(proyecto):
    active, raiz = proyecto
    (Path(active.report_dir) / "definition.pbir").write_text(
        "{ esto no es json", encoding="utf-8")

    with pytest.raises(PbirVersionUnsupported):
        pbir_edit.rename_page(active, una_pagina(active), "NoDeberia")


# ============================== el guard cubre TODAS las escrituras, no una ===
#: Operaciones que SI llegan a escribir sobre el fixture de una pagina.
#: `reorder_pages` con una sola pagina y `delete_page` de la ultima retornan
#: antes de escribir por motivos legitimos: no son un escape del guard.
ESCRITURAS = {
    "rename_page": lambda a, p: pbir_edit.rename_page(a, p, "Otra"),
    "duplicate_page": lambda a, p: pbir_edit.duplicate_page(a, p, "Copia"),
    "duplicate_visual": lambda a, p: pbir_edit.duplicate_visual(
        a, p, pbir_reader.list_visuals(a, p)[0]["id"]),
    "set_visual_title": lambda a, p: pbir_edit.set_visual_title(
        a, p, pbir_reader.list_visuals(a, p)[0]["id"], "Titulo nuevo"),
}


@pytest.mark.parametrize("nombre", sorted(ESCRITURAS))
def test_ninguna_escritura_pbir_se_escapa(proyecto, nombre):
    active, raiz = proyecto
    poner_version(active, "9.9")
    antes = huella(raiz)
    pagina = una_pagina(active)

    with pytest.raises(PbirVersionUnsupported):
        ESCRITURAS[nombre](active, pagina)
    assert huella(raiz) == antes, f"{nombre} escribio pese al bloqueo"


def test_todas_las_escrituras_pbir_invocan_el_guard():
    """Auditoria estatica: ninguna funcion que abra transaccion se lo salta.

    Cubre lo que la parametrizacion no alcanza —las que en este fixture
    retornan antes— sin fingir que se ejecutaron.
    """
    import ast
    import pathlib

    exentas = {"assert_escritura_pbir", "assert_pbir_soportado"}
    fallos = []
    for archivo in ("src/services/pbir_edit.py", "src/pbip/pbir_writer.py"):
        texto = pathlib.Path(archivo).read_text(encoding="utf-8")
        for nodo in ast.walk(ast.parse(texto)):
            if not isinstance(nodo, ast.FunctionDef) or nodo.name in exentas:
                continue
            src = ast.get_source_segment(texto, nodo) or ""
            abre_txn = "project_transaction(" in src
            tiene_guard = ("assert_escritura_pbir" in src
                           or "_assert_escritura_pbir" in src)
            if abre_txn and not tiene_guard:
                fallos.append(f"{archivo}::{nodo.name}")

    assert not fallos, (
        "estas funciones abren una transaccion PBIR sin pasar por el guard de "
        f"version: {fallos}")


def test_el_formato_se_comprueba_antes_que_desktop(proyecto, monkeypatch):
    """Si no sabemos escribirlo, da igual que Desktop este cerrado."""
    active, _raiz = proyecto
    poner_version(active, "9.9")

    from services import project_state

    llamadas = []
    original = project_state.assert_writable
    monkeypatch.setattr(project_state, "assert_writable",
                        lambda *a, **k: llamadas.append(1) or original(*a, **k))

    with pytest.raises(PbirVersionUnsupported):
        pbir_edit.assert_escritura_pbir(active, "prueba")
    assert llamadas == [], (
        "se comprobo el estado de Desktop antes que el formato")
