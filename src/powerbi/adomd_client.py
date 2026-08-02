"""Cliente ADOMD.NET de bajo nivel para el motor tabular local.

Encapsula AdomdConnection/AdomdCommand y convierte los tipos .NET del lector a
tipos Python serializables en JSON. Soporta consultas DAX (EVALUATE ...) y DMVs
(SELECT ... FROM $SYSTEM...).
"""
from __future__ import annotations

import json
import time
from typing import Any, List, Optional, Tuple

from config import get_settings
from logging_config import get_logger
from powerbi.clr_bootstrap import load_adomd
from powerbi.errors import ConnectionFailedError, DaxQueryError

log = get_logger("adomd")


def _with_connect_timeout(connection_string: str, seconds: int) -> str:
    """Anade 'Connect Timeout=<seconds>' a la cadena si no lo trae.

    Evita que la conexion se cuelgue indefinidamente al intentar un puerto muerto
    (p.ej. archivos de puerto obsoletos de un Desktop que crasheo).
    """
    # `Command Timeout`, una contrasena o cualquier valor que contenga la
    # palabra "timeout" no configura el tiempo de CONEXION. Comprobar la
    # subcadena completa hacia que un puerto muerto pudiera esperar el timeout
    # largo del proveedor.
    keys = {
        part.split("=", 1)[0].strip().casefold()
        for part in connection_string.split(";") if "=" in part
    }
    if keys.intersection({"connect timeout", "connection timeout"}):
        return connection_string
    sep = "" if connection_string.rstrip().endswith(";") else ";"
    return f"{connection_string}{sep}Connect Timeout={int(seconds)}"

# Tipos .NET cacheados para la conversion de valores.
_NET_TYPES_READY = False
_DateTime = None
_Decimal = None


def _ensure_net_types() -> None:
    global _NET_TYPES_READY, _DateTime, _Decimal
    if _NET_TYPES_READY:
        return
    try:
        from System import DateTime, Decimal  # type: ignore

        _DateTime = DateTime
        _Decimal = Decimal
    except Exception:  # pragma: no cover
        _DateTime = None
        _Decimal = None
    _NET_TYPES_READY = True


def _convert(value: Any) -> Any:
    """Convierte un valor .NET a un tipo Python JSON-serializable."""
    if value is None:
        return None
    _ensure_net_types()
    # DateTime -> ISO 8601 (culture-invariant, evita separadores locales)
    if _DateTime is not None and isinstance(value, _DateTime):
        return value.ToString("o")
    # Decimal -> float via conversion invariante
    if _Decimal is not None and isinstance(value, _Decimal):
        try:
            return float(_Decimal.ToDouble(value))
        except Exception:  # pragma: no cover
            return float(str(value))
    # Tipos primitivos ya vienen convertidos por pythonnet (int/float/str/bool)
    if isinstance(value, (int, float, str, bool)):
        return value
    # Cualquier otra cosa (Guid, TimeSpan, etc.) -> str
    return str(value)


class AdomdClient:
    """Conexion ADOMD a un modelo tabular local. Uso como context manager."""

    def __init__(self, connection_string: str, catalog: Optional[str] = None,
                 connect_timeout: int = 10, command_timeout: Optional[int] = None):
        self.connection_string = _with_connect_timeout(connection_string, connect_timeout)
        self.catalog = catalog
        #: Timeout por comando; si es None se usa el de la configuracion.
        self.command_timeout = command_timeout
        self._conn = None
        self.last_truncation_reason: Optional[str] = None

    def __enter__(self) -> "AdomdClient":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        Adomd = load_adomd()
        try:
            self._conn = Adomd.AdomdConnection(self.connection_string)
            self._conn.Open()
            if self.catalog:
                self._conn.ChangeDatabase(self.catalog)
        except Exception as exc:  # noqa: BLE001
            from services import redaction

            msg = getattr(exc, "Message", None) or str(exc)
            # Si Open alcanzo a crear el canal y ChangeDatabase fallo,
            # __enter__ nunca termina y por tanto __exit__ no lo cerraria.
            self.close()
            raise ConnectionFailedError(
                f"No se pudo conectar al modelo local: {redaction.texto(msg)}",
                # Solo el destino (localhost:puerto). La connection string
                # completa lleva la ruta local del .pbix y, contra servicios
                # remotos, credenciales.
                details={"connection_string":
                         redaction.connection_string(self.connection_string)},
            ) from exc

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.Close()
            except Exception:  # pragma: no cover
                pass
            self._conn = None

    def change_database(self, catalog: str) -> None:
        """Cambia el catalogo de una conexion ya abierta, con error de dominio."""
        if self._conn is None:
            self.catalog = catalog
            self.open()
            return
        try:
            self._conn.ChangeDatabase(catalog)
            self.catalog = catalog
        except Exception as exc:  # noqa: BLE001
            from services import redaction

            msg = getattr(exc, "Message", None) or str(exc)
            raise ConnectionFailedError(
                f"No se pudo activar el catalogo solicitado: "
                f"{redaction.texto(msg)}",
                details={"catalog": catalog},
            ) from exc

    def execute_reader(
        self, query: str, max_rows: Optional[int] = None,
        max_bytes: Optional[int] = None,
    ) -> Tuple[List[str], List[List[Any]], bool, float]:
        """Ejecuta una consulta y devuelve (columnas, filas, truncado, ms).

        Lee en streaming y corta al llegar a `max_rows` (marcando truncado=True)
        para no cargar en memoria resultados enormes.
        """
        if self._conn is None:
            self.open()
        start = time.perf_counter()
        self.last_truncation_reason = None
        reader = None
        cmd = None
        try:
            cmd = self._conn.CreateCommand()
            cmd.CommandText = query
            try:
                cmd.CommandTimeout = int(self.command_timeout
                                         or get_settings().command_timeout)
            except Exception:  # pragma: no cover - propiedad opcional
                pass
            reader = cmd.ExecuteReader()
            field_count = reader.FieldCount
            columns = [reader.GetName(i) for i in range(field_count)]
            rows: List[List[Any]] = []
            truncated = False
            bytes_used = 2  # corchetes del array JSON exterior
            while reader.Read():
                if max_rows is not None and len(rows) >= max_rows:
                    truncated = True
                    self.last_truncation_reason = "rows"
                    break
                row = []
                for i in range(field_count):
                    if reader.IsDBNull(i):
                        row.append(None)
                    else:
                        row.append(_convert(reader.GetValue(i)))
                if max_bytes is not None:
                    row_bytes = len(json.dumps(
                        row, ensure_ascii=False, separators=(",", ":"),
                        default=str).encode("utf-8"))
                    cost = row_bytes + (1 if rows else 0)
                    if bytes_used + cost > max_bytes:
                        truncated = True
                        self.last_truncation_reason = "bytes"
                        break
                    bytes_used += cost
                rows.append(row)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return columns, rows, truncated, elapsed_ms
        except ConnectionFailedError:
            raise
        except Exception as exc:  # noqa: BLE001
            from services import redaction

            msg = getattr(exc, "Message", None) or str(exc)
            # El motor suele incrustar la consulta ENTERA en su mensaje; y la
            # consulta lleva nombres del negocio y a veces literales filtrados.
            raise DaxQueryError(
                redaction.texto(msg, query=query),
                details={"query": redaction.dax(query)},
            ) from exc
        finally:
            if reader is not None:
                try:
                    reader.Close()
                except Exception:  # pragma: no cover
                    pass
            if cmd is not None:
                try:
                    cmd.Dispose()
                except Exception:  # pragma: no cover - algunos dobles/API viejas
                    pass

    def execute_scalar(self, query: str) -> Any:
        cols, rows, _trunc, _ms = self.execute_reader(query, max_rows=1)
        if rows and rows[0]:
            return rows[0][0]
        return None
