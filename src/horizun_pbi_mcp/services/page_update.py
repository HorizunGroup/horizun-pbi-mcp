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

Lo conservador tiene un precio, y hay que decirlo
------------------------------------------------
Si el lienzo del spec no es el de la pagina —se recompuso con otro sistema de
diseno— los visuales que `merge` conserva se quedan con las coordenadas del
lienzo VIEJO. Los que no caben en el nuevo no desaparecen: quedan fuera de
limites, invisibles al abrir el informe pero presentes en el render y en la
publicacion, y solo se descubren con `pbi_detect_layout_issues`. Paso de
verdad al pasar de 1920x1080 a 1280x720. Ahora se avisa ANTES, con la cuenta
hecha y con las dos salidas: `sync_mode='replace'` o `pbi_reflow_pages`.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.pbip import pbir_reader, pbir_writer
from horizun_pbi_mcp.services import paths as safe_paths

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


def _avisar_de_los_que_quedan_fuera(sobrantes: List[Dict[str, Any]],
                                    pagina_actual: Dict[str, Any],
                                    canvas_nuevo: Dict[str, Any]
                                    ) -> Dict[str, Any]:
    """Los que `merge` conserva y ya no caben en el lienzo del spec.

    Solo cuenta como fuera de limites lo que se sale del lienzo NUEVO: si el
    lienzo no cambia, un visual que ya estaba mal colocado se avisa igual, que
    para el que lee es el mismo problema.
    """
    ancho = float(canvas_nuevo.get("width") or 0)
    alto = float(canvas_nuevo.get("height") or 0)
    if not sobrantes or not ancho or not alto:
        return {"ids": [], "warnings": []}

    fuera = []
    for v in sobrantes:
        pos = v.get("position") or {}
        x, y = float(pos.get("x") or 0), float(pos.get("y") or 0)
        w, h = float(pos.get("width") or 0), float(pos.get("height") or 0)
        if x + w > ancho or y + h > alto:
            fuera.append(v["id"])
    if not fuera:
        return {"ids": [], "warnings": []}

    viejo = (float(pagina_actual.get("width") or 0),
             float(pagina_actual.get("height") or 0))
    cambio = viejo != (ancho, alto) and all(viejo)
    porque = (f"El lienzo pasa de {viejo[0]:.0f}x{viejo[1]:.0f} a "
              f"{ancho:.0f}x{alto:.0f} y " if cambio else "")
    return {"ids": fuera, "warnings": [
        f"{porque}{len(fuera)} visual(es) de la composicion anterior quedan "
        f"FUERA del lienzo y sync_mode='merge' los conserva: {fuera}. No se "
        "ven al abrir el informe, pero si viajan al render y a la "
        "publicacion. Usa sync_mode='replace' para eliminarlos, o "
        "pbi_reflow_pages para reescalar la pagina entera al lienzo nuevo."]}


def _planificar_update(active: ActivePbip, compilado: Dict[str, Any],
                       page_id: str, sync_mode: str) -> Dict[str, Any]:
    pdir = pbir_reader.pages_dir(active)
    page_dir = safe_paths.safe_join(pdir, page_id, kind="ruta de pagina")
    page_json = safe_paths.safe_join(page_dir, "page.json", kind="page.json")

    from horizun_pbi_mcp.utils.json_utils import read_json

    actual_pagina = read_json(page_json)
    existentes = pbir_reader.list_visuals(active, page_id, strict=True)

    # Primera pasada: emparejado por ID. Es la unica identidad que no admite
    # duda: el id determinista que produce la misma semilla, o el id real que
    # el autor puso en el spec. Se reserva ANTES de la pasada por firma para
    # que un emparejado difuso no consuma un visual que otro nombro explicito.
    por_id = {v["id"]: v for v in existentes}
    reservados: Dict[int, Dict[str, Any]] = {}
    usados: set = set()
    for indice, c in enumerate(compilado["visuals"]):
        for candidato_id in (c["visual"].get("name"),
                             (c.get("meta") or {}).get("spec_id")):
            existente = por_id.get(str(candidato_id or ""))
            if existente is not None and existente["id"] not in usados:
                reservados[indice] = existente
                usados.add(existente["id"])
                break

    # Segunda pasada: emparejado por firma (tipo + titulo + campos). Cada
    # existente se consume una sola vez, para que dos visuales identicos no se
    # emparejen ambos con el mismo del spec.
    disponibles: Dict[tuple, List[Dict[str, Any]]] = {}
    for v in existentes:
        if v["id"] not in usados:
            disponibles.setdefault(_firma_de_existente(v), []).append(v)

    archivos: Dict[Path, Any] = {}
    kept, added, updated = [], [], []
    colisiones: List[Dict[str, Any]] = []

    for indice, c in enumerate(compilado["visuals"]):
        firma = _firma_de_compilado(c)
        pareja = reservados.get(indice)
        emparejado_por_id = pareja is not None
        if pareja is None:
            cola = disponibles.get(firma) or []
            while cola:
                candidato = cola.pop(0)
                if candidato["id"] not in usados:
                    pareja = candidato
                    break

        datos = copy.deepcopy(c["visual"])
        # Un textbox no tiene titulo ni campos: su firma es solo el tipo, y
        # DOS textbox distintos (titulo y subtitulo) comparten firma. Antes el
        # merge emparejaba el subtitulo del spec con el titulo existente y le
        # REEMPLAZABA el texto: perdida silenciosa de contenido. Si la firma
        # no distingue y el contenido difiere, es un conflicto que decide el
        # autor, nunca un update callado. En 'replace' no aplica: ahi el spec
        # declara la pagina completa y sustituir contenido es lo pedido.
        if (pareja is not None and not emparejado_por_id
                and sync_mode == MERGE and firma[1:] == ("", (), ())):
            anterior_visual = read_json(Path(pareja["file"])).get("visual")
            if anterior_visual != datos.get("visual"):
                colisiones.append({
                    "spec_index": indice, "type": firma[0],
                    "existing_id": pareja["id"]})
                continue

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

    if colisiones:
        raise PageConflict(
            f"{len(colisiones)} visual(es) del spec comparten firma con uno "
            "existente pero difieren en contenido; actualizarlos a ciegas "
            "reemplazaria contenido ajeno (p.ej. el subtitulo del spec "
            "pisando el texto del titulo). Ponle al visual del spec un 'id' "
            "con el id real del visual a actualizar, o cambia a "
            "sync_mode='replace' si el spec describe la pagina completa.",
            details={"collisions": colisiones,
                     "existing_ids": {c["existing_id"]: c["type"]
                                      for c in colisiones}})

    sobrantes = [v for v in existentes if v["id"] not in usados]
    borrados: List[Path] = []
    if sync_mode == REPLACE:
        borrados = [Path(v["file"]) for v in sobrantes]

    avisos = _avisar_de_los_que_quedan_fuera(
        sobrantes if sync_mode == MERGE else [], actual_pagina,
        compilado["canvas"])

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
        "out_of_bounds_kept": avisos["ids"],
        "warnings": avisos["warnings"],
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
