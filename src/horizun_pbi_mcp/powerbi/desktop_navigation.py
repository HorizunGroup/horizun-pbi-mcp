"""Elegir pagina y zoom en una ventana de Desktop YA ABIERTA, con evidencia.

Por que existe
--------------
Fotografiar una pagina concreta exigia cerrar la sesion, escribir
`activePageName` en `pages.json`, reabrir el proyecto y refrescar: unos
cuarenta segundos por vuelta, y una escritura en el proyecto solo para mirar.
Con la ventana abierta, la pagina se puede elegir en la propia interfaz -la
pestaña de abajo- y "Ajustar a la pagina" vive en la cinta. Eso es lo que
hace este modulo, a traves del mismo helper de UI Automation que conduce el
cuadro de guardado, y sin tocar `pages.json`.

Lo que se promete y lo que no
-----------------------------
Cada accion devuelve `verified`, y solo es `true` con evidencia que hable de
LO QUE SE PIDIO:

- pagina: la pestaña dice `IsSelected`;
- zoom: el control dice que quedo activo -no lo expone en las versiones
  medidas- o Power BI publica un anuncio NUEVO de nivel de zoom
  ("Informe ampliado a 72 %") entre el instante anterior a pulsar y el
  posterior. Eso demuestra que el NIVEL de zoom cambio al pulsar, no que el
  modo resultante sea "ajustar a la pagina": "ajustar al ancho" tambien
  anunciaria. Es la evidencia mas especifica que la ventana expone.

Un cambio de pixeles NO es verificacion: la ventana tambien cambia al abrir
la cinta, al mover el foco, al terminar de pintar los datos o por una
animacion. Viaja como `visual_change`, evidencia de que algo se movio, y
nunca pone `verified` en `true` por si solo.

Y la falta de cambio tampoco demuestra lo contrario: si nada cambia, no se
afirma que "ya estaba ajustada"; se dice que no se pudo determinar.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

log = get_logger("desktop_navigation")


class DesktopNavigationError(PowerBIMCPError):
    """La pagina o el zoom pedidos no se pudieron establecer ni demostrar."""

    code = "desktop_navigation_failed"


def nombre_visible_de_pagina(documento: str | Path, page: str) -> Dict[str, Any]:
    """Resuelve `page` (id o nombre visible) al NOMBRE que muestra la pestaña.

    En un `.pbip` la pestaña lleva `displayName`, y lo que suele tener quien
    llama es el id de carpeta de la pagina. En un `.pbix` no hay carpeta que
    leer: se usa lo que llego, y la respuesta dice que no se pudo resolver.
    """
    doc = Path(str(documento))
    pedido = str(page or "").strip()
    if not pedido:
        raise ValidationError("Falta la pagina que se quiere seleccionar.",
                              details={"parameter": "page"})
    if doc.suffix.casefold() != ".pbip":
        return {"page": pedido, "display_name": pedido, "resolved_from": None,
                "page_id": None}

    from horizun_pbi_mcp.powerbi.desktop_capture import _definicion_de_report

    try:
        definicion = _definicion_de_report(doc, raiz=doc.parent)
    except Exception as exc:                              # noqa: BLE001
        log.debug("No se pudo leer el report de %s: %s", doc.name, exc)
        return {"page": pedido, "display_name": pedido, "resolved_from": None,
                "page_id": None}
    paginas = definicion / "pages"
    coincidencias = []
    if paginas.is_dir():
        for hija in sorted(paginas.iterdir()):
            page_json = hija / "page.json"
            if not page_json.is_file():
                continue
            try:
                datos = json.loads(page_json.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                continue
            visible = str(datos.get("displayName") or "")
            if hija.name == pedido or datos.get("name") == pedido:
                return {"page": pedido, "display_name": visible or pedido,
                        "resolved_from": "page_id", "page_id": hija.name}
            if visible.casefold() == pedido.casefold():
                coincidencias.append((hija.name, visible))
    if len(coincidencias) == 1:
        return {"page": pedido, "display_name": coincidencias[0][1],
                "resolved_from": "display_name",
                "page_id": coincidencias[0][0]}
    if len(coincidencias) > 1:
        raise ValidationError(
            f"El nombre visible '{pedido}' esta repetido en el informe; usa "
            "el id de la pagina.",
            details={"page": pedido,
                     "matches": [c[0] for c in coincidencias]})
    raise ValidationError(
        f"La pagina '{pedido}' no existe en el informe; usa el id de la "
        "pagina o su nombre visible exacto.",
        details={"page": pedido})


def contar_visuales(documento: str | Path, page_id: Optional[str]) -> Optional[int]:
    """Cuantos visuales declara esa pagina en disco. None si no se puede saber.

    Sirve para distinguir una captura uniforme de una pagina LEGITIMAMENTE
    vacia: con cero visuales, un lienzo de un solo color es lo esperable.
    """
    doc = Path(str(documento))
    if doc.suffix.casefold() != ".pbip" or not page_id:
        return None
    from horizun_pbi_mcp.powerbi.desktop_capture import _definicion_de_report

    try:
        definicion = _definicion_de_report(doc, raiz=doc.parent)
        carpeta = definicion / "pages" / page_id / "visuals"
        if not carpeta.is_dir():
            return 0
        return sum(1 for h in carpeta.iterdir()
                   if (h / "visual.json").is_file())
    except Exception:                                     # noqa: BLE001
        return None


#: Cuanto se deja repintar la ventana antes de volver a mirarla.
ESPERA_REPINTADO = 1.5


def huella_de_ventana(opened: Any) -> Optional[str]:
    """Huella de los pixeles de la ventana AHORA. `None` si no se pudo.

    No escribe ningun archivo: se usa para comparar el antes y el despues de
    una accion de vista. Para el zoom es una señal SECUNDARIA -el oraculo que
    decide es el anuncio de nivel que publica Power BI-, y sirve para poder
    decir que algo se movio cuando no hay anuncio. El control de "Ajustar a
    la pagina" es un `Button` sin `Toggle` ni `SelectionItem` que releer:
    medido contra Desktop real ofrece `Invoke` y `LegacyIAccessible`, y el
    estado de este ultimo no se ha examinado como oraculo.
    """
    from horizun_pbi_mcp.powerbi import desktop_capture

    pid = getattr(opened, "desktop_pid", None)
    documento = str(getattr(opened, "pbix_path", ""))
    if not pid:
        return None
    try:
        ventana = desktop_capture._choose_window(          # noqa: SLF001
            desktop_capture._enumerate_windows(int(pid)), documento)  # noqa: SLF001
        _ancho, _alto, pixeles = desktop_capture._capture_window_bgra(  # noqa: SLF001
            ventana.hwnd)
    except Exception as exc:                              # noqa: BLE001
        log.debug("Sin huella de ventana: %s", exc)
        return None
    return hashlib.sha256(pixeles).hexdigest()


def _cambio_de_vista(opened: Any, antes: Optional[str]) -> Optional[bool]:
    """Si la ventana se ve distinta que antes. `None` si no se pudo mirar.

    Es una señal DEBIL a proposito: dice que algo se movio en la ventana, no
    que se movio lo que se pidio. Quien la usa debe llamarla por su nombre.
    """
    if antes is None:
        return None
    time.sleep(ESPERA_REPINTADO)
    despues = huella_de_ventana(opened)
    if despues is None:
        return None
    return despues != antes


def navegar(opened: Any, *, page: Optional[str] = None,
            fit_to_page: bool = False,
            adapter: Optional[Any] = None) -> Dict[str, Any]:
    """Selecciona la pagina y/o el zoom en la ventana de `opened`.

    Devuelve un bloque por accion con `attempted`, `verified` y la evidencia
    del helper. No lanza si la accion no se pudo demostrar: lo declara. Solo
    lanza si ni siquiera se pudo intentar (sin identidad, sin helper).
    """
    pid = getattr(opened, "desktop_pid", None)
    started = getattr(opened, "desktop_started", None)
    documento = str(getattr(opened, "pbix_path", ""))
    if not pid:
        raise DesktopNavigationError(
            "La sesion abierta no identifica el proceso de Desktop; no se "
            "navega en una ventana sin saber cual es.",
            details={"pid": pid, "path": documento})
    if adapter is None:
        from horizun_pbi_mcp.powerbi import desktop_ui

        adapter = desktop_ui.adaptador_por_defecto()

    salida: Dict[str, Any] = {"desktop_pid": int(pid), "pages_json_touched": False}

    if page:
        resuelto = nombre_visible_de_pagina(documento, page)
        bloque: Dict[str, Any] = {"attempted": True, "requested": page,
                                  "display_name": resuelto["display_name"],
                                  "page_id": resuelto.get("page_id"),
                                  "resolved_from": resuelto.get("resolved_from"),
                                  "visual_count": contar_visuales(
                                      documento, resuelto.get("page_id"))}
        antes = huella_de_ventana(opened)
        try:
            respuesta = adapter.seleccionar_pagina(
                pid=int(pid), started=started,
                page_name=resuelto["display_name"])
            por_estado = bool(respuesta.get("verified"))
            cambio = _cambio_de_vista(opened, antes)
            bloque.update({
                "verified": por_estado,
                "verified_by": "control_state" if por_estado else None,
                "disambiguated_by": respuesta.get("disambiguated_by"),
                "container": respuesta.get("container"),
                # El lienzo cambia al cambiar de pagina. No sustituye a
                # `IsSelected` -no dice CUAL pagina quedo- pero cuando la
                # seleccion se demostro, confirma que ademas se repinto.
                "canvas_changed": cambio,
                "via": respuesta.get("via"),
                "selection_state": respuesta.get("selection_state"),
                "reason": respuesta.get("verification_reason"),
                "tabs_seen": respuesta.get("tabs_seen"),
                "attempts": respuesta.get("attempts"),
            })
        except PowerBIMCPError as exc:
            bloque.update({"verified": False, "error": exc.code,
                           "reason": str(exc.message)[:200],
                           "details": exc.details})
        salida["page"] = bloque

    if fit_to_page:
        bloque: Dict[str, Any] = {"attempted": True}
        antes = huella_de_ventana(opened)
        try:
            respuesta = adapter.ajustar_a_pagina(pid=int(pid), started=started)
            por_estado = bool(respuesta.get("verified"))
            por_zoom = bool(respuesta.get("zoom_level_changed"))
            # Los pixeles se miran siempre, pero como lo que son: señal de
            # que algo se movio. Nunca deciden `verified`.
            cambio = None if (por_estado or por_zoom) else _cambio_de_vista(
                opened, antes)
            bloque.update({
                "verified": por_estado or por_zoom,
                "verified_by": ("control_state" if por_estado
                                else "zoom_level_announced" if por_zoom
                                else None),
                "verified_means": SIGNIFICADO_DEL_ZOOM.get(
                    "control_state" if por_estado
                    else "zoom_level_announced" if por_zoom else ""),
                "zoom_level_announced": respuesta.get("zoom_announcements_new"),
                "visual_change": cambio,
                "via": respuesta.get("via"),
                "path": respuesta.get("path"),
                "state_after": respuesta.get("state_after"),
                "reason": (None if por_estado or por_zoom else
                           _motivo_zoom(respuesta, cambio)),
                "attempts": respuesta.get("attempts"),
            })
        except PowerBIMCPError as exc:
            bloque.update({"verified": False, "verified_by": None,
                           "verified_means": None, "error": exc.code,
                           "reason": str(exc.message)[:200],
                           "details": exc.details})
        salida["fit_to_page"] = bloque

    return salida


#: Que demuestra cada oraculo del zoom, dicho en la propia respuesta. Existe
#: porque `verified=true` invita a leer mas de lo que se midio: el anuncio de
#: nivel prueba que el zoom CAMBIO, no que el modo resultante sea el pedido.
SIGNIFICADO_DEL_ZOOM = {
    "control_state": (
        "el control declaro su estado despues de la accion: es el unico "
        "oraculo que identifica el modo"),
    "zoom_level_announced": (
        "Power BI anuncio un nivel de zoom nuevo entre el instante anterior a "
        "pulsar y el posterior: demuestra que el zoom CAMBIO al pulsar, no que "
        "el modo resultante sea \"Ajustar a la pagina\" -\"Ajustar al ancho\" "
        "tambien anunciaria-"),
}


def _motivo_zoom(respuesta: Dict[str, Any], cambio: Optional[bool]) -> str:
    """Por que el zoom quedo SIN DEMOSTRAR, sin inventarse la explicacion.

    Tres casos, y ninguno afirma que la vista ya estuviera ajustada: eso
    haria falta demostrarlo, y no hay con que.
    """
    base = respuesta.get("verification_reason") or "sin estado que releer"
    comun = ("Power BI no publico ningun nivel de zoom nuevo, que es la unica "
             "señal especifica disponible")
    if cambio is True:
        return (f"{base}; {comun}. La ventana SI cambio de aspecto, pero eso "
                "tambien lo produce abrir la cinta, mover el foco o terminar "
                "de pintar: no identifica el modo de vista")
    if cambio is False:
        return (f"{base}; {comun}, y la ventana tampoco cambio de aspecto. No "
                "se puede distinguir si la vista ya estaba ajustada o si la "
                "accion no llego")
    return (f"{base}; {comun}, y ademas no se pudo comparar el aspecto de la "
            "ventana")
