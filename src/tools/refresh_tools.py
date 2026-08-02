"""Tool de refresh local del modelo (Fase 5)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session
from powerbi import refresh
from tools._common import guard, guard_mutation


def register(mcp) -> None:
    @mcp.tool()
    def pbi_refresh_model(type: str = "full",
                          tables: Optional[List[str]] = None, request_id: str = "") -> Dict[str, Any]:
        """Refresca el modelo LOCAL abierto en Power BI Desktop (no el Service).

        `type`: full | calculate | clear_values (tambien automatic | data_only).
        `tables`: lista opcional de tablas a refrescar; si se omite, todo el modelo.
        Devuelve estado y duracion. Los errores de credenciales/origen se reportan.
        """
        return guard_mutation(lambda: refresh.refresh_model(get_session(), type, tables))
