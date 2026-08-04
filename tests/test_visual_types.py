"""Lo que se ANUNCIA como tipo de visual soportado es lo que se ACEPTA.

`resolve_type()` normaliza a minusculas antes de buscar en `TYPE_MAP`, asi que
una clave declarada en camelCase era inalcanzable: 'cardVisual', 'tableEx' y
'pivotTable' se anunciaban como soportados (la lista salia de `TYPE_MAP.values()`)
y al usarlos el spec se rechazaba con "Tipo no soportado: 'cardVisual'" seguido
de una lista que INCLUIA 'cardVisual'. Sin leer el codigo no habia salida.

Esta era la prueba que faltaba: recorre TODOS los tipos anunciados y comprueba
que se resuelven. Contra el codigo anterior falla en cardVisual/tableEx/pivotTable.
"""
from __future__ import annotations

import ast

import pytest

from horizun_pbi_mcp.pbip import page_builder, project_locator, tmdl_reader, visual_factory
from horizun_pbi_mcp.powerbi.errors import VisualFactoryError
from horizun_pbi_mcp.services import page_spec


def spec_con_tipo(tipo: str) -> dict:
    return {
        "schema_version": "1.0",
        "page": {"name": "X", "width": 1280, "height": 720},
        "visuals": [{"type": tipo, "fields": {"values": ["[TotalAmount]"]}}],
    }


def errores_de_tipo(spec: dict) -> list:
    return [e for e in page_spec.validate_schema(spec)
            if e["path"].endswith(".type")]


def lista_anunciada(mensaje: str) -> list:
    """Extrae la lista 'Soportados: [...]' que el mensaje le enseña al usuario."""
    crudo = mensaje[mensaje.index("["):mensaje.rindex("]") + 1]
    return ast.literal_eval(crudo)


# ===================================== todo lo anunciado se puede resolver ====
@pytest.mark.parametrize("tipo", visual_factory.SUPPORTED)
def test_todo_tipo_anunciado_se_resuelve(tipo):
    """El contrato: si sale en SUPPORTED, `resolve_type` lo acepta."""
    real = visual_factory.resolve_type(tipo)
    assert real in visual_factory.REAL_TYPES


@pytest.mark.parametrize("tipo", visual_factory.SUPPORTED)
def test_todo_tipo_anunciado_pasa_el_validador(tipo):
    """La misma lista, por el camino de `pbi_validate_page_spec` (etapa schema)."""
    assert errores_de_tipo(spec_con_tipo(tipo)) == []


@pytest.mark.parametrize("tipo", visual_factory.SUPPORTED)
def test_la_busqueda_ignora_mayusculas_y_espacios(tipo):
    esperado = visual_factory.resolve_type(tipo)
    for variante in (tipo.lower(), tipo.upper(), f"  {tipo}  "):
        assert visual_factory.resolve_type(variante) == esperado


# ================================ el mensaje de error no puede mentir ========
def test_el_mensaje_de_error_solo_anuncia_lo_que_acepta():
    """Un tipo de la lista del error tiene que funcionar al copiarlo tal cual."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.resolve_type("inventado")

    anunciados = lista_anunciada(exc.value.message)
    assert anunciados, "el error debe decir que tipos hay"
    for tipo in anunciados:
        visual_factory.resolve_type(tipo)  # no puede levantar


def test_el_validador_anuncia_la_misma_lista_que_el_factory():
    """El hint del schema y el mensaje del factory no pueden divergir."""
    errores = errores_de_tipo(spec_con_tipo("inventado"))
    assert len(errores) == 1
    assert lista_anunciada(errores[0]["hint"]) == visual_factory.SUPPORTED

    faltante = spec_con_tipo("card")
    faltante["visuals"][0].pop("type")
    (sin_tipo,) = errores_de_tipo(faltante)
    assert lista_anunciada(sin_tipo["hint"]) == visual_factory.SUPPORTED


def test_building_blocks_anuncia_lo_mismo_que_acepta(session, sample_pbip):
    """`pbi_page_building_blocks` es de donde el usuario copia el tipo."""
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    bb = page_builder.building_blocks(active, tmdl_reader.read_semantic_model(active))

    assert bb["supported_visual_types"] == visual_factory.SUPPORTED
    for tipo in bb["supported_visual_types"]:
        visual_factory.resolve_type(tipo)


# ============================================= la regresion, tipo por tipo ===
@pytest.mark.parametrize("tipo,esperado", [
    ("cardVisual", "cardVisual"),
    ("tableEx", "tableEx"),
    ("pivotTable", "pivotTable"),
])
def test_los_tipos_en_camelcase_del_reporte(tipo, esperado):
    """Antes: anunciados y rechazados a la vez."""
    assert visual_factory.resolve_type(tipo) == esperado
    assert errores_de_tipo(spec_con_tipo(tipo)) == []


@pytest.mark.parametrize("alias,esperado", [
    ("card", "card"),
    ("table", "tableEx"),
    ("matrix", "pivotTable"),
    ("barchart", "barChart"),
    ("columnChart", "columnChart"),
    ("lineChart", "lineChart"),
    ("pieChart", "pieChart"),
    ("slicer", "slicer"),
    ("htmlcontent", visual_factory.HTML_CONTENT_TYPE),
    ("donut", "donutChart"),
    ("waterfall", "waterfallChart"),
    (visual_factory.HTML_CONTENT_TYPE, visual_factory.HTML_CONTENT_TYPE),
])
def test_los_alias_de_siempre_siguen_valiendo(alias, esperado):
    """La correccion no puede quitarle nada a quien ya escribia specs."""
    assert visual_factory.resolve_type(alias) == esperado


@pytest.mark.parametrize("tipo", ["barChart", "columnChart"])
def test_los_tipos_oficiales_no_se_convierten_en_clustered(tipo):
    """El nombre oficial solicitado debe ser el `visualType` que se escribe."""
    assert visual_factory.resolve_type(tipo) == tipo


# ================================================= invariantes del mapa ======
def test_ninguna_clave_del_mapa_es_inalcanzable():
    """Toda clave debe estar en minusculas: `resolve_type` busca en minusculas."""
    inalcanzables = [k for k in visual_factory.TYPE_MAP if k != k.lower()]
    assert inalcanzables == []


def test_cada_tipo_real_es_alias_de_si_mismo():
    for real in visual_factory.REAL_TYPES:
        assert visual_factory.resolve_type(real) == real


def test_cada_tipo_real_tiene_sus_roles():
    """Sin entrada en ROLE_MAP el visual cae al mapa por defecto y pierde roles
    (una matrix se quedaria sin rows/columns) sin avisar."""
    # Los elementos de composicion no consultan datos: no tienen roles que
    # mapear, y exigirselos seria pedirle campos a un rectangulo.
    sin_roles = [t for t in visual_factory.REAL_TYPES
                 if t not in visual_factory.ROLE_MAP
                 and t not in visual_factory.DECORATIVOS]
    assert sin_roles == []


def test_cada_tipo_con_datos_declara_roles_obligatorios():
    sin_contrato = [t for t in visual_factory.REAL_TYPES
                    if t not in visual_factory.REQUIRED_ROLES
                    and t not in visual_factory.DECORATIVOS]
    assert sin_contrato == []


def test_cada_rol_declara_el_tipo_de_campo_oficial():
    faltantes = {(tipo, rol) for tipo, roles in visual_factory.ROLE_MAP.items()
                 for rol in set(roles.values())
                 if rol not in visual_factory.ROLE_KINDS.get(tipo, {})}
    assert faltantes == set()


def test_faltan_roles_obligatorios_se_rechaza_antes_de_escribir(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            active, "lineChart", {"category": ["Fact[Category]"]},
            {"x": 0, "y": 0, "width": 400, "height": 240})
    assert exc.value.details["missing_roles"] == ["Y"]


@pytest.mark.parametrize("tipo,campos,rol,maximo", [
    ("card", {"values": ["[A]", "[B]"]}, "Values", 1),
    ("slicer", {"values": ["T[A]", "T[B]"]}, "Values", 1),
    ("kpi", {"values": ["[A]"], "goal": ["[B]", "[C]", "[D]"]},
     "Goal", 2),
])
def test_cardinalidad_oficial_se_exige(
        session, sample_pbip, tipo, campos, rol, maximo):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            active, tipo, campos,
            {"x": 0, "y": 0, "width": 400, "height": 240},
            measure_index={"A": "T", "B": "T", "C": "T", "D": "T"})
    (detalle,) = exc.value.details["cardinality_exceeded"]
    assert detalle == {"role": rol, "count": maximo + 1, "max": maximo}


def test_columna_cruda_no_puede_ocupar_un_rol_de_medida(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            active, "columnChart",
            {"category": ["Fact[Category]"], "values": ["Fact[Amount]"]},
            {"x": 0, "y": 0, "width": 400, "height": 240})
    (detalle,) = exc.value.details["role_kind_mismatch"]
    assert detalle["role"] == "Y"
    assert detalle["expected"] == "Measure"
    assert detalle["received"] == "Grouping"


def test_medida_no_puede_ocupar_un_rol_de_agrupacion(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            active, "lineChart",
            {"category": ["[TotalAmount]"], "values": ["[Ratio Pct]"]},
            {"x": 0, "y": 0, "width": 400, "height": 240},
            measure_index={"TotalAmount": "Fact", "Ratio Pct": "Fact"})
    assert exc.value.details["role_kind_mismatch"][0]["role"] == "Category"


def test_no_se_anuncia_nada_que_no_este_en_el_mapa():
    fantasmas = [t for t in visual_factory.SUPPORTED
                 if t.lower() not in visual_factory.TYPE_MAP]
    assert fantasmas == []
