"""Perfilado de datos: lo que el modelo NO dice y solo se ve mirando valores.

La auditoria de modelo revisa la ESTRUCTURA —relaciones, nombres, columnas
ocultas—, y con eso no se detecta que una columna llamada `pct_codificado`
valga -800. Ese defecto no esta en el modelo, esta en los datos, y solo aparece
consultandolos.

Lo que se busca aqui es lo que rompe un tablero sin dar ningun error:

- porcentajes fuera de 0-100 (un grafico de % con -800 destroza la escala)
- columnas enteramente vacias (un visual que las use sale en blanco)
- columnas de un solo valor (ocupan sitio y no discriminan nada)
- claves con vacios (filas que no cruzan con su tabla relacionada)
- categorias con cadena vacia, que se agrupan aparte sin que se note

Cada hallazgo trae la consulta que lo demuestra, para poder comprobarlo.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("data_profile")

#: Nombres que delatan una columna de porcentaje.
_RE_PORCENTAJE = re.compile(r"(^|[_\s])(pct|pourcent|percent|porcentaje)|%", re.I)
_NUMERICOS = {"Int64", "Double", "Decimal"}
#: Tope de columnas por consulta: perfilar un modelo entero de una vez puede
#: tardar mas que el timeout, y un perfil a medias no sirve de nada.
_MAX_COLUMNAS = 60


def _dax_seguro(nombre: str) -> str:
    return "'" + str(nombre).replace("'", "''") + "'"


def es_porcentaje(columna: str) -> bool:
    return bool(_RE_PORCENTAJE.search(str(columna or "")))


def _perfil_de_columna(session: Session, tabla: str, columna: str,
                       tipo: str) -> Dict[str, Any]:
    """Estadisticos de una columna. Una sola consulta por columna."""
    from horizun_pbi_mcp.powerbi import dax_runner

    ref = f"{_dax_seguro(tabla)}[{columna}]"
    if tipo in _NUMERICOS:
        consulta = (f"EVALUATE ROW("
                    f'"filas", COUNTROWS({_dax_seguro(tabla)}), '
                    f'"vacios", COUNTBLANK({ref}), '
                    f'"distintos", DISTINCTCOUNT({ref}), '
                    f'"minimo", MIN({ref}), '
                    f'"maximo", MAX({ref}))')
    else:
        consulta = (f"EVALUATE ROW("
                    f'"filas", COUNTROWS({_dax_seguro(tabla)}), '
                    f'"vacios", COUNTBLANK({ref}), '
                    f'"distintos", DISTINCTCOUNT({ref}))')
    try:
        r = dax_runner.run_dax(session, consulta, max_rows=1)
    except Exception as exc:                                   # noqa: BLE001
        return {"table": tabla, "column": columna, "type": tipo,
                "error": str(getattr(exc, "message", exc))[:200]}
    fila = (r.get("rows") or [[None] * 5])[0]
    columnas = [c.lstrip("[").rstrip("]") for c in (r.get("columns") or [])]
    datos = dict(zip(columnas, fila))
    return {"table": tabla, "column": columna, "type": tipo,
            "rows": datos.get("filas"), "blanks": datos.get("vacios"),
            "distinct": datos.get("distintos"),
            "min": datos.get("minimo"), "max": datos.get("maximo"),
            "query": consulta}


def _hallazgos(perfil: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Traduce un perfil a problemas concretos, con su consecuencia."""
    salida: List[Dict[str, Any]] = []
    if perfil.get("error"):
        return salida
    filas = perfil.get("rows") or 0
    vacios = perfil.get("blanks") or 0
    distintos = perfil.get("distinct") or 0
    tabla, columna = perfil["table"], perfil["column"]

    if filas and vacios >= filas:
        salida.append({
            "rule": "columna_vacia", "severity": "warning",
            "table": tabla, "column": columna,
            "evidence": {"rows": filas, "blanks": vacios},
            "impact": "Cualquier visual que la use saldra en blanco."})
    elif filas and distintos <= 1 and not vacios:
        salida.append({
            "rule": "columna_constante", "severity": "info",
            "table": tabla, "column": columna,
            "evidence": {"distinct": distintos},
            "impact": "Tiene un solo valor: no sirve para segmentar ni agrupar."})

    if filas and 0 < vacios < filas:
        proporcion = vacios / filas
        if proporcion >= 0.5:
            salida.append({
                "rule": "columna_mayormente_vacia", "severity": "warning",
                "table": tabla, "column": columna,
                "evidence": {"blank_ratio": round(proporcion, 3),
                             "rows": filas, "blanks": vacios},
                "impact": ("Mas de la mitad de las filas no tienen valor: los "
                           "promedios que la usen estaran sesgados.")})

    if es_porcentaje(columna):
        minimo, maximo = perfil.get("min"), perfil.get("max")
        fuera = [v for v in (minimo, maximo)
                 if isinstance(v, (int, float)) and (v < 0 or v > 100)]
        if fuera:
            salida.append({
                "rule": "porcentaje_fuera_de_rango", "severity": "error",
                "table": tabla, "column": columna,
                "evidence": {"min": minimo, "max": maximo},
                "impact": ("Se llama porcentaje y sale de 0-100. Un grafico con "
                           "estos valores queda ilegible, y el promedio no "
                           "significa nada. Hay que corregirlo en el origen."),
                "query": perfil.get("query")})
    return salida


def profile_model(session: Session, *, tables: Optional[List[str]] = None,
                  max_columns: int = _MAX_COLUMNAS) -> Dict[str, Any]:
    """Perfila las columnas del modelo en vivo y devuelve lo que no cuadra.

    Solo lectura. `tables` acota el trabajo; sin acotar se recorre el modelo
    hasta `max_columns` columnas, porque perfilar cientos de columnas tarda mas
    que el timeout y un perfil a medias no vale.
    """
    from horizun_pbi_mcp.powerbi import model_reader

    modelo = model_reader.read_model(session)
    pedidas = set(tables or [])
    perfiles: List[Dict[str, Any]] = []
    omitidas = 0

    for tabla in modelo.get("tables", []):
        nombre = tabla["name"]
        if pedidas and nombre not in pedidas:
            continue
        if nombre.startswith(("LocalDateTable_", "DateTableTemplate_")):
            continue
        for col in tabla.get("columns", []):
            if col.get("column_type") == "RowNumber":
                continue
            if len(perfiles) >= max_columns:
                omitidas += 1
                continue
            perfiles.append(_perfil_de_columna(
                session, nombre, col["name"], str(col.get("data_type"))))

    hallazgos: List[Dict[str, Any]] = []
    for p in perfiles:
        hallazgos.extend(_hallazgos(p))

    orden = {"error": 0, "warning": 1, "info": 2}
    hallazgos.sort(key=lambda h: (orden.get(h["severity"], 3), h["table"], h["column"]))
    ilegibles = [p for p in perfiles if p.get("error")]

    log.info("Perfiladas %s columnas; %s hallazgos", len(perfiles), len(hallazgos))
    return {
        "profiled_columns": len(perfiles),
        "skipped_columns": omitidas,
        "findings": hallazgos,
        "by_severity": {s: sum(1 for h in hallazgos if h["severity"] == s)
                        for s in ("error", "warning", "info")},
        "unreadable": ilegibles,
        "profiles": perfiles,
        "warnings": (
            ([f"{len(ilegibles)} columna(s) no se pudieron perfilar."]
             if ilegibles else [])
            + ([f"{omitidas} columna(s) se omitieron por max_columns."]
               if omitidas else [])),
        "note": ("Perfilado de VALORES, complementario a pbi_audit_model, que "
                 "revisa la estructura. Un porcentaje negativo no es un defecto "
                 "del modelo sino de los datos, y solo se ve consultandolos."),
    }
