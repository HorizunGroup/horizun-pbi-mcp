"""Tools de documentacion e inspeccion del modelo (Fase 3)."""
from __future__ import annotations

from typing import Any, Dict, List

from config import get_session, get_settings
from logging_config import get_logger
from powerbi import model_reader
from pbip import tmdl_reader
from reporting import (analyze_model_quality, document_model_markdown)
from tools._common import guard
from utils.file_utils import atomic_write_text, timestamp

log = get_logger("doc_tools")


def _load_model_data(source: str = "live") -> Dict[str, Any]:
    session = get_session()
    if source == "pbip":
        active = session.require_active_pbip()
        return tmdl_reader.read_semantic_model(active)
    return model_reader.read_model(session)


def register(mcp) -> None:
    @mcp.tool()
    def pbi_list_tables(source: str = "live") -> Dict[str, Any]:
        """Lista tablas con columnas, tipos, visibilidad y conteos.

        `source`: 'live' (modelo abierto, por defecto) o 'pbip' (archivos TMDL).
        """
        def _impl():
            data = _load_model_data(source)
            return {"count": len(data["tables"]), "tables": data["tables"]}
        return guard(_impl)

    @mcp.tool()
    def pbi_list_measures(source: str = "live") -> Dict[str, Any]:
        """Lista medidas con tabla, expresion DAX, formato, descripcion y carpeta."""
        def _impl():
            data = _load_model_data(source)
            return {"count": len(data["measures"]), "measures": data["measures"]}
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
            data = _load_model_data(source)
            quality = analyze_model_quality(data) if include_quality else None
            md = document_model_markdown(data, quality)
            settings = get_settings()
            out_path = settings.outputs_dir / f"model_documentation_{timestamp()}.md"
            atomic_write_text(out_path, md)
            return {
                "output_path": str(out_path),
                "source": source,
                "summary": {
                    "tables": len(data["tables"]),
                    "measures": len(data["measures"]),
                    "relationships": len(data["relationships"]),
                    "quality_issues": quality["issue_count"] if quality else None,
                },
            }
        return guard(_impl)
