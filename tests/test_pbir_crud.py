"""Macrofase C — CRUD de visuales y paginas, y layout."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator
from powerbi.errors import ValidationError
from services import layout_doctor, pbir_edit, project_state
from services import txn as txn_service
from services.pbir_edit import UnsupportedPbirFeature
from tests.fixtures import synthetic

P = synthetic.PAGE_ID
CARD = synthetic.CARD_TEMPLATE_ID
COL = synthetic.COLUMN_TEMPLATE_ID


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip(), pbip.parent, isolated_settings


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


def journals(backups: Path) -> list:
    return sorted(backups.rglob("manifest.json"))


@pytest.fixture
def fallo_al_escribir(monkeypatch):
    def _instalar(n=0):
        original = txn_service.durable_write
        c = {"n": 0}

        def fake(path, data, validator=None):
            if Path(path).name == "visual.json":
                if c["n"] == n:
                    c["n"] += 1
                    raise OSError("fallo inyectado")
                c["n"] += 1
            return original(path, data, validator)

        monkeypatch.setattr(txn_service, "durable_write", fake)
    return _instalar


# ============================================================== inspeccion ===
def test_capacidades_pbir(proyecto):
    active, _p, _s = proyecto
    c = pbir_edit.report_capabilities(active)
    assert c["pbir_version"] == "4.0" and c["supported_version"] is True
    assert set(c["clonable_types"]) == {"card", "clusteredColumnChart"}
    assert c["visual_types_present"]["card"]["template"]["visual_id"] == CARD


def test_get_visual(proyecto):
    active, _p, _s = proyecto
    v = pbir_edit.get_visual(active, P, CARD)
    assert v["type"] == "card" and v["title"] == "Plantilla Tarjeta"
    assert v["measures"] == ["Fact[TotalAmount]"]
    assert v["has_container_format"] is True and v["raw"]["name"] == CARD


def test_get_visual_inexistente(proyecto):
    active, _p, _s = proyecto
    with pytest.raises(ValidationError):
        pbir_edit.get_visual(active, P, "noexiste000000000000")


# ========================================================= CRUD de visuales ===
def test_duplicar_visual(proyecto):
    active, project, settings = proyecto
    d = pbir_edit.duplicate_visual(active, P, CARD, new_title="Copia")
    assert d["visual_id"] != CARD
    assert len(pbir_reader.list_visuals(active, P)) == 3
    copia = pbir_edit.get_visual(active, P, d["visual_id"])
    assert copia["type"] == "card" and copia["title"] == "Copia"
    assert copia["measures"] == ["Fact[TotalAmount]"], "conserva los campos"
    assert copia["has_container_format"] is True, "conserva el formato"
    assert len(journals(settings.backups_dir)) == 1


def test_duplicar_desplaza_la_copia(proyecto):
    active, _p, _s = proyecto
    original = pbir_edit.get_visual(active, P, CARD)["position"]
    d = pbir_edit.duplicate_visual(active, P, CARD, offset=(50, 60))
    assert d["position"]["x"] == original["x"] + 50
    assert d["position"]["y"] == original["y"] + 60


def test_duplicar_a_otra_pagina(proyecto):
    active, _p, _s = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Destino")
    d = pbir_edit.duplicate_visual(active, P, CARD, target_page=dp["page_id"])
    assert d["page"] == dp["page_id"]
    assert any(v["id"] == d["visual_id"]
               for v in pbir_reader.list_visuals(active, dp["page_id"]))


def test_eliminar_visual_exige_confirm(proyecto):
    active, project, settings = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError):
        pbir_edit.delete_visual(active, P, CARD)
    assert huella(project) == antes
    assert journals(settings.backups_dir) == []


def test_eliminar_visual(proyecto):
    active, _p, _s = proyecto
    r = pbir_edit.delete_visual(active, P, CARD, confirm=True)
    assert r["deleted"] == CARD
    assert {v["id"] for v in pbir_reader.list_visuals(active, P)} == {COL}
    # El journal guarda rutas RELATIVAS al proyecto: una absoluta filtraria la
    # ruta personal del equipo y ademas no serviria si el .pbip se mueve.
    for archivo in r["transaction"]["files"]:
        assert not Path(archivo["path"]).is_absolute(), (
            f"el journal registro una ruta absoluta: {archivo['path']}")


def test_eliminar_visual_se_puede_revertir(proyecto, monkeypatch):
    """El journal conserva el original de un borrado."""
    active, project, settings = proyecto
    antes = huella(project)
    pbir_edit.delete_visual(active, P, CARD, confirm=True)
    assert len(pbir_reader.list_visuals(active, P)) == 1

    manifest = json.loads(journals(settings.backups_dir)[0].read_text(encoding="utf-8"))
    jdir = Path(manifest["source_root"])
    respaldo = journals(settings.backups_dir)[0].parent / "files"
    guardados = list(respaldo.rglob("visual.json"))
    assert guardados, "el original debe quedar en el journal"
    # Restauracion manual desde el journal: el contenido coincide byte a byte.
    destino = project / manifest["files"][0]["path"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(guardados[0].read_bytes())
    assert huella(project) == antes


def test_set_visual_title_preserva_formato(proyecto):
    active, _p, _s = proyecto
    ruta = pbir_edit._visual_file(active, P, CARD)
    antes = json.loads(ruta.read_text(encoding="utf-8"))
    props_antes = antes["visual"]["visualContainerObjects"]["title"][0]["properties"]
    assert "fontColor" in props_antes

    pbir_edit.set_visual_title(active, P, CARD, "Nuevo titulo")
    despues = json.loads(ruta.read_text(encoding="utf-8"))
    props = despues["visual"]["visualContainerObjects"]["title"][0]["properties"]
    assert props["text"]["expr"]["Literal"]["Value"] == "'Nuevo titulo'"
    assert "fontColor" in props, "el formato del titulo se conserva"


def test_orden_z(proyecto):
    active, _p, _s = proyecto
    r = pbir_edit.set_visual_z_order(active, P, [COL, CARD])
    assert r["z_order"][COL] == 0 and r["z_order"][CARD] == 1
    assert pbir_edit.get_visual(active, P, CARD)["z_order"] == 1


def test_orden_z_con_visual_inexistente(proyecto):
    active, project, _s = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError):
        pbir_edit.set_visual_z_order(active, P, ["noexiste000000000000"])
    assert huella(project) == antes


def test_reemplazar_campo(proyecto):
    active, _p, _s = proyecto
    r = pbir_edit.replace_visual_field(active, P, COL,
                                       "Calendar[Year]", "Calendar[MonthNumber]")
    assert r["count"] == 1
    v = pbir_edit.get_visual(active, P, COL)
    assert v["columns"] == ["Calendar[MonthNumber]"]


def test_reemplazar_campo_inexistente_falla(proyecto):
    active, project, settings = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError):
        pbir_edit.replace_visual_field(active, P, COL, "Fact[NoEsta]", "Fact[Amount]")
    assert huella(project) == antes, "no se escribe si no habia nada que sustituir"
    assert journals(settings.backups_dir) == []


def test_copiar_formato_entre_mismo_tipo(proyecto):
    active, _p, _s = proyecto
    d = pbir_edit.duplicate_visual(active, P, CARD)
    pbir_edit.set_visual_title(active, P, d["visual_id"], "Destino")
    r = pbir_edit.copy_visual_format(active, P, CARD, P, [d["visual_id"]])
    assert r["count"] == 1
    assert pbir_edit.get_visual(active, P, d["visual_id"])["title"] == "Destino", \
        "el texto del titulo es contenido, no formato: no se pisa"


def test_copiar_formato_entre_tipos_distintos_se_rechaza(proyecto):
    active, project, _s = proyecto
    antes = huella(project)
    with pytest.raises(UnsupportedPbirFeature) as exc:
        pbir_edit.copy_visual_format(active, P, CARD, P, [COL])
    assert exc.value.code == "pbir_feature_unsupported"
    assert huella(project) == antes


# ========================================================== CRUD de paginas ===
def test_duplicar_pagina(proyecto):
    active, _p, settings = proyecto
    r = pbir_edit.duplicate_page(active, P, "Copia")
    assert r["count"] == 2, "copia los dos visuales"
    assert len(pbir_reader.list_pages(active)) == 2
    nuevos = {v["id"] for v in pbir_reader.list_visuals(active, r["page_id"])}
    originales = {CARD, COL}
    assert not (nuevos & originales), "los ids de visual se regeneran"
    assert len(journals(settings.backups_dir)) == 1, "una sola transaccion"


def test_duplicar_pagina_con_nombre_repetido(proyecto):
    active, project, _s = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError):
        pbir_edit.duplicate_page(active, P, synthetic.PAGE_DISPLAY_NAME)
    assert huella(project) == antes


def test_eliminar_pagina_actualiza_metadatos(proyecto):
    active, project, _s = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Segunda")
    r = pbir_edit.delete_page(active, dp["page_id"], confirm=True)
    assert r["files_removed"] == 3, "page.json + 2 visuales"
    assert dp["page_id"] not in r["page_order"]
    assert len(pbir_reader.list_pages(active)) == 1
    meta = json.loads((project / "Demo.Report" / "definition" / "pages" /
                       "pages.json").read_text(encoding="utf-8"))
    assert dp["page_id"] not in meta["pageOrder"]


def test_eliminar_la_pagina_activa_reasigna(proyecto):
    active, project, _s = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Segunda")
    pages_json = project / "Demo.Report" / "definition" / "pages" / "pages.json"
    meta = json.loads(pages_json.read_text(encoding="utf-8"))
    meta["activePageName"] = dp["page_id"]
    pages_json.write_text(json.dumps(meta), encoding="utf-8")

    r = pbir_edit.delete_page(active, dp["page_id"], confirm=True)
    assert r["active_page_before"] == dp["page_id"]
    assert r["active_page_after"] == P, "la activa pasa a una que existe"


def test_no_se_elimina_la_ultima_pagina(proyecto):
    active, project, _s = proyecto
    antes = huella(project)
    with pytest.raises(ValidationError) as exc:
        pbir_edit.delete_page(active, P, confirm=True)
    assert "unica pagina" in exc.value.message
    assert huella(project) == antes


def test_eliminar_pagina_exige_confirm(proyecto):
    active, project, _s = proyecto
    pbir_edit.duplicate_page(active, P, "Segunda")
    antes = huella(project)
    with pytest.raises(ValidationError):
        pbir_edit.delete_page(active, "Segunda")
    assert huella(project) == antes


def test_renombrar_pagina(proyecto):
    active, _p, _s = proyecto
    r = pbir_edit.rename_page(active, P, "Resumen")
    assert r["changed"] is True and r["before"] == synthetic.PAGE_DISPLAY_NAME
    assert pbir_reader.list_pages(active)[0]["display_name"] == "Resumen"


def test_renombrar_al_mismo_nombre_no_escribe(proyecto):
    active, project, settings = proyecto
    antes = huella(project)
    r = pbir_edit.rename_page(active, P, synthetic.PAGE_DISPLAY_NAME)
    assert r["changed"] is False
    assert huella(project) == antes and journals(settings.backups_dir) == []


def test_renombrar_con_sintaxis_de_ruta_se_rechaza(proyecto):
    active, _p, _s = proyecto
    with pytest.raises(Exception):
        pbir_edit.rename_page(active, P, "../fuera")


def test_reordenar_paginas(proyecto):
    active, _p, _s = proyecto
    dp = pbir_edit.duplicate_page(active, P, "Segunda")
    r = pbir_edit.reorder_pages(active, [dp["page_id"], P])
    assert r["page_order"] == [dp["page_id"], P]
    assert [p["name"] for p in pbir_reader.list_pages(active)] == [dp["page_id"], P]


def test_reordenar_acepta_nombres_visibles(proyecto):
    active, _p, _s = proyecto
    pbir_edit.duplicate_page(active, P, "Segunda")
    r = pbir_edit.reorder_pages(active, ["Segunda"])
    assert len(r["page_order"]) == 2 and r["unspecified_kept_at_end"] == [P]


def test_reordenar_con_pagina_repetida(proyecto):
    active, _p, _s = proyecto
    with pytest.raises(ValidationError):
        pbir_edit.reorder_pages(active, [P, P])


# ================================================================= layout ====
def test_detecta_solapamiento():
    vis = [{"id": "a", "position": {"x": 0, "y": 0, "width": 200, "height": 100}},
           {"id": "b", "position": {"x": 100, "y": 50, "width": 200, "height": 100}}]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    solapes = [i for i in r["issues"] if i["rule"] == "layout_overlap"]
    assert len(solapes) == 1
    assert solapes[0]["evidence"]["overlap_width"] == 100


def test_detecta_fuera_del_lienzo():
    vis = [{"id": "a", "position": {"x": 1200, "y": 0, "width": 200, "height": 100}}]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    fuera = [i for i in r["issues"] if i["rule"] == "layout_out_of_canvas"]
    assert fuera and fuera[0]["severity"] == "error"
    assert fuera[0]["evidence"]["overflow"]["right"] == 120


def test_detecta_visual_minusculo():
    vis = [{"id": "a", "position": {"x": 20, "y": 20, "width": 10, "height": 10}}]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    assert any(i["rule"] == "layout_visual_too_small" for i in r["issues"])


def test_detecta_z_duplicado():
    vis = [{"id": "a", "position": {"x": 20, "y": 20, "width": 200, "height": 100, "z": 0}},
           {"id": "b", "position": {"x": 400, "y": 20, "width": 200, "height": 100, "z": 0}}]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    dup = [i for i in r["issues"] if i["rule"] == "layout_z_order_duplicated"]
    assert dup and dup[0]["evidence"]["count"] == 2


def test_detecta_pagina_vacia():
    r = layout_doctor.detect_issues([], {"width": 1280, "height": 720})
    assert any(i["rule"] == "layout_page_empty" for i in r["issues"])


def test_detecta_pagina_saturada():
    vis = [{"id": f"v{i}", "position": {"x": 20, "y": 20, "width": 100, "height": 80}}
           for i in range(15)]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    assert any(i["rule"] == "layout_page_crowded" for i in r["issues"])


def test_layout_limpio():
    vis = [{"id": "a", "position": {"x": 20, "y": 20, "width": 300, "height": 200, "z": 0}},
           {"id": "b", "position": {"x": 340, "y": 20, "width": 300, "height": 200, "z": 1}}]
    r = layout_doctor.detect_issues(vis, {"width": 1280, "height": 720})
    assert r["clean"] is True and r["issue_count"] == 0


@pytest.mark.parametrize("edge,esperado", [
    ("left", "x"), ("top", "y"), ("right", "x"), ("bottom", "y"),
])
def test_alinear(edge, esperado):
    vis = [{"id": "a", "position": {"x": 10, "y": 10, "width": 100, "height": 100}},
           {"id": "b", "position": {"x": 200, "y": 300, "width": 100, "height": 100}}]
    r = layout_doctor.align(vis, ["a", "b"], edge, {"width": 1280, "height": 720})
    assert len({p[esperado] for p in r}) == 1, f"todos deben compartir {esperado}"


def test_alinear_es_determinista():
    vis = [{"id": "a", "position": {"x": 10, "y": 10, "width": 100, "height": 100}},
           {"id": "b", "position": {"x": 200, "y": 300, "width": 100, "height": 100}}]
    canvas = {"width": 1280, "height": 720}
    assert (layout_doctor.align(vis, ["a", "b"], "left", canvas)
            == layout_doctor.align(vis, ["a", "b"], "left", canvas))


def test_alinear_exige_dos():
    vis = [{"id": "a", "position": {"x": 10, "y": 10, "width": 100, "height": 100}}]
    with pytest.raises(ValidationError):
        layout_doctor.align(vis, ["a"], "left", {"width": 1280, "height": 720})


def test_distribuir():
    vis = [{"id": f"{c}", "position": {"x": x, "y": 10, "width": 100, "height": 100}}
           for c, x in (("a", 0), ("b", 150), ("c", 600))]
    r = layout_doctor.distribute(vis, ["a", "b", "c"], "horizontal")
    huecos = [r[i + 1]["x"] - (r[i]["x"] + r[i]["width"]) for i in range(2)]
    assert abs(huecos[0] - huecos[1]) <= 1, "separacion uniforme"


def test_distribuir_exige_tres():
    vis = [{"id": "a", "position": {"x": 0, "y": 0, "width": 10, "height": 10}},
           {"id": "b", "position": {"x": 50, "y": 0, "width": 10, "height": 10}}]
    with pytest.raises(ValidationError):
        layout_doctor.distribute(vis, ["a", "b"], "horizontal")


def test_normalizar_corrige_solo_lo_necesario():
    vis = [{"id": "malo", "position": {"x": -10, "y": 0, "width": 20, "height": 20}},
           {"id": "bueno", "position": {"x": 100, "y": 100, "width": 300, "height": 200}}]
    r = layout_doctor.normalize(vis, {"width": 1280, "height": 720})
    assert {p["visual_id"] for p in r} == {"malo"}, "el que ya esta bien no se toca"
    corregido = r[0]
    assert corregido["x"] >= layout_doctor.MARGEN
    assert corregido["width"] >= layout_doctor.MIN_ANCHO


# ================================================= seguridad y fallos ========
@pytest.mark.parametrize("operacion", [
    lambda a: pbir_edit.duplicate_visual(a, P, CARD),
    lambda a: pbir_edit.delete_visual(a, P, CARD, confirm=True),
    lambda a: pbir_edit.set_visual_title(a, P, CARD, "X"),
    lambda a: pbir_edit.duplicate_page(a, P, "Nueva"),
    lambda a: pbir_edit.rename_page(a, P, "Otra"),
])
@pytest.mark.real_project_state
@pytest.mark.parametrize("estado", [project_state.OPEN, project_state.UNKNOWN])
def test_las_mutantes_respetan_la_politica_estricta(proyecto, monkeypatch,
                                                    operacion, estado):
    active, project, settings = proyecto
    antes = huella(project)
    monkeypatch.setattr(project_state, "detect",
                        lambda a, **k: project_state.ProjectOpenState(
                            estado, "high", "forzado"))
    with pytest.raises(project_state.ProjectOpenInDesktopError):
        operacion(active)
    assert huella(project) == antes
    assert journals(settings.backups_dir) == []


def test_fallo_al_duplicar_pagina_revierte_todo(proyecto, fallo_al_escribir):
    active, project, _s = proyecto
    antes = huella(project)
    fallo_al_escribir(1)          # falla el segundo visual de la copia
    with pytest.raises(Exception):
        pbir_edit.duplicate_page(active, P, "Copia")
    assert huella(project) == antes, "restauracion byte a byte"
    assert len(pbir_reader.list_pages(active)) == 1
    assert list(project.rglob("*.tmp")) == []


def test_cambio_concurrente_aborta_el_reemplazo(proyecto, monkeypatch):
    active, project, _s = proyecto
    ruta = pbir_edit._visual_file(active, P, COL)
    original = txn_service.durable_write

    def fake(path, data, validator=None):
        # Alguien toca el archivo entre el plan y la escritura.
        ruta.write_text('{"EXTERNO":true}', encoding="utf-8")
        monkeypatch.setattr(txn_service, "durable_write", original)
        return original(path, data, validator)

    plan = pbir_edit.get_visual(active, P, COL)      # lee antes
    monkeypatch.setattr(txn_service, "durable_write", fake)
    with pytest.raises(Exception):
        pbir_edit.replace_visual_field(active, P, COL,
                                       "Calendar[Year]", "Calendar[MonthNumber]")


# ============================ finales de linea del PBIR real ==================
def test_se_conserva_el_final_de_linea_del_archivo(proyecto, tmp_path):
    """Power BI escribe CRLF. Reescribir con LF cambia el archivo byte a byte.

    Lo detecto la prueba sobre un PBIP real: el contenido era identico pero
    `pages.json` dejaba de coincidir en huella, que es justo lo que se usa para
    demostrar que no se toco nada.
    """
    from utils.json_utils import detect_newline, write_json

    _active, project, _s = proyecto
    crlf = tmp_path / "crlf.json"
    crlf.write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    assert detect_newline(crlf) == b"\r\n"
    write_json(crlf, {"a": 2})
    assert b"\r\n" in crlf.read_bytes()
    assert b"\n" in crlf.read_bytes() and crlf.read_bytes().count(b"\r\n") > 0

    lf = tmp_path / "lf.json"
    lf.write_bytes(b'{\n  "a": 1\n}\n')
    assert detect_newline(lf) == b"\n"
    write_json(lf, {"a": 2})
    assert b"\r\n" not in lf.read_bytes(), "un archivo con LF se conserva con LF"


def test_un_archivo_nuevo_usa_crlf(tmp_path):
    """Por defecto se escribe como escribe Power BI."""
    from utils.json_utils import detect_newline

    assert detect_newline(tmp_path / "no_existe.json") == b"\r\n"
