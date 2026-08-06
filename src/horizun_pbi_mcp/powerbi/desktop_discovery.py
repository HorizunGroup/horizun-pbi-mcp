"""Descubrimiento de modelos de Power BI Desktop abiertos localmente.

Con Power BI Desktop abierto se levanta un motor de Analysis Services
(`msmdsrv.exe`) escuchando en `localhost:<puerto>` (puerto dinamico en cada
arranque). Este modulo lo detecta de dos formas complementarias:

1. Procesos `msmdsrv` + sus puertos TCP en escucha (psutil).
2. Archivos `msmdsrv.port.txt` de los workspaces de Power BI Desktop (respaldo).

Luego se conecta a cada puerto para leer catalogo, nombre de modelo y nº de tablas.
"""
from __future__ import annotations

import hashlib
import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import ActiveModel, Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.adomd_client import AdomdClient
from horizun_pbi_mcp.powerbi.errors import (ModelDiscoveryError,
                                            PowerBIMCPError, ValidationError)

log = get_logger("discovery")

_SYS_TABLE_PREFIXES = ("LocalDateTable_", "DateTableTemplate_")

#: Espera maxima para comprobar por TCP si un puerto sigue vivo. Es corta a
#: proposito: los archivos msmdsrv.port.txt sobreviven al cierre de Desktop y
#: sin este filtro se intentaba una conexion ADOMD completa contra cada puerto
#: muerto, con su timeout largo.
_TCP_PROBE_TIMEOUT = 0.5
_LOCAL_HOST_ALIASES = {"localhost", "127.0.0.1", "::1"}


class StaleSessionError(PowerBIMCPError):
    """La sesion persistida ya no corresponde al proceso/modelo que se guardo.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de la
    Fase 1A. `guard()` la serializa como cualquier error de dominio.
    """

    code = "stale_session"


def _msmdsrv_procs() -> List[Any]:
    import psutil

    procs = []
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info.get("name") or "").lower() == "msmdsrv.exe":
                procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return procs


def _msmdsrv_pids() -> List[int]:
    return [p.pid for p in _msmdsrv_procs()]


def _port_is_listening(host: str, port: int) -> bool:
    """Comprueba por TCP si algo escucha en el puerto. Solo lectura."""
    try:
        with socket.create_connection((host, port), timeout=_TCP_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _ports_from_processes() -> List[Dict[str, Any]]:
    """Devuelve [{host, port, pid, create_time}] de procesos msmdsrv en escucha."""
    import psutil

    procs = {p.pid: p for p in _msmdsrv_procs()}
    if not procs:
        return []
    found: Dict[int, Dict[str, Any]] = {}
    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.AccessDenied, PermissionError):  # pragma: no cover
        log.warning("Sin permiso para listar conexiones TCP; uso solo archivos de puerto.")
        return []
    for c in conns:
        if c.status != psutil.CONN_LISTEN or c.pid not in procs or not c.laddr:
            continue
        host = c.laddr.ip
        port = c.laddr.port
        if port not in found or host == "127.0.0.1":
            try:
                created = procs[c.pid].create_time()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                created = None
            found[port] = {"host": "localhost", "port": port, "pid": c.pid,
                           "create_time": created, "source": "process"}
    return list(found.values())


def _workspace_port_files() -> List[Dict[str, Any]]:
    """Respaldo: lee los puertos desde los workspaces de Power BI Desktop."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    bases = [
        Path(local) / "Microsoft" / "Power BI Desktop" / "AnalysisServicesWorkspaces",
        Path(local) / "Microsoft" / "Power BI Desktop Store App" / "AnalysisServicesWorkspaces",
    ]
    out: List[Dict[str, Any]] = []
    for base in bases:
        if not base.exists():
            continue
        for port_file in base.glob("*/Data/msmdsrv.port.txt"):
            port = None
            for enc in ("utf-16", "utf-8-sig", "utf-8"):
                try:
                    raw = port_file.read_text(encoding=enc).strip()
                except (OSError, ValueError, UnicodeError):
                    continue
                # Una secuencia UTF-8 de longitud par puede ser tambien UTF-16
                # valido pero producir caracteres basura. Solo se acepta una
                # codificacion cuando el contenido decodificado ES un puerto.
                try:
                    candidate = int(raw)
                except ValueError:
                    continue
                if 1 <= candidate <= 65535:
                    port = candidate
                    break
            if port is None:
                continue
            out.append({"host": "localhost", "port": port, "pid": None,
                        "create_time": None, "source": "port_file",
                        "workspace": str(port_file.parent.parent)})
    return out


def _enrich(host: str, port: int) -> Dict[str, Any]:
    """Conecta al puerto y agrega catalogo, modelo y nº de tablas."""
    conn_str = f"Data Source={host}:{port}"
    info: Dict[str, Any] = {
        "host": host,
        "port": port,
        "connection_string": conn_str,
        "catalog": None,
        "database_name": None,
        "model_name": None,
        "table_count": None,
        "status": "unreachable",
        "warnings": [],
    }
    try:
        with AdomdClient(conn_str) as client:
            _, rows, _, _ = client.execute_reader(
                "SELECT [CATALOG_NAME] FROM $SYSTEM.DBSCHEMA_CATALOGS"
            )
            if rows:
                info["catalog"] = rows[0][0]
                info["database_name"] = rows[0][0]
            if info["catalog"]:
                # Asignar el atributo no cambia la base de una conexion ya
                # abierta. Sin ChangeDatabase, las DMVs siguientes dependian
                # del catalogo por defecto del proveedor.
                client.change_database(info["catalog"])
            # nombre de modelo (best-effort)
            try:
                _, mrows, _, _ = client.execute_reader(
                    "SELECT [Name] FROM $SYSTEM.TMSCHEMA_MODEL"
                )
                if mrows:
                    info["model_name"] = mrows[0][0]
            except Exception as exc:  # noqa: BLE001
                info["warnings"].append(f"No se pudo leer TMSCHEMA_MODEL: {exc}")
            # conteo de tablas de usuario
            try:
                _, trows, _, _ = client.execute_reader(
                    "SELECT [Name] FROM $SYSTEM.TMSCHEMA_TABLES"
                )
                names = [r[0] for r in trows if r and r[0]]
                user = [n for n in names if not n.startswith(_SYS_TABLE_PREFIXES)]
                info["table_count"] = len(user)
                # Dos informes abiertos se llaman los dos "Model" y solo se
                # distinguian por el numero de tablas: habia que seleccionar
                # cada uno y pedirle un resumen para saber cual era cual. Los
                # nombres ya se acaban de leer aqui; tirarlos era el problema.
                info["tables_sample"] = sorted(user)[:5]
            except Exception as exc:  # noqa: BLE001
                info["warnings"].append(f"No se pudo contar tablas: {exc}")
            info["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        # Las excepciones .NET exponen .Message (PascalCase); nuestras de dominio, .message.
        msg = getattr(exc, "Message", None) or getattr(exc, "message", None) or str(exc)
        info["warnings"].append(msg)
    return info


def discover_instances() -> List[Dict[str, Any]]:
    """Descubre todas las instancias locales de Power BI Desktop."""
    candidates: Dict[int, Dict[str, Any]] = {}
    for item in _ports_from_processes():
        candidates[item["port"]] = item

    workspaces: Dict[int, List[Dict[str, Any]]] = {}
    for item in _workspace_port_files():
        workspaces.setdefault(item["port"], []).append(item)

    for port, items in workspaces.items():
        existing = candidates.get(port)
        if existing is None:
            # Varios archivos obsoletos pueden conservar el mismo puerto. Se
            # crea un solo candidato sin inventar a que workspace pertenece.
            candidates[port] = dict(items[0])
            if len(items) > 1:
                candidates[port].pop("workspace", None)
                candidates[port]["workspace_ambiguous"] = len(items)
        elif len(items) == 1 and items[0].get("workspace"):
            # El proceso aporta PID/hora de arranque y el archivo aporta el
            # workspace. Son piezas complementarias de una misma identidad;
            # `setdefault` descartaba la segunda y debilitaba la verificacion.
            existing["workspace"] = items[0]["workspace"]
        elif len(items) > 1:
            # Elegir "el ultimo" hacia depender la identidad del orden de
            # glob y podia asociar un proceso actual con un workspace viejo.
            existing["workspace_ambiguous"] = len(items)

    if not candidates:
        return []

    results = []
    for port, meta in sorted(candidates.items()):
        host = meta.get("host", "localhost")
        # Pre-chequeo TCP: si nadie escucha, no se intenta ADOMD. Evita esperar
        # el timeout completo por cada archivo de puerto huerfano.
        if not _port_is_listening(host, port):
            enriched: Dict[str, Any] = {
                "host": host, "port": port,
                "connection_string": f"Data Source={host}:{port}",
                "catalog": None, "database_name": None, "model_name": None,
                "table_count": None, "tables_sample": [], "status": "unreachable",
                "warnings": ["Nadie escucha en el puerto: probablemente sea el "
                             "archivo de puerto de una sesion ya cerrada."],
            }
        else:
            enriched = _enrich(host, port)
        enriched["pid"] = meta.get("pid")
        enriched["create_time"] = meta.get("create_time")
        enriched["source"] = meta.get("source")
        if meta.get("workspace"):
            enriched["workspace"] = meta["workspace"]
        if meta.get("workspace_ambiguous"):
            enriched.setdefault("warnings", []).append(
                f"Hay {meta['workspace_ambiguous']} archivos de workspace "
                f"para el puerto {port}; no se asigno uno arbitrariamente."
            )
        if enriched.get("status") == "ok":
            enriched["session_fingerprint"] = session_fingerprint(enriched)
        results.append(enriched)
    return results


def session_fingerprint(instance: Dict[str, Any]) -> str:
    """Huella estable de una sesion concreta del motor.

    Combina puerto, pid, hora de creacion, workspace y catalogo. Si el puerto
    se reutiliza por otro proceso, o el mismo proceso/workspace carga otro
    modelo, la huella cambia: por eso sirve para detectar una sesion reutilizada.
    """
    workspace = instance.get("workspace")
    workspace_id = (os.path.normcase(os.path.normpath(str(workspace)))
                    if workspace else None)
    parts = [
        str(instance.get("port")),
        str(instance.get("pid")),
        str(instance.get("create_time")),
        str(workspace_id),
        str(instance.get("catalog")),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _connection_target(connection_string: str) -> Optional[tuple[str, int]]:
    """Extrae host/puerto de Data Source sin exponer el resto de la cadena."""
    if not isinstance(connection_string, str):
        return None
    value = None
    for part in connection_string.split(";"):
        if "=" not in part:
            continue
        key, candidate = part.split("=", 1)
        if key.strip().casefold() in {"data source", "server"}:
            value = candidate.strip().strip('"\'')
            break
    if not value:
        return None
    # ADOMD local usa host:port; se admite tambien host,port.
    separator = ":" if ":" in value else ","
    try:
        host, raw_port = value.rsplit(separator, 1)
        return host.strip("[]").casefold(), int(raw_port)
    except (TypeError, ValueError):
        return None


def verify_model(model: ActiveModel) -> Dict[str, Any]:
    """Comprueba si la sesion persistida sigue siendo LA MISMA.

    Que el puerto vuelva a estar abierto no basta: Power BI Desktop reasigna
    puertos en cada arranque y otro proceso puede haberlo tomado. Se comparan
    pid, hora de creacion del proceso, catalogo y huella de sesion.

    Estados: `ok`, `stale` (ya no existe) y `mismatch` (existe, pero es otra).
    """
    instances = discover_instances()
    same_port = [i for i in instances if i["port"] == model.port]

    if not same_port:
        return {"status": "stale", "reason": (
            f"Ya no hay ninguna instancia escuchando en el puerto {model.port}. "
            "Power BI Desktop asigna un puerto nuevo en cada arranque.")}

    inst = same_port[0]
    if inst.get("status") != "ok":
        return {"status": "stale", "reason": (
            f"El puerto {model.port} no responde."), "instance": inst}

    target = _connection_target(model.connection_string)
    expected_host = str(inst.get("host") or model.host).casefold()
    if target is None:
        return {"status": "mismatch", "reason": (
            "La connection string guardada no identifica un host/puerto local "
            "valido."), "instance": inst}
    target_host, target_port = target
    hosts_match = (target_host == expected_host or
                   {target_host, expected_host}.issubset(_LOCAL_HOST_ALIASES))
    if target_port != int(model.port) or not hosts_match:
        return {"status": "mismatch", "reason": (
            "La connection string guardada apunta a otro host o puerto que la "
            "identidad activa."), "instance": inst}

    recorded_pid = getattr(model, "pid", None)
    if recorded_pid is not None and inst.get("pid") is not None \
            and int(recorded_pid) != int(inst["pid"]):
        return {"status": "mismatch", "reason": (
            f"El puerto {model.port} lo ocupa ahora el proceso {inst['pid']}, no "
            f"el {recorded_pid} de la sesion guardada."), "instance": inst}

    recorded_started = getattr(model, "process_started", None)
    if recorded_started is not None and inst.get("create_time") is not None \
            and abs(float(recorded_started) - float(inst["create_time"])) > 1.0:
        return {"status": "mismatch", "reason": (
            "El proceso del puerto tiene otra hora de arranque: el identificador "
            "de proceso se reutilizo."), "instance": inst}

    recorded_workspace = getattr(model, "workspace", None)
    current_workspace = inst.get("workspace")
    if recorded_workspace is not None:
        recorded_workspace = os.path.normcase(os.path.normpath(
            str(recorded_workspace)))
        current_workspace = (os.path.normcase(os.path.normpath(
            str(current_workspace))) if current_workspace else None)
        if recorded_workspace != current_workspace:
            return {"status": "mismatch", "reason": (
                "El puerto pertenece ahora a otro workspace de Power BI "
                "Desktop."), "instance": inst}

    if model.catalog and inst.get("catalog") and model.catalog != inst["catalog"]:
        return {"status": "mismatch", "reason": (
            f"El puerto {model.port} sirve ahora el catalogo '{inst['catalog']}' y "
            f"la sesion guardada apuntaba a '{model.catalog}'."), "instance": inst}

    recorded_fp = getattr(model, "session_fingerprint", None)
    if recorded_fp and inst.get("session_fingerprint") \
            and recorded_fp != inst["session_fingerprint"]:
        return {"status": "mismatch", "reason": (
            "La huella de sesion no coincide: es otra sesion del motor."),
            "instance": inst}

    return {"status": "ok", "instance": inst}


_PUERTO_EN_CADENA = re.compile(r":(\d{1,5})\s*;?\s*$")


def puerto_de_connection_string(valor: str) -> int:
    """Extrae el puerto de lo que devuelve `pbi_list_desktop_models`.

    Acepta la forma exacta que publica el listado -`Data Source=localhost:56057`-
    y tambien `localhost:56057` a secas. Existe porque el campo se ofrecia en la
    salida de una tool y la siguiente no lo aceptaba: habia que leerlo, extraer
    el puerto a mano y pasarlo por otro parametro. Un valor que sale de aqui
    tiene que poder entrar ahi.
    """
    texto = str(valor or "").strip()
    if not texto:
        raise ValidationError("connection_string no puede estar vacio.",
                              details={"parameter": "connection_string"})
    coincidencia = _PUERTO_EN_CADENA.search(texto.split("=")[-1].strip())
    if not coincidencia:
        raise ValidationError(
            "No se pudo leer el puerto de connection_string. Se espera algo "
            "como 'Data Source=localhost:56057' -tal cual lo devuelve "
            "pbi_list_desktop_models- o 'localhost:56057'.",
            details={"parameter": "connection_string", "value": texto[:120]})
    puerto = int(coincidencia.group(1))
    if not 1 <= puerto <= 65_535:
        raise ValidationError(
            f"El puerto {puerto} de connection_string esta fuera de rango.",
            details={"parameter": "connection_string"})
    return puerto


def select_model(
    session: Session, port: Optional[int] = None, catalog: Optional[str] = None,
    connection_string: Optional[str] = None
) -> ActiveModel:
    """Selecciona un modelo como activo para la sesion.

    - Si `port` se indica, usa ese.
    - Si se indica `connection_string`, se extrae el puerto de ahi.
    - Si no y hay exactamente una instancia, la usa.
    - Si hay varias y no se indica puerto, lanza error con la lista.
    """
    if connection_string is not None:
        derivado = puerto_de_connection_string(connection_string)
        if port is not None and int(port) != derivado:
            raise ValidationError(
                f"port={port} y connection_string apuntan a puertos distintos "
                f"({port} y {derivado}). Indica solo uno.",
                details={"port": port, "connection_string_port": derivado})
        port = derivado
    instances = discover_instances()
    if not instances:
        raise ModelDiscoveryError(
            "No se detecto ningun modelo de Power BI Desktop abierto. Abre tu "
            "informe en Power BI Desktop y vuelve a intentar."
        )

    chosen: Optional[Dict[str, Any]] = None
    if port is not None:
        chosen = next((i for i in instances if i["port"] == int(port)), None)
        if chosen is None:
            raise ModelDiscoveryError(
                f"No hay ninguna instancia local en el puerto {port}.",
                details={"available": [i["port"] for i in instances]},
            )
    elif len(instances) == 1:
        chosen = instances[0]
    else:
        # El mensaje anterior remitia a la tool que se acababa de llamar, sin
        # decir QUE parametro usar ni con que valor. Ahora lleva la llamada
        # exacta y los nombres de tabla, que es lo unico que distingue a dos
        # modelos que se llaman los dos "Model".
        opciones = [
            {"port": i["port"], "model_name": i.get("model_name"),
             "table_count": i.get("table_count"),
             "tables_sample": i.get("tables_sample") or [],
             "select_with": f"pbi_select_model(port={i['port']})"}
            for i in instances
        ]
        puertos = ", ".join(str(i["port"]) for i in instances)
        raise ModelDiscoveryError(
            f"Hay {len(instances)} modelos abiertos y ninguno esta indicado. "
            f"Llama pbi_select_model(port=...) con uno de estos puertos: "
            f"{puertos}. Tambien acepta connection_string tal cual lo devuelve "
            "pbi_list_desktop_models. En `instances` va una muestra de tablas "
            "de cada uno para saber cual es cual.",
            details={"instances": opciones,
                     "ports": [i["port"] for i in instances]},
        )

    if chosen["status"] != "ok":
        raise ModelDiscoveryError(
            f"La instancia en el puerto {chosen['port']} no responde.",
            details={"warnings": chosen.get("warnings")},
        )

    model = ActiveModel(
        host=chosen["host"],
        port=chosen["port"],
        connection_string=chosen["connection_string"],
        catalog=catalog or chosen.get("catalog"),
        database_name=chosen.get("database_name"),
        model_name=chosen.get("model_name"),
        # Identidad de la sesion: permite detectar despues que el puerto lo
        # ocupa otro proceso o que el mismo proceso cargo otro modelo.
        pid=chosen.get("pid"),
        process_started=chosen.get("create_time"),
        workspace=chosen.get("workspace"),
        session_fingerprint=chosen.get("session_fingerprint"),
    )
    session.set_active_model(model)
    log.info("Modelo activo: puerto %s catalogo %s", model.port, model.catalog)
    return model
