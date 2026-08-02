"""Validacion real del TMDL: las cinco formas de romper un .pbip que pasaban.

Contexto (2026-08-01): `pbi_validate_pbip_project` devolvio `valid: true` cinco
veces seguidas sobre un proyecto que Power BI Desktop se negaba a abrir. Solo
comprobaba que los archivos existieran; nunca miraba dentro. Cada prueba de
aqui reproduce uno de los fallos reales que se colaron.

Todo es sintetico y offline: ningun caso necesita las DLL de TOM ni Desktop.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services import tmdl_validate


def _definition(tmp_path: Path, tablas: dict, relaciones: str = "",
                modelo: str = None) -> Path:
    """Escribe una carpeta `definition/` minima y devuelve su ruta."""
    definition = tmp_path / "definition"
    (definition / "tables").mkdir(parents=True)
    for nombre, contenido in tablas.items():
        (definition / "tables" / f"{nombre}.tmdl").write_text(
            contenido, encoding="utf-8")
    if relaciones:
        (definition / "relationships.tmdl").write_text(relaciones, encoding="utf-8")
    # Por defecto se declaran todas las tablas escritas: un modelo real siempre
    # lo hace, y omitirlo dispararia `tmdl_table_not_referenced` en cada prueba.
    refs = "".join(f"\nref table {_nombre_tmdl(c)}" for c in tablas.values())
    (definition / "model.tmdl").write_text(
        (modelo or "model Model\n\tculture: en-US\n") + refs + "\n",
        encoding="utf-8")
    return definition


def _nombre_tmdl(contenido: str) -> str:
    """Nombre de la tabla tal y como aparece en su declaracion."""
    for linea in contenido.splitlines():
        if linea.startswith("table "):
            return linea[len("table "):].strip()
    return ""


def _reglas(resultado) -> set:
    return {f["rule"] for f in resultado["findings"]}


# --------------------------------------------------------------------------
# 1. Propiedad de la tabla despues de sus hijos -> "sangria no valida"
# --------------------------------------------------------------------------

TABLA_PROPIEDAD_HUERFANA = """table Actividades
\texcludeFromModelRefresh

\tmeasure 'Total' = COUNTROWS ( 'Actividades' )
\t\tformatString: #,0
\tlineageTag: 1dfb8942-fd83-42e0-afaa-b8f7e4e26d44

\tcolumn CODIGO
\t\tdataType: string
\t\tsourceColumn: CODIGO
"""


def test_detecta_propiedad_de_tabla_despues_de_los_hijos(tmp_path):
    """El error que Power BI reporta como 'Indentation'.

    TMDL exige que las propiedades del objeto vayan antes que sus hijos.
    Insertar medidas justo despues de `table X` deja huerfano lo que venia
    despues, y el proyecto no abre.
    """
    definition = _definition(tmp_path, {"Actividades": TABLA_PROPIEDAD_HUERFANA})
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_property_after_children" in _reglas(resultado)
    assert resultado["valid"] is False
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_property_after_children")
    assert fallo["severity"] == "error"
    # Tiene que decir DONDE, no solo que algo esta mal.
    assert fallo["object"]["file"].endswith("Actividades.tmdl")
    assert fallo["object"]["line"] == 6
    assert "lineageTag" in fallo["evidence"]["property"]


TABLA_SANA = """table Actividades
\texcludeFromModelRefresh
\tlineageTag: 1dfb8942-fd83-42e0-afaa-b8f7e4e26d44

\tmeasure 'Total' = COUNTROWS ( 'Actividades' )
\t\tformatString: #,0

\tcolumn CODIGO
\t\tdataType: string
\t\tsourceColumn: CODIGO
"""


def test_tabla_bien_formada_no_dispara_nada(tmp_path):
    definition = _definition(tmp_path, {"Actividades": TABLA_SANA})
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert resultado["findings"] == []
    assert resultado["valid"] is True


def test_las_anotaciones_al_final_de_la_tabla_son_legales(tmp_path):
    """`annotation` si puede ir despues de los hijos; no es un falso positivo."""
    tabla = TABLA_SANA + "\n\tannotation PBI_ResultType = Table\n"
    definition = _definition(tmp_path, {"Actividades": tabla})
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_property_after_children" not in _reglas(resultado)


# --------------------------------------------------------------------------
# 2. `///` sobre una relacion -> description, que las relaciones no aceptan
# --------------------------------------------------------------------------

RELACIONES_CON_DESCRIPCION = """/// Enlaza cada elemento con la tarea que lo construye.
relationship d4e70001-0000-4000-8000-000000000001
\tfromColumn: Modelo.Codigo
\ttoColumn: Cronograma.Codigo
"""


def test_detecta_comentario_de_documentacion_sobre_una_relacion(tmp_path):
    """Un `///` se serializa como `description`, y SingleColumnRelationship
    no tiene esa propiedad: el proyecto no abre."""
    definition = _definition(
        tmp_path,
        {"Modelo": "table Modelo\n\tcolumn Codigo\n\t\tdataType: string\n",
         "Cronograma": "table Cronograma\n\tcolumn Codigo\n\t\tdataType: string\n"},
        relaciones=RELACIONES_CON_DESCRIPCION)
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_description_on_relationship" in _reglas(resultado)
    assert resultado["valid"] is False
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_description_on_relationship")
    assert fallo["object"]["line"] == 1


def test_relacion_sin_comentario_no_dispara(tmp_path):
    definition = _definition(
        tmp_path,
        {"Modelo": "table Modelo\n\tcolumn Codigo\n\t\tdataType: string\n",
         "Cronograma": "table Cronograma\n\tcolumn Codigo\n\t\tdataType: string\n"},
        relaciones=RELACIONES_CON_DESCRIPCION.split("\n", 1)[1])
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_description_on_relationship" not in _reglas(resultado)


# --------------------------------------------------------------------------
# 3. Medida con el mismo nombre que una columna de su tabla
# --------------------------------------------------------------------------

TABLA_COLISION = """table Modelo
\tlineageTag: aaaa

\tmeasure 'Cantidad' = SUM ( 'Modelo'[CANTIDAD] )
\t\tformatString: #,0

\tcolumn CANTIDAD
\t\tdataType: double
\t\tsourceColumn: CANTIDAD
"""


def test_detecta_medida_que_choca_con_una_columna(tmp_path):
    """El parser TMDL lo acepta; el motor lo rechaza al crear la base.

    Es case-insensitive: 'Cantidad' choca con la columna 'CANTIDAD'.
    """
    definition = _definition(tmp_path, {"Modelo": TABLA_COLISION})
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_measure_column_collision" in _reglas(resultado)
    assert resultado["valid"] is False
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_measure_column_collision")
    assert fallo["object"]["table"] == "Modelo"
    assert fallo["evidence"]["column"] == "CANTIDAD"
    assert fallo["evidence"]["measure"] == "Cantidad"


# --------------------------------------------------------------------------
# 4. Nombre de medida repetido en dos tablas
# --------------------------------------------------------------------------

def test_detecta_medidas_duplicadas_entre_tablas(tmp_path):
    """En un modelo tabular el nombre de medida es global."""
    tabla = ("table {n}\n\tlineageTag: {n}\n\n"
             "\tmeasure 'Total' = 1\n\t\tformatString: #,0\n\n"
             "\tcolumn C\n\t\tdataType: string\n")
    definition = _definition(tmp_path, {
        "Uno": tabla.format(n="Uno"),
        "Dos": tabla.format(n="Dos"),
    })
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_duplicate_measure_name" in _reglas(resultado)
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_duplicate_measure_name")
    assert sorted(fallo["evidence"]["tables"]) == ["Dos", "Uno"]


# --------------------------------------------------------------------------
# 5. TransformColumnTypes sin cultura -> el separador decimal se lo come
# --------------------------------------------------------------------------

TABLA_CSV_SIN_CULTURA = """table CostosReales
\tlineageTag: bbbb

\tcolumn Valor
\t\tdataType: decimal
\t\tsourceColumn: Valor

\tpartition CostosReales = m
\t\tmode: import
\t\tsource =
\t\t\t\tlet
\t\t\t\t    Origen = Csv.Document(File.Contents("C:\\datos\\costos.csv"), [Delimiter = ","]),
\t\t\t\t    Encabezados = Table.PromoteHeaders(Origen),
\t\t\t\t    Tipos = Table.TransformColumnTypes(Encabezados, {{"Valor", Currency.Type}})
\t\t\t\tin
\t\t\t\t    Tipos
"""

MODELO_ES_CO = "model Model\n\tculture: en-US\n\tsourceQueryCulture: es-CO\n"


def test_avisa_de_conversion_numerica_sin_cultura_explicita(tmp_path):
    """El fallo mas peligroso: NO da error, solo numeros mal.

    Con `sourceQueryCulture: es-CO` un CSV con punto decimal se lee como
    separador de miles y el total sale inflado, sin ninguna alerta.
    """
    definition = _definition(tmp_path, {"CostosReales": TABLA_CSV_SIN_CULTURA},
                             modelo=MODELO_ES_CO)
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_transform_without_culture" in _reglas(resultado)
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_transform_without_culture")
    # Es un aviso, no un error: el proyecto abre igual.
    assert fallo["severity"] == "warning"
    assert fallo["object"]["table"] == "CostosReales"
    assert fallo["evidence"]["source_query_culture"] == "es-CO"


def test_conversion_con_cultura_explicita_no_avisa(tmp_path):
    tabla = TABLA_CSV_SIN_CULTURA.replace(
        '{{"Valor", Currency.Type}})', '{{"Valor", Currency.Type}}, "en-US")')
    definition = _definition(tmp_path, {"CostosReales": tabla},
                             modelo=MODELO_ES_CO)
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_transform_without_culture" not in _reglas(resultado)


def test_sin_sourceQueryCulture_no_avisa(tmp_path):
    """Sin cultura de consulta declarada no hay ambiguedad que reportar."""
    definition = _definition(tmp_path, {"CostosReales": TABLA_CSV_SIN_CULTURA})
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_transform_without_culture" not in _reglas(resultado)


def test_origen_ya_tipado_no_avisa(tmp_path):
    """Excel devuelve valores tipados: ahi la cultura no cambia nada.

    Avisar tambien aqui llenaria de ruido cualquier modelo que lea de Excel o
    de una base de datos, y un validador que llora lobo se ignora.
    """
    tabla = TABLA_CSV_SIN_CULTURA.replace("Csv.Document", "Excel.Workbook")
    assert "Csv.Document" not in tabla  # el reemplazo se aplico de verdad
    definition = _definition(tmp_path, {"CostosReales": tabla},
                             modelo=MODELO_ES_CO)
    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_transform_without_culture" not in _reglas(resultado)


# --------------------------------------------------------------------------
# 5 bis. Una tabla que existe en disco pero no esta en el modelo
# --------------------------------------------------------------------------

def test_detecta_una_tabla_que_no_esta_declarada_en_el_modelo(tmp_path):
    """Sin `ref table` la tabla NO forma parte del modelo, aunque el archivo este.

    Es un fallo mudo y caro: el .tmdl se ve perfecto en disco, el proyecto abre
    sin quejarse, y la tabla simplemente no existe. Todo lo que la usara —una
    medida, un visual— aparece roto sin explicacion.
    """
    definition = _definition(tmp_path, {"Ventas": TABLA_SANA.replace(
        "Actividades", "Ventas")})
    # model.tmdl sin ninguna referencia
    (definition / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n", encoding="utf-8")

    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_table_not_referenced" in _reglas(resultado)
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_table_not_referenced")
    assert fallo["severity"] == "error"
    assert fallo["object"]["table"] == "Ventas"
    assert resultado["valid"] is False


def test_detecta_una_referencia_a_una_tabla_que_no_existe(tmp_path):
    definition = _definition(tmp_path, {"Ventas": TABLA_SANA.replace(
        "Actividades", "Ventas")})
    (definition / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\nref table Ventas\nref table Fantasma\n",
        encoding="utf-8")

    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_ref_table_missing" in _reglas(resultado)
    fallo = next(f for f in resultado["findings"]
                 if f["rule"] == "tmdl_ref_table_missing")
    assert fallo["evidence"]["table"] == "Fantasma"


def test_tabla_declarada_y_presente_no_dispara(tmp_path):
    definition = _definition(tmp_path, {"Ventas": TABLA_SANA.replace(
        "Actividades", "Ventas")})
    (definition / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\nref table Ventas\n", encoding="utf-8")

    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_table_not_referenced" not in _reglas(resultado)
    assert "tmdl_ref_table_missing" not in _reglas(resultado)


def test_el_nombre_entrecomillado_en_la_referencia_tambien_cuenta(tmp_path):
    """`ref table 'con espacios'` referencia la misma tabla que sin comillas."""
    tabla = TABLA_SANA.replace("table Actividades", "table 'public qa_runs'")
    definition = _definition(tmp_path, {"qa_runs": tabla})
    (definition / "model.tmdl").write_text(
        "model Model\n\tculture: en-US\n\nref table 'public qa_runs'\n",
        encoding="utf-8")

    resultado = tmdl_validate.validate(definition, use_tom=False)
    assert "tmdl_table_not_referenced" not in _reglas(resultado)


# --------------------------------------------------------------------------
# 6. Relacion que apunta a una columna inexistente
# --------------------------------------------------------------------------

def test_detecta_relacion_hacia_una_columna_que_no_existe(tmp_path):
    definition = _definition(
        tmp_path,
        {"Modelo": "table Modelo\n\tcolumn Codigo\n\t\tdataType: string\n",
         "Cronograma": "table Cronograma\n\tcolumn Otra\n\t\tdataType: string\n"},
        relaciones=("relationship r1\n\tfromColumn: Modelo.Codigo\n"
                    "\ttoColumn: Cronograma.NoExiste\n"))
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert "tmdl_relationship_column_missing" in _reglas(resultado)
    assert resultado["valid"] is False


# --------------------------------------------------------------------------
# Honestidad sobre lo que no se pudo comprobar
# --------------------------------------------------------------------------

def test_sin_tom_lo_dice_en_vez_de_afirmar_que_parsea(tmp_path):
    """'No pude mirar' no puede leerse igual que 'esta bien'."""
    definition = _definition(tmp_path, {"Actividades": TABLA_SANA})
    resultado = tmdl_validate.validate(definition, use_tom=False)

    assert resultado["parsed"] is None
    assert resultado["parse_checked"] is False
    assert resultado["parse_skipped_reason"]


def test_carpeta_inexistente_falla_claro(tmp_path):
    with pytest.raises(tmdl_validate.TmdlValidationError):
        tmdl_validate.validate(tmp_path / "no_existe", use_tom=False)


# --------------------------------------------------------------------------
# Un informe sin modelo propio es legitimo, no una ruta rota
# --------------------------------------------------------------------------

def test_un_pbip_de_solo_informe_se_explica_como_lo_que_es(tmp_path):
    """Un informe con conexion en vivo no tiene .SemanticModel, y esta bien.

    Es lo que produce `pbi_convert_pbix_to_pbip` con include_model=false y lo
    que es cualquier informe fino contra un dataset publicado. Decir 'no se
    encontro un modelo TMDL' suena a que algo se rompio; hay que distinguir
    'este proyecto no tiene modelo por diseño' de 'busque y no lo encontre'.
    """
    proyecto = tmp_path / "Informe"
    (proyecto / "Informe.Report" / "definition").mkdir(parents=True)
    pbip = proyecto / "Informe.pbip"
    pbip.write_text('{"version": "1.0", "artifacts": [{"report": '
                    '{"path": "Informe.Report"}}]}', encoding="utf-8")

    with pytest.raises(tmdl_validate.TmdlValidationError) as exc:
        tmdl_validate.resolve_definition_dir(pbip)

    assert exc.value.code == "tmdl_report_only_project"
    assert "conexion en vivo" in str(exc.value).lower() or \
           "sin modelo" in str(exc.value).lower()
    assert exc.value.details.get("report_only") is True


# --------------------------------------------------------------------------
# model.bim: el formato que trae la mayoria de los .pbip
# --------------------------------------------------------------------------

MODEL_BIM_CON_COLISION = """{
  "name": "SemanticModel",
  "compatibilityLevel": 1567,
  "model": {
    "culture": "en-US",
    "tables": [
      {
        "name": "Modelo",
        "columns": [{"name": "CANTIDAD", "dataType": "double"}],
        "measures": [{"name": "Cantidad", "expression": "SUM(Modelo[CANTIDAD])"}]
      },
      {
        "name": "Otra",
        "columns": [{"name": "Id", "dataType": "int64"}],
        "measures": [{"name": "Cantidad", "expression": "1"}]
      }
    ],
    "relationships": [
      {"name": "r1", "fromTable": "Otra", "fromColumn": "Id",
       "toTable": "Modelo", "toColumn": "NoExiste"}
    ]
  }
}"""


def _semantic_model_bim(tmp_path: Path, contenido: str) -> Path:
    modelo = tmp_path / "Demo.SemanticModel"
    modelo.mkdir(parents=True)
    (modelo / "model.bim").write_text(contenido, encoding="utf-8")
    (modelo / "definition.pbism").write_text("{}", encoding="utf-8")
    return modelo


def test_valida_un_modelo_en_formato_model_bim(tmp_path):
    """Un .pbip sin el preview de TMDL guarda el modelo como model.bim.

    Es el formato por defecto, asi que ignorarlo dejaria fuera a la mayoria de
    los proyectos. Los chequeos estructurales de TMDL no aplican —no hay
    sangria que romper en un JSON— pero los semanticos valen igual.
    """
    modelo = _semantic_model_bim(tmp_path, MODEL_BIM_CON_COLISION)
    resultado = tmdl_validate.validate(modelo, use_tom=False)

    assert resultado["format"] == "model.bim"
    reglas = _reglas(resultado)
    assert "tmdl_measure_column_collision" in reglas
    assert "tmdl_duplicate_measure_name" in reglas
    assert "tmdl_relationship_column_missing" in reglas
    assert resultado["valid"] is False


def test_model_bim_sano_pasa(tmp_path):
    sano = MODEL_BIM_CON_COLISION.replace('"name": "Cantidad"', '"name": "Total"', 1)
    sano = sano.replace('{"name": "Cantidad", "expression": "1"}',
                        '{"name": "Otro total", "expression": "1"}')
    sano = sano.replace('"toColumn": "NoExiste"', '"toColumn": "CANTIDAD"')
    modelo = _semantic_model_bim(tmp_path, sano)
    resultado = tmdl_validate.validate(modelo, use_tom=False)

    assert resultado["format"] == "model.bim"
    assert resultado["findings"] == []
    assert resultado["valid"] is True


def test_model_bim_ilegible_se_reporta_como_tal(tmp_path):
    modelo = _semantic_model_bim(tmp_path, "{ esto no es json")
    resultado = tmdl_validate.validate(modelo, use_tom=False)
    assert "model_bim_unreadable" in _reglas(resultado)
    assert resultado["valid"] is False


def test_resolve_encuentra_el_semantic_model_con_model_bim(tmp_path):
    """Resolver debe llegar al modelo aunque no haya carpeta definition/."""
    proyecto = tmp_path / "Demo"
    proyecto.mkdir()
    modelo = _semantic_model_bim(proyecto, MODEL_BIM_CON_COLISION)
    (proyecto / "Demo.Report").mkdir()
    pbip = proyecto / "Demo.pbip"
    pbip.write_text('{"version": "1.0"}', encoding="utf-8")

    assert tmdl_validate.resolve_definition_dir(pbip) == modelo
