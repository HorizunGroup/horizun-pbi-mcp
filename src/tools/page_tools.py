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


def normalizar_spec(spec: Any) -> Any:
    """Acepta los dos dialectos de spec que conviven en el servidor.

    `pbi_create_page_from_spec` nacio con `{page_name, canvas}`; el constructor
    declarativo posterior usa `{schema_version, page:{name,width,height}}`. Se
    validaba con uno y no se podia aplicar con el otro, y el mensaje de error
    ("falta 'page_name'") no daba ninguna pista de que hubiera dos formatos.

    Traducir aqui es aditivo: quien ya llamaba con el formato viejo no nota
    nada, y el nuevo deja de rebotar. La firma de la tool no cambia, que es lo
    que el contrato del baseline protege.
    """
    if not isinstance(spec, dict) or "page_name" in spec:
        return spec
    pagina = spec.get("page")
    if not isinstance(pagina, dict):
        return spec

    traducido = {k: v for k, v in spec.items() if k not in ("page", "schema_version")}
    traducido["page_name"] = pagina.get("name")
    lienzo = {k: pagina[k] for k in ("width", "height") if pagina.get(k)}
    if lienzo and "canvas" not in traducido:
        traducido["canvas"] = lienzo
    return traducido


def register(mcp) -> None:

    @mcp.tool()
    def pbi_propose_dashboard() -> Dict[str, Any]:
        """Mira el modelo y PROPONE varios diseños distintos, con su porque.

        A diferencia de pbi_page_building_blocks, que entrega el inventario y
        deja el diseño en manos de quien pregunta, esto clasifica lo que hay
        —que columna es un estado, cual una fecha, cuales forman una familia de
        metricas comparables— y devuelve paginas completas con un spec listo
        para aplicar.

        Devuelve tambien `blockers`: lo que hay que resolver ANTES de construir
        (p.ej. un modelo sin medidas, donde todo visual caeria en sumas
        implicitas), y `themes` con las paletas disponibles.

        Usalo para ofrecer opciones al usuario en vez de decidir por el.
        """
        from pbip import theme
        from services import proposals

        return guard(lambda: proposals.propose(_model_data(), theme.list_presets()))

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
            res = page_builder.create_page_from_spec(active, normalizar_spec(spec), mi)
            res["reload_hint"] = RELOAD_HINT
            return res
        return guard_mutation(_impl)
