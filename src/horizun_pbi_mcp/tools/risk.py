"""Clase de riesgo de cada tool, y su traduccion a `ToolAnnotations` del MCP.

El problema que cierra
----------------------
Para un cliente MCP, `pbi_list_measures` y `pbi_delete_page` eran
indistinguibles: ambas llegaban sin un solo metadato. El protocolo tiene desde
hace versiones un sitio para decirlo —`annotations`— y las 118 tools lo dejaban
vacio. Consecuencia practica: el cliente no puede auto-permitir una lectura ni
avisar distinto ante un borrado, asi que o pregunta por todo o no pregunta por
nada. Las dos opciones son malas.

Aqui NO se inventa una taxonomia nueva: se usa la que el repositorio ya tenia
escrita en `docs/TOOL_CATALOG.md`, y se le anaden dos clases que esa tabla no
distinguia y que si cambian lo que el cliente debe hacer.

Por que la tabla es explicita y no deducida
-------------------------------------------
Deducirla de `guard`/`guard_mutation` habria sido mas corto, pero equivocarse
hacia el lado facil —marcar de solo lectura algo que escribe— es justo el error
que un cliente convertiria en "esto se puede ejecutar sin preguntar". Asi que la
tabla se escribe entera y a mano, y es `tests/test_tool_annotations.py` quien la
contrasta contra la evidencia AST del codigo. Si alguien anade una tool y no la
clasifica, la suite falla; si alguien clasifica de `read_only` algo que llama a
`guard_mutation`, la suite falla. La tabla no puede quedarse desfasada en
silencio.

Las clases
----------
- `read_only`               No toca nada del usuario y no escribe ningun fichero.
- `read_only_emits_file`    No toca el proyecto, pero DEJA un fichero en
                            `outputs/` (documentacion, informe, export, captura
                            de una vista previa). No es "solo lectura" para el
                            protocolo: `readOnlyHint` significa que la tool no
                            modifica su entorno, y crear un fichero lo modifica.
- `side_effect_external`    No escribe en el proyecto, pero actua sobre la
                            maquina: abre —y a veces cierra— Power BI Desktop.
- `write_reversible`        Escribe con transaccion y journal; revierte si falla.
- `write_destructive`       Exige `confirm=true`. Borra o reemplaza.
- `write_irreversible`      No hay vuelta atras posible (`pbi_refresh_model`).

Sobre `idempotentHint`
----------------------
Se declara `False` en todo lo que escribe, aunque el servidor tenga idempotencia
por `request_id`. Esa proteccion es **opt-in del cliente**: sin `request_id`, dos
llamadas iguales a `pbi_create_visual` crean dos visuales. Anunciar
`idempotent=True` seria prometer una garantia que depende de que quien llama haga
su parte.

`openWorldHint` es `False` en las 127: este servidor habla con el disco local y
con un Power BI Desktop local. No hay dominio abierto.
"""
from __future__ import annotations

from typing import Any, Dict

READ_ONLY = "read_only"
READ_ONLY_EMITS_FILE = "read_only_emits_file"
SIDE_EFFECT_EXTERNAL = "side_effect_external"
WRITE_REVERSIBLE = "write_reversible"
WRITE_DESTRUCTIVE = "write_destructive"
WRITE_IRREVERSIBLE = "write_irreversible"

#: Clases que NO pueden usar `guard_mutation`. El test lo verifica por AST.
CLASES_DE_LECTURA = frozenset(
    {READ_ONLY, READ_ONLY_EMITS_FILE, SIDE_EFFECT_EXTERNAL})

#: Clases que el cliente debe tratar como destructivas.
CLASES_DESTRUCTIVAS = frozenset({WRITE_DESTRUCTIVE, WRITE_IRREVERSIBLE})


#: Clase de riesgo de cada tool registrada. Una entrada por tool, sin excepciones:
#: `test_toda_tool_registrada_esta_clasificada` falla si falta una.
RISK_BY_TOOL: Dict[str, str] = {
    # -- read_only (51): ni escriben fichero ni tocan el proyecto --
    "pbi_analyze_model_quality": READ_ONLY,
    "pbi_audit_model": READ_ONLY,
    "pbi_audit_report_only": READ_ONLY,
    "pbi_capabilities": READ_ONLY,
    "pbi_column_dependencies": READ_ONLY,
    "pbi_compare_live_to_pbip": READ_ONLY,
    "pbi_detect_layout_issues": READ_ONLY,
    # DAX de solo lectura contra el modelo vivo; sin ficheros.
    "pbi_diagnose_data": READ_ONLY,
    "pbi_check_contract": READ_ONLY,
    "pbi_diff_page_spec": READ_ONLY,
    "pbi_generate_page_spec": READ_ONLY,
    "pbi_get_object": READ_ONLY,
    "pbi_get_visual": READ_ONLY,
    "pbi_health_check": READ_ONLY,
    "pbi_inspect_journal": READ_ONLY,
    "pbi_inspect_pbix": READ_ONLY,
    "pbi_list_audit_rules": READ_ONLY,
    "pbi_list_autofix_rules": READ_ONLY,
    "pbi_list_bookmarks": READ_ONLY,
    "pbi_list_convertible_pbix": READ_ONLY,
    "pbi_list_design_systems": READ_ONLY,
    "pbi_list_desktop_models": READ_ONLY,
    "pbi_list_hierarchies": READ_ONLY,
    "pbi_list_measures": READ_ONLY,
    "pbi_list_page_presets": READ_ONLY,
    "pbi_list_partitions": READ_ONLY,
    "pbi_list_pending_journals": READ_ONLY,
    "pbi_list_perspectives": READ_ONLY,
    "pbi_list_relationships": READ_ONLY,
    "pbi_list_report_pages": READ_ONLY,
    "pbi_list_report_resources": READ_ONLY,
    "pbi_list_roles": READ_ONLY,
    "pbi_list_tables": READ_ONLY,
    "pbi_list_themes": READ_ONLY,
    "pbi_list_visuals": READ_ONLY,
    "pbi_measure_dependencies": READ_ONLY,
    "pbi_model_summary": READ_ONLY,
    # Cambian el estado de SESION (proyecto/modelo activo), no artefactos del
    # usuario. La sesion es del servidor y se reconstruye sola.
    "pbi_open_pbip_project": READ_ONLY,
    "pbi_select_model": READ_ONLY,
    "pbi_page_building_blocks": READ_ONLY,
    "pbi_plan_audit_fixes": READ_ONLY,
    "pbi_plan_change": READ_ONLY,
    "pbi_propose_dashboard": READ_ONLY,
    "pbi_report_capabilities": READ_ONLY,
    "pbi_search_model": READ_ONLY,
    "pbi_session_info": READ_ONLY,
    "pbi_get_brief": READ_ONLY,
    "pbi_start_here": READ_ONLY,
    "pbi_test_connection": READ_ONLY,
    "pbi_validate_generated_page": READ_ONLY,
    "pbi_validate_measures": READ_ONLY,
    "pbi_validate_page_spec": READ_ONLY,
    "pbi_validate_pbip_project": READ_ONLY,
    "pbi_validate_tmdl": READ_ONLY,

    # -- read_only_emits_file (9): dejan un fichero en outputs/ --
    "pbi_audit_project": READ_ONLY_EMITS_FILE,
    "pbi_document_model": READ_ONLY_EMITS_FILE,
    "pbi_document_report_layout": READ_ONLY_EMITS_FILE,
    "pbi_export_page_html": READ_ONLY_EMITS_FILE,
    "pbi_generate_technical_documentation": READ_ONLY_EMITS_FILE,
    "pbi_preview_page_spec": READ_ONLY_EMITS_FILE,
    "pbi_preview_spec_html": READ_ONLY_EMITS_FILE,
    "pbi_profile_data": READ_ONLY_EMITS_FILE,
    "pbi_run_dax": READ_ONLY_EMITS_FILE,

    # -- side_effect_external (2): abren/cierran Power BI Desktop --
    "pbi_open_in_desktop": SIDE_EFFECT_EXTERNAL,
    "pbi_validate_desktop_render": SIDE_EFFECT_EXTERNAL,

    # -- write_reversible (48) --
    "pbi_add_custom_visual": WRITE_REVERSIBLE,
    "pbi_add_image_resource": WRITE_REVERSIBLE,
    "pbi_add_table_from_file": WRITE_REVERSIBLE,
    "pbi_add_table_from_source": WRITE_REVERSIBLE,
    # El contrato del puerto es un artefacto del proyecto.
    "pbi_define_port_contract": WRITE_REVERSIBLE,
    "pbi_align_visuals": WRITE_REVERSIBLE,
    "pbi_apply_design_system": WRITE_REVERSIBLE,
    "pbi_reflow_pages": WRITE_REVERSIBLE,
    # El brief es un artefacto del proyecto, con journal como todo.
    "pbi_define_brief": WRITE_REVERSIBLE,
    "pbi_apply_page_spec": WRITE_REVERSIBLE,
    "pbi_apply_theme": WRITE_REVERSIBLE,
    "pbi_arrange_visuals": WRITE_REVERSIBLE,
    "pbi_backup_pbip_project": WRITE_REVERSIBLE,
    "pbi_build_dashboard": WRITE_REVERSIBLE,
    "pbi_build_evm_page": WRITE_REVERSIBLE,
    "pbi_build_executive_page": WRITE_REVERSIBLE,
    "pbi_compose_page": WRITE_REVERSIBLE,
    "pbi_convert_pbix_to_pbip": WRITE_REVERSIBLE,
    "pbi_copy_visual_format": WRITE_REVERSIBLE,
    "pbi_create_bookmark": WRITE_REVERSIBLE,
    "pbi_create_calculated_column": WRITE_REVERSIBLE,
    "pbi_create_calculated_table": WRITE_REVERSIBLE,
    "pbi_create_hierarchy": WRITE_REVERSIBLE,
    "pbi_create_html_visual": WRITE_REVERSIBLE,
    "pbi_create_measure": WRITE_REVERSIBLE,
    # Renombra en modelo E informe dentro de una transaccion con journal.
    "pbi_rename_measure": WRITE_REVERSIBLE,
    "pbi_create_page_from_spec": WRITE_REVERSIBLE,
    "pbi_create_pbip_project": WRITE_REVERSIBLE,
    "pbi_create_relationship": WRITE_REVERSIBLE,
    "pbi_create_visual": WRITE_REVERSIBLE,
    "pbi_disable_auto_date_time": WRITE_REVERSIBLE,
    "pbi_distribute_visuals": WRITE_REVERSIBLE,
    "pbi_duplicate_page": WRITE_REVERSIBLE,
    "pbi_duplicate_visual": WRITE_REVERSIBLE,
    "pbi_generate_report_page": WRITE_REVERSIBLE,
    "pbi_hide_columns": WRITE_REVERSIBLE,
    "pbi_normalize_page_layout": WRITE_REVERSIBLE,
    "pbi_normalize_report": WRITE_REVERSIBLE,
    "pbi_prepare_delivery": WRITE_REVERSIBLE,
    "pbi_rename_page": WRITE_REVERSIBLE,
    "pbi_reorder_pages": WRITE_REVERSIBLE,
    "pbi_repair_broken_references": WRITE_REVERSIBLE,
    "pbi_replace_visual_field": WRITE_REVERSIBLE,
    "pbi_set_column_visibility": WRITE_REVERSIBLE,
    "pbi_set_conditional_format": WRITE_REVERSIBLE,
    "pbi_set_relationship_direction": WRITE_REVERSIBLE,
    "pbi_set_storage_mode": WRITE_REVERSIBLE,
    "pbi_set_visual_filter": WRITE_REVERSIBLE,
    "pbi_set_visual_title": WRITE_REVERSIBLE,
    "pbi_set_visual_z_order": WRITE_REVERSIBLE,
    "pbi_update_measure": WRITE_REVERSIBLE,
    "pbi_update_visual_position": WRITE_REVERSIBLE,

    # -- write_destructive (8): todas exigen confirm=true --
    "pbi_apply_audit_fixes": WRITE_DESTRUCTIVE,
    "pbi_apply_plan": WRITE_DESTRUCTIVE,
    "pbi_delete_bookmark": WRITE_DESTRUCTIVE,
    "pbi_delete_measure": WRITE_DESTRUCTIVE,
    # Cierra una ventana del usuario: lo no guardado se pierde.
    "pbi_close_desktop": WRITE_DESTRUCTIVE,
    "pbi_delete_page": WRITE_DESTRUCTIVE,
    "pbi_delete_visual": WRITE_DESTRUCTIVE,
    "pbi_purge_backups": WRITE_DESTRUCTIVE,
    "pbi_recover_from_journal": WRITE_DESTRUCTIVE,

    # -- write_irreversible (2) --
    "pbi_refresh_model": WRITE_IRREVERSIBLE,
    # Abre Desktop Y refresca. Se clasifica por lo mas fuerte que hace, no por
    # lo primero: el refresh no tiene vuelta atras.
    "pbi_open_and_refresh": WRITE_IRREVERSIBLE,
}


def annotations_for(nombre: str) -> Dict[str, Any]:
    """`ToolAnnotations` de una tool, como dict listo para FastMCP.

    Falla cerrado: una tool sin clasificar no recibe `readOnlyHint=True` por
    descuido; se trata como escritura destructiva hasta que alguien la clasifique.
    """
    clase = RISK_BY_TOOL.get(nombre, WRITE_DESTRUCTIVE)
    if clase == READ_ONLY:
        return {"readOnlyHint": True, "openWorldHint": False}
    return {
        "readOnlyHint": False,
        "destructiveHint": clase in CLASES_DESTRUCTIVAS,
        "idempotentHint": False,
        "openWorldHint": False,
    }
