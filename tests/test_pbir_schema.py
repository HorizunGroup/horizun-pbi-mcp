"""Fase E3.1 — validacion contra los esquemas PBIR OFICIALES completos.

El validador de E3 era casero y solo interpretaba `type`, `required`,
`properties`, `items` y `enum`, contra esquemas escritos a mano. Ignoraba
`$ref`, `anyOf`, `oneOf`, `allOf`, `additionalProperties`, `pattern`,
`minimum`... es decir, casi todo lo que el PBIR usa de verdad.

Ahora se usa `jsonschema` contra los documentos oficiales exactos (12, con su
cierre transitivo de `$ref`), resueltos desde un registry local construido solo
con el manifiesto: cero red, cero URLs arbitrarias.

Las pruebas marcadas REGLA-OFICIAL fallan contra 66c5189 porque el validador
anterior ignoraba esa regla.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import pbir_reader, project_locator
from horizun_pbi_mcp.services import pbir_schema
from horizun_pbi_mcp.services import txn as txn_service
from horizun_pbi_mcp.services.pbir_schema import (SchemaUnavailable, SchemaUnsupported,
                                  SchemaValidationFailed)
from tests.fixtures import synthetic

VISUAL = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
          "definition/visualContainer/2.7.0/schema.json")
PAGE = ("https://developer.microsoft.com/json-schemas/fabric/item/report/"
        "definition/page/2.1.0/schema.json")

pytestmark = pytest.mark.skipif(
    not pbir_schema.estado_cache()["ready"],
    reason=("Los esquemas oficiales del PBIR no estan instalados. "
            "Ejecuta: python scripts/fetch_pbir_schemas.py"))


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    return session.require_active_pbip(), pbip.parent


def visual_valido(**cambios):
    base = {"$schema": VISUAL, "name": "abc123",
            "position": {"x": 0, "y": 0, "width": 100, "height": 100},
            "visual": {"visualType": "card"}}
    base.update(cambios)
    return base


def ruta_pbir(tmp_path, nombre="visual.json"):
    """Ruta dentro de un arbol de informe, que es donde el validador aplica."""
    d = tmp_path / "Demo.Report" / "definition" / "pages" / "p1" / "visuals" / "v1"
    d.mkdir(parents=True, exist_ok=True)
    return d / nombre


# ======================================== procedencia: oficiales y verificados ==
def test_el_manifiesto_cubre_las_raices_que_escribimos():
    """Las cinco que este servidor produce, mas las que aparecen en informes
    reales y hay que saber leer."""
    raices = [d for d in pbir_schema.manifiesto()["documents"] if d["root"]]
    urls = " ".join(d["url"] for d in raices)
    for escribimos in ("visualContainer/2.7.0", "page/2.1.0",
                       "pagesMetadata/1.1.0", "report/2.0.0",
                       "definitionProperties/2.0.0"):
        assert escribimos in urls, f"falta la raiz que escribimos: {escribimos}"
    for d in raices:
        assert d["url"].startswith("https://developer.microsoft.com/json-schemas/fabric/")
        assert len(d["sha256"]) == 64


def test_el_manifiesto_incluye_el_cierre_transitivo():
    """Los $ref externos (semanticQuery, filterConfiguration...) tambien."""
    docs = pbir_schema.manifiesto()["documents"]
    assert len(docs) > 5, "faltan las referencias transitivas"
    nombres = " ".join(d["url"] for d in docs)
    for necesario in ("semanticQuery", "filterConfiguration",
                      "formattingObjectDefinitions", "visualConfiguration"):
        assert necesario in nombres, f"falta el cierre de {necesario}"


def test_el_manifiesto_declara_que_no_se_redistribuyen():
    m = pbir_schema.manifiesto()
    assert "NO VERIFICADA" in m["redistribution"]
    assert m["retrieved"]


def test_los_esquemas_no_estan_en_el_repositorio():
    """No se copian: no declaran permiso de redistribucion."""
    repo = Path(__file__).resolve().parent.parent
    vendidos = list((repo / "src" / "services" / "schemas").glob("pbir/*.json"))
    assert not vendidos, (
        f"hay esquemas copiados al repositorio: {vendidos}. Se instalan con "
        "scripts/fetch_pbir_schemas.py")


def test_la_cache_esta_verificada_por_hash():
    estado = pbir_schema.estado_cache()
    assert estado["ready"] is True
    assert estado["missing"] == [] and estado["corrupt"] == []


def test_un_esquema_con_hash_alterado_da_schema_unavailable(tmp_path, monkeypatch):
    falsa = tmp_path / "cache"
    falsa.mkdir()
    for d in pbir_schema.manifiesto()["documents"]:
        (falsa / d["file"]).write_text('{"type":"object"}', encoding="utf-8")

    monkeypatch.setattr(pbir_schema, "cache_dir", lambda: falsa)
    pbir_schema.limpiar_cache_memoria()
    try:
        estado = pbir_schema.estado_cache()
        assert estado["ready"] is False
        assert len(estado["corrupt"]) == len(pbir_schema.manifiesto()["documents"])

        with pytest.raises(SchemaUnavailable) as exc:
            pbir_schema.validar(visual_valido())
        assert exc.value.code == "schema_unavailable"
    finally:
        pbir_schema.limpiar_cache_memoria()


def test_sin_cache_las_escrituras_fallan_cerradas(tmp_path, monkeypatch):
    """Sin esquemas NO se degrada a 'no compruebo nada'."""
    monkeypatch.setattr(pbir_schema, "cache_dir", lambda: tmp_path / "vacia")
    pbir_schema.limpiar_cache_memoria()
    try:
        with pytest.raises(SchemaUnavailable):
            pbir_schema.validar(visual_valido())
    finally:
        pbir_schema.limpiar_cache_memoria()


def test_no_se_toca_la_red_al_validar(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("se intento una descarga durante la validacion")))
    assert pbir_schema.validar(visual_valido())["validated"] is True


def test_se_usa_el_draft_que_declara_el_esquema():
    r = pbir_schema.validar(visual_valido())
    assert r["draft"] == "http://json-schema.org/draft-07/schema#"
    assert r["validator"] == "Draft7Validator"


# ================ REGLA-OFICIAL: lo que el validador casero ignoraba ==========
def test_regla_oficial_ref_a_otro_documento():
    """`query` referencia semanticQuery por $ref: antes ni se miraba."""
    malo = visual_valido()
    malo["visual"]["query"] = {"queryState": {"Values": {"projections": "no-es-lista"}}}
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo)
    assert exc.value.details["error_count"] >= 1


def test_regla_oficial_additional_properties():
    """El esquema prohibe propiedades desconocidas en `position`."""
    malo = visual_valido(position={"x": 0, "y": 0, "width": 10, "height": 10,
                                   "propiedadInventada": 1})
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo)
    reglas = {e["rule"] for e in exc.value.details["errors"]}
    assert "additionalProperties" in reglas, (
        f"no se aplico additionalProperties; reglas vistas: {reglas}")


def test_regla_oficial_anyof_de_const():
    """`howCreated` es un anyOf de 16 `const` en el esquema oficial.

    Ni `anyOf` ni `const` existian en el validador casero: este valor pasaba.
    """
    malo = visual_valido(howCreated="InventadoPorMi")
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo)
    reglas = {e["rule"] for e in exc.value.details["errors"]}
    assert reglas & {"anyOf", "const"}, f"reglas vistas: {reglas}"


def test_regla_oficial_const_del_propio_schema():
    """El esquema fija el valor EXACTO de `$schema` con `const`."""
    base, _url = pbir_schema.cargar(VISUAL)
    assert base["properties"]["$schema"]["const"] == VISUAL, (
        "el esquema oficial ya no fija $schema con const; revisa la prueba")


def test_regla_oficial_tipo_anidado_invalido():
    malo = visual_valido()
    malo["visual"]["visualType"] = 123
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo)
    rutas = [e["path"] for e in exc.value.details["errors"]]
    assert any("visualType" in r for r in rutas)


def _campo_de_prueba():
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "qa"}},
                        "Property": "Puntaje"}}


def test_el_formato_condicional_que_generamos_pasa_la_barrera_de_objects():
    """La forma buena viene del escritor, no de un JSON montado por la prueba."""
    from horizun_pbi_mcp.pbip import conditional_format

    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    conditional_format.apply_to_visual(documento, _campo_de_prueba(),
                                       "#FFFFFF", "#2A78D6")

    assert pbir_schema.validar(documento)["validated"] is True


def test_un_expr_solido_que_no_es_fillrule_se_bloquea_aunque_el_schema_lo_acepte():
    """Un ``solid.expr`` literal no es un formato condicional valido.

    Desktop reserva ``solid.expr`` para ``FillRule``; un color literal debe
    vivir en ``solid.color.expr``. El esquema oficial deja ambos caminos
    abiertos, por eso esta barrera es necesaria.
    """
    documento = visual_valido()
    documento["visual"]["objects"] = {
        "values": [{"properties": {
            "backColor": {"solid": {"expr": {
                "Literal": {"Value": "'#FFFFFF'"},
            }}},
        }}]}

    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(documento)

    assert exc.value.details["rule"] == "format_objects"
    assert exc.value.details["errors"] == [{
        "path": "$.visual.objects.values[0].properties.backColor.solid.color",
        "rule": "format_solid_color",
        "error": "La estructura de formato PBIR no tiene la forma esperada.",
    }]


def test_las_anclas_numericas_del_escritor_pasan_la_barrera():
    from horizun_pbi_mcp.pbip import conditional_format

    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    conditional_format.apply_to_visual(
        documento, _campo_de_prueba(), "#FFFFFF", "#2A78D6",
        min_value=0, max_value=100)

    assert pbir_schema.validar(documento)["validated"] is True


def test_una_parada_con_expr_de_mas_se_bloquea():
    """Regresion Torre Aurora: un `expr` de mas dentro de una parada dejaba el
    mapa de calor sin pintar con todos los validadores en verde."""
    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    documento["visual"]["objects"] = {
        "values": [{"properties": {
            "backColor": {"solid": {"color": {"expr": {"FillRule": {
                "Input": _campo_de_prueba(),
                "FillRule": {"linearGradient2": {
                    "min": {"color": {"expr": {
                        "Literal": {"Value": "'#FFFFFF'"}}}},
                    "max": {"color": {"Literal": {"Value": "'#2A78D6'"}}},
                }}}}}}},
        }}]}

    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(documento)
    reglas = {e["rule"] for e in exc.value.details["errors"]}
    assert "format_gradient_color_literal" in reglas


def test_un_ancla_malformada_se_bloquea():
    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    documento["visual"]["objects"] = {
        "values": [{"properties": {
            "backColor": {"solid": {"color": {"expr": {"FillRule": {
                "Input": _campo_de_prueba(),
                "FillRule": {"linearGradient2": {
                    "min": {"color": {"Literal": {"Value": "'#FFFFFF'"}},
                            "value": {"expr": {"Literal": {"Value": "0D"}}}},
                    "max": {"color": {"Literal": {"Value": "'#2A78D6'"}}},
                }}}}}}},
        }}]}

    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(documento)
    reglas = {e["rule"] for e in exc.value.details["errors"]}
    assert "format_gradient_anchor" in reglas


def test_el_color_por_valor_de_campo_pasa_la_barrera():
    """El modo 'Field value' escribe el campo como expresion de color."""
    from horizun_pbi_mcp.pbip import conditional_format

    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    conditional_format.apply_field_value_to_visual(
        documento, _campo_de_prueba())

    assert pbir_schema.validar(documento)["validated"] is True


def test_un_expr_vacio_se_bloquea_aunque_el_schema_lo_acepte():
    documento = visual_valido()
    documento["visual"]["visualType"] = "pivotTable"
    documento["visual"]["objects"] = {
        "values": [{"properties": {
            "backColor": {"solid": {"color": {"expr": {}}}},
        }}]}

    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(documento)

    assert exc.value.details["rule"] == "format_objects"
    assert exc.value.details["errors"][0]["rule"] == "format_expression_empty"


def test_el_oraculo_completo_bloquea_propiedad_desconocida(monkeypatch):
    """El esquema acepta ``objects`` abierto, Desktop no debe hacerlo."""
    from horizun_pbi_mcp.services import format_oracle

    documento = visual_valido()
    documento["visual"]["objects"] = {
        "values": [{"properties": {"propiedadInventada": _campo_de_prueba()}}]
    }
    monkeypatch.setattr(
        format_oracle, "compare_all_managed_objects",
        lambda _doc, **_kw: {"equivalence_checked": True,
                      "errors": [{"path": "$.visual.objects.values[0].properties.propiedadInventada",
                                   "rule": "format_property_unknown"}],
                      "visual_type": "card"})
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(documento)
    assert exc.value.details["rule"] == "format_oracle"
    assert exc.value.details["errors"][0]["rule"] == "format_property_unknown"


def test_regla_oficial_pattern_o_enum_en_page():
    """`page.json` restringe `displayOption` a un conjunto."""
    doc = {"$schema": PAGE, "name": "p1", "displayName": "P",
           "displayOption": "ValorQueNoExiste"}
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(doc)
    reglas = {e["rule"] for e in exc.value.details["errors"]}
    assert reglas & {"enum", "const", "pattern", "anyOf", "oneOf"}, (
        f"reglas vistas: {reglas}")


def test_propiedad_obligatoria_ausente(tmp_path):
    malo = visual_valido()
    del malo["position"]
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo, archivo=ruta_pbir(tmp_path))
    assert exc.value.code == "schema_validation_failed"
    assert any(e["rule"] == "required" for e in exc.value.details["errors"])


# =========================================================== taxonomia ========
def test_version_futura_de_otra_mayor_se_bloquea():
    """Un salto de version MAYOR no se aproxima: ahi el formato puede romper.

    Dentro de la misma mayor si se comprueba contra la anterior (ver
    `test_version_no_publicada_cae_a_la_anterior`), porque esta medido que el
    formato crece por adicion. Entre mayores no hay tal garantia.
    """
    futuro = visual_valido()
    futuro["$schema"] = VISUAL.replace("2.7.0", "9.9.0")
    with pytest.raises(SchemaUnsupported) as exc:
        pbir_schema.validar(futuro)
    assert exc.value.code == "schema_unsupported"


def test_version_futura_inventada_de_la_misma_mayor_se_bloquea():
    futuro = visual_valido()
    futuro["$schema"] = VISUAL.replace("2.7.0", "2.999.999")

    with pytest.raises(SchemaUnsupported) as exc:
        pbir_schema.validar(futuro)

    assert exc.value.code == "schema_unsupported"


def test_version_no_publicada_cae_a_la_anterior():
    """2.10/2.11 no existen aun en el origen; se comprueba contra 2.7.0.

    Lo unico que se perdona es la cadena de version del propio `$schema`. Se
    midio sobre 275 archivos reales: nada mas del contenido discrepaba.
    """
    doc = visual_valido()
    doc["$schema"] = VISUAL.replace("2.7.0", "2.11.0")
    r = pbir_schema.validar(doc)
    assert r["validated"] is True
    assert r["degraded"] is True
    assert r["checked_against"] == VISUAL
    assert [t["path"] for t in r["tolerated"]] == ["$.$schema"]


def test_la_aproximacion_no_perdona_un_error_de_verdad():
    """Comprobar contra la anterior no puede convertirse en no comprobar."""
    doc = visual_valido()
    doc["$schema"] = VISUAL.replace("2.7.0", "2.11.0")
    del doc["position"]                      # obligatorio en toda version
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(doc)
    assert any(e["rule"] == "required" for e in exc.value.details["errors"])


def test_referencia_remota_no_permitida_se_bloquea():
    ajeno = visual_valido()
    ajeno["$schema"] = "https://ejemplo.invalido/otro/schema.json"
    with pytest.raises(SchemaUnsupported) as exc:
        pbir_schema.validar(ajeno)
    assert "manifiesto" in exc.value.message.lower()


def test_tipo_pbir_sin_schema_se_bloquea(tmp_path):
    """E3.1 punto 9: no se acepta genericamente un archivo sin $schema."""
    with pytest.raises(SchemaUnsupported) as exc:
        pbir_schema.validar({"name": "x"}, archivo=ruta_pbir(tmp_path))
    assert exc.value.details["rule"] == "tipo_pbir_exige_schema"


def test_tipo_desconocido_dentro_del_informe_se_bloquea(tmp_path):
    with pytest.raises(SchemaUnsupported) as exc:
        pbir_schema.validar({"algo": 1},
                            archivo=ruta_pbir(tmp_path, "inventado.json"))
    assert exc.value.details["rule"] == "tipo_desconocido_en_pbir"


def test_excepcion_documentada_se_omite(tmp_path):
    r = pbir_schema.validar({"algo": 1},
                            archivo=ruta_pbir(tmp_path, "localSettings.json"))
    assert r["validated"] is False
    assert r["reason"] == "excepcion documentada"


def test_fuera_del_informe_no_se_valida(tmp_path):
    """El validador PBIR no opina sobre archivos que no son del informe."""
    r = pbir_schema.validar({"a": 1}, archivo=tmp_path / "cualquiera.json")
    assert r["validated"] is False
    assert "no es un documento del PBIR" in r["reason"]


def test_el_error_no_filtra_valores(tmp_path):
    """jsonschema pone el valor en el mensaje; aqui se quita."""
    malo = visual_valido()
    malo["visual"]["visualType"] = "SECRETO-COMERCIAL-XYZ"
    malo["position"] = {"x": "OTRO-DATO-SENSIBLE", "y": 0,
                        "width": 10, "height": 10}
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(malo, archivo=ruta_pbir(tmp_path))

    texto = json.dumps(exc.value.details, ensure_ascii=False)
    assert "OTRO-DATO-SENSIBLE" not in texto
    assert "SECRETO-COMERCIAL-XYZ" not in texto
    assert "$.position.x" in texto, "debe decir QUE ruta falla"


# ============================== enganchado a la transaccion, antes del commit ==
def test_una_escritura_invalida_aborta_la_transaccion(proyecto, isolated_settings):
    active, raiz = proyecto
    antes = huella(raiz)
    destino = Path(pbir_reader.list_visuals(
        active, pbir_reader.list_pages(active)[0]["display_name"])[0]["file"])

    malo = visual_valido()
    del malo["position"]

    with pytest.raises(SchemaValidationFailed):
        with txn_service.project_transaction(
                active, [destino], tool="prueba_esquema") as t:
            t.write_json(destino, malo)

    assert huella(raiz) == antes


def test_un_lote_se_revierte_entero_si_uno_no_cumple(proyecto, isolated_settings):
    active, raiz = proyecto
    antes = huella(raiz)
    pagina = pbir_reader.list_pages(active)[0]["display_name"]
    visuales = pbir_reader.list_visuals(active, pagina)
    if len(visuales) < 2:
        pytest.skip("hacen falta dos visuales")

    destinos = [Path(v["file"]) for v in visuales[:2]]
    bueno = json.loads(destinos[0].read_text(encoding="utf-8-sig"))
    bueno["position"]["x"] = 42
    malo = visual_valido()
    del malo["name"]

    with pytest.raises(SchemaValidationFailed):
        with txn_service.project_transaction(
                active, destinos, tool="prueba_lote") as t:
            t.write_json(destinos[0], bueno)
            t.write_json(destinos[1], malo)

    assert huella(raiz) == antes


def test_los_fixtures_cumplen_el_esquema_oficial(proyecto):
    active, raiz = proyecto
    comprobados = 0
    for f in raiz.rglob("*.json"):
        datos = json.loads(f.read_text(encoding="utf-8-sig"))
        if isinstance(datos, dict) and datos.get("$schema"):
            pbir_schema.validar(datos, archivo=f)
            comprobados += 1
    assert comprobados >= 3


def test_el_generador_produce_pbir_valido(proyecto, isolated_settings):
    """Lo que escribimos cumple el esquema oficial, no solo el de andar por casa."""
    from horizun_pbi_mcp.services import pbir_edit

    active, _raiz = proyecto
    pagina = pbir_reader.list_pages(active)[0]["display_name"]
    r = pbir_edit.duplicate_page(active, pagina, "Copia validada")
    assert r["page_id"]


# =============== E3.1: lo que Power BI declara y Microsoft no publica ========
def _sin_version_anterior():
    """Un `$schema` no publicado que ademas no tiene familia contra la que caer."""
    for url in pbir_schema.no_publicados():
        if pbir_schema.version_mas_cercana(url) is None:
            return url
    return None


def test_esquema_no_publicado_upstream_se_escribe_pero_marcado():
    """Sin esquema NI version anterior, se escribe y se dice que no se comprobo.

    Bloquear aqui no protegia el archivo: empujaba a editarlo a mano, sin
    transaccion ni journal. Lo que no se puede es fingir que se valido.
    """
    url = _sin_version_anterior()
    assert url, "el manifiesto ya no registra esquemas no publicados sin fallback"

    doc = visual_valido()
    doc["$schema"] = url
    r = pbir_schema.validar(doc)
    assert r["validated"] is False
    assert r["schema_unchecked"] is True
    assert r["rule"] == "no_publicado_upstream"
    assert r["schema"] == url


def test_esquema_no_publicado_no_perdona_un_formato_imposible():
    """Que no haya esquema no apaga las reglas estructurales de formato."""
    url = _sin_version_anterior()
    assert url

    doc = visual_valido()
    doc["$schema"] = url
    doc["visual"]["objects"] = {
        "values": [{"properties": {"backColor": {"solid": {"color": {"expr": {}}}}}}]}
    with pytest.raises(SchemaValidationFailed) as exc:
        pbir_schema.validar(doc)
    assert exc.value.details["rule"] == "format_objects"


def test_sin_cache_instalada_se_sigue_fallando_cerrado(monkeypatch):
    """Que falte la instalacion NO es el hueco de Microsoft: eso si bloquea."""
    monkeypatch.setattr(pbir_schema, "estado_cache",
                        lambda: {"ready": False, "reason": "0 de 22 presentes",
                                 "missing": ["x.json"], "corrupt": []})
    with pytest.raises(SchemaUnavailable):
        pbir_schema.validar(visual_valido())


def test_el_manifiesto_documenta_los_no_publicados():
    m = pbir_schema.manifiesto()
    assert "unavailable_upstream" in m
    for a in m["unavailable_upstream"]:
        assert a["url"].startswith("https://developer.microsoft.com/")
        assert a["reason"]


def test_los_recursos_de_visuales_personalizados_no_son_pbir(tmp_path):
    """`CustomVisuals/` y `StaticResources/` estan dentro del informe pero no
    son documentos PBIR: formato ajeno, publicado por un tercero."""
    for carpeta, nombre in (("CustomVisuals", "package.json"),
                            ("StaticResources", "tema.json")):
        d = tmp_path / "Demo.Report" / carpeta / "algo"
        d.mkdir(parents=True, exist_ok=True)
        r = pbir_schema.validar({"author": "x"}, archivo=d / nombre)
        assert r["validated"] is False
        assert "no es un documento del PBIR" in r["reason"]


def test_se_cubren_las_versiones_de_informes_reales():
    """El manifiesto no puede limitarse a lo que ESCRIBIMOS: para leer un
    informe ajeno hacen falta sus versiones."""
    urls = " ".join(d["url"] for d in pbir_schema.manifiesto()["documents"])
    for necesario in ("bookmark/", "versionMetadata/", "report/3.3.0",
                      "pagesMetadata/1.0.0", "localSettings/"):
        assert necesario in urls, f"el manifiesto no cubre {necesario}"
