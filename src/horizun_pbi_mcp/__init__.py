"""Horizun PBI MCP — servidor MCP para Power BI Desktop local y proyectos `.pbip`.

Todo el codigo vive bajo este unico paquete a proposito. Antes se instalaban en
`site-packages` diez nombres de primer nivel —`config`, `server`, `services`,
`tools`, `utils`, `pbip`, `powerbi`, `reporting`, `logging_config`,
`branding`—, y cuatro de ellos estan entre los mas comunes de todo Python: en
cualquier entorno donde otro paquete o la propia aplicacion del usuario hiciera
`import config`, uno de los dos ganaba y el otro se rompia, en la direccion que
tocara ese dia. Publicado en PyPI, eso deja de ser un problema propio para
serlo de quien lo instale.

El punto de entrada sigue siendo el mismo comando (`horizun-pbi-mcp`); lo que
cambia es que ahora resuelve a `horizun_pbi_mcp.server:main`.
"""
from __future__ import annotations

from horizun_pbi_mcp.branding import VERSION

__all__ = ["VERSION"]
__version__ = VERSION
