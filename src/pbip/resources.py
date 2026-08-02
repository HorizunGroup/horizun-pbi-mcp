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

from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from services import paths as safe_paths
from services import txn as txn_service
from utils.json_utils import read_json

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
    propuesto = name or ruta.name
    if not Path(propuesto).suffix:
        propuesto += ruta.suffix
    # ``name`` llega directamente desde la tool MCP. Antes se combinaba con
    # ``carpeta`` sin validarlo: un nombre absoluto o con ``..`` podia escapar
    # de StaticResources y, con overwrite=true, sobrescribir cualquier archivo
    # accesible (incluido definition/report.json).
    safe_paths.safe_identifier(propuesto, kind="nombre de recurso")
    item_name = propuesto if overwrite else _nombre_unico(carpeta, propuesto)

    destino = safe_paths.safe_join(carpeta, item_name,
                                   kind="ruta de recurso del informe")
    informe_path = Path(active.report_dir) / "definition" / "report.json"
    if not informe_path.exists():
        raise ResourceError(f"No se encontro report.json en {informe_path}.")
    informe = read_json(informe_path)
    informe["resourcePackages"] = _declarar(
        informe.get("resourcePackages"),
        {"name": item_name, "path": item_name, "type": tipo})

    # Copia y registro forman una sola unidad logica. El visual no encuentra un
    # archivo sin declarar, y una declaracion sin archivo queda igualmente rota.
    # La transaccion respalda ambos destinos, revalida contencion justo antes de
    # escribir y revierte la copia si report.json no se puede confirmar.
    from services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation="Anadir un recurso de imagen")
    contenido = ruta.read_bytes()
    cm = txn_service.project_transaction(
        active, [destino, informe_path], tool="pbi_add_image_resource")
    with cm as tx:
        tx.write_bytes(destino, contenido)
        tx.write_json(informe_path, informe)

    log.info("Recurso '%s' anadido (%s bytes)", item_name, tamano)
    return {"item_name": item_name, "file": str(destino), "type": tipo,
            "bytes": tamano, "source": str(ruta),
            "backup": cm.result["journal"], "transaction": cm.result,
            "validation_report": cm.validation,
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
    no_declarados = [f for f in en_disco if f not in rutas]
    faltantes = [p for p in rutas if p and p not in en_disco]
    return {
        "declared": declarados,
        "on_disk": en_disco,
        # Un archivo sin declarar no lo encuentra Power BI; una declaracion sin
        # archivo deja el visual vacio. Los dos casos son invisibles al abrir.
        "undeclared_files": no_declarados,
        "missing_files": faltantes,
        "warnings": (
            ([f"{len(no_declarados)} recurso(s) estan en disco pero no "
              "declarados."] if no_declarados else [])
            + ([f"{len(faltantes)} recurso(s) declarados no existen en disco."]
               if faltantes else [])),
    }
