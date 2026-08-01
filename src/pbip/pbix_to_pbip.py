"""Conversion de un .pbix a un proyecto Power BI Project (.pbip).

Un .pbip no es un formato: es una carpeta con dos artefactos de Fabric y un
archivo indice.

    MiInforme.pbip                 <- indice: apunta al artefacto de informe
    MiInforme.Report/              <- informe en PBIR
        .platform, definition.pbir, definition/, StaticResources/, CustomVisuals/
    MiInforme.SemanticModel/       <- modelo en TMDL
        .platform, definition.pbism, definition/

Cada mitad viene de un sitio distinto:

- **Informe**: sale del propio .pbix. Si ya trae `Report/definition/` (Desktop
  reciente guarda PBIR dentro del .pbix) se copia tal cual; si trae el
  `Report/Layout` heredado, se traduce (ver `layout_to_pbir`).
- **Modelo**: NO se puede sacar del archivo. El stream `DataModel` es un backup
  ABF comprimido y solo el motor de Analysis Services lo entiende, asi que se
  abre el .pbix en Power BI Desktop y se serializa desde ahi (`tmdl_export`).

El original nunca se toca: se abre en solo lectura y todo se escribe en la
carpeta de destino.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from pbip import layout_to_pbir, pbix_reader
from pbip.pbix_reader import PbixContents
from powerbi.errors import PowerBIMCPError
from utils.file_utils import rutas_demasiado_largas
from utils.json_utils import write_json

log = get_logger("pbix_to_pbip")

SCHEMA_PBIP = ("https://developer.microsoft.com/json-schemas/fabric/pbip/"
               "pbipProperties/1.0.0/schema.json")
SCHEMA_PBIR = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
               "definitionProperties/2.0.0/schema.json")
SCHEMA_PBISM = ("https://developer.microsoft.com/json-schemas/fabric/item/"
                "semanticModel/definitionProperties/1.0.0/schema.json")
SCHEMA_PLATFORM = ("https://developer.microsoft.com/json-schemas/fabric/"
                   "gitIntegration/platformProperties/2.0.0/schema.json")

PBIP_VERSION = "1.0"
PBIR_VERSION = "4.0"
PBISM_VERSION = "4.2"


class PbixConversionError(PowerBIMCPError):
    code = "pbix_conversion_failed"


@dataclass
class ConversionResult:
    """Que se genero y que quedo fuera al convertir un .pbix."""

    source: str
    project_dir: str
    pbip_path: str
    report_dir: str
    semantic_model_dir: Optional[str] = None
    #: "pbir_copied" (ya venia en PBIR) o "layout_converted" (traducido).
    report_source: str = "layout_converted"
    #: "exported" | "skipped" | "absent"
    model_status: str = "skipped"
    pages: int = 0
    visuals: int = 0
    files_written: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    model: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "pbip_path": self.pbip_path,
            "project_dir": self.project_dir,
            "report_dir": self.report_dir,
            "semantic_model_dir": self.semantic_model_dir,
            "report_source": self.report_source,
            "model_status": self.model_status,
            "pages": self.pages,
            "visuals": self.visuals,
            "file_count": len(self.files_written),
            "warnings": self.warnings,
            "dropped": self.dropped,
            "model": self.model,
        }


def _nombre_proyecto(pbix: Path, nombre: Optional[str]) -> str:
    elegido = (nombre or pbix.stem).strip()
    prohibidos = '<>:"/\\|?*'
    limpio = "".join("_" if c in prohibidos else c for c in elegido).rstrip(". ")
    if not limpio:
        raise PbixConversionError(
            f"No se pudo derivar un nombre de proyecto valido de '{pbix.name}'.")
    return limpio


def _platform(tipo: str, nombre: str) -> Dict[str, Any]:
    return {
        "$schema": SCHEMA_PLATFORM,
        "metadata": {"type": tipo, "displayName": nombre},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }


def _escribir_binario(destino: Path, datos: bytes) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(datos)


#: Version de tema que se declara cuando el origen no trae ninguna. Es
#: obligatoria en PBIR y la mas antigua es lo unico cierto que podemos afirmar.
_TEMA_VERSION_MINIMA = {"visual": "1.0.0", "page": "1.0.0", "report": "1.0.0"}


def _reparar_report_json(datos: bytes, avisos: List[str]) -> bytes:
    """Completa lo que PBIR exige y algunos .pbix no traen.

    El informe embebido se copia tal cual salvo aqui: `reportVersionAtImport`
    y `type` son obligatorios en cada tema, y un report.json sin ellos hace que
    Power BI Desktop se niegue a abrir el proyecto. Copiar fielmente un archivo
    que no abre no es fidelidad, es un proyecto roto.
    """
    import json as _json

    try:
        informe = _json.loads(datos.decode("utf-8-sig"))
    except (ValueError, UnicodeError):
        return datos
    temas = informe.get("themeCollection")
    if not isinstance(temas, dict):
        return datos

    version = next((t["reportVersionAtImport"] for t in temas.values()
                    if isinstance(t, dict)
                    and isinstance(t.get("reportVersionAtImport"), dict)),
                   _TEMA_VERSION_MINIMA)
    reparado = False
    for clave, tema in temas.items():
        if not isinstance(tema, dict):
            continue
        if not isinstance(tema.get("reportVersionAtImport"), dict):
            tema["reportVersionAtImport"] = version
            reparado = True
            avisos.append(
                f"El informe del .pbix no declaraba 'reportVersionAtImport' en "
                f"'{clave}'; PBIR lo exige y se completo con "
                f"{'la version del otro tema' if version is not _TEMA_VERSION_MINIMA else 'la version mas antigua'}.")
        if not tema.get("type"):
            tema["type"] = ("RegisteredResources" if clave == "customTheme"
                            else "SharedResources")
            reparado = True
            avisos.append(f"El tema '{clave}' no declaraba 'type'; se asumio "
                          f"'{tema['type']}'.")
    if not reparado:
        return datos
    return _json.dumps(informe, indent=2, ensure_ascii=False).encode("utf-8")


def _copiar_pbir(contents: PbixContents, definition_dir: Path,
                 escritos: List[str], raiz: Path, avisos: List[str]) -> None:
    """El .pbix ya traia PBIR: se copia byte a byte, sin reinterpretarlo."""
    for relativa, datos in sorted(contents.pbir_parts.items()):
        if relativa == "report.json":
            datos = _reparar_report_json(datos, avisos)
        destino = definition_dir / relativa
        _escribir_binario(destino, datos)
        escritos.append(destino.relative_to(raiz).as_posix())


def _escribir_conversion(conversion: layout_to_pbir.LayoutConversion,
                         definition_dir: Path, escritos: List[str],
                         raiz: Path) -> None:
    for relativa, contenido in sorted(conversion.files.items()):
        destino = definition_dir / relativa
        destino.parent.mkdir(parents=True, exist_ok=True)
        write_json(destino, contenido)
        escritos.append(destino.relative_to(raiz).as_posix())


def _copiar_recursos(contents: PbixContents, report_dir: Path,
                     escritos: List[str], raiz: Path) -> None:
    """Temas, imagenes y visuales personalizados van fuera de `definition/`."""
    for relativa, datos in sorted(contents.report_assets.items()):
        destino = report_dir / relativa
        _escribir_binario(destino, datos)
        escritos.append(destino.relative_to(raiz).as_posix())


def _referencia_dataset(nombre: str, con_modelo: bool,
                        connection_string: Optional[str]) -> Dict[str, Any]:
    if con_modelo:
        return {"byPath": {"path": f"../{nombre}.SemanticModel"}}
    return {"byConnection": {"connectionString": connection_string}}


def _exigir_rutas_cortas(destino: Path, nombre: str,
                         relativas: List[str]) -> None:
    """Aborta si el proyecto no cabria en los limites de ruta de Desktop."""
    problemas = rutas_demasiado_largas(destino, relativas)
    if not problemas:
        return
    peor = max(problemas, key=lambda p: p["length"])
    sobra = peor["length"] - peor["limit"] + 1
    raise PbixConversionError(
        f"El proyecto no cabe en la ruta de destino: '{peor['path']}' mide "
        f"{peor['length']} caracteres y Power BI Desktop no abre un .pbip con "
        f"rutas de {peor['limit']} o mas. Elige un 'out_dir' al menos {sobra} "
        f"caracteres mas corto (p.ej. C:\\pbip) o acorta 'project_name'.",
        details={"too_long": problemas[:10], "count": len(problemas),
                 "project_name": nombre},
    )


def _preparar_carpeta(carpeta: Path, overwrite: bool) -> None:
    if carpeta.exists() and any(carpeta.iterdir()):
        if not overwrite:
            raise PbixConversionError(
                f"La carpeta de destino ya existe y no esta vacia: {carpeta}. "
                "Usa overwrite=true si quieres reemplazarla.",
                details={"path": str(carpeta)},
            )
        import shutil

        shutil.rmtree(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)


def convert(
    pbix_path: str | Path,
    out_dir: str | Path,
    *,
    project_name: Optional[str] = None,
    include_model: bool = True,
    overwrite: bool = False,
    dataset_connection_string: Optional[str] = None,
    desktop_timeout: int = 300,
    close_desktop: bool = True,
    reuse_open_desktop: bool = True,
) -> ConversionResult:
    """Convierte un .pbix en un proyecto .pbip completo.

    `include_model=False` genera solo la mitad del informe: util para revisar
    la conversion del PBIR sin esperar a que Power BI Desktop cargue el modelo.
    """
    pbix = Path(pbix_path).expanduser().resolve()
    contents = pbix_reader.read_pbix(pbix)

    if contents.report_format == "none":
        raise PbixConversionError(
            f"'{pbix.name}' no contiene ningun informe (ni 'Report/Layout' ni "
            "'Report/definition'). Puede ser una plantilla .pbit renombrada o "
            "un archivo dañado.",
            details={"parts": contents.parts[:20]},
        )

    nombre = _nombre_proyecto(pbix, project_name)
    destino = Path(out_dir).expanduser().resolve() / nombre
    # El informe referencia al modelo por su carpeta hermana siempre que el
    # .pbix lleve uno, aunque en esta pasada no lo exportemos: asi el
    # definition.pbir queda valido y solo falta rellenar el .SemanticModel.
    tiene_modelo = contents.has_data_model
    quiere_modelo = include_model and tiene_modelo

    if not contents.has_data_model and not dataset_connection_string:
        raise PbixConversionError(
            f"'{pbix.name}' es un informe con conexion en vivo: no lleva modelo "
            "propio, y un .pbip necesita saber a que modelo apuntar. Vuelve a "
            "llamar indicando 'dataset_connection_string' con la cadena de "
            "conexion del modelo semantico en el servicio.",
            details={"remote_artifacts": contents.remote_artifacts},
        )

    # El informe se traduce en memoria ANTES de tocar el disco: asi se conocen
    # todas las rutas que se van a escribir y se puede comprobar que caben.
    conversion: Optional[layout_to_pbir.LayoutConversion] = None
    if contents.report_format == "layout":
        conversion = layout_to_pbir.convert_layout(contents.layout or {})
        relativas_informe = list(conversion.files)
    else:
        relativas_informe = list(contents.pbir_parts)

    previstas = [f"{nombre}.pbip",
                 f"{nombre}.Report/.platform",
                 f"{nombre}.Report/definition.pbir",
                 f"{nombre}.SemanticModel/.platform",
                 f"{nombre}.SemanticModel/definition.pbism"]
    previstas += [f"{nombre}.Report/definition/{r}" for r in relativas_informe]
    previstas += [f"{nombre}.Report/{r}" for r in contents.report_assets]
    _exigir_rutas_cortas(destino, nombre, previstas)

    _preparar_carpeta(destino, overwrite)
    report_dir = destino / f"{nombre}.Report"
    definition_dir = report_dir / "definition"
    definition_dir.mkdir(parents=True, exist_ok=True)

    resultado = ConversionResult(
        source=str(pbix),
        project_dir=str(destino),
        pbip_path=str(destino / f"{nombre}.pbip"),
        report_dir=str(report_dir),
    )
    resultado.warnings.extend(contents.warnings)
    escritos = resultado.files_written

    if conversion is None:
        resultado.report_source = "pbir_copied"
        _copiar_pbir(contents, definition_dir, escritos, destino,
                     resultado.warnings)
        resultado.pages = sum(1 for p in contents.pbir_parts if p.endswith("/page.json"))
        resultado.visuals = sum(1 for p in contents.pbir_parts if p.endswith("/visual.json"))
        log.info("%s ya venia en PBIR: copiadas %s partes",
                 pbix.name, len(contents.pbir_parts))
    else:
        resultado.report_source = "layout_converted"
        _escribir_conversion(conversion, definition_dir, escritos, destino)
        resultado.pages = len(conversion.pages)
        resultado.visuals = conversion.visual_count
        resultado.warnings.extend(conversion.warnings)
        resultado.dropped.extend(conversion.dropped)

    _copiar_recursos(contents, report_dir, escritos, destino)

    write_json(report_dir / ".platform", _platform("Report", nombre))
    write_json(report_dir / "definition.pbir", {
        "$schema": SCHEMA_PBIR,
        "version": PBIR_VERSION,
        "datasetReference": _referencia_dataset(
            nombre, tiene_modelo, dataset_connection_string),
    })
    escritos.extend([f"{nombre}.Report/.platform", f"{nombre}.Report/definition.pbir"])

    if quiere_modelo:
        try:
            modelo = _exportar_modelo(
                pbix, destino, nombre, contents,
                timeout=desktop_timeout, close_after=close_desktop,
                reuse_open=reuse_open_desktop)
        except PowerBIMCPError as exc:
            # El informe ya esta escrito. Decirlo evita que el usuario crea que
            # no quedo nada y borre a mano un trabajo que si sirve.
            exc.details = dict(exc.details or {})
            exc.details["partial_project"] = {
                "report_written": True,
                "project_dir": str(destino),
                "pages": resultado.pages,
                "visuals": resultado.visuals,
                "note": "El informe se convirtio y quedo en disco; falta el "
                        "'.SemanticModel'. Repite la conversion con "
                        "overwrite=true cuando se resuelva el motivo.",
            }
            raise
        resultado.semantic_model_dir = modelo.pop("semantic_model_dir", None)
        resultado.model_status = modelo.pop("status", "skipped")
        resultado.model = modelo
        resultado.warnings.extend(modelo.pop("warnings", []))
        escritos.extend(modelo.get("written", []))
    elif contents.has_data_model:
        resultado.model_status = "skipped"
        resultado.warnings.append(
            "El .pbix lleva modelo de datos pero se pidio no exportarlo: el "
            ".pbip queda sin '.SemanticModel' y no abrira en Desktop hasta que "
            "se genere.")
    else:
        resultado.model_status = "absent"

    write_json(destino / f"{nombre}.pbip", {
        "$schema": SCHEMA_PBIP,
        "version": PBIP_VERSION,
        "artifacts": [{"report": {"path": f"{nombre}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    escritos.append(f"{nombre}.pbip")

    log.info("Convertido %s -> %s (%s paginas, %s visuales, modelo=%s)",
             pbix.name, destino, resultado.pages, resultado.visuals,
             resultado.model_status)
    return resultado


def _exportar_modelo(pbix: Path, destino: Path, nombre: str,
                     contents: PbixContents, *, timeout: int,
                     close_after: bool, reuse_open: bool) -> Dict[str, Any]:
    """Abre el .pbix en Desktop y serializa su modelo a TMDL."""
    from config import ActiveModel
    from powerbi import desktop_launcher, tmdl_export

    sm_dir = destino / f"{nombre}.SemanticModel"
    definition = sm_dir / "definition"
    salida: Dict[str, Any] = {"semantic_model_dir": str(sm_dir), "warnings": [],
                              "written": []}

    abierto = desktop_launcher.open_pbix(pbix, timeout=timeout,
                                         reuse_open=reuse_open)
    salida["reused_open_session"] = not abierto.launched_by_us
    salida["wait_seconds"] = abierto.waited_seconds
    try:
        instancia = abierto.instance
        modelo_activo = ActiveModel(
            host=instancia["host"],
            port=instancia["port"],
            connection_string=instancia["connection_string"],
            catalog=instancia.get("catalog"),
            database_name=instancia.get("database_name"),
            model_name=instancia.get("model_name"),
            pid=instancia.get("pid"),
            process_started=instancia.get("create_time"),
        )
        detalle = tmdl_export.export_to_tmdl(modelo_activo, definition,
                                             overwrite=True)
        tmdl_export.rename_database(definition, nombre)
    finally:
        if close_after:
            try:
                salida["desktop"] = desktop_launcher.close(abierto)
            except Exception as exc:  # noqa: BLE001
                salida["warnings"].append(
                    f"No se pudo cerrar Power BI Desktop: {exc}")
        else:
            salida["desktop"] = {"closed": False, "reason": "close_desktop=false"}

    write_json(sm_dir / ".platform", _platform("SemanticModel", nombre))
    write_json(sm_dir / "definition.pbism", {
        "$schema": SCHEMA_PBISM, "version": PBISM_VERSION, "settings": {}})

    diagrama = _diagrama(contents)
    if diagrama is not None:
        write_json(sm_dir / "diagramLayout.json", diagrama)

    salida["written"] = [
        f"{nombre}.SemanticModel/definition/{f}" for f in detalle["files"]]
    salida["written"].extend([f"{nombre}.SemanticModel/.platform",
                              f"{nombre}.SemanticModel/definition.pbism"])
    salida["status"] = "exported"
    salida.update({k: v for k, v in detalle.items() if k != "files"})
    salida["file_count"] = detalle["file_count"]
    if detalle.get("auto_date_tables"):
        salida["warnings"].append(
            f"El modelo trae {detalle['auto_date_tables']} tablas de fecha "
            "automatica; se conservan tal cual las genera Power BI.")
    return salida


def _diagrama(contents: PbixContents) -> Optional[Dict[str, Any]]:
    """`DiagramLayout` del .pbix: la disposicion de la vista de modelo."""
    import json
    import zipfile

    try:
        with zipfile.ZipFile(contents.path) as zf:
            if "DiagramLayout" not in zf.namelist():
                return None
            texto = pbix_reader.decode_text(zf.read("DiagramLayout")).strip()
        return json.loads(texto) if texto else None
    except (OSError, ValueError, UnicodeError, zipfile.BadZipFile) as exc:
        log.debug("No se pudo copiar DiagramLayout: %s", exc)
        return None


def convert_many(
    pbix_paths: List[Path],
    out_dir: str | Path,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Convierte varios .pbix. Un fallo no detiene al resto del lote."""
    resultados: List[Dict[str, Any]] = []
    fallos: List[Dict[str, Any]] = []
    for pbix in pbix_paths:
        try:
            resultado = convert(pbix, out_dir, **kwargs)
            resultados.append(resultado.to_dict())
        except PowerBIMCPError as exc:
            log.warning("Fallo la conversion de %s: %s", pbix.name, exc.message)
            fallos.append({"source": str(pbix), "error": exc.code,
                           "message": exc.message})
        except Exception as exc:  # noqa: BLE001
            log.exception("Error inesperado convirtiendo %s", pbix.name)
            fallos.append({"source": str(pbix), "error": "unexpected",
                           "message": str(exc)})
    return {
        "converted": resultados,
        "failed": fallos,
        "total": len(pbix_paths),
        "ok_count": len(resultados),
        "failed_count": len(fallos),
    }


def find_pbix(path: str | Path, recursive: bool = False) -> List[Path]:
    """Resuelve la entrada de la tool: un .pbix o una carpeta con varios."""
    p = Path(path).expanduser().resolve()
    if p.is_file():
        if p.suffix.lower() != ".pbix":
            raise PbixConversionError(f"La ruta no es un archivo .pbix: {p}")
        return [p]
    if not p.is_dir():
        raise PbixConversionError(f"La ruta no existe: {p}")
    patron = "**/*.pbix" if recursive else "*.pbix"
    encontrados = sorted(x for x in p.glob(patron) if x.is_file())
    if not encontrados:
        raise PbixConversionError(
            f"No se encontro ningun .pbix en {p}"
            f"{' (ni en sus subcarpetas)' if recursive else ''}.")
    return encontrados
