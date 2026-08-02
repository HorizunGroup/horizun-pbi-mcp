"""Identidad del producto, en un solo sitio.

El nombre vive aqui y no repartido por el codigo: renombrar un producto no
deberia obligar a buscar cadenas sueltas en veinte archivos.

COMPATIBILIDAD: las 117 tools conservan el prefijo `pbi_`. Renombrarlas romperia
a cualquier cliente ya configurado, y el nombre comercial no es motivo
suficiente para eso.
"""
from __future__ import annotations

import os
from typing import Optional

#: Nombre comercial, para textos dirigidos a personas.
PRODUCT_NAME = "Horizun PBI MCP"
#: Identificador del servidor MCP: es lo que ve el cliente en `serverInfo.name`.
MCP_SERVER_NAME = "horizun-pbi-mcp"
#: Nombre del paquete distribuible.
PACKAGE_NAME = "horizun-pbi-mcp"
#: Version VISIBLE del producto: la que anuncia serverInfo y la que coincide
#: con el tag de Git.
VERSION = "1.0.1"

#: La misma version en el formato que exige PEP 440 para `pyproject.toml`.
#: `1.0.0-rc.2` no es valido ahi; una release estable usa `1.0.1`.
VERSION_PEP440 = "1.0.1"
#: Logger raiz.
LOGGER_NAME = "horizun_pbi_mcp"
#: Prefijo de las tools. NO cambia: es contrato publico.
TOOL_PREFIX = "pbi_"

#: Nombre anterior, que se conserva como alias durante la transicion.
LEGACY_SERVER_NAME = "powerbi-mcp"
LEGACY_ENV_PREFIX = "PBI_MCP_"
ENV_PREFIX = "HORIZUN_PBI_MCP_"


def env(nombre: str, default: Optional[str] = None) -> Optional[str]:
    """Lee una variable de entorno con precedencia HORIZUN_ sobre la antigua.

    `nombre` se da SIN prefijo: `env("LOG_LEVEL")` mira primero
    HORIZUN_PBI_MCP_LOG_LEVEL y despues PBI_MCP_LOG_LEVEL.
    """
    for prefijo in (ENV_PREFIX, LEGACY_ENV_PREFIX):
        valor = os.environ.get(f"{prefijo}{nombre}")
        if valor is not None and valor.strip() != "":
            return valor.strip()
    return default


def env_source(nombre: str) -> Optional[str]:
    """Devuelve QUE variable proporciono el valor. Util para diagnostico."""
    for prefijo in (ENV_PREFIX, LEGACY_ENV_PREFIX):
        clave = f"{prefijo}{nombre}"
        valor = os.environ.get(clave)
        if valor is not None and valor.strip() != "":
            return clave
    return None


def identity() -> dict:
    """Identidad completa, para health check y capabilities."""
    return {
        "product": PRODUCT_NAME,
        "server_name": MCP_SERVER_NAME,
        "package": PACKAGE_NAME,
        "version": VERSION,
        "tool_prefix": TOOL_PREFIX,
        "legacy_aliases": {
            "server_name": LEGACY_SERVER_NAME,
            "env_prefix": LEGACY_ENV_PREFIX,
            "note": ("Se conservan por compatibilidad: las variables "
                     f"{LEGACY_ENV_PREFIX}* siguen funcionando, con menor "
                     f"precedencia que {ENV_PREFIX}*."),
        },
    }
