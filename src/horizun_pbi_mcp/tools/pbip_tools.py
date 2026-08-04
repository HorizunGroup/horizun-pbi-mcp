"""Tools de proyecto Power BI Project .pbip (Fase 6)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.pbip import backup, project_locator
from horizun_pbi_mcp.services import tmdl_validate
from horizun_pbi_mcp.tools._common import guard, guard_mutation


def register(mcp) -> None:
    @mcp.tool()
    def pbi_open_pbip_project(path: str) -> Dict[str, Any]:
        """Abre un proyecto .pbip y lo marca como proyecto activo.

        Detecta carpetas .SemanticModel (TMDL) y .Report (PBIR) y devuelve un
        resumen con advertencias (p.ej. si el informe no usa PBIR).
        `path`: ruta al archivo .pbip o a su carpeta.
        """
        return guard(lambda: project_locator.open_project(get_session(), path))

    @mcp.tool()
    def pbi_validate_pbip_project() -> Dict[str, Any]:
        """Valida a fondo el proyecto .pbip activo (estructura, PBIR, TMDL).

        Incluye `references`: cada Measure/Column que un visual.json o su
        filterConfig citan, cruzada contra el TMDL real. El esquema PBIR y la
        sintaxis TMDL pueden pasar limpios por separado con un visual que
        apunta a una medida borrada -- Desktop lo resuelve en silencio a
        nada, sin marca visible. Solo corre si el TMDL es valido (comparar
        contra un modelo que no abre es ruido, no una comprobacion).
        """
        return guard(lambda: project_locator.validate_project(get_session()))

    @mcp.tool()
    def pbi_create_pbip_project(out_dir: str, name: str,
                                culture: str = "es-ES",
                                width: int = 1280, height: int = 1080,
                                page_name: str = "Pagina 1",
                                overwrite: bool = False,
                                open_project: bool = True,
                                request_id: str = "") -> Dict[str, Any]:
        """Crea un proyecto .pbip vacio pero valido, y lo deja activo.

        Es el punto de partida para armar un tablero solo con rutas de
        archivos: crear el proyecto, cargarle los datos con
        pbi_add_table_from_file y componer las paginas, sin abrir Power BI
        Desktop hasta el final.

        Escribe el minimo que Power BI acepta —informe y modelo semantico
        apuntandose entre si— con la referencia en ruta RELATIVA: una absoluta
        ataria el proyecto a esta maquina. Incluye una pagina, porque un
        informe sin ninguna no abre.

        No declara `sourceQueryCulture` a proposito: la cultura se fija en cada
        consulta, que es lo unico que no obliga a suponer como escribe los
        decimales cada origen.
        """
        def _impl():
            from horizun_pbi_mcp.pbip import pbip_scaffold

            salida = pbip_scaffold.crear_proyecto(
                out_dir, name, culture=culture, width=width, height=height,
                page_name=page_name, overwrite=overwrite)
            if open_project:
                try:
                    salida["active"] = project_locator.open_project(
                        get_session(), salida["pbip_path"])
                except Exception as exc:                    # noqa: BLE001
                    # La publicacion ya se confirmo y fue validada. Fallar al
                    # seleccionarla en la sesion es auxiliar: devolver error
                    # afirmaria falsamente que no se creo, invitando a un
                    # reintento con overwrite. Se conserva el resultado y se
                    # hace explicita la seleccion pendiente.
                    salida["active"] = None
                    salida.setdefault("warnings", []).append(
                        "El proyecto se creo y valido, pero no se pudo dejar "
                        "activo en esta sesion: "
                        f"{type(exc).__name__}: {exc}. Abrelo con "
                        "pbi_open_pbip_project.")
            return salida
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_validate_tmdl(path: str = "",
                          use_tom: bool = True) -> Dict[str, Any]:
        """Comprueba si un modelo TMDL abrira, sin abrir Power BI Desktop.

        Dos capas. Un lint estatico que caza las trampas que solo se veian al
        abrir (una propiedad de tabla colocada despues de sus hijos, un
        comentario '///' sobre una relacion, una medida que se llama como una
        columna de su tabla, medidas duplicadas, referencias rotas) y, si estan
        las DLL, un parseo con el MISMO serializador que usa Power BI.

        Cada hallazgo trae `rule`, `severity`, el archivo y la linea. Si el
        parseo no se pudo ejecutar se dice (`parse_checked: false`) en vez de
        darlo por bueno.

        Hay fallos que NINGUN analisis estatico ve porque dependen de los datos
        —un blanco en el lado 'uno' de una relacion, un separador decimal mal
        interpretado—. Salen en `limitations`: para esos hay que refrescar.

        `path`: carpeta `definition` del modelo, la carpeta `.SemanticModel` o
        el `.pbip`. Si se omite, el proyecto activo.
        `use_tom=False`: solo el lint estatico, sin tocar las DLL.
        """
        def _impl():
            destino = Path(path) if path else None
            if destino is None:
                active = get_session().require_active_pbip()
                if not active.semantic_model_dir:
                    raise tmdl_validate.TmdlValidationError(
                        "El proyecto activo no tiene modelo semantico (.SemanticModel).")
                destino = Path(active.semantic_model_dir)
            destino = tmdl_validate.resolve_definition_dir(destino)
            resultado = tmdl_validate.validate(destino, use_tom=use_tom)
            resultado["definition_dir"] = str(destino)
            return resultado
        return guard(_impl)

    @mcp.tool()
    def pbi_backup_pbip_project(mode: str = "folder",
                                scope: str = "both",
                                request_id: str = "") -> Dict[str, Any]:
        """Crea un backup con timestamp del proyecto .pbip activo.

        `mode`: folder | zip. `scope`: report | model | both. Devuelve la ruta.
        """
        return guard_mutation(
            lambda: backup.backup_project(get_session(), mode, scope))
