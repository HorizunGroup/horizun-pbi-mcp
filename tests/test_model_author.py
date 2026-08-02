"""Autoria del modelo en TMDL: columnas calculadas, relaciones y jerarquias.

Tres reglas del formato que cuesta descubrir y que se congelan aqui:

- La descripcion es un doc-comment `///` ENCIMA de la declaracion; la propiedad
  `description:` no existe y Power BI rechaza el archivo.
- Las columnas se declaran ANTES de la particion, no despues.
- Las relaciones viven en `relationships.tmdl`, no dentro de la tabla, y las
  tablas con espacios van entrecomilladas.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pbip import model_author, project_locator
from pbip.model_author import ModelAuthorError


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _tabla(activo, nombre="Ventas") -> str:
    from pbip.tmdl_reader import find_table_file

    return find_table_file(activo, nombre).read_text(encoding="utf-8-sig")


def _columnas_para_relacion(proyecto):
    model_author.create_calculated_column(proyecto, "Ventas", "Region", '"N"')
    model_author.create_calculated_column(proyecto, "Ventas", "Destino", '"D"')


# -------------------------------------------------- columna calculada -------
def test_columna_calculada_se_declara_antes_de_la_particion(proyecto):
    """Una columna despues de `partition` no la lee Power BI."""
    from pbip.tmdl_reader import find_table_file

    archivo = find_table_file(proyecto, "Ventas")
    archivo.write_text(
        archivo.read_text(encoding="utf-8-sig").rstrip("\n")
        + "\n\n\tpartition Ventas = m\n\t\tmode: import\n\t\tsource = let x = 1\n",
        encoding="utf-8")

    model_author.create_calculated_column(
        proyecto, "Ventas", "Margen", "[Total] * 0.2", data_type="double")
    texto = _tabla(proyecto)
    assert "column Margen = [Total] * 0.2" in texto
    assert texto.index("column Margen") < texto.index("partition Ventas")


def test_columna_lleva_tipo_resumen_y_linaje(proyecto):
    model_author.create_calculated_column(
        proyecto, "Ventas", "Categoria", '"A"', data_type="string",
        display_folder="Clasificacion")
    texto = _tabla(proyecto)
    assert "dataType: string" in texto
    assert "summarizeBy: none" in texto
    assert "lineageTag:" in texto
    assert "displayFolder: Clasificacion" in texto


def test_la_descripcion_es_un_doc_comment(proyecto):
    """`description:` no existe en TMDL: va como /// encima."""
    model_author.create_calculated_column(
        proyecto, "Ventas", "ConNota", "1", description="Explicacion")
    texto = _tabla(proyecto)
    assert "/// Explicacion" in texto
    assert "description:" not in texto


def test_columna_duplicada_exige_permiso(proyecto):
    model_author.create_calculated_column(proyecto, "Ventas", "X", "1")
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_calculated_column(proyecto, "Ventas", "X", "2")
    assert "overwrite" in str(exc.value)

    r = model_author.create_calculated_column(
        proyecto, "Ventas", "X", "2", overwrite=True)
    assert r["action"] == "replaced"
    texto = _tabla(proyecto)
    assert texto.count("column X ") == 1
    assert "column X = 2" in texto


def test_tipo_y_resumen_se_validan(proyecto):
    with pytest.raises(ModelAuthorError):
        model_author.create_calculated_column(
            proyecto, "Ventas", "Y", "1", data_type="unicornio")
    with pytest.raises(ModelAuthorError):
        model_author.create_calculated_column(
            proyecto, "Ventas", "Y", "1", summarize_by="a_ojo")


# --------------------------------------------------------- relaciones -------
def test_relacion_va_en_su_archivo_y_no_en_la_tabla(proyecto):
    _columnas_para_relacion(proyecto)
    r = model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino")
    archivo = Path(r["file"])
    assert archivo.name == "relationships.tmdl"
    texto = archivo.read_text(encoding="utf-8-sig")
    assert "fromColumn: Ventas.Region" in texto
    assert "toColumn: Ventas.Destino" in texto


def test_los_valores_por_defecto_no_se_escriben(proyecto):
    """Muchos-a-uno con filtro simple es el defecto del motor: no se repite."""
    _columnas_para_relacion(proyecto)
    r = model_author.create_relationship(proyecto, "Ventas", "Region",
                                         "Ventas", "Destino")
    texto = Path(r["file"]).read_text(encoding="utf-8-sig")
    assert "fromCardinality" not in texto
    assert "crossFilteringBehavior" not in texto


def test_relacion_bidireccional_si_se_declara(proyecto):
    _columnas_para_relacion(proyecto)
    r = model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino",
        cross_filtering="bothDirections", is_active=False)
    texto = Path(r["file"]).read_text(encoding="utf-8-sig")
    assert "crossFilteringBehavior: bothDirections" in texto
    assert "isActive: false" in texto


def test_relacion_duplicada_exige_permiso(proyecto):
    _columnas_para_relacion(proyecto)
    model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino")
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_relationship(proyecto, "Ventas", "Region",
                                         "Ventas", "Destino")
    assert "Ya existe" in str(exc.value)


def test_overwrite_reemplaza_bloque_completo_y_conserva_nombre(proyecto):
    _columnas_para_relacion(proyecto)
    primera = model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino",
        name="RegionDestino", cross_filtering="bothDirections")

    segunda = model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino",
        overwrite=True, cross_filtering="oneDirection")

    texto = Path(segunda["file"]).read_text(encoding="utf-8-sig")
    assert primera["name"] == segunda["name"] == "RegionDestino"
    assert texto.count("relationship RegionDestino") == 1
    assert "crossFilteringBehavior" not in texto


def test_relacion_rechaza_tabla_o_columna_inexistente_sin_escribir(proyecto):
    relaciones = (Path(proyecto.semantic_model_dir) / "definition" /
                  "relationships.tmdl")

    with pytest.raises(ModelAuthorError):
        model_author.create_relationship(
            proyecto, "Fantasma", "Id", "Ventas", "Monto")
    assert not relaciones.exists()

    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_relationship(
            proyecto, "Ventas", "Fantasma", "Ventas", "Monto")
    assert "available" in exc.value.details
    assert not relaciones.exists()


def test_nombre_de_relacion_duplicado_se_rechaza_sin_escribir(proyecto):
    _columnas_para_relacion(proyecto)
    model_author.create_calculated_column(proyecto, "Ventas", "Alterna", '"A"')
    primera = model_author.create_relationship(
        proyecto, "Ventas", "Region", "Ventas", "Destino", name="Relacion")
    archivo = Path(primera["file"])
    before = archivo.read_bytes()

    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_relationship(
            proyecto, "Ventas", "Alterna", "Ventas", "Monto", name="relacion")

    assert "unicos" in str(exc.value)
    assert archivo.read_bytes() == before


def test_cardinalidad_invalida_se_rechaza(proyecto):
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_relationship(proyecto, "Ventas", "Region",
                                         "Ventas", "Total",
                                         from_cardinality="bastantes")
    assert "many" in str(exc.value)


# --------------------------------------------------------- jerarquias -------
def test_jerarquia_conserva_el_orden_de_los_niveles(proyecto):
    """El orden es el de profundizacion: no se ordena ni se deduplica."""
    model_author.create_calculated_column(proyecto, "Ventas", "Anio", "1")
    model_author.create_calculated_column(proyecto, "Ventas", "Mes", "1")
    r = model_author.create_hierarchy(proyecto, "Ventas", "Calendario",
                                      ["Anio", "Mes"])
    texto = _tabla(proyecto)
    assert r["levels"] == ["Anio", "Mes"]
    assert texto.index("level Anio") < texto.index("level Mes")
    assert "hierarchy Calendario" in texto


def test_jerarquia_sobre_columna_inexistente_lo_dice(proyecto):
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_hierarchy(proyecto, "Ventas", "H", ["NoExiste"])
    assert "NoExiste" in str(exc.value)
    assert exc.value.details["available"]


def test_jerarquia_con_niveles_repetidos_se_rechaza(proyecto):
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_hierarchy(proyecto, "Ventas", "H", ["Region", "Region"])
    assert "repetidos" in str(exc.value)


def test_jerarquia_sin_niveles_se_rechaza(proyecto):
    with pytest.raises(ModelAuthorError):
        model_author.create_hierarchy(proyecto, "Ventas", "H", [])


# --------------------------------------------------- tabla calculada --------
def test_tabla_calculada_declara_columnas_y_particion(proyecto):
    """TMDL exige las columnas: una tabla sin ellas no tiene nada que mostrar."""
    from pathlib import Path

    r = model_author.create_calculated_table(
        proyecto, "Modulos", 'ROW("modulo", "x")',
        columns=[{"name": "modulo", "data_type": "string"},
                 {"name": "puntaje", "data_type": "double"}])
    texto = Path(r["file"]).read_text(encoding="utf-8-sig")

    assert texto.startswith("table Modulos")
    assert "column modulo" in texto and "dataType: string" in texto
    assert "column puntaje" in texto and "dataType: double" in texto
    assert "partition Modulos = calculated" in texto
    assert "mode: import" in texto
    assert 'source = ROW("modulo", "x")' in texto
    # las columnas van declaradas ANTES de la particion
    assert texto.index("column modulo") < texto.index("partition")
    objetivos = {Path(f["path"]).name for f in r["transaction"]["files"]}
    assert objetivos == {"Modulos.tmdl", "model.tmdl"}
    assert r["transaction"]["committed"] is True


def test_tabla_y_registro_revierten_juntos_si_falla_segunda_escritura(
        proyecto, monkeypatch):
    from services import txn

    definition = Path(proyecto.semantic_model_dir) / "definition"
    model_file = definition / "model.tmdl"
    table_file = definition / "tables" / "Atomica.tmdl"
    model_before = model_file.read_bytes()
    original = txn.Transaction.write_text

    def falla_en_modelo(self, target, text):
        if Path(target).name == "model.tmdl":
            raise OSError("fallo inyectado en segunda escritura")
        return original(self, target, text)

    monkeypatch.setattr(txn.Transaction, "write_text", falla_en_modelo)
    with pytest.raises(OSError, match="segunda escritura"):
        model_author.create_calculated_table(
            proyecto, "Atomica", "ROW(1)",
            columns=[{"name": "a", "data_type": "int64"}])

    assert not table_file.exists()
    assert model_file.read_bytes() == model_before


def test_error_nuevo_de_validacion_revierte_tabla_y_registro(
        proyecto, monkeypatch):
    from services import tmdl_validate

    definition = Path(proyecto.semantic_model_dir) / "definition"
    model_file = definition / "model.tmdl"
    table_file = definition / "tables" / "Invalida.tmdl"
    model_before = model_file.read_bytes()
    llamadas = 0

    def valida(_definition, use_tom=True):
        nonlocal llamadas
        llamadas += 1
        if llamadas == 1:
            return {"valid": True, "findings": [], "parsed": True,
                    "parse_checked": True}
        return {
            "valid": False, "parsed": False, "parse_checked": True,
            "findings": [{
                "rule": "tmdl_parse_failed", "severity": "error",
                "object": {"kind": "model"},
                "evidence": {"message": "inyectado"},
                "fix": "corregir",
            }],
        }

    monkeypatch.setattr(tmdl_validate, "validate", valida)
    with pytest.raises(ModelAuthorError, match="introduce errores"):
        model_author.create_calculated_table(
            proyecto, "Invalida", "ROW(1)",
            columns=[{"name": "a", "data_type": "int64"}])

    assert llamadas == 2
    assert not table_file.exists()
    assert model_file.read_bytes() == model_before


def test_sin_columnas_ni_modelo_abierto_lo_dice(proyecto):
    """Adivinar las columnas produciria una tabla vacia sin avisar."""
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_calculated_table(proyecto, "T", "ROW(1)")
    assert "columns" in str(exc.value)


def test_tabla_duplicada_exige_permiso(proyecto):
    model_author.create_calculated_table(
        proyecto, "T", "ROW(1)", columns=[{"name": "a", "data_type": "int64"}])
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_calculated_table(
            proyecto, "T", "ROW(2)", columns=[{"name": "a", "data_type": "int64"}])
    assert "overwrite" in str(exc.value)


def test_tipo_de_columna_invalido_se_rechaza(proyecto):
    with pytest.raises(ModelAuthorError):
        model_author.create_calculated_table(
            proyecto, "T", "ROW(1)", columns=[{"name": "a", "data_type": "vector"}])


def test_el_tipo_en_camelCase_tambien_vale(proyecto):
    """`dataType` es como se llama la propiedad en TMDL y en el esquema JSON.

    Antes solo se leia `data_type` y cualquier otra grafia caia al defecto
    'string' SIN avisar: una columna numerica se escribia como texto y las
    agregaciones dejaban de funcionar en silencio. Un tipo que se pierde sin
    ruido es peor que un error.
    """
    from pathlib import Path

    r = model_author.create_calculated_table(
        proyecto, "Modulos", 'ROW("n", 1)',
        columns=[{"name": "orden", "dataType": "int64"},
                 {"name": "puntaje", "type": "double"}])
    texto = Path(r["file"]).read_text(encoding="utf-8-sig")

    assert "dataType: int64" in texto
    assert "dataType: double" in texto
    assert "dataType: string" not in texto


def test_una_columna_sin_nombre_se_rechaza(proyecto):
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_calculated_table(
            proyecto, "T", "ROW(1)", columns=[{"dataType": "int64"}])
    assert "name" in str(exc.value)


def test_una_clave_desconocida_en_la_columna_se_rechaza(proyecto):
    """Un typo no puede degradar el tipo a texto sin que nadie se entere."""
    with pytest.raises(ModelAuthorError) as exc:
        model_author.create_calculated_table(
            proyecto, "T", "ROW(1)",
            columns=[{"name": "a", "datatipo": "int64"}])
    assert "datatipo" in str(exc.value)


def test_el_nombre_del_archivo_se_sanea(proyecto):
    """Un nombre con caracteres de ruta no puede decidir donde se escribe."""
    from pathlib import Path

    r = model_author.create_calculated_table(
        proyecto, "Con/Barra", "ROW(1)",
        columns=[{"name": "a", "data_type": "int64"}])
    assert Path(r["file"]).name == "Con_Barra.tmdl"
    assert "table 'Con/Barra'" in Path(r["file"]).read_text(encoding="utf-8-sig")


# ------------------------------------------------ modo de almacenamiento ----
def _con_particion(proyecto, modo="import"):
    from pbip.tmdl_reader import find_table_file

    archivo = find_table_file(proyecto, "Ventas")
    archivo.write_text(
        archivo.read_text(encoding="utf-8-sig").rstrip("\n")
        + f"\n\n\tpartition Ventas = m\n\t\tmode: {modo}\n\t\tsource = let x = 1\n",
        encoding="utf-8")
    return archivo


def test_cambiar_a_directquery_devuelve_el_modo_anterior(proyecto):
    """Es un cambio que hay que poder deshacer sabiendo que se toco."""
    _con_particion(proyecto, "import")
    r = model_author.set_storage_mode(proyecto, "Ventas", "directQuery")

    assert r["previous"] == ["import"]
    assert r["partitions_changed"] == 1
    assert "plegable" in r["warning"]
    assert "mode: directQuery" in _tabla(proyecto)


def test_volver_a_import_no_avisa(proyecto):
    _con_particion(proyecto, "directQuery")
    r = model_author.set_storage_mode(proyecto, "Ventas", "import")
    assert r["previous"] == ["directQuery"]
    assert r["warning"] is None


def test_modo_desconocido_se_rechaza(proyecto):
    _con_particion(proyecto)
    with pytest.raises(ModelAuthorError) as exc:
        model_author.set_storage_mode(proyecto, "Ventas", "cuandoDePorSi")
    assert "directQuery" in str(exc.value)


def test_una_tabla_sin_particion_lo_dice(proyecto):
    with pytest.raises(ModelAuthorError) as exc:
        model_author.set_storage_mode(proyecto, "Ventas", "directQuery")
    assert "particion" in str(exc.value)
