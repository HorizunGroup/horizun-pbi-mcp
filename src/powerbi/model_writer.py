"""Creacion/edicion/borrado de medidas en el modelo activo via TOM (en vivo).

Los cambios se aplican al modelo en memoria de Power BI Desktop (igual que hace
Tabular Editor). Quedan persistidos en el .pbix/.pbip solo cuando el usuario
guarda en Power BI Desktop (Ctrl+S).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import Session
from logging_config import get_logger
from powerbi.clr_bootstrap import load_tom
from powerbi.errors import (MeasureExistsError, MeasureNotFoundError,
                            PowerBIMCPError, TableNotFoundError)
from powerbi.model_reader import connect
from utils.validation import validate_measure_expression, validate_object_name

log = get_logger("model_writer")


class LiveWriteError(PowerBIMCPError):
    """El motor rechazo una escritura en el modelo en vivo.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de esta
    fase. Envuelve las excepciones .NET crudas de TOM para que sean
    distinguibles y compensables.
    """

    code = "live_write_failed"

LIVE_NOTE = (
    "Cambio aplicado en el modelo en memoria de Power BI Desktop. Para que quede "
    "guardado en el archivo, usa Ctrl+S en Power BI Desktop."
)


def _find_table(mdl, table_name: str):
    for t in mdl.Tables:
        if t.Name == table_name:
            return t
    available = [t.Name for t in mdl.Tables]
    raise TableNotFoundError(
        f"La tabla '{table_name}' no existe en el modelo.",
        details={"available": available},
    )


def _find_measure_anywhere(mdl, name: str):
    for t in mdl.Tables:
        m = t.Measures.Find(name)
        if m is not None:
            return t, m
    return None, None


def _find_measure_in_table(mdl, table_obj, name: str):
    """Localiza una medida solo dentro de la tabla solicitada.

    Los nombres de medida son globales en TOM, pero ``table`` sigue siendo
    parte del contrato de update/delete. Buscar globalmente convertia un
    error de tabla en una escritura sobre otra tabla.
    """
    measure = table_obj.Measures.Find(name)
    if measure is not None:
        return measure

    owner, _other = _find_measure_anywhere(mdl, name)
    details = {
        "table": table_obj.Name,
        "available": [m.Name for m in table_obj.Measures],
    }
    if owner is not None:
        details["existing_table"] = owner.Name
    raise MeasureNotFoundError(
        f"La medida '{name}' no existe en la tabla '{table_obj.Name}'.",
        details=details,
    )


def _snapshot(measure) -> Dict[str, Any]:
    return {
        "name": measure.Name,
        "expression": measure.Expression,
        "format_string": measure.FormatString or None,
        "display_folder": measure.DisplayFolder or None,
        "description": measure.Description or None,
    }


def create_measure(
    session: Session,
    table: str,
    name: str,
    expression: str,
    format_string: Optional[str] = None,
    description: Optional[str] = None,
    display_folder: Optional[str] = None,
    overwrite: bool = False,
    data_category: Optional[str] = None,
) -> Dict[str, Any]:
    name = validate_object_name(name, "medida")
    expression = validate_measure_expression(expression)
    model = session.require_active_model()
    with connect(model) as (_server, db, mdl):
        target = _find_table(mdl, table)
        owner, existing = _find_measure_anywhere(mdl, name)
        before = None
        action = "created"
        if existing is not None:
            if not overwrite:
                raise MeasureExistsError(
                    f"La medida '{name}' ya existe en la tabla '{owner.Name}'. "
                    "Usa overwrite=true para reemplazarla.",
                    details={"table": owner.Name},
                )
            before = _snapshot(existing)
            existing.Expression = expression
            if format_string is not None:
                existing.FormatString = format_string
            if description is not None:
                existing.Description = description
            if display_folder is not None:
                existing.DisplayFolder = display_folder
            if data_category is not None:
                existing.DataCategory = data_category
            measure = existing
            action = "updated"
        else:
            TOM = load_tom()
            measure = TOM.Measure()
            measure.Name = name
            measure.Expression = expression
            if format_string:
                measure.FormatString = format_string
            if description:
                measure.Description = description
            if display_folder:
                measure.DisplayFolder = display_folder
            if data_category:
                measure.DataCategory = data_category
            target.Measures.Add(measure)
        mdl.SaveChanges()
        after = _snapshot(measure)
    return {"action": action, "table": table, "before": before, "after": after, "note": LIVE_NOTE}


def update_measure(
    session: Session,
    table: str,
    name: str,
    expression: Optional[str] = None,
    format_string: Optional[str] = None,
    description: Optional[str] = None,
    display_folder: Optional[str] = None,
) -> Dict[str, Any]:
    name = validate_object_name(name, "medida")
    if expression is not None:
        expression = validate_measure_expression(expression)
    model = session.require_active_model()
    with connect(model) as (_server, db, mdl):
        owner = _find_table(mdl, table)
        measure = _find_measure_in_table(mdl, owner, name)
        before = _snapshot(measure)
        if expression is not None:
            measure.Expression = expression
        if format_string is not None:
            measure.FormatString = format_string
        if description is not None:
            measure.Description = description
        if display_folder is not None:
            measure.DisplayFolder = display_folder
        mdl.SaveChanges()
        after = _snapshot(measure)
    return {"action": "updated", "table": owner.Name, "before": before, "after": after,
            "note": LIVE_NOTE}


def set_column_hidden(session: Session, table: str, column: str,
                      hidden: bool = True) -> Dict[str, Any]:
    model = session.require_active_model()
    with connect(model) as (_server, _db, mdl):
        t = _find_table(mdl, table)
        col = None
        for c in t.Columns:
            if c.Name == column:
                col = c
                break
        if col is None:
            raise TableNotFoundError(
                f"La columna '{column}' no existe en la tabla '{table}'.",
                details={"available": [c.Name for c in t.Columns]})
        before = bool(col.IsHidden)
        col.IsHidden = bool(hidden)
        mdl.SaveChanges()
    return {"table": table, "column": column, "before_hidden": before,
            "after_hidden": bool(hidden), "note": LIVE_NOTE}


def _find_column(mdl, table_name: str, column_name: str, index: int):
    """Localiza una columna, con un error que identifica la entrada del lote."""
    try:
        t = _find_table(mdl, table_name)
    except TableNotFoundError as exc:
        raise TableNotFoundError(
            f"Entrada {index} ({table_name}[{column_name}]): {exc.message}",
            details={"index": index, "table": table_name, "column": column_name,
                     **exc.details}) from exc
    for c in t.Columns:
        if c.Name == column_name:
            return c
    raise TableNotFoundError(
        f"Entrada {index} ({table_name}[{column_name}]): la columna "
        f"'{column_name}' no existe en la tabla '{table_name}'.",
        details={"index": index, "table": table_name, "column": column_name,
                 "available": [c.Name for c in t.Columns]})


def validate_columns_live(session: Session, entries: List[Dict[str, str]]) -> None:
    """Comprueba que todas las tablas y columnas existen. NO modifica nada.

    Se usa antes de un `mode='both'`: si el modelo en vivo no admite el lote,
    conviene saberlo ANTES de escribir en disco, para no tener que compensar.
    """
    if not entries:
        return
    model = session.require_active_model()
    with connect(model) as (_server, _db, mdl):
        for idx, e in enumerate(entries):
            _find_column(mdl, e["table"], e["column"], idx)


def set_columns_hidden_bulk(session: Session, entries: List[Dict[str, str]],
                            hidden: bool = True) -> Dict[str, Any]:
    """Cambia la visibilidad de VARIAS columnas con UN SOLO SaveChanges.

    Una sola conexion TOM para todo el lote. Primero se validan TODAS las
    tablas y columnas; si alguna no existe, se lanza ANTES de modificar nada y
    sin llamar a SaveChanges, asi que no se persiste ningun cambio.

    ATENCION AL ALCANCE DE LA GARANTIA: TOM aplica los cambios sobre el modelo
    en memoria y `SaveChanges()` los envia en una sola operacion. Eso NO es una
    transaccion distribuida: si el motor rechaza el lote, los objetos en
    memoria pueden quedar modificados hasta que Power BI Desktop se recargue.
    Lo que si se garantiza es que no hay escrituras parciales por nuestra parte
    (un unico SaveChanges) y que una validacion fallida no persiste nada.
    """
    if not entries:
        return {"changed": 0, "results": [], "save_changes_calls": 0}

    model = session.require_active_model()
    with connect(model) as (_server, _db, mdl):
        # --- 1. Validar TODAS las entradas antes de tocar nada -------------
        objetivos = []
        for idx, e in enumerate(entries):
            col = _find_column(mdl, e["table"], e["column"], idx)
            objetivos.append((idx, e, col))

        # --- 2. Capturar el estado previo y modificar en memoria ----------
        resultados = []
        for idx, e, col in objetivos:
            before = bool(col.IsHidden)
            col.IsHidden = bool(hidden)
            resultados.append({
                "table": e["table"], "column": e["column"],
                "before_hidden": before, "after_hidden": bool(hidden),
                "changed": before != bool(hidden),
            })

        # --- 3. Un unico SaveChanges para todo el lote ---------------------
        try:
            mdl.SaveChanges()
        except Exception as exc:  # noqa: BLE001
            # El motor puede lanzar una excepcion .NET cruda. Se envuelve como
            # error de dominio para que quien coordina un modo 'both' pueda
            # distinguirla y compensar lo ya escrito en disco.
            msg = getattr(exc, "Message", None) or str(exc)
            raise LiveWriteError(
                f"El motor rechazo el lote al guardar: {msg}",
                details={"columns": len(entries),
                         "original_type": type(exc).__name__}) from exc

    return {"changed": sum(1 for r in resultados if r["changed"]),
            "results": resultados, "save_changes_calls": 1, "note": LIVE_NOTE}


def set_relationship_crossfilter(session: Session, from_table: str, to_table: str,
                                 direction: str = "single") -> Dict[str, Any]:
    """direction: 'single' (OneDirection) o 'both' (BothDirections)."""
    direction = direction.lower()
    if direction not in ("single", "both"):
        from powerbi.errors import ValidationError
        raise ValidationError("direction debe ser 'single' o 'both'.")
    TOM = load_tom()
    target = (TOM.CrossFilteringBehavior.BothDirections if direction == "both"
              else TOM.CrossFilteringBehavior.OneDirection)
    model = session.require_active_model()
    wanted = {from_table, to_table}
    matched = 0
    changes = []
    with connect(model) as (_server, _db, mdl):
        for r in mdl.Relationships:
            ft = getattr(r.FromTable, "Name", None)
            tt = getattr(r.ToTable, "Name", None)
            if {ft, tt} == wanted:
                matched += 1
                before = str(r.CrossFilteringBehavior)
                r.CrossFilteringBehavior = target
                changes.append({"from": ft, "to": tt, "before": before,
                                "after": str(target)})
        if matched == 0:
            raise TableNotFoundError(
                f"No se encontro relacion entre '{from_table}' y '{to_table}'.")
        mdl.SaveChanges()
    return {"matched": matched, "direction": direction, "changes": changes,
            "note": LIVE_NOTE}


def delete_measure(session: Session, table: str, name: str) -> Dict[str, Any]:
    model = session.require_active_model()
    with connect(model) as (_server, db, mdl):
        owner = _find_table(mdl, table)
        measure = _find_measure_in_table(mdl, owner, name)
        before = _snapshot(measure)
        owner.Measures.Remove(measure)
        mdl.SaveChanges()
    return {"action": "deleted", "table": owner.Name, "before": before, "note": LIVE_NOTE}
