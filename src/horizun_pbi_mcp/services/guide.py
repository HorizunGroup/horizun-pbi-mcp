"""Por donde se empieza. Un punto de entrada para 127 tools.

El problema que cierra
----------------------
Ciento veintisiete tools con buen nombre y buena descripcion siguen siendo
ciento veintisiete tools. Quien llega no sabe si primero abre un proyecto o primero carga datos, ni
que la escritura de TMDL exige Power BI Desktop CERRADO —y eso no lo dice
ninguna lista alfabetica—. El catalogo estaba completo y el camino no existia.

Aqui no se documenta nada nuevo: se MIRA el estado real y se responde a «¿y
ahora que?» con tres o cuatro pasos concretos, cada uno con el nombre exacto de
la tool y el motivo. Una lista de 127 opciones no es una respuesta a esa
pregunta; tres si.

La regla que lo mantiene util
-----------------------------
Cada paso dice **por que** toca ahora. Un paso sin motivo es una orden, y una
orden no se puede discutir ni saltar con criterio. Y el estado se comprueba, no
se supone: si el proyecto esta abierto en Desktop se dice, porque cambia lo que
se puede hacer a continuacion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("guide")


def _paso(tool: str, porque: str, ejemplo: Optional[Dict[str, Any]] = None,
          ) -> Dict[str, Any]:
    salida: Dict[str, Any] = {"tool": tool, "why": porque}
    if ejemplo is not None:
        salida["example_args"] = ejemplo
    return salida


#: Tareas frecuentes -> la secuencia que las resuelve. Es el indice que
#: convierte «quiero X» en tools, sin tener que leerse el catalogo.
TAREAS: List[Dict[str, Any]] = [
    {"task": "Partir de cero con unos archivos de datos",
     "steps": ["pbi_create_pbip_project", "pbi_add_table_from_file",
               "pbi_create_measure", "pbi_apply_design_system",
               "pbi_compose_page", "pbi_validate_tmdl", "pbi_open_in_desktop"]},
    {"task": "Trabajar sobre un .pbip que ya existe",
     "steps": ["pbi_open_pbip_project", "pbi_model_summary", "pbi_list_report_pages",
               "pbi_page_building_blocks"]},
    {"task": "Convertir un .pbix heredado",
     "steps": ["pbi_inspect_pbix", "pbi_convert_pbix_to_pbip",
               "pbi_validate_tmdl"]},
    {"task": "Anadir una pagina con criterio de diseno",
     "steps": ["pbi_list_design_systems", "pbi_apply_design_system",
               "pbi_compose_page"]},
    {"task": "Anadir una pagina a medida, controlando cada visual",
     "steps": ["pbi_page_building_blocks", "pbi_validate_page_spec",
               "pbi_preview_page_spec", "pbi_apply_page_spec"]},
    {"task": "Consultar datos del modelo en vivo",
     "steps": ["pbi_list_desktop_models", "pbi_select_model", "pbi_run_dax"]},
    {"task": "Revisar la salud del proyecto",
     "steps": ["pbi_audit_project", "pbi_detect_layout_issues",
               "pbi_validate_tmdl"]},
    {"task": "Comprobar que abre antes de entregar",
     "steps": ["pbi_validate_tmdl", "pbi_audit_report_only",
               "pbi_open_in_desktop"]},
    {"task": "Deshacer algo que salio mal",
     "steps": ["pbi_list_pending_journals", "pbi_recover_from_journal"]},
]


def _contar_modelo(active) -> Dict[str, Any]:
    """Tablas y medidas del TMDL. Nunca lanza: esto es un diagnostico."""
    try:
        from horizun_pbi_mcp.pbip import tmdl_reader

        modelo = tmdl_reader.read_semantic_model(active, strict=False)
        return {"tables": len(modelo.get("tables") or []),
                "measures": len(modelo.get("measures") or [])}
    except Exception as exc:                                 # noqa: BLE001
        log.info("No se pudo leer el modelo para la guia: %s", exc)
        return {"tables": None, "measures": None}


def _contar_informe(active) -> Dict[str, Any]:
    """Paginas y visuales.

    Los visuales importan tanto como las paginas: un proyecto recien creado
    trae una pagina vacia, asi que contar solo paginas responderia «ya tienes
    una» a quien todavia no tiene nada.
    """
    try:
        from horizun_pbi_mcp.pbip import pbir_reader

        paginas = pbir_reader.list_pages(active, strict=False)
        visuales = 0
        for p in paginas:
            try:
                visuales += len(pbir_reader.list_visuals(active, p["name"]))
            except Exception:                                # noqa: BLE001
                pass
        return {"pages": len(paginas), "visuals": visuales}
    except Exception as exc:                                 # noqa: BLE001
        log.info("No se pudo leer el informe para la guia: %s", exc)
        return {"pages": None, "visuals": None}


def _sin_proyecto(session) -> Dict[str, Any]:
    """Lo primero es siempre lo mismo: decidir sobre que se trabaja."""
    pasos = [
        _paso("pbi_open_pbip_project",
              "Si ya tienes un .pbip, empieza por abrirlo: casi todo lo demas "
              "actua sobre el proyecto activo.",
              {"path": "C:/ruta/a/MiInforme.pbip"}),
        _paso("pbi_create_pbip_project",
              "Si partes de cero y solo tienes archivos de datos, esto crea un "
              "proyecto vacio pero valido en el que cargarlos.",
              {"out_dir": "C:/pbip", "name": "MiTablero"}),
        _paso("pbi_convert_pbix_to_pbip",
              "Si lo que tienes es un .pbix, conviertelo primero: sobre un "
              ".pbix no se puede editar nada.",
              {"pbix_path": "C:/ruta/a/Informe.pbix"}),
    ]
    if session.active_model is not None:
        pasos.insert(0, _paso(
            "pbi_run_dax",
            "Hay un modelo en vivo seleccionado: puedes consultarlo ya, aunque "
            "no haya proyecto en disco. Para EDITAR hace falta el .pbip.",
            {"query": "EVALUATE TOPN(10, Ventas)"}))
    return {
        "situation": ("No hay ningun proyecto .pbip activo, asi que las tools "
                      "de edicion no tienen sobre que actuar."),
        "next_steps": pasos,
    }


def situacion(session) -> Dict[str, Any]:
    """Donde estas y que toca ahora. No escribe nada."""
    active = session.active_pbip
    if active is None:
        base = _sin_proyecto(session)
        return {**base, "project": None, "common_tasks": TAREAS}

    from horizun_pbi_mcp.services import project_state

    estado = project_state.detect(active)
    modelo = _contar_modelo(active) if active.has_tmdl else {"tables": None,
                                                             "measures": None}
    informe = (_contar_informe(active) if active.has_pbir
               else {"pages": None, "visuals": None})

    # El brief es el techo de todo lo demas: si existe, cada paso sirve a un
    # proposito dicho por el dueño; si no, se sugiere definirlo ANTES de
    # construir. Nunca lanza aqui: la guia es un diagnostico.
    try:
        from horizun_pbi_mcp.services import brief as brief_service

        el_brief = brief_service.read_brief(active)
    except Exception:                                    # noqa: BLE001
        el_brief = None

    proyecto = {
        "path": active.pbip_path,
        "name": active.report_name,
        "has_model": bool(active.has_tmdl),
        "has_report": bool(active.has_pbir),
        "tables": modelo["tables"],
        "measures": modelo["measures"],
        "pages": informe["pages"],
        "visuals": informe["visuals"],
        "desktop": estado.to_dict(),
        "brief": ({"purpose": el_brief.get("purpose"),
                   "audience": el_brief.get("audience")}
                  if el_brief else None),
    }

    pasos: List[Dict[str, Any]] = []
    frases: List[str] = [f"Proyecto activo: {Path(active.pbip_path).name}."]
    if el_brief:
        frases.append(f"Proposito declarado: {el_brief['purpose']}")
    else:
        pasos.append(_paso(
            "pbi_get_brief",
            "El tablero no tiene brief de intencion: nadie ha dicho PARA QUE "
            "existe. Pregunta al usuario y definelo antes de construir; la "
            "propuesta y el sistema de diseño lo leen."))

    # --- lo que BLOQUEA va primero: no tiene sentido sugerir algo imposible --
    from horizun_pbi_mcp.services import coherencia

    coh = coherencia.check(session)
    proyecto["coherence"] = {"state": coh["state"], "reason": coh["reason"]}
    if coh["state"] == coherencia.DIFFERENT:
        # Antes que cualquier otra cosa: el modelo que se consulta NO describe
        # el informe que se va a escribir, y todo lo demas se leeria como si si.
        frases.append(
            "AVISO GRAVE: el modelo en vivo y este proyecto son archivos "
            "DISTINTOS. Lo que consultes con DAX no describe el informe que "
            "vas a escribir, y las escrituras de informe estan bloqueadas.")
        pasos.append(_paso(
            "pbi_list_desktop_models",
            "Resuelve la divergencia antes de nada: elige el Desktop que "
            "tiene abierto ESTE proyecto, o abre el proyecto del modelo que "
            "estas consultando."))
    elif coh["state"] == coherencia.UNKNOWN:
        frases.append(
            "No se pudo comprobar que el modelo en vivo corresponda a este "
            "proyecto (permisos). No se bloquea nada, pero conviene verificarlo "
            "antes de escribir.")

    if estado.state == project_state.OPEN:
        frases.append(
            "Esta ABIERTO en Power BI Desktop, asi que el modelo (TMDL) no se "
            "puede escribir: Desktop reescribiria los cambios al guardar.")
        pasos.append(_paso(
            "pbi_health_check",
            "Confirma el estado y cierra Desktop antes de tocar el modelo. El "
            "informe (PBIR) tampoco conviene tocarlo con el abierto."))

    # --- despues, el hueco mas grande que tenga el proyecto ------------------
    if not active.has_tmdl:
        frases.append("No tiene modelo propio (solo informe).")
        pasos.append(_paso(
            "pbi_audit_report_only",
            "Es un informe con conexion en vivo a un dataset publicado. No hay "
            "TMDL que validar y eso NO es un fallo; se audita el informe."))
    elif not modelo["tables"]:
        frases.append("El modelo esta vacio.")
        pasos.append(_paso(
            "pbi_add_table_from_file",
            "Carga los datos primero: sin tablas no hay medidas que escribir "
            "ni campos que poner en un visual.",
            {"path": "C:/datos/ventas.csv"}))
    elif not modelo["measures"]:
        frases.append(f"Tiene {modelo['tables']} tabla(s) y ninguna medida.")
        pasos.append(_paso(
            "pbi_create_measure",
            "Un tablero se construye sobre medidas, no sobre columnas sueltas: "
            "son las que se pueden poner en una tarjeta o un grafico.",
            {"table": "Ventas", "name": "Importe Total",
             "expression": "SUM(Ventas[Importe])"}))
    elif not informe["visuals"]:
        # Se mira el numero de VISUALES, no el de paginas: un proyecto recien
        # creado trae una pagina vacia, y responderle "ya tienes una" a quien
        # todavia no tiene nada es la clase de respuesta que hace desconfiar.
        frases.append(
            f"Tiene {modelo['measures']} medida(s) y "
            + (f"{informe['pages']} pagina(s), todas vacias."
               if informe["pages"] else "ninguna pagina."))
        pasos.append(_paso(
            "pbi_list_design_systems",
            "Elige sobre que rejilla y con que tema se va a construir ANTES de "
            "la primera pagina: cambiarlo despues obliga a recolocarlo todo."))
        pasos.append(_paso(
            "pbi_compose_page",
            "Compone una pagina entera —titulo, indicadores, protagonista— "
            "colocada sobre la rejilla del sistema.",
            {"system": "informe", "title": "Resumen",
             "kpis": ["[Importe Total]"]}))
    else:
        frases.append(f"Tiene {modelo['tables']} tabla(s), "
                      f"{modelo['measures']} medida(s) y {informe['pages']} "
                      f"pagina(s) con {informe['visuals']} visual(es).")
        pasos.append(_paso(
            "pbi_audit_project",
            "Con el proyecto ya montado, lo util es ver que esta flojo: "
            "modelo, informe y layout de una pasada."))
        pasos.append(_paso(
            "pbi_compose_page",
            "Para anadir otra pagina con el mismo criterio que las que ya hay.",
            {"system": "informe", "title": "Detalle"}))

    # --- y el cierre, que casi nadie hace y es el que evita el ridiculo ------
    if active.has_tmdl:
        pasos.append(_paso(
            "pbi_validate_tmdl",
            "Responde «¿esto abrira?» sin abrirlo. Es mas barato que "
            "descubrirlo cuando lo abre otro."))
    pasos.append(_paso(
        "pbi_open_in_desktop",
        "Y para verlo de verdad: abre el proyecto y espera al motor. Que "
        "valide no es lo mismo que que se vea bien."))

    return {"situation": " ".join(frases), "project": proyecto,
            "next_steps": pasos, "common_tasks": TAREAS}
