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

Esquemas que Power BI declara y Microsoft no publica
----------------------------------------------------
Power BI escribe versiones de esquema antes de que Microsoft las publique: hoy
`visualContainer` 2.10.0 y 2.11.0 devuelven **404** en el origen oficial. Antes
eso bloqueaba toda escritura sobre cualquier informe guardado con una version
reciente de Desktop, que es casi cualquiera.

Ahora se comprueba contra la version anterior de la MISMA familia
(`version_mas_cercana`) y se perdona **solo** lo que una version posterior pudo
anadir: una propiedad nueva o un valor nuevo de una enumeracion. Un tipo
equivocado o un campo obligatorio ausente sigue bloqueando, que es lo que la
politica queria proteger.

No es una suposicion: se midio sobre **275 archivos reales** que declaran 2.10 o
2.11, y en todos ellos lo UNICO que no cuadraba contra 2.7.0 era la cadena de
version del propio `$schema` (`$.$schema`, regla `const`). El contenido validaba
entero. El formato crece por adicion, y ahora esta comprobado.

Si no hay ninguna version anterior en la cache (`bookmarks/2.0.0` es el caso),
se mantiene el bloqueo: sin nada contra que comparar, adivinar seria peor.

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
from typing import Any, Dict, List, Optional, Tuple

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


#: `.../<familia>/<major>.<minor>.<patch>/schema.json`
_RE_URL = re.compile(r"^(?P<base>.*/)(?P<familia>[^/]+)/"
                     r"(?P<version>\d+\.\d+\.\d+)/schema\.json$")


def _partes(url: str) -> Optional[Tuple[str, str, Tuple[int, int, int]]]:
    m = _RE_URL.match(str(url or ""))
    if not m:
        return None
    v = tuple(int(x) for x in m.group("version").split("."))
    return m.group("base") + m.group("familia"), m.group("familia"), v  # type: ignore[return-value]


def version_mas_cercana(url: str) -> Optional[str]:
    """La version cacheada mas alta de la MISMA familia que no supere a `url`.

    Power BI escribe versiones de esquema que Microsoft tarda en publicar (hoy
    `visualContainer` 2.10 y 2.11 dan 404). Bloquear por eso deja sin editar
    cualquier informe reciente. Como el formato crece por adicion, el esquema
    anterior sigue describiendo la estructura: sirve para comprobar que no se
    rompe nada, aunque no conozca lo que se anadio despues.
    """
    partes = _partes(url)
    if partes is None:
        return None
    prefijo, _familia, objetivo = partes
    candidatas = []
    for doc in manifiesto()["documents"]:
        otras = _partes(doc["url"])
        if otras is None or otras[0] != prefijo:
            continue
        # Solo dentro de la MISMA version mayor. Que el formato crezca por
        # adicion esta comprobado entre 2.7 y 2.11; asumir lo mismo de un salto
        # de mayor seria justo la adivinanza que esta politica evita.
        if otras[2][0] != objetivo[0]:
            continue
        if otras[2] <= objetivo:
            candidatas.append((otras[2], doc["url"]))
    if not candidatas:
        return None
    return max(candidatas)[1]


def cargar(url: str, *, permitir_cercana: bool = False) -> Tuple[Dict[str, Any], str]:
    """Documento del esquema y la URL contra la que se comprobara de verdad.

    Con `permitir_cercana`, un esquema que Microsoft aun no publica cae a la
    version anterior de su familia en vez de bloquear.
    """
    conocido = any(d["url"] == url for d in manifiesto()["documents"])
    if not conocido and permitir_cercana:
        alternativa = version_mas_cercana(url)
        if alternativa:
            _exigir_cache()
            entrada = next(d for d in manifiesto()["documents"] if d["url"] == alternativa)
            ruta = cache_dir() / entrada["file"]
            return json.loads(ruta.read_text(encoding="utf-8")), alternativa

    ausente = no_publicados().get(url)
    if ausente:
        raise SchemaUnavailable(
            f"Power BI declara el esquema {url}, pero Microsoft no lo publica "
            f"({ausente}) y no hay ninguna version anterior de la misma familia "
            "en la cache. No se puede comprobar lo que se escribiria.",
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
    return json.loads(ruta.read_text(encoding="utf-8")), url


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


#: Reglas cuyo incumplimiento se explica por comprobar contra un esquema mas
#: antiguo: una propiedad que se anadio despues, o un valor nuevo de una
#: enumeracion. Todo lo demas (tipo, obligatorio, patron) es un error de verdad.
_REGLAS_DE_NOVEDAD = frozenset({"additionalProperties", "const", "enum"})


def _es_novedad_de_version(err) -> bool:
    """Si el error se explica porque el esquema comprobado es anterior."""
    if err.validator in _REGLAS_DE_NOVEDAD:
        return True
    # Las enumeraciones del PBIR se escriben como `anyOf` de `const`. Solo se
    # perdona si TODAS las alternativas fallaron por ser un valor desconocido.
    if err.validator == "anyOf" and err.context:
        return all(sub.validator in ("const", "enum") for sub in err.context)
    return False


def _ruta_json(err) -> str:
    partes = ["$"]
    for p in err.absolute_path:
        partes.append(f"[{p}]" if isinstance(p, int) else f".{p}")
    return "".join(partes)


def _ruta_hija(ruta: str, clave: Any) -> str:
    """Ruta JSON sin interpolar claves del informe como si fueran codigo."""
    texto = str(clave)
    return f"{ruta}.{texto}" if texto.isidentifier() else f"{ruta}[{texto!r}]"


def _error_formato(ruta: str, regla: str) -> Dict[str, str]:
    """Diagnostico estructural sin copiar valores potencialmente sensibles."""
    return {"path": ruta, "rule": regla,
            "error": "La estructura de formato PBIR no tiene la forma esperada."}


def _validar_expresion_formato(valor: Any, ruta: str,
                               errores: List[Dict[str, str]]) -> None:
    """Comprueba las expresiones de formato que produce este servidor.

    El esquema oficial deja ``objects`` abierto, por lo que aceptaba incluso
    ``solid: {expr: ...}``: JSON valido que Desktop ignora. No se intenta
    enumerar todos los objetos que pueda introducir una version futura de
    Power BI; se comprueban solo los centinelas de las formas que escribimos.
    """
    if not isinstance(valor, dict):
        errores.append(_error_formato(ruta, "format_expression_object"))
        return

    literal = valor.get("Literal")
    if literal is not None:
        if not isinstance(literal, dict) or not isinstance(literal.get("Value"), str):
            errores.append(_error_formato(ruta, "format_literal_value"))

    regla = valor.get("FillRule")
    if regla is None:
        return
    if not isinstance(regla, dict):
        errores.append(_error_formato(ruta, "format_fill_rule_object"))
        return
    if not isinstance(regla.get("Input"), dict):
        errores.append(_error_formato(_ruta_hija(ruta, "FillRule"),
                                      "format_fill_rule_input"))

    gradientes = regla.get("FillRule")
    ruta_gradientes = _ruta_hija(_ruta_hija(ruta, "FillRule"), "FillRule")
    if not isinstance(gradientes, dict):
        errores.append(_error_formato(ruta_gradientes, "format_fill_rule_gradient"))
        return
    nombres = [n for n in ("linearGradient2", "linearGradient3") if n in gradientes]
    if len(nombres) != 1 or not isinstance(gradientes[nombres[0]], dict):
        errores.append(_error_formato(ruta_gradientes, "format_fill_rule_gradient"))
        return

    gradiente = gradientes[nombres[0]]
    paradas = ("min", "max") if nombres[0] == "linearGradient2" else ("min", "mid", "max")
    for parada in paradas:
        color = gradiente.get(parada)
        ruta_color = _ruta_hija(_ruta_hija(ruta_gradientes, nombres[0]), parada)
        if not isinstance(color, dict) or not isinstance(color.get("color"), dict):
            errores.append(_error_formato(ruta_color, "format_gradient_color"))
            continue
        literal_color = color["color"].get("Literal")
        if (not isinstance(literal_color, dict)
                or not isinstance(literal_color.get("Value"), str)):
            errores.append(_error_formato(_ruta_hija(ruta_color, "color"),
                                          "format_gradient_color_literal"))


def _validar_valor_formato(valor: Any, ruta: str,
                            errores: List[Dict[str, str]]) -> None:
    """Busca envoltorios de color/expresion mal anidados sin vetar extensiones."""
    if isinstance(valor, list):
        for indice, hijo in enumerate(valor):
            _validar_valor_formato(hijo, f"{ruta}[{indice}]", errores)
        return
    if not isinstance(valor, dict):
        return

    if "solid" in valor:
        solido = valor["solid"]
        ruta_solido = _ruta_hija(ruta, "solid")
        if not isinstance(solido, dict) or not isinstance(solido.get("color"), dict):
            errores.append(_error_formato(_ruta_hija(ruta_solido, "color"),
                                          "format_solid_color"))
        else:
            color = solido["color"]
            if "expr" in color:
                _validar_expresion_formato(color["expr"],
                                            _ruta_hija(_ruta_hija(ruta_solido, "color"), "expr"),
                                            errores)

    if "expr" in valor:
        _validar_expresion_formato(valor["expr"], _ruta_hija(ruta, "expr"), errores)

    for clave, hijo in valor.items():
        if clave in {"solid", "expr"}:
            continue
        _validar_valor_formato(hijo, _ruta_hija(ruta, clave), errores)


def _validar_bloque_objetos(objetos: Any, ruta: str,
                             errores: List[Dict[str, str]]) -> None:
    """Valida el armazon comun de ``objects`` y ``visualContainerObjects``."""
    if not isinstance(objetos, dict):
        errores.append(_error_formato(ruta, "format_objects_object"))
        return
    for grupo, bloques in objetos.items():
        ruta_grupo = _ruta_hija(ruta, grupo)
        if not isinstance(bloques, list):
            errores.append(_error_formato(ruta_grupo, "format_object_group_array"))
            continue
        for indice, bloque in enumerate(bloques):
            ruta_bloque = f"{ruta_grupo}[{indice}]"
            if not isinstance(bloque, dict) or not isinstance(bloque.get("properties"), dict):
                errores.append(_error_formato(ruta_bloque, "format_object_properties"))
                continue
            selector = bloque.get("selector")
            if selector is not None and not isinstance(selector, dict):
                errores.append(_error_formato(_ruta_hija(ruta_bloque, "selector"),
                                              "format_selector_object"))
            for propiedad, valor in bloque["properties"].items():
                _validar_valor_formato(valor,
                                       _ruta_hija(_ruta_hija(ruta_bloque, "properties"), propiedad),
                                       errores)


def validar_objetos_visual(datos: Dict[str, Any]) -> List[Dict[str, str]]:
    """Reglas estructurales que el schema oficial omite dentro de un visual.

    Es una barrera deliberadamente acotada: detecta formas imposibles que
    generan nuestras propias rutas de formato, sin fingir que reemplaza un
    corpus exportado de Power BI Desktop. Ese corpus sigue siendo necesario
    para conocer propiedades y combinaciones que aun no escribimos.
    """
    visual = datos.get("visual")
    if not isinstance(visual, dict):
        return []
    errores: List[Dict[str, str]] = []
    for clave in ("objects", "visualContainerObjects"):
        if clave in visual:
            _validar_bloque_objetos(visual[clave], f"$.visual.{clave}", errores)
    return errores


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

    esquema, comprobado_con = cargar(url, permitir_cercana=True)
    degradado = comprobado_con != url
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

    tolerados: List[Dict[str, str]] = []
    if degradado and errores:
        # Al comprobar contra una version anterior, lo unico que se perdona es
        # lo que una version posterior pudo ANADIR: una propiedad nueva o un
        # valor nuevo de una enumeracion. Un tipo equivocado o un campo
        # obligatorio ausente sigue siendo un error en cualquier version.
        reales = []
        for e in errores:
            if _es_novedad_de_version(e):
                tolerados.append({"path": _ruta_json(e), "rule": e.validator})
            else:
                reales.append(e)
        errores = reales

    if errores:
        detalle = [{"path": _ruta_json(e), "rule": e.validator,
                    "error": _mensaje_seguro(e)} for e in errores[:20]]
        raise SchemaValidationFailed(
            f"{len(errores)} error(es) de esquema en "
            f"{Path(archivo).name if archivo else 'el documento'}.",
            details={"file": str(archivo) if archivo else None,
                     "schema": url, "checked_against": comprobado_con,
                     "draft": esquema.get("$schema"),
                     "errors": detalle, "error_count": len(errores)})

    # `formattingObjectDefinitions` deja este bloque con
    # `additionalProperties: {}`. La validacion oficial no puede distinguir un
    # color solido de una expresion mal anidada, pero Desktop si: la ignora.
    # Estas reglas cubren los envoltorios que escribimos sin intentar inventar
    # un catalogo de propiedades que Microsoft no publica.
    errores_formato = validar_objetos_visual(datos)
    if errores_formato:
        raise SchemaValidationFailed(
            f"{len(errores_formato)} error(es) estructural(es) de formato en "
            f"{Path(archivo).name if archivo else 'el visual'}.",
            details={"file": str(archivo) if archivo else None,
                     "schema": url, "checked_against": comprobado_con,
                     "errors": errores_formato[:20],
                     "error_count": len(errores_formato),
                     "rule": "format_objects"})

    salida = {"validated": True, "schema": url, "draft": esquema.get("$schema"),
              "validator": Validador.__name__}
    if degradado:
        salida.update({"checked_against": comprobado_con, "degraded": True,
                       "tolerated": tolerados[:20],
                       "tolerated_count": len(tolerados)})
    return salida


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
