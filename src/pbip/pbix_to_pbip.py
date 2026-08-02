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
from utils.json_utils import read_json, write_json

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
    report_validation: Optional[Dict[str, Any]] = None
    publication: Optional[Dict[str, Any]] = None

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
            "report_validation": self.report_validation,
            "publication": self.publication,
        }


def _nombre_proyecto(pbix: Path, nombre: Optional[str]) -> str:
    elegido = (nombre or pbix.stem).strip()
    prohibidos = '<>:"/\\|?*'
    limpio = "".join("_" if c in prohibidos else c for c in elegido).rstrip(". ")
    if not limpio:
        raise PbixConversionError(
            f"No se pudo derivar un nombre de proyecto valido de '{pbix.name}'.")
    from services import paths as safe_paths

    try:
        safe_paths.safe_identifier(limpio, kind="nombre de proyecto")
    except PowerBIMCPError as exc:
        raise PbixConversionError(
            f"El nombre de proyecto '{elegido}' no es valido en Windows.",
            details={"project_name": elegido, "cause": exc.to_dict()}) from exc
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


def _nombre_json(datos: bytes) -> Optional[str]:
    """Lee solamente el ``name`` de una parte PBIR, sin modificarla."""
    import json as _json

    try:
        contenido = _json.loads(pbix_reader.decode_text(datos).lstrip("\ufeff"))
    except (ValueError, UnicodeError):
        return None
    nombre = contenido.get("name") if isinstance(contenido, dict) else None
    return nombre if isinstance(nombre, str) and nombre else None


def _normalizar_rutas_pbir(partes: Dict[str, bytes],
                           avisos: List[str]) -> Dict[str, bytes]:
    """Alinea carpetas de pagina/visual con sus identificadores internos.

    Algunos PBIX report-only empaquetan ``pages/Resumen/page.json`` aunque el
    ``name`` real sea un token y ``pages.json`` apunte a ese token. Al copiar
    literalmente, el CLI devuelve ``PBIR_PAGE_JSON_MISSING``. En un proyecto
    PBIR la ruta es identidad, por eso se conserva cada byte y solo se remapea
    su carpeta al ``name`` declarado por el propio archivo.
    """
    from services import paths as safe_paths

    paginas: Dict[str, str] = {}
    visuales: Dict[tuple[str, str], str] = {}
    for relativa, datos in partes.items():
        trozos = relativa.split("/")
        if len(trozos) == 3 and trozos[0] == "pages" \
                and trozos[2] == "page.json":
            nombre = _nombre_json(datos)
            if nombre:
                safe_paths.safe_identifier(nombre, kind="id de pagina PBIR")
                paginas[trozos[1]] = nombre
        elif len(trozos) == 5 and trozos[0] == "pages" \
                and trozos[2] == "visuals" and trozos[4] == "visual.json":
            nombre = _nombre_json(datos)
            if nombre:
                safe_paths.safe_identifier(nombre, kind="id de visual PBIR")
                visuales[(trozos[1], trozos[3])] = nombre

    salida: Dict[str, bytes] = {}
    origen_por_destino: Dict[str, str] = {}
    cambios_pagina: set[tuple[str, str]] = set()
    cambios_visual: set[tuple[str, str]] = set()
    for relativa, datos in partes.items():
        trozos = relativa.split("/")
        pagina_origen = trozos[1] if len(trozos) > 1 and trozos[0] == "pages" else None
        if pagina_origen in paginas:
            pagina_destino = paginas[pagina_origen]
            if pagina_destino != pagina_origen:
                trozos[1] = pagina_destino
                cambios_pagina.add((pagina_origen, pagina_destino))
        if len(trozos) > 3 and trozos[0] == "pages" and trozos[2] == "visuals" \
                and pagina_origen is not None:
            visual_origen = trozos[3]
            visual_destino = visuales.get((pagina_origen, visual_origen))
            if visual_destino and visual_destino != visual_origen:
                trozos[3] = visual_destino
                cambios_visual.add((visual_origen, visual_destino))
        destino = "/".join(trozos)
        clave = destino.casefold()
        if clave in origen_por_destino:
            raise PbixConversionError(
                "Dos partes PBIR apuntan al mismo archivo despues de alinear "
                "sus identificadores internos.",
                details={"parts": [origen_por_destino[clave], relativa],
                         "target": destino})
        origen_por_destino[clave] = relativa
        salida[destino] = datos

    if cambios_pagina:
        avisos.append(
            f"Se alinearon {len(cambios_pagina)} carpeta(s) de pagina con su "
            "identificador PBIR interno.")
    if cambios_visual:
        avisos.append(
            f"Se alinearon {len(cambios_visual)} carpeta(s) de visual con su "
            "identificador PBIR interno.")
    return salida


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
        informe = _json.loads(pbix_reader.decode_text(datos).lstrip("\ufeff"))
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
    partes = _normalizar_rutas_pbir(contents.pbir_parts, avisos)
    for relativa, datos in sorted(partes.items()):
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


def _normalizar_tema_personalizado(report_dir: Path,
                                   avisos: List[str]) -> None:
    """Iguala nombre declarado, nombre de archivo y ``name`` interno.

    El layout heredado puede llevar un archivo renombrado por Desktop cuyo JSON
    conserva el nombre original. PBIR no tolera esa discrepancia y el CLI la
    reporta como ``PBIR_THEME_FILE_NAME_MISMATCH``. El nombre autoritativo es
    el declarado en ``themeCollection`` y en el paquete de recursos.
    """
    from services import paths as safe_paths

    report_json = report_dir / "definition" / "report.json"
    if not report_json.exists():
        return
    informe = read_json(report_json)
    tema = (informe.get("themeCollection") or {}).get("customTheme") or {}
    declarado = tema.get("name")
    if not isinstance(declarado, str) or not declarado:
        return

    item = None
    for paquete in informe.get("resourcePackages") or []:
        if paquete.get("type") != "RegisteredResources":
            continue
        candidatos = [i for i in paquete.get("items") or []
                      if i.get("type") == "CustomTheme"]
        item = next((i for i in candidatos
                     if i.get("name") == declarado or i.get("path") == declarado),
                    candidatos[0] if len(candidatos) == 1 else None)
        if item:
            break
    if not item:
        return
    relativa = item.get("path")
    if not isinstance(relativa, str) or not relativa:
        return

    base = report_dir / "StaticResources" / "RegisteredResources"
    archivo = safe_paths.ensure_contained(
        base, base / relativa, kind="archivo de tema personalizado")
    if not archivo.is_file():
        return
    try:
        contenido = read_json(archivo)
    except Exception:                                  # el CLI dara el error preciso
        return
    if contenido.get("name") == declarado:
        return
    contenido["name"] = declarado
    write_json(archivo, contenido)
    avisos.append(
        f"El tema personalizado declaraba internamente otro nombre; se igualo "
        f"a '{declarado}' para que PBIR no rechace el archivo.")


_ERRORES_TEMA_REPARABLES = frozenset({
    "PBIR_THEME_VISUAL_PROP_UNKNOWN",
    "PBIR_FORMATTING_OBJECT_UNKNOWN",
})


def _quitar_propiedad_visual_tema(tema: Dict[str, Any], ruta: str) -> int:
    """Quita una propiedad obsoleta ``objeto.propiedad`` del tema."""
    partes = ruta.split(".")
    if len(partes) != 2:
        return 0
    objeto, propiedad = partes
    eliminadas = 0
    estilos = tema.get("visualStyles")
    if not isinstance(estilos, dict):
        return 0
    for selectores in estilos.values():
        if not isinstance(selectores, dict):
            continue
        for objetos in selectores.values():
            if not isinstance(objetos, dict):
                continue
            instancias = objetos.get(objeto)
            if not isinstance(instancias, list):
                continue
            for instancia in instancias:
                if isinstance(instancia, dict) and propiedad in instancia:
                    del instancia[propiedad]
                    eliminadas += 1
    return eliminadas


def _quitar_objeto_visual_tema(tema: Dict[str, Any], ruta: str) -> int:
    """Quita un objeto obsoleto descrito como ``visualStyles.V.S.O``."""
    partes = ruta.split(".")
    if len(partes) != 4 or partes[0] != "visualStyles":
        return 0
    _, tipo_patron, selector_patron, objeto = partes
    estilos = tema.get("visualStyles")
    if not isinstance(estilos, dict):
        return 0
    eliminados = 0
    for tipo, selectores in estilos.items():
        if tipo_patron != "*" and tipo != tipo_patron:
            continue
        if not isinstance(selectores, dict):
            continue
        for selector, objetos in selectores.items():
            if selector_patron != "*" and selector != selector_patron:
                continue
            if isinstance(objetos, dict) and objeto in objetos:
                del objetos[objeto]
                eliminados += 1
    return eliminados


def _reparar_temas_obsoletos(report_dir: Path, errores: List[Any]) -> int:
    """Aplica exclusivamente las incompatibilidades señaladas por el CLI.

    No se mantiene una lista inventada de propiedades: el validador oficial
    identifica el archivo y la ruta exacta. Solo se podan objetos/propiedades
    de tema que el PBIR actual declara desconocidos; datos, consultas y
    recursos permanecen intactos.
    """
    from collections import defaultdict
    from services import paths as safe_paths

    por_archivo: Dict[str, List[Any]] = defaultdict(list)
    for error in errores:
        if error.code in _ERRORES_TEMA_REPARABLES:
            por_archivo[error.file].append(error)

    total = 0
    for relativa, diagnosticos in por_archivo.items():
        if not relativa.startswith("StaticResources/RegisteredResources/"):
            continue
        archivo = safe_paths.ensure_contained(
            report_dir, report_dir / relativa,
            kind="tema señalado por el validador oficial")
        if not archivo.is_file() or archivo.suffix.lower() != ".json":
            continue
        try:
            tema = read_json(archivo)
        except Exception:                              # el CLI conserva el error
            continue
        cambios = 0
        for diagnostico in diagnosticos:
            if diagnostico.code == "PBIR_THEME_VISUAL_PROP_UNKNOWN":
                cambios += _quitar_propiedad_visual_tema(
                    tema, diagnostico.path)
            elif diagnostico.code == "PBIR_FORMATTING_OBJECT_UNKNOWN":
                cambios += _quitar_objeto_visual_tema(tema, diagnostico.path)
        if cambios:
            write_json(archivo, tema)
            total += cambios
    return total


def _validar_informe_convertido(report_dir: Path) -> Dict[str, Any]:
    """Exige que el informe generado no tenga errores del CLI oficial."""
    from services import report_validator

    resultado = report_validator.validar_informe(report_dir)
    if resultado.status == report_validator.UNAVAILABLE:
        return {"checked": False, "reason": resultado.detail}
    if resultado.status == report_validator.TIMEOUT:
        raise PbixConversionError(
            "El validador oficial no respondio; no se publica un proyecto que "
            "no se pudo comprobar.", details=resultado.to_envelope())
    errores_obj = [d for d in resultado.diagnostics if d.severity == "error"]
    reparaciones = 0
    if errores_obj and all(d.code in _ERRORES_TEMA_REPARABLES
                           for d in errores_obj):
        reparaciones = _reparar_temas_obsoletos(report_dir, errores_obj)
        if reparaciones:
            resultado = report_validator.validar_informe(report_dir)
            if resultado.status in (report_validator.UNAVAILABLE,
                                     report_validator.TIMEOUT):
                raise PbixConversionError(
                    "El validador oficial no estuvo disponible despues de reparar un "
                    "tema heredado; no se publico el proyecto.",
                    details=resultado.to_envelope())
            errores_obj = [d for d in resultado.diagnostics
                           if d.severity == "error"]
    errores = [d.__dict__ for d in errores_obj]
    if errores:
        raise PbixConversionError(
            "La conversion produjo un informe que Power BI no aceptaria; no se "
            "publico ningun proyecto parcial.",
            details={"diagnostics": errores, "report_dir": str(report_dir)})
    return {"checked": True, "status": resultado.status,
            "warnings": resultado.warnings,
            "diagnostics": len(resultado.diagnostics),
            "theme_repairs": reparaciones}


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


def _construir_en_stage(
    pbix: Path,
    contents: PbixContents,
    conversion: Optional[layout_to_pbir.LayoutConversion],
    stage: Path,
    destino_final: Path,
    nombre: str,
    *,
    tiene_modelo: bool,
    quiere_modelo: bool,
    dataset_connection_string: Optional[str],
    desktop_timeout: int,
    close_desktop: bool,
    reuse_open_desktop: bool,
) -> ConversionResult:
    """Construye y valida todo sin hacer visible el destino final."""
    report_dir = stage / f"{nombre}.Report"
    definition_dir = report_dir / "definition"
    definition_dir.mkdir(parents=True, exist_ok=True)

    resultado = ConversionResult(
        source=str(pbix),
        project_dir=str(destino_final),
        pbip_path=str(destino_final / f"{nombre}.pbip"),
        report_dir=str(destino_final / f"{nombre}.Report"),
    )
    resultado.warnings.extend(contents.warnings)
    escritos = resultado.files_written

    if conversion is None:
        resultado.report_source = "pbir_copied"
        _copiar_pbir(contents, definition_dir, escritos, stage,
                     resultado.warnings)
        resultado.pages = sum(
            1 for p in contents.pbir_parts if p.endswith("/page.json"))
        resultado.visuals = sum(
            1 for p in contents.pbir_parts if p.endswith("/visual.json"))
        log.info("%s ya venia en PBIR: copiadas %s partes",
                 pbix.name, len(contents.pbir_parts))
    else:
        resultado.report_source = "layout_converted"
        _escribir_conversion(conversion, definition_dir, escritos, stage)
        resultado.pages = len(conversion.pages)
        resultado.visuals = conversion.visual_count
        resultado.warnings.extend(conversion.warnings)
        resultado.dropped.extend(conversion.dropped)

    _copiar_recursos(contents, report_dir, escritos, stage)

    write_json(report_dir / ".platform", _platform("Report", nombre))
    write_json(report_dir / "definition.pbir", {
        "$schema": SCHEMA_PBIR,
        "version": PBIR_VERSION,
        "datasetReference": _referencia_dataset(
            nombre, tiene_modelo, dataset_connection_string),
    })
    escritos.extend([f"{nombre}.Report/.platform",
                     f"{nombre}.Report/definition.pbir"])

    _normalizar_tema_personalizado(report_dir, resultado.warnings)
    resultado.report_validation = _validar_informe_convertido(report_dir)
    reparaciones_tema = resultado.report_validation.get("theme_repairs", 0)
    if reparaciones_tema:
        resultado.warnings.append(
            f"El validador oficial detecto {reparaciones_tema} propiedad(es) "
            "u objeto(s) de tema obsoletos; se retiraron y el informe se "
            "valido de nuevo.")

    if quiere_modelo:
        modelo = _exportar_modelo(
            pbix, stage, nombre, contents, timeout=desktop_timeout,
            close_after=close_desktop, reuse_open=reuse_open_desktop)
        modelo.pop("semantic_model_dir", None)          # apuntaba al staging
        resultado.semantic_model_dir = str(
            destino_final / f"{nombre}.SemanticModel")
        # `tmdl_export` devuelve también la carpeta `definition` en `path`.
        # Construimos dentro del staging, pero esa carpeta se elimina justo
        # después de publicar: exponerla en la respuesta entregaba una ruta
        # aparentemente válida que ya no existía. Toda ruta pública debe
        # señalar al árbol final, no a la implementación transitoria.
        if "path" in modelo:
            modelo["path"] = str(
                Path(resultado.semantic_model_dir) / "definition")
        resultado.model_status = modelo.pop("status", "skipped")
        resultado.model = modelo
        resultado.warnings.extend(modelo.pop("warnings", []))
        escritos.extend(modelo.get("written", []))
    elif contents.has_data_model:
        resultado.model_status = "skipped"
        resultado.warnings.append(
            "El .pbix lleva modelo de datos pero se pidio no exportarlo: el "
            ".pbip queda sin '.SemanticModel' y no abrira en Desktop hasta "
            "que se genere.")
    else:
        resultado.model_status = "absent"

    write_json(stage / f"{nombre}.pbip", {
        "$schema": SCHEMA_PBIP,
        "version": PBIP_VERSION,
        "artifacts": [{"report": {"path": f"{nombre}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
    escritos.append(f"{nombre}.pbip")
    return resultado


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

    if destino.exists() and not destino.is_dir():
        raise PbixConversionError(
            f"El destino existe pero no es una carpeta: {destino}.",
            details={"path": str(destino)})
    if destino.exists() and any(destino.iterdir()) and not overwrite:
        raise PbixConversionError(
            f"La carpeta de destino ya existe y no esta vacia: {destino}. "
            "Usa overwrite=true si quieres reemplazarla.",
            details={"path": str(destino)})

    from services import project_publish

    stage = project_publish.create_stage(destino.parent)
    limpio = True
    try:
        resultado = _construir_en_stage(
            pbix, contents, conversion, stage, destino, nombre,
            tiene_modelo=tiene_modelo, quiere_modelo=quiere_modelo,
            dataset_connection_string=dataset_connection_string,
            desktop_timeout=desktop_timeout, close_desktop=close_desktop,
            reuse_open_desktop=reuse_open_desktop)
        resultado.publication = project_publish.publish_tree(
            stage, destino, overwrite=overwrite,
            tool="pbi_convert_pbix_to_pbip")
    finally:
        limpio = project_publish.discard_stage(stage)

    if not limpio:
        resultado.warnings.append(
            "La conversion se publico, pero no se pudo retirar su carpeta "
            "temporal de staging; puede limpiarse manualmente cuando deje de "
            "estar en uso.")

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
