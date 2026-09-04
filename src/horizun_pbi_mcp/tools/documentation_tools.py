"""Tools de documentacion e inspeccion del modelo (Fase 3)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session, get_settings
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi import model_reader
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip import tmdl_reader
from horizun_pbi_mcp.reporting import (analyze_model_quality, document_model_markdown)
from horizun_pbi_mcp.services import model_explorer
from horizun_pbi_mcp.tools._common import guard
from horizun_pbi_mcp.utils.file_utils import atomic_write_text, timestamp

log = get_logger("doc_tools")


def _normalizar_source(source: str) -> str:
    """Fuente canonica; nunca convierte un typo en una lectura live."""
    normalizada = str(source or "").strip().casefold()
    if normalizada not in {"live", "pbip"}:
        raise ValidationError(
            f"source invalido: {source!r}. Usa 'live' o 'pbip'.",
            details={"parameter": "source", "value": source,
                     "valid": ["live", "pbip"]})
    return normalizada


def _load_model_data(source: str = "live") -> Dict[str, Any]:
    source = _normalizar_source(source)
    session = get_session()
    if source == "pbip":
        active = session.require_active_pbip()
        return tmdl_reader.read_semantic_model(active)
    return model_reader.read_model(session)


def register(mcp) -> None:
    @mcp.tool()
    def pbi_list_tables(source: str = "live", detail: str = "full",
                        tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Lista tablas con columnas, tipos, visibilidad y conteos.

        `source`: 'live' (modelo abierto, por defecto) o 'pbip' (archivos TMDL).

        **Empieza por `detail='summary'`.** Devuelve nombre, visibilidad y
        recuentos, sin la lista de columnas. Con `detail='full'` (por defecto,
        por compatibilidad) un modelo de siete tablas ocupa ~28.000 caracteres y
        uno corporativo puede llenar buena parte de la ventana de contexto en
        una sola llamada.

        `tables`: acota a esas tablas por nombre. Es lo que se usa despues del
        resumen para pedir el detalle solo de las que interesan. Un nombre que
        no existe falla y devuelve los disponibles, en vez de una lista vacia.
        """
        def _impl():
            data = _load_model_data(source)
            return model_explorer.tables_view(data, tables=tables, detail=detail)
        return guard(_impl)

    @mcp.tool()
    def pbi_list_measures(source: str = "live", detail: str = "full",
                          tables: Optional[List[str]] = None) -> Dict[str, Any]:
        """Lista medidas con tabla, expresion DAX, formato, descripcion y carpeta.

        **Empieza por `detail='summary'`.** Omite la expresion DAX, que es el
        grueso del peso y rara vez hace falta para orientarse; para leer el DAX
        de una medida concreta usa `pbi_get_object`, y para buscar dentro del
        DAX, `pbi_search_model`. `detail='full'` sigue siendo el valor por
        defecto por compatibilidad.

        `tables`: acota a las medidas de esas tablas.
        """
        def _impl():
            data = _load_model_data(source)
            return model_explorer.measures_view(data, tables=tables, detail=detail)
        return guard(_impl)

    @mcp.tool()
    def pbi_list_relationships(source: str = "live") -> Dict[str, Any]:
        """Lista relaciones: tablas/columnas, cardinalidad, filtro cruzado y estado."""
        def _impl():
            data = _load_model_data(source)
            return {"count": len(data["relationships"]),
                    "relationships": data["relationships"]}
        return guard(_impl)

    @mcp.tool()
    def pbi_analyze_model_quality(source: str = "live") -> Dict[str, Any]:
        """Detecta problemas tipicos del modelo (calidad).

        Revisa medidas sin carpeta, DAX muy largo, relaciones bidireccionales/
        inactivas, columnas calculadas, IDs visibles, ausencia de calendario, etc.
        """
        def _impl():
            data = _load_model_data(source)
            return analyze_model_quality(data)
        return guard(_impl)

    @mcp.tool()
    def pbi_document_model(source: str = "live",
                           include_quality: bool = True) -> Dict[str, Any]:
        """Genera documentacion completa del modelo en Markdown.

        Incluye resumen, tablas, columnas, medidas, relaciones, jerarquias, roles
        (RLS) y advertencias de calidad. Guarda el archivo en outputs/.
        """
        def _impl():
            fuente = _normalizar_source(source)
            data = _load_model_data(fuente)
            quality = analyze_model_quality(data) if include_quality else None
            md = document_model_markdown(data, quality)
            settings = get_settings()
            out_path = settings.outputs_dir / f"model_documentation_{timestamp()}.md"
            atomic_write_text(out_path, md)
            return {
                "output_path": str(out_path),
                "source": fuente,
                "summary": {
                    "tables": len(data["tables"]),
                    "measures": len(data["measures"]),
                    "relationships": len(data["relationships"]),
                    "quality_issues": quality["issue_count"] if quality else None,
                },
            }
        return guard(_impl)
