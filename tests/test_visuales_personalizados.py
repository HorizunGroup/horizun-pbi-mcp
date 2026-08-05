"""Visuales PERSONALIZADOS: se descubren del informe, no se codifican.

El bloqueo que cierra: `pbi_apply_page_spec` rechazaba un GUID de visual
personalizado porque `TYPE_MAP` es una tupla fija de nativos. En un tablero
4D/5D eso significaba poder montar KPIs, curvas y tablas pero NO el visor 3D
ni la linea de tiempo —justo el motivo de conectar BIM con Power BI—.

Las reglas que se vigilan aqui:

1. **El contrato lo publica el visual**, no nosotros: los roles validos salen
   de `capabilities.dataRoles` del `.pbiviz.json` que el informe ya tiene
   instalado. Un rol que ese GUID no declara se rechaza con la lista de los
   validos, con el mismo rigor que con un nativo.
2. **Los contratos NATIVOS no se aplican a un tercero.** `REQUIRED_ROLES`,
   `MAX_PER_ROLE` y `ROLE_KINDS` salieron del catalogo oficial de Microsoft;
   imponerlos a un visual de terceros seria inventarle un contrato.
3. **Se conserva su configuracion propia.** `objects.connection` del visor APS
   lleva `baseUrl` y el token `mt`: sin eso el visual carga vacio. Lo que SI
   se descarta es la seleccion puntual heredada (`selector.data` con
   `scopeId`) y el formato de marco que el catalogo oficial rechaza.

Los fixtures son SINTETICOS: el informe real con el visor y el buildMotion no
puede entrar al repositorio.
"""
from __future__ import annotations

import json

import pytest

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.pbip import custom_visuals, visual_factory
from horizun_pbi_mcp.powerbi.errors import VisualFactoryError
from horizun_pbi_mcp.services import page_spec

GUID_VISOR = "aPSModelViewer02FA0CE460E64F779B012F5831EB091C"
GUID_TIMELINE = "buildMotionFull9BE91111D3D84D37B27DA2B9B03293AD"

POS = {"x": 0, "y": 0, "width": 600, "height": 400}


def _pbiviz(guid: str, display: str, roles: list) -> dict:
    return {"visual": {"guid": guid, "displayName": display, "version": "1.0.0"},
            "capabilities": {"dataRoles": [
                {"name": r, "kind": "Grouping", "displayName": r} for r in roles]}}


@pytest.fixture
def informe(tmp_path):
    """Informe minimo con dos visuales personalizados instalados."""
    custom_visuals.invalidate_cache()
    report = tmp_path / "Demo.Report"
    (report / "definition" / "pages").mkdir(parents=True)
    for guid, nombre, roles in (
            (GUID_VISOR, "PowerBIM Viewer",
             ["dbids", "modelId", "colorBy", "externalIds"]),
            (GUID_TIMELINE, "BuildMotion Timeline", ["timelineDate", "label"])):
        d = report / "CustomVisuals" / guid / "resources"
        d.mkdir(parents=True)
        (d / f"{guid}.pbiviz.json").write_text(
            json.dumps(_pbiviz(guid, nombre, roles)), encoding="utf-8")
    active = ActivePbip(pbip_path=str(tmp_path / "Demo.pbip"),
                        project_dir=str(tmp_path), report_dir=str(report),
                        report_name="Demo", has_pbir=True, has_tmdl=True)
    yield active
    custom_visuals.invalidate_cache()


def _plantilla(informe, guid: str, objects: dict | None = None,
               vco: dict | None = None) -> None:
    """Escribe un visual del tipo dado para que sirva de plantilla.

    Incluye `page.json` y `pages.json`: `list_pages` se niega -con razon- a
    trabajar sobre un inventario incompleto del informe.
    """
    from pathlib import Path

    paginas = Path(informe.report_dir) / "definition" / "pages"
    paginas.mkdir(parents=True, exist_ok=True)
    (paginas / "pages.json").write_text(json.dumps({
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/pagesMetadata/1.0.0/schema.json"),
        "pageOrder": ["p1"], "activePageName": "p1"}), encoding="utf-8")
    (paginas / "p1").mkdir(exist_ok=True)
    (paginas / "p1" / "page.json").write_text(json.dumps({
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/page/2.1.0/schema.json"),
        "name": "p1", "displayName": "Pagina", "displayOption": "FitToPage",
        "height": 720, "width": 1280}), encoding="utf-8")
    d = paginas / "p1" / "visuals" / "plantilla0000000000aa"
    d.mkdir(parents=True, exist_ok=True)
    vis = {"visualType": guid, "query": {"queryState": {}},
           "drillFilterOtherVisuals": True}
    if objects is not None:
        vis["objects"] = objects
    if vco is not None:
        vis["visualContainerObjects"] = vco
    (d / "visual.json").write_text(json.dumps({
        "$schema": visual_factory.SCHEMA_VISUAL, "name": "plantilla0000000000aa",
        "position": POS, "visual": vis}), encoding="utf-8")


# ------------------------------------------------------------ descubrimiento ---
def test_descubre_los_instalados_con_sus_roles(informe):
    hallados = custom_visuals.discover_for(informe)
    assert set(hallados) == {GUID_VISOR, GUID_TIMELINE}
    assert custom_visuals.role_names(hallados[GUID_VISOR]) == [
        "dbids", "modelId", "colorBy", "externalIds"]


def test_un_informe_sin_custom_visuals_no_es_un_error(tmp_path):
    custom_visuals.invalidate_cache()
    (tmp_path / "R.Report").mkdir(parents=True)
    assert custom_visuals.discover(tmp_path / "R.Report") == {}


def test_un_manifiesto_corrupto_no_tumba_a_los_demas(informe, tmp_path):
    """Metadato de un visual de terceros: que uno venga roto no puede impedir
    escribir el resto de la pagina."""
    from pathlib import Path

    malo = (Path(informe.report_dir) / "CustomVisuals" / "roto" / "resources")
    malo.mkdir(parents=True)
    (malo / "roto.pbiviz.json").write_text("{esto no es json", encoding="utf-8")
    custom_visuals.invalidate_cache()
    assert set(custom_visuals.discover_for(informe)) == {GUID_VISOR, GUID_TIMELINE}


def test_la_cache_se_entera_de_una_instalacion_nueva(informe):
    from pathlib import Path

    assert len(custom_visuals.discover_for(informe)) == 2
    guid = "otroVisual1234567890ABCDEF1234567890ABCD"
    d = Path(informe.report_dir) / "CustomVisuals" / guid / "resources"
    d.mkdir(parents=True)
    (d / f"{guid}.pbiviz.json").write_text(
        json.dumps(_pbiviz(guid, "Otro", ["x"])), encoding="utf-8")
    assert guid in custom_visuals.discover_for(informe), (
        "cachear solo por ruta serviria datos viejos tras instalar un visual")


# ------------------------------------------------------- validacion del tipo ---
def test_el_spec_acepta_un_guid_instalado(informe):
    spec = {"schema_version": "1.0", "page": {"name": "P"},
            "visuals": [{"type": GUID_VISOR,
                         "fields": {"dbids": ["Modelo[dbids]"]}}]}
    assert page_spec.validate_schema(spec, informe) == []


def test_el_spec_rechaza_un_guid_que_no_esta_instalado(informe):
    spec = {"schema_version": "1.0", "page": {"name": "P"},
            "visuals": [{"type": "guidQueNadieInstalo", "fields": {}}]}
    errores = page_spec.validate_schema(spec, informe)
    assert errores and errores[0]["path"] == "$.visuals[0].type"
    assert GUID_VISOR in errores[0]["hint"], (
        "el error debe listar lo que SI se puede usar en este informe")


def test_sin_proyecto_activo_solo_se_admiten_nativos():
    spec = {"schema_version": "1.0", "page": {"name": "P"},
            "visuals": [{"type": GUID_VISOR, "fields": {}}]}
    assert page_spec.validate_schema(spec) != []


def test_el_guid_se_escribe_canonico_no_como_se_teclee(informe):
    """`visualType` tiene que coincidir exacto o Power BI no lo encuentra."""
    assert visual_factory.resolve_type(GUID_VISOR.lower(), informe) == GUID_VISOR


# ------------------------------------------------------------ roles verbatim ---
def test_los_roles_se_escriben_tal_cual_los_declara_el_manifiesto(informe):
    r = visual_factory.build_visual(
        informe, GUID_VISOR,
        {"dbids": ["M[a]"], "modelId": ["M[b]"], "colorBy": ["M[c]"],
         "externalIds": ["M[d]"]}, POS, measure_index={})
    estados = r["visual"]["visual"]["query"]["queryState"]
    assert sorted(estados) == ["colorBy", "dbids", "externalIds", "modelId"]


def test_un_rol_que_el_visual_no_declara_se_rechaza(informe):
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(informe, GUID_TIMELINE,
                                    {"category": ["M[a]"]}, POS, measure_index={})
    assert "no declara un rol 'category'" in str(exc.value)
    assert "timelineDate" in str(exc.value.details["valid_roles"])


def test_los_contratos_nativos_no_se_le_imponen_a_un_tercero(informe):
    """Un solo rol basta: REQUIRED_ROLES es del catalogo oficial, no suyo."""
    r = visual_factory.build_visual(informe, GUID_TIMELINE,
                                    {"timelineDate": ["Cal[Fecha]"]}, POS,
                                    measure_index={})
    assert sorted(r["visual"]["visual"]["query"]["queryState"]) == ["timelineDate"]


def test_el_guard_de_contratos_nativos_protege_de_verdad(informe, monkeypatch):
    """Prueba el GUARD, no su ausencia de efecto.

    Con las tablas nativas tal cual, `_validate_role_contract` ya es un no-op
    para un tipo desconocido -todos los `.get(tipo, vacio)` fallan al mismo
    sitio-, asi que quitar el `return` temprano no cambiaba nada y la prueba
    anterior pasaba igual: no verificaba nada. Aqui se pone a proposito una
    entrada nativa para ese GUID y se exige que NO se le aplique. Si alguien
    quita el guard, esto falla.
    """
    monkeypatch.setitem(visual_factory.REQUIRED_ROLES, GUID_TIMELINE,
                        ("RolQueNadieVaAMandar",))
    monkeypatch.setitem(visual_factory.MAX_PER_ROLE, GUID_TIMELINE,
                        {"timelineDate": 0})
    r = visual_factory.build_visual(informe, GUID_TIMELINE,
                                    {"timelineDate": ["Cal[Fecha]"]}, POS,
                                    measure_index={})
    assert sorted(r["visual"]["visual"]["query"]["queryState"]) == ["timelineDate"]


def test_un_personalizado_sin_roles_declarados_se_acusa(tmp_path):
    from pathlib import Path

    custom_visuals.invalidate_cache()
    report = tmp_path / "R.Report"
    guid = "sinRoles1234567890ABCDEF1234567890ABCDEF"
    d = report / "CustomVisuals" / guid / "resources"
    d.mkdir(parents=True)
    (d / f"{guid}.pbiviz.json").write_text(
        json.dumps({"visual": {"guid": guid, "displayName": "X"},
                    "capabilities": {}}), encoding="utf-8")
    (report / "definition" / "pages").mkdir(parents=True)
    active = ActivePbip(pbip_path=str(tmp_path / "R.pbip"),
                        project_dir=str(tmp_path), report_dir=str(report),
                        report_name="R", has_pbir=True, has_tmdl=True)
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(active, guid, {"x": ["A[b]"]}, POS,
                                    measure_index={})
    assert "no declara ningun rol" in str(exc.value)
    custom_visuals.invalidate_cache()


# --------------------------------------------- lo propio del visual se conserva ---
def test_se_conserva_la_configuracion_propia_del_visual(informe):
    """`connection` lleva baseUrl y el token: sin eso el visor carga vacio."""
    _plantilla(informe, GUID_VISOR, objects={
        "connection": [{"properties": {
            "baseUrl": {"expr": {"Literal": {"Value": "'https://ejemplo'"}}},
            "mt": {"expr": {"Literal": {"Value": "'token123'"}}}}}],
        "viewer": [{"properties": {
            "showBrand": {"expr": {"Literal": {"Value": "true"}}}}}]})
    r = visual_factory.build_visual(informe, GUID_VISOR,
                                    {"dbids": ["M[a]"]}, POS, measure_index={})
    objetos = r["visual"]["visual"]["objects"]
    props = objetos["connection"][0]["properties"]
    assert "baseUrl" in props and "mt" in props
    assert "viewer" in objetos


def test_se_descarta_la_seleccion_puntual_heredada(informe):
    """scopeIds de la plantilla: apuntan a filas que este visual no tiene."""
    _plantilla(informe, GUID_VISOR, objects={
        "connection": [{"properties": {
            "baseUrl": {"expr": {"Literal": {"Value": "'https://ejemplo'"}}}}}],
        "dataColors": [{"properties": {}, "selector": {"data": [
            {"scopeId": {"Comparison": {"ComparisonKind": 0}}}]}}]})
    r = visual_factory.build_visual(informe, GUID_VISOR,
                                    {"dbids": ["M[a]"]}, POS, measure_index={})
    objetos = r["visual"]["visual"]["objects"]
    assert "dataColors" not in objetos
    assert "connection" in objetos, "lo propio del visual NO se toca"


def test_un_wildcard_no_es_una_seleccion_puntual(informe):
    """`dataViewWildcard` significa 'todos los puntos': se conserva."""
    _plantilla(informe, GUID_VISOR, objects={
        "dataColors": [{"properties": {"fill": {"solid": {"color": {
            "expr": {"Literal": {"Value": "'#FF0000'"}}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]})
    r = visual_factory.build_visual(informe, GUID_VISOR,
                                    {"dbids": ["M[a]"]}, POS, measure_index={})
    assert "dataColors" in r["visual"]["visual"]["objects"]
