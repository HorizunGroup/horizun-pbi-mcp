"""Tools de creacion/edicion/borrado de medidas DAX (Fase 4).

mode:
- 'live': escribe en el modelo abierto (TOM). Requiere Power BI Desktop ABIERTO
  y que el usuario guarde (Ctrl+S) para que quede en el archivo.
- 'pbip': escribe en el archivo TMDL del proyecto activo, en una transaccion con
  journal. Requiere Desktop CERRADO.
- 'both': DESHABILITADO bajo la politica estricta. Los dos destinos exigen
  estados de Desktop incompatibles, asi que una sola llamada terminaria
  aplicando solo uno. Se rechaza antes de producir cualquier efecto.
  Ver `services.dual_mode`.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.powerbi import model_writer
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.pbip import tmdl_writer
from horizun_pbi_mcp.services import dual_mode
from horizun_pbi_mcp.tools._common import guard, guard_mutation

# La normalizacion del modo y la precondicion viven en services.dual_mode: la
# decision de si `both` es ejecutable es una sola y no puede duplicarse por tool.
_check_mode = dual_mode.assert_mode_is_safely_executable
_run_dual = dual_mode.run_dual


def register(mcp) -> None:
    @mcp.tool()
    def pbi_create_measure(
        table: str,
        name: str,
        expression: str,
        format_string: Optional[str] = None,
        description: Optional[str] = None,
        display_folder: Optional[str] = None,
        mode: str = "live",
        overwrite: bool = False,
        data_category: Optional[str] = None,
    request_id: str = "") -> Dict[str, Any]:
        """Crea (o reemplaza con overwrite=true) una medida DAX.

        Valida que la tabla exista y que la medida no exista salvo overwrite.
        `data_category`: opcional, p.ej. "ImageUrl" para medidas que devuelven un
        data-URI SVG (Power BI las renderiza como imagen en tablas/matrices).
        Devuelve el diff antes/despues.

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip'.
        """
        def _impl():
            m = _check_mode(mode)
            session = get_session()
            return _run_dual(
                m,
                lambda: model_writer.create_measure(
                    session, table, name, expression, format_string,
                    description, display_folder, overwrite, data_category),
                lambda: tmdl_writer.create_measure_pbip(
                    session.require_active_pbip(), table, name, expression,
                    format_string, description, display_folder, overwrite,
                    data_category=data_category),
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_update_measure(
        table: str,
        name: str,
        expression: Optional[str] = None,
        format_string: Optional[str] = None,
        description: Optional[str] = None,
        display_folder: Optional[str] = None,
        mode: str = "live",
    request_id: str = "") -> Dict[str, Any]:
        """Actualiza una medida existente. Lo no especificado se conserva.

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip'.
        """
        def _impl():
            m = _check_mode(mode)
            session = get_session()
            return _run_dual(
                m,
                lambda: model_writer.update_measure(
                    session, table, name, expression, format_string,
                    description, display_folder),
                lambda: tmdl_writer.update_measure_pbip(
                    session.require_active_pbip(), table, name, expression,
                    format_string, description, display_folder),
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_delete_measure(table: str, name: str, mode: str = "live",
                           confirm: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Elimina una medida. Operacion destructiva: requiere confirm=true.

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip'.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Operacion destructiva: pasa confirm=true para eliminar la medida.")
            m = _check_mode(mode)
            session = get_session()
            return _run_dual(
                m,
                lambda: model_writer.delete_measure(session, table, name),
                lambda: tmdl_writer.delete_measure_pbip(
                    session.require_active_pbip(), table, name),
            )
        return guard_mutation(_impl)
