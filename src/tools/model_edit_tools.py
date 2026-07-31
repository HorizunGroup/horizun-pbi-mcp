"""Tools de edicion de modelo: visibilidad de columnas, direccion de relaciones,
auto fecha/hora. Complementan a las tools de medidas.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from config import get_session
from logging_config import get_logger
from powerbi import model_writer
from powerbi.errors import PowerBIMCPError, ValidationError
from pbip import model_edit
from services import dual_mode
from tools._common import guard, guard_mutation
from utils.validation import validate_object_name

log = get_logger("model_edit_tools")

# La normalizacion del modo y la precondicion viven en services.dual_mode: la
# decision de si `both` es ejecutable es una sola y no puede duplicarse por tool.
_check_mode = dual_mode.assert_mode_is_safely_executable


def _validar_entradas(columns: Any) -> List[Dict[str, str]]:
    """Valida y normaliza la lista de columnas ANTES de escribir nada.

    Devuelve las entradas unicas conservando el orden. Los duplicados exactos
    (misma tabla y misma columna) se descartan en silencio: la operacion es
    idempotente, pedir dos veces lo mismo no es contradictorio.
    """
    if not isinstance(columns, list):
        raise ValidationError(
            "columns debe ser una lista de {'table': ..., 'column': ...}.")

    unicas: List[Dict[str, str]] = []
    vistas: Dict[tuple, int] = {}
    duplicadas: List[Dict[str, Any]] = []

    for idx, item in enumerate(columns):
        if not isinstance(item, dict):
            raise ValidationError(
                f"Entrada {idx}: se esperaba un objeto con 'table' y 'column'.",
                details={"index": idx, "received": type(item).__name__})
        table, column = item.get("table"), item.get("column")
        for nombre, valor in (("table", table), ("column", column)):
            if not isinstance(valor, str) or not valor.strip():
                raise ValidationError(
                    f"Entrada {idx}: falta '{nombre}' o esta vacio.",
                    details={"index": idx, "table": table, "column": column})
        table, column = table.strip(), column.strip()
        validate_object_name(table, "tabla")
        validate_object_name(column, "columna")

        clave = (table, column)
        if clave in vistas:
            duplicadas.append({"index": idx, "table": table, "column": column,
                               "duplica_a": vistas[clave]})
            continue
        vistas[clave] = idx
        unicas.append({"table": table, "column": column})

    return unicas, duplicadas


_dual = dual_mode.run_dual


class BulkPartialError(PowerBIMCPError):
    """El lote quedo aplicado en un solo destino y la compensacion no fue limpia.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de esta
    fase. `guard()` la serializa como cualquier error de dominio.
    """

    code = "bulk_partially_applied"


class BulkApplyFailedError(PowerBIMCPError):
    """El lote fallo y la compensacion dejo TODO como estaba.

    Se distingue de `BulkPartialError` a proposito: decir "parcial" cuando la
    restauracion fue completa induce a pensar que hay algo que arreglar a mano,
    y no lo hay. Aqui `applied_to` es siempre "ninguno".
    """

    code = "bulk_apply_failed"


def _reconstruir_resultados(solicitadas, por_columna, m, duplicadas,
                            consistente=None) -> Dict[str, Any]:
    """Respuesta compatible: una entrada por columna SOLICITADA, en orden."""
    resultados = []
    for item in solicitadas:
        clave = (str(item.get("table", "")).strip(),
                 str(item.get("column", "")).strip())
        resultados.append(por_columna.get(clave, {"ok": True, "mode": m}))
    salida: Dict[str, Any] = {"mode": m, "count": len(solicitadas),
                              "results": resultados}
    if duplicadas:
        salida["duplicates_ignored"] = duplicadas
    if consistente is not None:
        salida["consistent"] = consistente
    return salida


def _apply_both_compensated(session, unicas, solicitadas, duplicadas,
                            hidden: bool) -> Dict[str, Any]:
    """Coordinador compensado disco -> memoria. MECANISMO INTERNO.

    NO es accesible desde la tool publica: `assert_mode_is_safely_executable`
    rechaza `mode='both'` antes de llegar aqui, porque los dos destinos exigen
    estados de Power BI Desktop incompatibles.

    Se conserva como defensa —y con pruebas unitarias directas— porque la Fase
    1B tendra que decidir como coordinar los dos destinos, y este es el
    comportamiento correcto cuando esa coordinacion exista: escribir el disco
    con journal, aplicar en vivo y, si lo vivo falla, compensar el disco.
    """
    active = session.require_active_pbip()

    # Validacion previa de AMBOS destinos: si el modelo en vivo no admite el
    # lote, se descubre antes de escribir en disco y no hay nada que compensar.
    model_writer.validate_columns_live(session, unicas)
    model_edit.plan_columns_hidden_pbip(active, unicas, hidden)

    por_columna: Dict[tuple, Dict[str, Any]] = {
        (e["table"], e["column"]): {"ok": True, "mode": "both"} for e in unicas}

    pbip_res = model_edit.set_columns_hidden_pbip_bulk(active, unicas, hidden)
    for r in pbip_res["results"]:
        por_columna[(r["table"], r["column"])]["pbip"] = r

    try:
        live_res = model_writer.set_columns_hidden_bulk(session, unicas, hidden)
    except Exception as exc:  # noqa: BLE001
        # Se captura TODO, no solo PowerBIMCPError: una excepcion cruda del
        # motor .NET que se escapara aqui dejaria el disco modificado y el
        # modelo en vivo sin cambiar, que es justo lo que no puede pasar.
        detalle = (exc.to_dict() if isinstance(exc, PowerBIMCPError)
                   else {"error": type(exc).__name__, "message": str(exc)})
        txn = pbip_res.get("txn_object")
        compensacion = (txn.compensate(
            cause=f"fallo al aplicar en vivo: {detalle.get('error')}")
            if txn is not None else None)

        if compensacion is not None and not compensacion["clean"]:
            raise BulkPartialError(
                "El cambio se escribio en los archivos TMDL, fallo al aplicarse "
                "en el modelo en vivo, y la restauracion del disco NO quedo "
                "limpia. Requiere intervencion manual: el journal contiene los "
                "originales.",
                details={"live_error": detalle, "compensation": compensacion,
                         "applied_to": "solo_disco_parcialmente",
                         "journal": compensacion["journal"]}) from exc

        raise BulkApplyFailedError(
            "No se aplico el cambio: fallo en el modelo en vivo y los archivos "
            "TMDL se restauraron por completo a su estado original.",
            details={"live_error": detalle, "compensation": compensacion,
                     "applied_to": "ninguno"}) from exc

    for r in live_res["results"]:
        por_columna[(r["table"], r["column"])]["live"] = r
    return _reconstruir_resultados(solicitadas, por_columna, "both", duplicadas,
                                   consistente=True)


def hide_columns_service(session, columns: Any, hidden: bool,
                         mode: str) -> Dict[str, Any]:
    """Logica interna de `pbi_hide_columns`. SIN decorar.

    Una tool nunca debe llamar a otra tool decorada: `guard()` convertiria los
    errores en datos, el bucle continuaria y el lote devolveria `ok:true` con
    fallos escondidos dentro. Aqui los errores son excepciones y detienen todo.

    `mode='both'` se rechaza en la precondicion, ANTES de cualquier efecto.
    """
    # Lo primero: antes de conectar a TOM, de validar contra el motor, de crear
    # journal, de leer para planificar o de tocar un archivo.
    m = _check_mode(mode)

    unicas, duplicadas = _validar_entradas(columns)
    solicitadas = list(columns) if isinstance(columns, list) else []

    # Lista vacia: se conserva el comportamiento previo (no es un error).
    if not unicas:
        return {"mode": m, "count": len(solicitadas), "results": [],
                "duplicates_ignored": duplicadas}

    por_columna: Dict[tuple, Dict[str, Any]] = {
        (e["table"], e["column"]): {"ok": True, "mode": m} for e in unicas}

    if m == dual_mode.PBIP:
        active = session.require_active_pbip()
        res = model_edit.set_columns_hidden_pbip_bulk(active, unicas, hidden)
        for r in res["results"]:
            por_columna[(r["table"], r["column"])]["pbip"] = r
    else:
        res = model_writer.set_columns_hidden_bulk(session, unicas, hidden)
        for r in res["results"]:
            por_columna[(r["table"], r["column"])]["live"] = r

    return _reconstruir_resultados(solicitadas, por_columna, m, duplicadas)


def register(mcp) -> None:
    @mcp.tool()
    def pbi_set_column_visibility(table: str, column: str, hidden: bool = True,
                                  mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Oculta o muestra una columna del modelo (p.ej. ocultar columnas de ID).

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip'.
        """
        def _impl():
            m = _check_mode(mode)
            session = get_session()
            return _dual(
                m,
                lambda: model_writer.set_column_hidden(session, table, column, hidden),
                lambda: model_edit.set_column_hidden_pbip(
                    session.require_active_pbip(), table, column, hidden),
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_hide_columns(columns: List[Dict[str, str]], hidden: bool = True,
                         mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Oculta/muestra VARIAS columnas como un solo lote.

        `columns`: lista de {"table": ..., "column": ...}.

        Valida todas las entradas antes de escribir: si alguna tabla o columna
        no existe, no se modifica nada y el error indica el indice. Los archivos
        TMDL se escriben en una sola transaccion y el modelo en vivo con un solo
        SaveChanges. `count` es el numero de entradas SOLICITADAS (incluidos
        duplicados); `results` trae una entrada por cada una, en el mismo orden.

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip'.
        """
        return guard_mutation(lambda: hide_columns_service(
            get_session(), columns, hidden, mode))

    @mcp.tool()
    def pbi_set_relationship_direction(from_table: str, to_table: str,
                                       direction: str = "single",
                                       mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Cambia el filtro cruzado de una relacion.

        `direction`: 'single' (una direccion, recomendado) o 'both' (bidireccional).
        OJO: cambiar a 'single' puede alterar totales que dependian de la bidireccional;
        verifica el informe despues.

        No confundir `direction='both'` (bidireccional, valido) con `mode='both'`,
        que esta temporalmente deshabilitado bajo la politica estricta: 'live'
        necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado, asi que
        una sola llamada aplicaria solo uno de los dos destinos.
        """
        def _impl():
            m = _check_mode(mode)
            session = get_session()
            return _dual(
                m,
                lambda: model_writer.set_relationship_crossfilter(
                    session, from_table, to_table, direction),
                lambda: model_edit.set_relationship_direction_pbip(
                    session.require_active_pbip(), from_table, to_table, direction),
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_disable_auto_date_time(enabled: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Activa/desactiva 'Auto fecha y hora' (solo modo pbip).

        Desactivarlo aligera el modelo: al reabrir el .pbip, Power BI elimina las
        tablas de fecha automaticas (LocalDateTable_*). Requiere proyecto .pbip activo.
        """
        def _impl():
            session = get_session()
            return model_edit.set_auto_datetime_pbip(
                session.require_active_pbip(), enabled)
        return guard_mutation(_impl)
