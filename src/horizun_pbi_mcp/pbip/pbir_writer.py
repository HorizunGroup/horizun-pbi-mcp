"""Escritura de bajo nivel sobre PBIR: visuales, posiciones y paginas.

Fase 1A. Toda escritura pasa por tres controles, en este orden:

1. `services._assert_escritura_pbir()` — no se escribe si Power BI
   Desktop puede tener el proyecto abierto (politica estricta: `open` y
   `unknown` bloquean).
2. `services.paths` — el id de pagina y el de visual son IDENTIFICADORES, no
   rutas: se rechaza cualquier sintaxis de ruta antes de combinarlos con el
   root, y el destino se revalida justo antes de escribir.
3. `services.txn` — transaccion compensada: fingerprint, journal, escritura
   durable, relectura y rollback si algo falla.

Un escritor puede recibir una transaccion ya abierta (`tx`) para que varias
escrituras formen una sola unidad logica; si no la recibe, abre la suya.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip.pbir_reader import pages_dir, resolve_page_dir
from horizun_pbi_mcp.services import paths as safe_paths
from horizun_pbi_mcp.services import project_state
from horizun_pbi_mcp.services import txn as txn_service
from horizun_pbi_mcp.utils.change_log import record_change
from horizun_pbi_mcp.utils.json_utils import read_json

log = get_logger("pbir_writer")

SCHEMA_VISUAL = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.7.0/schema.json"
SCHEMA_PAGE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
SCHEMA_PAGES = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json"


def _assert_escritura_pbir(active: ActivePbip, operation: str) -> None:
    """Puerta unica de escritura PBIR. Import diferido: pbir_edit nos importa."""
    from horizun_pbi_mcp.services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation)


def new_id() -> str:
    """Genera un id de 20 hex como los que usa Power BI para paginas/visuales."""
    return uuid.uuid4().hex[:20]


# GUID del visual "HTML Content" (dm-p, AppSource). Renderiza HTML/SVG desde una medida.
HTML_CONTENT_GUID = "htmlContent443BE3AD55E043BF878BED274D3A6855"


def _report_json_path(active: ActivePbip) -> Path:
    from horizun_pbi_mcp.pbip.pbir_reader import report_definition_dir

    return safe_paths.safe_join(report_definition_dir(active), "report.json",
                                kind="report.json")


def _visual_path(page_dir: Path, visual_id: str) -> Path:
    safe_paths.safe_identifier(visual_id, kind="id de visual")
    return safe_paths.safe_join(page_dir, "visuals", visual_id, "visual.json",
                                kind="ruta de visual")


def add_public_custom_visual(active: ActivePbip, visual_id: str,
                             do_backup: bool = True,
                             tx: Optional[txn_service.Transaction] = None) -> Dict[str, Any]:
    """Registra un custom visual de AppSource en report.json (publicCustomVisuals).

    Power BI Desktop lo descarga automaticamente al abrir el informe (requiere
    internet y que los visuales de AppSource esten permitidos).
    """
    report_json = _report_json_path(active)
    data = read_json(report_json)
    existing = data.get("publicCustomVisuals") or []
    if visual_id in existing:
        return {"visual_id": visual_id, "added": False,
                "note": "El visual ya estaba registrado."}

    _assert_escritura_pbir(active, operation="Registrar un custom visual")
    data["publicCustomVisuals"] = existing + [visual_id]

    if tx is not None:
        tx.write_json(report_json, data)
        result = None
    else:
        with txn_service.project_transaction(
                active, [report_json], tool="pbi_add_custom_visual") as t:
            t.write_json(report_json, data)
            result = t.summary()

    if do_backup and result:
        record_change("pbi_add_custom_visual",
                      f"Custom visual '{visual_id}' registrado en report.json.",
                      files=[str(report_json)], backup=result["journal"])
    return {"visual_id": visual_id, "added": True,
            "backup": result["journal"] if result else None,
            "transaction": result,
            "note": ("Power BI Desktop descargara el visual de AppSource al abrir "
                     "el informe (requiere internet).")}


def write_visual(active: ActivePbip, page: str, visual_dict: Dict[str, Any],
                 do_backup: bool = True,
                 tx: Optional[txn_service.Transaction] = None) -> Dict[str, Any]:
    page_dir = resolve_page_dir(active, page)
    vid = visual_dict.get("name") or new_id()
    visual_dict["name"] = vid
    visual_dict.setdefault("$schema", SCHEMA_VISUAL)
    target = _visual_path(page_dir, vid)

    if tx is not None:
        tx.write_json(target, visual_dict)
        result = None
    else:
        _assert_escritura_pbir(active, operation="Crear un visual")
        with txn_service.project_transaction(
                active, [target], tool="pbi_create_visual") as t:
            t.write_json(target, visual_dict)
            result = t.summary()

    if do_backup and result:
        record_change("pbi_create_visual",
                      f"Visual '{vid}' ({visual_dict.get('visual', {}).get('visualType')}) "
                      f"creado en pagina '{page}'.",
                      files=[str(target)], backup=result["journal"])
    return {"visual_id": vid, "file": str(target),
            "backup": result["journal"] if result else None,
            "transaction": result}


def update_visual_position(
    active: ActivePbip,
    page: str,
    visual_id: str,
    x: float,
    y: float,
    width: float,
    height: float,
    z: Optional[float] = None,
    do_backup: bool = True,
    tx: Optional[txn_service.Transaction] = None,
) -> Dict[str, Any]:
    page_dir = resolve_page_dir(active, page)
    target = _visual_path(page_dir, visual_id)
    if not target.exists():
        raise ValidationError(
            f"No existe el visual '{visual_id}' en la pagina '{page}'.")

    data = read_json(target)
    pos = dict(data.get("position", {}))
    before = dict(pos)
    pos["x"], pos["y"], pos["width"], pos["height"] = x, y, width, height
    if z is not None:
        pos["z"] = z
    data["position"] = pos

    if tx is not None:
        tx.write_json(target, data)
        result = None
    else:
        _assert_escritura_pbir(active, operation="Mover un visual")
        with txn_service.project_transaction(
                active, [target], tool="pbi_update_visual_position") as t:
            t.write_json(target, data)
            result = t.summary()

    if do_backup and result:
        record_change("pbi_update_visual_position",
                      f"Visual '{visual_id}' reposicionado en pagina '{page}'.",
                      files=[str(target)], backup=result["journal"])
    return {"visual_id": visual_id, "before": before, "after": pos,
            "backup": result["journal"] if result else None,
            "transaction": result}


def update_visual_filters(
    active: ActivePbip,
    page: str,
    visual_id: str,
    filters: List[Dict[str, Any]],
    do_backup: bool = True,
    tx: Optional[txn_service.Transaction] = None,
) -> Dict[str, Any]:
    """Reemplaza el `filterConfig` de un visual EXISTENTE.

    `filters` llega en el formato de `filter_builder.build_filter` (una lista
    vacia quita el filterConfig del visual por completo, no lo deja vacio:
    Power BI no distingue "sin filtros" de "filterConfig: {filters: []}", y
    dejar la clave vacia es basura que ningun lector espera).
    """
    from horizun_pbi_mcp.pbip import filter_builder

    page_dir = resolve_page_dir(active, page)
    target = _visual_path(page_dir, visual_id)
    if not target.exists():
        raise ValidationError(
            f"No existe el visual '{visual_id}' en la pagina '{page}'.")

    data = read_json(target)
    antes = data.get("filterConfig")
    nuevo = filter_builder.build_filter_config(filters)
    if nuevo is None:
        data.pop("filterConfig", None)
    else:
        data["filterConfig"] = nuevo

    if tx is not None:
        tx.write_json(target, data)
        result = None
    else:
        _assert_escritura_pbir(active, operation="Filtrar un visual")
        with txn_service.project_transaction(
                active, [target], tool="pbi_set_visual_filter") as t:
            t.write_json(target, data)
            result = t.summary()

    if do_backup and result:
        record_change("pbi_set_visual_filter",
                      f"Filtros del visual '{visual_id}' actualizados en "
                      f"pagina '{page}'.",
                      files=[str(target)], backup=result["journal"])
    return {"visual_id": visual_id, "before": antes, "after": nuevo,
            "backup": result["journal"] if result else None,
            "transaction": result}


def _existing_page_id(active: ActivePbip, display_name: str) -> Optional[str]:
    """Devuelve el id de una pagina con ese nombre visible, si ya existe."""
    for d in pages_dir(active).iterdir():
        if d.is_dir() and (d / "page.json").exists():
            try:
                if str(read_json(d / "page.json").get("displayName", "")).lower() == \
                        display_name.lower():
                    return d.name
            except ValidationError:
                continue
    return None


def _page_json(page_id: str, display_name: str, width: int, height: int) -> Dict[str, Any]:
    return {
        "$schema": SCHEMA_PAGE,
        "name": page_id,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "height": height,
        "width": width,
    }


def _pages_metadata(active: ActivePbip, page_id: str) -> Dict[str, Any]:
    pages_json_path = safe_paths.safe_join(pages_dir(active), "pages.json",
                                           kind="pages.json")
    if pages_json_path.exists():
        meta = read_json(pages_json_path)
    else:
        meta = {"$schema": SCHEMA_PAGES, "pageOrder": [], "activePageName": page_id}
    # Si el pages.json existente no declaraba $schema, se propagaba la carencia
    # al reescribirlo. Power BI SIEMPRE lo escribe, y sin el no hay forma de
    # saber contra que version validar el archivo que estamos produciendo.
    meta.setdefault("$schema", SCHEMA_PAGES)
    meta.setdefault("pageOrder", [])
    if page_id not in meta["pageOrder"]:
        meta["pageOrder"].append(page_id)
    meta.setdefault("activePageName", page_id)
    return meta


# --------------------------------------------------------------- API bulk ---
def plan_page_with_visuals(
    active: ActivePbip,
    display_name: str,
    width: int,
    height: int,
    visuals: List[Dict[str, Any]],
    *,
    page_id: Optional[str] = None,
    filter_config: Optional[Dict[str, Any]] = None,
    interactions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Calcula TODO lo que se escribiria, sin escribir ni comprobar permisos.

    Separar esto de `create_page_with_visuals` es lo que permite que un plan
    (`dry_run`) describa los bytes exactos que luego aplicara `pbi_apply_plan`,
    en vez de guardar el spec y recompilarlo al aplicar —que es como el plan de
    page spec acababa creando una pagina distinta de la aprobada—.

    `page_id` puede fijarse para que planificar y aplicar coincidan; si no se
    da, se genera uno nuevo.
    """
    ya_existe = _existing_page_id(active, display_name)
    if ya_existe is not None:
        return {"page_exists": True, "page_id": ya_existe, "files": {},
                "visuals": [], "ensure_dirs": [],
                "page_json_path": None, "pages_json_path": None}

    pid = page_id or new_id()
    pdir = pages_dir(active)
    page_json_path = safe_paths.safe_join(pdir, pid, "page.json",
                                          kind="ruta de pagina")
    pages_json_path = safe_paths.safe_join(pdir, "pages.json", kind="pages.json")

    planificados: List[Dict[str, Any]] = []
    for item in visuals:
        vdict = dict(item["visual"])
        vid = vdict.get("name") or new_id()
        vdict["name"] = vid
        vdict.setdefault("$schema", SCHEMA_VISUAL)
        page_dir = safe_paths.safe_join(pdir, pid, kind="ruta de pagina")
        planificados.append({"id": vid, "path": _visual_path(page_dir, vid),
                             "visual": vdict, "meta": item.get("meta", {})})

    pagina = _page_json(pid, display_name, width, height)
    if filter_config:
        pagina["filterConfig"] = filter_config
    if interactions:
        pagina["visualInteractions"] = interactions
    archivos: Dict[Path, Any] = {
        page_json_path: pagina,
        pages_json_path: _pages_metadata(active, pid),
    }
    for p in planificados:
        archivos[p["path"]] = p["visual"]

    return {"page_exists": False, "page_id": pid, "files": archivos,
            "visuals": planificados,
            "ensure_dirs": [str(pdir / pid / "visuals")],
            "page_json_path": page_json_path,
            "pages_json_path": pages_json_path}


def create_page_with_visuals(
    active: ActivePbip,
    display_name: str,
    width: int,
    height: int,
    visuals: List[Dict[str, Any]],
    *,
    tool: str,
    do_backup: bool = True,
) -> Dict[str, Any]:
    """Crea una pagina Y todos sus visuales en UNA SOLA transaccion.

    `visuals` son diccionarios de visual YA CONSTRUIDOS y con su posicion
    FINAL: esta funcion no construye nada ni recalcula posiciones. Quien llama
    debe haber validado todo antes, de modo que si algo no se puede construir
    no llegue a producirse ninguna escritura.

    Es la operacion PBIR multiarchivo mas grande: `page.json` + `pages.json` +
    N `visual.json`. Si falla cualquiera de ellos, se revierte el conjunto
    completo y la pagina no queda a medias.
    """
    materializado = plan_page_with_visuals(active, display_name, width, height,
                                           visuals)
    if materializado["page_exists"]:
        return {"page_id": materializado["page_id"], "created": False,
                "note": "La pagina ya existia.", "visuals_created": []}

    _assert_escritura_pbir(active, operation="Crear una pagina con visuales")

    page_id = materializado["page_id"]
    pdir = pages_dir(active)
    page_json_path = materializado["page_json_path"]
    pages_json_path = materializado["pages_json_path"]
    planificados = materializado["visuals"]

    targets = [page_json_path, pages_json_path] + [p["path"] for p in planificados]

    with txn_service.project_transaction(active, targets, tool=tool) as t:
        t.write_json(page_json_path, materializado["files"][page_json_path])
        t.write_json(pages_json_path, materializado["files"][pages_json_path])
        for p in planificados:
            t.write_json(p["path"], p["visual"])
        for d in materializado["ensure_dirs"]:
            t.ensure_directory(Path(d))
        result = t.summary()

    creados = [{"id": p["id"], "file": str(p["path"]), **p["meta"]}
               for p in planificados]
    if do_backup:
        record_change(tool,
                      f"Pagina '{display_name}' creada con {len(creados)} visuales "
                      "en una sola transaccion.",
                      files=[str(page_json_path)] + [str(p["path"]) for p in planificados],
                      backup=result["journal"])
    return {"page_id": page_id, "created": True, "display_name": display_name,
            "width": width, "height": height, "visuals_created": creados,
            "backup": result["journal"], "transaction": result}


def plan_visuals_bulk(active: ActivePbip, page: str,
                      updates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Calcula el JSON final de cada visual SIN escribir ni abrir transaccion.

    Separarlo permite que un workflow que toca VARIAS paginas compile todo y
    escriba en una sola transaccion. Antes `normalize_report` llamaba a
    `update_visuals_bulk` una vez por pagina: atomico por pagina, pero si
    fallaba la tercera, las dos primeras quedaban confirmadas.
    """
    page_dir = resolve_page_dir(active, page)
    planificados: List[Dict[str, Any]] = []
    for upd in updates:
        vid = upd["visual_id"]
        target = _visual_path(page_dir, vid)
        if not target.exists():
            raise ValidationError(
                f"No existe el visual '{vid}' en la pagina '{page}'.")
        data = read_json(target)
        pos = dict(data.get("position", {}))
        before = dict(pos)
        pos["x"], pos["y"] = upd["x"], upd["y"]
        pos["width"], pos["height"] = upd["width"], upd["height"]
        if upd.get("z") is not None:
            pos["z"] = upd["z"]
        data["position"] = pos
        planificados.append({"id": vid, "path": target, "data": data,
                             "before": before, "after": pos, "page": page})
    return planificados


def update_visuals_bulk(
    active: ActivePbip,
    page: str,
    updates: List[Dict[str, Any]],
    *,
    tool: str,
    do_backup: bool = True,
) -> Dict[str, Any]:
    """Reposiciona VARIOS visuales en UNA SOLA transaccion.

    `updates`: [{"visual_id", "x", "y", "width", "height", "z"?}].

    Se leen y validan todos los visuales, se construyen en memoria todos los
    JSON finales y solo entonces se escribe. Un cambio concurrente en
    cualquiera de ellos aborta el lote entero: no quedan posiciones
    parcialmente aplicadas.
    """
    planificados = plan_visuals_bulk(active, page, updates)

    if not planificados:
        return {"page": page, "moved": 0, "positions": [], "transaction": None}

    _assert_escritura_pbir(active, operation="Reposicionar visuales")

    # 2. Una sola transaccion sobre todos los visual.json.
    with txn_service.project_transaction(
            active, [p["path"] for p in planificados], tool=tool) as t:
        for p in planificados:
            t.write_json(p["path"], p["data"])
        result = t.summary()

    if do_backup:
        record_change(tool,
                      f"{len(planificados)} visuales reposicionados en '{page}' "
                      "en una sola transaccion.",
                      files=[str(p["path"]) for p in planificados],
                      backup=result["journal"])
    return {"page": page, "moved": len(planificados),
            "positions": [{"visual_id": p["id"], "before": p["before"],
                           "after": p["after"]} for p in planificados],
            "backup": result["journal"], "transaction": result}


def write_visual_with_registration(
    active: ActivePbip,
    page: str,
    custom_visual_guid: str,
    visual_dict: Dict[str, Any],
    *,
    tool: str,
    do_backup: bool = True,
) -> Dict[str, Any]:
    """Registra un custom visual y escribe el visual que lo usa, atomicamente.

    Son DOS archivos (`report.json` y `visual.json`) y forman una unidad: un
    visual que referencia un custom visual no registrado no se renderiza, y un
    registro sin visual ensucia el informe. Si falla cualquiera de los dos, se
    revierten ambos.
    """
    page_dir = resolve_page_dir(active, page)
    report_json = _report_json_path(active)

    data = read_json(report_json)
    existing = data.get("publicCustomVisuals") or []
    ya_registrado = custom_visual_guid in existing
    if not ya_registrado:
        data["publicCustomVisuals"] = existing + [custom_visual_guid]

    vid = visual_dict.get("name") or new_id()
    visual_dict["name"] = vid
    visual_dict.setdefault("$schema", SCHEMA_VISUAL)
    target = _visual_path(page_dir, vid)

    _assert_escritura_pbir(active, operation="Crear un visual HTML")

    targets = [target] if ya_registrado else [report_json, target]
    with txn_service.project_transaction(active, targets, tool=tool) as t:
        if not ya_registrado:
            t.write_json(report_json, data)
        t.write_json(target, visual_dict)
        result = t.summary()

    if do_backup:
        record_change(tool,
                      f"Visual '{vid}' creado y custom visual "
                      f"'{custom_visual_guid}' registrado en una sola transaccion.",
                      files=[str(report_json), str(target)],
                      backup=result["journal"])
    return {"visual_id": vid, "file": str(target),
            "custom_visual_registered": {"visual_id": custom_visual_guid,
                                         "added": not ya_registrado},
            "backup": result["journal"], "transaction": result}


def create_page(
    active: ActivePbip,
    display_name: str,
    width: int = 1280,
    height: int = 720,
    do_backup: bool = True,
    tx: Optional[txn_service.Transaction] = None,
) -> Dict[str, Any]:
    """Crea una pagina. Escribe DOS archivos: page.json y pages.json.

    Es la operacion multiarchivo del escritor PBIR: ambos archivos forman una
    unidad logica. Si falla el segundo, se revierte tambien el primero.
    """
    pdir = pages_dir(active)
    for d in pdir.iterdir():
        if d.is_dir() and (d / "page.json").exists():
            try:
                if str(read_json(d / "page.json").get("displayName", "")).lower() == \
                        display_name.lower():
                    return {"page_id": d.name, "created": False,
                            "note": "La pagina ya existia."}
            except ValidationError:
                pass

    page_id = new_id()
    page_json_path = safe_paths.safe_join(pdir, page_id, "page.json",
                                          kind="ruta de pagina")
    pages_json_path = safe_paths.safe_join(pdir, "pages.json", kind="pages.json")

    page_json = {
        "$schema": SCHEMA_PAGE,
        "name": page_id,
        "displayName": display_name,
        "displayOption": "FitToPage",
        "height": height,
        "width": width,
    }

    if pages_json_path.exists():
        meta = read_json(pages_json_path)
    else:
        meta = {"$schema": SCHEMA_PAGES, "pageOrder": [], "activePageName": page_id}
    # Los proyectos PBIR antiguos pueden no declararlo. Al reescribir el
    # documento hay que normalizarlo igual que hace ``_pages_metadata``;
    # conservar la omision impide validar la escritura y bloquea la pagina.
    meta.setdefault("$schema", SCHEMA_PAGES)
    meta.setdefault("pageOrder", [])
    if page_id not in meta["pageOrder"]:
        meta["pageOrder"].append(page_id)
    meta.setdefault("activePageName", page_id)

    def _apply(t: txn_service.Transaction) -> None:
        t.write_json(page_json_path, page_json)
        t.write_json(pages_json_path, meta)
        t.ensure_directory(pdir / page_id / "visuals")

    if tx is not None:
        _apply(tx)
        result = None
    else:
        _assert_escritura_pbir(active, operation="Crear una pagina")
        with txn_service.project_transaction(
                active, [page_json_path, pages_json_path],
                tool="pbi_create_page") as t:
            _apply(t)
            result = t.summary()

    if do_backup and result:
        record_change("pbi_create_page",
                      f"Pagina '{display_name}' creada ({width}x{height}).",
                      files=[str(page_json_path), str(pages_json_path)],
                      backup=result["journal"])
    return {"page_id": page_id, "created": True, "display_name": display_name,
            "width": width, "height": height,
            "backup": result["journal"] if result else None,
            "transaction": result}
