"""Read-only discovery, including tools excluded by the active profile."""
from __future__ import annotations

from typing import Any, Dict

from horizun_pbi_mcp.services.tool_search import find_tools
from horizun_pbi_mcp.tool_profiles import FULL_HINT, LOCAL_DESCRIPTIONS
from horizun_pbi_mcp.tools._common import guard


def register(mcp, catalog: dict[str, str], profile: str) -> None:
    @mcp.tool()
    def pbi_find_tool(query: str) -> Dict[str, Any]:
        """Busca herramientas por palabras en español o inglés, incluso fuera del perfil activo.

        Devuelve nombre, propósito y disponibilidad; para las ocultas indica cómo activar el perfil completo.
        """
        return guard(lambda: find_tools(
            catalog, query, set(LOCAL_DESCRIPTIONS) if profile == "local" else set(catalog),
            profile, FULL_HINT))
