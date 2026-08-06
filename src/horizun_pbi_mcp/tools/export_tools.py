"""Tools de exportacion documental y acceso de solo lectura a SharePoint."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.services import exporting, sharepoint
from horizun_pbi_mcp.tools._common import guard


def register(mcp) -> None:
    @mcp.tool()
    def pbi_export_excel(source: str = "auto", query: str = "",
                         include_report: bool = True,
                         include_audit: bool = True,
                         max_rows: int = 100_000,
                         file_name: str = "") -> Dict[str, Any]:
        """Exporta la informacion disponible a un libro Excel verificado.

        Crea hojas para resumen, tablas, columnas, medidas, relaciones y, si
        hay PBIP activo, paginas, visuales y auditoria. `source` admite
        `auto|live|pbip`. Si se proporciona `query`, ejecuta DAX de solo lectura
        contra Desktop y agrega `Datos_DAX`, declarando cualquier truncamiento.

        El archivo se escribe en `outputs/excel/`, nunca dentro del PBIP. No
        sobrescribe nombres existentes, neutraliza formulas inyectadas y vuelve
        a abrir el XLSX antes de informar exito.
        """
        return guard(lambda: exporting.export_excel(
            get_session(), source=source, query=query,
            include_report=include_report, include_audit=include_audit,
            max_rows=max_rows, file_name=file_name))

    @mcp.tool()
    def pbi_generate_pdf_report(
            report_type: str = "executive", source: str = "auto",
            title: str = "", capture_paths: Optional[List[str]] = None,
            include_audit: bool = True, max_findings: int = 50,
            file_name: str = "") -> Dict[str, Any]:
        """Genera un PDF ejecutivo, tecnico o de auditoria con capturas.

        Compone informacion existente del modelo, paginas, visuales y auditoria.
        `capture_paths` acepta hasta 20 PNG/JPEG, por ejemplo las rutas devueltas
        por `pbi_validate_desktop_render`. No inventa graficas si no hay captura.

        El PDF se reabre con pypdf y, cuando Poppler esta disponible, su primera
        pagina se renderiza a PNG como prueba visual. Se guarda en `outputs/pdf/`.
        """
        return guard(lambda: exporting.generate_pdf_report(
            get_session(), report_type=report_type, source=source, title=title,
            capture_paths=capture_paths, include_audit=include_audit,
            max_findings=max_findings, file_name=file_name))

    @mcp.tool()
    def pbi_sharepoint_list_folder(
            site_url: str, library: str = "Documents", folder_path: str = "",
            recursive: bool = False, max_items: int = 500) -> Dict[str, Any]:
        """Conecta con SharePoint Online y lista una carpeta mediante Graph.

        `site_url` es la URL HTTPS del sitio; `library` es el nombre o id de la
        biblioteca documental; `folder_path` usa `/`. Sigue la paginacion de
        Graph y puede recorrer subcarpetas con un limite total explicito.

        Autenticacion app-only: las credenciales SOLO se leen de las variables
        `HORIZUN_PBI_MCP_SHAREPOINT_TENANT_ID`, `_CLIENT_ID` y `_CLIENT_SECRET`.
        Nunca se aceptan secretos como parametros ni se devuelven tokens.
        """
        return guard(lambda: sharepoint.list_folder(
            site_url, library=library, folder_path=folder_path,
            recursive=recursive, max_items=max_items))

    @mcp.tool()
    def pbi_sharepoint_download_folder(
            site_url: str, library: str = "Documents", folder_path: str = "",
            extensions: Optional[List[str]] = None, recursive: bool = True,
            max_files: int = 200, max_total_mb: int = 500) -> Dict[str, Any]:
        """Descarga una carpeta de SharePoint a outputs/ de forma todo-o-nada.

        Primero inventaria el lote y valida `max_files`, `max_total_mb` y el
        filtro opcional `extensions` (por ejemplo `['xlsx','csv']`). Luego usa
        URLs temporales emitidas por Graph, verifica tamano y SHA-256 y publica
        el directorio completo bajo `outputs/sharepoint/`. Un fallo elimina el
        staging y no deja una carpeta final parcial.

        Es una descarga de solo lectura remota: no sube, mueve ni elimina nada
        en SharePoint.
        """
        return guard(lambda: sharepoint.download_folder(
            site_url, library=library, folder_path=folder_path,
            extensions=extensions, recursive=recursive, max_files=max_files,
            max_total_mb=max_total_mb))
