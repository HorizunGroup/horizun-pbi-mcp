"""Tablas desde bases de datos y APIs: el M es lo facil; la verdad, primero.

Fase 3 de la vision. La trampa que esta escrita en el roadmap desde el dia
uno: generar el M para SQL Server u OData son cuatro lineas; lo que duele son
las CREDENCIALES y los niveles de privacidad, que viven en la interfaz de
Power BI Desktop y NO se guardan en el `.pbip`. Este modulo no finge lo
contrario:

- Escribe la consulta M y el TMDL correctos, transaccionados y validados como
  todo lo demas.
- Dice en la respuesta, siempre, que el PRIMER refresh lo hara una persona en
  Desktop: autenticarse y elegir nivel de privacidad no es automatizable
  desde aqui, y prometerlo seria mentir.
- No conecta a la fuente ni infiere el esquema: sin credenciales no se puede,
  asi que las columnas las declara el llamante -que conoce su base- y se
  validan contra el conjunto de tipos TMDL. Inventar columnas esta prohibido
  por la regla 5 de la casa.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.pbip.table_from_file import _literal_m, _tmdl_nombre

log = get_logger("table_from_source")


class TableFromSourceError(PowerBIMCPError):
    code = "table_from_source_error"


#: Tipos TMDL admitidos para columnas declaradas, y su tipo M para APIs.
_TIPOS = {"string": "type text", "int64": "Int64.Type",
          "double": "type number", "decimal": "Currency.Type",
          "dateTime": "type datetime", "boolean": "type logical"}

SOURCES = ("sqlserver", "postgresql", "odata", "web_json")

#: El aviso que viaja SIEMPRE. Las credenciales no estan en el .pbip: estan en
#: el almacen de Desktop, por usuario y por maquina.
AVISO_CREDENCIALES = (
    "La consulta quedo escrita, pero el PRIMER refresh lo completa una "
    "persona en Power BI Desktop: pedira credenciales de la fuente y nivel "
    "de privacidad, y ambos viven en Desktop (no en el .pbip). Hasta "
    "entonces la tabla existe sin datos, y este servidor no puede verificar "
    "la conexion.")


def _validar_columnas(columns: Any) -> List[Dict[str, str]]:
    if not isinstance(columns, list) or not columns:
        raise ValidationError(
            "Declara 'columns' ([{name, type}]): sin credenciales no se puede "
            "leer el esquema de la fuente, y las columnas no se inventan. "
            f"Tipos: {sorted(_TIPOS)}.")
    salida = []
    vistos = set()
    for i, c in enumerate(columns):
        if not isinstance(c, dict) or not str(c.get("name") or "").strip():
            raise ValidationError(f"columns[{i}] necesita 'name'.")
        tipo = str(c.get("type") or "string")
        if tipo not in _TIPOS:
            raise ValidationError(
                f"columns[{i}].type '{tipo}' no es un tipo TMDL. "
                f"Usa uno de: {sorted(_TIPOS)}.")
        nombre = str(c["name"]).strip()
        if nombre.casefold() in vistos:
            raise ValidationError(f"Columna duplicada: '{nombre}'.")
        vistos.add(nombre.casefold())
        salida.append({"name": nombre, "data_type": tipo})
    return salida


# ------------------------------------------------------------ consultas M ---
def _m_sqlserver(server: str, database: str, schema: str, source_table: str,
                 native_query: Optional[str]) -> str:
    origen = (f"    Origen = Sql.Database({_literal_m(server)}, "
              f"{_literal_m(database)}),")
    if native_query:
        # EnableFolding=true: si el motor no puede plegar la consulta, que lo
        # diga en el refresh en vez de traerse la tabla entera y filtrar local.
        datos = (f"    Datos = Value.NativeQuery(Origen, "
                 f"{_literal_m(native_query)}, null, [EnableFolding = true])")
    else:
        datos = (f"    Datos = Origen{{[Schema = {_literal_m(schema)}, "
                 f"Item = {_literal_m(source_table)}]}}[Data]")
    return f"let\n{origen}\n{datos}\nin\n    Datos"


def _m_postgresql(server: str, database: str, schema: str,
                  source_table: str) -> str:
    return ("let\n"
            f"    Origen = PostgreSQL.Database({_literal_m(server)}, "
            f"{_literal_m(database)}),\n"
            f"    Datos = Origen{{[Schema = {_literal_m(schema)}, "
            f"Item = {_literal_m(source_table)}]}}[Data]\n"
            "in\n    Datos")


def _m_odata(url: str) -> str:
    # La URL debe apuntar al ENTITY SET (…/odata/Presupuestos), no a la raiz
    # del servicio: asi OData.Feed devuelve directamente la tabla.
    return ("let\n"
            f"    Origen = OData.Feed({_literal_m(url)}, null, "
            "[Implementation = \"2.0\"])\n"
            "in\n    Origen")


def _m_web_json(url: str, json_path: Optional[List[str]],
                columnas: List[Dict[str, str]]) -> str:
    descenso = "".join(f"[{p}]" for p in (json_path or []))
    tipos = ", ".join(
        f"{{{_literal_m(c['name'])}, {_TIPOS[c['data_type']]}}}"
        for c in columnas)
    # La cultura va FIJA a en-US: el JSON escribe numeros sin cultura (punto
    # decimal siempre), y dejar la del sistema es el bug del 10527.52 que se
    # vuelve diez millones. Aqui no hay nada que deducir: es el estandar JSON.
    return ("let\n"
            f"    Origen = Json.Document(Web.Contents({_literal_m(url)})),\n"
            f"    Registros = Origen{descenso},\n"
            "    Tabla = Table.FromRecords(Registros),\n"
            f"    #\"Tipo cambiado\" = Table.TransformColumnTypes(Tabla, "
            f"{{{tipos}}}, \"en-US\")\n"
            "in\n    #\"Tipo cambiado\"")


# ------------------------------------------------------------------- TMDL ---
def _tmdl(nombre: str, columnas: List[Dict[str, str]], consulta_m: str,
          description: Optional[str]) -> str:
    lineas: List[str] = []
    if description:
        lineas += [f"/// {l.strip()}" for l in str(description).splitlines()]
    lineas.append(f"table {_tmdl_nombre(nombre)}")
    lineas.append(f"\tlineageTag: {uuid.uuid4()}")
    lineas.append("")
    for c in columnas:
        tipo = c["data_type"]
        lineas.append(f"\tcolumn {_tmdl_nombre(c['name'])}")
        lineas.append(f"\t\tdataType: {tipo}")
        if tipo in ("int64", "double", "decimal"):
            lineas.append("\t\tformatString: " + ("0" if tipo == "int64" else "0.00"))
        elif tipo == "dateTime":
            lineas.append("\t\tformatString: Short Date")
        lineas.append(f"\t\tlineageTag: {uuid.uuid4()}")
        lineas.append("\t\tsummarizeBy: " +
                      ("sum" if tipo in ("int64", "double", "decimal") else "none"))
        lineas.append(f"\t\tsourceColumn: {c['name']}")
        lineas.append("")
    lineas.append(f"\tpartition {_tmdl_nombre(nombre)} = m")
    lineas.append("\t\tmode: import")
    lineas.append("\t\tsource =")
    lineas += ["\t\t\t\t" + l for l in consulta_m.split("\n")]
    lineas.append("")
    lineas.append("\tannotation PBI_ResultType = Table")
    lineas.append("")
    return "\n".join(lineas)


# --------------------------------------------------------------- entrada ---
def agregar_tabla_desde_fuente(
        active: Any, source: str, table_name: str,
        columns: List[Dict[str, Any]],
        server: str = "", database: str = "", schema: str = "dbo",
        source_table: str = "", url: str = "",
        native_query: Optional[str] = None,
        json_path: Optional[List[str]] = None,
        description: Optional[str] = None,
        overwrite: bool = False, dry_run: bool = False) -> Dict[str, Any]:
    """Crea la tabla (TMDL + particion M) apuntando a una fuente externa."""
    from horizun_pbi_mcp.pbip.model_author import ModelAuthorError, resolver_destino_tabla
    from horizun_pbi_mcp.utils.validation import validate_object_name

    fuente = str(source).strip().casefold()
    if fuente not in SOURCES:
        raise ValidationError(
            f"Fuente '{source}' no soportada. Opciones: {list(SOURCES)}.")
    columnas = _validar_columnas(columns)
    nombre = validate_object_name(table_name, "tabla")

    if fuente in ("sqlserver", "postgresql"):
        if not server or not database:
            raise ValidationError(
                f"'{fuente}' necesita 'server' y 'database'.")
        if not native_query and not source_table:
            raise ValidationError(
                "Indica 'source_table' (con 'schema', defecto dbo) o una "
                "'native_query'.")
        if fuente == "postgresql" and native_query:
            raise ValidationError(
                "native_query solo esta soportada en sqlserver por ahora; "
                "para PostgreSQL usa 'source_table'.")
        consulta = (_m_sqlserver(server, database, schema, source_table,
                                 native_query)
                    if fuente == "sqlserver"
                    else _m_postgresql(server, database, schema, source_table))
    else:
        if not url:
            raise ValidationError(f"'{fuente}' necesita 'url'.")
        if not url.casefold().startswith(("https://", "http://")):
            raise ValidationError("La 'url' debe ser http(s).")
        consulta = (_m_odata(url) if fuente == "odata"
                    else _m_web_json(url, json_path, columnas))

    # La consulta que se acaba de armar lleva lo que el llamante paso: una
    # `native_query` o una URL pueden traer la credencial incrustada, y una vez
    # escrita queda en texto plano dentro del .pbip.
    from horizun_pbi_mcp.services import secret_scan

    escaneo = secret_scan.build_result(
        secret_scan.scan_text(consulta, file="power_query"), files_scanned=1)
    if escaneo["status"] == secret_scan.BLOCKED:
        raise TableFromSourceError(
            "La consulta M generada lleva una credencial incrustada y no se "
            "escribe: quedaria en texto plano dentro del proyecto. Las "
            "credenciales van en el almacen de Power BI Desktop, no en el M. "
            "En 'security_scan' esta la regla y la linea; el valor NO se "
            "devuelve a proposito.",
            details={"security_scan": escaneo})

    texto = _tmdl(nombre, columnas, consulta, description)
    resumen: Dict[str, Any] = {
        "table": nombre, "source": fuente,
        "columns": columnas, "column_count": len(columnas),
        "security_scan": escaneo,
        "warnings": ([AVISO_CREDENCIALES]
                     + ([f"{escaneo['finding_count']} hallazgo(s) de seguridad "
                         "de BAJA confianza en la consulta generada; revisa "
                         "'security_scan'."]
                        if escaneo["status"] == secret_scan.WARNING else [])),
    }
    if dry_run:
        return {**resumen, "dry_run": True, "tmdl": texto, "m": consulta}

    try:
        destino = resolver_destino_tabla(active, nombre, overwrite)
    except ModelAuthorError as exc:
        raise TableFromSourceError(exc.message, details=exc.details) from exc

    from horizun_pbi_mcp.pbip.model_author import escribir_tabla_y_registrarla

    salida = escribir_tabla_y_registrarla(
        active, destino, texto.split("\n"), nombre,
        "pbi_add_table_from_source")
    return {**resumen, **salida}
