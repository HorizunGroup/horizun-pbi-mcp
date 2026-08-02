"""La unica pregunta que importa: lo que generamos, ¿abre?

Por que existe
--------------
La suite tenia 1169 pruebas en verde mientras el generador de visuales
producia informes que Power BI no podia mostrar. No es que las pruebas
estuvieran mal escritas: es que ninguna le preguntaba a un oraculo REAL. Se
comprobaba que el JSON tuviera la forma esperada, y la forma esperada la
definia el mismo codigo que se estaba probando.

Aqui se pregunta a los dos unicos jueces que no son nuestros:

1. `TmdlSerializer` — el MISMO codigo que usa Power BI para leer el modelo.
2. El CLI oficial `@microsoft/powerbi-report-authoring-cli` — el que sabe que
   roles admite cada tipo de visual, que es justo lo que no se puede deducir.

Los tres defectos que esto encontro la primera vez que se ejecuto:

* `cardVisual` declaraba el rol `Values`; PBIR exige `Data`. El tipo estaba
  anunciado como soportado y SIEMPRE generaba un informe invalido.
* El rol se buscaba con `fields.get(rol)`, sensible a mayusculas: `{"Values":
  [...]}` —que es como lo escribe cualquiera, porque es el nombre que sale en
  el propio visual.json— no casaba con la clave `values` y el visual se
  escribia SIN datos, sin error.
* Un rol mal escrito junto a uno bueno desaparecia sin ni siquiera un aviso.

Los tres comparten sintoma: el informe abre y pinta un visual vacio. Eso es
peor que no abrir, porque nadie va a buscar un error que nunca se dio.

Coste
-----
Estas pruebas arrancan procesos y leen DLL: son lentas y no corren en CI, que
no tiene ni Node ni Analysis Services. Se marcan `abre` y se omiten solas.
Las de mas abajo —contrato de roles y viaje de ida y vuelta— no necesitan
ningun oraculo y corren siempre: son la red que queda en CI.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from config import ActivePbip
from pbip import (bookmarks, filter_builder, pbip_scaffold, pbir_reader,
                  table_from_file, theme, tmdl_reader, tmdl_writer,
                  visual_factory)
from powerbi.errors import VisualFactoryError
from services import page_spec, report_validator, tmdl_validate


# ============================================================ disponibilidad ==
def _hay_tom() -> bool:
    try:
        from powerbi.clr_bootstrap import load_tom

        load_tom()
        return True
    except Exception:                                        # pragma: no cover
        return False


def _hay_cli() -> bool:
    try:
        return bool(report_validator.estado()["available"])
    except Exception:                                        # pragma: no cover
        return False


requiere_oraculos = pytest.mark.skipif(
    not (_hay_tom() and _hay_cli()),
    reason="hacen falta las DLL de Analysis Services y el CLI oficial "
           "(python scripts/fetch_report_validator.py)")


# ================================================================= proyecto ===
CSV_VENTAS = ("Fecha,Region,Producto,Unidades,Importe\n"
              "2026-01-15,Norte,Cemento,120,1450.75\n"
              "2026-02-20,Sur,Acero,85,2310.40\n"
              "2026-03-05,Norte,Cemento,200,2900.00\n")
CSV_REGIONES = "Region,Zona,Meta\nNorte,A,5000\nSur,B,4000\n"


@pytest.fixture
def proyecto_real(tmp_path, session, isolated_settings):
    """Un .pbip construido con los generadores DE VERDAD, no a mano.

    Un fixture escrito a mano solo demuestra que el validador acepta lo que el
    fixture escribio. Aqui todo lo que se valida lo ha producido el servidor.
    """
    r = pbip_scaffold.crear_proyecto(tmp_path, "Gen", culture="es-ES")
    active = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Gen.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Gen",
        has_pbir=True, has_tmdl=True)
    session.set_active_pbip(active)

    datos = tmp_path / "datos"
    datos.mkdir()
    (datos / "Ventas.csv").write_text(CSV_VENTAS, encoding="utf-8")
    (datos / "Regiones.csv").write_text(CSV_REGIONES, encoding="utf-8")
    table_from_file.agregar_tabla(active, datos / "Ventas.csv", "Ventas")
    table_from_file.agregar_tabla(active, datos / "Regiones.csv", "Regiones")
    tmdl_writer.create_measure_pbip(active, "Ventas", "Importe Total",
                                    "SUM(Ventas[Importe])", format_string="#,0.00")
    return active, tmdl_reader.read_semantic_model(active)


def _errores_pbir(active) -> list:
    res = report_validator.validar_informe(Path(active.report_dir))
    assert res.status != report_validator.UNAVAILABLE, res.detail
    return [d for d in res.diagnostics if d.severity == "error"]


# ==================================================== 1. el modelo que abre ===
@pytest.mark.abre
@requiere_oraculos
def test_el_modelo_generado_lo_lee_el_serializador_de_microsoft(proyecto_real):
    """Esqueleto + tablas desde archivo + medida, leido por TmdlSerializer.

    `parsed` es la respuesta literal a "¿esto abrira?". Si es False, Power BI
    Desktop tampoco lo abrira, y da igual lo que opine el lint propio.
    """
    active, _ = proyecto_real
    definicion = Path(active.semantic_model_dir) / "definition"

    v = tmdl_validate.validate(definicion, use_tom=True)

    assert v["parse_checked"] is True, v["parse_skipped_reason"]
    assert v["parsed"] is True, v.get("parse_error")
    assert v["valid"] is True, [f["rule"] for f in v["findings"]
                                if f["severity"] == "error"]
    # El serializador tiene que ver las dos tablas: una tabla escrita en disco
    # pero no declarada en model.tmdl no existe para el motor.
    assert v["tom_counts"]["tables"] == 2


@pytest.mark.abre
@requiere_oraculos
def test_el_esqueleto_recien_creado_no_trae_ni_un_error(tmp_path, session):
    """El punto de partida tiene que estar limpio, o arrastra el error a todo."""
    r = pbip_scaffold.crear_proyecto(tmp_path, "Vacio")
    active = ActivePbip(
        pbip_path=str(Path(r["project_dir"]) / "Vacio.pbip"),
        project_dir=r["project_dir"], report_dir=r["report_dir"],
        semantic_model_dir=r["semantic_model_dir"], report_name="Vacio",
        has_pbir=True, has_tmdl=True)

    assert _errores_pbir(active) == []


# ============================== 2. cada tipo de visual que decimos soportar ===
#: Campos minimos por tipo, en el rol que el tipo admite de verdad.
CAMPOS = {
    "card": {"values": ["Ventas[Importe Total]"]},
    "cardVisual": {"values": ["Ventas[Importe Total]"]},
    "tableEx": {"values": ["Regiones[Zona]", "Ventas[Importe Total]"]},
    "pivotTable": {"rows": ["Regiones[Zona]"], "values": ["Ventas[Importe Total]"]},
    "slicer": {"values": ["Regiones[Zona]"]},
    "barChart": {"category": ["Regiones[Zona]"],
                 "values": ["Ventas[Importe Total]"]},
    "columnChart": {"category": ["Regiones[Zona]"],
                    "values": ["Ventas[Importe Total]"]},
    "clusteredBarChart": {"category": ["Regiones[Zona]"],
                          "values": ["Ventas[Importe Total]"]},
    "clusteredColumnChart": {"category": ["Regiones[Zona]"],
                             "values": ["Ventas[Importe Total]"]},
    "lineChart": {"category": ["Regiones[Zona]"],
                  "values": ["Ventas[Importe Total]"]},
    "pieChart": {"category": ["Regiones[Zona]"],
                 "values": ["Ventas[Importe Total]"]},
    "gauge": {"values": ["Ventas[Importe Total]"]},
    "kpi": {"values": ["Ventas[Importe Total]"]},
    "donutChart": {"category": ["Regiones[Zona]"],
                   "values": ["Ventas[Importe Total]"]},
    "areaChart": {"category": ["Regiones[Zona]"],
                  "values": ["Ventas[Importe Total]"]},
    "scatterChart": {"x": ["Ventas[Importe]"], "y": ["Ventas[Unidades]"]},
    "treemap": {"category": ["Regiones[Zona]"],
                "values": ["Ventas[Importe Total]"]},
    "funnel": {"category": ["Regiones[Zona]"],
               "values": ["Ventas[Importe Total]"]},
    "waterfallChart": {"category": ["Regiones[Zona]"],
                       "values": ["Ventas[Importe Total]"]},
    "multiRowCard": {"values": ["Ventas[Importe Total]"]},
    "ribbonChart": {"category": ["Regiones[Zona]"],
                    "values": ["Ventas[Importe Total]"]},
    visual_factory.HTML_CONTENT_TYPE: {
        "content": ["Ventas[Importe Total]"]},
}


@pytest.mark.abre
@requiere_oraculos
def test_todos_los_visuales_con_datos_pasan_el_validador_oficial(proyecto_real):
    """Una pagina por tipo, y UNA sola pasada del CLI sobre el informe entero.

    Es la prueba que faltaba: `cardVisual` llevaba anunciado como soportado
    generando siempre un informe invalido, y nada lo decia.
    """
    active, md = proyecto_real

    for tipo, campos in CAMPOS.items():
        spec = {"schema_version": "1.0",
                "page": {"name": f"pg {tipo}", "displayName": tipo},
                "visuals": [{"type": tipo, "title": tipo,
                             "position": {"x": 20, "y": 20,
                                          "width": 400, "height": 300},
                             "fields": campos}]}
        compilado = page_spec.compile_spec(active, spec, md)
        page_spec.apply_spec(active, compilado)

    errores = _errores_pbir(active)
    assert errores == [], [f"{d.code} {d.path} {d.file}" for d in errores]


def _escribir_pagina_cruda(active, page_id: str, visual: dict) -> None:
    """Escribe una pagina SIN pasar por la barrera de validacion.

    `apply_spec` se niega a escribir en cuanto el informe empeora, que es
    justo lo que se quiere en produccion y justo lo que estorba aqui: para
    preguntarle al CLI que roles conoce hay que poder ponerle delante un rol
    que quiza no conozca.
    """
    pages = Path(active.report_dir) / "definition" / "pages"
    destino = pages / page_id / "visuals" / visual["name"]
    destino.mkdir(parents=True, exist_ok=True)
    (pages / page_id / "page.json").write_text(json.dumps({
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/page/2.1.0/schema.json"),
        "name": page_id, "displayName": page_id,
        "width": 1280, "height": 720, "displayOption": "FitToPage"},
        indent=2), encoding="utf-8")
    (destino / "visual.json").write_text(
        json.dumps(visual, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = pages / "pages.json"
    datos = json.loads(meta.read_text(encoding="utf-8"))
    if page_id not in datos["pageOrder"]:
        datos["pageOrder"].append(page_id)
    meta.write_text(json.dumps(datos, indent=2), encoding="utf-8")


@pytest.mark.abre
@requiere_oraculos
def test_cada_rol_declarado_existe_de_verdad_en_pbir(proyecto_real):
    """Cada rol de ROLE_MAP se escribe y se le pregunta al CLI si existe.

    `PBIR_ROLE_UNKNOWN` es la respuesta a "ese rol te lo has inventado". Es lo
    que delataba a `cardVisual` —declaraba `Values`, PBIR quiere `Data`— y lo
    que impide volver a declarar un rol porque suene razonable.

    Solo se miran los `PBIR_ROLE_UNKNOWN`: escribir un rol suelto deja el
    visual incompleto y eso produce otros errores que aqui no interesan.
    """
    active, _ = proyecto_real
    indice = {}

    for tipo, role_map in sorted(visual_factory.ROLE_MAP.items()):
        for orden, logico in enumerate(role_map):
            campos = {k: list(v) for k, v in CAMPOS.get(tipo, {}).items()}
            # El visual que se pregunta al CLI debe seguir completo: probar un
            # rol opcional aislado produciria REQUIRED_MISSING antes de poder
            # responder si ese rol existe. Si dos alias llegan a la misma clave
            # PBIR, se sustituye el anterior para no exceder cardinalidad.
            clave_probada = role_map[logico]
            for existente in list(campos):
                if role_map.get(existente) == clave_probada:
                    campos.pop(existente)
            refs = CAMPOS.get(tipo, {}).get(logico) or ["Ventas[Importe Total]"]
            campos[logico] = refs
            construido = visual_factory.build_visual(
                active, tipo, campos,
                {"x": 0, "y": 0, "width": 300, "height": 200},
                measure_index={"Importe Total": "Ventas"})
            pid = f"rol{len(indice):04d}".ljust(20, "0")
            construido["visual"]["name"] = f"vis{len(indice):04d}".ljust(20, "0")
            _escribir_pagina_cruda(active, pid, construido["visual"])
            indice[pid] = (tipo, logico, role_map[logico])

    desconocidos = [d for d in _errores_pbir(active)
                    if d.code == "PBIR_ROLE_UNKNOWN"]
    culpables = []
    for d in desconocidos:
        partes = Path(d.file).parts
        tipo, logico, clave = indice.get(partes[2], ("?", "?", d.path))
        culpables.append(f"ROLE_MAP['{tipo}']['{logico}'] = '{clave}'")
    assert culpables == [], (
        "PBIR no tiene estos roles: " + ", ".join(sorted(set(culpables))))


# ================================================ 3. el resto de la cadena ====
@pytest.mark.abre
@requiere_oraculos
def test_una_pagina_con_filtros_e_interacciones_pasa_el_validador(proyecto_real):
    """`interactions` estaba declarado, validado y era inservible.

    Referencia visuales por id, y los ids los genera el compilador: nadie que
    escriba un spec puede conocerlos. Todos los generadores del repositorio le
    pasaban `[]`, y por eso nadie descubrio que ademas dos de sus tres tipos no
    existian en PBIR. Aqui se escribe una de verdad y se valida.
    """
    active, md = proyecto_real

    compilado = page_spec.compile_spec(active, {
        "schema_version": "1.0",
        "page": {"name": "cruzada", "displayName": "Cruzada"},
        "visuals": [
            {"id": "seg", "type": "slicer", "title": "Zona",
             "position": {"x": 0, "y": 0, "width": 200, "height": 300},
             "fields": {"values": ["Regiones[Zona]"]}},
            {"id": "barras", "type": "barChart", "title": "Importe",
             "position": {"x": 220, "y": 0, "width": 500, "height": 300},
             "fields": {"category": ["Regiones[Zona]"],
                        "values": ["Ventas[Importe Total]"]},
             "filters": [{"field": "Regiones[Zona]", "values": ["A"]}]},
        ],
        "filters": [{"field": "Ventas[Producto]", "values": ["Cemento"]}],
        # Por 'id' y por posicion: las dos formas tienen que valer.
        "interactions": [{"source": "seg", "target": "barras", "type": "filter"},
                         {"source": 1, "target": 0, "type": "NoFilter"}],
    }, md)
    res = page_spec.apply_spec(active, compilado)

    assert _errores_pbir(active) == []

    pagina = json.loads(
        (Path(active.report_dir) / "definition" / "pages" / res["page_id"]
         / "page.json").read_text(encoding="utf-8"))
    interacciones = pagina["visualInteractions"]
    assert len(interacciones) == 2
    # Resueltas a ids reales, no al nombre que traia el spec.
    ids = {v["visual"]["name"] for v in compilado["visuals"]}
    assert {i["source"] for i in interacciones} <= ids
    assert {i["target"] for i in interacciones} <= ids
    assert interacciones[0]["type"] == "DataFilter"


@pytest.mark.abre
@requiere_oraculos
def test_un_mapa_de_calor_con_dos_medidas_pasa_el_validador(proyecto_real):
    """Dos degradados en la misma matriz, uno por medida.

    Antes solo se podia colorear una: la segunda borraba a la primera. Aqui se
    comprueba que las dos reglas conviven Y que el CLI oficial las acepta —el
    `selector.metadata` que las distingue no me lo he inventado, sale del
    esquema `formattingObjectDefinitions`—.
    """
    from pbip import conditional_format
    from utils.json_utils import read_json, write_json

    active, md = proyecto_real
    compilado = page_spec.compile_spec(active, {
        "schema_version": "1.0",
        "page": {"name": "calor", "displayName": "Mapa de calor"},
        "visuals": [{"type": "matrix", "title": "Calor",
                     "position": {"x": 20, "y": 20, "width": 800, "height": 400},
                     "fields": {"rows": ["Regiones[Zona]"],
                                "values": ["Ventas[Importe Total]"]}}]}, md)
    res = page_spec.apply_spec(active, compilado)

    visual = pbir_reader.list_visuals(active, res["page_id"])[0]
    ruta = Path(visual["file"])
    datos = read_json(ruta)
    for propiedad, color in [("Importe Total", "#2A78D6"),
                             ("Unidades", "#EB6834")]:
        conditional_format.apply_to_visual(
            datos, {"Measure": {"Expression": {"SourceRef": {"Entity": "Ventas"}},
                                "Property": propiedad}},
            "#FFFFFF", color)
    write_json(ruta, datos)

    bloques = datos["visual"]["objects"]["values"]
    assert len(bloques) == 2, "la segunda regla borro la primera"

    errores = _errores_pbir(active)
    assert errores == [], [f"{d.code} {d.path}" for d in errores]


@pytest.mark.abre
@requiere_oraculos
def test_tema_y_marcador_no_invalidan_el_informe(proyecto_real):
    """El tema y los marcadores tocan report.json: se validan como todo lo demas."""
    active, md = proyecto_real

    compilado = page_spec.compile_spec(active, {
        "schema_version": "1.0",
        "page": {"name": "Portada", "displayName": "Portada"},
        "visuals": [{"type": "card", "title": "Total",
                     "position": {"x": 20, "y": 20, "width": 300, "height": 200},
                     "fields": {"values": ["Ventas[Importe Total]"]}}]}, md)
    res = page_spec.apply_spec(active, compilado)

    theme.apply_theme(active, {"name": "Prueba",
                               "dataColors": ["#2A78D6", "#EB6834"],
                               "background": "#FFFFFF", "foreground": "#252423",
                               "tableAccent": "#2A78D6"})
    bookmarks.create_bookmark(active, "Vista inicial", res["page_id"])

    assert _errores_pbir(active) == []


# ================================================ contrato de roles (sin CLI) =
# Lo de aqui abajo no necesita oraculo: es la red que SI corre en CI.

@pytest.mark.parametrize("rol", ["values", "Values", "VALUES", "value", "measure"])
def test_el_rol_se_reconoce_escriba_como_escriba(rol):
    """`Values` es el nombre que sale en el propio visual.json.

    Buscarlo con distincion de mayusculas hacia que el visual se escribiera
    vacio: sin error, sin nada que mirar, y una tarjeta en blanco en el informe.
    """
    q = visual_factory._build_query("card", {rol: ["Ventas[Importe]"]}, {}, [])
    assert list(q["queryState"]) == ["Values"]


def test_un_rol_que_no_existe_se_acusa_en_vez_de_desaparecer():
    """Un rol mal escrito junto a uno bueno se perdia SIN ningun aviso."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory._build_query(
            "clusteredBarChart",
            {"category": ["V[R]"], "valeus": ["V[I]"]}, {}, [])
    assert "valeus" in exc.value.message
    assert "category" in str(exc.value.details["valid_roles"])


def test_un_rol_ajeno_al_tipo_se_acusa():
    """Una tarjeta no tiene leyenda. Aceptarlo callando pinta una tarjeta sola."""
    with pytest.raises(VisualFactoryError):
        visual_factory._build_query("card", {"legend": ["V[R]"]}, {}, [])


def test_un_sinonimo_solo_vale_si_significa_lo_mismo():
    """`details` es un rol propio en Power BI, no otra forma de decir `category`.

    Mandarlo a `category` colocaria un campo donde nadie lo pidio, que es el
    mismo defecto que el mapa de sinonimos existe para cerrar.
    """
    with pytest.raises(VisualFactoryError):
        visual_factory._build_query(
            "clusteredBarChart", {"details": ["V[R]"]}, {}, [])


def test_un_campo_que_no_es_un_campo_da_un_error_del_servidor():
    """`list(5)` seria un TypeError crudo en mitad del generador."""
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory._build_query("card", {"values": 5}, {}, [])
    assert "int" in exc.value.message


def test_el_orden_del_queryState_no_depende_del_orden_del_spec():
    """Dos specs equivalentes tienen que dar el MISMO visual.json.

    Si no, el diff de `page_update` ve cambios donde no los hay y reescribe
    paginas identicas en cada pasada.
    """
    a = visual_factory._build_query(
        "clusteredBarChart", {"category": ["V[R]"], "values": ["V[I]"]}, {}, [])
    b = visual_factory._build_query(
        "clusteredBarChart", {"values": ["V[I]"], "category": ["V[R]"]}, {}, [])
    assert json.dumps(a) == json.dumps(b)


def test_los_tipos_de_interaccion_son_los_del_esquema_oficial():
    """Ni uno inventado, ni uno de menos.

    `INTERACCIONES` decia `("NoFilter", "Filter", "Highlight")`. `Filter` y
    `Highlight` NO existen en PBIR —son `DataFilter` y `HighlightFilter`— y
    `Default` faltaba. Dos de los tres valores que se ofrecian producian una
    pagina que el esquema rechaza.

    Se lee del esquema CACHEADO: sin red y sin CLI, asi que corre siempre.
    """
    from services import pbir_schema

    esquema, _ = pbir_schema.cargar(
        "https://developer.microsoft.com/json-schemas/fabric/item/report/"
        "definition/page/2.1.0/schema.json")
    definicion = esquema["definitions"]["VisualInteractionFilterType"]
    oficiales = {opcion["const"] for opcion in definicion["anyOf"]}

    assert set(filter_builder.INTERACCIONES) == oficiales


def test_los_alias_de_interaccion_llegan_a_un_valor_oficial():
    """Aceptar `filter` esta bien; escribirlo en el archivo, no."""
    for alias in filter_builder._ALIAS_INTERACCION:
        r = filter_builder.build_interactions(
            [{"source": "a", "target": "b", "type": alias}])
        assert r[0]["type"] in filter_builder.INTERACCIONES


def test_cardVisual_usa_Data_y_no_Values():
    """El rol de `cardVisual` no se deduce: se comprobo contra el CLI oficial.

    Con `Values` el informe ENTERO queda invalido (PBIR_ROLE_UNKNOWN mas
    PBIR_ROLE_REQUIRED_MISSING sobre `Data`).
    """
    q = visual_factory._build_query(
        "cardVisual", {"values": ["Ventas[Importe]"]}, {}, [])
    assert list(q["queryState"]) == ["Data"]


@pytest.mark.parametrize("tipo", [
    "barChart", "columnChart", "clusteredBarChart", "clusteredColumnChart",
    "lineChart", "pieChart", "donutChart", "areaChart", "scatterChart",
    "treemap", "funnel", "waterfallChart", "gauge", "ribbonChart",
])
def test_los_visuales_con_tooltip_exponen_el_rol_oficial(tipo):
    assert visual_factory.roles_de(tipo)["tooltips"] == "Tooltips"


def test_linea_y_area_exponen_eje_secundario_y_small_multiples():
    for tipo in ("lineChart", "areaChart"):
        roles = visual_factory.roles_de(tipo)
        assert roles["y2"] == "Y2"
        assert roles["rows"] == "Rows"


@pytest.mark.abre
@pytest.mark.skipif(not _hay_cli(), reason="hace falta el CLI oficial")
def test_catalogo_oficial_cardVisual_confirma_value_y_label():
    """Mantiene el contrato de formato ligado al oraculo de Microsoft."""
    cli = report_validator.localizar()
    node = report_validator._node()
    assert cli is not None and node is not None

    proc = subprocess.run(
        [str(node), str(cli), "catalog", "describe", "cardVisual"],
        capture_output=True, text=True, timeout=10, check=True)
    objetos = set(json.loads(proc.stdout)["data"]["formattingObjects"])

    assert {"value", "label"} <= objetos
    assert {"labels", "categoryLabels"}.isdisjoint(objetos)


# ======================================== viaje de ida y vuelta (sin CLI) =====
def test_lo_que_devuelve_el_lector_lo_acepta_el_generador(proyecto_real):
    """Leer una pagina y hacer otra parecida es el flujo mas natural que hay.

    El lector devuelve los roles con el nombre PBIR (`Category`, `Y`) y cada
    campo como un diccionario; el generador esperaba roles logicos y cadenas.
    Las dos mitades del mismo servidor no se entendian: reutilizar lo leido
    fallaba, y si alguien extraia el `ref` a mano el visual salia vacio.
    """
    active, md = proyecto_real

    origen = page_spec.apply_spec(active, page_spec.compile_spec(active, {
        "schema_version": "1.0",
        "page": {"name": "origen", "displayName": "Origen"},
        "visuals": [{"type": "barChart", "title": "Por zona",
                     "position": {"x": 20, "y": 20, "width": 400, "height": 300},
                     "fields": {"category": ["Regiones[Zona]"],
                                "values": ["Ventas[Importe Total]"]}}]}, md))

    leido = pbir_reader.list_visuals(active, origen["page_id"])[0]
    assert set(leido["fields"]) == {"Category", "Y"}, "cambio la forma del lector"

    copia = page_spec.apply_spec(active, page_spec.compile_spec(active, {
        "schema_version": "1.0",
        "page": {"name": "copia", "displayName": "Copia"},
        "visuals": [{"type": "barChart", "title": "Copia",
                     "position": {"x": 20, "y": 20, "width": 400, "height": 300},
                     "fields": leido["fields"]}]}, md))

    rehecho = pbir_reader.list_visuals(active, copia["page_id"])[0]
    assert rehecho["fields"] == leido["fields"], "la copia no conserva los campos"
    assert rehecho["measures"] == leido["measures"]
    assert rehecho["columns"] == leido["columns"]


# =========================== interacciones desde un spec (sin CLI) ============
def _spec_con_interaccion(interacciones):
    return {"schema_version": "1.0",
            "page": {"name": "p", "displayName": "P"},
            "visuals": [
                {"id": "uno", "type": "slicer", "title": "Segmentador",
                 "position": {"x": 0, "y": 0, "width": 200, "height": 300},
                 "fields": {"values": ["Regiones[Zona]"]}},
                {"id": "dos", "type": "barChart", "title": "Barras",
                 "position": {"x": 220, "y": 0, "width": 400, "height": 300},
                 "fields": {"category": ["Regiones[Zona]"],
                            "values": ["Ventas[Importe Total]"]}}],
            "interactions": interacciones}


@pytest.mark.parametrize("origen,destino", [
    (0, 1),                        # por posicion
    ("uno", "dos"),                # por el id del spec
    ("Segmentador", "Barras"),     # por el titulo
])
def test_una_interaccion_puede_nombrar_los_visuales_del_propio_spec(
        proyecto_real, origen, destino):
    """Sin esto la clave `interactions` no se podia rellenar de ninguna forma."""
    active, md = proyecto_real
    c = page_spec.compile_spec(active, _spec_con_interaccion(
        [{"source": origen, "target": destino, "type": "DataFilter"}]), md)

    ids = [v["visual"]["name"] for v in c["visuals"]]
    assert c["page_interactions"] == [
        {"source": ids[0], "target": ids[1], "type": "DataFilter"}]


def test_la_posicion_cero_es_una_referencia_valida(proyecto_real):
    """`if not origen` daba por ausente el indice 0 por ser falsy."""
    active, md = proyecto_real
    c = page_spec.compile_spec(active, _spec_con_interaccion(
        [{"source": 1, "target": 0, "type": "NoFilter"}]), md)

    ids = [v["visual"]["name"] for v in c["visuals"]]
    assert c["page_interactions"][0]["target"] == ids[0]


def test_una_interaccion_a_un_visual_que_no_existe_se_acusa(proyecto_real):
    active, md = proyecto_real
    with pytest.raises(page_spec.SpecValidationError) as exc:
        page_spec.compile_spec(active, _spec_con_interaccion(
            [{"source": "uno", "target": "fantasma"}]), md)
    assert "fantasma" in str(exc.value)

    with pytest.raises(page_spec.SpecValidationError):
        page_spec.compile_spec(active, _spec_con_interaccion(
            [{"source": 0, "target": 7}]), md)


# ============================ nombres que el MOTOR rechaza (sin oraculo) ======
# El parser TMDL se los traga; el motor los rechaza al cargar. O sea: el
# proyecto "valida" y luego Power BI abre una ventana Sin titulo con el modelo
# vacio. El lint conocia las dos reglas desde siempre; el escritor no las
# consultaba, asi que solo aparecian al abrir.

def test_una_medida_no_puede_llamarse_como_una_columna_de_su_tabla(proyecto_real):
    """Power BI: «No se puede crear la medida 'X' porque ya existe una columna
    con el mismo nombre». Se comprobo abriendolo: el modelo queda vacio."""
    from powerbi.errors import MeasureExistsError

    active, _ = proyecto_real
    with pytest.raises(MeasureExistsError) as exc:
        tmdl_writer.create_measure_pbip(
            active, "Ventas", "Importe", "SUM(Ventas[Importe])")

    assert exc.value.details["rule"] == "measure_column_collision"
    assert "Importe" in exc.value.message


def test_el_nombre_de_una_medida_es_unico_en_todo_el_modelo(proyecto_real):
    """No por tabla: el motor rechaza las dos si se repiten en tablas distintas."""
    from powerbi.errors import MeasureExistsError

    active, _ = proyecto_real
    with pytest.raises(MeasureExistsError) as exc:
        tmdl_writer.create_measure_pbip(
            active, "Regiones", "Importe Total", "SUM(Regiones[Meta])")

    assert exc.value.details["rule"] == "duplicate_measure_name"
    assert "Ventas" in exc.value.message


def test_reemplazar_una_medida_existente_sigue_funcionando(proyecto_real):
    """El guardia es solo para nombres NUEVOS; `overwrite` no puede romperse."""
    active, _ = proyecto_real
    r = tmdl_writer.create_measure_pbip(
        active, "Ventas", "Importe Total", "SUM(Ventas[Importe]) * 2",
        overwrite=True)
    assert r["action"] == "updated"


@pytest.mark.abre
@requiere_oraculos
def test_el_modelo_sigue_abriendo_tras_rechazar_un_nombre_invalido(proyecto_real):
    """Rechazar no puede dejar el TMDL a medias."""
    from powerbi.errors import MeasureExistsError

    active, _ = proyecto_real
    with pytest.raises(MeasureExistsError):
        tmdl_writer.create_measure_pbip(
            active, "Ventas", "Importe", "SUM(Ventas[Importe])")

    v = tmdl_validate.validate(
        Path(active.semantic_model_dir) / "definition", use_tom=True)
    assert v["parsed"] is True
    assert v["valid"] is True, [f["rule"] for f in v["findings"]
                                if f["severity"] == "error"]
