"""Fases C2-C4 — actualizar una pagina EXISTENTE, no crear otra.

El defecto
----------
`diff_against_page()` sabia decir `change="update"`, pero `apply_spec()`
llamaba siempre a `create_page_with_visuals()`. Y esa funcion, al encontrar una
pagina con el mismo nombre, se limitaba a devolver `created: False` sin tocar
nada. Resultado: aplicar un spec sobre una pagina que ya existia no hacia NADA
y decia que todo habia ido bien.

Los cuatro desenlaces
---------------------
``create``     la pagina no existe: se crea.
``update``     existe y el spec cambia algo.
``no_change``  existe y el spec ya esta aplicado; no se escribe.
``conflict``   el nombre no identifica una sola pagina, o el destino no encaja.

Que se conserva
---------------
El **id de la pagina** siempre: cambiarlo romperia marcadores, navegacion y
cualquier referencia externa. Y el **id de cada visual** que siga representando
lo mismo, emparejado por firma (tipo + titulo + campos), no por posicion: mover
un visual no debe regenerar su id.

`sync_mode`
-----------
``merge`` (por defecto)  anade y actualiza; NO borra lo que el spec no menciona.
``replace``              ademas elimina los visuales ausentes del spec.

El defecto por defecto es el conservador: un spec parcial no puede vaciar una
pagina por omision.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from pbip import pbir_reader, pbir_writer
from services import paths as safe_paths

log = get_logger("page_update")

CREATE = "create"
UPDATE = "update"
NO_CHANGE = "no_change"
CONFLICT = "conflict"

MERGE = "merge"
REPLACE = "replace"
SYNC_MODES = (MERGE, REPLACE)


class PageConflict(PowerBIMCPError):
    """El destino no identifica una sola pagina, o no se puede actualizar."""

    code = "page_conflict"


def _firma(tipo: Optional[str], titulo: Optional[str],
           medidas, columnas) -> tuple:
    """Identidad logica de un visual: que es y que muestra.

    Deliberadamente NO incluye la posicion: mover un visual no lo convierte en
    otro, y si lo hiciera, cada reacomodo regeneraria ids y romperia las
    referencias que apuntan a ellos.
    """
    return (str(tipo or ""), str(titulo or ""),
            tuple(sorted(str(m) for m in (medidas or []))),
            tuple(sorted(str(c) for c in (columnas or []))))


def _firma_de_compilado(c: Dict[str, Any]) -> tuple:
    campos = pbir_reader._extract_fields(c["visual"].get("visual", {}))  # noqa: SLF001
    return _firma(c["meta"]["type"], c["meta"].get("title"),
                  campos["measures"], campos["columns"])


def _firma_de_existente(v: Dict[str, Any]) -> tuple:
    return _firma(v.get("type"), v.get("title"),
                  v.get("measures", []), v.get("columns", []))


def resolver_pagina(active: ActivePbip, nombre: str) -> Optional[str]:
    """Id de la pagina que corresponde a `nombre` (id o nombre visible).

    Un nombre visible repetido es un CONFLICTO, no una invitacion a elegir la
    primera: actualizar la pagina equivocada es peor que no actualizar.
    """
    paginas = pbir_reader.list_pages(active)
    por_id = [p for p in paginas if p["name"] == nombre]
    if por_id:
        return por_id[0]["name"]

    por_nombre = [p for p in paginas
                  if str(p.get("display_name") or "").lower() == str(nombre).lower()]
    if len(por_nombre) > 1:
        raise PageConflict(
            f"Hay {len(por_nombre)} paginas llamadas '{nombre}'. Indica el id "
            "para que no haya duda de cual se actualiza.",
            details={"name": nombre,
                     "candidates": [p["name"] for p in por_nombre]})
    return por_nombre[0]["name"] if por_nombre else None


def planificar(active: ActivePbip, compilado: Dict[str, Any], *,
               page: Optional[str] = None,
               sync_mode: str = MERGE) -> Dict[str, Any]:
    """Calcula el desenlace y TODO lo que se escribiria. No escribe nada."""
    if sync_mode not in SYNC_MODES:
        raise PageConflict(
            f"sync_mode invalido: {sync_mode!r}. Usa 'merge' o 'replace'.",
            details={"sync_mode": sync_mode, "valid": list(SYNC_MODES)})

    objetivo = page or compilado["page_name"]
    page_id = resolver_pagina(active, objetivo)

    if page_id is None:
        materializado = pbir_writer.plan_page_with_visuals(
            active, compilado["page_name"], compilado["canvas"]["width"],
            compilado["canvas"]["height"], compilado["visuals"],
            filter_config=compilado.get("page_filter_config"),
            interactions=compilado.get("page_interactions"))
        return {"change": CREATE, "page_id": materializado["page_id"],
                "files": materializado["files"], "deletes": [],
                "ensure_dirs": materializado["ensure_dirs"],
                "kept": [], "added": [c["meta"].get("title")
                                      for c in compilado["visuals"]],
                "updated": [], "removed": [],
                "sync_mode": sync_mode}

    return _planificar_update(active, compilado, page_id, sync_mode)


def _planificar_update(active: ActivePbip, compilado: Dict[str, Any],
                       page_id: str, sync_mode: str) -> Dict[str, Any]:
    pdir = pbir_reader.pages_dir(active)
    page_dir = safe_paths.safe_join(pdir, page_id, kind="ruta de pagina")
    page_json = safe_paths.safe_join(page_dir, "page.json", kind="page.json")

    from utils.json_utils import read_json

    actual_pagina = read_json(page_json)
    existentes = pbir_reader.list_visuals(active, page_id, strict=True)

    # Emparejado por firma. Cada existente se consume una sola vez, para que
    # dos visuales identicos no se emparejen ambos con el mismo del spec.
    disponibles: Dict[tuple, List[Dict[str, Any]]] = {}
    for v in existentes:
        disponibles.setdefault(_firma_de_existente(v), []).append(v)

    archivos: Dict[Path, Any] = {}
    kept, added, updated = [], [], []
    usados: set = set()

    for c in compilado["visuals"]:
        firma = _firma_de_compilado(c)
        pareja = None
        cola = disponibles.get(firma) or []
        while cola:
            candidato = cola.pop(0)
            if candidato["id"] not in usados:
                pareja = candidato
                break

        datos = copy.deepcopy(c["visual"])
        if pareja is not None:
            # Se CONSERVA el id: es lo que mantiene vivas las referencias.
            datos["name"] = pareja["id"]
            usados.add(pareja["id"])
            destino = Path(pareja["file"])
            anterior = read_json(destino)
            if anterior == datos:
                kept.append(pareja["id"])
                continue
            updated.append(pareja["id"])
        else:
            vid = datos.get("name") or pbir_writer.new_id()
            datos["name"] = vid
            destino = safe_paths.safe_join(page_dir, "visuals", vid,
                                           "visual.json", kind="ruta de visual")
            added.append(vid)
        datos.setdefault("$schema", pbir_writer.SCHEMA_VISUAL)
        archivos[destino] = datos

    sobrantes = [v for v in existentes if v["id"] not in usados]
    borrados: List[Path] = []
    if sync_mode == REPLACE:
        borrados = [Path(v["file"]) for v in sobrantes]

    # page.json: solo lo que el spec declara.
    nueva_pagina = dict(actual_pagina)
    nueva_pagina["displayName"] = compilado["page_name"]
    nueva_pagina["width"] = compilado["canvas"]["width"]
    nueva_pagina["height"] = compilado["canvas"]["height"]
    nueva_pagina["name"] = page_id          # el id NO cambia
    # Filtros e interacciones: se declaran o se quitan segun el spec, para que
    # aplicar dos veces el mismo spec deje siempre el mismo resultado.
    for clave, valor in (("filterConfig", compilado.get("page_filter_config")),
                         ("visualInteractions", compilado.get("page_interactions"))):
        if valor:
            nueva_pagina[clave] = valor
        else:
            nueva_pagina.pop(clave, None)
    if nueva_pagina != actual_pagina:
        archivos[page_json] = nueva_pagina

    cambia = bool(archivos or borrados)
    return {
        "change": UPDATE if cambia else NO_CHANGE,
        "page_id": page_id,
        "files": archivos,
        "deletes": borrados,
        "ensure_dirs": [],
        "kept": kept, "added": added, "updated": updated,
        "removed": [v["id"] for v in (sobrantes if sync_mode == REPLACE else [])],
        "not_removed": [v["id"] for v in (sobrantes if sync_mode == MERGE else [])],
        "sync_mode": sync_mode,
    }


def resumen(plan: Dict[str, Any]) -> str:
    if plan["change"] == CREATE:
        return f"Se creara la pagina con {len(plan['added'])} visual(es)."
    if plan["change"] == NO_CHANGE:
        return "La pagina ya coincide con el spec: no hay nada que escribir."
    partes = [f"{len(plan['added'])} nuevo(s)", f"{len(plan['updated'])} modificado(s)",
              f"{len(plan['kept'])} sin cambios"]
    if plan.get("removed"):
        partes.append(f"{len(plan['removed'])} eliminado(s)")
    if plan.get("not_removed"):
        partes.append(f"{len(plan['not_removed'])} conservado(s) por sync_mode=merge")
    return ", ".join(partes) + "."
