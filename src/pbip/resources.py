"""Recursos estaticos del informe: imagenes, logos y temas.

Un recurso no basta con copiarlo a `StaticResources/RegisteredResources/`:
hay que declararlo en `report.json` -> `resourcePackages`. Sin las dos cosas
Power BI no lo encuentra y el visual que lo use sale vacio, sin ningun error
que explique por que.

El nombre con el que se declara (`ItemName`) es el que despues usa el visual
`image`, y por eso `add_image` lo devuelve: es el unico dato que hace falta
para enlazarlos.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from utils.json_utils import read_json, write_json

log = get_logger("resources")

#: Formatos que Power BI muestra en un visual de imagen.
EXTENSIONES = {".png": "Image", ".jpg": "Image", ".jpeg": "Image",
               ".gif": "Image", ".bmp": "Image", ".svg": "Image"}
#: Tope prudente. Un informe con imagenes enormes tarda en abrir y en publicar.
MAX_BYTES = 8 * 1024 * 1024


class ResourceError(PowerBIMCPError):
    code = "resource_error"


def _carpeta(active: ActivePbip) -> Path:
    if not active.report_dir:
        raise ResourceError("El proyecto no tiene carpeta .Report.")
    return Path(active.report_dir) / "StaticResources" / "RegisteredResources"


def _declarar(paquetes: Any, item: Dict[str, str]) -> List[Dict[str, Any]]:
    """Mete el recurso en el paquete `RegisteredResources`, creandolo si falta."""
    paquetes = list(paquetes or [])
    for paquete in paquetes:
        if paquete.get("type") == "RegisteredResources":
            items = [i for i in (paquete.get("items") or [])
                     if i.get("path") != item["path"]]
            items.append(item)
            paquete["items"] = items
            return paquetes
    paquetes.append({"name": "RegisteredResources",
                     "type": "RegisteredResources", "items": [item]})
    return paquetes


def _nombre_unico(carpeta: Path, propuesto: str) -> str:
    """Evita pisar un recurso existente distinto del que se anade."""
    destino = carpeta / propuesto
    if not destino.exists():
        return propuesto
    tallo, sufijo = Path(propuesto).stem, Path(propuesto).suffix
    for n in range(2, 100):
        alternativo = f"{tallo}_{n}{sufijo}"
        if not (carpeta / alternativo).exists():
            return alternativo
    raise ResourceError(f"Demasiados recursos llamados '{propuesto}'.")


def add_image(active: ActivePbip, origen: str | Path,
              name: Optional[str] = None,
              overwrite: bool = False) -> Dict[str, Any]:
    """Copia una imagen al informe y la deja declarada como recurso.

    Devuelve `item_name`, que es lo que hay que pasarle al visual `image`
    en `options.resource`.
    """
    ruta = Path(origen).expanduser()
    if not ruta.exists() or not ruta.is_file():
        raise ResourceError(f"No existe el archivo de imagen: {ruta}")
    tipo = EXTENSIONES.get(ruta.suffix.lower())
    if tipo is None:
        raise ResourceError(
            f"Extension no soportada: '{ruta.suffix}'. Power BI muestra "
            f"{sorted(EXTENSIONES)}.")
    tamano = ruta.stat().st_size
    if tamano > MAX_BYTES:
        raise ResourceError(
            f"La imagen pesa {tamano / 1e6:.1f} MB y el limite prudente son "
            f"{MAX_BYTES / 1e6:.0f} MB: un informe con imagenes asi tarda en "
            "abrir y en publicar. Reducela antes de incrustarla.")

    carpeta = _carpeta(active)
    carpeta.mkdir(parents=True, exist_ok=True)
    propuesto = name or ruta.name
    if not Path(propuesto).suffix:
        propuesto += ruta.suffix
    item_name = propuesto if overwrite else _nombre_unico(carpeta, propuesto)

    destino = carpeta / item_name
    shutil.copy2(ruta, destino)

    informe_path = Path(active.report_dir) / "definition" / "report.json"
    if not informe_path.exists():
        raise ResourceError(f"No se encontro report.json en {informe_path}.")
    informe = read_json(informe_path)
    informe["resourcePackages"] = _declarar(
        informe.get("resourcePackages"),
        {"name": item_name, "path": item_name, "type": tipo})
    write_json(informe_path, informe)

    log.info("Recurso '%s' anadido (%s bytes)", item_name, tamano)
    return {"item_name": item_name, "file": str(destino), "type": tipo,
            "bytes": tamano, "source": str(ruta),
            "usage": {"type": "image", "options": {"resource": item_name}}}


def list_resources(active: ActivePbip) -> Dict[str, Any]:
    """Recursos declarados y los que estan en disco, para detectar desajustes."""
    carpeta = _carpeta(active)
    en_disco = sorted(p.name for p in carpeta.glob("*")) if carpeta.exists() else []

    informe_path = Path(active.report_dir) / "definition" / "report.json"
    declarados: List[Dict[str, Any]] = []
    if informe_path.exists():
        for paquete in read_json(informe_path).get("resourcePackages") or []:
            if paquete.get("type") == "RegisteredResources":
                declarados = list(paquete.get("items") or [])
    rutas = {i.get("path") for i in declarados}
    return {
        "declared": declarados,
        "on_disk": en_disco,
        # Un archivo sin declarar no lo encuentra Power BI; una declaracion sin
        # archivo deja el visual vacio. Los dos casos son invisibles al abrir.
        "undeclared_files": [f for f in en_disco if f not in rutas],
        "missing_files": [p for p in rutas if p and p not in en_disco],
    }
