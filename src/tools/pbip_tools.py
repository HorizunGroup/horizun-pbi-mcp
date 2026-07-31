"""Tools de proyecto Power BI Project .pbip (Fase 6)."""
from __future__ import annotations

from typing import Any, Dict

from config import get_session
from pbip import backup, project_locator
from tools._common import guard


def register(mcp) -> None:
    @mcp.tool()
    def pbi_open_pbip_project(path: str) -> Dict[str, Any]:
        """Abre un proyecto .pbip y lo marca como proyecto activo.

        Detecta carpetas .SemanticModel (TMDL) y .Report (PBIR) y devuelve un
        resumen con advertencias (p.ej. si el informe no usa PBIR).
        `path`: ruta al archivo .pbip o a su carpeta.
        """
        return guard(lambda: project_locator.open_project(get_session(), path))

    @mcp.tool()
    def pbi_validate_pbip_project() -> Dict[str, Any]:
        """Valida a fondo el proyecto .pbip activo (estructura, PBIR, TMDL)."""
        return guard(lambda: project_locator.validate_project(get_session()))

    @mcp.tool()
    def pbi_backup_pbip_project(mode: str = "folder",
                                scope: str = "both") -> Dict[str, Any]:
        """Crea un backup con timestamp del proyecto .pbip activo.

        `mode`: folder | zip. `scope`: report | model | both. Devuelve la ruta.
        """
        return guard(lambda: backup.backup_project(get_session(), mode, scope))
