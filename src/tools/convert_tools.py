"""Tools de conversion .pbix -> .pbip."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pbip import pbix_reader, pbix_to_pbip
from powerbi.errors import PowerBIMCPError
from tools._common import guard, guard_mutation


def _resumen_lote(resultado: Dict[str, Any]) -> Dict[str, Any]:
    """Añade al lote los totales que interesan de un vistazo."""
    convertidos = resultado["converted"]
    resultado["pages"] = sum(c["pages"] for c in convertidos)
    resultado["visuals"] = sum(c["visuals"] for c in convertidos)
    resultado["with_model"] = sum(
        1 for c in convertidos if c["model_status"] == "exported")
    resultado["needs_review"] = [
        {"source": c["source"], "warnings": c["warnings"], "dropped": c["dropped"]}
        for c in convertidos if c["warnings"] or c["dropped"]
    ]
    return resultado


class BatchConversionError(PowerBIMCPError):
    """Un lote no puede presentarse como exito si una conversion fallo."""

    code = "bulk_apply_failed"


class BatchConversionPartialError(BatchConversionError):
    """Al menos un proyecto se publico y al menos otro fallo."""

    code = "bulk_partially_applied"


def _exigir_lote_completo(resultado: Dict[str, Any]) -> Dict[str, Any]:
    fallos = int(resultado.get("failed_count") or 0)
    if not fallos:
        return resultado
    detalle = _resumen_lote(resultado)
    error = (BatchConversionPartialError if detalle.get("ok_count")
             else BatchConversionError)
    raise error(
        f"Fallaron {fallos} de {detalle.get('total', fallos)} conversion(es).",
        details=detalle)


def register(mcp) -> None:

    @mcp.tool()
    def pbi_inspect_pbix(path: str) -> Dict[str, Any]:
        """Radiografia de un .pbix SIN convertirlo ni abrir Power BI Desktop.

        Dice en que formato esta el informe ('pbir' si ya trae el formato
        mejorado y solo hay que copiarlo, 'layout' si es el heredado y hay que
        traducirlo), si lleva modelo de datos propio o es un informe con
        conexion en vivo, y cuantas paginas, recursos y visuales personalizados
        tiene. Sirve para saber que esperar antes de lanzar la conversion.

        `path`: ruta al archivo .pbix.
        """
        return guard(lambda: pbix_reader.read_pbix(path).summary())

    @mcp.tool()
    def pbi_convert_pbix_to_pbip(
        path: str,
        out_dir: str,
        project_name: Optional[str] = None,
        include_model: bool = True,
        overwrite: bool = False,
        recursive: bool = False,
        dataset_connection_string: Optional[str] = None,
        desktop_timeout: int = 300,
        close_desktop: bool = True,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Convierte uno o varios .pbix en proyectos .pbip (PBIR + TMDL).

        El informe sale del propio archivo: si el .pbix ya guarda PBIR se copia
        tal cual, y si trae el formato heredado se traduce pagina a pagina.

        El modelo NO se puede leer del archivo (es un backup comprimido del
        motor), asi que se ABRE EL .pbix EN POWER BI DESKTOP y se serializa a
        TMDL desde ahi. Cuenta con eso: cada archivo tarda lo que tarde Desktop
        en cargarlo. Si el informe ya esta abierto se reutiliza esa sesion; si
        lo abre esta tool, lo cierra al terminar (`close_desktop`). El .pbix
        original nunca se modifica.

        `path`: un .pbix o una CARPETA (`recursive` incluye subcarpetas).
        `out_dir`: carpeta donde crear el proyecto; se crea una subcarpeta por
        informe. `include_model=false` genera solo la mitad del informe, sin
        tocar Desktop. `dataset_connection_string` es obligatorio para informes
        con conexion en vivo (los que no llevan modelo propio).

        Devuelve, por archivo, que se escribio y —lo importante— los avisos y
        lo que se quedo por el camino (`dropped`), como los marcadores.
        """
        def _impl():
            archivos = pbix_to_pbip.find_pbix(path, recursive=recursive)
            opciones: Dict[str, Any] = {
                "include_model": include_model,
                "overwrite": overwrite,
                "dataset_connection_string": dataset_connection_string,
                "desktop_timeout": desktop_timeout,
                "close_desktop": close_desktop,
            }
            if len(archivos) == 1:
                resultado = pbix_to_pbip.convert(
                    archivos[0], out_dir, project_name=project_name, **opciones)
                return resultado.to_dict()
            return _exigir_lote_completo(
                pbix_to_pbip.convert_many(archivos, out_dir, **opciones))

        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_list_convertible_pbix(path: str,
                                  recursive: bool = False) -> Dict[str, Any]:
        """Lista los .pbix de una carpeta y como se convertiria cada uno.

        Recorre los archivos sin abrirlos en Desktop y dice, por cada uno, si
        el informe se copiaria (ya esta en PBIR) o habria que traducirlo, y si
        hace falta Desktop para sacar el modelo. Es la vista previa del lote.

        `path`: carpeta (o un .pbix suelto). `recursive`: incluir subcarpetas.
        """
        def _impl():
            archivos = pbix_to_pbip.find_pbix(path, recursive=recursive)
            items: List[Dict[str, Any]] = []
            errores: List[Dict[str, Any]] = []
            for archivo in archivos:
                try:
                    resumen = pbix_reader.read_pbix(archivo).summary()
                except Exception as exc:  # noqa: BLE001
                    errores.append({"path": str(archivo), "message": str(exc)})
                    continue
                resumen["name"] = Path(archivo).name
                resumen["plan"] = ("copiar informe PBIR"
                                   if resumen["report_format"] == "pbir"
                                   else "traducir informe heredado"
                                   if resumen["report_format"] == "layout"
                                   else "sin informe: no convertible")
                resumen["needs_desktop"] = bool(resumen["has_data_model"])
                items.append(resumen)
            return {
                "root": str(Path(path).expanduser().resolve()),
                "count": len(items),
                "needs_desktop": sum(1 for i in items if i["needs_desktop"]),
                "already_pbir": sum(1 for i in items
                                    if i["report_format"] == "pbir"),
                "legacy_layout": sum(1 for i in items
                                     if i["report_format"] == "layout"),
                "items": items,
                "unreadable": errores,
                "warnings": ([f"{len(errores)} archivo(s) no se pudieron "
                              "inspeccionar; el listado es parcial."]
                             if errores else []),
            }

        return guard(_impl)
