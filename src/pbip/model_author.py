"""Autoria del modelo semantico en TMDL: mas alla de las medidas.

Hasta ahora el servidor solo sabia crear medidas, y eso deja fuera cosas que
un tablero necesita a diario: una columna calculada para clasificar, una
relacion para que dos tablas se filtren, una jerarquia para poder profundizar.

Tres reglas del formato que no se deducen leyendo la documentacion y que aqui
estan resueltas:

- La descripcion es un doc-comment `///` ENCIMA de la declaracion. La propiedad
  `description:` no existe y Power BI rechaza el archivo.
- Toda entidad lleva `lineageTag`: es lo que permite renombrarla sin romper los
  visuales que la usan.
- Las relaciones viven en `relationships.tmdl`, no dentro de la tabla, y
  referencian las columnas como `Tabla.Columna` con la tabla entrecomillada si
  lleva espacios.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError, ValidationError
from pbip.tmdl_reader import (_parse_relationships, find_table_file,
                              parse_table_file)
from utils.validation import (tmdl_quote_name, validate_measure_expression,
                              validate_object_name)

log = get_logger("model_author")

#: Como resume Power BI una columna por defecto.
RESUMEN = ("none", "sum", "average", "min", "max", "count", "distinctCount")
#: Tipos de dato de una columna calculada.
TIPOS = ("string", "int64", "double", "decimal", "boolean", "dateTime", "binary")
#: Cardinalidades y sentido del filtro de una relacion.
CARDINALIDADES = ("one", "many")
FILTRO_CRUZADO = ("oneDirection", "bothDirections", "automatic")


class ModelAuthorError(PowerBIMCPError):
    code = "model_author_error"


def _indent(linea: str) -> int:
    n = 0
    for c in linea:
        if c == "\t":
            n += 1
        else:
            break
    return n


def _definition(active: ActivePbip) -> Path:
    if not active.semantic_model_dir:
        raise ModelAuthorError("El proyecto no tiene carpeta .SemanticModel.")
    d = Path(active.semantic_model_dir) / "definition"
    if not d.exists():
        raise ModelAuthorError(f"No existe {d}: el modelo no esta en TMDL.")
    return d


def _escribir(active: ActivePbip, ruta: Path, lineas: List[str],
              herramienta: str) -> Dict[str, Any]:
    """Escritura transaccional, con las mismas garantias que el resto."""
    from services import txn as txn_service
    from services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation=herramienta)
    texto = "\n".join(lineas)
    if not texto.endswith("\n"):
        texto += "\n"
    cm = txn_service.project_transaction(active, [ruta], tool=herramienta)
    with cm as t:
        t.write_text(ruta, texto)
    return {"file": str(ruta), "backup": cm.result["journal"],
            "transaction": cm.result}


def _texto(lineas: List[str]) -> str:
    texto = "\n".join(lineas)
    return texto if texto.endswith("\n") else texto + "\n"


def _plan_registro_tabla(active: ActivePbip, nombre: str
                         ) -> tuple[Path, List[str], bool]:
    """Calcula el `ref table` sin escribir nada."""
    modelo = _definition(active) / "model.tmdl"
    if not modelo.exists():
        raise ModelAuthorError(
            "El modelo no tiene model.tmdl, asi que no se puede declarar la "
            "tabla. El proyecto esta incompleto.",
            details={"expected": str(modelo)})

    lineas = modelo.read_text(encoding="utf-8-sig").splitlines()
    ya_esta = any(
        l.strip().startswith("ref table ")
        and _unquote_tmdl(l.strip()[len("ref table "):].strip()).casefold()
        == nombre.casefold()
        for l in lineas)
    if ya_esta:
        return modelo, lineas, False

    ultimos = [i for i, l in enumerate(lineas)
               if l.strip().startswith("ref table ")]
    destino = (ultimos[-1] + 1) if ultimos else len(lineas)
    lineas.insert(destino, f"ref table {tmdl_quote_name(nombre)}")
    return modelo, lineas, True


def _errores(resultado: Dict[str, Any]) -> set[str]:
    """Identidades estables de los errores de validacion de un modelo."""
    return {
        json.dumps(f, sort_keys=True, ensure_ascii=False, default=str)
        for f in resultado.get("findings", [])
        if f.get("severity") == "error"
    }


def _validar_modelo_sin_errores_nuevos(definicion: Path,
                                       baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Valida dentro de la transaccion; lanzar aqui provoca rollback total."""
    from services import tmdl_validate

    revision = tmdl_validate.validate(definicion, use_tom=True)
    introducidos = _errores(revision) - _errores(baseline)
    if introducidos:
        detalles = [json.loads(item) for item in sorted(introducidos)]
        raise ModelAuthorError(
            "El cambio introduce errores en el modelo TMDL. Se revierte la "
            "operacion completa para no dejar un proyecto que Power BI no abra.",
            details={"findings": detalles, "definition": str(definicion)})
    return {
        "valid": revision["valid"],
        "parsed": revision.get("parsed"),
        "parse_checked": revision.get("parse_checked", False),
        "introduced_errors": 0,
    }


def escribir_tabla_y_registrarla(active: ActivePbip, ruta: Path,
                                 lineas: List[str], nombre: str,
                                 herramienta: str) -> Dict[str, Any]:
    """Escribe tabla + `model.tmdl` en UNA transaccion validada.

    Antes eran dos commits independientes. Si fallaba el segundo, quedaba un
    archivo de tabla huerfano que el modelo no referenciaba. La validacion se
    ejecuta despues de escribir ambos objetivos pero antes del commit: cualquier
    error lanza dentro del context manager y restaura los dos archivos.
    """
    from services import tmdl_validate
    from services import txn as txn_service
    from services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation=herramienta)
    definicion = _definition(active)
    modelo, modelo_final, ref_added = _plan_registro_tabla(active, nombre)
    baseline = tmdl_validate.validate(definicion, use_tom=True)

    objetivos = [ruta]
    if ref_added:
        objetivos.append(modelo)
    cm = txn_service.project_transaction(active, objetivos, tool=herramienta)
    with cm as t:
        t.write_text(ruta, _texto(lineas))
        if ref_added:
            t.write_text(modelo, _texto(modelo_final))
        validacion = _validar_modelo_sin_errores_nuevos(definicion, baseline)

    log.info("Tabla '%s' y registro en model.tmdl confirmados juntos", nombre)
    return {
        "file": str(ruta),
        "model_tmdl": str(modelo),
        "ref_added": ref_added,
        "backup": cm.result["journal"],
        "transaction": cm.result,
        "model_validation": validacion,
    }


def _escribir_modelo_validado(active: ActivePbip, ruta: Path,
                              lineas: List[str], herramienta: str
                              ) -> Dict[str, Any]:
    """Escribe un archivo TMDL y valida el modelo antes del commit."""
    from services import tmdl_validate
    from services import txn as txn_service
    from services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation=herramienta)
    definicion = _definition(active)
    baseline = tmdl_validate.validate(definicion, use_tom=True)
    cm = txn_service.project_transaction(active, [ruta], tool=herramienta)
    with cm as t:
        t.write_text(ruta, _texto(lineas))
        validacion = _validar_modelo_sin_errores_nuevos(definicion, baseline)
    return {"file": str(ruta), "backup": cm.result["journal"],
            "transaction": cm.result, "model_validation": validacion}


def _bloque_existe(lineas: List[str], palabra: str, nombre: str) -> Optional[int]:
    """Indice de la linea donde empieza `<palabra> <nombre>`, si existe."""
    objetivo = nombre.strip("'")
    for i, linea in enumerate(lineas):
        limpio = linea.strip()
        if not limpio.startswith(palabra + " "):
            continue
        declarado = limpio[len(palabra):].strip().split("=")[0].strip()
        if declarado.strip("'") == objetivo:
            return i
    return None


def _fin_del_bloque(lineas: List[str], inicio: int) -> int:
    """Primera linea despues del bloque que empieza en `inicio`."""
    base = _indent(lineas[inicio])
    j = inicio + 1
    ultimo = inicio
    while j < len(lineas):
        if lineas[j].strip() and _indent(lineas[j]) <= base:
            break
        if lineas[j].strip():
            ultimo = j
        j += 1
    return ultimo + 1


# ------------------------------------------------------- columna calculada ---
def create_calculated_column(active: ActivePbip, table: str, name: str,
                             expression: str, *,
                             data_type: str = "string",
                             format_string: Optional[str] = None,
                             display_folder: Optional[str] = None,
                             description: Optional[str] = None,
                             summarize_by: str = "none",
                             is_hidden: bool = False,
                             overwrite: bool = False) -> Dict[str, Any]:
    """Añade una columna calculada (DAX) a una tabla existente."""
    name = validate_object_name(name, "columna")
    expression = validate_measure_expression(expression)
    if data_type not in TIPOS:
        raise ModelAuthorError(f"Tipo no soportado: '{data_type}'. Usa {list(TIPOS)}.")
    if summarize_by not in RESUMEN:
        raise ModelAuthorError(
            f"summarize_by no soportado: '{summarize_by}'. Usa {list(RESUMEN)}.")

    ruta = find_table_file(active, table)
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines()

    existente = _bloque_existe(lineas, "column", tmdl_quote_name(name))
    if existente is None:
        existente = _bloque_existe(lineas, "column", name)
    if existente is not None:
        if not overwrite:
            raise ModelAuthorError(
                f"La columna '{name}' ya existe en '{table}'. Usa overwrite=true.")
        fin = _fin_del_bloque(lineas, existente)
        del lineas[existente:fin]
        insercion = existente
    else:
        insercion = _indice_insercion_en_tabla(lineas)

    bloque: List[str] = []
    if description:
        bloque += [f"\t/// {l.strip()}" for l in str(description).splitlines()]
    if "\n" in expression:
        bloque.append(f"\tcolumn {tmdl_quote_name(name)} =")
        bloque += ["\t\t\t" + l for l in expression.split("\n")]
    else:
        bloque.append(f"\tcolumn {tmdl_quote_name(name)} = {expression}")
    bloque.append(f"\t\tdataType: {data_type}")
    if format_string:
        bloque.append(f"\t\tformatString: {format_string}")
    if is_hidden:
        bloque.append("\t\tisHidden")
    bloque.append(f"\t\tlineageTag: {uuid.uuid4()}")
    bloque.append(f"\t\tsummarizeBy: {summarize_by}")
    if display_folder:
        bloque.append(f"\t\tdisplayFolder: {display_folder}")
    bloque.append("")

    lineas[insercion:insercion] = bloque
    salida = _escribir(active, ruta, lineas, "pbi_create_calculated_column")
    log.info("Columna calculada '%s' en '%s'", name, table)
    return {"table": table, "column": name, "data_type": data_type,
            "action": "replaced" if existente is not None else "created",
            "expression": expression, **salida}


def infer_columns(session: Any, expression: str) -> List[Dict[str, str]]:
    """Deduce columnas y tipos de una tabla calculada EJECUTANDO su DAX.

    TMDL exige declarar las columnas de una tabla calculada, y no se pueden
    adivinar leyendo la expresion. En vez de pedirselas a quien llama —que
    tendria que teclear diez columnas a mano y equivocarse en los tipos— se
    evalua la expresion acotada a una fila contra el modelo abierto y se lee el
    esquema que devuelve el motor. Es la unica fuente que no adivina.
    """
    from powerbi import dax_runner

    consulta = f"EVALUATE TOPN(1, {expression})"
    r = dax_runner.run_dax(session, consulta, max_rows=1)
    columnas: List[Dict[str, str]] = []
    equivalencia = {"int": "int64", "float": "double", "str": "string",
                    "bool": "boolean", "datetime": "dateTime",
                    "decimal": "decimal"}
    for c in r.get("column_types") or []:
        nombre = str(c.get("name", "")).strip()
        # El motor devuelve 'Tabla[Columna]' o '[Columna]'
        if "[" in nombre and nombre.endswith("]"):
            nombre = nombre[nombre.rindex("[") + 1:-1]
        if not nombre:
            continue
        columnas.append({"name": nombre,
                         "data_type": equivalencia.get(str(c.get("type")), "string")})
    if not columnas:
        raise ModelAuthorError(
            "La expresion no devolvio ninguna columna, asi que no hay tabla que "
            "declarar. Comprueba el DAX con pbi_run_dax.",
            details={"query": consulta})
    return columnas


#: Grafias aceptadas para cada propiedad de columna. TMDL usa camelCase y el
#: resto del codigo snake_case; quien llama no tiene por que saber cual toca.
_ALIAS_COLUMNA = {
    "name": "name", "nombre": "name",
    "data_type": "data_type", "datatype": "data_type", "type": "data_type",
    "tipo": "data_type",
    "summarize_by": "summarize_by", "summarizeby": "summarize_by",
    "format_string": "format_string", "formatstring": "format_string",
    "display_folder": "display_folder", "displayfolder": "display_folder",
    "source_column": "source_column", "sourcecolumn": "source_column",
    "is_hidden": "is_hidden", "ishidden": "is_hidden",
    "description": "description", "descripcion": "description",
}


def registrar_tabla_en_modelo(active: ActivePbip, nombre: str,
                              herramienta: str) -> Dict[str, Any]:
    """Declara `ref table <nombre>` en model.tmdl si aun no esta.

    Escribir el archivo de la tabla NO la mete en el modelo: hace falta esta
    linea. Sin ella el .tmdl se ve perfecto en disco, el proyecto abre sin
    quejarse y la tabla simplemente no existe, asi que cualquier medida o
    visual que la use aparece roto sin ninguna explicacion.
    """
    modelo, lineas, ref_added = _plan_registro_tabla(active, nombre)
    if not ref_added:
        return {"model_tmdl": str(modelo), "ref_added": False}

    _escribir(active, modelo, lineas, herramienta)
    log.info("Tabla '%s' declarada en model.tmdl", nombre)
    return {"model_tmdl": str(modelo), "ref_added": True}


def _unquote_tmdl(nombre: str) -> str:
    nombre = nombre.strip()
    if nombre.startswith("'") and nombre.endswith("'") and len(nombre) >= 2:
        return nombre[1:-1].replace("''", "'")
    return nombre


def _normalizar_columnas(columns: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Unifica las grafias y RECHAZA lo que no reconoce.

    Antes se leia solo `data_type` y cualquier otra grafia caia al defecto
    'string' sin avisar: una columna numerica quedaba escrita como texto y las
    agregaciones dejaban de funcionar sin que nada fallara. Un tipo que se
    pierde en silencio es peor que un error, asi que un nombre de propiedad
    desconocido ahora se rechaza en vez de ignorarse.
    """
    normalizadas: List[Dict[str, str]] = []
    for indice, columna in enumerate(columns):
        if not isinstance(columna, dict):
            raise ModelAuthorError(
                f"La columna {indice} no es un objeto: {columna!r}.")
        salida: Dict[str, str] = {}
        for clave, valor in columna.items():
            destino = _ALIAS_COLUMNA.get(str(clave).replace("_", "").lower())
            if destino is None:
                raise ModelAuthorError(
                    f"Propiedad de columna desconocida: '{clave}'. Admitidas: "
                    f"{', '.join(sorted(set(_ALIAS_COLUMNA.values())))}.",
                    details={"column_index": indice, "unknown_key": clave})
            salida[destino] = valor
        if not str(salida.get("name", "")).strip():
            raise ModelAuthorError(
                f"La columna {indice} no tiene 'name'.",
                details={"column_index": indice, "column": columna})
        normalizadas.append(salida)
    return normalizadas


def create_calculated_table(active: ActivePbip, name: str, expression: str, *,
                            columns: Optional[List[Dict[str, str]]] = None,
                            session: Any = None,
                            description: Optional[str] = None,
                            overwrite: bool = False) -> Dict[str, Any]:
    """Crea una tabla calculada (DAX) como archivo TMDL propio.

    `columns` se puede dar a mano; si no, se deducen ejecutando la expresion
    contra el modelo abierto (`session`). Sin una de las dos cosas no se puede
    escribir la tabla, y se dice en vez de generar uno vacio.
    """
    name = validate_object_name(name, "tabla")
    expression = validate_measure_expression(expression)

    definicion = _definition(active)
    carpeta = definicion / "tables"
    seguro = re.sub(r'[<>:"/\\|?*]', "_", name)
    ruta = carpeta / f"{seguro}.tmdl"
    if ruta.exists() and not overwrite:
        raise ModelAuthorError(
            f"Ya existe la tabla '{name}'. Usa overwrite=true para reemplazarla.")

    if not columns:
        if session is None:
            raise ModelAuthorError(
                "Hacen falta las columnas de la tabla calculada. Pasalas en "
                "'columns' o abre el modelo en Power BI Desktop para deducirlas "
                "ejecutando la expresion.")
        columns = infer_columns(session, expression)
    columns = _normalizar_columnas(columns)

    lineas: List[str] = []
    if description:
        lineas += [f"/// {l.strip()}" for l in str(description).splitlines()]
    lineas.append(f"table {tmdl_quote_name(name)}")
    lineas.append(f"\tlineageTag: {uuid.uuid4()}")
    lineas.append("")
    for col in columns:
        tipo = col.get("data_type", "string")
        if tipo not in TIPOS:
            raise ModelAuthorError(
                f"Tipo no soportado en la columna '{col.get('name')}': '{tipo}'.")
        lineas.append(f"\tcolumn {tmdl_quote_name(col['name'])}")
        lineas.append(f"\t\tdataType: {tipo}")
        lineas.append(f"\t\tlineageTag: {uuid.uuid4()}")
        lineas.append("\t\tsummarizeBy: none")
        lineas.append(f"\t\tsourceColumn: {col['name']}")
        lineas.append("")
    lineas.append(f"\tpartition {tmdl_quote_name(name)} = calculated")
    lineas.append("\t\tmode: import")
    if "\n" in expression:
        lineas.append("\t\tsource =")
        lineas += ["\t\t\t\t" + l for l in expression.split("\n")]
    else:
        lineas.append(f"\t\tsource = {expression}")
    lineas.append("")

    salida = escribir_tabla_y_registrarla(
        active, ruta, lineas, name, "pbi_create_calculated_table")
    log.info("Tabla calculada '%s' con %s columnas", name, len(columns))
    return {"table": name, "columns": columns, "column_count": len(columns),
            "expression": expression,
            "action": "replaced" if overwrite else "created",
            **salida}


#: Modos de almacenamiento de una particion.
MODOS = ("import", "directQuery", "dual")


def set_storage_mode(active: ActivePbip, table: str, mode: str) -> Dict[str, Any]:
    """Cambia el modo de almacenamiento de las particiones de una tabla.

    Con `directQuery` el dato se consulta al origen en cada interaccion y deja
    de haber refresco que esperar, pero **no es un interruptor inocuo**:

    - la consulta M tiene que ser plegable al origen; si lleva pasos que no se
      traducen a SQL, Power BI rechaza la tabla al abrirla;
    - las columnas y tablas calculadas dejan de estar disponibles;
    - cada visual se convierte en una consulta al servidor, y un origen lento
      se nota en todo el informe.

    Por eso se devuelve el modo anterior y las particiones tocadas: es un
    cambio que hay que poder deshacer sabiendo exactamente que se cambio.
    """
    if mode not in MODOS:
        raise ModelAuthorError(
            f"Modo no soportado: '{mode}'. Usa {list(MODOS)}.")

    ruta = find_table_file(active, table)
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines()

    anteriores: List[str] = []
    tocadas = 0
    dentro_de_particion = False
    for i, linea in enumerate(lineas):
        limpio = linea.strip()
        if _indent(linea) == 1 and limpio.startswith("partition "):
            dentro_de_particion = True
            continue
        if dentro_de_particion and limpio.startswith("mode:"):
            anteriores.append(limpio.split(":", 1)[1].strip())
            lineas[i] = f"\t\tmode: {mode}"
            tocadas += 1
            dentro_de_particion = False
        elif _indent(linea) <= 1 and limpio and not limpio.startswith("partition "):
            dentro_de_particion = False

    if not tocadas:
        raise ModelAuthorError(
            f"La tabla '{table}' no declara ninguna particion con 'mode:'. "
            "Una tabla calculada sin particion importada no tiene modo que "
            "cambiar.")

    salida = _escribir(active, ruta, lineas, "pbi_set_storage_mode")
    log.info("Modo de '%s': %s -> %s (%s particiones)", table,
             ",".join(sorted(set(anteriores))), mode, tocadas)
    return {"table": table, "mode": mode,
            "previous": sorted(set(anteriores)), "partitions_changed": tocadas,
            "warning": ("DirectQuery exige que la consulta M sea plegable al "
                        "origen y desactiva las columnas calculadas. Abre el "
                        "informe y comprueba la tabla antes de dar el cambio "
                        "por bueno.") if mode == "directQuery" else None,
            **salida}


def _indice_insercion_en_tabla(lineas: List[str]) -> int:
    """Antes de la particion: las columnas van declaradas por encima."""
    for i, linea in enumerate(lineas):
        if _indent(linea) == 1 and linea.strip().startswith("partition "):
            return i
    for i in range(len(lineas) - 1, -1, -1):
        if lineas[i].strip():
            return i + 1
    return len(lineas)


def _resolver_columna(active: ActivePbip, table: str,
                      column: str) -> tuple[str, str]:
    """Devuelve nombres canonicos o falla antes de tocar relationships.tmdl."""
    table = validate_object_name(table, "tabla")
    column = validate_object_name(column, "columna")
    try:
        ruta = find_table_file(active, table)
        leida = parse_table_file(ruta)
    except Exception as exc:  # noqa: BLE001 - se traduce a error de autoria
        raise ModelAuthorError(
            f"La tabla '{table}' no existe o no se puede leer en el modelo TMDL.",
            details={"table": table, "column": column}) from exc

    encontrada = next((c["name"] for c in leida["columns"]
                       if c["name"].casefold() == column.casefold()), None)
    if encontrada is None:
        raise ModelAuthorError(
            f"La columna '{column}' no existe en la tabla '{table}'.",
            details={"table": table, "column": column,
                     "available": [c["name"] for c in leida["columns"]]})
    return leida["name"], encontrada


def _rangos_relaciones(lineas: List[str]) -> List[tuple[int, int]]:
    inicios = [i for i, linea in enumerate(lineas)
               if _indent(linea) == 0
               and linea.strip().startswith("relationship ")]
    return [(inicio, inicios[pos + 1] if pos + 1 < len(inicios) else len(lineas))
            for pos, inicio in enumerate(inicios)]


def _clave_extremos(relacion: Dict[str, Any]) -> frozenset[tuple[str, str]]:
    return frozenset({
        (str(relacion.get("from_table", "")).casefold(),
         str(relacion.get("from_column", "")).casefold()),
        (str(relacion.get("to_table", "")).casefold(),
         str(relacion.get("to_column", "")).casefold()),
    })


# --------------------------------------------------------------- relacion ----
def create_relationship(active: ActivePbip, from_table: str, from_column: str,
                        to_table: str, to_column: str, *,
                        from_cardinality: str = "many",
                        to_cardinality: str = "one",
                        cross_filtering: str = "oneDirection",
                        is_active: bool = True,
                        name: Optional[str] = None,
                        overwrite: bool = False) -> Dict[str, Any]:
    """Crea una relacion entre dos columnas.

    Por defecto muchos-a-uno con filtro en un sentido, que es lo que Power BI
    crea y lo unico que no introduce ambiguedad en el modelo.
    """
    for etiqueta, valor, validos in (("from_cardinality", from_cardinality, CARDINALIDADES),
                                     ("to_cardinality", to_cardinality, CARDINALIDADES),
                                     ("cross_filtering", cross_filtering, FILTRO_CRUZADO)):
        if valor not in validos:
            raise ModelAuthorError(
                f"{etiqueta} no soportado: '{valor}'. Usa {list(validos)}.")

    # Resolver ambos extremos es una precondicion, no una comprobacion tardia:
    # una referencia inexistente hace que Desktop no pueda cargar el modelo.
    from_table, from_column = _resolver_columna(active, from_table, from_column)
    to_table, to_column = _resolver_columna(active, to_table, to_column)
    if name is not None:
        name = validate_object_name(name, "relacion")

    ruta = _definition(active) / "relationships.tmdl"
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines() if ruta.exists() else []

    desde = f"{tmdl_quote_name(from_table)}.{tmdl_quote_name(from_column)}"
    hasta = f"{tmdl_quote_name(to_table)}.{tmdl_quote_name(to_column)}"

    existentes = _parse_relationships(ruta)
    clave_nueva = frozenset({
        (from_table.casefold(), from_column.casefold()),
        (to_table.casefold(), to_column.casefold()),
    })
    mismos_extremos = [i for i, rel in enumerate(existentes)
                       if _clave_extremos(rel) == clave_nueva]
    if len(mismos_extremos) > 1:
        raise ModelAuthorError(
            "El modelo ya contiene varias relaciones duplicadas entre estos "
            "extremos; corrige el modelo antes de reemplazarlas.",
            details={"from": desde, "to": hasta, "count": len(mismos_extremos)})
    if mismos_extremos and not overwrite:
        raise ModelAuthorError(
            f"Ya existe una relacion entre {desde} y {hasta}. "
            "Usa overwrite=true para reemplazarla.")

    reemplazada = mismos_extremos[0] if mismos_extremos else None
    nombres = {
        _unquote_tmdl(str(rel.get("name", ""))).casefold(): i
        for i, rel in enumerate(existentes)
        if rel.get("name")
    }
    if name is not None:
        colision = nombres.get(name.casefold())
        if colision is not None and colision != reemplazada:
            raise ModelAuthorError(
                f"Ya existe una relacion llamada '{name}'. Los nombres de "
                "relacion deben ser unicos en todo el modelo.",
                details={"name": name, "existing_index": colision})

    if reemplazada is not None:
        rangos = _rangos_relaciones(lineas)
        inicio, fin = rangos[reemplazada]
        del lineas[inicio:fin]

    if name is not None:
        identificador = name
    elif reemplazada is not None:
        identificador = _unquote_tmdl(str(existentes[reemplazada]["name"]))
    else:
        identificador = str(uuid.uuid4())
    bloque = [f"relationship {tmdl_quote_name(identificador)}"]
    if not is_active:
        bloque.append("\tisActive: false")
    if from_cardinality != "many":
        bloque.append(f"\tfromCardinality: {from_cardinality}")
    if to_cardinality != "one":
        bloque.append(f"\ttoCardinality: {to_cardinality}")
    if cross_filtering != "oneDirection":
        bloque.append(f"\tcrossFilteringBehavior: {cross_filtering}")
    bloque.append(f"\tfromColumn: {desde}")
    bloque.append(f"\ttoColumn: {hasta}")
    bloque.append("")

    if lineas and lineas[-1].strip():
        lineas.append("")
    lineas += bloque
    salida = _escribir_modelo_validado(
        active, ruta, lineas, "pbi_create_relationship")
    log.info("Relacion %s -> %s", desde, hasta)
    return {"name": identificador, "from": desde, "to": hasta,
            "from_cardinality": from_cardinality, "to_cardinality": to_cardinality,
            "cross_filtering": cross_filtering, "is_active": is_active, **salida}


# -------------------------------------------------------------- jerarquia ----
def create_hierarchy(active: ActivePbip, table: str, name: str,
                     levels: List[str], *,
                     display_folder: Optional[str] = None,
                     description: Optional[str] = None,
                     overwrite: bool = False) -> Dict[str, Any]:
    """Crea una jerarquia sobre columnas de la MISMA tabla.

    `levels`: nombres de columna, de mayor a menor granularidad. El orden es el
    de profundizacion, y por eso no se ordena ni se deduplica: es informacion.
    """
    name = validate_object_name(name, "jerarquia")
    if not levels:
        raise ModelAuthorError("Una jerarquia necesita al menos un nivel.")
    if len(set(levels)) != len(levels):
        raise ModelAuthorError(
            f"Hay niveles repetidos en la jerarquia: {levels}. Cada nivel debe "
            "ser una columna distinta.")

    ruta = find_table_file(active, table)
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines()
    columnas = {l.strip()[len("column"):].strip().split("=")[0].strip().strip("'")
                for l in lineas if _indent(l) == 1 and l.strip().startswith("column ")}
    faltan = [n for n in levels if n not in columnas]
    if faltan:
        raise ModelAuthorError(
            f"Estas columnas no existen en '{table}': {faltan}.",
            details={"available": sorted(columnas)})

    existente = _bloque_existe(lineas, "hierarchy", tmdl_quote_name(name))
    if existente is None:
        existente = _bloque_existe(lineas, "hierarchy", name)
    if existente is not None:
        if not overwrite:
            raise ModelAuthorError(
                f"La jerarquia '{name}' ya existe en '{table}'. Usa overwrite=true.")
        fin = _fin_del_bloque(lineas, existente)
        del lineas[existente:fin]
        insercion = existente
    else:
        insercion = _indice_insercion_en_tabla(lineas)

    bloque: List[str] = []
    if description:
        bloque += [f"\t/// {l.strip()}" for l in str(description).splitlines()]
    bloque.append(f"\thierarchy {tmdl_quote_name(name)}")
    if display_folder:
        bloque.append(f"\t\tdisplayFolder: {display_folder}")
    bloque.append(f"\t\tlineageTag: {uuid.uuid4()}")
    bloque.append("")
    for nivel in levels:
        bloque.append(f"\t\tlevel {tmdl_quote_name(nivel)}")
        bloque.append(f"\t\t\tlineageTag: {uuid.uuid4()}")
        bloque.append(f"\t\t\tcolumn: {tmdl_quote_name(nivel)}")
        bloque.append("")

    lineas[insercion:insercion] = bloque
    salida = _escribir(active, ruta, lineas, "pbi_create_hierarchy")
    log.info("Jerarquia '%s' en '%s' con %s niveles", name, table, len(levels))
    return {"table": table, "hierarchy": name, "levels": list(levels),
            "action": "replaced" if existente is not None else "created", **salida}
