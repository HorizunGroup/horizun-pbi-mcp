"""Particiones, consultas M y expresiones compartidas del modelo EN VIVO.

Por que existe
--------------
`pbi_list_partitions` contestaba `supported=false` con `source='live'` y
`pbi_get_power_query` contestaba `no_active_pbip` con un modelo vivo perfecto
delante. Las dos cosas estan en el motor tabular, expuestas por las DMV
`$SYSTEM.TMSCHEMA_PARTITIONS` y `$SYSTEM.TMSCHEMA_EXPRESSIONS`, y se leen con
la misma conexion ADOMD de solo lectura que usa todo lo demas.

Lo que se distingue, a proposito
--------------------------------
- Una **particion** pertenece a una tabla y tiene un tipo de origen (M,
  calculada, consulta heredada...) y un modo (import, DirectQuery, dual...).
- Una **expresion compartida** (`expressions.tmdl` en disco) es una consulta
  con nombre que no pertenece a ninguna tabla: parametros y funciones M.

No se inventa nada: un tipo o modo cuyo numero no este en la tabla de
correspondencia sale como `unknown(<n>)`, y una particion sin
`QueryDefinition` se declara sin consulta en vez de con una cadena vacia.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

log = get_logger("live_query")

PARTITION, EXPRESSION = "partition", "expression"
TIPOS = (PARTITION, EXPRESSION)

#: `PartitionSourceType` del modelo tabular, tal como lo publica la DMV.
_TIPO_DE_ORIGEN = {1: "query", 2: "calculated", 3: "none", 4: "m", 5: "entity",
                   6: "policyRange", 7: "calculationGroup", 8: "inferred"}
#: `ModeType` del modelo tabular.
_MODO = {0: "import", 1: "directQuery", 2: "default", 3: "push", 4: "dual",
         5: "directLake"}
#: `ExpressionKind`: hoy solo existe M.
_KIND_EXPRESION = {0: "m"}


class LiveQueryError(PowerBIMCPError):
    code = "live_query_error"


def _mapear(tabla: Dict[int, str], valor: Any) -> Optional[str]:
    if valor is None:
        return None
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return str(valor)
    return tabla.get(numero, f"unknown({numero})")


def sha256(texto: str) -> str:
    return hashlib.sha256((texto or "").encode("utf-8")).hexdigest()


def _filas(cli, consulta: str, columnas: List[str]) -> List[Dict[str, Any]]:
    cols, filas, _trunc, _ms = cli.execute_reader(consulta)
    indice = {c: i for i, c in enumerate(cols)}
    salida = []
    for fila in filas:
        registro = {}
        for nombre in columnas:
            i = indice.get(nombre)
            if i is None:
                # La DMV de esta version del motor no trae la columna: se
                # declara ausente, no se rellena.
                registro[nombre] = None
            else:
                registro[nombre] = fila[i]
        salida.append(registro)
    return salida


def leer_objetos(session: Session) -> Dict[str, Any]:
    """Inventario vivo: particiones (con su M) y expresiones compartidas."""
    from horizun_pbi_mcp.powerbi.adomd_client import AdomdClient

    modelo = session.require_active_model()
    avisos: List[str] = []
    with AdomdClient(modelo.connection_string, modelo.catalog) as cli:
        tablas = _filas(cli, "SELECT [ID],[Name] FROM $SYSTEM.TMSCHEMA_TABLES",
                        ["ID", "Name"])
        nombre_de_tabla = {t["ID"]: t["Name"] for t in tablas}
        particiones = _filas(
            cli,
            "SELECT [ID],[TableID],[Name],[Description],[Type],[Mode],"
            "[QueryDefinition],[State],[RefreshedTime] "
            "FROM $SYSTEM.TMSCHEMA_PARTITIONS",
            ["ID", "TableID", "Name", "Description", "Type", "Mode",
             "QueryDefinition", "State", "RefreshedTime"])
        try:
            expresiones = _filas(
                cli,
                "SELECT [ID],[Name],[Kind],[Expression],[Description] "
                "FROM $SYSTEM.TMSCHEMA_EXPRESSIONS",
                ["ID", "Name", "Kind", "Expression", "Description"])
        except Exception as exc:                          # noqa: BLE001
            # Un modelo sin expresiones compartidas contesta vacio; un motor
            # que no expone la DMV contesta con error. Se declara, no se
            # confunde con "no hay".
            avisos.append("No se pudo leer TMSCHEMA_EXPRESSIONS: "
                          f"{type(exc).__name__}.")
            expresiones = []

    objetos: List[Dict[str, Any]] = []
    for p in particiones:
        m = p.get("QueryDefinition")
        objetos.append({
            "kind": PARTITION,
            "table": nombre_de_tabla.get(p.get("TableID")),
            "name": p.get("Name"),
            "description": p.get("Description") or None,
            "source_type": _mapear(_TIPO_DE_ORIGEN, p.get("Type")),
            "mode": _mapear(_MODO, p.get("Mode")),
            "state": p.get("State"),
            "refreshed_time": (str(p["RefreshedTime"])
                               if p.get("RefreshedTime") is not None else None),
            "has_query": bool(m),
            "m": str(m) if m else None,
        })
    for e in expresiones:
        m = e.get("Expression")
        objetos.append({
            "kind": EXPRESSION,
            "table": None,
            "name": e.get("Name"),
            "description": e.get("Description") or None,
            "source_type": _mapear(_KIND_EXPRESION, e.get("Kind")),
            "mode": None,
            "has_query": bool(m),
            "m": str(m) if m else None,
        })
    return {"objects": objetos, "warnings": avisos,
            "partitions_supported": True,
            "expressions_supported": not avisos}


def list_partitions(session: Session) -> Dict[str, Any]:
    """Particiones y expresiones del modelo vivo, sin el texto M entero."""
    leido = leer_objetos(session)
    particiones = [{k: v for k, v in o.items() if k != "m"}
                   | {"query_sha256": sha256(o["m"]) if o.get("m") else None}
                   for o in leido["objects"] if o["kind"] == PARTITION]
    expresiones = [{k: v for k, v in o.items() if k not in ("m", "mode")}
                   | {"query_sha256": sha256(o["m"]) if o.get("m") else None}
                   for o in leido["objects"] if o["kind"] == EXPRESSION]
    return {"partitions": particiones, "expressions": expresiones,
            "warnings": leido["warnings"],
            "partitions_supported": leido["partitions_supported"],
            "expressions_supported": leido["expressions_supported"]}


def _candidatos(objetos: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "partitions": [{"table": o["table"], "name": o["name"],
                        "source_type": o["source_type"]}
                       for o in objetos if o["kind"] == PARTITION],
        "expressions": [{"name": o["name"]}
                        for o in objetos if o["kind"] == EXPRESSION],
    }


def get_power_query(session: Session, *, table: Optional[str] = None,
                    name: Optional[str] = None,
                    kind: Optional[str] = None) -> Dict[str, Any]:
    """El M de UNA particion o expresion del modelo vivo, con su SHA-256.

    Misma regla de seleccion que la version en disco: `table`+`name` para
    una particion, `name` con `kind='expression'` para una expresion, y ante
    ambiguedad o ausencia, el error trae los candidatos.
    """
    if kind and kind not in TIPOS:
        raise ValidationError(
            f"kind='{kind}' no existe. Usa {' o '.join(TIPOS)}.",
            details={"parameter": "kind", "valid": list(TIPOS)})
    leido = leer_objetos(session)
    objetos = list(leido["objects"])
    if kind:
        objetos = [o for o in objetos if o["kind"] == kind]
    if table:
        objetos = [o for o in objetos
                   if str(o["table"] or "").casefold() == table.casefold()]
    if name:
        objetos = [o for o in objetos
                   if str(o["name"] or "").casefold() == name.casefold()]

    if len(objetos) == 1:
        objeto = objetos[0]
        if not objeto.get("has_query"):
            raise LiveQueryError(
                f"El objeto '{objeto['name']}' no declara ninguna consulta M "
                "en el motor (source_type="
                f"{objeto.get('source_type')}). No se inventa una.",
                details={"kind": objeto["kind"], "table": objeto["table"],
                         "name": objeto["name"],
                         "source_type": objeto.get("source_type")})
        return {
            "kind": objeto["kind"],
            "table": objeto["table"],
            "name": objeto["name"],
            "source_type": objeto["source_type"],
            "mode": objeto.get("mode"),
            "file": None,
            "source": "live",
            "m": objeto["m"],
            "sha256": sha256(objeto["m"]),
            "read_checked": True,
            "m_engine_checked": False,
            "note": ("Texto leido del motor en vivo (DMV TMSCHEMA). Es la "
                     "definicion cargada en Desktop, que puede diferir del "
                     "TMDL en disco si hay cambios sin guardar."),
            "warnings": leido["warnings"],
        }
    pedido = {"table": table, "name": name, "kind": kind}
    if not objetos:
        raise LiveQueryError(
            "No hay ninguna particion ni expresion M que coincida con "
            f"{ {k: v for k, v in pedido.items() if v} } en el modelo vivo. "
            "En 'candidates' estan las que si existen.",
            details={"requested": pedido,
                     "candidates": _candidatos(leido["objects"])})
    raise LiveQueryError(
        f"La seleccion es ambigua: coinciden {len(objetos)} objetos. Indica "
        "'table' y 'name' (o 'kind') para dejar uno solo.",
        details={"requested": pedido,
                 "matches": [{"kind": o["kind"], "table": o["table"],
                              "name": o["name"]} for o in objetos],
                 "candidates": _candidatos(leido["objects"])})
