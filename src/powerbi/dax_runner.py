"""Ejecucion de consultas DAX contra el modelo activo (en vivo)."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from config import Session, get_settings
from logging_config import get_logger
from powerbi.adomd_client import AdomdClient
from powerbi.errors import ValidationError
from services import dax_guard
from utils.validation import (validate_dax_query, validate_measure_expression,
                              validate_object_name)

log = get_logger("dax")


def run_dax(session: Session, query: str, max_rows: Optional[int] = None,
            max_bytes: Optional[int] = None, timeout_seconds: Optional[int] = None,
            export: bool = False) -> Dict[str, Any]:
    """Ejecuta DAX de SOLO LECTURA contra el modelo local activo.

    La consulta llega del cliente MCP, asi que pasa por `services.dax_guard`
    antes de tocar el motor: solo se admiten formas reconocidas (EVALUATE,
    DEFINE...EVALUATE y DMVs de $SYSTEM). Politica fail-closed, sin escape.

    Las DMVs internas del propio servidor (descubrimiento, validacion de
    medidas) no pasan por aqui: son consultas nuestras, no del cliente.
    """
    from utils.validation import (MAX_BYTES_PERMITIDO, MAX_ROWS_PERMITIDO,
                                  MAX_TIMEOUT_PERMITIDO, validate_limit)

    # Lo que llega del cliente se valida ANTES de exigir sesion o tocar el
    # motor: una peticion mal formada se rechaza por si misma, y asi el error
    # dice cual es el parametro malo en vez de "no hay modelo activo".
    max_rows = validate_limit(max_rows, "max_rows", MAX_ROWS_PERMITIDO)
    max_bytes = validate_limit(max_bytes, "max_bytes", MAX_BYTES_PERMITIDO)
    timeout_seconds = validate_limit(timeout_seconds, "timeout_seconds",
                                     MAX_TIMEOUT_PERMITIDO)
    query = validate_dax_query(query)

    model = session.require_active_model()
    settings = get_settings()
    limit = int(max_rows) if max_rows is not None else settings.max_rows
    tope_bytes = int(max_bytes) if max_bytes else _MAX_BYTES_DEFECTO
    classification = dax_guard.assert_read_only(query)

    with AdomdClient(model.connection_string, model.catalog,
                     command_timeout=timeout_seconds) as client:
        columns, rows, truncated, elapsed_ms = client.execute_reader(
            query, max_rows=limit, max_bytes=tope_bytes)
        truncation_reason = client.last_truncation_reason

    # Limite por TAMANO, ademas de por filas: mil filas anchas pueden ser
    # decenas de megas y reventar el contexto del cliente.
    tamano = _tamano_aproximado(rows)
    recortado_por_bytes = truncation_reason == "bytes"
    if tamano > tope_bytes:
        conservadas = _recortar_a_bytes(rows, tope_bytes)
        recortado_por_bytes = True
        rows = conservadas
        truncated = True

    result = {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": limit,
        "elapsed_ms": round(elapsed_ms, 1),
        "port": model.port,
        "query_form": classification.form,
        "column_types": _tipos_de(columns, rows),
        "stats": {
            "rows_returned": len(rows),
            "approx_bytes": _tamano_aproximado(rows),
            "max_bytes": tope_bytes,
            "truncated_by_rows": truncated and not recortado_por_bytes,
            "truncated_by_bytes": recortado_por_bytes,
            "timeout_seconds": timeout_seconds or settings.command_timeout,
        },
    }
    notas = []
    if truncated and not recortado_por_bytes:
        notas.append(f"Resultado truncado a {limit} filas. Aumenta max_rows o usa "
                     "TOPN/filtros para acotar la consulta.")
    if recortado_por_bytes:
        notas.append(f"Resultado recortado por tamano (tope {tope_bytes} bytes). "
                     "Proyecta menos columnas o filtra mas.")
    if notas:
        result["note"] = " ".join(notas)
        result["warnings"] = notas

    if export:
        result["output_path"] = _exportar(query, columns, rows, result["stats"])
    return result


#: Tope de tamano por defecto del resultado (bytes aproximados).
_MAX_BYTES_DEFECTO = 2_000_000


def _tamano_aproximado(rows: List[List[Any]]) -> int:
    """Bytes UTF-8 reales del array JSON de filas.

    `len(str(v))` subestimaba caracteres no ASCII (un emoji cuenta 1 ahi y 4
    bytes en UTF-8) y tampoco contaba comillas, escapes ni separadores.
    """
    return len(json.dumps(
        rows, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8"))


def _recortar_a_bytes(rows: List[List[Any]], tope: int) -> List[List[Any]]:
    # Dos bytes pertenecen siempre al array exterior: []. Cada fila se
    # serializa una sola vez para mantener coste lineal incluso con 1M filas.
    salida, acumulado = [], 2
    for fila in rows:
        coste = len(json.dumps(
            fila, ensure_ascii=False, separators=(",", ":"), default=str
        ).encode("utf-8"))
        if salida:
            coste += 1  # coma entre filas
        if acumulado + coste > tope:
            break
        salida.append(fila)
        acumulado += coste
    return salida


def _tipos_de(columns: List[str], rows: List[List[Any]]) -> List[Dict[str, str]]:
    """Tipo observado de cada columna, a partir de la primera fila no nula."""
    tipos = []
    for i, nombre in enumerate(columns):
        tipo = "unknown"
        for fila in rows:
            if i < len(fila) and fila[i] is not None:
                tipo = type(fila[i]).__name__
                break
        tipos.append({"name": nombre, "type": tipo})
    return tipos


def _exportar(query: str, columns: List[str], rows: List[List[Any]],
              stats: Dict[str, Any]) -> str:
    """Vuelca a outputs/ el resultado PERMITIDO, que puede estar truncado.

    Decia "el resultado completo", y es falso: `rows` ya paso por el limite de
    filas y por el de bytes. Quien abriera el JSON creyendo tener el resultado
    entero podia sacar conclusiones sobre datos que faltan —contar, sumar o dar
    por cerrada una lista—. El archivo declara ahora su propio truncamiento.
    """
    from utils.file_utils import atomic_write_text, timestamp
    from services import redaction

    truncado = bool(stats.get("truncated_by_rows") or stats.get("truncated_by_bytes"))
    # `timestamp()` solo tiene precision de segundos. Dos consultas
    # concurrentes escribian la misma ruta y la segunda reemplazaba la primera.
    destino = (get_settings().outputs_dir /
               f"dax_result_{timestamp()}_{uuid.uuid4().hex[:10]}.json")
    atomic_write_text(destino, json.dumps(
        {"complete": not truncado,
         "truncated": truncado,
         "note": ("ATENCION: resultado TRUNCADO por los limites de la consulta. "
                  "No es el conjunto completo: no lo uses para contar, sumar ni "
                  "dar por cerrada una lista." if truncado else
                  "Resultado completo: no se alcanzo ningun limite."),
         "columns": columns, "rows": rows, "stats": stats,
         # La consulta no se vuelca: lleva nombres del negocio y a veces
         # literales filtrados, y el archivo acaba adjuntado en tickets.
         "query": redaction.dax(query)},
        indent=2, ensure_ascii=False, default=str))
    return str(destino)


def _quote(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def _bracket(name: str) -> str:
    """Identificador DAX entre corchetes, escapando `]` por duplicado."""
    return "[" + name.replace("]", "]]") + "]"


def _validate_measure_specs(measures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Valida y normaliza el lote antes de consultar sesion o motor."""
    if not isinstance(measures, list) or not measures:
        raise ValidationError("No se recibieron medidas para validar.")

    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(measures):
        if not isinstance(raw, dict):
            raise ValidationError(
                f"measures[{index}] debe ser un objeto con name y dax.",
                details={"parameter": "measures", "index": index},
            )
        if "name" not in raw or "dax" not in raw:
            missing = [key for key in ("name", "dax") if key not in raw]
            raise ValidationError(
                f"measures[{index}] no incluye: {', '.join(missing)}.",
                details={"parameter": "measures", "index": index,
                         "missing": missing},
            )
        name = validate_object_name(raw["name"], "medida")
        expression = validate_measure_expression(raw["dax"])
        table = raw.get("table")
        if table is not None:
            table = validate_object_name(table, "tabla")
        folded = name.casefold()
        if folded in seen:
            raise ValidationError(
                f"La medida '{name}' esta repetida en el lote.",
                details={"parameter": "measures", "index": index,
                         "name": name},
            )
        seen.add(folded)
        normalized.append({"name": name, "dax": expression, "table": table})
    return normalized


def validate_measures(session: Session, measures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Valida DAX de medidas SIN modificar el modelo (dry-run).

    Usa DEFINE MEASURE + EVALUATE ROW contra el modelo activo. Las medidas pueden
    referenciarse entre si (se definen todas juntas). Devuelve por cada una si
    compila/evalua, un valor de muestra, y el error del motor si falla.

    `measures`: [{"name", "dax", "table"(opcional)}].
    """
    measures = _validate_measure_specs(measures)
    model = session.require_active_model()

    with AdomdClient(model.connection_string, model.catalog) as client:
        default_table = None
        # tabla por defecto para medidas sin tabla (cualquiera real sirve para DEFINE)
        if any(not m.get("table") for m in measures):
            try:
                _c, rows, _t, _e = client.execute_reader(
                    "SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES", max_rows=200)
                names = [r[0] for r in rows if r and r[0]
                         and not str(r[0]).startswith(("LocalDateTable_", "DateTableTemplate_"))]
                default_table = names[0] if names else (rows[0][0] if rows else "Table")
            except Exception:  # noqa: BLE001
                default_table = "Table"

        def tbl(m):
            return m.get("table") or default_table

        define_all = "DEFINE\n" + "\n".join(
            f"  MEASURE {_quote(tbl(m))}{_bracket(m['name'])} = {m['dax']}"
            for m in measures)

        def _eval(define_block: str, m) -> Any:
            dax = (define_block +
                   f"\nEVALUATE ROW(\"v\", {_quote(tbl(m))}{_bracket(m['name'])})")
            _c, rows, _t, _e = client.execute_reader(dax, max_rows=1)
            return rows[0][0] if rows and rows[0] else None

        # Fast path: si el bloque completo parsea, todas se pueden evaluar juntas.
        global_ok = True
        try:
            client.execute_reader(define_all + "\nEVALUATE ROW(\"p\", 1)", max_rows=1)
        except Exception:  # noqa: BLE001
            global_ok = False  # alguna medida rompe el bloque compartido

        results = []
        for m in measures:
            entry = {"name": m["name"], "valid": False, "value": None, "error": None}
            try:
                entry["value"] = _eval(define_all, m)
                entry["valid"] = True
            except Exception as exc:  # noqa: BLE001
                if not global_ok:
                    # El bloque compartido esta roto por OTRA medida: reintenta aislada
                    # (pierde referencias a otras medidas nuevas, pero aisla errores de sintaxis).
                    define_one = (f"DEFINE\n  MEASURE {_quote(tbl(m))}"
                                  f"{_bracket(m['name'])} = {m['dax']}")
                    try:
                        entry["value"] = _eval(define_one, m)
                        entry["valid"] = True
                    except Exception as exc2:  # noqa: BLE001
                        entry["error"] = getattr(exc2, "Message", None) or str(exc2)
                else:
                    entry["error"] = getattr(exc, "Message", None) or str(exc)
            results.append(entry)

    valid = sum(1 for r in results if r["valid"])
    return {"total": len(results), "valid": valid, "invalid": len(results) - valid,
            "results": results}


def test_connection(session: Session) -> Dict[str, Any]:
    """Valida la conexion al modelo activo con una consulta trivial."""
    model = session.require_active_model()
    with AdomdClient(model.connection_string, model.catalog) as client:
        _cols, rows, _trunc, elapsed_ms = client.execute_reader(
            'EVALUATE ROW("ok", 1, "engine", "live")', max_rows=1
        )
    return {
        "connected": True,
        "port": model.port,
        "catalog": model.catalog,
        "model_name": model.model_name,
        "elapsed_ms": round(elapsed_ms, 1),
        "probe_rows": rows,
    }
