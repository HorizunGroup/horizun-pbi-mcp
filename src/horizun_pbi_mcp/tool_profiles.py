"""Opt-in tool registration for small local models.

The thirteen local tools cover an end-to-end file workflow: create/open a PBIP,
import a file, inspect tables AND columns, create relationships, measures and a
page, validate TMDL and the project, open Desktop, refresh and query DAX. Refresh
is essential: opening a new PBIP does not load its data. Discovery is the
thirteenth tool and explains how to reach the full catalog. Editing existing layouts,
exports and recovery remain discoverable in full rather than crowding this set.

Only registration/presentation changes. Original handlers, argument validation,
defaults, guards, transactions and risk annotations remain authoritative. Hidden
tools are catalogued without registering a callable. Full is the unchanged
registration path, plus the additive discovery tool; profiles are fixed at boot.
"""
from __future__ import annotations

import inspect
import os
from functools import wraps
from typing import Any

from horizun_pbi_mcp.services.compact_response import compact_response

PROFILE_ENV = "HORIZUN_PBI_MCP_PROFILE"
FULL_HINT = "Set HORIZUN_PBI_MCP_PROFILE=full (or remove it) and restart the MCP server/client."

LOCAL_DESCRIPTIONS = {
    "pbi_open_pbip_project": "Activa un proyecto existente: path al .pbip o su carpeta; no abre Desktop.",
    "pbi_create_pbip_project": "Crea y activa un PBIP vacío en out_dir/name; conserva overwrite=false para no reemplazar otro proyecto.",
    "pbi_add_table_from_file": "Importa CSV, Excel, JSON o HTML a TMDL con Desktop cerrado; path debe existir. dry_run=true ensaya, sheet elige hoja y text_columns fuerza texto.",
    "pbi_list_tables": "Lista tablas y columnas reales; usa source='pbip' offline o 'live' con Desktop abierto. detail='summary' resume; tables filtra por nombres.",
    "pbi_create_relationship": "Crea una relación entre from_table/from_column y to_table/to_column en TMDL con Desktop cerrado; defecto muchos-a-uno, filtro unidireccional. overwrite=false evita reemplazos.",
    "pbi_create_measure": "Crea una medida DAX en table; usa mode='pbip' con Desktop cerrado o 'live' abierto (defecto). mode='both' está bloqueado; overwrite=false evita reemplazos.",
    "pbi_create_page_from_spec": "Crea página y visuales con Desktop cerrado; spec={page_name,layout:'grid',visuals:[{type:'card',title,fields:{Values:['Tabla[Medida]']}}]}. Usa nombres reales; conserva las advertencias de plantillas.",
    "pbi_validate_pbip_project": "Valida el proyecto activo, TMDL, PBIR y referencias de campos; revisa errores y cobertura.",
    "pbi_validate_tmdl": "Valida TMDL sin abrir Desktop; path vacío usa el proyecto activo. use_tom=false hace solo lint; parse_checked indica si se comprobó el parseo.",
    "pbi_open_in_desktop": "Abre y selecciona el PBIP activo o path en Desktop; no carga datos. Después usa pbi_refresh_model antes de consultar valores DAX.",
    "pbi_refresh_model": "Carga datos en el modelo abierto; exige confirm=true y puede descartar cambios en memoria. Revisa warnings y rows_by_table; no publica en el Service.",
    "pbi_run_dax": "Consulta DAX de solo lectura en Desktop (EVALUATE o DEFINE...EVALUATE). max_rows limita filas; export=true guarda el resultado previo a la compactación local.",
    "pbi_find_tool": "Busca herramientas en español o inglés, incluidas las ocultas; indica si están activas y cómo acceder al perfil completo.",
}


def resolve_profile(profile: str | None = None) -> str:
    value = (os.environ.get(PROFILE_ENV, "full") if profile is None else profile).strip().lower()
    if not value:
        return "full"
    if value not in {"full", "local"}:
        raise ValueError(f"Unknown {PROFILE_ENV}={value!r}; use 'full' or 'local'.")
    return value


def minimal_schema(schema: Any) -> Any:
    """Remove display titles only, never parameters named 'title' or constraints."""
    if isinstance(schema, list):
        return [minimal_schema(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: ({name: minimal_schema(value) for name, value in values.items()}
              if key in {"properties", "$defs", "definitions", "patternProperties"}
              else minimal_schema(values))
        for key, values in schema.items() if key != "title"
    }


def _compact_callable(fn, name):
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_call(*args, **kwargs):
            return compact_response(name, await fn(*args, **kwargs))
        return async_call

    @wraps(fn)
    def call(*args, **kwargs):
        return compact_response(name, fn(*args, **kwargs))
    return call


class ProfileRegistrar:
    """Decorator adapter: collect all descriptions, register only the chosen set."""

    def __init__(self, mcp, profile: str):
        self.mcp = mcp
        self.profile = profile
        self.catalog: dict[str, str] = {}

    def tool(self, **options):
        def register(fn):
            name = options.get("name") or fn.__name__
            self.catalog[name] = inspect.cleandoc(options.get("description") or fn.__doc__ or "")
            if self.profile == "full":
                return self.mcp.tool(**options)(fn)
            if name not in LOCAL_DESCRIPTIONS:
                return fn
            registered = self.mcp.tool(**{**options, "description": LOCAL_DESCRIPTIONS[name]})(fn)
            tool = self.mcp._tool_manager.get_tool(name)
            # Build metadata from the ORIGINAL callable first: wrapping before
            # registration can lose custom validators such as FormatString.
            tool.fn = _compact_callable(fn, name)
            tool.parameters = minimal_schema(tool.parameters)
            return registered
        return register
