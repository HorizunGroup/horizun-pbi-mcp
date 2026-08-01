"""Marcadores: guardar un estado del informe y volver a el con un boton.

Un marcador PBIR son dos archivos que hay que mantener a la vez:

    definition/bookmarks/<nombre>.bookmark.json   el estado guardado
    definition/bookmarks/bookmarks.json           el indice que los lista

Si falta el indice, Power BI no muestra el marcador aunque el archivo exista.

Dos trampas del formato, ambas verificadas contra informes reales:

- Dentro de un marcador el filtro usa la clave `expression`, NO `field` como en
  `filterConfig`. Son estructuras parecidas con nombres distintos, y usar la de
  al lado produce un marcador que no restaura nada.
- `explorationState` exige `sections` aunque no se guarde estado por visual:
  un diccionario vacio por pagina es lo minimo valido.

Nota sobre esquemas: el que Microsoft no publica es `bookmarks/2.0.0` (plural),
que algunos informes declaran para el indice. Los que aqui se escriben —
`bookmark/2.1.0` y `bookmarksMetadata/1.0.0`— si estan publicados y en cache,
asi que estas escrituras se comprueban enteras.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from utils.json_utils import read_json, write_json

log = get_logger("bookmarks")

BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
SCHEMA_BOOKMARK = f"{BASE}/bookmark/2.1.0/schema.json"
SCHEMA_INDICE = f"{BASE}/bookmarksMetadata/1.0.0/schema.json"
#: Version del estado guardado. La escribe Power BI y el esquema la exige.
VERSION_ESTADO = "1.3"


class BookmarkError(PowerBIMCPError):
    code = "bookmark_error"


def _carpeta(active: ActivePbip) -> Path:
    if not active.report_dir:
        raise BookmarkError("El proyecto no tiene carpeta .Report.")
    return Path(active.report_dir) / "definition" / "bookmarks"


def _identificador(display_name: str) -> str:
    """Identificador al estilo de Power BI: 'Bookmark' + token hexadecimal."""
    return "Bookmark" + uuid.uuid4().hex[:20]


def _filtro_de_marcador(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Filtro EN UN MARCADOR: la clave es `expression`, no `field`.

    Se reutiliza el constructor de filtros para no tener dos gramaticas del
    mismo objeto, y se renombra la clave al salir.
    """
    from pbip import filter_builder

    construido = filter_builder.build_filter(spec)
    salida: Dict[str, Any] = {"name": construido["name"],
                              "type": construido.get("type", "Categorical")}
    if "filter" in construido:
        salida["filter"] = construido["filter"]
    salida["expression"] = construido["field"]
    # `howCreated` es numerico aqui (1 = lo creo el usuario), al reves que en
    # filterConfig, donde es una cadena.
    salida["howCreated"] = 1
    return salida


def list_bookmarks(active: ActivePbip) -> Dict[str, Any]:
    """Marcadores del informe: los del indice y los que hay en disco."""
    carpeta = _carpeta(active)
    indice_path = carpeta / "bookmarks.json"
    indexados: List[Dict[str, Any]] = []
    if indice_path.exists():
        indexados = list(read_json(indice_path).get("items") or [])

    en_disco = []
    if carpeta.exists():
        for f in sorted(carpeta.glob("*.bookmark.json")):
            try:
                d = read_json(f)
            except Exception:                                  # noqa: BLE001
                continue
            en_disco.append({"name": d.get("name"),
                             "display_name": d.get("displayName"),
                             "file": str(f)})
    nombres_indice = {i.get("name") for i in indexados}
    return {
        "indexed": indexados,
        "on_disk": en_disco,
        # Un marcador que no esta en el indice no lo muestra Power BI, y una
        # entrada del indice sin archivo rompe el panel. Los dos son mudos.
        "not_indexed": [b for b in en_disco if b["name"] not in nombres_indice],
        "missing_files": [n for n in nombres_indice
                          if n and n not in {b["name"] for b in en_disco}],
    }


def create_bookmark(active: ActivePbip, display_name: str, page: str, *,
                    filters: Optional[List[Dict[str, Any]]] = None,
                    target_visuals: Optional[List[str]] = None,
                    suppress_data: bool = False,
                    suppress_display: bool = False,
                    name: Optional[str] = None,
                    overwrite: bool = False) -> Dict[str, Any]:
    """Crea un marcador que activa una pagina y, opcionalmente, unos filtros.

    `page` es el NOMBRE INTERNO de la pagina (su id), que es lo que guarda el
    estado. `target_visuals` limita a que visuales afecta: sin el, el marcador
    actua sobre toda la pagina.

    Un marcador con `suppress_data` guarda solo el aspecto (posiciones,
    visibilidad) sin tocar filtros; con `suppress_display`, al reves.
    """
    from pbip import pbir_reader

    if not display_name or not str(display_name).strip():
        raise BookmarkError("El marcador necesita un nombre visible.")

    paginas = {p["name"]: p for p in pbir_reader.list_pages(active)}
    if page not in paginas:
        por_titulo = {p.get("display_name"): p["name"] for p in paginas.values()}
        if page in por_titulo:
            page = por_titulo[page]
        else:
            raise BookmarkError(
                f"No existe la pagina '{page}'.",
                details={"pages": [{"name": p["name"],
                                    "display_name": p.get("display_name")}
                                   for p in paginas.values()]})

    if target_visuals:
        existentes = {v["id"] for v in pbir_reader.list_visuals(active, page)}
        faltan = [v for v in target_visuals if v not in existentes]
        if faltan:
            raise BookmarkError(
                f"Estos visuales no existen en la pagina: {faltan}.",
                details={"available": sorted(existentes)})

    identificador = name or _identificador(display_name)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identificador):
        raise BookmarkError(
            f"El identificador '{identificador}' lleva caracteres que no valen "
            "para un nombre de archivo. Usa letras, digitos, guion o guion bajo.")

    carpeta = _carpeta(active)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{identificador}.bookmark.json"
    if destino.exists() and not overwrite:
        raise BookmarkError(
            f"Ya existe el marcador '{identificador}'. Usa overwrite=true.")

    estado: Dict[str, Any] = {
        "version": VERSION_ESTADO,
        "activeSection": page,
        # `sections` es obligatorio aunque no se guarde estado por visual.
        "sections": {page: {"visualContainers": {}}},
    }
    if filters:
        estado["filters"] = {"byExpr": [_filtro_de_marcador(f) for f in filters]}

    opciones: Dict[str, Any] = {"targetVisualNames": list(target_visuals or [])}
    if target_visuals:
        opciones["applyOnlyToTargetVisuals"] = True
    if suppress_data:
        opciones["suppressData"] = True
    if suppress_display:
        opciones["suppressDisplay"] = True

    marcador = {"$schema": SCHEMA_BOOKMARK, "name": identificador,
                "displayName": str(display_name), "options": opciones,
                "explorationState": estado}
    write_json(destino, marcador)

    # El indice: sin el, Power BI no lo muestra aunque el archivo exista.
    indice_path = carpeta / "bookmarks.json"
    indice = read_json(indice_path) if indice_path.exists() else {
        "$schema": SCHEMA_INDICE, "items": []}
    indice.setdefault("$schema", SCHEMA_INDICE)
    items = [i for i in (indice.get("items") or []) if i.get("name") != identificador]
    items.append({"name": identificador})
    indice["items"] = items
    write_json(indice_path, indice)

    log.info("Marcador '%s' (%s) sobre la pagina %s", display_name,
             identificador, page)
    return {"name": identificador, "display_name": display_name, "page": page,
            "file": str(destino), "index": str(indice_path),
            "filters": len(filters or []),
            "target_visuals": list(target_visuals or []),
            "usage": {"type": "button", "options": {"action": "bookmark",
                                                    "bookmark": identificador}}}


def delete_bookmark(active: ActivePbip, name: str) -> Dict[str, Any]:
    """Borra un marcador y lo quita del indice. Las dos cosas o ninguna."""
    carpeta = _carpeta(active)
    destino = carpeta / f"{name}.bookmark.json"
    indice_path = carpeta / "bookmarks.json"
    if not destino.exists():
        raise BookmarkError(f"No existe el marcador '{name}'.")

    destino.unlink()
    if indice_path.exists():
        indice = read_json(indice_path)
        indice["items"] = [i for i in (indice.get("items") or [])
                           if i.get("name") != name]
        write_json(indice_path, indice)
    log.info("Marcador '%s' borrado", name)
    return {"name": name, "deleted": True, "file": str(destino)}
