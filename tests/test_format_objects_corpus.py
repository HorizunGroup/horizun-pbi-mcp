"""Equivalencia estructural con formato que Power BI Desktop escribio de verdad.

El fixture no contiene un solo valor real: solo rutas del catalogo oficial y
formas cuyos escalares fueron sustituidos por tokens. Estas pruebas atan esa
evidencia a los fragmentos que producen ``visual_factory`` y el camino de
formato condicional usado por ``pbir_edit``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from horizun_pbi_mcp.pbip import conditional_format, project_locator, visual_factory
from horizun_pbi_mcp.services import pbir_edit

from scripts import build_format_objects_corpus as corpus_builder


CORPUS_PATH = (Path(__file__).parent / "fixtures" / "synthetic" /
               "format_objects_corpus.json")
CORPUS = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
TOKENS = corpus_builder.TOKENS


def _shape(value: Any) -> Any:
    """La misma normalizacion publica del extractor, sin leer ningun PBIX."""
    if isinstance(value, dict):
        return {key: _shape(child) for key, child in sorted(value.items())}
    if isinstance(value, list):
        variants = {
            json.dumps(_shape(child), sort_keys=True, separators=(",", ":")):
                _shape(child)
            for child in value
        }
        return [variants[key] for key in sorted(variants)]
    if isinstance(value, bool):
        return "<boolean>"
    if isinstance(value, (int, float)):
        return "<number>"
    if isinstance(value, str):
        return "<string>"
    if value is None:
        return "<null>"
    raise TypeError(type(value).__name__)


def _property_shapes(visual_type: str, family: str, group: str,
                     prop: str) -> List[Any]:
    return [shape
            for row in CORPUS["visual_types"][visual_type]["properties"]
            if (row["family"], row["group"], row["property"])
            == (family, group, prop)
            for shape in row["shapes"]]


def _all_property_shapes(group: str, prop: str) -> List[Any]:
    return [shape
            for entry in CORPUS["visual_types"].values()
            for row in entry["properties"]
            if (row["family"], row["group"], row["property"])
            == ("objects", group, prop)
            for shape in row["shapes"]]


def _all_selector_shapes(group: str) -> List[Any]:
    return [shape
            for entry in CORPUS["visual_types"].values()
            for row in entry["selectors"]
            if (row["family"], row["group"]) == ("objects", group)
            for shape in row["shapes"]]


def _assert_properties_seen(visual_type: str, visual: Dict[str, Any]) -> None:
    for family in ("objects", "visualContainerObjects"):
        for group, blocks in (visual.get(family) or {}).items():
            for block in blocks:
                for prop, value in (block.get("properties") or {}).items():
                    assert _shape(value) in _property_shapes(
                        visual_type, family, group, prop), (
                        f"Desktop no ha producido aun la forma generada en "
                        f"{visual_type}.{family}.{group}.{prop}")


def _walk_shape(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        assert set(value) <= corpus_builder.SAFE_WRAPPER_KEYS
        for child in value.values():
            yield from _walk_shape(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_shape(child)
    else:
        yield value


def test_corpus_es_cero_datos_reales_y_solo_vocabulario_aprobado():
    assert set(CORPUS) == {"schema_version", "anonymization", "visual_types"}
    assert "source_summary" not in CORPUS
    assert set(CORPUS["visual_types"]) <= corpus_builder.BUILTIN_TYPES
    assert {"card", "cardVisual", "tableEx", "pivotTable", "slicer",
            "clusteredColumnChart", "lineChart", "textbox", "shape",
            "image", "pageNavigator", "actionButton"} <= set(
                CORPUS["visual_types"])

    raw = CORPUS_PATH.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\\/]", raw)
    assert not re.search(r"https?://", raw, flags=re.IGNORECASE)
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab]"
        r"[0-9a-f]{3}-[0-9a-f]{12}\b", raw, flags=re.IGNORECASE)

    for entry in CORPUS["visual_types"].values():
        for row in entry["properties"] + entry["selectors"]:
            assert row["family"] in corpus_builder.FAMILIES
            for observed in row["shapes"]:
                assert set(_walk_shape(observed)) <= TOKENS


def test_extractor_falla_cerrado_ante_una_clave_dinamica():
    # Una hoja se anonimiza; una clave desconocida podria ser el nombre de una
    # tabla/campo/persona y por eso nunca se sustituye silenciosamente.
    assert corpus_builder._shape({"Literal": {"Value": "secreto"}}) == {
        "Literal": {"Value": "<string>"}}
    try:
        corpus_builder._shape({"NombreDeProyectoReal": "secreto"})
    except ValueError as exc:
        assert "no aprobadas" in str(exc)
    else:  # pragma: no cover - es una garantia de privacidad, no tolerancia
        raise AssertionError("El extractor acepto una clave dinamica")


def test_visual_factory_equivale_a_exports_reales_en_todo_lo_que_formatea():
    casos = [
        ("textbox", {"text": "x", "font_size": 20, "color": "#123456",
                     "bold": True, "font": "Arial", "align": "center"}),
        ("shape", {"shape": "roundedRectangle", "angle": 15,
                   "fill": "#123456", "transparency": 10, "text": "x",
                   "font_size": 12, "text_color": "#654321"}),
        ("image", {"resource": "recurso.png", "name": "imagen",
                   "scaling": "Fit"}),
        ("pageNavigator", {"show_hidden": True, "show_current": True}),
        ("actionButton", {"action": "back", "icon": "info", "text": "x",
                          "font_size": 12, "text_color": "#123456",
                          "fill": "#654321", "transparency": 2}),
    ]
    for visual_type, options in casos:
        _assert_properties_seen(
            visual_type, visual_factory._build_decorativo(visual_type, options))

    for visual_type in ("card", "cardVisual"):
        visual: Dict[str, Any] = {}
        visual_factory._set_title(visual, "Titulo sintetico")
        visual_factory._aplicar_opciones_de_tarjeta(
            visual, visual_type,
            {"show_category_label": False, "value_font_size": 32,
             "bold_value": True, "value_color": "#123456"})
        _assert_properties_seen(visual_type, visual)

    # El marco (fondo/borde) se puede pedir en CUALQUIER tipo de visual, no
    # solo tarjeta/forma. Se comprueba contra tres familias distintas (texto,
    # grafico, segmentador) que el corpus confirma con la forma exacta que
    # generamos (color liso, no ThemeDataColor/Conditional): 'card'/'tableEx'
    # tambien aceptan el marco en Desktop, pero la muestra del corpus para
    # esos dos solo capturo variantes de tema, asi que probarlos ahi habria
    # sido una afirmacion sin evidencia, no una comprobacion real.
    for visual_type in ("textbox", "lineChart", "slicer"):
        visual = {}
        visual_factory._aplicar_estilo_contenedor(
            visual, {"background_color": "#123456", "border_color": "#654321",
                     "border_radius": 8, "background_transparency": 10})
        _assert_properties_seen(visual_type, visual)


def test_fillrule_y_selectores_coinciden_con_formas_escritas_por_desktop():
    # Desktop conserva la agregacion exacta de la proyeccion. Es el caso que
    # ``pbir_edit`` reutiliza ahora en vez de reconstruir un nodo Column.
    field = {"Aggregation": {
        "Expression": {"Column": {
            "Expression": {"SourceRef": {"Entity": "TablaSintetica"}},
            "Property": "ColumnaSintetica"}},
        "Function": 0,
    }}
    value = conditional_format._propiedad_de_color(  # noqa: SLF001
        conditional_format.build_fill_rule(field, "#000000", "#FFFFFF"))

    assert _shape(value) in _all_property_shapes("values", "backColor")
    assert _shape(value) in _all_property_shapes("dataPoint", "fill")
    assert _shape(conditional_format._selector("T.C")) in (  # noqa: SLF001
        _all_selector_shapes("values"))
    assert _shape(conditional_format._selector(None)) in (  # noqa: SLF001
        _all_selector_shapes("dataPoint"))


def test_pbir_edit_escribe_la_misma_forma_sin_tocar_el_fixture_versionado(
        session, tmp_path, monkeypatch):
    # sample_pbip es una copia mutable dentro de tmp_path. La prueba nunca
    # escribe sobre el corpus ni sobre un proyecto real.
    from horizun_pbi_mcp.services import format_oracle
    from tests.fixtures import synthetic

    sample_pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    monkeypatch.setattr(format_oracle, "assert_managed_paths",
                        lambda *args, **kwargs: {"available": False, "errors": []})

    pbir_edit.set_conditional_format(
        active, "page01", "tmplcol00000000000", "Fact[TotalAmount]",
        "#000000", "#FFFFFF", target="bars")
    path = (sample_pbip.parent / "Demo.Report" / "definition" / "pages" /
            "page01" / "visuals" / "tmplcol00000000000" / "visual.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    block = document["visual"]["objects"]["dataPoint"][0]

    assert _shape(block["properties"]["fill"]) in _all_property_shapes(
        "dataPoint", "fill")
    assert _shape(block["selector"]) in _all_selector_shapes("dataPoint")
