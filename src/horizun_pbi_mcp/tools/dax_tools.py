"""Tools de conexion y ejecucion DAX (Fases 1 y 2)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.powerbi import dax_runner, desktop_discovery
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.powerbi.clr_bootstrap import diagnostics
from horizun_pbi_mcp.tools._common import guard, ruta_de_proyecto as _ruta_de_proyecto


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
                         catalog: Optional[str] = None,
                         connection_string: Optional[str] = None) -> Dict[str, Any]:
        """Selecciona el modelo local activo para futuras operaciones.

        Si hay un solo modelo abierto no hace falta indicar nada. Si hay varios,
        pasa `port` **o** `connection_string` TAL CUAL lo devuelve
        `pbi_list_desktop_models` (`"Data Source=localhost:56057"`): lo que sale
        de esa tool entra en esta sin tener que extraer el puerto a mano.
        """
        def _impl():
            session = get_session()
            model = desktop_discovery.select_model(
                session, port=port, catalog=catalog,
                connection_string=connection_string)
            return {"active_model": model.to_dict()}
        return guard(_impl)

    @mcp.tool()
    def pbi_open_in_desktop(path: Optional[str] = None, timeout: int = 300,
                            reuse_open: bool = True,
                            select: bool = True,
                            pbip_path: Optional[str] = None) -> Dict[str, Any]:
        """Abre un .pbip o .pbix en Power BI Desktop y espera a que sirva el modelo.

        Cierra el ciclo de trabajo: despues de editar un proyecto, esto permite
        comprobar que ABRE de verdad y consultar sus medidas, sin pedirle al
        usuario que lo haga a mano. Un TMDL que no carga se manifiesta aqui.

        Espera a que el motor local aparezca y deje de crecer, identifica cual
        de las instancias corresponde a este archivo (el puerto es dinamico) y,
        con `select=true`, lo deja como modelo activo.

        Si el archivo ya estaba abierto se reutiliza esa sesion y no se toca
        nada (`reuse_open`). Nunca cierra una ventana del usuario.

        `path` (o `pbip_path`, como lo llama `pbi_session_info`) se puede
        omitir: entonces se abre el proyecto .pbip activo.

        Ojo: un .pbip recien abierto trae el modelo SIN DATOS. Refresca despues
        con pbi_refresh_model si vas a comprobar valores.
        """
        def _impl():
            from horizun_pbi_mcp.powerbi import desktop_launcher

            abierto = desktop_launcher.open_pbix(
                str(_ruta_de_proyecto(path, pbip_path)),
                timeout=timeout, reuse_open=reuse_open)
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
    def pbi_validate_desktop_render(path: str, timeout: int = 300,
                                    capture_timeout: int = 30,
                                    reuse_open: bool = True,
                                    page: Optional[str] = None,
                                    fit_to_page: bool = True) -> Dict[str, Any]:
        """Abre un .pbip/.pbix y captura su ventana real sin depender del foco.

        Es la comprobacion visual automatizable que complementa al validador
        PBIR: espera el modelo, identifica la ventana por PID y hora de inicio,
        y la renderiza con PrintWindow directamente a ``outputs/desktop_captures``.
        La captura no activa ni trae Desktop al frente, por lo que no puede
        fotografiar por accidente otra ventana que tape el informe.

        `page` (solo .pbip): captura ESA pagina (id o nombre visible), no la
        que quedo activa. `fit_to_page` (solo .pbip, activado por defecto):
        fuerza la vista "Ajustar a la pagina" para que salga el lienzo COMPLETO
        y no el tercio superior al zoom guardado. Ambos ajustan la vista antes
        de abrir y la restauran byte a byte al terminar; exigen que el proyecto
        no este ya abierto (con sesion reutilizada no se pueden aplicar: `page`
        falla y `fit_to_page` degrada a un aviso).

        Si la tool tuvo que abrir Desktop, lo cierra al terminar (tambien si la
        captura falla). Si el informe ya estaba abierto, reutiliza esa sesion y
        nunca cierra la ventana del usuario.
        """
        def _impl():
            from horizun_pbi_mcp.powerbi import desktop_capture, desktop_launcher

            pbix = Path(path).expanduser().resolve()
            avisos: List[str] = []
            vista: Optional[Dict[str, Any]] = None
            quiere_vista = bool(page) or fit_to_page
            if quiere_vista and pbix.suffix.casefold() != ".pbip":
                if page:
                    raise ValidationError(
                        "Elegir pagina de captura requiere un proyecto .pbip: "
                        "un .pbix compilado no se puede preparar sin editarlo.",
                        details={"path": str(pbix), "page": page})
                avisos.append("fit_to_page solo aplica a .pbip; la captura "
                              "sale al zoom guardado del .pbix.")
            elif quiere_vista:
                pid = desktop_launcher.proceso_con_archivo_abierto(pbix)
                if pid and page:
                    raise ValidationError(
                        "El proyecto ya esta abierto en Desktop y la pagina "
                        "activa no se puede cambiar desde fuera; cierra la "
                        "sesion (pbi_close_desktop) y reintenta.",
                        details={"path": str(pbix), "pid": pid, "page": page,
                                 "reason": "desktop_open_page_unavailable"})
                if pid:
                    avisos.append(
                        "Sesion reutilizada: no se pudo forzar 'Ajustar a la "
                        "pagina'; la captura sale al zoom actual de Desktop.")
                else:
                    vista = desktop_capture.preparar_vista_de_captura(
                        pbix, page=page, fit_to_page=fit_to_page)

            try:
                opened = desktop_launcher.open_pbix(
                    path, timeout=timeout, reuse_open=reuse_open)
            except BaseException:
                # Desktop nunca llego a abrir: la vista temporal no puede
                # quedarse escrita como si fuera una edicion del informe.
                if vista is not None:
                    vista["restore"]()
                raise
            close_result: Dict[str, Any] = {
                "closed": False,
                "reason": "la sesion ya estaba abierta; no se toca",
            }
            result: Optional[Dict[str, Any]] = None
            try:
                capture = desktop_capture.capture_opened(
                    opened, timeout=capture_timeout)
                result = {
                    "path": opened.pbix_path,
                    "instance": opened.instance,
                    "desktop_pid": opened.desktop_pid,
                    "launched_by_us": opened.launched_by_us,
                    "reused_open_session": not opened.launched_by_us,
                    "waited_seconds": opened.waited_seconds,
                    "capture": capture,
                }
                if vista is not None:
                    result["capture_view"] = {"page_id": vista["page_id"],
                                              "changes": vista["changes"]}
            finally:
                # Incluso ante KeyboardInterrupt/SystemExit se intenta compensar
                # la ventana que esta llamada abrio. `close` revalida nombre y
                # create_time antes de terminar nada.
                if opened.launched_by_us:
                    try:
                        close_result = desktop_launcher.close(opened)
                    except Exception as exc:  # noqa: BLE001
                        # No enmascara un fallo de captura con un segundo fallo
                        # de compensacion. Si la captura si termino, el warning
                        # deja claro que no es seguro reintentar a ciegas.
                        close_result = {
                            "closed": False,
                            "reason": "desktop_close_failed",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        }
                # La vista era para la foto, no una edicion: se restaura
                # byte a byte DESPUES de cerrar Desktop.
                if vista is not None:
                    for ruta in vista["restore"]():     # pragma: no cover
                        avisos.append(
                            "No se pudo restaurar la vista de captura de "
                            f"{ruta}; revierte activePageName/displayOption "
                            "a mano.")
            assert result is not None
            result["desktop_close"] = close_result
            if opened.launched_by_us and not close_result.get("closed"):
                avisos.append(
                    "La captura termino, pero Desktop no se cerro porque su "
                    "identidad ya no pudo verificarse; no se termino otro proceso.")
            if avisos:
                result.setdefault("warnings", []).extend(avisos)
            return result
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

    @mcp.tool()
    def pbi_close_desktop(path: Optional[str] = None, confirm: bool = False,
                          pbip_path: Optional[str] = None,
                          request_id: str = "") -> Dict[str, Any]:
        """Cierra la instancia de Power BI Desktop que sirve ESE proyecto.

        Es la salida que faltaba del ciclo editar-abrir-mirar-editar: escribir
        el TMDL exige Desktop cerrado, y no habia forma de cerrarlo desde aqui
        -tocaba matar el proceso a mano en PowerShell-.

        Cierra SOLO la instancia con ese archivo abierto, verificando la
        identidad del proceso (nombre + hora de arranque, nunca el PID a
        secas). Al final re-comprueba que el archivo ya no este abierto y lo
        dice en `verified_closed`.

        DESTRUCTIVA: los cambios SIN GUARDAR de esa ventana se pierden -y en
        un .pbip eso incluye los datos refrescados de la sesion-. Por eso
        exige `confirm=true`. Si el archivo no estaba abierto, no hace nada y
        lo dice (`was_open: false`).

        `path` (o `pbip_path`) se puede omitir: se cierra el proyecto activo,
        que es el que el servidor ya conoce.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Cerrar Desktop descarta lo no guardado de esa ventana "
                    "(y en .pbip, los datos refrescados). Pasa confirm=true "
                    "si es lo que quieres.")
            from horizun_pbi_mcp.powerbi import desktop_launcher

            return desktop_launcher.close_desktop_by_path(
                str(_ruta_de_proyecto(path, pbip_path)))
        return guard(_impl)
