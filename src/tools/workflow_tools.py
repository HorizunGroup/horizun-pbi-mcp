"""Workflows de alto nivel (Macrofase F).

Orientados a un resultado, no a una primitiva. Cada uno recorre analisis ->
plan -> preview -> apply -> verificacion -> reporte, y admite `dry_run`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session, get_settings
from services import workflows
from tools._common import guard, guard_mutation
from tools.visual_tools import _model_data
from utils.file_utils import atomic_write_text, timestamp


def _active():
    return get_session().require_active_pbip()


def register(mcp) -> None:

    @mcp.tool()
    def pbi_build_dashboard(name: str, measures: List[str],
                            category: Optional[str] = None,
                            preset: str = "executive", seed: str = "",
                            dry_run: bool = True) -> Dict[str, Any]:
        """Construye un dashboard completo desde un objetivo, no desde primitivas.

        Analiza el modelo, compone el spec segun el preset, calcula el layout,
        genera preview, aplica en una transaccion y verifica el resultado.
        `dry_run=true` (por defecto) se detiene tras el preview.
        """
        return guard(lambda: workflows.build_dashboard(
            _active(), _model_data(), name=name, measures=measures,
            category=category, preset=preset, seed=seed, dry_run=dry_run))

    @mcp.tool()
    def pbi_build_executive_page(measures: List[str],
                                 name: str = "Resumen ejecutivo",
                                 category: Optional[str] = None,
                                 seed: str = "",
                                 dry_run: bool = True) -> Dict[str, Any]:
        """Pagina de resumen ejecutivo: fila de KPIs y grafico protagonista."""
        return guard(lambda: workflows.build_executive_page(
            _active(), _model_data(), name=name, measures=measures,
            category=category, seed=seed, dry_run=dry_run))

    @mcp.tool()
    def pbi_build_evm_page(measures: List[str], name: str = "EVM",
                           category: Optional[str] = None, seed: str = "",
                           dry_run: bool = True) -> Dict[str, Any]:
        """Pagina EVM (Earned Value Management).

        Espera medidas del tipo PV, EV, AC, CPI y SPI; si no las reconoce, lo
        avisa en vez de generar una pagina que no significa nada.
        """
        return guard(lambda: workflows.build_evm_page(
            _active(), _model_data(), name=name, measures=measures,
            category=category, seed=seed, dry_run=dry_run))

    @mcp.tool()
    def pbi_repair_broken_references(mapping: Optional[Dict[str, str]] = None,
                                     dry_run: bool = True, request_id: str = "") -> Dict[str, Any]:
        """Detecta referencias rotas en los visuales y las repara.

        Sin `mapping` solo diagnostica: adivinar a que campo queria apuntar un
        visual roto no es una decision que deba tomarse sola. Pasa
        `{"Tabla[Viejo]": "Tabla[Nuevo]"}` para repararlas, y el destino se
        valida contra el modelo antes de escribir.
        """
        return guard_mutation(lambda: workflows.repair_broken_references(
            _active(), _model_data(), mapping=mapping, dry_run=dry_run))

    @mcp.tool()
    def pbi_normalize_report(dry_run: bool = True, request_id: str = "") -> Dict[str, Any]:
        """Normaliza la geometria de TODAS las paginas del informe.

        Mete dentro del lienzo lo que se sale, sube al minimo lo demasiado
        pequeno y respeta margenes. No reacomoda lo que ya cumple. Compara el
        puntaje de auditoria antes y despues.
        """
        return guard_mutation(lambda: workflows.normalize_report(
            _active(), _model_data(), dry_run=dry_run))

    @mcp.tool()
    def pbi_compare_live_to_pbip() -> Dict[str, Any]:
        """Compara el modelo EN VIVO con el TMDL del disco.

        Util para saber si hay cambios en memoria sin guardar: lista tablas y
        medidas que solo estan en un lado, y medidas cuyo DAX difiere.
        """
        return guard(lambda: workflows.compare_live_to_pbip(get_session()))

    @mcp.tool()
    def pbi_prepare_delivery(dry_run: bool = True, request_id: str = "") -> Dict[str, Any]:
        """Checklist de pre-entrega con plan de correccion.

        Audita el proyecto, produce un checklist de bloqueantes y propone las
        correcciones automaticas disponibles. Con `dry_run=false` las aplica y
        compara el puntaje antes y despues.
        """
        return guard_mutation(lambda: workflows.prepare_delivery(
            _active(), _model_data(), dry_run=dry_run))

    @mcp.tool()
    def pbi_generate_technical_documentation() -> Dict[str, Any]:
        """Documentacion tecnica completa en Markdown, guardada en outputs/.

        Incluye el modelo (tablas, medidas con su DAX y dependencias), el
        informe pagina a pagina con los campos de cada visual, y la auditoria
        con puntaje por dominio.
        """
        def _impl():
            active = _active()
            texto = workflows.generate_technical_documentation(active, _model_data())
            destino = get_settings().outputs_dir / f"technical_doc_{timestamp()}.md"
            atomic_write_text(destino, texto)
            return {"output_path": str(destino), "length": len(texto),
                    "sections": ["Modelo semantico", "Informe", "Auditoria"]}
        return guard(_impl)
