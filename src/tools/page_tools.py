"""Tools de generacion de hojas desde contenido existente + lenguaje natural.

Flujo:
1. pbi_page_building_blocks  -> material (modelo + catalogo de visuales + canvas).
2. (Claude razona la instruccion y arma un spec.)
3. pbi_preview_spec_html     -> maqueta HTML del spec propuesto (revisar antes).
4. pbi_create_page_from_spec -> materializa la pagina PBIR real.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config import get_session, get_settings
from pbip import page_builder
from tools._common import guard, guard_mutation
from tools.visual_tools import _measure_index, _model_data
from utils.file_utils import atomic_write_text, timestamp

RELOAD_HINT = ("Reabre el informe en Power BI Desktop para ver la hoja. "
               "Edita el PBIR con Desktop cerrado para no sobrescribir cambios.")


def register(mcp) -> None:
    @mcp.tool()
    def pbi_page_building_blocks() -> Dict[str, Any]:
        """Entrega el material para diseniar una hoja: modelo (tablas/medidas/columnas),
        catalogo de visuales existentes (reutilizables como plantilla), canvas y paginas.

        Usa esto ANTES de proponer una hoja: te dice que campos y tipos de visual hay.
        """
        def _impl():
            active = get_session().require_active_pbip()
            return page_builder.building_blocks(active, _model_data())
        return guard(_impl)

    @mcp.tool()
    def pbi_preview_spec_html(spec: Dict[str, Any]) -> Dict[str, Any]:
        """Genera una MAQUETA HTML de una hoja propuesta (sin escribir nada al .pbip).

        `spec`: {page_name, canvas?, layout?, visuals:[{type,title,fields,position?}]}.
        Devuelve la ruta del HTML (abrelo en el navegador para revisar el diseno).
        """
        def _impl():
            active = get_session().require_active_pbip()
            htmldoc = page_builder.spec_to_html(active, spec, standalone=True)
            out = get_settings().outputs_dir / f"preview_{timestamp()}.html"
            atomic_write_text(out, htmldoc)
            return {"output_path": str(out), "page_name": spec.get("page_name"),
                    "visual_count": len(spec.get("visuals", []))}
        return guard(_impl)

    @mcp.tool()
    def pbi_export_page_html(page: str) -> Dict[str, Any]:
        """Exporta una MAQUETA HTML de una pagina EXISTENTE (layout + campos de cada visual)."""
        def _impl():
            active = get_session().require_active_pbip()
            htmldoc = page_builder.page_to_html(active, page, standalone=True)
            out = get_settings().outputs_dir / f"page_{timestamp()}.html"
            atomic_write_text(out, htmldoc)
            return {"output_path": str(out), "page": page}
        return guard(_impl)

    @mcp.tool()
    def pbi_create_page_from_spec(spec: Dict[str, Any], request_id: str = "") -> Dict[str, Any]:
        """Crea una hoja (pagina) PBIR completa a partir de un spec.

        `spec`: {page_name, canvas?, layout?(grid|dashboard|executive_summary),
                 visuals:[{type,title,fields:{rol:refs},position?}]}.
        Clona visuales existentes del mismo tipo como plantilla. Hace backup.
        Omite 'position' en un visual para que se auto-acomode con 'layout'.
        """
        def _impl():
            session = get_session()
            active = session.require_active_pbip()
            mi = _measure_index(_model_data())
            res = page_builder.create_page_from_spec(active, spec, mi)
            res["reload_hint"] = RELOAD_HINT
            return res
        return guard_mutation(_impl)
