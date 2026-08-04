"""Cargar un archivo al modelo como lo haria una persona: abrir, transformar, cargar.

Es el mismo recorrido que hace alguien en Power BI —Obtener datos, promover
encabezados, cambiar tipos, Cargar— pero escrito directo en el proyecto. La
consulta resultante usa los nombres de paso que pone Power BI en español
(`Origen`, `Encabezados promovidos`, `Tipo cambiado`), porque quien abra el
editor de Power Query va a verlos y unos nombres inventados delatan la maquina
y estorban para editar.

Por que existe: meter un CSV a mano obligaba a redactar la particion M y el
TMDL a pelo, y ahi nacieron las cinco trampas que documenta
`services/tmdl_validate.py`. La regla de diseño aqui es que el generador **no
pueda** cometerlas:

- La **cultura se deduce del archivo**, mirando como escribe los decimales, y
  se emite SIEMPRE explicita. Asumir la del modelo es lo que convierte
  `10527.52` en diez millones sin que nada falle.
- El TMDL se compone en orden (propiedades, columnas, particion) y se **valida
  contra `pbi_validate_tmdl` antes de confirmar**. Si el resultado no pasara, la
  escritura se revierte en vez de dejar un proyecto que no abre.

Sin dependencias nuevas: CSV con `csv`, Excel con `zipfile` —un .xlsx es un zip
con XML— y JSON con `json`.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import uuid
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("table_from_file")


class TableFromFileError(PowerBIMCPError):
    code = "table_from_file_error"


#: Extensiones que sabemos cargar. Lo demas se rechaza por su nombre, para que
#: el mensaje diga QUE formato es y no un generico "no soportado". '.xls' NO
#: mapea directo a un formato fijo: hay que mirar los primeros bytes (ver
#: `_formato_real_de_xls`), porque en la practica un '.xls' casi nunca es el
#: binario OLE2 que la extension promete.
_FORMATOS = {".csv": "csv", ".txt": "csv", ".tsv": "csv",
             ".xlsx": "xlsx", ".xlsm": "xlsx", ".json": "json",
             ".html": "html", ".htm": "html"}

#: Firma de un .xls binario de verdad (Excel 97-2003, formato OLE2/CFB).
_MAGIC_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
#: Firma de un .xlsx/.xlsm real (son un .zip), aunque venga con extension .xls.
_MAGIC_ZIP = b"PK\x03\x04"

#: Juegos de caracteres declarados en <meta charset> -> codec de Python. Los
#: reportes de ERPs casi nunca declaran UTF-8 de verdad: 'windows-1252' e
#: 'iso-8859-1' son el caso normal, no la excepcion.
_CHARSET_A_CODEC = {
    "utf-8": "utf-8", "utf8": "utf-8",
    "windows-1252": "cp1252", "cp1252": "cp1252",
    "iso-8859-1": "cp1252", "latin1": "cp1252", "latin-1": "cp1252",
    "iso-8859-15": "cp1252",
}
#: codec de Python -> textEncoding de Power Query (`Text.FromBinary`). Son los
#: dos unicos que este cargador necesita distinguir; falta cualquier otro se
#: trata como 1252 porque es el fallback mas comun en reportes latinoamericanos.
_CODEC_A_TEXTENCODING = {"utf-8": 65001, "cp1252": 1252}

_DELIMITADORES = (",", ";", "\t", "|")

#: Tipo TMDL -> tipo M. La M la escribe Power Query; el TMDL declara la columna.
_TIPO_M = {"int64": "Int64.Type", "double": "type number",
           "decimal": "Currency.Type", "dateTime": "type date",
           "boolean": "type logical", "string": "type text"}

_VERDADEROS = {"true", "verdadero", "si", "sí", "yes", "1"}
_FALSOS = {"false", "falso", "no", "0"}

_RE_ENTERO = re.compile(r"^-?\d+$")
_RE_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")
_RE_DMY = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{4}([ T].*)?$")

_NS_HOJA = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


# ---------------------------------------------------------------------------
# Inferencia de tipos
# ---------------------------------------------------------------------------

def _separador_decimal(valores: List[str]) -> Optional[str]:
    """Deduce si los decimales se escriben con punto o con coma.

    Cuando aparecen los dos, manda el ULTIMO: en `1.234,56` el punto agrupa
    miles y la coma decide los decimales. Si solo hay uno, se mira cuantas
    cifras lo siguen: tres exactas huelen a separador de miles.
    """
    votos = {".": 0, ",": 0}
    for bruto in valores:
        v = bruto.strip()
        if not v or not re.fullmatch(r"-?[\d.,]+", v):
            continue
        tiene_punto, tiene_coma = "." in v, "," in v
        if tiene_punto and tiene_coma:
            votos["." if v.rindex(".") > v.rindex(",") else ","] += 1
        elif tiene_punto or tiene_coma:
            sep = "." if tiene_punto else ","
            if v.count(sep) == 1:
                decimales = v.split(sep)[1]
                # 3 cifras justas es ambiguo: puede ser millar. No vota.
                if len(decimales) != 3:
                    votos[sep] += 1
    if votos["."] == votos[","] == 0:
        return None
    return "." if votos["."] >= votos[","] else ","


def _es_numero(v: str, sep: str) -> bool:
    limpio = v.replace("," if sep == "." else ".", "").replace(sep, ".")
    try:
        float(limpio)
        return True
    except ValueError:
        return False


def _inferir_tipo(valores: List[str], sep_decimal: Optional[str]) -> str:
    """Tipo de una columna a partir de sus valores. Vacio no es un dato."""
    utiles = [v.strip() for v in valores if v is not None and v.strip() != ""]
    if not utiles:
        # Sin un solo valor no hay nada que deducir, y adivinar numerico
        # romperia la carga en cuanto llegara un texto.
        return "string"

    bajos = [v.casefold() for v in utiles]
    if all(v in _VERDADEROS or v in _FALSOS for v in bajos):
        return "boolean"
    if all(_RE_ENTERO.fullmatch(v) for v in utiles):
        return "int64"
    if sep_decimal and all(_es_numero(v, sep_decimal) for v in utiles):
        return "double"
    if all(_RE_ISO.match(v) or _RE_DMY.match(v) for v in utiles):
        return "dateTime"
    return "string"


# ---------------------------------------------------------------------------
# Lectura de cada formato
# ---------------------------------------------------------------------------

def _perfilar_csv(ruta: Path, muestra: int) -> Dict[str, Any]:
    crudo = ruta.read_bytes()
    tiene_bom = crudo.startswith(b"\xef\xbb\xbf")
    texto = crudo.decode("utf-8-sig" if tiene_bom else "utf-8", errors="replace")

    cabecera = texto.splitlines()[0] if texto.splitlines() else ""
    delimitador = max(_DELIMITADORES, key=cabecera.count)
    if cabecera.count(delimitador) == 0:
        delimitador = ","

    filas = list(csv.reader(io.StringIO(texto), delimiter=delimitador))
    if not filas:
        raise TableFromFileError(f"El archivo esta vacio: {ruta}")
    encabezados = [c.strip() for c in filas[0]]
    datos = filas[1:muestra + 1]

    columnas_valores: List[List[str]] = [
        [f[i] if i < len(f) else "" for f in datos]
        for i in range(len(encabezados))]
    sep = _separador_decimal([v for col in columnas_valores for v in col])

    return {
        "format": "csv", "path": str(ruta), "delimiter": delimitador,
        "encoding": 65001, "has_bom": tiene_bom, "sheet": None,
        "decimal_separator": sep,
        "row_sample": len(datos),
        "columns": [{"name": n, "data_type": _inferir_tipo(v, sep)}
                    for n, v in zip(encabezados, columnas_valores)],
    }


def _hojas_xlsx(z: zipfile.ZipFile) -> List[str]:
    import xml.etree.ElementTree as ET

    raiz = ET.fromstring(z.read("xl/workbook.xml"))
    return [h.get("name") for h in raiz.iter(f"{_NS_HOJA}sheet") if h.get("name")]


#: Formatos de fecha/hora integrados de Excel (ECMA-376).
_NUMFMT_FECHA = set(range(14, 23)) | set(range(45, 48))


def _estilos_de_fecha(z: zipfile.ZipFile) -> set:
    """Indices de estilo que pintan una fecha.

    Excel no guarda fechas: guarda dias desde 1899-12-30 y, aparte, un formato
    que dice como mostrarlos. Sin mirar el formato, una fecha es un entero
    cualquiera y la columna se declara mal.
    """
    import xml.etree.ElementTree as ET

    if "xl/styles.xml" not in z.namelist():
        return set()
    try:
        raiz = ET.fromstring(z.read("xl/styles.xml"))
    except Exception:  # noqa: BLE001 - un styles.xml roto no debe tumbar la carga
        return set()

    personalizados = {
        int(f.get("numFmtId")): (f.get("formatCode") or "")
        for f in raiz.iter(f"{_NS_HOJA}numFmt") if f.get("numFmtId")}
    fechas = set()
    xfs = raiz.find(f"{_NS_HOJA}cellXfs")
    for indice, xf in enumerate(list(xfs) if xfs is not None else []):
        try:
            numfmt = int(xf.get("numFmtId") or 0)
        except ValueError:
            continue
        codigo = personalizados.get(numfmt, "")
        # Un formato personalizado es fecha si menciona año, mes o dia y no es
        # solo una hora suelta.
        if numfmt in _NUMFMT_FECHA or re.search(r"[yYmMdD]", _sin_literales(codigo)):
            fechas.add(indice)
    return fechas


def _sin_literales(codigo: str) -> str:
    """Quita lo escapado y entrecomillado de un formato numerico de Excel."""
    codigo = re.sub(r'"[^"]*"', "", codigo)
    return re.sub(r"\\.", "", codigo)


def _serial_a_fecha(bruto: str) -> str:
    """Convierte el serial de Excel a ISO. 1899-12-30 es el dia cero."""
    from datetime import datetime, timedelta

    try:
        dias = float(bruto)
    except (TypeError, ValueError):
        return bruto
    return (datetime(1899, 12, 30) + timedelta(days=dias)).strftime("%Y-%m-%d")


def _filas_xlsx(z: zipfile.ZipFile, indice: int, muestra: int) -> List[List[str]]:
    import xml.etree.ElementTree as ET

    estilos_fecha = _estilos_de_fecha(z)
    compartidas: List[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in raiz.iter(f"{_NS_HOJA}si"):
            compartidas.append("".join(t.text or "" for t in si.iter(f"{_NS_HOJA}t")))

    nombre = f"xl/worksheets/sheet{indice + 1}.xml"
    if nombre not in z.namelist():
        return []
    raiz = ET.fromstring(z.read(nombre))
    filas: List[List[str]] = []
    for fila in raiz.iter(f"{_NS_HOJA}row"):
        valores: List[str] = []
        for celda in fila.iter(f"{_NS_HOJA}c"):
            tipo = celda.get("t")
            if tipo == "inlineStr":
                valores.append("".join(t.text or ""
                                       for t in celda.iter(f"{_NS_HOJA}t")))
            else:
                v = celda.find(f"{_NS_HOJA}v")
                bruto = v.text if v is not None else ""
                if tipo == "s" and bruto and bruto.isdigit():
                    idx = int(bruto)
                    bruto = compartidas[idx] if idx < len(compartidas) else ""
                elif bruto and tipo in (None, "n"):
                    try:
                        estilo = int(celda.get("s") or -1)
                    except ValueError:
                        estilo = -1
                    if estilo in estilos_fecha:
                        bruto = _serial_a_fecha(bruto)
                valores.append(bruto or "")
        filas.append(valores)
        if len(filas) > muestra:
            break
    return filas


def _perfilar_xlsx(ruta: Path, hoja: Optional[str], muestra: int) -> Dict[str, Any]:
    with zipfile.ZipFile(ruta) as z:
        hojas = _hojas_xlsx(z)
        if not hojas:
            raise TableFromFileError(f"El libro no tiene hojas: {ruta}")
        if hoja is None:
            hoja = hojas[0]
        elif hoja not in hojas:
            raise TableFromFileError(
                f"El libro no tiene la hoja '{hoja}'. Tiene: "
                f"{', '.join(hojas)}.",
                details={"sheets": hojas, "path": str(ruta)})
        filas = _filas_xlsx(z, hojas.index(hoja), muestra)

    if not filas:
        raise TableFromFileError(f"La hoja '{hoja}' esta vacia: {ruta}")
    encabezados = [str(c).strip() for c in filas[0]]
    datos = filas[1:]
    columnas_valores = [[f[i] if i < len(f) else "" for f in datos]
                        for i in range(len(encabezados))]
    sep = _separador_decimal([v for col in columnas_valores for v in col])

    return {
        "format": "xlsx", "path": str(ruta), "sheet": hoja, "delimiter": None,
        "encoding": None, "has_bom": False, "decimal_separator": sep,
        "row_sample": len(datos),
        "columns": [{"name": n, "data_type": _inferir_tipo(v, sep)}
                    for n, v in zip(encabezados, columnas_valores)],
    }


def _perfilar_json(ruta: Path, muestra: int) -> Dict[str, Any]:
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    if isinstance(datos, dict):
        listas = [v for v in datos.values() if isinstance(v, list)]
        datos = listas[0] if listas else [datos]
    if not isinstance(datos, list) or not datos:
        raise TableFromFileError(
            f"El JSON no contiene una lista de registros que cargar: {ruta}")
    registros = [r for r in datos[:muestra] if isinstance(r, dict)]
    if not registros:
        raise TableFromFileError(
            f"El JSON no contiene objetos, solo valores sueltos: {ruta}")

    encabezados: List[str] = []
    for r in registros:
        for k in r:
            if k not in encabezados:
                encabezados.append(k)
    columnas_valores = [["" if r.get(n) is None else str(r.get(n))
                         for r in registros] for n in encabezados]
    sep = _separador_decimal([v for col in columnas_valores for v in col])
    return {
        "format": "json", "path": str(ruta), "sheet": None, "delimiter": None,
        "encoding": 65001, "has_bom": False, "decimal_separator": sep,
        "row_sample": len(registros),
        "columns": [{"name": n, "data_type": _inferir_tipo(v, sep)}
                    for n, v in zip(encabezados, columnas_valores)],
    }


# ---------------------------------------------------------------------------
# '.xls' que en realidad es HTML (el caso normal en exportaciones de ERP)
# ---------------------------------------------------------------------------

def _formato_real_de_xls(ruta: Path) -> str:
    """Mira los primeros bytes: la extension '.xls' no dice la verdad.

    Un ERP casi nunca exporta el binario OLE2 que la extension promete;
    exporta una tabla HTML y la nombra '.xls' porque Excel la abre igual.
    Tratarlo como el .xls real (`Excel.Workbook`) falla en seco; tratarlo
    siempre como HTML rompe el .xls autentico que si aparezca. Se decide
    mirando la firma, no la extension.
    """
    cabecera = ruta.read_bytes()[:8]
    if cabecera.startswith(_MAGIC_OLE2):
        raise TableFromFileError(
            f"'{ruta.name}' es un .xls binario real (formato Excel 97-2003, "
            "OLE2). Este cargador no lo lee: convierte el archivo a .xlsx "
            "desde Excel y vuelve a intentarlo.",
            details={"path": str(ruta), "detected": "ole2"})
    if cabecera.startswith(_MAGIC_ZIP):
        return "xlsx"  # un .xlsx/.xlsm real, solo que renombrado a .xls
    return "html"  # el caso normal: tabla HTML con extension .xls


def _detectar_codec(crudo: bytes) -> str:
    """Codec de Python a partir de un <meta charset> o Content-Type.

    Se busca en los primeros 4KB decodificados como latin-1 (que nunca falla
    y conserva cada byte 1 a 1): los <meta> siempre estan en ASCII puro, asi
    que buscarlos ahi es seguro sin importar la codificacion real del resto.
    """
    cabecera = crudo[:4096].decode("latin-1")
    m = re.search(r'charset\s*=\s*"?\'?([\w-]+)', cabecera, re.I)
    declarado = (m.group(1).lower() if m else None)
    return _CHARSET_A_CODEC.get(declarado or "", "cp1252")


class _TablaHTML(HTMLParser):
    """Extrae todas las <table> de un HTML, con celdas colspan repetidas.

    Repetir el valor de una celda `colspan=N` en las N columnas que ocupa es
    el MISMO criterio que usa `Web.Page` de Power Query al aplanar una fila:
    reproducirlo aqui es lo que permite que el indice/id de tabla que se
    calcula en Python siga significando lo mismo dentro de la consulta M.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tablas: List[Dict[str, Any]] = []
        self._pila_tablas: List[int] = []
        self._fila_actual: Optional[List[Tuple[str, int]]] = None
        self._celda_actual: Optional[List[str]] = None

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "table":
            self.tablas.append({"id": atributos.get("id"), "rows": [],
                                "has_th": False})
            self._pila_tablas.append(len(self.tablas) - 1)
        elif tag == "tr" and self._pila_tablas:
            self._fila_actual = []
        elif tag in ("td", "th") and self._fila_actual is not None:
            if tag == "th" and self._pila_tablas:
                self.tablas[self._pila_tablas[-1]]["has_th"] = True
            try:
                colspan = max(1, int(atributos.get("colspan", 1)))
            except ValueError:
                colspan = 1
            self._celda_actual = []
            self._colspan_actual = colspan

    def handle_data(self, data):
        if self._celda_actual is not None:
            self._celda_actual.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._celda_actual is not None:
            texto = "".join(self._celda_actual).replace("\xa0", " ").strip()
            self._fila_actual.extend([texto] * self._colspan_actual)
            self._celda_actual = None
        elif tag == "tr" and self._fila_actual is not None and self._pila_tablas:
            self.tablas[self._pila_tablas[-1]]["rows"].append(self._fila_actual)
            self._fila_actual = None
        elif tag == "table" and self._pila_tablas:
            self._pila_tablas.pop()


def _elegir_tabla(tablas: List[Dict[str, Any]], table_id: Optional[str]
                  ) -> Tuple[Dict[str, Any], int, List[str]]:
    """Devuelve (tabla, indice, avisos). Sin `table_id`, la mas grande gana."""
    avisos: List[str] = []
    if table_id is not None:
        for i, t in enumerate(tablas):
            if t["id"] == table_id:
                return t, i, avisos
        disponibles = [t["id"] for t in tablas if t["id"]]
        raise TableFromFileError(
            f"No hay ninguna <table id=\"{table_id}\"> en el archivo.",
            details={"requested_id": table_id, "available_ids": disponibles})

    no_vacias = [(i, t) for i, t in enumerate(tablas) if t["rows"]]
    if not no_vacias:
        raise TableFromFileError("El archivo no contiene ninguna tabla HTML "
                                 "con filas.")
    indice, tabla = max(no_vacias, key=lambda par: len(par[1]["rows"]))
    if tabla["id"] is None:
        avisos.append(
            f"Se eligio la tabla mas grande ({len(tabla['rows'])} filas, "
            f"posicion {indice}) porque ninguna <table> tiene 'id'. Sin un "
            "id, la consulta M selecciona por POSICION: si el archivo cambia "
            "de estructura en el proximo reporte, puede dejar de apuntar a "
            "la tabla correcta. Verificalo en el editor de Power Query.")
    return tabla, indice, avisos


def _perfilar_html(ruta: Path, muestra: int, table_id: Optional[str] = None
                   ) -> Dict[str, Any]:
    crudo = ruta.read_bytes()
    codec = _detectar_codec(crudo)
    texto = crudo.decode(codec, errors="replace")

    parser = _TablaHTML()
    parser.feed(texto)
    tabla, indice, avisos = _elegir_tabla(parser.tablas, table_id)
    filas = tabla["rows"]

    ancho = max((len(f) for f in filas), default=0)
    if ancho == 0:
        raise TableFromFileError(f"La tabla elegida no tiene columnas: {ruta}")
    filas = [f + [""] * (ancho - len(f)) for f in filas]  # rellena filas cortas

    # Un reporte de ERP casi nunca tiene una fila 1 que sirva de encabezado
    # (suele ser el titulo del reporte). Solo se promueve si ESTA tabla usa
    # <th> en algun punto: adivinarlo a partir del texto de la fila 1 seria
    # inventar una estructura que el archivo no afirma tener.
    promover = bool(tabla["rows"]) and tabla.get("has_th", False)
    if promover:
        encabezados = [c or f"Column{i + 1}" for i, c in enumerate(filas[0])]
        datos = filas[1:muestra + 1]
    else:
        encabezados = [f"Column{i + 1}" for i in range(ancho)]
        datos = filas[:muestra]

    columnas_valores = [[f[i] if i < len(f) else "" for f in datos]
                        for i in range(ancho)]
    sep = _separador_decimal([v for col in columnas_valores for v in col])

    return {
        "format": "html", "path": str(ruta), "sheet": None, "delimiter": None,
        "encoding": _CODEC_A_TEXTENCODING.get(codec, 1252), "has_bom": False,
        "decimal_separator": sep, "row_sample": len(datos),
        "table_id": tabla["id"], "table_index": indice,
        "promote_headers": promover, "warnings": avisos,
        "columns": [{"name": n, "data_type": _inferir_tipo(v, sep)}
                    for n, v in zip(encabezados, columnas_valores)],
    }


def perfilar(path: Path | str, sheet: Optional[str] = None,
             muestra: int = 200, table_id: Optional[str] = None) -> Dict[str, Any]:
    """Mira dentro del archivo y deduce columnas, tipos y cultura."""
    ruta = Path(path).expanduser()
    if not ruta.exists():
        raise TableFromFileError(f"El archivo no existe: {ruta}",
                                 details={"path": str(ruta)})
    if ruta.suffix.lower() == ".xls":
        # '.xls' no se toma por su palabra: se mira la firma real del
        # archivo (ver `_formato_real_de_xls`).
        formato = _formato_real_de_xls(ruta)
    else:
        formato = _FORMATOS.get(ruta.suffix.lower())
    if formato is None:
        raise TableFromFileError(
            f"No se sabe cargar un '{ruta.suffix}'. Formatos admitidos: "
            f"{', '.join(sorted(_FORMATOS))}, .xls (si es HTML o un .xlsx "
            "renombrado).",
            details={"path": str(ruta), "suffix": ruta.suffix})

    if formato == "csv":
        perfil = _perfilar_csv(ruta, muestra)
    elif formato == "xlsx":
        perfil = _perfilar_xlsx(ruta, sheet, muestra)
    elif formato == "html":
        perfil = _perfilar_html(ruta, muestra, table_id)
    else:
        perfil = _perfilar_json(ruta, muestra)

    if not perfil["columns"]:
        raise TableFromFileError(f"No se encontro ninguna columna en {ruta}")
    vacios = [i for i, c in enumerate(perfil["columns"]) if not c["name"]]
    if vacios:
        raise TableFromFileError(
            f"Hay {len(vacios)} columna(s) sin nombre en la fila de "
            "encabezados. Power BI no puede cargarlas asi.",
            details={"positions": vacios, "path": str(ruta)})

    vistos: Dict[str, tuple[int, str]] = {}
    for indice, columna in enumerate(perfil["columns"]):
        nombre = str(columna["name"])
        controles = [
            {"char": repr(ch), "codepoint": f"U+{ord(ch):04X}"}
            for ch in nombre
            if ch in "\r\n" or unicodedata.category(ch).startswith("C")
        ]
        if controles:
            # No se sustituye silenciosamente: el nombre original tambien se
            # usa en Power Query para promover y tipar la columna. Cambiar solo
            # el TMDL produciria una tabla que abre pero no refresca. El detalle
            # conserva la representacion exacta para que la correccion sea
            # reversible en el archivo fuente.
            raise TableFromFileError(
                f"El encabezado de la columna {indice} contiene saltos de "
                "linea o caracteres de control y no puede escribirse en TMDL.",
                details={"index": indice, "header": repr(nombre),
                         "controls": controles, "path": str(ruta)})
        clave = nombre.casefold()
        if clave in vistos:
            anterior, original = vistos[clave]
            raise TableFromFileError(
                f"Los encabezados {anterior} ('{original}') e {indice} "
                f"('{nombre}') son duplicados para el motor de Power BI.",
                details={"first_index": anterior, "second_index": indice,
                         "header": nombre, "path": str(ruta)})
        vistos[clave] = (indice, nombre)

    # La cultura sale de como escribe los decimales el propio archivo. Es el
    # unico dato que no obliga a suponer.
    perfil["culture"] = {".": "en-US", ",": "es-ES"}.get(
        perfil["decimal_separator"] or ".", "en-US")
    return perfil


# ---------------------------------------------------------------------------
# La consulta M
# ---------------------------------------------------------------------------

def _literal_m(texto: str) -> str:
    """Literal de cadena en M: solo hay que duplicar las comillas."""
    return '"' + str(texto).replace('"', '""') + '"'


def _lista_tipos(perfil: Dict[str, Any]) -> str:
    partes = [f'{{{_literal_m(c["name"])}, {_TIPO_M[c["data_type"]]}}}'
              for c in perfil["columns"]]
    return "{" + ", ".join(partes) + "}"


def construir_m(perfil: Dict[str, Any], culture: Optional[str] = None) -> str:
    """Consulta M con los pasos que pondria una persona en Power Query."""
    cultura = culture or perfil["culture"]
    ruta = _literal_m(perfil["path"])
    tipos = _lista_tipos(perfil)
    pasos: List[str] = []

    if perfil["format"] == "csv":
        columnas = len(perfil["columns"])
        pasos.append(
            f'    Origen = Csv.Document(File.Contents({ruta}), '
            f'[Delimiter = {_literal_m(perfil["delimiter"])}, '
            f'Columns = {columnas}, Encoding = {perfil["encoding"]}, '
            'QuoteStyle = QuoteStyle.Csv]),')
        anterior = "Origen"
    elif perfil["format"] == "xlsx":
        pasos.append(
            f'    Origen = Excel.Workbook(File.Contents({ruta}), null, true),')
        pasos.append(
            f'    #"Navegación" = Origen{{[Item = {_literal_m(perfil["sheet"])}, '
            'Kind = "Sheet"]}[Data],')
        anterior = '#"Navegación"'
    elif perfil["format"] == "html":
        # Web.Page no toma codificacion: hay que decodificar ANTES con
        # Text.FromBinary y darle texto, no bytes. Es la diferencia entre
        # 'Cimentación' y 'CimentaciÃ³n' con signos de interrogación.
        pasos.append(
            f'    Origen = Web.Page(Text.FromBinary(File.Contents({ruta}), '
            f'{perfil["encoding"]})),')
        if perfil.get("table_id"):
            pasos.append(
                f'    #"Tabla" = Origen{{[Id = {_literal_m(perfil["table_id"])}]}}[Data],')
        else:
            # Sin id, se selecciona por POSICION: el mismo orden en que el
            # extractor de Python encontro las <table> (ver `_elegir_tabla`
            # y el aviso que ya viaja en `perfil["warnings"]`).
            pasos.append(f'    #"Tabla" = Origen{{{perfil["table_index"]}}}[Data],')
        anterior = '#"Tabla"'
        if perfil.get("promote_headers"):
            pasos.append(f'    #"Fila de titulo quitada" = Table.Skip({anterior}, 1),')
            anterior = '#"Fila de titulo quitada"'
        # Web.Page NO nombra las columnas "Column1", "Column2"... como Csv o
        # Excel.Workbook sin encabezados: cuando la tabla no trae <th> les
        # pone un nombre propio deducido de lo que ve en cada columna, y ese
        # nombre no es predecible desde Python. Fijarlos aqui por POSICION
        # (Table.FromRows + Table.ToRows) es lo que hace que
        # Table.TransformColumnTypes, mas abajo, encuentre columnas que de
        # verdad existen.
        nombres_fijos = "{" + ", ".join(
            _literal_m(c["name"]) for c in perfil["columns"]) + "}"
        pasos.append(
            f'    #"Encabezados fijados" = Table.FromRows(Table.ToRows('
            f'{anterior}), {nombres_fijos}),')
        anterior = '#"Encabezados fijados"'
    else:
        pasos.append(f'    Origen = Json.Document(File.Contents({ruta})),')
        pasos.append('    #"Convertida en tabla" = Table.FromRecords(Origen),')
        anterior = '#"Convertida en tabla"'

    if perfil["format"] == "html":
        pass  # los nombres ya quedaron fijados arriba: promover aqui los duplicaria
    elif perfil["format"] != "json":
        pasos.append(f'    #"Encabezados promovidos" = Table.PromoteHeaders('
                     f'{anterior}, [PromoteAllScalars = true]),')
        anterior = '#"Encabezados promovidos"'

    # La cultura va SIEMPRE explicita, como tercer argumento. Es la diferencia
    # entre un numero correcto y uno multiplicado por cien sin previo aviso.
    pasos.append(f'    #"Tipo cambiado" = Table.TransformColumnTypes('
                 f'{anterior}, {tipos}, {_literal_m(cultura)})')

    return "let\n" + "\n".join(pasos) + '\nin\n    #"Tipo cambiado"'


# ---------------------------------------------------------------------------
# Escritura en el proyecto
# ---------------------------------------------------------------------------

def _tmdl_nombre(nombre: str) -> str:
    from horizun_pbi_mcp.utils.validation import tmdl_quote_name

    return tmdl_quote_name(nombre)


def construir_tmdl(nombre: str, perfil: Dict[str, Any],
                   culture: Optional[str] = None,
                   description: Optional[str] = None) -> str:
    """TMDL de la tabla: propiedades, columnas y particion, en ese orden.

    El orden no es estetico: una propiedad de la tabla colocada despues de sus
    hijos hace que Power BI aborte la carga con un error de sangria.
    """
    lineas: List[str] = []
    if description:
        lineas += [f"/// {l.strip()}" for l in str(description).splitlines()]
    lineas.append(f"table {_tmdl_nombre(nombre)}")
    lineas.append(f"\tlineageTag: {uuid.uuid4()}")
    lineas.append("")

    for columna in perfil["columns"]:
        tipo = columna["data_type"]
        lineas.append(f"\tcolumn {_tmdl_nombre(columna['name'])}")
        lineas.append(f"\t\tdataType: {tipo}")
        if tipo in ("int64", "double", "decimal"):
            lineas.append("\t\tformatString: " + ("0" if tipo == "int64" else "0.00"))
        elif tipo == "dateTime":
            lineas.append("\t\tformatString: Short Date")
        lineas.append(f"\t\tlineageTag: {uuid.uuid4()}")
        lineas.append("\t\tsummarizeBy: " +
                      ("sum" if tipo in ("int64", "double", "decimal") else "none"))
        lineas.append(f"\t\tsourceColumn: {columna['name']}")
        lineas.append("")

    lineas.append(f"\tpartition {_tmdl_nombre(nombre)} = m")
    lineas.append("\t\tmode: import")
    lineas.append("\t\tsource =")
    lineas += ["\t\t\t\t" + l for l in construir_m(perfil, culture).split("\n")]
    lineas.append("")
    if perfil["format"] == "xlsx":
        lineas.append("\tannotation PBI_NavigationStepName = Navegación")
        lineas.append("")
    lineas.append("\tannotation PBI_ResultType = Table")
    lineas.append("")
    return "\n".join(lineas)


def agregar_tabla(active: Any, path: Path | str, table_name: str = "",
                  sheet: Optional[str] = None, culture: Optional[str] = None,
                  description: Optional[str] = None, overwrite: bool = False,
                  dry_run: bool = False, muestra: int = 200,
                  table_id: Optional[str] = None) -> Dict[str, Any]:
    """Carga un archivo como tabla del modelo, y comprueba que el TMDL abre.

    `table_id`: solo para HTML/'.xls' que en realidad es HTML. El `id` de la
    `<table>` a leer, cuando el archivo trae varias. Sin el, se elige la mas
    grande y se avisa (`perfil["warnings"]`).
    """
    from horizun_pbi_mcp.pbip.model_author import ModelAuthorError, resolver_destino_tabla
    from horizun_pbi_mcp.utils.validation import validate_object_name

    ruta = Path(path).expanduser()
    perfil = perfilar(ruta, sheet=sheet, muestra=muestra, table_id=table_id)

    nombre = validate_object_name(table_name or ruta.stem, "tabla")
    try:
        destino = resolver_destino_tabla(active, nombre, overwrite)
    except ModelAuthorError as exc:
        raise TableFromFileError(
            exc.message, details=exc.details) from exc

    texto = construir_tmdl(nombre, perfil, culture, description)
    resumen = {
        "table": nombre, "source": str(ruta), "format": perfil["format"],
        "sheet": perfil["sheet"], "culture": culture or perfil["culture"],
        "decimal_separator": perfil["decimal_separator"],
        "columns": perfil["columns"], "column_count": len(perfil["columns"]),
        "rows_sampled": perfil["row_sample"],
    }
    if perfil["format"] == "html":
        resumen["table_id"] = perfil.get("table_id")
        resumen["table_index"] = perfil.get("table_index")
        resumen["promoted_headers"] = perfil.get("promote_headers")
        if perfil.get("warnings"):
            resumen["warnings"] = perfil["warnings"]
    if dry_run:
        return {**resumen, "dry_run": True, "tmdl": texto, "m": construir_m(perfil, culture)}

    from horizun_pbi_mcp.pbip.model_author import escribir_tabla_y_registrarla

    try:
        salida = escribir_tabla_y_registrarla(
            active, destino, texto.split("\n"), nombre,
            "pbi_add_table_from_file")
    except ModelAuthorError as exc:
        # La transaccion ya revirtio tabla + model.tmdl. Se conserva el codigo
        # de error propio de esta tool y se adjunta la causa precisa.
        raise TableFromFileError(
            "La tabla generada no pasa la validacion del modelo y no se dejo "
            "ningun cambio parcial en el proyecto.",
            details={"cause": exc.message, **exc.details,
                     "file": str(destino)}) from exc

    log.info("Tabla '%s' cargada desde %s (%s columnas, cultura %s)",
             nombre, ruta.name, len(perfil["columns"]), resumen["culture"])
    return {**resumen, "dry_run": False, "validated": True,
            **salida}
