"""Elementos de composicion (portadas, navegacion) e identidad visual.

Lo que se congela aqui son equivalencias que costo averiguar y que no se pueden
deducir leyendo la documentacion:

- El contenido de un textbox, una forma o un boton vive entero en `objects`,
  con la gramatica de literales del motor (`'texto'`, `12D`, `0L`).
- Un boton NO lleva consulta, y pedirle campos es un error del que llama.
- En un tema, `textClasses.*.color` es un hex PLANO; la forma
  `{"solid": {"color": ...}}` que usan los visualStyles ahi es invalida.
- El nombre interno del tema debe incluir la extension y coincidir con el
  archivo y con `themeCollection.customTheme.name`. Los tres, o Power BI avisa.

Las tres ultimas se descubrieron con el validador oficial de Microsoft sobre un
informe real, no leyendo especificaciones.
"""
from __future__ import annotations

import json

import pytest

from pbip import theme, visual_factory
from pbip.visual_factory import VisualFactoryError


def _construir(active, tipo, opciones, pos=None):
    return visual_factory.build_visual(
        active, tipo, {}, pos or {"x": 0, "y": 0, "width": 100, "height": 50},
        options=opciones)["visual"]["visual"]


# ------------------------------------------------------- tipos y alias --------
def test_todos_los_tipos_anunciados_se_pueden_usar():
    """El listado de soportados y lo que resuelve `resolve_type` no pueden divergir.

    Antes TYPE_MAP declaraba claves en camelCase y la busqueda las pasaba a
    minusculas: 'cardVisual', 'tableEx' y 'pivotTable' se anunciaban como
    soportados y se rechazaban al usarlos.
    """
    for anunciado in set(visual_factory.TYPE_MAP.values()):
        assert visual_factory.resolve_type(anunciado) == anunciado, (
            f"'{anunciado}' aparece como soportado pero resolve_type lo rechaza")


@pytest.mark.parametrize("alias,esperado", [
    ("button", "actionButton"), ("text", "textbox"), ("rectangle", "shape"),
    ("navigation", "pageNavigator"), ("matrix", "pivotTable"),
])
def test_alias_comodos(alias, esperado):
    assert visual_factory.resolve_type(alias) == esperado


# ------------------------------------------------------- composicion ----------
def test_textbox_lleva_su_texto_y_estilo(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    vis = _construir(activo, "textbox",
                     {"text": "Calidad BIM", "font_size": 40, "bold": True,
                      "color": "#FFFFFF"})
    run = vis["objects"]["general"][0]["properties"]["paragraphs"][0]["textRuns"][0]
    assert run["value"] == "Calidad BIM"
    assert run["textStyle"] == {"fontSize": "40pt", "color": "#FFFFFF",
                                "fontWeight": "bold"}
    # sin consulta: un cuadro de texto no pinta datos
    assert "query" not in vis


def test_forma_con_relleno_y_literales_del_motor(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    vis = _construir(activo, "shape", {"shape": "rectangle", "fill": "#1A1A19"})
    assert vis["objects"]["shape"][0]["properties"]["tileShape"] == {
        "expr": {"Literal": {"Value": "'rectangle'"}}}
    color = vis["objects"]["fill"][0]["properties"]["fillColor"]["solid"]["color"]
    assert color == {"expr": {"Literal": {"Value": "'#1A1A19'"}}}
    # los enteros llevan sufijo L y los decimales D: es la gramatica del motor
    assert vis["objects"]["rotation"][0]["properties"]["shapeAngle"] == {
        "expr": {"Literal": {"Value": "0L"}}}


def test_boton_de_navegacion_apunta_a_la_pagina(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    vis = _construir(activo, "button",
                     {"action": "page", "target_page": "pagina0001",
                      "text": "Ver familias"})
    enlace = vis["visualContainerObjects"]["visualLink"][0]["properties"]
    assert enlace["type"] == {"expr": {"Literal": {"Value": "'PageNavigation'"}}}
    assert enlace["navigationSection"] == {
        "expr": {"Literal": {"Value": "'pagina0001'"}}}


def test_boton_de_pagina_sin_destino_falla_claro(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        _construir(activo, "button", {"action": "page"})
    assert "target_page" in str(exc.value)


def test_composicion_no_admite_campos(sample_pbip, session):
    """Pedirle campos a un boton es un error de quien llama, no algo a ignorar."""
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        visual_factory.build_visual(
            activo, "textbox", {"values": ["Ventas[Total]"]},
            {"x": 0, "y": 0, "width": 100, "height": 50}, options={"text": "x"})
    assert "no lleva campos" in str(exc.value)


def test_textbox_sin_texto_falla(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    with pytest.raises(VisualFactoryError):
        _construir(activo, "textbox", {})


def test_forma_desconocida_se_rechaza(sample_pbip, session):
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    with pytest.raises(VisualFactoryError) as exc:
        _construir(activo, "shape", {"shape": "estrella_ninja"})
    assert "estrella_ninja" in str(exc.value)


# ------------------------------------------------------------ layout ---------
def test_el_detector_no_se_queja_del_solape_de_una_portada():
    """Una portada superpone a proposito: el fondo esta debajo de todo.

    Antes cada portada generaba una veintena de avisos falsos y enterraba los
    de verdad.
    """
    from services import layout_doctor

    visuales = [
        {"id": "fondo", "type": "shape",
         "position": {"x": 0, "y": 0, "width": 1280, "height": 720, "z": 0}},
        {"id": "titulo", "type": "textbox",
         "position": {"x": 60, "y": 55, "width": 900, "height": 70, "z": 2}},
        {"id": "boton", "type": "actionButton",
         "position": {"x": 60, "y": 470, "width": 270, "height": 54, "z": 3}},
    ]
    r = layout_doctor.detect_issues(visuales, {"width": 1280, "height": 720})
    reglas = {i["rule"] for i in r["issues"]}
    assert "layout_overlap" not in reglas
    assert "layout_visual_too_small" not in reglas


def test_el_detector_sigue_viendo_el_solape_entre_graficos():
    """La tolerancia es solo para composicion: dos graficos encimados siguen mal."""
    from services import layout_doctor

    visuales = [
        {"id": "a", "type": "clusteredBarChart",
         "position": {"x": 0, "y": 0, "width": 400, "height": 300, "z": 0}},
        {"id": "b", "type": "clusteredBarChart",
         "position": {"x": 100, "y": 100, "width": 400, "height": 300, "z": 1}},
    ]
    r = layout_doctor.detect_issues(visuales, {"width": 1280, "height": 720})
    assert "layout_overlap" in {i["rule"] for i in r["issues"]}


# -------------------------------------------------------------- tema ---------
def test_los_tres_temas_comparten_los_colores_de_estado():
    """El semaforo significa lo mismo se pinte donde se pinte."""
    estados = [{k: t["tema"][k] for k in theme.ESTADO} for t in theme.PRESETS.values()]
    assert all(e == estados[0] for e in estados)
    assert estados[0]["good"] != estados[0]["bad"]


def test_las_clases_de_texto_usan_hex_plano():
    """`textClasses.*.color` con {'solid': ...} lo rechaza Power BI."""
    for clave in theme.PRESETS:
        clases = theme.build_theme(clave)["textClasses"]
        for nombre, definicion in clases.items():
            assert isinstance(definicion["color"], str), (
                f"{clave}/{nombre}: el color debe ser un hex plano")
            assert definicion["color"].startswith("#")


def test_ningun_color_de_serie_pisa_uno_de_estado():
    """Un color de estado no puede impersonar a una serie."""
    for clave in theme.PRESETS:
        t = theme.build_theme(clave)
        assert not (set(c.upper() for c in t["dataColors"])
                    & set(v.upper() for v in theme.ESTADO.values()))


def test_tema_desconocido_lista_los_disponibles():
    with pytest.raises(theme.ThemeError) as exc:
        theme.build_theme("no_existe")
    assert "control_room" in str(exc.value.details["available"])


def test_paleta_propia_se_valida():
    with pytest.raises(theme.ThemeError):
        theme.build_theme("claro", data_colors=["azul", "#FFF"])
    t = theme.build_theme("claro", data_colors=["#112233", "#445566"])
    assert t["dataColors"] == ["#112233", "#445566"]


def test_aplicar_tema_deja_los_tres_nombres_iguales(sample_pbip, session, tmp_path):
    """Nombre interno, archivo y report.json han de coincidir, extension incluida."""
    from pbip import project_locator

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    resultado = theme.apply_theme(activo, theme.build_theme("control_room"))

    from pathlib import Path

    archivo = Path(resultado["file"])
    contenido = json.loads(archivo.read_text(encoding="utf-8-sig"))
    informe = json.loads((Path(activo.report_dir) / "definition" / "report.json")
                         .read_text(encoding="utf-8-sig"))
    declarado = informe["themeCollection"]["customTheme"]["name"]

    assert contenido["name"] == archivo.name == declarado
    assert declarado.endswith(".json")
    # y queda declarado como recurso, o Desktop lo ignora en silencio
    paquetes = {p["type"]: p for p in informe["resourcePackages"]}
    rutas = [i["path"] for i in paquetes["RegisteredResources"]["items"]]
    assert archivo.name in rutas


def test_fallo_al_declarar_tema_revierte_el_archivo_y_report_json(
        sample_pbip, session, monkeypatch):
    """Si falla el segundo archivo, el tema no puede quedar huerfano."""
    from pathlib import Path

    from pbip import project_locator
    from services import txn as txn_service

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    informe = Path(activo.report_dir) / "definition" / "report.json"
    antes = informe.read_bytes()
    destino = (Path(activo.report_dir) / "StaticResources" /
               "RegisteredResources" / "Atomico.json")
    original = txn_service.Transaction.write_json

    def fallar_en_report(self, target, data):
        if Path(target) == informe:
            raise RuntimeError("fallo inyectado al declarar el tema")
        return original(self, target, data)

    monkeypatch.setattr(txn_service.Transaction, "write_json", fallar_en_report)
    with pytest.raises(RuntimeError, match="fallo inyectado"):
        theme.apply_theme(activo, theme.build_theme("claro"),
                          file_name="Atomico.json")

    assert not destino.exists()
    assert informe.read_bytes() == antes


def test_nombre_de_archivo_de_tema_no_admite_traversal(
        sample_pbip, session, tmp_path):
    from pbip import project_locator
    from powerbi.errors import PathSecurityError

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    victima = tmp_path / "fuera.json"
    victima.write_bytes(b"NO TOCAR")

    with pytest.raises(PathSecurityError):
        theme.apply_theme(activo, theme.build_theme("claro"),
                          file_name=str(victima))
    assert victima.read_bytes() == b"NO TOCAR"


@pytest.mark.real_project_state
def test_tema_no_se_escribe_con_desktop_abierto(
        sample_pbip, session, monkeypatch):
    from pathlib import Path

    from pbip import project_locator
    from services import project_state

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    informe = Path(activo.report_dir) / "definition" / "report.json"
    antes = informe.read_bytes()
    destino = (Path(activo.report_dir) / "StaticResources" /
               "RegisteredResources" / "Bloqueado.json")
    monkeypatch.setattr(
        project_state, "detect",
        lambda a, **kw: project_state.ProjectOpenState(
            project_state.OPEN, "high", "forzado"))

    with pytest.raises(project_state.ProjectOpenInDesktopError):
        theme.apply_theme(activo, theme.build_theme("claro"),
                          file_name="Bloqueado.json")

    assert not destino.exists()
    assert informe.read_bytes() == antes


# ------------------------------------------------- de que modelo se leen ------
def test_el_tmdl_del_proyecto_manda_sobre_el_modelo_en_vivo(sample_pbip, session,
                                                            monkeypatch):
    """Un visual se escribe en ESE .pbip: sus campos deben existir ahi.

    Antes mandaba el modelo en vivo, asi que bastaba tener otro .pbix abierto en
    Desktop para que las medidas recien escritas en el TMDL se dieran por
    inexistentes y se rechazara la pagina entera.
    """
    from config import ActiveModel
    from pbip import project_locator
    from tools import visual_tools

    project_locator.open_project(session, str(sample_pbip))
    session.set_active_model(ActiveModel(
        host="localhost", port=99999,
        connection_string="Data Source=localhost:99999", catalog="otro"))

    def _no_deberia_llamarse(*a, **k):                    # pragma: no cover
        raise AssertionError("se leyo el modelo en vivo teniendo TMDL")

    # `_model_data` consulta la sesion global; aqui se le da la del fixture
    # para que la prueba no dependa de lo que otras dejaran puesto.
    monkeypatch.setattr(visual_tools, "get_session", lambda: session)
    monkeypatch.setattr(visual_tools.model_reader, "read_model", _no_deberia_llamarse)
    datos = visual_tools._model_data()

    assert datos is not None
    assert any(t["name"] == "Ventas" for t in datos["tables"])


# --------------------------------------------------- dialectos de spec --------
def test_create_page_acepta_el_dialecto_del_constructor():
    """Los dos formatos de spec que conviven deben entrar por la misma puerta.

    Se validaba con `{schema_version, page}` y se aplicaba con `{page_name}`:
    un spec que pasaba la validacion rebotaba al crearlo, con un error que no
    mencionaba que existieran dos formatos.
    """
    from tools.page_tools import normalizar_spec

    nuevo = {"schema_version": "1.0",
             "page": {"name": "Portada", "width": 1280, "height": 720},
             "visuals": [{"type": "card"}]}
    salida = normalizar_spec(nuevo)
    assert salida["page_name"] == "Portada"
    assert salida["canvas"] == {"width": 1280, "height": 720}
    assert salida["visuals"] == nuevo["visuals"]
    assert "schema_version" not in salida and "page" not in salida


def test_el_dialecto_antiguo_pasa_intacto():
    from tools.page_tools import normalizar_spec

    viejo = {"page_name": "P", "canvas": {"width": 800, "height": 600},
             "visuals": []}
    assert normalizar_spec(viejo) is viejo


# ------------------------------------------------- formato condicional -------
def _visual_matriz():
    return {"$schema": "x", "name": "v1",
            "position": {"x": 0, "y": 0, "width": 100, "height": 100},
            "visual": {"visualType": "pivotTable"}}


def _campo():
    return {"Measure": {"Expression": {"SourceRef": {"Entity": "qa"}},
                        "Property": "Puntaje"}}


def test_degradado_de_dos_paradas():
    from pbip import conditional_format as cf

    r = cf.build_fill_rule(_campo(), "#D03B3B", "#0CA30C")
    regla = r["expr"]["FillRule"]
    assert regla["Input"] == _campo()
    grad = regla["FillRule"]["linearGradient2"]
    assert grad["min"]["color"]["Literal"]["Value"] == "'#D03B3B'"
    assert grad["max"]["color"]["Literal"]["Value"] == "'#0CA30C'"


def test_degradado_de_tres_paradas_cuando_hay_punto_neutro():
    from pbip import conditional_format as cf

    r = cf.build_fill_rule(_campo(), "#D03B3B", "#0CA30C", mid_color="#FAB219")
    assert "linearGradient3" in r["expr"]["FillRule"]["FillRule"]


def test_la_regla_se_aplica_a_todas_las_filas():
    """Sin el selector comodin el color solo pinta la primera fila."""
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo(), "#D03B3B", "#0CA30C")
    bloque = vis["visual"]["objects"]["values"][0]
    # Se comprueba `data`, no el selector entero: ahi ademas va `metadata`, que
    # es lo que acota la regla a SU campo y permite colorear varias medidas.
    assert bloque["selector"]["data"] == [
        {"dataViewWildcard": {"matchingOption": 1}}]
    assert "backColor" in bloque["properties"]


def test_una_segunda_regla_sobre_el_MISMO_campo_sustituye_a_la_primera():
    """Dos degradados sobre el mismo campo si se pisan: dejar los dos escritos
    haria impredecible cual gana.

    Sobre campos DISTINTOS conviven, que es lo que permite un mapa de calor de
    varias medidas; eso lo cubre
    `test_colorear_una_segunda_medida_no_borra_la_primera`.
    """
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo(), "#000000", "#111111")
    r = cf.apply_to_visual(vis, _campo(), "#D03B3B", "#0CA30C")
    assert r["replaced"] is True
    bloques = vis["visual"]["objects"]["values"]
    assert len(bloques) == 1
    grad = (bloques[0]["properties"]["backColor"]["solid"]["color"]["expr"]["FillRule"]
            ["FillRule"]["linearGradient2"])
    assert grad["min"]["color"]["Literal"]["Value"] == "'#D03B3B'"


def test_destino_y_colores_se_validan():
    from pbip import conditional_format as cf

    with pytest.raises(cf.ConditionalFormatError) as exc:
        cf.apply_to_visual(_visual_matriz(), _campo(), "#FFF", "#000",
                           target="el_techo")
    assert "background" in str(exc.value)
    with pytest.raises(cf.ConditionalFormatError):
        cf.build_fill_rule(_campo(), "rojo", "#000000")
    with pytest.raises(cf.ConditionalFormatError):
        cf.build_fill_rule(_campo(), "#FFFFFF", "#000000", null_strategy="inventada")
    with pytest.raises(cf.ConditionalFormatError):
        cf.build_fill_rule(_campo(), "#GGGGGG", "#000000")


def _campo_de(propiedad, entidad="qa"):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entidad}},
                        "Property": propiedad}}


def test_colorear_una_segunda_medida_no_borra_la_primera():
    """El defecto: en una matriz de varias metricas solo quedaba pintada la ultima.

    Se reemplazaba cualquier bloque que tuviera esa propiedad, sin mirar a que
    campo apuntaba. El rodeo conocido era dinamizar las metricas a filas para
    tener una sola medida; ya no hace falta.
    """
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo_de("Importe"), "#FFFFFF", "#2A78D6")
    cf.apply_to_visual(vis, _campo_de("Unidades"), "#FFFFFF", "#EB6834")

    bloques = vis["visual"]["objects"]["values"]
    assert len(bloques) == 2
    assert {b["selector"]["metadata"] for b in bloques} == {"qa.Importe",
                                                            "qa.Unidades"}


def test_repetir_el_mismo_campo_sustituye_su_regla_y_solo_la_suya():
    """Acumular tampoco puede significar dejar dos reglas del mismo campo."""
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo_de("Importe"), "#FFFFFF", "#FF0000")
    cf.apply_to_visual(vis, _campo_de("Unidades"), "#FFFFFF", "#0000FF")
    r = cf.apply_to_visual(vis, _campo_de("Importe"), "#FFFFFF", "#00FF00")

    assert r["replaced"] is True
    bloques = vis["visual"]["objects"]["values"]
    assert len(bloques) == 2
    importe = next(b for b in bloques
                   if b["selector"]["metadata"] == "qa.Importe")
    gradiente = (importe["properties"]["backColor"]["solid"]["color"]["expr"]
                 ["FillRule"]["FillRule"]["linearGradient2"])
    assert gradiente["max"]["color"]["Literal"]["Value"] == "'#00FF00'"


def test_la_regla_se_acota_al_campo_con_metadata():
    """`selector.metadata` es lo que el esquema oficial llama 'scope to a field'.

    Sin el, dos degradados sobre la misma propiedad no se pueden distinguir.
    """
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo(), "#FFFFFF", "#000000")
    selector = vis["visual"]["objects"]["values"][0]["selector"]

    assert selector["metadata"] == "qa.Puntaje"
    # Y sigue aplicandose a todas las filas, no solo a la primera.
    assert selector["data"] == [{"dataViewWildcard": {"matchingOption": 1}}]


@pytest.mark.parametrize("campo,esperado", [
    ({"Measure": {"Expression": {"SourceRef": {"Entity": "V"}}, "Property": "M"}},
     "V.M"),
    ({"Column": {"Expression": {"SourceRef": {"Entity": "V"}}, "Property": "C"}},
     "V.C"),
    ({"Measure": {"Property": "SinTabla"}}, "SinTabla"),
    ({"Algo": {"raro": 1}}, None),
])
def test_la_referencia_del_campo_se_deduce_como_en_el_resto_del_servidor(
        campo, esperado):
    """Tiene que coincidir con el `queryRef` que escribe `visual_factory`.

    Si no coinciden, la regla apunta a una columna que el visual no tiene.
    """
    from pbip import conditional_format as cf

    assert cf.query_ref(campo) == esperado


@pytest.mark.parametrize("target,grupo,prop", [
    ("background", "values", "backColor"),
    ("font", "values", "fontColor"),
])
def test_cada_destino_escribe_donde_toca(target, grupo, prop):
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    cf.apply_to_visual(vis, _campo(), "#D03B3B", "#0CA30C", target=target)
    assert prop in vis["visual"]["objects"][grupo][0]["properties"]


def test_barras_escribe_en_data_point_de_un_grafico_compatible():
    from pbip import conditional_format as cf

    vis = {"visual": {"visualType": "clusteredColumnChart"}}
    cf.apply_to_visual(vis, _campo(), "#D03B3B", "#0CA30C", target="bars")
    assert "fill" in vis["visual"]["objects"]["dataPoint"][0]["properties"]


def test_el_destino_de_formato_debe_existir_en_ese_tipo_de_visual():
    """El esquema oficial acepta cualquier `objects`; Desktop simplemente
    ignora un grupo que no pertenece al tipo. Hay que bloquearlo nosotros.
    """
    from pbip import conditional_format as cf

    vis = _visual_matriz()
    with pytest.raises(cf.ConditionalFormatError) as exc:
        cf.apply_to_visual(vis, _campo(), "#D03B3B", "#0CA30C", target="bars")
    assert exc.value.details["visual_type"] == "pivotTable"
    assert "objects" not in vis["visual"]


# --------------------------------------------------------- recursos ----------
def _png(tmp_path):
    """PNG de 1x1 valido, suficiente para ejercitar el registro."""
    import base64

    datos = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    f = tmp_path / "logo.png"
    f.write_bytes(datos)
    return f


def test_una_imagen_se_copia_y_se_declara(sample_pbip, session, tmp_path):
    """Copiarla sin declararla la deja invisible para Power BI."""
    from pathlib import Path

    from pbip import project_locator, resources

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    r = resources.add_image(activo, _png(tmp_path))

    assert Path(r["file"]).exists()
    informe = json.loads((Path(activo.report_dir) / "definition" / "report.json")
                         .read_text(encoding="utf-8-sig"))
    rr = next(p for p in informe["resourcePackages"]
              if p["type"] == "RegisteredResources")
    assert any(i["path"] == r["item_name"] and i["type"] == "Image"
               for i in rr["items"])
    # y devuelve como usarla, que es el dato que hace falta despues
    assert r["usage"]["options"]["resource"] == r["item_name"]
    assert r["transaction"]["clean"] is True
    assert r["backup"]


def test_no_pisa_un_recurso_existente(sample_pbip, session, tmp_path):
    from pbip import project_locator, resources

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    a = resources.add_image(activo, _png(tmp_path))
    b = resources.add_image(activo, _png(tmp_path))
    assert a["item_name"] != b["item_name"]


def test_nombre_de_recurso_no_puede_escapar_del_informe(
        sample_pbip, session, tmp_path):
    """La prueba de traversal solo apunta a un marcador dentro de tmp_path."""
    from pbip import project_locator, resources
    from powerbi.errors import PathSecurityError

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    victima = fuera / "victima.json"
    victima.write_bytes(b"NO TOCAR")

    with pytest.raises(PathSecurityError):
        resources.add_image(
            activo, _png(tmp_path), name=str(victima), overwrite=True)

    assert victima.read_bytes() == b"NO TOCAR"


def test_fallo_al_registrar_recurso_revierte_la_copia(
        sample_pbip, session, tmp_path, monkeypatch):
    """Si falla el segundo archivo, ni la imagen ni report.json cambian."""
    from pathlib import Path

    from pbip import project_locator, resources
    from services import txn as txn_service

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    informe = Path(activo.report_dir) / "definition" / "report.json"
    antes = informe.read_bytes()
    destino = (Path(activo.report_dir) / "StaticResources"
               / "RegisteredResources" / "atomico.png")

    def fallar_registro(self, target, data):
        raise RuntimeError("fallo inyectado al registrar report.json")

    monkeypatch.setattr(txn_service.Transaction, "write_json", fallar_registro)
    with pytest.raises(RuntimeError, match="fallo inyectado"):
        resources.add_image(activo, _png(tmp_path), name="atomico.png")

    assert not destino.exists()
    assert informe.read_bytes() == antes


def test_extension_no_soportada_se_rechaza(sample_pbip, session, tmp_path):
    from pbip import project_locator, resources

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    malo = tmp_path / "cosa.exe"
    malo.write_bytes(b"MZ")
    with pytest.raises(resources.ResourceError) as exc:
        resources.add_image(activo, malo)
    assert ".png" in str(exc.value)


def test_listar_recursos_detecta_lo_que_no_cuadra(sample_pbip, session, tmp_path):
    """Un archivo sin declarar y una declaracion sin archivo son invisibles."""
    from pathlib import Path

    from pbip import project_locator, resources

    project_locator.open_project(session, str(sample_pbip))
    activo = session.require_active_pbip()
    resources.add_image(activo, _png(tmp_path))
    huerfano = Path(activo.report_dir) / "StaticResources" / "RegisteredResources" / "suelto.png"
    huerfano.write_bytes(b"x")

    r = resources.list_resources(activo)
    assert "suelto.png" in r["undeclared_files"]
    assert r["missing_files"] == []
