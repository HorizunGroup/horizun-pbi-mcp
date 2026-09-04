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
Cada accion devuelve `verified`. Solo es `true` cuando la interfaz releyo el
estado que se pidio (la pestaña dice `IsSelected`, el control de zoom dice
que esta activo). Si Desktop no expone ese estado, se devuelve `false` con la
razon exacta, y quien captura decide: capturar la pagina equivocada en
silencio es peor que decir que no se pudo demostrar.
"""
from __future__ import annotations

import json
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
        try:
            respuesta = adapter.seleccionar_pagina(
                pid=int(pid), started=started,
                page_name=resuelto["display_name"])
            bloque.update({
                "verified": bool(respuesta.get("verified")),
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
        bloque = {"attempted": True}
        try:
            respuesta = adapter.ajustar_a_pagina(pid=int(pid), started=started)
            bloque.update({
                "verified": bool(respuesta.get("verified")),
                "via": respuesta.get("via"),
                "path": respuesta.get("path"),
                "state_after": respuesta.get("state_after"),
                "reason": respuesta.get("verification_reason"),
                "attempts": respuesta.get("attempts"),
            })
        except PowerBIMCPError as exc:
            bloque.update({"verified": False, "error": exc.code,
                           "reason": str(exc.message)[:200],
                           "details": exc.details})
        salida["fit_to_page"] = bloque

    return salida
