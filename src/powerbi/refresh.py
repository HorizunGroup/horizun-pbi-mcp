"""Refresh local del modelo activo (Power BI Desktop) via TOM.

IMPORTANTE: esto refresca el modelo LOCAL abierto en Power BI Desktop, no el
Power BI Service. Requiere que las credenciales de origen esten configuradas en
Desktop; si no, el motor devolvera un error de credenciales/origen.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from config import Session
from logging_config import get_logger
from powerbi.clr_bootstrap import load_tom
from powerbi.errors import RefreshError, TableNotFoundError, ValidationError
from powerbi.model_reader import connect

log = get_logger("refresh")

_TYPE_ALIASES = {
    "full": "Full",
    "calculate": "Calculate",
    "clear_values": "ClearValues",
    "clearvalues": "ClearValues",
    "automatic": "Automatic",
    "data_only": "DataOnly",
    "dataonly": "DataOnly",
}


def refresh_model(
    session: Session,
    refresh_type: str = "full",
    tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    model = session.require_active_model()
    TOM = load_tom()
    key = str(refresh_type).lower().strip()
    if key not in _TYPE_ALIASES:
        raise ValidationError(
            f"Tipo de refresh no valido: '{refresh_type}'. "
            f"Usa uno de: {sorted(set(_TYPE_ALIASES))}."
        )
    rt = getattr(TOM.RefreshType, _TYPE_ALIASES[key])

    start = time.perf_counter()
    try:
        with connect(model) as (_server, _db, mdl):
            refreshed: List[str] = []
            if tables:
                for tn in tables:
                    tobj = None
                    for t in mdl.Tables:
                        if t.Name == tn:
                            tobj = t
                            break
                    if tobj is None:
                        raise TableNotFoundError(
                            f"La tabla '{tn}' no existe en el modelo.")
                    tobj.RequestRefresh(rt)
                    refreshed.append(tn)
            else:
                mdl.RequestRefresh(rt)
                refreshed = ["<todo el modelo>"]
            mdl.SaveChanges()  # dispara el refresh de forma sincrona
    except (TableNotFoundError, ValidationError):
        raise
    except Exception as exc:  # noqa: BLE001
        msg = getattr(exc, "Message", None) or str(exc)
        raise RefreshError(
            f"Fallo el refresh local: {msg}",
            details={"refresh_type": _TYPE_ALIASES[key], "tables": tables},
        ) from exc

    duration_s = round(time.perf_counter() - start, 2)
    return {
        "status": "ok",
        "refresh_type": _TYPE_ALIASES[key],
        "tables": refreshed,
        "duration_s": duration_s,
        "scope": "local (Power BI Desktop)",
        "note": "Refresh local aplicado. Guarda en Power BI Desktop para persistirlo.",
    }
