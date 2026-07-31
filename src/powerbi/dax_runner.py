"""Ejecucion de consultas DAX contra el modelo activo (en vivo)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import Session, get_settings
from logging_config import get_logger
from powerbi.adomd_client import AdomdClient
from powerbi.errors import ValidationError
from services import dax_guard
from utils.validation import validate_dax_query

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
    classification = dax_guard.assert_read_only(query)

    with AdomdClient(model.connection_string, model.catalog,
                     command_timeout=timeout_seconds) as client:
        columns, rows, truncated, elapsed_ms = client.execute_reader(query, max_rows=limit)

    # Limite por TAMANO, ademas de por filas: mil filas anchas pueden ser
    # decenas de megas y reventar el contexto del cliente.
    tope_bytes = int(max_bytes) if max_bytes else _MAX_BYTES_DEFECTO
    tamano = _tamano_aproximado(rows)
    recortado_por_bytes = False
    if tamano > tope_bytes:
        conservadas = _recortar_a_bytes(rows, tope_bytes)
        recortado_por_bytes = True
        rows_completas, rows = rows, conservadas
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
    total = 0
    for fila in rows:
        for v in fila:
            total += len(str(v)) if v is not None else 4
    return total


def _recortar_a_bytes(rows: List[List[Any]], tope: int) -> List[List[Any]]:
    salida, acumulado = [], 0
    for fila in rows:
        coste = sum(len(str(v)) if v is not None else 4 for v in fila)
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
    import json

    from utils.file_utils import atomic_write_text, timestamp
    from services import redaction

    truncado = bool(stats.get("truncated_by_rows") or stats.get("truncated_by_bytes"))
    destino = get_settings().outputs_dir / f"dax_result_{timestamp()}.json"
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


def validate_measures(session: Session, measures: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Valida DAX de medidas SIN modificar el modelo (dry-run).

    Usa DEFINE MEASURE + EVALUATE ROW contra el modelo activo. Las medidas pueden
    referenciarse entre si (se definen todas juntas). Devuelve por cada una si
    compila/evalua, un valor de muestra, y el error del motor si falla.

    `measures`: [{"name", "dax", "table"(opcional)}].
    """
    model = session.require_active_model()
    if not measures:
        raise ValidationError("No se recibieron medidas para validar.")

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
            f"  MEASURE {_quote(tbl(m))}[{m['name']}] = {m['dax']}" for m in measures)

        def _eval(define_block: str, m) -> Any:
            dax = define_block + f"\nEVALUATE ROW(\"v\", {_quote(tbl(m))}[{m['name']}])"
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
                    define_one = f"DEFINE\n  MEASURE {_quote(tbl(m))}[{m['name']}] = {m['dax']}"
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
