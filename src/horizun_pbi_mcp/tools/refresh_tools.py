"""Tool de refresh local del modelo (Fase 5)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.powerbi import refresh
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.tools._common import guard, guard_mutation, ruta_de_proyecto


def register(mcp) -> None:
    @mcp.tool()
    def pbi_refresh_model(type: str = "full",
                          tables: Optional[List[str]] = None,
                          timeout_seconds: Optional[int] = None,
                          confirm: bool = False,
                          request_id: str = "") -> Dict[str, Any]:
        """Refresca el modelo LOCAL abierto en Power BI Desktop (no el Service).

        `type`: full | calculate | clear_values (tambien automatic | data_only).
        `tables`: lista opcional de tablas a refrescar; si se omite, todo el modelo.
        Los errores de credenciales/origen se reportan.

        `timeout_seconds` (600 por defecto, `0` lo desactiva): un refresh
        lanzado por XMLA **no puede mostrar el dialogo de credenciales** de
        Desktop, asi que un origen sin credenciales guardadas deja al motor
        esperando para siempre y no hay ninguna ventana que cerrar. Al
        agotarse el plazo se pide la cancelacion al motor y se devuelve
        `refresh_timeout` enumerando los origenes que REQUIEREN credenciales
        -no si las tienen: eso Desktop no lo expone- y si la cancelacion se
        confirmo o el comando pudo quedar corriendo.

        Devuelve estado, duracion y **`rows_by_table`**: cuantas filas quedaron
        en cada tabla refrescada. Un refresh puede terminar en 'ok' y haber
        cargado CERO filas -credenciales que devuelven vacio, un filtro de
        fecha que no alcanza nada, un origen que cambio de esquema-, asi que
        las tablas vacias salen ademas en `warnings`. Si no se pudo contar, se
        dice; no se inventa el numero.

        En un proyecto **.pbip los datos NO se guardan**: viven en la sesion de
        Desktop y al reabrir hay que refrescar otra vez. Lo que persiste al
        guardar es la definicion (TMDL + PBIR).

        **Exige `confirm=true` desde 2.0.0.** Hasta entonces era la unica tool
        `destructiveHint` sin confirmacion junto con `pbi_open_and_refresh`: un
        agente que decide por «¿tiene `confirm`?» no veia nada que preguntar.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Refrescar bloquea el modelo durante minutos y descarta lo "
                    "que hubiera en memoria sin guardar. Pasa confirm=true.")
            return refresh.refresh_model(get_session(), type, tables,
                                         timeout_seconds)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_open_and_refresh(path: Optional[str] = None, timeout: int = 300,
                             reuse_open: bool = True, type: str = "full",
                             tables: Optional[List[str]] = None,
                             refresh_timeout_seconds: Optional[int] = None,
                             pbip_path: Optional[str] = None,
                             confirm: bool = False,
                             request_id: str = "",
                             project_path: Optional[str] = None,
                             page: Optional[str] = None,
                             fit_to_page: bool = False) -> Dict[str, Any]:
        """Abre el proyecto en Power BI Desktop y lo refresca, en una llamada.

        Es la secuencia real de trabajo y siempre eran dos llamadas de unos
        catorce segundos cada una, porque un `.pbip` recien abierto trae el
        modelo SIN DATOS: abrirlo sin refrescar no sirve para comprobar nada.

        `path` y `pbip_path` son el mismo parametro -el segundo es como lo
        llama `pbi_session_info`-, y se pueden omitir los dos: entonces se usa
        el proyecto .pbip activo.

        Devuelve lo mismo que las dos por separado, incluido `rows_by_table`.
        Si el archivo ya estaba abierto se reutiliza esa sesion (`reuse_open`).

        Si el refresh falla, la ventana se DEJA ABIERTA: ya cargo bien, y
        cerrarla borraria justo el contexto que hace falta para ver por que
        fallo. Sale en `desktop_left_open`.

        `page` y `fit_to_page` (opcionales) eligen, DESPUES de refrescar, la
        pestaña de esa pagina y la vista "Ajustar a la pagina" en la propia
        ventana, por UI Automation, sin tocar `pages.json`. Mueven la ventana
        igual que el refresh la vacia: los cubre el mismo `confirm=true`. La
        respuesta trae `navigation` con `verified` por accion: lo que no se
        pudo demostrar se dice en `warnings`, no se da por hecho.

        **Exige `confirm=true` desde 2.0.0.** Abre una aplicacion y refresca:
        los dos efectos son visibles y el segundo descarta lo que hubiera en
        memoria sin guardar.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Abrir Desktop y refrescar descarta lo que hubiera en "
                    "memoria sin guardar. Pasa confirm=true.")
            from horizun_pbi_mcp.powerbi import desktop_discovery, desktop_launcher

            abierto = desktop_launcher.open_pbix(
                str(ruta_de_proyecto(path, pbip_path, project_path)),
                timeout=timeout, reuse_open=reuse_open)
            salida: Dict[str, Any] = {
                "path": abierto.pbix_path,
                "instance": abierto.instance,
                "desktop_pid": abierto.desktop_pid,
                "launched_by_us": abierto.launched_by_us,
                "reused_open_session": not abierto.launched_by_us,
                "waited_seconds": abierto.waited_seconds,
            }
            session = get_session()
            try:
                modelo = desktop_discovery.select_model(
                    session, port=abierto.instance.get("port"))
                salida["active_model"] = modelo.to_dict()
            except BaseException:
                # Sin modelo seleccionado no hay refresh posible, asi que esta
                # llamada no logro nada: se compensa el efecto que produjo.
                if abierto.launched_by_us:
                    desktop_launcher.close(abierto)
                raise

            # `timeout` es el plazo para ABRIR Desktop; el del refresh es otro
            # y se nombra aparte para que no se confundan.
            salida["refresh"] = refresh.refresh_model(
                session, type, tables, refresh_timeout_seconds)
            salida["desktop_left_open"] = True
            if page or fit_to_page:
                from horizun_pbi_mcp.powerbi import desktop_navigation

                navegacion = desktop_navigation.navegar(
                    abierto, page=page, fit_to_page=fit_to_page)
                salida["navigation"] = navegacion
                avisos: List[str] = []
                bloque = navegacion.get("page")
                if bloque is not None and not bloque.get("verified"):
                    avisos.append(
                        "La pagina pedida no se pudo demostrar en la ventana "
                        f"abierta ({bloque.get('reason')}); la pagina activa "
                        "puede ser otra.")
                bloque = navegacion.get("fit_to_page")
                if bloque is not None and not bloque.get("verified"):
                    avisos.append(
                        "No se pudo demostrar 'Ajustar a la pagina' "
                        f"({bloque.get('reason')}).")
                if avisos:
                    salida.setdefault("warnings", []).extend(avisos)
            return salida
        return guard_mutation(_impl)
