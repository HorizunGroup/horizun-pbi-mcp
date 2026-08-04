"""Refresh local del modelo activo (Power BI Desktop) via TOM.

IMPORTANTE: esto refresca el modelo LOCAL abierto en Power BI Desktop, no el
Power BI Service. Requiere que las credenciales de origen esten configuradas en
Desktop; si no, el motor devolvera un error de credenciales/origen.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.clr_bootstrap import load_tom
from horizun_pbi_mcp.powerbi.errors import RefreshError, TableNotFoundError, ValidationError
from horizun_pbi_mcp.powerbi.model_reader import connect, lease_active_model

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
    key = str(refresh_type).lower().strip()
    if key not in _TYPE_ALIASES:
        raise ValidationError(
            f"Tipo de refresh no valido: '{refresh_type}'. "
            f"Usa uno de: {sorted(set(_TYPE_ALIASES))}."
        )

    # Una lista vacia no es lo mismo que omitir `tables`. Interpretarla como
    # falso hacia que una peticion sin objetivos refrescara TODO el modelo.
    if tables is not None and not tables:
        raise ValidationError(
            "tables no puede ser una lista vacia. Omite el parametro para "
            "refrescar todo el modelo.",
            details={"parameter": "tables"},
        )
    if tables is not None:
        for index, name in enumerate(tables):
            if not isinstance(name, str) or not name.strip():
                raise ValidationError(
                    f"tables[{index}] debe ser un nombre de tabla no vacio.",
                    details={"parameter": "tables", "index": index},
                )

    # Las entradas invalidas se rechazan antes de descubrir/conectar al motor.
    # El lease queda por fuera del traductor de errores de refresh: una sesion
    # obsoleta debe seguir saliendo como `stale_session`, no disfrazada de un
    # fallo de credenciales/origen.
    with lease_active_model(session) as model:
        TOM = load_tom()
        rt = getattr(TOM.RefreshType, _TYPE_ALIASES[key])
        start = time.perf_counter()
        try:
            with connect(model) as (_server, _db, mdl):
                refreshed: List[str] = []
                if tables is not None:
                    by_name = {str(t.Name).casefold(): t for t in mdl.Tables}
                    targets = []
                    seen = set()
                    # Resolver y validar TODO antes de llamar RequestRefresh.
                    # Esa llamada deja una operacion pendiente en el objeto
                    # TOM aunque todavia no se haya ejecutado SaveChanges.
                    for tn in tables:
                        key_name = tn.strip().casefold()
                        tobj = by_name.get(key_name)
                        if tobj is None:
                            raise TableNotFoundError(
                                f"La tabla '{tn}' no existe en el modelo.",
                                details={
                                    "available": [t.Name for t in mdl.Tables]},
                            )
                        if key_name not in seen:
                            targets.append(tobj)
                            seen.add(key_name)
                    for tobj in targets:
                        tobj.RequestRefresh(rt)
                        refreshed.append(tobj.Name)
                else:
                    mdl.RequestRefresh(rt)
                    refreshed = ["<todo el modelo>"]
                mdl.SaveChanges()  # dispara el refresh de forma sincrona
        except (TableNotFoundError, ValidationError):
            raise
        except Exception as exc:  # noqa: BLE001
            from horizun_pbi_mcp.services import redaction

            msg = getattr(exc, "Message", None) or str(exc)
            raise RefreshError(
                f"Fallo el refresh local: {redaction.texto(msg)}",
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
