"""Fase E4 — duplicar remapeando las referencias internas.

`duplicate_page()` copiaba cada visual con un id NUEVO y no tocaba nada mas.
Todo lo que apuntara a los ids viejos —interacciones, grupos, drillthrough,
navegacion— seguia apuntando a la pagina ORIGINAL. La copia se creaba sin
error y el destrozo solo se veia al abrir el informe.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator
from horizun_pbi_mcp.services import page_clone, pbir_edit
from horizun_pbi_mcp.services.page_clone import UnsupportedPageStructure
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


def con_interacciones(active):
    """Anade visualInteractions reales entre los dos visuales de la pagina.

    El fixture sintetico no las trae; sin ellas no habria nada que remapear y
    la prueba pasaria sin comprobar lo que importa.
    """
    pagina = pbir_reader.list_pages(active)[0]
    visuales = pbir_reader.list_visuals(active, pagina["name"])
    assert len(visuales) >= 2, "hacen falta dos visuales"

    ruta = Path(pbir_reader.pages_dir(active)) / pagina["name"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    # `type` sale del enum oficial (VisualInteractionFilterType); inventarlo
    # haria fallar la validacion por esquema, que es justo lo que queremos que
    # siga funcionando.
    datos["visualInteractions"] = [
        {"source": visuales[0]["id"], "target": visuales[1]["id"],
         "type": "NoFilter"},
        {"source": visuales[1]["id"], "target": visuales[0]["id"],
         "type": "DataFilter"},
    ]
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8", newline="\r\n")
    return pagina, visuales


# ================================================= el mapa y el remapeo =======
def test_las_interacciones_apuntan_a_los_nuevos_ids(proyecto):
    """REGRESION: antes seguian apuntando a los visuales de la pagina origen."""
    active, _raiz = proyecto
    pagina, visuales = con_interacciones(active)
    viejos = {v["id"] for v in visuales}

    r = pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")
    copia = Path(pbir_reader.pages_dir(active)) / r["page_id"] / "page.json"
    datos = json.loads(copia.read_text(encoding="utf-8-sig"))

    interacciones = datos.get("visualInteractions") or []
    assert interacciones, "la copia perdio las interacciones"
    for i in interacciones:
        assert i["source"] not in viejos, "source apunta al visual original"
        assert i["target"] not in viejos, "target apunta al visual original"
        assert i["source"] in r["id_map"].values()
        assert i["target"] in r["id_map"].values()


def test_los_ids_nuevos_son_unicos_y_distintos(proyecto):
    active, _raiz = proyecto
    pagina, visuales = con_interacciones(active)
    r = pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    nuevos = list(r["id_map"].values())
    assert len(nuevos) == len(set(nuevos)), "ids repetidos en la copia"
    assert not (set(nuevos) & {v["id"] for v in visuales})
    assert r["reference_check"]["clean"] is True


def test_no_queda_ningun_id_viejo_en_la_copia(proyecto):
    active, _raiz = proyecto
    pagina, visuales = con_interacciones(active)
    r = pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    destino = Path(pbir_reader.pages_dir(active)) / r["page_id"]
    texto = "\n".join(f.read_text(encoding="utf-8-sig")
                      for f in destino.rglob("*.json"))
    for v in visuales:
        assert f'"{v["id"]}"' not in texto, f"quedo el id viejo {v['id']}"
    assert f'"{pagina["name"]}"' not in texto


def test_el_id_propio_no_se_confunde_con_una_referencia():
    """`$.name` en la raiz es la identidad del documento, no una referencia."""
    mapa = {"viejo1": "nuevo1"}
    doc = {"name": "viejo1", "position": {"x": 0}}
    salida, pendientes = page_clone.remapear_documento(doc, mapa, {}, "visual.json")
    assert pendientes == [], "la identidad propia no es una referencia pendiente"


# ================================ estructura desconocida: se bloquea =========
def test_una_referencia_bajo_clave_desconocida_bloquea(proyecto):
    active, raiz = proyecto
    pagina, visuales = con_interacciones(active)
    antes = huella(raiz)

    ruta = Path(pbir_reader.pages_dir(active)) / pagina["name"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["algoQueNoConocemos"] = {"apuntaA": visuales[0]["id"]}
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8", newline="\r\n")
    tras_preparar = huella(raiz)

    with pytest.raises(UnsupportedPageStructure) as exc:
        pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    assert exc.value.code == "unsupported_page_structure"
    assert exc.value.details["unmapped_count"] >= 1
    assert huella(raiz) == tras_preparar, "no puede escribir al bloquear"


def test_un_id_suelto_en_una_lista_bloquea(proyecto):
    active, _raiz = proyecto
    pagina, visuales = con_interacciones(active)

    ruta = Path(pbir_reader.pages_dir(active)) / pagina["name"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["listaRara"] = [visuales[0]["id"]]
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8", newline="\r\n")

    with pytest.raises(UnsupportedPageStructure):
        pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")


def test_el_error_dice_donde_esta_la_referencia(proyecto):
    active, _raiz = proyecto
    pagina, visuales = con_interacciones(active)

    ruta = Path(pbir_reader.pages_dir(active)) / pagina["name"] / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["misterio"] = {"ref": visuales[0]["id"]}
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8", newline="\r\n")

    with pytest.raises(UnsupportedPageStructure) as exc:
        pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    pendiente = exc.value.details["unmapped"][0]
    assert pendiente["path"] == "$.misterio.ref"
    assert pendiente["file"] == "page.json"
    assert "known_keys" in exc.value.details


def test_no_se_reemplaza_a_ciegas():
    """Un id que aparece dentro de un literal no se sustituye: se denuncia."""
    mapa = {"abc123": "xyz789"}
    doc = {"expresion": "abc123", "position": {"x": 0}}
    _salida, pendientes = page_clone.remapear_documento(doc, mapa, {}, "v.json")
    assert len(pendientes) == 1
    assert pendientes[0]["key"] == "expresion"


# ============================================================ atomicidad ======
def test_un_fallo_al_duplicar_revierte_todo(proyecto, monkeypatch):
    active, raiz = proyecto
    pagina, _visuales = con_interacciones(active)
    antes = huella(raiz)

    from horizun_pbi_mcp.services import txn as txn_service

    original = txn_service.Transaction.write_json
    estado = {"n": 0}

    def falla(self, target, data):
        estado["n"] += 1
        if estado["n"] == 2:
            raise OSError("fallo inyectado")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", falla)
    with pytest.raises(Exception):
        pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    assert huella(raiz) == antes, "la duplicacion debe revertirse byte a byte"


def test_la_pagina_original_no_se_toca(proyecto):
    active, _raiz = proyecto
    pagina, _visuales = con_interacciones(active)
    origen_dir = Path(pbir_reader.pages_dir(active)) / pagina["name"]
    antes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
             for f in sorted(origen_dir.rglob("*.json"))}

    pbir_edit.duplicate_page(active, pagina["display_name"], "Copia")

    despues = {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
               for f in sorted(origen_dir.rglob("*.json"))}
    assert despues == antes, "duplicar modifico la pagina de origen"
