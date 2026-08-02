"""Lectura del contenedor .pbix (paquete OPC / ZIP).

Un .pbix es un ZIP con partes de nombre fijo. Las que nos importan para
convertir a .pbip son:

- ``Report/Layout``        informe en formato HEREDADO (JSON en UTF-16LE, con
                           sub-JSON serializados dentro de strings).
- ``Report/definition/``   informe ya en PBIR: Power BI Desktop reciente guarda
                           el formato mejorado DENTRO del propio .pbix. Cuando
                           esta presente, convertir es copiar.
- ``Report/StaticResources/``  temas e imagenes.
- ``Report/CustomVisuals/``    visuales personalizados empaquetados.
- ``DataModel``            backup ABF comprimido con XPress9. NO es legible sin
                           el motor de Analysis Services (ver powerbi/tmdl_export).
- ``Connections``          conexiones a datasets remotos (informes conectados).

Las partes de texto no traen codificacion declarada: Power BI escribe unas en
UTF-16LE y otras en UTF-8 sin ningun criterio estable, asi que se detecta.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("pbix_reader")

LAYOUT_PART = "Report/Layout"
PBIR_PREFIX = "Report/definition/"
STATIC_PREFIX = "Report/StaticResources/"
CUSTOM_VISUALS_PREFIX = "Report/CustomVisuals/"
DATA_MODEL_PART = "DataModel"

#: Marca del stream DataModel. Confirma que es un backup ABF y no otra cosa.
_XPRESS9_MARK = "backup was created using XPress9"


class PbixReadError(PowerBIMCPError):
    code = "pbix_read_error"


@dataclass
class PbixContents:
    """Lo que hay dentro de un .pbix, ya decodificado."""

    path: str
    #: "pbir" (definition/ embebido), "layout" (heredado) o "none".
    report_format: str
    #: Informe heredado ya parseado (solo si report_format == "layout").
    layout: Optional[Dict[str, Any]] = None
    #: Rutas relativas a Report/definition/ -> bytes (solo si report_format == "pbir").
    pbir_parts: Dict[str, bytes] = field(default_factory=dict)
    #: Rutas relativas a Report/ -> bytes, para StaticResources y CustomVisuals.
    report_assets: Dict[str, bytes] = field(default_factory=dict)
    has_data_model: bool = False
    data_model_size: int = 0
    connections: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None
    settings: Optional[Dict[str, Any]] = None
    version: Optional[str] = None
    parts: List[str] = field(default_factory=list)
    #: Problemas encontrados al leer que no impiden convertir.
    warnings: List[str] = field(default_factory=list)

    @property
    def is_thin_report(self) -> bool:
        """Informe conectado a un dataset remoto: no lleva modelo propio."""
        return not self.has_data_model

    @property
    def remote_artifacts(self) -> List[Dict[str, Any]]:
        conns = self.connections or {}
        return list(conns.get("RemoteArtifacts") or [])

    def summary(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "pbix_version": self.version,
            "report_format": self.report_format,
            "has_data_model": self.has_data_model,
            "data_model_size": self.data_model_size,
            "page_count": len((self.layout or {}).get("sections", []))
            if self.report_format == "layout"
            else sum(1 for p in self.pbir_parts if p.endswith("/page.json")),
            "static_resources": sum(
                1 for p in self.report_assets if p.startswith("StaticResources/")),
            "custom_visual_packages": len({
                p.split("/")[1] for p in self.report_assets
                if p.startswith("CustomVisuals/") and "/" in p[len("CustomVisuals/"):]
            }),
            "remote_artifacts": self.remote_artifacts,
            "warnings": self.warnings,
        }


def decode_text(raw: bytes, errors: str = "strict") -> str:
    """Decodifica una parte de texto de un .pbix detectando su codificacion.

    Power BI escribe `Report/Layout`, `Settings` y `Metadata` en UTF-16LE sin
    BOM, pero `Connections` y las partes PBIR en UTF-8. Sin BOM la unica pista
    fiable es el patron de bytes nulos intercalados del UTF-16LE.

    `errors` se pasa tal cual al decodificador: util cuando se lee un trozo
    suelto (una cabecera cortada parte un caracter por la mitad).
    """
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", errors=errors)
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", errors=errors)
    # UTF-16LE sin BOM: el segundo byte de un caracter ASCII es 0x00.
    muestra = raw[:64]
    if len(muestra) >= 2 and muestra[1::2].count(0) > len(muestra[1::2]) // 2:
        return raw.decode("utf-16-le", errors=errors)
    return raw.decode("utf-8", errors=errors)


def _read_json_part(zf: zipfile.ZipFile, name: str) -> Optional[Dict[str, Any]]:
    """Lee una parte JSON. Devuelve None si no existe o no es JSON valido."""
    if name not in zf.namelist():
        return None
    try:
        texto = decode_text(zf.read(name)).strip()
    except (ValueError, UnicodeError) as exc:
        log.warning("No se pudo decodificar la parte '%s': %s", name, exc)
        return None
    if not texto:
        return None
    try:
        data = json.loads(texto)
    except ValueError as exc:
        log.warning("La parte '%s' no es JSON valido: %s", name, exc)
        return None
    return data if isinstance(data, dict) else {"value": data}


def _partes_copiables(nombres: List[str], prefijo: str) -> Dict[str, str]:
    """Valida rutas del ZIP antes de convertirlas en rutas de Windows.

    ``zipfile.read`` no extrae los archivos, pero el conversor si materializa
    sus nombres despues. Un ``../`` o dos nombres que solo difieren por
    mayusculas podrian escapar del staging o sobrescribirse silenciosamente en
    NTFS. Cada componente se trata como identificador y el namespace se compara
    de forma insensible a mayusculas, igual que el destino real.
    """
    from services import paths as safe_paths

    salida: Dict[str, str] = {}
    for nombre in nombres:
        if not nombre.startswith(prefijo) or nombre.endswith("/"):
            continue
        relativa = nombre[len(prefijo):]
        componentes = relativa.split("/")
        if not relativa or any(not parte for parte in componentes):
            raise PbixReadError(
                f"La parte '{nombre}' tiene una ruta vacia o ambigua.",
                details={"part": nombre, "prefix": prefijo})
        try:
            for parte in componentes:
                safe_paths.safe_identifier(
                    parte, kind="componente de ruta dentro del .pbix")
        except PowerBIMCPError as exc:
            raise PbixReadError(
                f"La parte '{nombre}' contiene una ruta insegura; no se "
                "materializo ningun archivo.",
                details={"part": nombre, "cause": exc.to_dict()},
            ) from exc
        clave = relativa.casefold()
        anterior = salida.get(clave)
        if anterior is not None:
            raise PbixReadError(
                "El .pbix contiene dos partes que chocarian en Windows: "
                f"'{prefijo}{anterior}' y '{nombre}'.",
                details={"parts": [f"{prefijo}{anterior}", nombre]},
            )
        salida[clave] = relativa
    return {relativa: f"{prefijo}{relativa}" for relativa in salida.values()}


def _leer_layout(zf: zipfile.ZipFile, p: Path,
                 avisos: List[str]) -> Dict[str, Any]:
    """`Report/Layout` -> dict. Tolera texto mal codificado, pero lo dice.

    Algunos .pbix guardan en el layout texto con codificacion rota (tipico de
    informes que pasaron por copiar y pegar). Fallar entero por un caracter
    seria peor que convertir y avisar de que ese texto se sustituyo.
    """
    crudo = zf.read(LAYOUT_PART)
    for modo in ("strict", "replace"):
        try:
            texto = decode_text(crudo, errors=modo)
        except UnicodeError:
            continue
        try:
            datos = json.loads(texto)
        except ValueError as exc:
            if modo == "replace":
                raise PbixReadError(
                    f"'Report/Layout' de {p.name} no es JSON valido: {exc}",
                    details={"part": LAYOUT_PART},
                ) from exc
            continue
        if modo == "replace":
            avisos.append(
                "El informe tenia texto con codificacion invalida; esos "
                "caracteres se sustituyeron por '�' al convertir.")
            log.warning("Layout de %s decodificado en modo tolerante", p.name)
        return datos
    raise PbixReadError(
        f"No se pudo decodificar 'Report/Layout' de {p.name}.",
        details={"part": LAYOUT_PART},
    )


def read_pbix(path: str | Path) -> PbixContents:
    """Abre un .pbix y devuelve su contenido decodificado.

    No toca el archivo: se abre en solo lectura y se cierra al salir.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise PbixReadError(f"El archivo .pbix no existe: {p}")
    if p.suffix.lower() != ".pbix":
        raise PbixReadError(f"La ruta no es un archivo .pbix: {p}")

    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile as exc:
        raise PbixReadError(
            f"El archivo no es un .pbix valido (no se pudo abrir como ZIP): {p}. "
            "Si el informe esta abierto en Power BI Desktop y sin guardar, "
            "guardalo antes de convertir.",
            details={"cause": str(exc)},
        ) from exc

    with zf:
        nombres = zf.namelist()
        contents = PbixContents(path=str(p), report_format="none", parts=nombres)

        version = _read_json_part(zf, "Version")
        if version is None and "Version" in nombres:
            contents.version = decode_text(zf.read("Version")).strip()
        contents.connections = _read_json_part(zf, "Connections")
        contents.metadata = _read_json_part(zf, "Metadata")
        contents.settings = _read_json_part(zf, "Settings")

        pbir = _partes_copiables(nombres, PBIR_PREFIX)
        recursos_estaticos = _partes_copiables(nombres, STATIC_PREFIX)
        visuales_personalizados = _partes_copiables(
            nombres, CUSTOM_VISUALS_PREFIX)
        if pbir:
            contents.report_format = "pbir"
            contents.pbir_parts = {
                relativa: zf.read(nombre) for relativa, nombre in pbir.items()
            }
        elif LAYOUT_PART in nombres:
            contents.report_format = "layout"
            contents.layout = _leer_layout(zf, p, contents.warnings)

        for prefijo, partes in (
                (STATIC_PREFIX, recursos_estaticos),
                (CUSTOM_VISUALS_PREFIX, visuales_personalizados)):
            for relativa, nombre in partes.items():
                ruta_reporte = f"{prefijo[len('Report/'):]}{relativa}"
                contents.report_assets[ruta_reporte] = zf.read(nombre)

        if DATA_MODEL_PART in nombres:
            info = zf.getinfo(DATA_MODEL_PART)
            contents.has_data_model = info.file_size > 0
            contents.data_model_size = info.file_size
            with zf.open(DATA_MODEL_PART) as fh:
                cabecera = fh.read(128)
            # El corte a 128 bytes puede partir un caracter: aqui solo se
            # comprueba una marca, asi que se decodifica sin exigir validez.
            if _XPRESS9_MARK not in decode_text(cabecera, errors="replace"):
                log.info(
                    "El stream DataModel de %s no lleva la marca XPress9 esperada; "
                    "se extraera igual desde el motor en vivo.", p.name)

    log.info("Leido %s: informe=%s modelo=%s",
             p.name, contents.report_format, contents.has_data_model)
    return contents
