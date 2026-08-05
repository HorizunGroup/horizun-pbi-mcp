"""Visuales PERSONALIZADOS del informe: se descubren, no se codifican.

El generador conoce 29 tipos nativos porque su contrato se verifico uno a uno
contra el catalogo oficial de Microsoft. Un visual personalizado no puede
estar en esa lista: cada informe instala los suyos, y su identificador es un
GUID distinto en cada uno.

Consecuencia practica, y el motivo de este modulo: en un tablero 4D/5D el MCP
podia montar KPIs, curvas y tablas, pero NO el visor 3D ni la linea de tiempo
—justo lo que motiva conectar BIM con Power BI—. Habia que dejar recuadros
marcadores y pedirle a una persona que los pegara a mano.

La fuente de verdad ya existe dentro del propio informe:

    <Report>/CustomVisuals/<GUID>/resources/<GUID>.pbiviz.json

Ese archivo declara `capabilities.dataRoles`, o sea los nombres de rol REALES
que el visual acepta. Con eso se puede validar igual de estricto que con los
nativos —un rol inventado se rechaza con la lista de los validos— sin inventar
ningun contrato: el contrato lo publica el propio visual.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("custom_visuals")

CUSTOM_DIR = "CustomVisuals"

#: Cache por (ruta del informe, huella de mtimes). Un informe puede instalar o
#: quitar visuales entre llamadas, y releer el disco en cada validacion de rol
#: seria un coste absurdo; pero cachear solo por ruta serviria datos viejos
#: justo despues de instalar uno. La huella resuelve las dos cosas.
_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def custom_dir(report_dir: Path | str) -> Path:
    return Path(report_dir) / CUSTOM_DIR


def _huella(directorio: Path) -> tuple:
    """mtime+tamaño de cada pbiviz. Cambia si se instala, quita o actualiza."""
    marcas = []
    try:
        for p in sorted(directorio.glob("*/resources/*.pbiviz.json")):
            try:
                st = p.stat()
                marcas.append((p.name, int(st.st_mtime), st.st_size))
            except OSError:                              # pragma: no cover
                marcas.append((p.name, 0, 0))
    except OSError:                                      # pragma: no cover
        return ()
    return tuple(marcas)


def _leer_pbiviz(ruta: Path) -> Optional[Dict[str, Any]]:
    """Un pbiviz ilegible NO tumba el informe: se avisa y se sigue.

    Es metadato de un visual de terceros; que uno venga corrupto no puede
    impedir escribir el resto de la pagina.
    """
    try:
        # utf-8-sig: varios .pbiviz.json reales vienen con BOM.
        datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        log.warning("No se pudo leer %s: %s", ruta.name, exc)
        return None
    if not isinstance(datos, dict):
        return None

    visual = datos.get("visual") or {}
    guid = str(visual.get("guid") or "").strip()
    if not guid:
        # Sin GUID no hay con que referenciarlo desde un visual.json.
        log.warning("%s no declara visual.guid; se ignora", ruta.name)
        return None

    roles = []
    for r in (datos.get("capabilities") or {}).get("dataRoles") or []:
        nombre = str((r or {}).get("name") or "").strip()
        if nombre:
            roles.append({"name": nombre,
                          "kind": str(r.get("kind") or ""),
                          "display_name": str(r.get("displayName") or nombre)})
    return {
        "guid": guid,
        "display_name": str(visual.get("displayName") or guid),
        "version": str(visual.get("version") or ""),
        "roles": roles,
        "manifest": str(ruta),
    }


def discover(report_dir: Path | str) -> Dict[str, Dict[str, Any]]:
    """{GUID: {display_name, version, roles:[{name,kind,display_name}]}}.

    Devuelve `{}` si el informe no tiene `CustomVisuals/` — que es el caso
    normal y no es un error.
    """
    directorio = custom_dir(report_dir)
    clave = str(Path(report_dir).resolve())
    huella = _huella(directorio)

    with _lock:
        previo = _cache.get(clave)
        if previo is not None and previo[0] == huella:
            return previo[1]

    encontrados: Dict[str, Dict[str, Any]] = {}
    try:
        manifiestos = sorted(directorio.glob("*/resources/*.pbiviz.json"))
    except OSError:                                      # pragma: no cover
        manifiestos = []
    for ruta in manifiestos:
        info = _leer_pbiviz(ruta)
        if info:
            encontrados[info["guid"]] = info

    with _lock:
        _cache[clave] = (huella, encontrados)
    if encontrados:
        log.info("Visuales personalizados descubiertos en %s: %s",
                 Path(report_dir).name, sorted(encontrados))
    return encontrados


def discover_for(active) -> Dict[str, Dict[str, Any]]:
    """Los del proyecto activo. `{}` si no tiene carpeta de informe."""
    if active is None or not getattr(active, "report_dir", None):
        return {}
    return discover(active.report_dir)


def role_names(info: Dict[str, Any]) -> List[str]:
    return [r["name"] for r in info.get("roles") or []]


def resolve_guid(report_dir: Path | str, visual_type: str) -> Optional[str]:
    """El GUID tal cual si esta instalado. Compara sin distinguir mayusculas.

    Devuelve el GUID CANONICO (como lo escribe el manifiesto), no lo que se
    tecleo: `visualType` en el visual.json tiene que coincidir exactamente con
    el nombre de la carpeta o Power BI no encuentra el visual.
    """
    pedido = str(visual_type or "").strip().casefold()
    for guid in discover(report_dir):
        if guid.casefold() == pedido:
            return guid
    return None


def invalidate_cache() -> None:
    """Para las pruebas y para tras instalar un visual en la misma sesion."""
    with _lock:
        _cache.clear()
