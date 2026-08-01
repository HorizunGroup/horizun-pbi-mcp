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
_ESQUEMA_PLATFORM = ("https://developer.microsoft.com/json-schemas/fabric/"
                     "gitIntegration/platformProperties/2.0.0/schema.json")
_ESQUEMA_VERSION = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
                    "definition/versionMetadata/1.0.0/schema.json")

#: Caracteres que convertirian el nombre en una ruta.
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*]')

#: Nombre del tema base propio. No se copia el de Microsoft (CY26SU05):
#: vendorizarlo en un repositorio Apache-2.0 no es nuestro para hacerlo.
_TEMA_BASE = "HorizunBase"

#: Versiones de esquema que este generador escribe. Power BI las exige dentro
#: de `themeCollection.baseTheme` y deben describir lo que hay de verdad.
_VERSIONES = {"report": "3.3.0", "page": "2.1.0", "visual": "2.7.0"}


def _escribir_tema_base(report_dir: Path, nombre: str) -> None:
    """Tema base minimo pero completo, propio.

    Un informe sin tema base resuelto no carga: Power BI busca el archivo, no
    lo encuentra y aborta la vista. La paleta es neutra a proposito —quien
    quiera identidad propia aplica la suya con `pbi_apply_theme`, que sabe
    escribir el archivo y declararlo.
    """
    _json(report_dir / "StaticResources" / "SharedResources" / "BaseThemes"
          / f"{nombre}.json", {
        "name": nombre,
        "dataColors": ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100",
                       "#E87BA4", "#008300", "#4A3AA7", "#E34948"],
        "background": "#FFFFFF",
        "foreground": "#252423",
        "tableAccent": "#2A78D6",
        "good": "#0CA30C", "neutral": "#FAB219", "bad": "#D03B3B",
    })


def _json(ruta: Path, contenido: Dict[str, Any]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(contenido, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")


def _platform(ruta: Path, tipo: str, nombre: str) -> None:
    """Identidad del artefacto para la integracion con Git de Fabric.

    No es opcional: sin `.platform` el validador oficial falla con
    PBIR_PLATFORM_MISSING y Power BI Desktop abre una ventana 'Sin titulo' con
    el modelo vacio, sin explicar por que. Cada artefacto lleva el suyo, con
    su propio logicalId: dos artefactos no pueden compartir identidad.
    """
    _json(ruta / ".platform", {
        "$schema": _ESQUEMA_PLATFORM,
        "metadata": {"type": tipo, "displayName": nombre},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    })


def _revisar_informe(report_dir: Path) -> Dict[str, Any]:
    """Pasa el informe recien escrito por el validador oficial, si lo hay.

    La primera version de este esqueleto olvidaba `.platform` y
    `definition/version.json`. El TMDL parseaba, el validador propio decia que
    todo estaba bien, y Power BI Desktop abria una ventana 'Sin titulo' con el
    modelo vacio sin explicar nada. Generar un proyecto que no abre es peor que
    no generarlo.
    """
    from services import report_validator

    try:
        resultado = report_validator.validar_informe(report_dir)
    except Exception as exc:  # noqa: BLE001 - la falta del CLI no debe tumbar esto
        return {"checked": False, "reason": str(exc)}

    if resultado.status == report_validator.UNAVAILABLE:
        return {"checked": False,
                "reason": "El validador oficial de informes no esta instalado "
                          "(python scripts/fetch_report_validator.py)."}

    errores = [d.__dict__ for d in resultado.diagnostics if d.severity == "error"]
    if errores:
        raise ScaffoldError(
            "El informe generado no pasa el validador oficial, asi que no se "
            "deja escrito: Power BI no lo abriria. Es un fallo del generador.",
            details={"diagnostics": errores, "report_dir": str(report_dir)})
    return {"checked": True, "status": resultado.status,
            "diagnostics": len(resultado.diagnostics)}


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
    _platform(model_dir, "SemanticModel", name)
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
    _platform(report_dir, "Report", name)
    _json(report_dir / "definition" / "version.json",
          {"$schema": _ESQUEMA_VERSION, "version": "2.0.0"})
    _json(report_dir / "definition.pbir", {
        "$schema": _ESQUEMA_PBIR,
        "version": "4.0",
        # Relativa: una ruta absoluta ata el proyecto a esta maquina.
        "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}},
    })
    # Un informe NECESITA un tema base resuelto, y son TRES cosas que van
    # juntas o no van: la declaracion en `themeCollection`, la entrada en
    # `resourcePackages` y el archivo en disco. Ademas, `reportVersionAtImport`
    # es obligatorio dentro de baseTheme —Power BI lo dice literalmente: "La
    # propiedad necesaria 'reportVersionAtImport' no se incluyo"—.
    #
    # El tema se genera aqui en vez de copiar el de Microsoft: vendorizar
    # CY26SU05.json en un repositorio Apache-2.0 no es nuestro para hacerlo.
    _escribir_tema_base(report_dir, _TEMA_BASE)
    _json(report_dir / "definition" / "report.json", {
        "$schema": _ESQUEMA_REPORT,
        "themeCollection": {
            "baseTheme": {
                "name": _TEMA_BASE,
                "type": "SharedResources",
                "reportVersionAtImport": _VERSIONES,
            }
        },
        "resourcePackages": [{
            "name": "SharedResources",
            "type": "SharedResources",
            "items": [{"name": _TEMA_BASE,
                       "path": f"BaseThemes/{_TEMA_BASE}.json",
                       "type": "BaseTheme"}],
        }],
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

    # El TMDL puede ser perfecto y el proyecto no abrir igual: la mitad del
    # .pbip es el informe. Si el validador oficial esta disponible se usa aqui,
    # que es el unico momento en que el error sale gratis.
    informe = _revisar_informe(report_dir)

    log.info("Proyecto .pbip creado en %s (%sx%s, cultura %s)",
             raiz, width, height, culture)
    return {
        "report_validation": informe,
        "project_dir": str(raiz),
        "pbip_path": str(raiz / f"{name}.pbip"),
        "report_dir": str(report_dir),
        "semantic_model_dir": str(model_dir),
        "page_id": page_id,
        "canvas": {"width": width, "height": height},
        "culture": culture,
        "created": True,
    }
