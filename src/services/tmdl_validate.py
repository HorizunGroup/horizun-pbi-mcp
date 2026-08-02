"""Validacion REAL de un TMDL: lo que el parser dice y lo que no llega a decir.

Por que existe: `pbi_validate_pbip_project` comprobaba que los archivos
existieran y devolvia `valid: true`. Un TMDL malformado pasaba esa validacion y
reventaba al abrir Power BI Desktop, que acababa siendo el unico detector de
errores disponible. Eso es caro y llega tarde.

Dos capas, deliberadamente separadas:

1. **Lint estatico** (Python puro, sin dependencias). Cubre las trampas que
   cuestan un ciclo de apertura entero y funciona en cualquier maquina, con o
   sin las DLL de Analysis Services.
2. **Parseo autoritativo** con `TmdlSerializer`, el MISMO codigo que usa Power
   BI para abrir el proyecto. Es la verdad, pero necesita las DLL.

Si la capa 2 no se puede ejecutar se dice (`parse_checked: False`) en vez de
callarlo: "no pude mirar" no puede leerse igual que "esta bien".

Hay una tercera clase de fallo que **ningun analisis estatico puede ver**: los
que dependen de los datos (un blanco en el lado "uno" de una relacion, o un
separador decimal mal interpretado). Para esos hace falta refrescar. Estan
documentados en `LIMITACIONES`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from pbip import tmdl_reader
from powerbi.errors import PowerBIMCPError

log = get_logger("tmdl_validate")


class TmdlValidationError(PowerBIMCPError):
    code = "tmdl_validation_error"


class ReportOnlyProjectError(TmdlValidationError):
    """El proyecto no tiene modelo propio, y eso es legitimo.

    Un informe fino contra un dataset publicado —o el resultado de convertir
    con `include_model=false`— solo tiene carpeta `.Report`. No hay nada que
    validar, pero tampoco hay nada roto: son dos cosas distintas y decirlas
    igual manda a buscar un problema que no existe.
    """

    code = "tmdl_report_only_project"


#: Lo que el lint NO puede ver, y que solo aparece al refrescar el modelo.
LIMITACIONES = (
    "Un blanco o un duplicado en la columna del lado 'uno' de una relacion solo "
    "se detecta al refrescar: depende de los datos, no del TMDL.",
    "Un separador decimal mal interpretado no da error: produce numeros mal. El "
    "lint avisa del riesgo, pero confirmarlo exige comparar contra el origen.",
)

#: Palabras que abren un objeto hijo dentro de una tabla.
_HIJOS = {"measure", "column", "partition", "hierarchy", "calculationGroup",
          "changedProperty", "level"}

#: Propiedades que TMDL si admite despues de los hijos.
_PERMITIDAS_AL_FINAL = {"annotation", "extendedProperty"}

#: Tipos M cuya conversion depende de la cultura.
_TIPOS_SENSIBLES_A_CULTURA = (
    "Currency.Type", "Int64.Type", "Percentage.Type", "Single.Type",
    "Double.Type", "Decimal.Type", "type number", "type date", "type datetime",
    "type datetimezone", "type time",
)

_CULTURA_INVARIANTE = {"", "invariant", "en-us"}

#: Origenes que entregan TEXTO y por tanto obligan a interpretar el numero.
#: Excel y las bases de datos devuelven valores ya tipados: ahi la cultura no
#: cambia nada y avisar seria ruido. Un validador que llora lobo se ignora.
#: Solo ORIGENES, no transformaciones: `Text.From(...)` aparece en cualquier
#: consulta y no dice nada sobre como llegaron los numeros.
_ORIGENES_DE_TEXTO = (
    "Csv.Document", "Table.FromCsv", "Json.Document", "Xml.Tables",
    "Xml.Document", "Web.Contents", "Lines.FromBinary",
)

_RE_CULTURA_FINAL = re.compile(r',\s*"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*"\s*$')


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _finding(rule: str, severity: str, obj: Dict[str, Any],
             evidence: Dict[str, Any], recommendation: str) -> Dict[str, Any]:
    return {"rule": rule, "severity": severity, "object": obj,
            "evidence": evidence, "recommendation": recommendation}


def _leer(path: Path) -> List[str]:
    return path.read_text(encoding="utf-8-sig").splitlines()


def _llamadas_balanceadas(texto: str, funcion: str) -> List[str]:
    """Devuelve el texto de cada llamada a `funcion`, con parentesis cuadrados.

    Un `split(")")` partiria la llamada por el primer parentesis interno; hay
    que contar la profundidad para quedarse con la llamada completa.
    """
    salida: List[str] = []
    inicio = 0
    while True:
        i = texto.find(funcion + "(", inicio)
        if i < 0:
            return salida
        j = i + len(funcion)
        profundidad = 0
        for k in range(j, len(texto)):
            if texto[k] == "(":
                profundidad += 1
            elif texto[k] == ")":
                profundidad -= 1
                if profundidad == 0:
                    salida.append(texto[i:k + 1])
                    break
        else:  # sin cierre: llamada truncada, no se analiza
            return salida
        inicio = k + 1


# ---------------------------------------------------------------------------
# Lint estatico
# ---------------------------------------------------------------------------

def _lint_orden_de_propiedades(path: Path) -> List[Dict[str, Any]]:
    """Una propiedad de la tabla despues de sus hijos rompe el parseo.

    TMDL exige que las propiedades del objeto vayan antes que sus hijos. Es el
    fallo tipico de insertar medidas justo despues de la linea `table X`: lo
    que venia detras (`lineageTag`, `isHidden`...) queda huerfano y Power BI
    aborta con "se detecto una sangria no valida".
    """
    hallazgos: List[Dict[str, Any]] = []
    visto_hijo = False
    for numero, linea in enumerate(_leer(path), start=1):
        if not linea.strip() or linea.strip().startswith("///"):
            continue
        if tmdl_reader._indent(linea) != 1:
            continue
        token = tmdl_reader._first_token(linea)
        if token in _HIJOS:
            visto_hijo = True
            continue
        if token in _PERMITIDAS_AL_FINAL:
            continue
        if visto_hijo:
            hallazgos.append(_finding(
                "tmdl_property_after_children", "error",
                {"kind": "table_property", "file": str(path), "line": numero},
                {"property": linea.strip()},
                "En TMDL las propiedades de la tabla van ANTES que sus medidas, "
                "columnas y particiones. Muevela justo debajo de la linea "
                "'table ...' o el proyecto no abrira."))
    return hallazgos


def _lint_descripcion_en_relaciones(path: Path) -> List[Dict[str, Any]]:
    """Un `///` sobre una relacion se serializa como `description`.

    `SingleColumnRelationship` no tiene esa propiedad: Power BI aborta con
    'La propiedad "description" es desconocida e inesperada'.
    """
    if not path.exists():
        return []
    hallazgos: List[Dict[str, Any]] = []
    doc_abierto: Optional[int] = None
    for numero, linea in enumerate(_leer(path), start=1):
        limpia = linea.strip()
        if limpia.startswith("///"):
            if doc_abierto is None:
                doc_abierto = numero
            continue
        if not limpia:
            continue
        if doc_abierto is not None:
            if tmdl_reader._first_token(linea) == "relationship":
                hallazgos.append(_finding(
                    "tmdl_description_on_relationship", "error",
                    {"kind": "relationship", "file": str(path),
                     "line": doc_abierto},
                    {"comment": limpia},
                    "Las relaciones no admiten 'description', y un comentario "
                    "'///' se convierte justo en eso. Quita el comentario o "
                    "documenta la relacion fuera del TMDL."))
            doc_abierto = None
    return hallazgos


def _lint_colisiones(tablas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Una medida no puede llamarse como una columna de su tabla.

    El parser TMDL lo acepta sin rechistar; el motor lo rechaza al crear la
    base, asi que solo se ve al abrir. La comparacion es sin distinguir
    mayusculas, como la hace el motor.
    """
    hallazgos: List[Dict[str, Any]] = []
    for tabla in tablas:
        columnas = {c["name"].casefold(): c["name"] for c in tabla["columns"]}
        for medida in tabla["measures"]:
            choque = columnas.get(medida["name"].casefold())
            if choque is not None:
                hallazgos.append(_finding(
                    "tmdl_measure_column_collision", "error",
                    {"kind": "measure", "table": tabla["name"],
                     "name": medida["name"], "file": tabla.get("file")},
                    {"measure": medida["name"], "column": choque},
                    f"Renombra la medida: en '{tabla['name']}' ya hay una "
                    f"columna '{choque}' y el motor no admite el mismo nombre "
                    "para las dos."))
    return hallazgos


def _lint_medidas_duplicadas(tablas: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """En un modelo tabular el nombre de medida es global, no por tabla."""
    por_nombre: Dict[str, List[str]] = {}
    original: Dict[str, str] = {}
    for tabla in tablas:
        for medida in tabla["measures"]:
            clave = medida["name"].casefold()
            por_nombre.setdefault(clave, []).append(tabla["name"])
            original.setdefault(clave, medida["name"])
    hallazgos: List[Dict[str, Any]] = []
    for clave, tablas_con in por_nombre.items():
        if len(tablas_con) > 1:
            hallazgos.append(_finding(
                "tmdl_duplicate_measure_name", "error",
                {"kind": "measure", "name": original[clave]},
                {"tables": tablas_con},
                "El nombre de una medida es unico en todo el modelo, no por "
                "tabla. Renombra una de las dos."))
    return hallazgos


def _lint_relaciones(tablas: List[Dict[str, Any]],
                     relaciones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Una relacion que apunta a una columna inexistente."""
    indice = {t["name"].casefold(): {c["name"].casefold() for c in t["columns"]}
              for t in tablas}
    hallazgos: List[Dict[str, Any]] = []
    for rel in relaciones:
        for lado in ("from", "to"):
            tabla = rel.get(f"{lado}_table")
            columna = rel.get(f"{lado}_column")
            if not tabla or not columna:
                continue
            columnas = indice.get(tabla.casefold())
            if columnas is None:
                falta = "table"
            elif columna.casefold() not in columnas:
                falta = "column"
            else:
                continue
            hallazgos.append(_finding(
                "tmdl_relationship_column_missing", "error",
                {"kind": "relationship", "name": rel.get("name"), "side": lado},
                {"reference": f"{tabla}.{columna}", "missing": falta},
                "La relacion apunta a algo que no existe en el modelo. "
                "Corrige la referencia o crea la columna."))
    return hallazgos


def _lint_cultura(definition: Path, tablas: List[Dict[str, Any]],
                  source_query_culture: Optional[str]) -> List[Dict[str, Any]]:
    """Conversion numerica sin cultura explicita bajo una cultura ambigua.

    Es el fallo mas peligroso de todos porque **no da error**: con
    `sourceQueryCulture: es-CO`, un CSV con punto decimal se lee como separador
    de miles y los totales salen inflados. El informe abre, pinta y miente.
    """
    if not source_query_culture:
        return []
    if source_query_culture.strip().casefold() in _CULTURA_INVARIANTE:
        return []

    hallazgos: List[Dict[str, Any]] = []
    for tabla in tablas:
        archivo = tabla.get("file")
        if not archivo:
            continue
        texto = Path(archivo).read_text(encoding="utf-8-sig")
        origen = next((o for o in _ORIGENES_DE_TEXTO if o in texto), None)
        if origen is None:
            # El origen entrega valores ya tipados: no hay nada que interpretar.
            continue
        for llamada in _llamadas_balanceadas(texto, "Table.TransformColumnTypes"):
            if not any(t in llamada for t in _TIPOS_SENSIBLES_A_CULTURA):
                continue
            if _RE_CULTURA_FINAL.search(llamada.rstrip()[:-1]):
                continue  # ya lleva cultura explicita
            hallazgos.append(_finding(
                "tmdl_transform_without_culture", "warning",
                {"kind": "partition", "table": tabla["name"], "file": archivo},
                {"source_query_culture": source_query_culture,
                 "text_source": origen, "call": llamada[:160]},
                "Pasa la cultura como tercer argumento, p.ej. "
                'Table.TransformColumnTypes(t, {...}, "en-US"). Sin ella se usa '
                f"la del modelo ({source_query_culture}) y un punto decimal "
                "puede leerse como separador de miles SIN dar ningun error."))
            break  # un aviso por tabla es suficiente
    return hallazgos


def _refs_del_modelo(definition: Path) -> Optional[set]:
    """Nombres declarados con `ref table` en model.tmdl. None si no hay modelo."""
    modelo = definition / "model.tmdl"
    if not modelo.exists():
        return None
    refs = set()
    for linea in _leer(modelo):
        limpia = linea.strip()
        if limpia.startswith("ref table "):
            refs.add(tmdl_reader._unquote(limpia[len("ref table "):].strip()))
    return refs


def _lint_tablas_declaradas(definition: Path, tablas: List[Dict[str, Any]],
                            ilegibles: List[str]) -> List[Dict[str, Any]]:
    """Una tabla solo forma parte del modelo si model.tmdl la declara.

    Es un fallo mudo y caro: el .tmdl se ve perfecto en disco, el proyecto abre
    sin quejarse y la tabla simplemente no existe. Todo lo que la usara —una
    medida, un visual— aparece roto sin ninguna explicacion.
    """
    refs = _refs_del_modelo(definition)
    if refs is None:
        return []

    hallazgos: List[Dict[str, Any]] = []
    presentes = {}
    for tabla in tablas:
        presentes[tabla["name"].casefold()] = tabla
        if tabla["name"] not in refs and tabla["name"].casefold() not in {
                r.casefold() for r in refs}:
            hallazgos.append(_finding(
                "tmdl_table_not_referenced", "error",
                {"kind": "table", "table": tabla["name"],
                 "file": tabla.get("file")},
                {"table": tabla["name"]},
                f"Agrega 'ref table {tabla['name']}' a model.tmdl. Sin esa "
                "linea el archivo esta en disco pero la tabla no existe en el "
                "modelo, y lo que la use aparecera roto sin decir por que."))

    if not ilegibles:
        for ref in sorted(refs):
            if ref.casefold() not in presentes:
                hallazgos.append(_finding(
                    "tmdl_ref_table_missing", "error",
                    {"kind": "model", "file": str(definition / "model.tmdl")},
                    {"table": ref},
                    f"model.tmdl declara 'ref table {ref}' pero no hay ningun "
                    "archivo de tabla con ese nombre. Power BI no podra cargar "
                    "el modelo."))
    return hallazgos


def _source_query_culture(definition: Path) -> Optional[str]:
    modelo = definition / "model.tmdl"
    if not modelo.exists():
        return None
    for linea in _leer(modelo):
        if linea.strip().startswith("sourceQueryCulture:"):
            return linea.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Parseo autoritativo (TOM)
# ---------------------------------------------------------------------------

_RE_DOC = re.compile(r'Document(?:o)?:\s*[“"\']?([^”"\'\r\n]+)')
_RE_LINEA = re.compile(r'(?:Line number|N[uú]mero de l[ií]nea):\s*(\d+)')


def parse_with_tom(definition: Path) -> Dict[str, Any]:
    """Parsea con el MISMO serializador que usa Power BI para abrir el .pbip.

    No hay forma mas fiel de responder "¿esto abrira?" sin abrirlo.
    """
    from powerbi.clr_bootstrap import load_tom

    tom = load_tom()
    try:
        base = tom.TmdlSerializer.DeserializeDatabaseFromFolder(str(definition))
    except Exception as exc:  # noqa: BLE001 - cualquier fallo del serializador
        mensajes: List[str] = []
        actual: Any = exc
        for _ in range(6):
            if actual is None:
                break
            texto = str(getattr(actual, "Message", None) or actual).strip()
            if texto and texto not in mensajes:
                mensajes.append(texto)
            actual = getattr(actual, "InnerException", None)
        completo = "\n".join(mensajes)
        doc = _RE_DOC.search(completo)
        linea = _RE_LINEA.search(completo)
        return {
            "parsed": False,
            "error": {
                "message": mensajes[0] if mensajes else str(exc),
                "chain": mensajes[1:],
                "file": doc.group(1).strip() if doc else None,
                "line": int(linea.group(1)) if linea else None,
            },
            "counts": None,
        }

    modelo = base.Model
    return {
        "parsed": True,
        "error": None,
        "counts": {
            "tables": modelo.Tables.Count,
            "relationships": modelo.Relationships.Count,
            "measures": sum(t.Measures.Count for t in modelo.Tables),
            "columns": sum(t.Columns.Count for t in modelo.Tables),
        },
    }


# ---------------------------------------------------------------------------
# Entrada publica
# ---------------------------------------------------------------------------

def _leer_model_bim(path: Path) -> Dict[str, Any]:
    """Normaliza un `model.bim` (TMSL/JSON) a la misma forma que el lector TMDL.

    Asi los chequeos semanticos se escriben una sola vez y valen para los dos
    formatos. Los estructurales no aplican: en un JSON no hay sangria que
    romper ni comentarios `///` que se conviertan en propiedades.
    """
    import json

    try:
        crudo = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "tables": [], "relationships": [],
                "culture": None}

    modelo = crudo.get("model") or {}
    tablas: List[Dict[str, Any]] = []
    for tabla in modelo.get("tables") or []:
        tablas.append({
            "name": tabla.get("name", ""),
            "file": str(path),
            "columns": [{"name": c.get("name", "")}
                        for c in (tabla.get("columns") or [])],
            "measures": [{"name": m.get("name", "")}
                         for m in (tabla.get("measures") or [])],
        })
    relaciones: List[Dict[str, Any]] = []
    for rel in modelo.get("relationships") or []:
        relaciones.append({
            "name": rel.get("name"),
            "from_table": rel.get("fromTable"), "from_column": rel.get("fromColumn"),
            "to_table": rel.get("toTable"), "to_column": rel.get("toColumn"),
        })
    return {"error": None, "tables": tablas, "relationships": relaciones,
            "culture": modelo.get("sourceQueryCulture")}


def _validate_model_bim(path: Path) -> Dict[str, Any]:
    """Chequeos semanticos sobre un modelo en formato heredado (`model.bim`)."""
    leido = _leer_model_bim(path)
    hallazgos: List[Dict[str, Any]] = []

    if leido["error"]:
        hallazgos.append(_finding(
            "model_bim_unreadable", "error",
            {"kind": "model", "file": str(path)},
            {"cause": leido["error"]},
            "El model.bim no es JSON valido. Power BI tampoco podra leerlo."))
    else:
        hallazgos.extend(_lint_colisiones(leido["tables"]))
        hallazgos.extend(_lint_medidas_duplicadas(leido["tables"]))
        hallazgos.extend(_lint_relaciones(leido["tables"], leido["relationships"]))

    errores = [f for f in hallazgos if f["severity"] == "error"]
    return {
        "valid": not errores,
        "format": "model.bim",
        "findings": hallazgos,
        "error_count": len(errores),
        "warning_count": len(hallazgos) - len(errores),
        "parsed": None,
        "parse_checked": False,
        "parse_skipped_reason":
            "El formato heredado model.bim no lo lee TmdlSerializer. Se aplican "
            "los chequeos semanticos, que no dependen del formato.",
        "parse_error": None,
        "counts": {
            "table_files": 1,
            "tables_read": len(leido["tables"]),
            "unreadable_files": [str(path)] if leido["error"] else [],
            "relationships": len(leido["relationships"]),
            "measures": sum(len(t["measures"]) for t in leido["tables"]),
        },
        "tom_counts": None,
        "source_query_culture": leido["culture"],
        "limitations": list(LIMITACIONES) + [
            "Este modelo usa el formato heredado model.bim: no se comprueba la "
            "estructura del TMDL porque no hay TMDL. Activa el preview de TMDL "
            "en Power BI Desktop si quieres esa capa.",
        ],
    }


def resolve_definition_dir(destino: Path | str) -> Path:
    """Acepta lo que la gente tiene a mano y llega a la carpeta `definition`.

    Vale un `.pbip`, la carpeta del proyecto, la `.SemanticModel` o la propia
    `definition`. Pedir exactamente una de las cuatro seria una trampa mas.
    """
    destino = Path(destino).expanduser()
    if destino.is_file() and destino.suffix.lower() == ".pbip":
        destino = destino.parent
    if not destino.is_dir():
        raise TmdlValidationError(
            f"No existe la ruta indicada: {destino}", details={"path": str(destino)})

    if (destino / "model.tmdl").exists() or (destino / "tables").is_dir():
        return destino
    if (destino / "definition" / "model.tmdl").exists():
        return destino / "definition"

    if (destino / "model.bim").exists():
        return destino

    modelos = sorted(destino.glob("*.SemanticModel"))
    if len(modelos) == 1:
        if (modelos[0] / "definition").is_dir():
            return modelos[0] / "definition"
        if (modelos[0] / "model.bim").exists():
            # Formato heredado: es el que trae un .pbip sin el preview de TMDL,
            # o sea la mayoria. Ignorarlo dejaria fuera casi todos los casos.
            return modelos[0]
    if len(modelos) > 1:
        raise TmdlValidationError(
            f"Hay {len(modelos)} carpetas .SemanticModel en {destino}: indica cual.",
            details={"candidates": [m.name for m in modelos]})

    informes = sorted(destino.glob("*.Report"))
    if informes and not modelos:
        raise ReportOnlyProjectError(
            f"'{destino.name}' es un proyecto de SOLO INFORME: tiene "
            f"'{informes[0].name}' pero ningun .SemanticModel. Es lo normal en "
            "un informe con conexion en vivo a un dataset publicado, o al "
            "convertir con include_model=false. No hay TMDL que validar, y no "
            "hay nada roto: valida el informe con pbi_audit_report_only.",
            details={"path": str(destino), "report_only": True,
                     "report_dir": informes[0].name})

    raise TmdlValidationError(
        f"No se encontro un modelo TMDL a partir de {destino}. Pasa el .pbip, "
        "la carpeta del proyecto, la .SemanticModel o la carpeta definition.",
        details={"path": str(destino)})


def validate(definition: Path | str, use_tom: bool = True) -> Dict[str, Any]:
    """Valida una carpeta `definition/` de un modelo semantico TMDL.

    `use_tom=False` se queda en el lint estatico: util en pruebas y en equipos
    sin las DLL de Analysis Services.
    """
    definition = Path(definition)
    if not definition.is_dir():
        raise TmdlValidationError(
            f"No existe la carpeta de definicion del modelo: {definition}",
            details={"path": str(definition)})

    hallazgos: List[Dict[str, Any]] = []
    tablas: List[Dict[str, Any]] = []
    ilegibles: List[str] = []

    model_bim = definition / "model.bim"
    if model_bim.exists():
        return _validate_model_bim(model_bim)

    tables_dir = definition / "tables"
    archivos = sorted(tables_dir.glob("*.tmdl")) if tables_dir.is_dir() else []
    for archivo in archivos:
        hallazgos.extend(_lint_orden_de_propiedades(archivo))
        try:
            tablas.append(tmdl_reader.parse_table_file(archivo))
        except Exception as exc:  # noqa: BLE001
            ilegibles.append(str(archivo))
            hallazgos.append(_finding(
                "tmdl_table_unreadable", "error",
                {"kind": "table", "file": str(archivo)},
                {"cause": str(exc)},
                "El archivo no se pudo leer como una tabla TMDL. Revisalo "
                "antes de seguir: el resto de comprobaciones lo omite."))

    relaciones_file = definition / "relationships.tmdl"
    hallazgos.extend(_lint_descripcion_en_relaciones(relaciones_file))
    relaciones = tmdl_reader._parse_relationships(relaciones_file)

    hallazgos.extend(_lint_colisiones(tablas))
    hallazgos.extend(_lint_medidas_duplicadas(tablas))
    hallazgos.extend(_lint_tablas_declaradas(definition, tablas, ilegibles))
    if not ilegibles:
        # Con una tabla ilegible el indice esta incompleto y daria falsos
        # positivos: se prefiere no acusar a acusar mal.
        hallazgos.extend(_lint_relaciones(tablas, relaciones))
    cultura = _source_query_culture(definition)
    hallazgos.extend(_lint_cultura(definition, tablas, cultura))

    parsed: Optional[bool] = None
    parse_error = None
    parse_checked = False
    parse_skipped_reason: Optional[str] = None
    counts_tom = None

    if use_tom:
        try:
            resultado = parse_with_tom(definition)
            parse_checked = True
            parsed = resultado["parsed"]
            parse_error = resultado["error"]
            counts_tom = resultado["counts"]
            if parsed is False:
                hallazgos.append(_finding(
                    "tmdl_parse_failed", "error",
                    {"kind": "model",
                     "file": parse_error.get("file") or str(definition),
                     "line": parse_error.get("line")},
                    {"message": parse_error["message"]},
                    "El serializador oficial no puede leer este TMDL, asi que "
                    "Power BI Desktop tampoco lo abrira."))
        except Exception as exc:  # noqa: BLE001 - sin DLL, sin runtime, etc.
            parse_skipped_reason = str(exc)
            log.info("Parseo TOM no disponible: %s", exc)
    else:
        parse_skipped_reason = "use_tom=False"

    errores = [f for f in hallazgos if f["severity"] == "error"]
    return {
        "valid": not errores,
        "format": "tmdl",
        "findings": hallazgos,
        "error_count": len(errores),
        "warning_count": len(hallazgos) - len(errores),
        "parsed": parsed,
        "parse_checked": parse_checked,
        "parse_skipped_reason": parse_skipped_reason,
        "parse_error": parse_error,
        "counts": {
            "table_files": len(archivos),
            "tables_read": len(tablas),
            "unreadable_files": ilegibles,
            "relationships": len(relaciones),
            "measures": sum(len(t["measures"]) for t in tablas),
        },
        "tom_counts": counts_tom,
        "source_query_culture": cultura,
        "limitations": list(LIMITACIONES),
    }
