"""Crear un proyecto .pbip vacio pero valido, para poder partir solo de rutas.

Sin esto, cargar archivos al modelo exigia un proyecto que ya existiera, asi que
"hazme un tablero con estos CSV" seguia empezando a mano en Power BI Desktop.

Un .pbip son dos artefactos que se apuntan entre si: el informe (`.Report`) y el
modelo semantico (`.SemanticModel`). Aqui se escribe el minimo que Power BI
acepta —ni un archivo de mas—, con la referencia entre ambos en ruta RELATIVA:
una absoluta ata el proyecto a la maquina donde se creo.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("pbip_scaffold")


class ScaffoldError(PowerBIMCPError):
    code = "pbip_scaffold_error"


_ESQUEMA_PBIP = ("https://developer.microsoft.com/json-schemas/fabric/pbip/"
                 "pbipProperties/1.0.0/schema.json")
_ESQUEMA_PBIR = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                 "definitionProperties/2.0.0/schema.json")
_ESQUEMA_PBISM = ("https://developer.microsoft.com/json-schemas/fabric/item/"
                  "semanticModel/definitionProperties/1.0.0/schema.json")
_ESQUEMA_REPORT = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                   "definition/report/3.3.0/schema.json")
_ESQUEMA_PAGES = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                  "definition/pagesMetadata/1.1.0/schema.json")
_ESQUEMA_PAGE = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                 "definition/page/2.1.0/schema.json")

#: Caracteres que convertirian el nombre en una ruta.
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*]')


def _json(ruta: Path, contenido: Dict[str, Any]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(contenido, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def crear_proyecto(out_dir: Path | str, name: str, *,
                   culture: str = "es-ES",
                   width: int = 1280, height: int = 1080,
                   page_name: str = "Pagina 1",
                   overwrite: bool = False) -> Dict[str, Any]:
    """Escribe un .pbip vacio y devuelve sus rutas."""
    if not str(name).strip():
        raise ScaffoldError("Hace falta un nombre para el proyecto.")
    if _PROHIBIDOS.search(name) or name.strip() in (".", ".."):
        raise ScaffoldError(
            f"El nombre '{name}' contiene caracteres de ruta. El nombre no "
            "puede decidir donde se escribe el proyecto.",
            details={"name": name})

    raiz = Path(out_dir).expanduser() / name
    if raiz.exists() and any(raiz.iterdir()) and not overwrite:
        raise ScaffoldError(
            f"Ya hay algo en {raiz}. Usa overwrite=true si de verdad quieres "
            "escribir encima.", details={"project_dir": str(raiz)})

    report_dir = raiz / f"{name}.Report"
    model_dir = raiz / f"{name}.SemanticModel"

    # --- el .pbip: solo apunta al informe; el informe apunta al modelo -------
    _json(raiz / f"{name}.pbip", {
        "$schema": _ESQUEMA_PBIP,
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{name}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })

    # --- modelo semantico ---------------------------------------------------
    _json(model_dir / "definition.pbism",
          {"$schema": _ESQUEMA_PBISM, "version": "4.2", "settings": {}})

    definition = model_dir / "definition"
    definition.mkdir(parents=True, exist_ok=True)
    (definition / "tables").mkdir(exist_ok=True)
    (definition / "database.tmdl").write_text(
        f"database {name}\n\tcompatibilityLevel: 1606\n"
        "\tcompatibilityMode: powerBI\n", encoding="utf-8")
    # Sin `sourceQueryCulture` a proposito: se declara la cultura en cada
    # consulta, que es lo unico que no obliga a suponer como se leen los
    # decimales de cada origen.
    (definition / "model.tmdl").write_text(
        "model Model\n"
        f"\tculture: {culture}\n"
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3\n"
        "\tdiscourageImplicitMeasures\n\n"
        "annotation __PBI_TimeIntelligenceEnabled = 0\n",
        encoding="utf-8")

    # --- informe ------------------------------------------------------------
    _json(report_dir / "definition.pbir", {
        "$schema": _ESQUEMA_PBIR,
        "version": "4.0",
        # Relativa: una ruta absoluta ata el proyecto a esta maquina.
        "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}},
    })
    _json(report_dir / "definition" / "report.json", {
        "$schema": _ESQUEMA_REPORT,
        "themeCollection": {"baseTheme": {"name": "CY26SU05",
                                          "type": "SharedResources"}},
        "settings": {},
    })

    # Power BI no abre un informe sin ninguna pagina.
    page_id = uuid.uuid4().hex[:20]
    _json(report_dir / "definition" / "pages" / "pages.json", {
        "$schema": _ESQUEMA_PAGES,
        "pageOrder": [page_id],
        "activePageName": page_id,
    })
    _json(report_dir / "definition" / "pages" / page_id / "page.json", {
        "$schema": _ESQUEMA_PAGE,
        "name": page_id,
        "displayName": page_name,
        "displayOption": "FitToPage",
        "width": width,
        "height": height,
    })

    log.info("Proyecto .pbip creado en %s (%sx%s, cultura %s)",
             raiz, width, height, culture)
    return {
        "project_dir": str(raiz),
        "pbip_path": str(raiz / f"{name}.pbip"),
        "report_dir": str(report_dir),
        "semantic_model_dir": str(model_dir),
        "page_id": page_id,
        "canvas": {"width": width, "height": height},
        "culture": culture,
        "created": True,
    }
