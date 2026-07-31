"""Lectura de metadatos del modelo tabular activo via TOM.

TOM (Tabular Object Model) da acceso rico a tablas, columnas, medidas,
relaciones, jerarquias y roles/RLS del modelo abierto en Power BI Desktop.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from config import ActiveModel, Session
from logging_config import get_logger
from powerbi.clr_bootstrap import load_tom
from powerbi.errors import ConnectionFailedError, TableNotFoundError

log = get_logger("model_reader")


def _s(value: Any) -> Optional[str]:
    """Convierte enums/strings .NET a str Python (o None)."""
    if value is None:
        return None
    return str(value)


@contextmanager
def connect(model: ActiveModel) -> Iterator[Tuple[Any, Any, Any]]:
    """Context manager que entrega (server, database, model_obj) via TOM."""
    TOM = load_tom()
    server = TOM.Server()
    try:
        server.Connect(model.connection_string)
    except Exception as exc:  # noqa: BLE001
        msg = getattr(exc, "Message", None) or str(exc)
        raise ConnectionFailedError(f"TOM no pudo conectar: {msg}") from exc
    try:
        db = None
        if model.catalog:
            db = server.Databases.FindByName(model.catalog)
        if db is None and server.Databases.Count > 0:
            db = server.Databases[0]
        if db is None:
            raise ConnectionFailedError("No hay ninguna base de datos en la instancia.")
        yield server, db, db.Model
    finally:
        try:
            server.Disconnect()
        except Exception:  # pragma: no cover
            pass


def read_columns(table) -> List[Dict[str, Any]]:
    cols = []
    for c in table.Columns:
        ctype = _s(getattr(c, "Type", None))
        cols.append({
            "name": c.Name,
            "data_type": _s(c.DataType),
            "column_type": ctype,  # Data / Calculated / RowNumber / ...
            "is_hidden": bool(c.IsHidden),
            "is_key": bool(getattr(c, "IsKey", False)),
            "summarize_by": _s(getattr(c, "SummarizeBy", None)),
            "display_folder": getattr(c, "DisplayFolder", None) or None,
            "data_category": getattr(c, "DataCategory", None) or None,
            "expression": getattr(c, "Expression", None) if ctype == "Calculated" else None,
        })
    return cols


def read_measures(table) -> List[Dict[str, Any]]:
    measures = []
    for m in table.Measures:
        measures.append({
            "name": m.Name,
            "table": table.Name,
            "expression": m.Expression,
            "format_string": m.FormatString or None,
            "display_folder": m.DisplayFolder or None,
            "description": m.Description or None,
            "is_hidden": bool(m.IsHidden),
        })
    return measures


def read_hierarchies(table) -> List[Dict[str, Any]]:
    hierarchies = []
    for h in getattr(table, "Hierarchies", []):
        levels = [{"name": lvl.Name, "column": getattr(lvl.Column, "Name", None)}
                  for lvl in h.Levels]
        hierarchies.append({"name": h.Name, "table": table.Name, "levels": levels})
    return hierarchies


def read_model(session: Session) -> Dict[str, Any]:
    """Lee todo el modelo activo y devuelve una estructura completa."""
    model = session.require_active_model()
    with connect(model) as (server, db, mdl):
        tables: List[Dict[str, Any]] = []
        all_measures: List[Dict[str, Any]] = []
        all_hierarchies: List[Dict[str, Any]] = []
        for t in mdl.Tables:
            cols = read_columns(t)
            measures = read_measures(t)
            hierarchies = read_hierarchies(t)
            all_measures.extend(measures)
            all_hierarchies.extend(hierarchies)
            tables.append({
                "name": t.Name,
                "is_hidden": bool(t.IsHidden),
                "description": t.Description or None,
                "data_category": getattr(t, "DataCategory", None) or None,
                "column_count": len(cols),
                "measure_count": len(measures),
                "columns": cols,
                "is_date_table": (getattr(t, "DataCategory", None) == "Time"),
            })

        relationships = []
        for r in mdl.Relationships:
            relationships.append({
                "name": _s(getattr(r, "Name", None)),
                "from_table": _s(getattr(r.FromTable, "Name", None)),
                "from_column": _s(getattr(r.FromColumn, "Name", None)),
                "to_table": _s(getattr(r.ToTable, "Name", None)),
                "to_column": _s(getattr(r.ToColumn, "Name", None)),
                "from_cardinality": _s(getattr(r, "FromCardinality", None)),
                "to_cardinality": _s(getattr(r, "ToCardinality", None)),
                "cross_filtering": _s(getattr(r, "CrossFilteringBehavior", None)),
                "is_active": bool(getattr(r, "IsActive", True)),
                "security_filtering": _s(getattr(r, "SecurityFilteringBehavior", None)),
            })

        roles = []
        for role in mdl.Roles:
            perms = []
            for tp in role.TablePermissions:
                fexpr = tp.FilterExpression
                if fexpr:
                    perms.append({"table": _s(getattr(tp.Table, "Name", None)),
                                  "filter": fexpr})
            roles.append({
                "name": role.Name,
                "model_permission": _s(getattr(role, "ModelPermission", None)),
                "table_permissions": perms,
            })

        info = {
            "name": _s(getattr(mdl, "Name", None)),
            "culture": _s(getattr(mdl, "Culture", None)),
            "database_name": _s(getattr(db, "Name", None)),
            "compatibility_level": getattr(db, "CompatibilityLevel", None),
        }

    return {
        "model": info,
        "tables": tables,
        "measures": all_measures,
        "relationships": relationships,
        "hierarchies": all_hierarchies,
        "roles": roles,
    }


def find_table(model_data: Dict[str, Any], table_name: str) -> Dict[str, Any]:
    for t in model_data["tables"]:
        if t["name"] == table_name:
            return t
    raise TableNotFoundError(
        f"La tabla '{table_name}' no existe en el modelo.",
        details={"available": [t["name"] for t in model_data["tables"]]},
    )
