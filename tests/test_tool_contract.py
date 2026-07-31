"""Contrato MCP: congela las 34 tools existentes.

Estas pruebas son la red de seguridad del refactor. Su unico trabajo es que
NADIE rompa, sin darse cuenta, una tool de la que ya dependen clientes MCP
configurados. Si una prueba de aqui falla, el fallo explica QUE cambio y si
rompe compatibilidad, no solo que dos JSON son distintos.
"""
from __future__ import annotations

import pytest

from tests import contract_utils

# --- Las 34 tools del baseline. Esta lista es deliberadamente explicita: -------
# es el contrato publico. Anadir una tool aqui es una decision consciente;
# quitar una es una ruptura que debe estar aprobada.
BASELINE_TOOLS = [
    # sesion / DAX
    "pbi_list_desktop_models",
    "pbi_select_model",
    "pbi_run_dax",
    "pbi_test_connection",
    "pbi_validate_measures",
    # documentacion
    "pbi_list_tables",
    "pbi_list_measures",
    "pbi_list_relationships",
    "pbi_analyze_model_quality",
    "pbi_document_model",
    # medidas
    "pbi_create_measure",
    "pbi_update_measure",
    "pbi_delete_measure",
    # edicion de modelo
    "pbi_set_column_visibility",
    "pbi_hide_columns",
    "pbi_set_relationship_direction",
    "pbi_disable_auto_date_time",
    # generacion de hojas
    "pbi_page_building_blocks",
    "pbi_preview_spec_html",
    "pbi_export_page_html",
    "pbi_create_page_from_spec",
    # proyecto pbip
    "pbi_open_pbip_project",
    "pbi_validate_pbip_project",
    "pbi_backup_pbip_project",
    # refresh
    "pbi_refresh_model",
    # informe PBIR
    "pbi_list_report_pages",
    "pbi_list_visuals",
    "pbi_document_report_layout",
    "pbi_create_visual",
    "pbi_add_custom_visual",
    "pbi_create_html_visual",
    "pbi_update_visual_position",
    "pbi_arrange_visuals",
    "pbi_generate_report_page",
]

# Tools operativas anadidas en la Macrofase A. Son ADICIONES: las 34 del
# baseline siguen intactas, que es la garantia de compatibilidad.
MACROFASE_A_TOOLS = [
    "pbi_health_check", "pbi_capabilities", "pbi_session_info",
    "pbi_list_pending_journals", "pbi_inspect_journal",
    "pbi_plan_change", "pbi_apply_plan",
]

# Exploracion y auditoria del modelo semantico (Macrofase B).
MACROFASE_B_TOOLS = [
    "pbi_model_summary", "pbi_search_model", "pbi_get_object",
    "pbi_measure_dependencies", "pbi_column_dependencies",
    "pbi_list_hierarchies", "pbi_list_roles", "pbi_list_perspectives",
    "pbi_list_partitions", "pbi_audit_model", "pbi_list_audit_rules",
]

# Autoria de informes PBIR (Macrofase C).
MACROFASE_C_TOOLS = [
    "pbi_get_visual", "pbi_report_capabilities",
    "pbi_duplicate_visual", "pbi_delete_visual", "pbi_set_visual_title",
    "pbi_set_visual_z_order", "pbi_replace_visual_field",
    "pbi_copy_visual_format",
    "pbi_duplicate_page", "pbi_delete_page", "pbi_rename_page",
    "pbi_reorder_pages",
    "pbi_detect_layout_issues", "pbi_align_visuals",
    "pbi_distribute_visuals", "pbi_normalize_page_layout",
]

# Constructor declarativo de paginas (Macrofase D).
MACROFASE_D_TOOLS = [
    "pbi_list_page_presets", "pbi_generate_page_spec", "pbi_validate_page_spec",
    "pbi_preview_page_spec", "pbi_diff_page_spec", "pbi_apply_page_spec",
    "pbi_validate_generated_page",
]

# Auditoria integral y autofixes (Macrofase E).
MACROFASE_E_TOOLS = [
    "pbi_audit_project", "pbi_audit_report_only", "pbi_plan_audit_fixes",
    "pbi_apply_audit_fixes", "pbi_list_autofix_rules",
]

# Workflows de alto nivel (Macrofase F).
MACROFASE_F_TOOLS = [
    "pbi_build_dashboard", "pbi_build_executive_page", "pbi_build_evm_page",
    "pbi_repair_broken_references", "pbi_normalize_report",
    "pbi_compare_live_to_pbip", "pbi_prepare_delivery",
    "pbi_generate_technical_documentation",
]

#: Fase F/R5: recuperacion operativa y retencion de backups.
FASE_F_R5_TOOLS = [
    "pbi_recover_from_journal",
    "pbi_purge_backups",
]

TOOLS_NUEVAS = (MACROFASE_A_TOOLS + MACROFASE_B_TOOLS + MACROFASE_C_TOOLS
                + MACROFASE_D_TOOLS + MACROFASE_E_TOOLS + MACROFASE_F_TOOLS
                + FASE_F_R5_TOOLS)
BASELINE_COUNT = 34
EXPECTED_COUNT = BASELINE_COUNT + len(TOOLS_NUEVAS)


@pytest.fixture(scope="module")
def snapshot():
    """Snapshot del contrato actual, calculado una sola vez."""
    return contract_utils.snapshot_from_server()


# ------------------------------------------------------------ nombres ---------
def test_tool_count_is_stable(snapshot):
    assert snapshot["tool_count"] == EXPECTED_COUNT, (
        f"Se esperaban {EXPECTED_COUNT} tools y hay {snapshot['tool_count']}. "
        "Si anadiste una tool, actualiza EXPECTED_COUNT y BASELINE_TOOLS "
        "y regenera el golden con: python -m tests.contract_utils --write"
    )


def test_no_baseline_tool_disappeared(snapshot):
    current = {t["name"] for t in snapshot["tools"]}
    missing = sorted(set(BASELINE_TOOLS) - current)
    assert not missing, (
        "Estas tools del baseline ya no existen y romperian a los clientes "
        f"ya configurados: {missing}"
    )


def test_baseline_list_matches_declared_count():
    assert len(BASELINE_TOOLS) == BASELINE_COUNT
    assert len(set(BASELINE_TOOLS)) == BASELINE_COUNT, "hay nombres repetidos"


def test_las_tools_nuevas_estan_registradas(snapshot):
    actuales = {t["name"] for t in snapshot["tools"]}
    faltan = sorted(set(TOOLS_NUEVAS) - actuales)
    assert not faltan, f"tools nuevas sin registrar: {faltan}"


def test_las_tools_nuevas_no_pisan_el_baseline():
    solapadas = set(TOOLS_NUEVAS) & set(BASELINE_TOOLS)
    assert not solapadas, f"una tool nueva reusa un nombre del baseline: {solapadas}"
    assert len(TOOLS_NUEVAS) == len(set(TOOLS_NUEVAS)), "nombres repetidos"


def test_all_tools_are_prefixed(snapshot):
    bad = [t["name"] for t in snapshot["tools"] if not t["name"].startswith("pbi_")]
    assert not bad, f"Tools sin el prefijo 'pbi_': {bad}"


# ------------------------------------------------------------ contrato --------
def test_contract_matches_golden(snapshot):
    """El contrato completo (params, tipos, defaults, required) no cambio."""
    golden = contract_utils.load_golden()
    breaking, compatible = contract_utils.diff_snapshots(golden, snapshot)
    assert not breaking and not compatible, (
        "\n\nEl contrato MCP cambio respecto al golden:\n\n"
        + contract_utils.format_diff(breaking, compatible)
    )


def test_no_breaking_changes(snapshot):
    """Prueba mas laxa: permite anadidos compatibles, prohibe rupturas.

    Util durante el desarrollo de una fase: deja avanzar mientras solo se
    anadan parametros opcionales o tools nuevas.
    """
    golden = contract_utils.load_golden()
    breaking, _ = contract_utils.diff_snapshots(golden, snapshot)
    assert not breaking, (
        "\n\nCambios que ROMPEN compatibilidad:\n\n"
        + contract_utils.format_diff(breaking, [])
    )


# ------------------------------------------------------------ calidad ---------
def test_every_tool_has_a_useful_description(snapshot):
    """Un LLM decide si usar una tool leyendo su descripcion."""
    too_short = [(t["name"], len(t["description"]))
                 for t in snapshot["tools"] if len(t["description"]) < 30]
    assert not too_short, f"Descripciones demasiado cortas para decidir uso: {too_short}"


def test_every_tool_declares_an_output_shape(snapshot):
    missing = [t["name"] for t in snapshot["tools"] if not t.get("output_shape")]
    assert not missing, f"Tools sin outputSchema declarado: {missing}"


DESTRUCTIVAS = ["pbi_delete_measure", "pbi_delete_visual", "pbi_delete_page",
                "pbi_apply_audit_fixes"]


@pytest.mark.parametrize("nombre", DESTRUCTIVAS)
def test_destructive_tool_requires_confirmation(snapshot, nombre):
    """Toda tool destructiva debe exigir confirm=true explicito."""
    tool = next(t for t in snapshot["tools"] if t["name"] == nombre)
    assert "confirm" in tool["params"], f"{nombre} perdio el parametro 'confirm'"
    assert tool["params"]["confirm"]["default"] is False, (
        f"'confirm' de {nombre} debe seguir siendo False por defecto: si pasa a "
        "True, un borrado accidental deja de estar protegido."
    )


# ------------------------------------------------- el detector, probado -------
# Si el motor de diff no detectara las rupturas, las pruebas de arriba darian
# una falsa sensacion de seguridad. Aqui se comprueba que SI las detecta.
def _mini(name="pbi_x", params=None, desc="descripcion suficientemente larga para pasar"):
    return {
        "contract_version": 1,
        "tool_count": 1,
        "tools": [{
            "name": name,
            "description": desc,
            "params": params if params is not None else {
                "a": {"required": True, "type": "string"},
                "b": {"required": False, "type": "string", "default": "live"},
            },
            "required": ["a"],
            "output_shape": {"type": "object", "properties": ["result"],
                             "required": ["result"]},
        }],
    }


def test_diff_detects_removed_tool():
    breaking, _ = contract_utils.diff_snapshots(
        _mini(), {"contract_version": 1, "tool_count": 0, "tools": []})
    assert any("TOOL ELIMINADA" in b for b in breaking)


def test_diff_detects_new_required_param():
    after = _mini(params={
        "a": {"required": True, "type": "string"},
        "b": {"required": False, "type": "string", "default": "live"},
        "c": {"required": True, "type": "string"},
    })
    breaking, _ = contract_utils.diff_snapshots(_mini(), after)
    assert any("NUEVO OBLIGATORIO" in b and "'c'" in b for b in breaking)


def test_diff_detects_changed_default():
    after = _mini(params={
        "a": {"required": True, "type": "string"},
        "b": {"required": False, "type": "string", "default": "pbip"},
    })
    breaking, _ = contract_utils.diff_snapshots(_mini(), after)
    assert any("valor por defecto" in b for b in breaking)


def test_diff_detects_type_change():
    after = _mini(params={
        "a": {"required": True, "type": "integer"},
        "b": {"required": False, "type": "string", "default": "live"},
    })
    breaking, _ = contract_utils.diff_snapshots(_mini(), after)
    assert any("tipo string -> integer" in b for b in breaking)


def test_diff_treats_new_optional_param_as_compatible():
    after = _mini(params={
        "a": {"required": True, "type": "string"},
        "b": {"required": False, "type": "string", "default": "live"},
        "dry_run": {"required": False, "type": "boolean", "default": False},
    })
    breaking, compatible = contract_utils.diff_snapshots(_mini(), after)
    assert not breaking
    assert any("dry_run" in c for c in compatible)


def test_diff_reports_are_human_readable():
    after = {"contract_version": 1, "tool_count": 0, "tools": []}
    breaking, compatible = contract_utils.diff_snapshots(_mini(), after)
    text = contract_utils.format_diff(breaking, compatible)
    assert "RUPTURAS DE COMPATIBILIDAD" in text
    assert "pbi_x" in text
    assert "--write" in text, "el reporte debe decir como regenerar el golden"


# ================== el contrato no puede depender de la version de Python ====
def test_la_descripcion_se_normaliza_igual_con_y_sin_sangria():
    """REGRESION: el CI en Python 3.10 marcaba las 90 tools como modificadas.

    Python 3.13 cambio como se guardan los docstrings: desde esa version el
    compilador les quita la sangria (gh-81283). En 3.10-3.12 el `__doc__`
    conserva los espacios de cada linea de continuacion.

    El golden se genero con 3.14, asi que en 3.10 TODAS las descripciones
    salian mas largas —exactamente los bytes de sangria— y el contract check
    fallaba sin que nada del producto hubiera cambiado.
    """
    como_310 = ("Lista los modelos abiertos.\n\n"
                "        Devuelve puerto y catalogo.\n"
                "        Conviene llamarla primero.\n        ")
    como_313 = ("Lista los modelos abiertos.\n\n"
                "Devuelve puerto y catalogo.\n"
                "Conviene llamarla primero.\n")

    assert len(como_310) != len(como_313), "el escenario debe diferir en crudo"
    assert (contract_utils._normalize_description(como_310)          # noqa: SLF001
            == contract_utils._normalize_description(como_313)), (   # noqa: SLF001
        "la normalizacion depende de la sangria: el golden no sera portable "
        "entre versiones de Python")


def test_la_normalizacion_es_idempotente():
    """Normalizar dos veces no puede cambiar el resultado."""
    texto = "Titulo.\n\n    Cuerpo con sangria.\n    Y otra linea.\n    "
    una = contract_utils._normalize_description(texto)               # noqa: SLF001
    dos = contract_utils._normalize_description(una)                 # noqa: SLF001
    assert una == dos


def test_el_golden_no_tiene_sangria_residual():
    """Si el golden se regenerara en 3.10 sin normalizar, se notaria aqui."""
    golden = contract_utils.load_golden()
    con_sangria = [t["name"] for t in golden["tools"]
                   if any(l.startswith(("    ", "\t"))
                          for l in t["description"].splitlines()[1:]
                          if l.strip())]
    # Las lineas de una lista o un bloque indentado a proposito son legitimas;
    # lo que no puede haber es sangria UNIFORME en todas las continuaciones.
    for nombre in con_sangria:
        d = [t for t in golden["tools"] if t["name"] == nombre][0]["description"]
        cuerpo = [l for l in d.splitlines()[1:] if l.strip()]
        assert not all(l.startswith("        ") for l in cuerpo), (
            f"{nombre}: el golden conserva la sangria del docstring; se genero "
            "con un Python anterior a 3.13 y sin normalizar")
