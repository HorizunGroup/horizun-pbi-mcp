"""Jerarquia de errores de PowerBI-MCP.

Todos los errores de dominio heredan de `PowerBIMCPError`. Las tools capturan
esta base y devuelven un objeto de error consistente (ver tools/_common.py),
sin ocultar el mensaje original del motor de Analysis Services.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class PowerBIMCPError(Exception):
    """Base de todos los errores controlados del servidor."""

    #: codigo corto y estable para el cliente
    code = "powerbi_mcp_error"

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"error": self.code, "message": self.message}
        if self.details:
            out["details"] = self.details
        return out


# ---- Interop / entorno ----
class ClrNotAvailableError(PowerBIMCPError):
    code = "clr_not_available"


class AdomdNotInstalledError(PowerBIMCPError):
    code = "adomd_not_installed"


class TomNotInstalledError(PowerBIMCPError):
    code = "tom_not_installed"


# ---- Descubrimiento / conexion ----
class ModelDiscoveryError(PowerBIMCPError):
    code = "model_discovery_error"


class NoActiveModelError(PowerBIMCPError):
    code = "no_active_model"


class ConnectionFailedError(PowerBIMCPError):
    code = "connection_failed"


# ---- DAX ----
class DaxQueryError(PowerBIMCPError):
    """Error de ejecucion DAX; conserva el mensaje original del motor."""

    code = "dax_query_error"


# ---- Modelo / medidas ----
class TableNotFoundError(PowerBIMCPError):
    code = "table_not_found"


class MeasureNotFoundError(PowerBIMCPError):
    code = "measure_not_found"


class MeasureExistsError(PowerBIMCPError):
    code = "measure_exists"


class RefreshError(PowerBIMCPError):
    code = "refresh_error"


class RefreshTimeoutError(RefreshError):
    """El refresh agoto su plazo y se pidio cancelarlo.

    Codigo propio, no `refresh_error`, porque la accion del cliente es
    distinta: no hay nada que corregir en la peticion. Lo habitual es que un
    origen este esperando credenciales que un refresh lanzado por XMLA no
    puede pedir -no hay dialogo que mostrar-, y el motor espera sin fin.
    """

    code = "refresh_timeout"


# ---- PBIP / archivos ----
class NoActivePbipError(PowerBIMCPError):
    code = "no_active_pbip"


class PbipNotFoundError(PowerBIMCPError):
    code = "pbip_not_found"


class PbipStructureError(PowerBIMCPError):
    code = "pbip_structure_error"


class PbirNotEnabledError(PowerBIMCPError):
    code = "pbir_not_enabled"


class BackupError(PowerBIMCPError):
    code = "backup_error"


class ValidationError(PowerBIMCPError):
    code = "validation_error"


class PathSecurityError(PowerBIMCPError):
    code = "path_security_error"


class VisualFactoryError(PowerBIMCPError):
    code = "visual_factory_error"
