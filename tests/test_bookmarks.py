"""Marcadores: guardar un estado del informe y volver a el.

Dos trampas del formato que se congelan aqui, ambas verificadas contra un
informe real:

- Dentro de un marcador el filtro usa `expression`, NO `field` como en
  `filterConfig`. Son estructuras parecidas con nombres distintos.
- Sin entrada en `bookmarks.json`, Power BI no muestra el marcador aunque el
  archivo exista.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbip import bookmarks, project_locator
from pbip.bookmarks import BookmarkError


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _pagina(activo) -> str:
    from pbip import pbir_reader

    return pbir_reader.list_pages(activo)[0]["name"]


def test_se_escribe_el_marcador_y_su_indice(proyecto):
    """Sin indice, Power BI no lo muestra aunque el archivo este."""
    r = bookmarks.create_bookmark(proyecto, "Vista ejecutiva", _pagina(proyecto))

    archivo = json.loads(Path(r["file"]).read_text(encoding="utf-8-sig"))
    assert archivo["displayName"] == "Vista ejecutiva"
    assert archivo["explorationState"]["activeSection"] == _pagina(proyecto)
    # `sections` es obligatorio aunque no se guarde estado por visual
    assert _pagina(proyecto) in archivo["explorationState"]["sections"]

    indice = json.loads(Path(r["index"]).read_text(encoding="utf-8-sig"))
    assert any(i["name"] == r["name"] for i in indice["items"])


def test_el_marcador_valida_contra_su_esquema_oficial(proyecto):
    """El esquema que falta es `bookmarks/` (plural); estos dos si estan."""
    from services import pbir_schema

    if not pbir_schema.estado_cache()["ready"]:
        pytest.skip("los esquemas oficiales no estan instalados")

    r = bookmarks.create_bookmark(proyecto, "Con esquema", _pagina(proyecto))
    for ruta in (r["file"], r["index"]):
        datos = json.loads(Path(ruta).read_text(encoding="utf-8-sig"))
        assert pbir_schema.validar(datos, archivo=Path(ruta))["validated"]


def test_el_filtro_de_un_marcador_usa_expression_no_field(proyecto):
    """Usar la clave de al lado produce un marcador que no restaura nada."""
    r = bookmarks.create_bookmark(
        proyecto, "Filtrado", _pagina(proyecto),
        filters=[{"field": "Ventas[Region]", "values": ["Sur"]}])
    estado = json.loads(Path(r["file"]).read_text(encoding="utf-8-sig"))["explorationState"]
    filtro = estado["filters"]["byExpr"][0]
    assert "expression" in filtro and "field" not in filtro
    assert filtro["expression"]["Column"]["Property"] == "Region"
    # y dentro de la consulta la tabla sigue yendo por alias
    origen = (filtro["filter"]["Where"][0]["Condition"]["In"]["Expressions"][0]
              ["Column"]["Expression"]["SourceRef"])
    assert "Source" in origen


def test_acepta_el_titulo_de_la_pagina_ademas_del_id(proyecto):
    from pbip import pbir_reader

    titulo = pbir_reader.list_pages(proyecto)[0]["display_name"]
    r = bookmarks.create_bookmark(proyecto, "Por titulo", titulo)
    assert r["page"] == _pagina(proyecto)


def test_una_pagina_inexistente_lista_las_que_hay(proyecto):
    with pytest.raises(BookmarkError) as exc:
        bookmarks.create_bookmark(proyecto, "X", "pagina_fantasma")
    assert exc.value.details["pages"]


def test_visuales_objetivo_se_comprueban(proyecto):
    with pytest.raises(BookmarkError) as exc:
        bookmarks.create_bookmark(proyecto, "X", _pagina(proyecto),
                                  target_visuals=["no_existe"])
    assert "no_existe" in str(exc.value)


def test_devuelve_como_enlazarlo_a_un_boton(proyecto):
    r = bookmarks.create_bookmark(proyecto, "Vista", _pagina(proyecto))
    assert r["usage"]["options"]["action"] == "bookmark"
    assert r["usage"]["options"]["bookmark"] == r["name"]


def test_duplicado_exige_permiso(proyecto):
    r = bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto))
    with pytest.raises(BookmarkError):
        bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto), name=r["name"])


def test_listar_detecta_lo_que_no_cuadra(proyecto):
    r = bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto))
    suelto = Path(r["file"]).parent / "Huerfano.bookmark.json"
    suelto.write_text(json.dumps({"name": "Huerfano", "displayName": "H"}),
                      encoding="utf-8")

    listado = bookmarks.list_bookmarks(proyecto)
    assert "Huerfano" in [b["name"] for b in listado["not_indexed"]]
    assert listado["missing_files"] == []


def test_borrar_lo_quita_de_los_dos_sitios(proyecto):
    r = bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto))
    bookmarks.delete_bookmark(proyecto, r["name"])

    assert not Path(r["file"]).exists()
    indice = json.loads(Path(r["index"]).read_text(encoding="utf-8-sig"))
    assert not any(i["name"] == r["name"] for i in indice["items"])


def test_borrar_lo_que_no_existe_lo_dice(proyecto):
    with pytest.raises(BookmarkError):
        bookmarks.delete_bookmark(proyecto, "no_existe")


def test_fallo_al_indexar_revierte_el_marcador(proyecto, monkeypatch):
    """El archivo no puede sobrevivir si falla su entrada en el indice."""
    from services import txn as txn_service

    carpeta = Path(proyecto.report_dir) / "definition" / "bookmarks"
    original = txn_service.Transaction.write_json

    def fallar_en_indice(self, target, data):
        if Path(target).name == "bookmarks.json":
            raise RuntimeError("fallo inyectado en el indice")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", fallar_en_indice)
    with pytest.raises(RuntimeError, match="fallo inyectado"):
        bookmarks.create_bookmark(
            proyecto, "Atomico", _pagina(proyecto), name="BookmarkAtomico")

    assert not (carpeta / "BookmarkAtomico.bookmark.json").exists()
    assert not (carpeta / "bookmarks.json").exists()


def test_fallo_al_actualizar_indice_revierte_el_borrado(proyecto, monkeypatch):
    from services import txn as txn_service

    creado = bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto))
    archivo = Path(creado["file"])
    indice = Path(creado["index"])
    antes_archivo = archivo.read_bytes()
    antes_indice = indice.read_bytes()
    original = txn_service.Transaction.write_json

    def fallar_en_indice(self, target, data):
        if Path(target) == indice:
            raise RuntimeError("fallo inyectado al borrar del indice")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", fallar_en_indice)
    with pytest.raises(RuntimeError, match="fallo inyectado"):
        bookmarks.delete_bookmark(proyecto, creado["name"])

    assert archivo.read_bytes() == antes_archivo
    assert indice.read_bytes() == antes_indice


def test_borrar_marcador_no_admite_traversal(proyecto):
    from powerbi.errors import PathSecurityError

    victima = Path(proyecto.report_dir) / "fuera.bookmark.json"
    victima.write_bytes(b"NO TOCAR")
    with pytest.raises(PathSecurityError):
        bookmarks.delete_bookmark(proyecto, "../../fuera")
    assert victima.read_bytes() == b"NO TOCAR"


@pytest.mark.real_project_state
def test_marcadores_no_se_escriben_con_desktop_abierto(
        proyecto, monkeypatch):
    from services import project_state

    monkeypatch.setattr(
        project_state, "detect",
        lambda a, **kw: project_state.ProjectOpenState(
            project_state.CLOSED, "high", "forzado"))
    creado = bookmarks.create_bookmark(proyecto, "V", _pagina(proyecto))
    archivo = Path(creado["file"])
    indice = Path(creado["index"])
    antes_archivo = archivo.read_bytes()
    antes_indice = indice.read_bytes()

    monkeypatch.setattr(
        project_state, "detect",
        lambda a, **kw: project_state.ProjectOpenState(
            project_state.OPEN, "high", "forzado"))
    project_state.invalidate_cache()
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        bookmarks.create_bookmark(
            proyecto, "Bloqueado", _pagina(proyecto), name="BookmarkBloqueado")
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        bookmarks.delete_bookmark(proyecto, creado["name"])

    assert archivo.read_bytes() == antes_archivo
    assert indice.read_bytes() == antes_indice
