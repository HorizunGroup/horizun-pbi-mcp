"""Fase E5 — el fixture representativo es valido y sirve para lo que se creo.

El fixture `minimal` no tiene interacciones, ni marcadores, ni referencias que
remapear: varias pruebas pasaban sin comprobar lo que decian comprobar.

Estas pruebas verifican dos cosas distintas:
1. que el fixture sea PBIR **valido** (si no, las pruebas que lo usen fallarian
   por el fixture, no por el codigo);
2. que sea **sintetico**: ni un nombre, ni un GUID, ni una ruta de ningun
   proyecto real.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator
from services import pbir_schema
from tests.fixtures import rich


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = rich.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip(), pbip


# ================================================== es PBIR valido ============
def test_tiene_las_paginas_esperadas(proyecto):
    active, _pbip = proyecto
    nombres = {p["name"] for p in pbir_reader.list_pages(active)}
    assert {rich.PAGINA_PRINCIPAL, rich.PAGINA_DETALLE,
            rich.PAGINA_FUTURA} <= nombres


def test_los_json_cumplen_el_esquema_oficial(proyecto):
    """Los que declaran un esquema publicado. El de 2.10.0 se bloquea aparte."""
    if not pbir_schema.estado_cache()["ready"]:
        pytest.skip("los esquemas oficiales no estan instalados")

    _active, pbip = proyecto
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    comprobados = 0
    for f in rep.rglob("*.json"):
        datos = json.loads(f.read_text(encoding="utf-8-sig"))
        if not isinstance(datos, dict) or not datos.get("$schema"):
            continue
        if datos["$schema"] in pbir_schema.no_publicados():
            continue
        pbir_schema.validar(datos, archivo=f)      # no debe lanzar
        comprobados += 1
    assert comprobados >= 5, f"solo se validaron {comprobados} documentos"


def test_todos_los_json_usan_crlf(proyecto):
    """Es lo que escribe Power BI; con LF una huella byte a byte no sirve."""
    _active, pbip = proyecto
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    for f in rep.rglob("*.json"):
        crudo = f.read_bytes()
        if b"\n" not in crudo:
            continue
        assert b"\r\n" in crudo, f"{f.name} no usa CRLF"
        assert crudo.replace(b"\r\n", b"") .count(b"\n") == 0, (
            f"{f.name} mezcla CRLF y LF")


# ============================================== trae lo que decia traer =======
def test_hay_interacciones_reales(proyecto):
    active, _pbip = proyecto
    ruta = (Path(pbir_reader.pages_dir(active)) / rich.PAGINA_PRINCIPAL /
            "page.json")
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    interacciones = datos["visualInteractions"]
    assert len(interacciones) >= 3
    ids = {v["id"] for v in pbir_reader.list_visuals(active, rich.PAGINA_PRINCIPAL)}
    for i in interacciones:
        assert i["source"] in ids and i["target"] in ids


def test_hay_marcadores_que_referencian_la_pagina(proyecto):
    _active, pbip = proyecto
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    bookmark = (rep / "definition" / "bookmarks" /
                "bkvistainicial001.bookmark.json")
    assert bookmark.exists()
    datos = json.loads(bookmark.read_text(encoding="utf-8-sig"))
    assert datos["explorationState"]["activeSection"] == rich.PAGINA_PRINCIPAL
    assert rich.VISUAL_KPI in datos["explorationState"]["sections"][
        rich.PAGINA_PRINCIPAL]["visualContainers"]


def test_hay_un_visual_personalizado(proyecto):
    active, _pbip = proyecto
    tipos = {v.get("type") for v in
             pbir_reader.list_visuals(active, rich.PAGINA_PRINCIPAL)}
    assert rich.TIPO_PERSONALIZADO in tipos


def test_hay_una_referencia_rota(proyecto):
    active, _pbip = proyecto
    visuales = pbir_reader.list_visuals(active, rich.PAGINA_PRINCIPAL)
    roto = [v for v in visuales if v["id"] == rich.VISUAL_ROTO][0]
    assert any(rich.MEDIDA_ROTA in m for m in roto.get("measures", []))


def test_hay_una_pagina_con_esquema_no_publicado(proyecto):
    if not pbir_schema.estado_cache()["ready"]:
        pytest.skip("los esquemas oficiales no estan instalados")

    _active, pbip = proyecto
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    futuro = (rep / "definition" / "pages" / rich.PAGINA_FUTURA / "visuals" /
              "vsfuturo000000000006" / "visual.json")
    datos = json.loads(futuro.read_text(encoding="utf-8-sig"))

    # Antes esto bloqueaba y dejaba sin editar cualquier informe reciente.
    # Ahora se comprueba contra la version anterior de la familia y se deja
    # constancia de que la comprobacion fue aproximada.
    resultado = pbir_schema.validar(datos, archivo=futuro)
    assert resultado["validated"] is True
    assert resultado["degraded"] is True
    assert "2.7.0" in resultado["checked_against"]


def test_el_drillthrough_se_puede_anadir(proyecto):
    _active, pbip = proyecto
    rich.con_drillthrough(pbip)
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    datos = json.loads((rep / "definition" / "pages" / rich.PAGINA_DETALLE /
                        "page.json").read_text(encoding="utf-8-sig"))
    assert datos["pageBinding"]["type"] == "Drillthrough"


# ================================================== es sintetico de verdad ====
def test_no_contiene_nada_de_ningun_proyecto_real(proyecto):
    """Ni nombres, ni rutas, ni GUID reutilizados."""
    _active, pbip = proyecto
    texto = "\n".join(f.read_text(encoding="utf-8-sig")
                      for f in pbip.parent.rglob("*")
                      if f.is_file() and f.suffix in (".json", ".pbir", ".tmdl",
                                                      ".pbip"))
    prohibidos = ["PB4", "FinSesion", "Prodesa", "Control Room", "speckle",
                  "pablo", "OneDrive", "C:\\Users", "C:/Users"]
    for p in prohibidos:
        assert p.lower() not in texto.lower(), (
            f"el fixture contiene '{p}', que procede de un proyecto real")


def test_los_identificadores_son_inventados_y_legibles():
    """Un GUID copiado de un informe real podria colisionar con el suyo."""
    import re

    ids = [rich.PAGINA_PRINCIPAL, rich.PAGINA_DETALLE, rich.PAGINA_FUTURA,
           rich.VISUAL_KPI, rich.VISUAL_GRAFICO, rich.VISUAL_PERSONALIZADO,
           rich.VISUAL_ROTO, rich.VISUAL_DETALLE]
    for i in ids:
        assert not re.fullmatch(r"[0-9a-f]{20,32}", i), (
            f"'{i}' parece un GUID real, no un identificador inventado")
        assert i.isascii() and i.replace("_", "").isalnum()
    assert len(set(ids)) == len(ids), "identificadores repetidos"


def test_el_fixture_no_toca_el_versionado(tmp_path):
    """`materialize` trabaja sobre una copia, nunca sobre el fixture del repo."""
    from tests.fixtures import synthetic

    original = {f: f.stat().st_mtime
                for f in synthetic.MINIMAL_DIR.rglob("*") if f.is_file()}
    rich.materialize(tmp_path)
    despues = {f: f.stat().st_mtime
               for f in synthetic.MINIMAL_DIR.rglob("*") if f.is_file()}
    assert despues == original, "se modifico el fixture versionado"
