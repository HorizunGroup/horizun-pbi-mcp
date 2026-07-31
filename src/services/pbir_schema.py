"""Fase E3.1 — validacion PBIR contra los esquemas OFICIALES completos.

Que habia en E3 y por que no bastaba
------------------------------------
Un validador casero que solo interpretaba `type`, `required`, `properties`,
`items` y `enum`, contra esquemas escritos a mano. Ignoraba `$ref`, `anyOf`,
`oneOf`, `allOf`, `additionalProperties`, `pattern`, `minimum`... es decir, casi
todo lo que el PBIR usa de verdad. Mejoraba la seguridad estructural, pero
llamarlo "validacion contra el esquema oficial" habria sido falso.

Ahora
-----
- Los esquemas son los **oficiales exactos** de `developer.microsoft.com`,
  con su cierre transitivo de `$ref` (22 documentos), cada uno con su SHA-256
  fijado en `schemas/pbir_manifest.json`.
- La validacion la hace **jsonschema**, con el draft que declare cada documento.
- Las referencias se resuelven contra un `referencing.Registry` construido
  **solo** con los documentos del manifiesto: no hay acceso a red ni resolucion
  de URLs arbitrarias.

Por que no estan en el repositorio
----------------------------------
No declaran licencia ni permiso de redistribucion. Copiarlos seria redistribuir
software de terceros sin autorizacion. Se instalan con
`scripts/fetch_pbir_schemas.py`, que verifica hashes antes de instalar. **Sin
cache instalada las escrituras fallan cerradas** con `schema_unavailable`: no se
degrada en silencio a "no compruebo nada".

Limitacion conocida: esquemas que Power BI declara y Microsoft no publica
-------------------------------------------------------------------------
El informe de referencia (PB4) declara `visualContainer/2.10.0` en **239**
archivos y `bookmarks/2.0.0`; ambas URLs devuelven **404** en el origen
oficial. Sin el documento no hay forma de comprobar lo que se escribiria, asi
que se bloquea con `schema_unavailable` y `rule=no_publicado_upstream`.

Consecuencia practica: sobre un informe guardado con una version reciente de
Power BI Desktop, este servidor **no puede escribir** los visuales que declaren
2.10.0. Es deliberado y fail-closed. La alternativa —validar contra 2.7.0 y
confiar en que no cambio nada— seria adivinar, y `additionalProperties: false`
rechazaria propiedades nuevas legitimas.

Por esto E3 queda **parcialmente cerrado** y G10 abierto.

Cobertura medida sobre el PB4 real (solo lectura, 443 JSON del informe):
176 cumplen, 240 bloqueados por esquema no publicado, 25 fuera del ambito
(recursos de visuales personalizados), 2 incumplen de verdad (`bookmark.json`
sin propiedades obligatorias, defecto del propio informe).

Taxonomia
---------
=========================== ==================================================
`invalid_json`              el contenido no parsea
`schema_unsupported`        el `$schema` declarado no esta en el manifiesto
`schema_unavailable`        esta en el manifiesto pero falta o no cuadra el hash
`schema_validation_failed`  parsea, el esquema se conoce, y no cumple
=========================== ==================================================
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from powerbi.errors import PowerBIMCPError

MANIFIESTO = Path(__file__).resolve().parent / "schemas" / "pbir_manifest.json"

#: Archivos PBIR que DEBEN declarar `$schema`. Si falta, se bloquea.
#: La clave es el nombre del archivo; el valor, el esquema que le corresponde
#: por ubicacion segun la documentacion oficial.
POR_UBICACION = {
    "visual.json": "definition/visualContainer/2.7.0/schema.json",
    "page.json": "definition/page/2.1.0/schema.json",
    "pages.json": "definition/pagesMetadata/1.1.0/schema.json",
    "report.json": "definition/report/2.0.0/schema.json",
    "definition.pbir": "definitionProperties/2.0.0/schema.json",
    # Los informes reales traen marcadores y metadatos de version. Sin
    # conocerlos, cualquier informe con marcadores quedaba bloqueado como
    # "tipo desconocido".
    "bookmarks.json": "definition/bookmarksMetadata/1.0.0/schema.json",
    "version.json": "definition/versionMetadata/1.0.0/schema.json",
}

#: Sufijos (no nombres exactos) de tipos PBIR conocidos.
POR_SUFIJO = {
    ".bookmark.json": "definition/bookmark/2.1.0/schema.json",
}

#: Excepciones documentadas: archivos del PBIR que NO exigen `$schema` y sobre
#: los que este servidor no valida. Cualquier otro nombre se bloquea.
SIN_ESQUEMA_PERMITIDO = {
    ".platform",              # metadatos de Fabric, fuera del ambito del informe
    "localSettings.json",     # ajustes locales del usuario, no versionables
    "reportExtension.json",   # extensiones; su esquema no es de los cinco raiz
}

_REGISTRO_CACHE: Any = None
_MANIFIESTO_CACHE: Optional[Dict[str, Any]] = None


class SchemaUnsupported(PowerBIMCPError):
    """El `$schema` declarado no esta en el manifiesto oficial."""

    code = "schema_unsupported"


class SchemaUnavailable(PowerBIMCPError):
    """Los esquemas no estan instalados, o su hash no coincide."""

    code = "schema_unavailable"


class SchemaValidationFailed(PowerBIMCPError):
    """El JSON parsea pero no cumple su esquema oficial."""

    code = "schema_validation_failed"


# --------------------------------------------------------------- manifiesto ---
def manifiesto() -> Dict[str, Any]:
    global _MANIFIESTO_CACHE
    if _MANIFIESTO_CACHE is None:
        try:
            _MANIFIESTO_CACHE = json.loads(MANIFIESTO.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SchemaUnavailable(
                f"No se pudo leer el manifiesto de esquemas {MANIFIESTO}: {exc}. "
                "La instalacion esta incompleta.",
                details={"manifest": str(MANIFIESTO)}) from exc
    return _MANIFIESTO_CACHE


def cache_dir() -> Path:
    """Donde `scripts/fetch_pbir_schemas.py` deja los esquemas verificados.

    NO cuelga de `settings.libs_dir`: las pruebas aislan settings a un tmp_path
    y la cache desapareceria, haciendo fallar toda escritura PBIR con
    `schema_unavailable`. Los esquemas son datos de referencia inmutables, como
    las DLL: su sitio no depende de la configuracion de la sesion.

    Orden: variable de entorno -> repositorio (desarrollo) -> cache del usuario.
    """
    import branding

    personalizado = branding.env("SCHEMAS_DIR")
    if personalizado:
        return Path(personalizado)

    en_repo = Path(__file__).resolve().parents[2] / "schemas_cache" / "pbir"
    if en_repo.exists():
        return en_repo

    import os

    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    return Path(base) / "horizun-pbi-mcp" / "schemas" / "pbir"


def urls_soportadas() -> List[str]:
    return [d["url"] for d in manifiesto()["documents"] if d["root"]]


def estado_cache() -> Dict[str, Any]:
    """Diagnostico: que falta y que no cuadra. No lanza."""
    try:
        m = manifiesto()
    except SchemaUnavailable as exc:
        return {"ready": False, "reason": exc.message, "missing": [], "corrupt": []}

    base = cache_dir()
    faltan, corruptos = [], []
    for d in m["documents"]:
        f = base / d["file"]
        if not f.exists():
            faltan.append(d["file"])
            continue
        if hashlib.sha256(f.read_bytes()).hexdigest() != d["sha256"]:
            corruptos.append(d["file"])
    return {"ready": not faltan and not corruptos, "dir": str(base),
            "expected": len(m["documents"]), "missing": faltan,
            "corrupt": corruptos,
            "reason": ("" if not faltan and not corruptos else
                       f"{len(faltan)} ausente(s), {len(corruptos)} con hash distinto")}


def _exigir_cache() -> Dict[str, Any]:
    estado = estado_cache()
    if not estado["ready"]:
        raise SchemaUnavailable(
            "Los esquemas oficiales del PBIR no estan instalados o su hash no "
            "coincide, asi que no se puede validar lo que se va a escribir. "
            "Ejecuta: python scripts/fetch_pbir_schemas.py",
            details=estado)
    return estado


# ---------------------------------------------------------------- registry ----
def _registry():
    """Registry de `referencing` con SOLO los documentos del manifiesto.

    Es la allowlist: cualquier `$ref` a algo que no este aqui no se resuelve, y
    `referencing` no sale a la red por su cuenta.
    """
    global _REGISTRO_CACHE
    if _REGISTRO_CACHE is not None:
        return _REGISTRO_CACHE

    from referencing import Registry, Resource

    _exigir_cache()
    base = cache_dir()
    recursos = []
    for d in manifiesto()["documents"]:
        doc = json.loads((base / d["file"]).read_text(encoding="utf-8"))
        recursos.append((d["url"], Resource.from_contents(doc)))
    _REGISTRO_CACHE = Registry().with_contents(
        [(url, r.contents) for url, r in recursos])
    return _REGISTRO_CACHE


def limpiar_cache_memoria() -> None:
    """Olvida manifiesto y registry. Para pruebas."""
    global _REGISTRO_CACHE, _MANIFIESTO_CACHE
    _REGISTRO_CACHE = None
    _MANIFIESTO_CACHE = None


def no_publicados() -> Dict[str, str]:
    """`$schema` que Power BI escribe y Microsoft NO publica.

    No es una hipotesis: el informe de referencia declara
    `visualContainer/2.10.0` en 239 archivos y `bookmarks/2.0.0`, y ambas URLs
    devuelven 404 en el origen oficial. Sin el documento no hay forma de
    comprobar lo que se escribiria, asi que se bloquea. Es la limitacion
    conocida de esta version, documentada en docs/SECURITY.md.
    """
    try:
        return {a["url"]: a["reason"]
                for a in manifiesto().get("unavailable_upstream", [])}
    except SchemaUnavailable:                            # pragma: no cover
        return {}


def cargar(url: str) -> Dict[str, Any]:
    """Documento del esquema, verificado contra su hash."""
    ausente = no_publicados().get(url)
    if ausente:
        raise SchemaUnavailable(
            f"Power BI declara el esquema {url}, pero Microsoft no lo publica "
            f"({ausente}). No se puede comprobar lo que se escribiria, asi que "
            "no se escribe. Es una limitacion conocida de esta version.",
            details={"schema": url, "reason": ausente,
                     "rule": "no_publicado_upstream"})

    entrada = next((d for d in manifiesto()["documents"] if d["url"] == url), None)
    if entrada is None:
        raise SchemaUnsupported(
            f"El archivo declara un $schema que no esta en el manifiesto "
            f"oficial: {url}. No se escribe contra un esquema que no se puede "
            "comprobar.",
            details={"schema": url, "supported": urls_soportadas()})
    _exigir_cache()
    ruta = cache_dir() / entrada["file"]
    return json.loads(ruta.read_text(encoding="utf-8"))


# --------------------------------------------------------------- validacion ---
_RE_VALOR = re.compile(r"^.*?(?=\s*(?:is not|does not|was expected))", re.S)


def _mensaje_seguro(err) -> str:
    """Mensaje de jsonschema SIN el valor que lo provoco.

    jsonschema formatea `'<valor>' is not of type 'number'`. Ese valor es dato
    del informe: un titulo, un nombre de medida, un campo del negocio. Se
    conserva la REGLA y se descarta el valor.
    """
    bruto = str(err.message)
    partido = _RE_VALOR.sub("", bruto, count=1).strip()
    return partido or f"incumple '{err.validator}'"


def _ruta_json(err) -> str:
    partes = ["$"]
    for p in err.absolute_path:
        partes.append(f"[{p}]" if isinstance(p, int) else f".{p}")
    return "".join(partes)


def es_documento_pbir(archivo: Optional[Any]) -> bool:
    """Si la ruta esta dentro de un arbol de informe PBIR (`*.Report/`).

    Acota el ambito del validador. `Transaction.write_json` es infraestructura
    generica; fuera de un informe no hay ningun esquema PBIR que aplicar, y
    fingir lo contrario bloquearia escrituras legitimas.
    """
    if archivo is None:
        return False
    partes = [p.lower() for p in Path(archivo).parts]
    if not any(p.endswith(".report") for p in partes):
        return False
    # `StaticResources/` y `CustomVisuals/` guardan recursos de visuales
    # personalizados y temas (package.json, *.pbiviz.json, paletas). Estan
    # DENTRO del informe pero no son documentos PBIR: tienen sus propios
    # formatos, los publica un tercero y nosotros no los escribimos.
    return not ({"staticresources", "customvisuals"} & set(partes))


def _esquema_por_ubicacion(archivo: Optional[Any]) -> Optional[str]:
    if archivo is None:
        return None
    nombre = Path(archivo).name
    sufijo = POR_UBICACION.get(nombre)
    if sufijo is None:
        for terminacion, ruta in POR_SUFIJO.items():
            if nombre.endswith(terminacion):
                sufijo = ruta
                break
    if sufijo is None:
        return None
    return next((u for u in urls_soportadas() if u.endswith(sufijo)), None)


def validar(datos: Any, *, archivo: Optional[Any] = None) -> Dict[str, Any]:
    """Valida un documento COMPLETO contra su esquema oficial.

    Se valida el documento entero, no solo lo que cambio: una escritura puede
    romper una regla que dependa de otra parte del archivo.
    """
    import jsonschema

    if not isinstance(datos, dict):
        return {"validated": False, "reason": "no es un objeto JSON"}

    url = datos.get("$schema")
    if not url:
        # E3.1 punto 9: NO se acepta genericamente cualquier archivo sin
        # $schema. Se clasifica por ubicacion, y solo dentro de un arbol PBIR:
        # fuera de el este validador no tiene nada que decir.
        if not es_documento_pbir(archivo):
            return {"validated": False,
                    "reason": "no es un documento del PBIR"}

        nombre = Path(archivo).name
        if nombre in SIN_ESQUEMA_PERMITIDO:
            return {"validated": False, "reason": "excepcion documentada",
                    "file": nombre}
        if nombre in POR_UBICACION or _esquema_por_ubicacion(archivo):
            # Power BI Desktop SIEMPRE escribe $schema en estos cinco. Que
            # falte significa que el archivo no lo produjo el, y no se puede
            # saber contra que version comprobarlo.
            raise SchemaUnsupported(
                f"'{nombre}' es un tipo PBIR que debe declarar $schema y no lo "
                "declara. No se escribe lo que no se puede comprobar.",
                details={"file": str(archivo), "expected_schema":
                         _esquema_por_ubicacion(archivo),
                         "rule": "tipo_pbir_exige_schema"})
        raise SchemaUnsupported(
            f"'{nombre}' esta dentro del informe pero no es un tipo PBIR "
            "conocido ni una excepcion documentada.",
            details={"file": str(archivo), "known": sorted(POR_UBICACION),
                     "exempt": sorted(SIN_ESQUEMA_PERMITIDO),
                     "rule": "tipo_desconocido_en_pbir"})

    esquema = cargar(url)
    registry = _registry()

    Validador = jsonschema.validators.validator_for(esquema)   # draft correcto
    try:
        Validador.check_schema(esquema)
    except jsonschema.SchemaError as exc:            # pragma: no cover
        raise SchemaUnavailable(
            f"El esquema oficial {url} no es un JSON Schema valido: {exc}",
            details={"schema": url}) from exc

    validador = Validador(esquema, registry=registry)
    errores = sorted(validador.iter_errors(datos), key=lambda e: list(e.absolute_path))

    if errores:
        detalle = [{"path": _ruta_json(e), "rule": e.validator,
                    "error": _mensaje_seguro(e)} for e in errores[:20]]
        raise SchemaValidationFailed(
            f"{len(errores)} error(es) de esquema en "
            f"{Path(archivo).name if archivo else 'el documento'}.",
            details={"file": str(archivo) if archivo else None,
                     "schema": url, "draft": esquema.get("$schema"),
                     "errors": detalle, "error_count": len(errores)})

    return {"validated": True, "schema": url, "draft": esquema.get("$schema"),
            "validator": Validador.__name__}


def esquemas_disponibles() -> List[Dict[str, Any]]:
    estado = estado_cache()
    try:
        docs = manifiesto()["documents"]
    except SchemaUnavailable:
        docs = []
    return [{"schema": d["url"], "file": d["file"], "root": d["root"],
             "present": d["file"] not in estado.get("missing", [])
             and d["file"] not in estado.get("corrupt", [])}
            for d in docs]
