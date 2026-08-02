"""Tools de conexion y ejecucion DAX (Fases 1 y 2)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session
from powerbi import dax_runner, desktop_discovery
from powerbi.clr_bootstrap import diagnostics
from tools._common import guard


def register(mcp) -> None:
    @mcp.tool()
    def pbi_list_desktop_models() -> Dict[str, Any]:
        """Lista los modelos de Power BI Desktop abiertos localmente.

        Detecta el motor de Analysis Services (localhost:<puerto>) de cada informe
        abierto y devuelve puerto, connection string, catalogo y nº de tablas.
        """
        def _impl():
            instances = desktop_discovery.discover_instances()
            return {"count": len(instances), "instances": instances,
                    "diagnostics": diagnostics()}
        return guard(_impl)

    @mcp.tool()
    def pbi_select_model(port: Optional[int] = None,
                         catalog: Optional[str] = None) -> Dict[str, Any]:
        """Selecciona el modelo local activo para futuras operaciones.

        Si hay un solo modelo abierto no hace falta indicar puerto. Si hay varios,
        pasa el `port` (visto en pbi_list_desktop_models).
        """
        def _impl():
            session = get_session()
            model = desktop_discovery.select_model(session, port=port, catalog=catalog)
            return {"active_model": model.to_dict()}
        return guard(_impl)

    @mcp.tool()
    def pbi_open_in_desktop(path: str, timeout: int = 300,
                            reuse_open: bool = True,
                            select: bool = True) -> Dict[str, Any]:
        """Abre un .pbip o .pbix en Power BI Desktop y espera a que sirva el modelo.

        Cierra el ciclo de trabajo: despues de editar un proyecto, esto permite
        comprobar que ABRE de verdad y consultar sus medidas, sin pedirle al
        usuario que lo haga a mano. Un TMDL que no carga se manifiesta aqui.

        Espera a que el motor local aparezca y deje de crecer, identifica cual
        de las instancias corresponde a este archivo (el puerto es dinamico) y,
        con `select=true`, lo deja como modelo activo.

        Si el archivo ya estaba abierto se reutiliza esa sesion y no se toca
        nada (`reuse_open`). Nunca cierra una ventana del usuario.

        Ojo: un .pbip recien abierto trae el modelo SIN DATOS. Refresca despues
        con pbi_refresh_model si vas a comprobar valores.
        """
        def _impl():
            from powerbi import desktop_launcher

            abierto = desktop_launcher.open_pbix(
                path, timeout=timeout, reuse_open=reuse_open)
            salida: Dict[str, Any] = {
                "path": abierto.pbix_path,
                "instance": abierto.instance,
                "desktop_pid": abierto.desktop_pid,
                "launched_by_us": abierto.launched_by_us,
                "reused_open_session": not abierto.launched_by_us,
                "waited_seconds": abierto.waited_seconds,
                "selected": False,
            }
            if select:
                try:
                    session = get_session()
                    model = desktop_discovery.select_model(
                        session, port=abierto.instance.get("port"))
                    salida["active_model"] = model.to_dict()
                    salida["selected"] = True
                except BaseException:
                    # Si esta llamada abrio Desktop y luego no pudo dejar la
                    # sesion utilizable, compensa el efecto. Una tool que
                    # devuelve error no debe dejar una ventana nueva a medias.
                    if abierto.launched_by_us:
                        desktop_launcher.close(abierto)
                    raise
            return salida
        return guard(_impl)

    @mcp.tool()
    def pbi_run_dax(query: str, max_rows: Optional[int] = None,
                    max_bytes: Optional[int] = None,
                    timeout_seconds: Optional[int] = None,
                    export: bool = False) -> Dict[str, Any]:
        """Ejecuta una consulta DAX de SOLO LECTURA contra el modelo activo.

        Solo se admiten formas reconocidas: EVALUATE, DEFINE...EVALUATE y DMVs
        de $SYSTEM. Cualquier otra cosa se rechaza (politica fail-closed).

        `max_rows`: limite de filas. `max_bytes`: tope de tamano del resultado,
        para no devolver megas al cliente. `timeout_seconds`: timeout del
        comando. `export=true` vuelca el resultado completo a outputs/ y
        devuelve la ruta.

        Devuelve columnas, tipos observados, filas, estadisticas de ejecucion y
        si se trunco (y por que: filas o tamano). Los errores DAX del motor se
        devuelven tal cual.
        """
        return guard(lambda: dax_runner.run_dax(
            get_session(), query, max_rows, max_bytes, timeout_seconds, export))

    @mcp.tool()
    def pbi_test_connection() -> Dict[str, Any]:
        """Valida la conexion al modelo activo con una consulta trivial."""
        return guard(lambda: dax_runner.test_connection(get_session()))

    @mcp.tool()
    def pbi_validate_measures(measures: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Valida DAX de medidas SIN modificar el modelo (dry-run con DEFINE MEASURE).

        Ideal para probar medidas ANTES de crearlas con pbi_create_measure.
        `measures`: lista de {"name","dax","table"(opcional)}. Las medidas pueden
        referenciarse entre si. Devuelve por cada una: valid, value (muestra) y error.
        """
        return guard(lambda: dax_runner.validate_measures(get_session(), measures))
