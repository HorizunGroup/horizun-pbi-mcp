"""Lectura y edicion segura de Power Query (M) dentro de un proyecto .pbip.

Donde vive el M
---------------
En un `.pbip` el codigo M no esta en un archivo propio. Vive en dos sitios,
dentro del TMDL del modelo semantico:

1. **Particiones de tabla** — `definition/tables/<Tabla>.tmdl`::

       partition Ventas = m
           mode: import
           source =
                   let
                       Origen = Sql.Database(...)
                   in
                       Origen

2. **Expresiones con nombre** — `definition/expressions.tmdl`: las consultas
   y los parametros que el panel de Power Query muestra fuera de las tablas.

Por que no se edita con expresiones regulares
---------------------------------------------
Porque el M es texto libre INDENTADO dentro de un formato sensible a la
indentacion: cualquier `re.sub` que acierte con una consulta falla con la
siguiente que tenga un `in` dentro de una cadena, un comentario con `source =`
o un salto de linea distinto. Aqui se localiza el BLOQUE por su estructura
-cabecera, indentacion, fin- y se reemplaza entero. No hay edicion parcial.

Que se comprueba y que NO
-------------------------
La respuesta separa cuatro cosas que es tentador confundir:

- `parse_checked`   — el TMDL resultante se leyo con el parser del proyecto.
- `tmdl_load_checked` — el serializador OFICIAL (TOM) acepto el modelo entero.
- `m_engine_checked` — **siempre False**: no hay motor M fuera de Power BI
  Desktop, asi que aqui NADIE ha ejecutado esta consulta.
- `refresh_checked` — **siempre False**: nada se refresco.

Que el archivo parsee no significa que la consulta cargue. Decir lo contrario
seria exactamente el tipo de promesa que este servidor no hace.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

log = get_logger("power_query")

PARTITION, EXPRESSION = "partition", "expression"
TIPOS = (PARTITION, EXPRESSION)

#: Propiedades que cierran el cuerpo de una expresion con nombre.
_PROPIEDADES = ("lineageTag", "queryGroup", "annotation", "description",
                "isHidden", "changedProperty", "dataType", "kind")


class PowerQueryError(PowerBIMCPError):
    code = "power_query_error"


def _indent(linea: str) -> int:
    """Nivel de indentacion en tabuladores. Cuatro espacios cuentan como uno."""
    sin_tabs = linea.lstrip("\t")
    tabs = len(linea) - len(sin_tabs)
    if tabs:
        return tabs
    return (len(linea) - len(linea.lstrip(" "))) // 4


def _unquote(nombre: str) -> str:
    nombre = nombre.strip()
    if len(nombre) >= 2 and nombre.startswith("'") and nombre.endswith("'"):
        return nombre[1:-1].replace("''", "'")
    return nombre


def sha256(texto: str) -> str:
    """Huella del texto NORMALIZADO a saltos de linea unix.

    Sin normalizar, el mismo M leido en Windows y reenviado por un cliente que
    usa `\\n` produce huellas distintas y la actualizacion se rechaza por un
    motivo que no existe.
    """
    return hashlib.sha256(
        str(texto).replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _definicion(active: ActivePbip) -> Path:
    from horizun_pbi_mcp.pbip.tmdl_reader import _definition_dir

    return _definition_dir(active)


def _leer(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


def _dedent(lineas: List[str]) -> str:
    """Quita la indentacion comun, conservando la relativa."""
    utiles = [l for l in lineas if l.strip()]
    if not utiles:
        return ""
    comun = min(len(l) - len(l.lstrip("\t ")) for l in utiles)
    return "\n".join(l[comun:] if l.strip() else "" for l in lineas).strip("\n")


def _reindentar(m: str, prefijo: str) -> List[str]:
    return [(prefijo + l) if l.strip() else "" for l in m.split("\n")]


# ------------------------------------------------------------- localizacion --
def _particiones_de_archivo(path: Path) -> List[Dict[str, Any]]:
    """Particiones M declaradas en un archivo de tabla TMDL."""
    lineas = _leer(path)
    tabla = None
    for l in lineas:
        if _indent(l) == 0 and l.strip().startswith("table "):
            tabla = _unquote(l.strip()[len("table"):].strip())
            break
    if tabla is None:
        return []

    salida: List[Dict[str, Any]] = []
    for i, linea in enumerate(lineas):
        limpio = linea.strip()
        if not limpio.startswith("partition "):
            continue
        cabecera = limpio[len("partition"):].strip()
        nombre, _, tipo = cabecera.partition("=")
        salida.append({
            "kind": PARTITION,
            "table": tabla,
            "name": _unquote(nombre.strip()),
            "source_type": tipo.strip() or None,
            "file": path,
            "header_line": i,
            "indent": _indent(linea),
        })
    return salida


def _expresiones_de_archivo(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    lineas = _leer(path)
    salida: List[Dict[str, Any]] = []
    for i, linea in enumerate(lineas):
        if _indent(linea) != 0 or not linea.strip().startswith("expression "):
            continue
        cabecera = linea.strip()[len("expression"):].strip()
        nombre, _, resto = cabecera.partition("=")
        salida.append({
            "kind": EXPRESSION,
            "table": None,
            "name": _unquote(nombre.strip()),
            "source_type": None,
            "file": path,
            "header_line": i,
            "indent": 0,
            "inline": resto.strip() or None,
        })
    return salida


def list_objects(active: ActivePbip) -> Dict[str, Any]:
    """Todo lo que este servicio sabe leer o escribir en el proyecto activo."""
    definicion = _definicion(active)
    tablas = definicion / "tables"
    particiones: List[Dict[str, Any]] = []
    ilegibles: List[Dict[str, str]] = []
    if tablas.is_dir():
        for archivo in sorted(tablas.glob("*.tmdl")):
            try:
                particiones.extend(_particiones_de_archivo(archivo))
            except (OSError, UnicodeError) as exc:
                # Un archivo que no se puede leer NO desaparece del inventario:
                # se declara. Omitirlo haria creer que la tabla no tiene
                # particion, y la siguiente escritura la crearia duplicada.
                log.warning("No se pudo leer %s: %s", archivo, exc)
                ilegibles.append({"file": str(archivo),
                                  "error": type(exc).__name__})
    expresiones = _expresiones_de_archivo(definicion / "expressions.tmdl")
    return {
        "partitions": [{"table": p["table"], "name": p["name"],
                        "source_type": p["source_type"]} for p in particiones],
        "expressions": [{"name": e["name"]} for e in expresiones],
        "unreadable_files": ilegibles,
        "complete": not ilegibles,
        "_objetos": particiones + expresiones,
    }


def _candidatos(inventario: Dict[str, Any]) -> Dict[str, Any]:
    salida = {"partitions": inventario["partitions"],
              "expressions": inventario["expressions"]}
    if inventario.get("unreadable_files"):
        # Si la lista esta incompleta, el mensaje "no existe" no puede darse
        # por definitivo: puede estar en el archivo que no se pudo leer.
        salida["unreadable_files"] = inventario["unreadable_files"]
        salida["complete"] = False
    return salida


def resolve_object(active: ActivePbip, *, table: Optional[str] = None,
                   name: Optional[str] = None,
                   kind: Optional[str] = None) -> Dict[str, Any]:
    """Selecciona UN objeto sin ambiguedad, o falla enseñando los candidatos."""
    if kind and kind not in TIPOS:
        raise ValidationError(
            f"kind='{kind}' no existe. Usa {' o '.join(TIPOS)}.",
            details={"parameter": "kind", "valid": list(TIPOS)})

    inventario = list_objects(active)
    objetos: List[Dict[str, Any]] = list(inventario["_objetos"])
    if kind:
        objetos = [o for o in objetos if o["kind"] == kind]
    if table:
        objetos = [o for o in objetos
                   if str(o["table"] or "").casefold() == table.casefold()]
    if name:
        objetos = [o for o in objetos
                   if o["name"].casefold() == name.casefold()]

    if len(objetos) == 1:
        return objetos[0]

    if not objetos:
        pedido = {"table": table, "name": name, "kind": kind}
        raise PowerQueryError(
            "No hay ninguna particion ni expresion M que coincida con "
            f"{ {k: v for k, v in pedido.items() if v} }. En 'candidates' "
            "estan las que si existen: elige por 'table'+'name' para una "
            "particion, o por 'name' con kind='expression'.",
            details={"requested": pedido,
                     "candidates": _candidatos(inventario)})

    raise PowerQueryError(
        f"La seleccion es ambigua: coinciden {len(objetos)} objetos. Indica "
        "'table' y 'name' (o 'kind') para dejar uno solo.",
        details={"requested": {"table": table, "name": name, "kind": kind},
                 "matches": [{"kind": o["kind"], "table": o["table"],
                              "name": o["name"]} for o in objetos],
                 "candidates": _candidatos(inventario)})


# ------------------------------------------------------------------ lectura --
def _sangria_de(linea: str) -> str:
    return linea[:len(linea) - len(linea.lstrip("\t "))]


def _cuerpo_de_particion(lineas: List[str],
                         objeto: Dict[str, Any]) -> Dict[str, Any]:
    """Donde empieza y acaba el M de una particion, y como reescribirlo.

    `header` existe para el caso `source = <una linea>`: ahi el M vive EN la
    misma linea que la propiedad, asi que reemplazar solo el cuerpo borraria
    el `source =`. Se devuelve la cabecera que hay que reponer.
    """
    base = objeto["indent"]
    fin_bloque = len(lineas)
    for j in range(objeto["header_line"] + 1, len(lineas)):
        if lineas[j].strip() and _indent(lineas[j]) <= base:
            fin_bloque = j
            break

    idx_source = None
    for j in range(objeto["header_line"] + 1, fin_bloque):
        if lineas[j].strip().split("=")[0].strip() == "source":
            idx_source = j
            break
    if idx_source is None:
        raise PowerQueryError(
            f"La particion '{objeto['name']}' no declara 'source': no hay "
            "consulta M que leer. No se inventa una.",
            details={"table": objeto["table"], "partition": objeto["name"],
                     "file": str(objeto["file"])})

    linea_source = lineas[idx_source]
    inline = (linea_source.split("=", 1)[1].strip()
              if "=" in linea_source else "")
    nivel = _indent(linea_source)
    sangria_source = _sangria_de(linea_source)

    fin = idx_source + 1
    while fin < fin_bloque and (not lineas[fin].strip()
                                or _indent(lineas[fin]) > nivel):
        fin += 1
    while fin - 1 > idx_source and not lineas[fin - 1].strip():
        fin -= 1

    cuerpo = lineas[idx_source + 1:fin]
    if inline and not cuerpo:
        # Forma en una sola linea: al reescribir se pasa a la forma larga,
        # que es la que Power BI Desktop genera y la unica que admite un M
        # multilinea.
        return {"start": idx_source, "end": idx_source + 1,
                "indent": sangria_source + "\t\t",
                "header": f"{sangria_source}source =",
                "m": inline, "m_line": idx_source}
    sangria = _sangria_de(cuerpo[0]) if cuerpo else sangria_source + "\t\t"
    return {"start": idx_source + 1, "end": fin, "indent": sangria,
            "header": None, "m": _dedent(cuerpo), "m_line": idx_source + 1}


def _cuerpo_de_expresion(lineas: List[str],
                         objeto: Dict[str, Any]) -> Dict[str, Any]:
    """Donde empieza y acaba el M de una expresion con nombre."""
    inicio = objeto["header_line"] + 1
    fin = inicio
    while fin < len(lineas):
        linea = lineas[fin]
        if not linea.strip():
            fin += 1
            continue
        if _indent(linea) == 0 or (
                _indent(linea) <= 1
                and linea.strip().split(":")[0].split(" ")[0] in _PROPIEDADES):
            break
        fin += 1
    while fin - 1 >= inicio and not lineas[fin - 1].strip():
        fin -= 1

    cuerpo = lineas[inicio:fin]
    cabecera = lineas[objeto["header_line"]]
    if objeto.get("inline") and not cuerpo:
        nombre = cabecera.strip()[len("expression"):].partition("=")[0].strip()
        return {"start": objeto["header_line"],
                "end": objeto["header_line"] + 1,
                "indent": "\t\t",
                "header": f"expression {nombre} =",
                "m": objeto["inline"], "m_line": objeto["header_line"]}
    sangria = _sangria_de(cuerpo[0]) if cuerpo else "\t\t"
    return {"start": inicio, "end": fin, "indent": sangria, "header": None,
            "m": _dedent(cuerpo), "m_line": inicio}


def _cuerpo(lineas: List[str], objeto: Dict[str, Any]) -> Dict[str, Any]:
    if objeto["kind"] == PARTITION:
        return _cuerpo_de_particion(lineas, objeto)
    return _cuerpo_de_expresion(lineas, objeto)


def get_power_query(active: ActivePbip, *, table: Optional[str] = None,
                    name: Optional[str] = None,
                    kind: Optional[str] = None) -> Dict[str, Any]:
    """Texto M actual de una particion o expresion, con su SHA-256."""
    objeto = resolve_object(active, table=table, name=name, kind=kind)
    lineas = _leer(objeto["file"])
    bloque = _cuerpo(lineas, objeto)
    proyecto = Path(active.project_dir)
    try:
        relativa = objeto["file"].relative_to(proyecto).as_posix()
    except ValueError:                                    # pragma: no cover
        relativa = objeto["file"].name
    return {
        "kind": objeto["kind"],
        "table": objeto["table"],
        "name": objeto["name"],
        "source_type": objeto["source_type"],
        "file": relativa,
        "line_start": bloque["m_line"] + 1,
        "line_end": max(bloque["end"], bloque["m_line"] + 1),
        "m": bloque["m"],
        "sha256": sha256(bloque["m"]),
        "read_checked": True,
        "m_engine_checked": False,
        "note": ("Texto leido del TMDL. Nadie lo ha ejecutado: no hay motor M "
                 "fuera de Power BI Desktop."),
    }


# ---------------------------------------------------------------- escritura --
def _exigir_pbip(active: ActivePbip) -> None:
    if not getattr(active, "semantic_model_dir", None):
        raise PowerQueryError(
            "Editar Power Query exige un proyecto .pbip con modelo semantico "
            "en disco. Contra un modelo en vivo el M no es editable: vive en "
            "los archivos, no en el motor.")


def _exigir_m_sin_secretos(m: str) -> Dict[str, Any]:
    """El detector de secretos, sobre la consulta NUEVA, antes de escribirla."""
    from horizun_pbi_mcp.services import secret_scan

    escaneo = secret_scan.build_result(
        secret_scan.scan_text(m, file="power_query"), files_scanned=1)
    if escaneo["status"] == secret_scan.BLOCKED:
        raise PowerQueryError(
            "La consulta M lleva una credencial incrustada y no se escribe: "
            "quedaria en texto plano dentro del proyecto. Usa un parametro de "
            "Power Query o el almacen de credenciales de Power BI Desktop. En "
            "'security_scan' esta la regla y la linea; el valor NO se "
            "devuelve a proposito.",
            details={"security_scan": escaneo})
    return escaneo


def update_power_query(active: ActivePbip, m: str, *,
                       table: Optional[str] = None,
                       name: Optional[str] = None,
                       kind: Optional[str] = None,
                       expected_sha256: Optional[str] = None,
                       dry_run: bool = True,
                       request_id: Optional[str] = None) -> Dict[str, Any]:
    """Reemplaza ENTERA la consulta M de una particion o expresion.

    No hay edicion parcial: se sustituye el bloque completo. `expected_sha256`
    rechaza la escritura si el texto cambio desde que se leyo, y `dry_run`
    -que viene activado- enseña exactamente lo que se escribiria sin tocar el
    disco, sin crear backup y sin abrir journal.
    """
    from horizun_pbi_mcp.services import project_state, tmdl_validate
    from horizun_pbi_mcp.services import paths as safe_paths
    from horizun_pbi_mcp.services import txn as txn_service

    _exigir_pbip(active)
    if not isinstance(m, str) or not m.strip():
        raise ValidationError(
            "La consulta M esta vacia. Para borrar una particion no uses esta "
            "tool: dejaria la tabla sin origen y el modelo no cargaria.")

    objeto = resolve_object(active, table=table, name=name, kind=kind)
    archivo = safe_paths.ensure_contained(
        Path(active.project_dir), objeto["file"],
        kind="archivo TMDL con la consulta M")

    try:
        lineas = _leer(archivo)
    except (OSError, UnicodeError) as exc:
        # Nunca sobrescribir un TMDL que no se pudo leer: seria escribir a
        # ciegas sobre algo cuyo contenido se desconoce.
        raise PowerQueryError(
            f"El archivo TMDL no se pudo leer ({type(exc).__name__}); no se "
            "escribe encima de lo que no se pudo comprobar.",
            details={"file": str(archivo)}) from exc

    bloque = _cuerpo(lineas, objeto)
    huella_actual = sha256(bloque["m"])
    if expected_sha256 and expected_sha256.strip().casefold() != huella_actual:
        raise PowerQueryError(
            "La consulta cambio desde que la leiste: el 'expected_sha256' no "
            "coincide con el texto actual. Vuelve a leerla con "
            "pbi_get_power_query, aplica tu cambio sobre lo que hay ahora y "
            "reintenta.",
            details={"expected_sha256": expected_sha256,
                     "actual_sha256": huella_actual,
                     "kind": objeto["kind"], "table": objeto["table"],
                     "name": objeto["name"]})

    escaneo = _exigir_m_sin_secretos(m)
    nuevo = m.replace("\r\n", "\n").rstrip("\n")
    reemplazo = ([bloque["header"]] if bloque["header"] else []) \
        + _reindentar(nuevo, bloque["indent"])
    nuevas_lineas = (lineas[:bloque["start"]] + reemplazo
                     + lineas[bloque["end"]:])
    texto = "\n".join(nuevas_lineas)
    if not texto.endswith("\n"):
        texto += "\n"

    base: Dict[str, Any] = {
        "kind": objeto["kind"], "table": objeto["table"],
        "name": objeto["name"],
        "file": archivo.relative_to(Path(active.project_dir)).as_posix(),
        "previous_sha256": huella_actual,
        "new_sha256": sha256(nuevo),
        "unchanged": sha256(nuevo) == huella_actual,
        "security_scan": escaneo,
        # Lo que se comprobo, separado de lo que NO. Ver el docstring del
        # modulo: que el TMDL parsee no dice nada sobre si el M carga.
        "m_engine_checked": False,
        "refresh_checked": False,
        "note": ("Ningun motor M evaluo esta consulta y nada se refresco. "
                 "Para saber si CARGA, abre el proyecto en Power BI Desktop y "
                 "refresca."),
    }

    if dry_run:
        return {**base, "dry_run": True, "applied": False,
                "parse_checked": False, "tmdl_load_checked": False,
                "preview": texto[:4000],
                "hint": "Repite con dry_run=false para escribirlo."}

    project_state.assert_writable(
        active, operation="Editar una consulta de Power Query")

    definicion = _definicion(active)
    previo = tmdl_validate.validate(definicion, use_tom=True)
    cm = txn_service.project_transaction(
        active, [archivo], tool="pbi_update_power_query",
        request_id=request_id)
    with cm as t:
        t.write_text(archivo, texto)
        validacion = _validar_sin_errores_nuevos(definicion, previo)
        # Relectura DENTRO de la transaccion: si el bloque no quedo donde
        # deberia, el context manager revierte byte a byte.
        releido = _cuerpo(_leer(archivo), resolve_object(
            active, table=objeto["table"], name=objeto["name"],
            kind=objeto["kind"]))["m"]
        if sha256(releido) != sha256(nuevo):
            raise PowerQueryError(
                "Tras escribir, la consulta releida del archivo no coincide "
                "con la que se pidio. Se revierte: no se confirma una "
                "escritura que no se pudo verificar.",
                details={"expected_sha256": sha256(nuevo),
                         "reread_sha256": sha256(releido)})

    log.info("Consulta M actualizada en %s (%s '%s')", archivo.name,
             objeto["kind"], objeto["name"])
    return {**base, "dry_run": False, "applied": True,
            "parse_checked": bool(validacion.get("parse_checked")),
            "tmdl_load_checked": bool(validacion.get("parse_checked")),
            "tmdl_parsed": validacion.get("parsed"),
            "model_validation": validacion,
            "reread_verified": True,
            "backup": cm.result["journal"],
            "transaction": cm.result}


def _validar_sin_errores_nuevos(definicion: Path,
                                previo: Dict[str, Any]) -> Dict[str, Any]:
    """Valida el modelo y lanza si aparecieron errores que antes no estaban."""
    from horizun_pbi_mcp.services import tmdl_validate

    actual = tmdl_validate.validate(definicion, use_tom=True)

    def _clave(f):
        return (f.get("rule"), str((f.get("object") or {}).get("name")),
                str((f.get("object") or {}).get("file")))

    antes = {_clave(f) for f in previo.get("findings", [])
             if f.get("severity") == "error"}
    nuevos = [f for f in actual.get("findings", [])
              if f.get("severity") == "error" and _clave(f) not in antes]
    if nuevos:
        raise PowerQueryError(
            f"La consulta nueva deja el modelo TMDL con {len(nuevos)} error(es) "
            "que antes no tenia. No se confirma: el proyecto queda como estaba.",
            details={"introduced": nuevos,
                     "preexisting": len(antes),
                     "parse_checked": actual.get("parse_checked")})
    return actual
