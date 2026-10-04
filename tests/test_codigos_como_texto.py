"""Una columna de codigo se carga como texto, aunque parezca un numero.

Por que existe: al grabar una demo, un `tablero_avance.csv` con `codigo` = 1.01,
2.03, 2.10 se cargo con `pbi_add_table_from_file` y la columna salio `double`.
Como numero, `2.10` se vuelve `2.1` y deja de cruzar con el `2.10` de las demas
tablas; el agente tuvo que corregir el TMDL a mano. Peor aun: los codigos
votaban en la deteccion del separador decimal, y en un archivo con `;` y coma
decimal empataban con los valores y decidian `en-US`, multiplicando las
cantidades por cien sin ningun error.

Todo sintetico: los archivos se fabrican en tmp_path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from horizun_pbi_mcp.pbip import project_locator, table_from_file


@pytest.fixture
def proyecto(session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    return session.require_active_pbip()


def _csv(tmp_path: Path, contenido: str, nombre: str = "avance.csv") -> Path:
    ruta = tmp_path / nombre
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def _tipos(perfil):
    return {c["name"]: c["data_type"] for c in perfil["columns"]}


def test_un_codigo_con_decimales_se_carga_como_texto(tmp_path):
    """El caso tal cual se vio: 2.10 no puede volverse 2.1."""
    ruta = _csv(tmp_path, "codigo,descripcion,valor\n"
                          "1.01,Cimentacion,439.54\n"
                          "2.03,Columnas,112.35\n"
                          "2.10,Vigas,358.80\n")
    perfil = table_from_file.perfilar(ruta)

    assert _tipos(perfil) == {"codigo": "string", "descripcion": "string",
                              "valor": "double"}
    assert any("'codigo'" in a for a in perfil["warnings"])


def test_la_m_tipa_el_codigo_como_texto(tmp_path):
    ruta = _csv(tmp_path, "codigo,valor\n1.01,10\n2.10,20\n")
    m = table_from_file.construir_m(table_from_file.perfilar(ruta))

    assert '{"codigo", type text}' in m


def test_la_tabla_declara_el_codigo_como_string_y_sin_sumar(proyecto, tmp_path):
    ruta = _csv(tmp_path, "codigo,valor\n1.01,10\n2.10,20\n")
    r = table_from_file.agregar_tabla(proyecto, ruta, table_name="Avance")
    assert r["validated"] is True

    tmdl = (Path(proyecto.semantic_model_dir) / "definition" / "tables"
            / "Avance.tmdl").read_text(encoding="utf-8-sig")
    bloque = tmdl.split("\tcolumn codigo", 1)[1].split("\tcolumn ", 1)[0]
    assert "dataType: string" in bloque
    assert "summarizeBy: none" in bloque
    assert "formatString" not in bloque


@pytest.mark.parametrize("encabezado", [
    "codigo", "Código", "CODIGO_PARTIDA", "CodigoPartida", "cod", "Cod.",
    "item", "Ítem", "code", "item_code", "clave", "SKU", "ref",
])
def test_reconoce_los_encabezados_de_codigo(tmp_path, encabezado):
    ruta = _csv(tmp_path, f"{encabezado},valor\n1.01,10\n2.10,20\n")
    assert _tipos(table_from_file.perfilar(ruta))[encabezado] == "string"


@pytest.mark.parametrize("encabezado", [
    "valor", "cantidad", "item_count", "cantidad_items", "valor_item",
    "decodificado", "codo",
])
def test_no_toca_columnas_que_no_son_codigos(tmp_path, encabezado):
    """`item_count` es una cantidad; 'decodificado' o 'codo' no son 'cod'."""
    ruta = _csv(tmp_path, f"{encabezado},otro\n1.25,a\n2.50,b\n")
    assert _tipos(table_from_file.perfilar(ruta))[encabezado] == "double"


def test_los_codigos_no_deciden_la_cultura(tmp_path):
    """Tres codigos con punto contra tres valores con coma: antes ganaba el
    punto (empate) y la consulta leia 439,54 como 43954 con en-US."""
    ruta = _csv(tmp_path, "codigo;partida;valor\n"
                          "1.01;Cimentacion;439,54\n"
                          "2.03;Columnas;112,35\n"
                          "2.10;Vigas;358,8\n")
    perfil = table_from_file.perfilar(ruta)

    assert perfil["decimal_separator"] == ","
    assert perfil["culture"] == "es-ES"
    assert _tipos(perfil) == {"codigo": "string", "partida": "string",
                              "valor": "double"}


def test_ceros_a_la_izquierda_se_quedan_en_texto(tmp_path):
    """`007` como numero es 7: el valor cambia aunque la columna no se llame
    codigo."""
    ruta = _csv(tmp_path, "cuenta,valor\n007,10\n0451,20\n1200,30\n")
    perfil = table_from_file.perfilar(ruta)

    assert _tipos(perfil)["cuenta"] == "string"
    assert any("'cuenta'" in a and "ceros" in a for a in perfil["warnings"])


def test_un_cero_solo_sigue_siendo_numero(tmp_path):
    ruta = _csv(tmp_path, "cantidad,otro\n0,a\n10,b\n25,c\n")
    assert _tipos(table_from_file.perfilar(ruta))["cantidad"] == "int64"


def test_un_codigo_de_texto_no_genera_aviso(tmp_path):
    """Si ya era texto (A-1, FE-001) no cambia nada y avisar seria ruido."""
    ruta = _csv(tmp_path, "Codigo,Valor\nA-1,10527.52\nA-2,1795.40\n")
    perfil = table_from_file.perfilar(ruta)

    assert _tipos(perfil)["Codigo"] == "string"
    assert not any("'Codigo'" in a for a in perfil.get("warnings") or [])


def test_text_columns_fuerza_texto_en_lo_que_el_encabezado_no_delata(tmp_path):
    ruta = _csv(tmp_path, "partida,valor\n1.01,10\n2.10,20\n")
    assert _tipos(table_from_file.perfilar(ruta))["partida"] == "double"

    perfil = table_from_file.perfilar(ruta, text_columns=["Partida"])
    assert _tipos(perfil) == {"partida": "string", "valor": "int64"}


def test_text_columns_con_un_nombre_que_no_existe_es_error(tmp_path):
    """No se ignora en silencio: quien la pidio cree que se aplico."""
    ruta = _csv(tmp_path, "partida,valor\n1.01,10\n")
    with pytest.raises(table_from_file.TableFromFileError) as exc:
        table_from_file.perfilar(ruta, text_columns=["partdia"])
    assert exc.value.details["missing"] == ["partdia"]


def test_agregar_tabla_pasa_text_columns(proyecto, tmp_path):
    ruta = _csv(tmp_path, "partida,valor\n1.01,10\n2.10,20\n")
    r = table_from_file.agregar_tabla(proyecto, ruta, table_name="Avance",
                                      text_columns=["partida"], dry_run=True)

    assert {c["name"]: c["data_type"] for c in r["columns"]}["partida"] == "string"
    assert '{"partida", type text}' in r["m"]


def test_el_codigo_de_un_json_tambien_es_texto(tmp_path):
    """La regla vale para todos los formatos, no solo csv."""
    import json
    ruta = tmp_path / "avance.json"
    ruta.write_text(json.dumps([{"codigo": "1.01", "valor": 10},
                                {"codigo": "2.10", "valor": 20}]),
                    encoding="utf-8")
    assert _tipos(table_from_file.perfilar(ruta))["codigo"] == "string"
