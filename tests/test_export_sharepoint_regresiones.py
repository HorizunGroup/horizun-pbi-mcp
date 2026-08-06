"""Regresiones de los defectos confirmados en la exportacion y en Graph.

Cada prueba de este modulo se escribio ANTES de la correccion y falla contra
el codigo defectuoso. No inspeccionan el fuente: producen el comportamiento.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from openpyxl import load_workbook

from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import exporting, sharepoint


# --------------------------------------------------------------------------
# Excel: el encabezado tambien es una celda, y tambien se evalua como formula
# --------------------------------------------------------------------------

def test_un_encabezado_dax_con_igual_no_llega_como_formula(
        session, isolated_settings, monkeypatch):
    """Un nombre de columna DAX que empieza por `=` no puede salir como formula.

    `_json_cell` neutralizaba las celdas de datos, pero los encabezados de
    `Datos_DAX` -que son nombres de columna devueltos por el motor- se escribian
    crudos. openpyxl convierte en formula REAL cualquier cadena que empiece por
    '=': basta un nombre de columna para inyectarla en el libro publicado.
    """
    contexto = exporting.ExportContext(
        source="live",
        model={"tables": [], "measures": [], "relationships": []},
        query_result={"columns": ["=cmd|'/c calc'!A1", "+SUM(1)", "-2+3", "@x"],
                      "rows": [[1, 2, 3, 4]], "row_count": 1,
                      "truncated": False})
    monkeypatch.setattr(exporting, "_collect_context", lambda *_a, **_k: contexto)

    resultado = exporting.export_excel(session, source="live")

    libro = load_workbook(Path(resultado["output_path"]))
    hoja = libro["Datos_DAX"]
    formulas = [c.value for c in hoja[1] if c.data_type == "f"]
    libro.close()
    assert not formulas, f"encabezados publicados como formula: {formulas}"


def test_los_encabezados_neutralizados_siguen_siendo_unicos():
    """Neutralizar no puede colisionar dos nombres distintos en uno solo."""
    encabezados = exporting._unique_headers(["=A", "'=A", "=A"])
    assert len(set(h.casefold() for h in encabezados)) == 3, encabezados


# --------------------------------------------------------------------------
# Graph: paginacion
# --------------------------------------------------------------------------

def test_un_ciclo_de_nextlink_sin_elementos_no_gira_para_siempre(monkeypatch):
    """Un nextLink que se apunta a si mismo con paginas vacias colgaba el server.

    `max_items` solo acota cuando las paginas TRAEN elementos: el corte vivia en
    el consumidor. Con `value: []` el cuerpo del bucle no se ejecutaba nunca y
    `_paged_children` giraba indefinidamente.
    """
    llamadas = {"n": 0}

    def graph(_route, _token):
        llamadas["n"] += 1
        if llamadas["n"] > 500:
            raise AssertionError("bucle infinito: no hay corte de paginacion")
        return {"value": [],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/x/"
                                   "root/children?$skiptoken=SIEMPRE_EL_MISMO"}

    monkeypatch.setattr(sharepoint, "_graph_json", graph)
    with pytest.raises(sharepoint.SharePointRequestError):
        list(sharepoint._paged_children("/inicio", "token"))


def test_la_paginacion_legitima_larga_no_se_bloquea(monkeypatch):
    """El corte anticiclos no puede romper una paginacion normal y larga."""
    def graph(route, _token):
        indice = int(route.rsplit("=", 1)[-1]) if "=" in route else 0
        if indice >= 30:
            return {"value": [{"id": f"i{indice}"}]}
        return {"value": [{"id": f"i{indice}"}],
                "@odata.nextLink":
                f"https://graph.microsoft.com/v1.0/drives/x/root/children?$skiptoken={indice + 1}"}

    monkeypatch.setattr(sharepoint, "_graph_json", graph)
    assert len(list(sharepoint._paged_children("/inicio?$skiptoken=0", "t"))) == 31


# --------------------------------------------------------------------------
# Graph: limitacion de tasa
# --------------------------------------------------------------------------

def _http_error(status, headers=None):
    from urllib.error import HTTPError
    return HTTPError("https://graph.microsoft.com/v1.0/x", status, "rate",
                     headers or {}, io.BytesIO(b'{"error":{"code":"tooManyRequests"}}'))


def test_un_429_con_retry_after_se_reintenta_y_termina_bien(monkeypatch):
    """Graph responde 429 en condiciones normales; abortar es un fallo nuestro."""
    intentos = {"n": 0}
    dormido = []

    def abrir(_request, timeout):  # noqa: ARG001
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise _http_error(429, {"Retry-After": "2"})

        class R(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_a):
                self.close()

        return R(b'{"value":[]}')

    monkeypatch.setattr(sharepoint, "urlopen", abrir)
    monkeypatch.setattr(sharepoint.time, "sleep", dormido.append)

    assert sharepoint._graph_json("/sites/root", "t") == {"value": []}
    assert intentos["n"] == 2
    assert dormido == [2.0], "no se respeto Retry-After"


def test_el_reintento_esta_acotado_y_no_gira_indefinidamente(monkeypatch):
    """Un 503 permanente tiene que rendirse, no reintentar sin fin."""
    intentos = {"n": 0}

    def abrir(_request, timeout):  # noqa: ARG001
        intentos["n"] += 1
        raise _http_error(503)

    monkeypatch.setattr(sharepoint, "urlopen", abrir)
    monkeypatch.setattr(sharepoint.time, "sleep", lambda _s: None)

    with pytest.raises(sharepoint.SharePointRequestError):
        sharepoint._graph_json("/sites/root", "t")
    assert 1 < intentos["n"] <= 6, f"reintentos fuera de rango: {intentos['n']}"


def test_un_404_no_se_reintenta(monkeypatch):
    """Solo se reintenta lo que es transitorio."""
    intentos = {"n": 0}

    def abrir(_request, timeout):  # noqa: ARG001
        intentos["n"] += 1
        raise _http_error(404)

    monkeypatch.setattr(sharepoint, "urlopen", abrir)
    monkeypatch.setattr(sharepoint.time, "sleep", lambda _s: None)

    with pytest.raises(sharepoint.SharePointRequestError):
        sharepoint._graph_json("/sites/root", "t")
    assert intentos["n"] == 1


# --------------------------------------------------------------------------
# Graph: un archivo mentiroso no puede gastar el presupuesto de todo el lote
# --------------------------------------------------------------------------

def test_un_archivo_que_miente_su_tamano_corta_en_su_propio_limite(
        monkeypatch, tmp_path):
    """Graph declara 10 bytes y transmite 8 MB: debe cortar cerca de 10 bytes.

    Antes se pasaba el presupuesto GLOBAL restante como limite por archivo, de
    modo que el primer archivo del lote podia consumirlo entero antes de que la
    comprobacion de tamano declarado llegara a ejecutarse.
    """
    leido = {"total": 0}

    class Torrente(io.RawIOBase):
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self, size=-1):
            trozo = b"x" * (size if size and size > 0 else 65_536)
            leido["total"] += len(trozo)
            if leido["total"] > 8 * 1024 * 1024:
                raise AssertionError(
                    "se consumio el presupuesto global antes de cortar")
            return trozo

    monkeypatch.setattr(sharepoint, "urlopen", lambda *_a, **_k: Torrente())

    with pytest.raises((sharepoint.SharePointLimitError,
                        sharepoint.SharePointRequestError)):
        sharepoint._download_one(
            "https://download.invalid/x", tmp_path / "mentiroso.bin",
            expected_size=10, max_bytes=512 * 1024 * 1024)

    assert leido["total"] < 4 * 1024 * 1024, (
        f"leyo {leido['total']} bytes para un archivo declarado de 10")
    assert not (tmp_path / "mentiroso.bin").exists()
    assert not list(tmp_path.glob("*.part"))


# --------------------------------------------------------------------------
# PDF: la prueba visual no puede mirar siempre solo la primera pagina
# --------------------------------------------------------------------------

def test_la_prueba_visual_cubre_primera_ultima_y_capturas(monkeypatch, tmp_path):
    """Un PDF de varias paginas se comprueba en mas de una pagina.

    Renderizar siempre la pagina 1 deja sin verificar justo donde se rompe el
    documento: las tablas largas y las paginas de capturas.
    """
    from horizun_pbi_mcp.powerbi import desktop_capture

    pedidas: list[int] = []

    def falso_pdftoppm(cmd, **_kwargs):
        # -f N ... -l M  o  -f N -singlefile
        primera = int(cmd[cmd.index("-f") + 1])
        pedidas.append(primera)
        destino = Path(cmd[-1])
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Dos colores distintos: una pagina con contenido, no una en blanco.
        bgra = (b"\x10\x20\x30\xff" + b"\xf0\xe0\xd0\xff") * 2
        Path(str(destino) + ".png").write_bytes(
            desktop_capture._encode_png(2, 2, bgra))

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(exporting.shutil, "which", lambda _n: "pdftoppm")
    monkeypatch.setattr(exporting.subprocess, "run", falso_pdftoppm)

    # Un PDF real de varias paginas, generado por reportlab.
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    estilos = getSampleStyleSheet()
    doc.build([Paragraph("uno", estilos["BodyText"]), PageBreak(),
               Paragraph("dos", estilos["BodyText"]), PageBreak(),
               Paragraph("tres", estilos["BodyText"])])
    pdf = buffer.getvalue()

    informe = exporting._render_probe(pdf, capture_pages=(3,))

    assert informe["status"] == "verified"
    assert 1 in pedidas and 3 in pedidas, (
        f"solo se renderizaron las paginas {sorted(set(pedidas))}")
    assert informe["pages_rendered"] >= 2


def test_una_pagina_en_blanco_no_pasa_como_verificada(monkeypatch):
    """Un PNG uniforme es una pagina en blanco: no es prueba de que se dibujo."""
    from horizun_pbi_mcp.powerbi import desktop_capture

    def falso_pdftoppm(cmd, **_kwargs):
        destino = Path(cmd[-1])
        destino.parent.mkdir(parents=True, exist_ok=True)
        blanco = b"\xff\xff\xff\xff" * (16 * 16)
        Path(str(destino) + ".png").write_bytes(
            desktop_capture._encode_png(16, 16, blanco))

        class Completed:
            returncode = 0

        return Completed()

    monkeypatch.setattr(exporting.shutil, "which", lambda _n: "pdftoppm")
    monkeypatch.setattr(exporting.subprocess, "run", falso_pdftoppm)

    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from reportlab.lib.styles import getSampleStyleSheet

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    doc.build([Paragraph("hola", getSampleStyleSheet()["BodyText"])])

    with pytest.raises(ValidationError, match="en blanco"):
        exporting._render_probe(buffer.getvalue())


# --------------------------------------------------------------------------
# El decodificador PNG frente a los cinco filtros del formato
# --------------------------------------------------------------------------

def _png_con_filtro(ancho, alto, pixeles, filtro):
    """Codifica RGB8 aplicando UN tipo de filtro a todas las filas.

    `pdftoppm` elige el filtro por fila de forma adaptativa. Si el
    des-filtrado del servidor fuera incorrecto, una pagina legitima se
    reportaria como 'en blanco' y bloquearia la generacion del PDF: por eso se
    comprueban los cinco, no solo el que emite el codificador propio.
    """
    import struct
    import zlib

    def paeth(a, b, c):
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        return a if pa <= pb and pa <= pc else (b if pb <= pc else c)

    ancho_linea = ancho * 3
    crudo = bytearray()
    anterior = bytearray(ancho_linea)
    for y in range(alto):
        linea = bytearray(pixeles[y * ancho_linea:(y + 1) * ancho_linea])
        salida = bytearray(ancho_linea)
        for i in range(ancho_linea):
            a = linea[i - 3] if i >= 3 else 0
            b = anterior[i]
            c = anterior[i - 3] if i >= 3 else 0
            x = linea[i]
            if filtro == 0:
                salida[i] = x
            elif filtro == 1:
                salida[i] = (x - a) & 0xFF
            elif filtro == 2:
                salida[i] = (x - b) & 0xFF
            elif filtro == 3:
                salida[i] = (x - ((a + b) >> 1)) & 0xFF
            else:
                salida[i] = (x - paeth(a, b, c)) & 0xFF
        crudo.append(filtro)
        crudo.extend(salida)
        anterior = linea

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    cabecera = struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", cabecera) +
            chunk(b"IDAT", zlib.compress(bytes(crudo))) + chunk(b"IEND", b""))


@pytest.mark.parametrize("filtro", [0, 1, 2, 3, 4])
def test_el_decodificador_png_maneja_los_cinco_filtros(filtro):
    ancho = alto = 8
    degradado = bytes(bytearray(
        (x * 7 + y * 11) % 256
        for y in range(alto) for x in range(ancho * 3)))
    uniforme = bytes(bytearray([0x77] * (ancho * alto * 3)))

    assert exporting._png_pixeles_uniformes(
        _png_con_filtro(ancho, alto, degradado, filtro)) is False, (
        f"filtro {filtro}: una imagen con contenido se leyo como uniforme")
    assert exporting._png_pixeles_uniformes(
        _png_con_filtro(ancho, alto, uniforme, filtro)) is True, (
        f"filtro {filtro}: una imagen uniforme no se detecto")


def test_un_png_que_no_se_sabe_leer_no_se_declara_verificado():
    """16 bits por canal no se decodifica: se dice, no se finge."""
    import struct
    import zlib

    def chunk(kind, payload):
        body = kind + payload
        return (struct.pack(">I", len(payload)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    cabecera = struct.pack(">IIBBBBB", 2, 2, 16, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", cabecera) +
           chunk(b"IDAT", zlib.compress(b"\x00" * 26)) + chunk(b"IEND", b""))
    assert exporting._png_pixeles_uniformes(png) is None
