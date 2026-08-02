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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("table_from_file")


class TableFromFileError(PowerBIMCPError):
    code = "table_from_file_error"


#: Extensiones que sabemos cargar. Lo demas se rechaza por su nombre, para que
#: el mensaje diga QUE formato es y no un generico "no soportado".
_FORMATOS = {".csv": "csv", ".txt": "csv", ".tsv": "csv",
             ".xlsx": "xlsx", ".xlsm": "xlsx", ".json": "json"}

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


def perfilar(path: Path | str, sheet: Optional[str] = None,
             muestra: int = 200) -> Dict[str, Any]:
    """Mira dentro del archivo y deduce columnas, tipos y cultura."""
    ruta = Path(path).expanduser()
    if not ruta.exists():
        raise TableFromFileError(f"El archivo no existe: {ruta}",
                                 details={"path": str(ruta)})
    formato = _FORMATOS.get(ruta.suffix.lower())
    if formato is None:
        raise TableFromFileError(
            f"No se sabe cargar un '{ruta.suffix}'. Formatos admitidos: "
            f"{', '.join(sorted(_FORMATOS))}.",
            details={"path": str(ruta), "suffix": ruta.suffix})

    if formato == "csv":
        perfil = _perfilar_csv(ruta, muestra)
    elif formato == "xlsx":
        perfil = _perfilar_xlsx(ruta, sheet, muestra)
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
    else:
        pasos.append(f'    Origen = Json.Document(File.Contents({ruta})),')
        pasos.append('    #"Convertida en tabla" = Table.FromRecords(Origen),')
        anterior = '#"Convertida en tabla"'

    if perfil["format"] != "json":
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
    from utils.validation import tmdl_quote_name

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
                  dry_run: bool = False, muestra: int = 200) -> Dict[str, Any]:
    """Carga un archivo como tabla del modelo, y comprueba que el TMDL abre."""
    from pbip.model_author import ModelAuthorError, resolver_destino_tabla
    from utils.validation import validate_object_name

    ruta = Path(path).expanduser()
    perfil = perfilar(ruta, sheet=sheet, muestra=muestra)

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
    if dry_run:
        return {**resumen, "dry_run": True, "tmdl": texto, "m": construir_m(perfil, culture)}

    from pbip.model_author import escribir_tabla_y_registrarla

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
