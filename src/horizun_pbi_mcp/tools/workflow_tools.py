"""Workflows de alto nivel (Macrofase F).

Orientados a un resultado, no a una primitiva. Cada uno recorre analisis ->
plan -> preview -> apply -> verificacion -> reporte, y admite `dry_run`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session, get_settings
from horizun_pbi_mcp.services import workflows
from horizun_pbi_mcp.tools._common import guard, guard_mutation
from horizun_pbi_mcp.tools.visual_tools import _model_data
from horizun_pbi_mcp.utils.file_utils import atomic_write_text, timestamp


def _active():
    return get_session().require_active_pbip()


def register(mcp) -> None:

    @mcp.tool()
    def pbi_build_dashboard(name: str, measures: List[str],
                            category: Optional[str] = None,
                            preset: str = "executive", seed: str = "",
                            dry_run: bool = True,
                            request_id: str = "") -> Dict[str, Any]:
        """Construye un dashboard completo desde un objetivo, no desde primitivas.

        Analiza el modelo, compone el spec segun el preset, calcula el layout,
        genera preview, aplica en una transaccion y verifica el resultado.
        `dry_run=true` (por defecto) se detiene tras el preview.
        """
        ejecutar = lambda: workflows.build_dashboard(  # noqa: E731
            _active(), _model_data(), name=name, measures=measures,
            category=category, preset=preset, seed=seed, dry_run=dry_run)
        return guard(ejecutar) if dry_run else guard_mutation(ejecutar)

    @mcp.tool()
    def pbi_build_executive_page(measures: List[str],
                                 name: str = "Resumen ejecutivo",
                                 category: Optional[str] = None,
                                 seed: str = "",
                                 dry_run: bool = True,
                                 request_id: str = "") -> Dict[str, Any]:
        """Pagina de resumen ejecutivo: fila de KPIs y grafico protagonista."""
        ejecutar = lambda: workflows.build_executive_page(  # noqa: E731
            _active(), _model_data(), name=name, measures=measures,
            category=category, seed=seed, dry_run=dry_run)
        return guard(ejecutar) if dry_run else guard_mutation(ejecutar)

    @mcp.tool()
    def pbi_build_evm_page(measures: List[str], name: str = "EVM",
                           category: Optional[str] = None, seed: str = "",
                           dry_run: bool = True,
                           request_id: str = "") -> Dict[str, Any]:
        """Pagina EVM (Earned Value Management).

        Espera medidas del tipo PV, EV, AC, CPI y SPI; si no las reconoce, lo
        avisa en vez de generar una pagina que no significa nada.
        """
        ejecutar = lambda: workflows.build_evm_page(  # noqa: E731
            _active(), _model_data(), name=name, measures=measures,
            category=category, seed=seed, dry_run=dry_run)
        return guard(ejecutar) if dry_run else guard_mutation(ejecutar)

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


    @mcp.tool()
    def pbi_export_pbix(pbip_path: str = "", out_path: str = "",
                        overwrite: bool = False, refresh: str = "auto",
                        leave_open: bool = True, timeout: int = 600,
                        confirm_reuse: bool = False,
                        request_id: str = "") -> Dict[str, Any]:
        """Exporta el proyecto .pbip a un archivo .pbix de verdad.

        Microsoft no publica ninguna API para convertir el formato, asi que
        esto automatiza el flujo OFICIAL: abre el proyecto en Power BI Desktop
        y usa `Archivo > Guardar como`. No se fabrica el .pbix a mano -un zip
        cosido con TOM abre a veces y rompe otras-.

        Antes de tocar Desktop se resuelve la ruta exacta, se valida el TMDL,
        se comprueba que el destino cabe en Windows y, si existe y pasas
        `overwrite=true`, se respalda. Si el destino existe y no lo pasas,
        falla ANTES de abrir ninguna ventana.

        `refresh`: 'auto' intenta refrescar y declara el resultado sin
        adornarlo; 'required' no exporta si no pudo refrescar -para no
        entregar un .pbix como si tuviera datos-; 'skip' guarda el estado que
        el modelo tenga ahora.

        Que el dialogo desaparezca NO es que se haya guardado: se comprueba
        que el archivo existe, que es .pbix y no .pbit, que pesa mas de cero,
        que su fecha es de esta ejecucion y que el lector de .pbix lo puede
        abrir con informe y modelo dentro. Si `saved_as_verified` no es true,
        la respuesta no es un exito.

        `leave_open=true` (por defecto) deja abierto exactamente el .pbix
        generado y lo selecciona como modelo activo. Nunca cierra una ventana
        que no abrio esta operacion.

        Requiere Windows, Power BI Desktop instalado y el extra `export`
        (`pip install "horizun-pbi-mcp[export]"`). El cuadro de guardado se
        conduce por UI Automation desde un proceso aparte: los mensajes Win32
        cambian lo que se LEE en el desplegable de tipo sin avisar a la
        aplicacion, y Desktop sigue guardando un proyecto. `pbi_capabilities`
        dice en `pbix_export` si esta disponible aqui y ahora.
        """
        from horizun_pbi_mcp.services import pbix_export

        return guard_mutation(lambda: pbix_export.export(
            get_session(), pbip_path=pbip_path or None,
            out_path=out_path or None, overwrite=overwrite, refresh=refresh,
            leave_open=leave_open, timeout=timeout,
            confirm_reuse=confirm_reuse))

    @mcp.tool()
    def pbi_finalize_delivery(path: str = "", format: str = "pbix",
                              out_path: str = "", refresh: str = "auto",
                              overwrite: bool = False,
                              leave_open: bool = True,
                              request_id: str = "") -> Dict[str, Any]:
        """El ULTIMO paso de una construccion: del proyecto al entregable.

        Hace de extremo a extremo lo que hasta ahora eran cinco llamadas y un
        par de suposiciones: resuelve el archivo exacto que le pasas, lo
        prepara -convirtiendolo si le das un .pbix-, valida el proyecto,
        exporta a .pbix conduciendo Power BI Desktop, inspecciona el resultado
        y deja abierto justo el entregable, seleccionado como modelo activo.

        Una sola respuesta verificable: `output_pbix`, `output_sha256`,
        `output_size`, `saved_as_verified` y `opened_path_verified`.

        `path` se puede omitir para usar el proyecto activo. `format` solo
        acepta 'pbix' por ahora, y se declara asi en vez de fingir que hay
        mas. Requiere Windows con Power BI Desktop instalado.
        """
        from horizun_pbi_mcp.services import pbix_export

        return guard_mutation(lambda: pbix_export.finalize_delivery(
            get_session(), path=path or None, format=format,
            out_path=out_path or None, refresh=refresh, overwrite=overwrite,
            leave_open=leave_open))
