"""Lectura controlada de carpetas de SharePoint Online mediante Graph v1.0.

Autenticacion app-only (client credentials) con MSAL. Los secretos se leen del
entorno y nunca forman parte de una firma MCP, una respuesta o un log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from horizun_pbi_mcp import branding
from horizun_pbi_mcp.config import get_settings
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.services import paths as safe_paths
from horizun_pbi_mcp.services import redaction
from horizun_pbi_mcp.utils.validation import validate_limit

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_TOKEN_SCOPE = ["https://graph.microsoft.com/.default"]
_CREDENTIAL_RE = re.compile(r"^[A-Za-z0-9.-]{3,160}$")
_APPS: Dict[Tuple[str, str, str], Any] = {}
_APPS_LOCK = threading.Lock()

#: Graph limita por tasa en condiciones normales; rendirse al primer 429 es un
#: fallo nuestro, no del servicio. 5xx transitorios entran en el mismo grupo.
_ESTADOS_REINTENTABLES = frozenset({429, 502, 503, 504})
_MAX_INTENTOS = 4
#: Techo por espera y techo acumulado: un `Retry-After` remoto no puede dejar
#: la llamada dormida indefinidamente.
_MAX_ESPERA_UNICA = 30.0
_MAX_ESPERA_TOTAL = 60.0
#: Un `@odata.nextLink` no puede pasear al servidor sin fin. `max_items` solo
#: acota cuando las paginas TRAEN elementos: una cadena de paginas vacias no
#: ejecuta nunca el cuerpo del consumidor.
_MAX_PAGINAS = 200


class SharePointConfigError(PowerBIMCPError):
    code = "sharepoint_not_configured"


class SharePointRequestError(PowerBIMCPError):
    code = "sharepoint_request_failed"


class SharePointLimitError(PowerBIMCPError):
    code = "sharepoint_limit_exceeded"


def _credentials() -> Tuple[str, str, str]:
    values = {
        "SHAREPOINT_TENANT_ID": branding.env("SHAREPOINT_TENANT_ID"),
        "SHAREPOINT_CLIENT_ID": branding.env("SHAREPOINT_CLIENT_ID"),
        "SHAREPOINT_CLIENT_SECRET": branding.env("SHAREPOINT_CLIENT_SECRET"),
    }
    missing = [f"{branding.ENV_PREFIX}{name}" for name, value in values.items()
               if not value]
    if missing:
        raise SharePointConfigError(
            "SharePoint no esta configurado. Registra una aplicacion Entra ID "
            "y define las tres variables indicadas.",
            details={"missing_environment_variables": missing,
                     "authentication": "client_credentials",
                     "scope": "https://graph.microsoft.com/.default"})
    tenant = str(values["SHAREPOINT_TENANT_ID"])
    client = str(values["SHAREPOINT_CLIENT_ID"])
    secret = str(values["SHAREPOINT_CLIENT_SECRET"])
    if not _CREDENTIAL_RE.fullmatch(tenant) or not _CREDENTIAL_RE.fullmatch(client):
        raise SharePointConfigError(
            "Tenant ID o Client ID tienen un formato no valido.",
            details={"tenant_variable":
                     f"{branding.ENV_PREFIX}SHAREPOINT_TENANT_ID",
                     "client_variable":
                     f"{branding.ENV_PREFIX}SHAREPOINT_CLIENT_ID"})
    return tenant, client, secret


def _token() -> str:
    tenant, client, secret = _credentials()
    try:
        import msal
    except ImportError as exc:  # pragma: no cover - dependencia declarada
        raise SharePointConfigError(
            "Falta MSAL. Reinstala horizun-pbi-mcp con sus dependencias.") from exc

    secret_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    key = (tenant.casefold(), client.casefold(), secret_fingerprint)
    with _APPS_LOCK:
        app = _APPS.get(key)
        if app is None:
            authority = f"https://login.microsoftonline.com/{quote(tenant, safe='.-')}"
            app = msal.ConfidentialClientApplication(
                client, authority=authority, client_credential=secret)
            _APPS[key] = app
    result = app.acquire_token_for_client(scopes=_TOKEN_SCOPE)
    token = result.get("access_token") if isinstance(result, dict) else None
    if not token:
        error = result.get("error") if isinstance(result, dict) else "unknown"
        description = result.get("error_description") if isinstance(result, dict) else ""
        raise SharePointConfigError(
            "Microsoft Entra ID rechazo la autenticacion de SharePoint.",
            details={"error": error,
                     "error_description": redaction.texto(str(description))[:600],
                     "correlation_id": (result.get("correlation_id")
                                        if isinstance(result, dict) else None)})
    return str(token)


def _graph_url(path_or_url: str) -> str:
    if path_or_url.startswith("/"):
        return GRAPH_ROOT + path_or_url
    parsed = urlparse(path_or_url)
    if ((parsed.scheme.lower(), (parsed.hostname or "").casefold()) !=
            ("https", "graph.microsoft.com") or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment):
        raise SharePointRequestError(
            "Graph devolvio una URL de paginacion fuera de graph.microsoft.com.",
            details={"reason": "untrusted_next_link"})
    return path_or_url


def _error_details(exc: HTTPError) -> Dict[str, Any]:
    try:
        body = exc.read(65_536).decode("utf-8", errors="replace")
        parsed = json.loads(body)
        graph_error = parsed.get("error") if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 - el error remoto puede no ser JSON
        graph_error = None
    if isinstance(graph_error, dict):
        message = redaction.texto(str(graph_error.get("message") or ""))[:600]
        code = graph_error.get("code")
    else:
        message, code = "", None
    return {"http_status": exc.code, "graph_code": code,
            "graph_message": message}


def _espera_tras(exc: HTTPError, intento: int) -> float:
    """Cuanto esperar antes de reintentar: `Retry-After` manda, con techo.

    Graph envia `Retry-After` en segundos. Si viniera como fecha HTTP -o como
    cualquier cosa que no sea un numero- se ignora y se usa el backoff propio:
    es preferible esperar de menos que confiar en un valor remoto sin forma.
    """
    cabeceras = getattr(exc, "headers", None)
    crudo = cabeceras.get("Retry-After") if cabeceras is not None else None
    if crudo is not None:
        try:
            return max(0.0, min(float(str(crudo).strip()), _MAX_ESPERA_UNICA))
        except (TypeError, ValueError):
            pass
    return min(2.0 ** intento, 8.0)


def _graph_json(path_or_url: str, token: str, *, timeout: int = 30) -> Dict[str, Any]:
    url = _graph_url(path_or_url)
    request = Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "User-Agent": "Horizun-PBI-MCP/1",
    })
    dormido = 0.0
    for intento in range(_MAX_INTENTOS):
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL fijada/validada
                payload = response.read(16 * 1024 * 1024 + 1)
            break
        except HTTPError as exc:
            reintentable = (exc.code in _ESTADOS_REINTENTABLES
                            and intento < _MAX_INTENTOS - 1
                            and dormido < _MAX_ESPERA_TOTAL)
            if not reintentable:
                detalles = _error_details(exc)
                detalles["attempts"] = intento + 1
                raise SharePointRequestError(
                    "Microsoft Graph rechazo la solicitud de SharePoint.",
                    details=detalles) from exc
            espera = min(_espera_tras(exc, intento), _MAX_ESPERA_TOTAL - dormido)
            dormido += espera
            time.sleep(espera)
        except (URLError, OSError, TimeoutError) as exc:
            raise SharePointRequestError(
                "No se pudo conectar con Microsoft Graph.",
                details={"error_type": type(exc).__name__,
                         "message": redaction.texto(str(exc))[:400]}) from exc
    if len(payload) > 16 * 1024 * 1024:
        raise SharePointLimitError(
            "La respuesta de Graph supera el limite de 16 MB.")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SharePointRequestError(
            "Microsoft Graph devolvio una respuesta JSON ilegible.") from exc
    if not isinstance(parsed, dict):
        raise SharePointRequestError(
            "Microsoft Graph devolvio una respuesta con forma inesperada.")
    return parsed


def _site_request(site_url: str) -> Tuple[str, str, str]:
    parsed = urlparse(str(site_url).strip())
    host = (parsed.hostname or "").casefold()
    if (parsed.scheme.lower() != "https" or not host.endswith(".sharepoint.com")
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValidationError(
            "site_url debe ser una URL HTTPS de SharePoint Online, sin "
            "credenciales, query ni fragmento.")
    decoded_segments = [unquote(segment) for segment in parsed.path.split("/")
                        if segment]
    for segment in decoded_segments:
        safe_paths.safe_identifier(segment, kind="segmento de URL de SharePoint")
    segments = [quote(segment, safe="") for segment in decoded_segments]
    relative = "/" + "/".join(segments) if segments else "/"
    route = (f"/sites/{quote(host, safe='.-')}:{relative}"
             if segments else f"/sites/{quote(host, safe='.-')}")
    return route + "?$select=id,displayName,webUrl", host, parsed.path or "/"


def _site_and_drive(token: str, site_url: str,
                    library: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    site_route, _host, _path = _site_request(site_url)
    site = _graph_json(site_route, token)
    site_id = site.get("id")
    if not site_id:
        raise SharePointRequestError("Graph no devolvio el identificador del sitio.")
    drives = _graph_json(
        f"/sites/{quote(str(site_id), safe=',-')}/drives?"
        + urlencode({"$select": "id,name,webUrl,driveType"}), token).get("value") or []
    requested = str(library or "Documents").strip()
    if not requested:
        raise ValidationError("library no puede estar vacio.")
    matches = [drive for drive in drives if isinstance(drive, dict) and
               (str(drive.get("name", "")).casefold() == requested.casefold()
                or str(drive.get("id", "")) == requested)]
    if len(matches) != 1:
        raise SharePointRequestError(
            f"No se encontro una biblioteca unica llamada '{requested}'.",
            details={"available_libraries": [
                {"name": drive.get("name"), "id": drive.get("id")}
                for drive in drives if isinstance(drive, dict)]})
    return site, matches[0]


def _folder_segments(folder_path: str) -> List[str]:
    raw = str(folder_path or "").strip().strip("/")
    if not raw:
        return []
    if "\\" in raw or len(raw) > 1_024:
        raise ValidationError(
            "folder_path usa '/' entre carpetas y no puede superar 1024 caracteres.")
    parts = raw.split("/")
    for part in parts:
        safe_paths.safe_identifier(part, kind="segmento de SharePoint")
    return parts


def _children_route(drive_id: str, *, item_id: str = "",
                    folder_segments: Sequence[str] = ()) -> str:
    drive = quote(str(drive_id), safe="-_")
    if item_id:
        base = f"/drives/{drive}/items/{quote(str(item_id), safe='-_')}/children"
    elif folder_segments:
        remote = "/".join(quote(part, safe="") for part in folder_segments)
        base = f"/drives/{drive}/root:/{remote}:/children"
    else:
        base = f"/drives/{drive}/root/children"
    return base + "?" + urlencode({
        "$select": "id,name,size,file,folder,lastModifiedDateTime,webUrl,eTag,parentReference",
        "$top": "200", "$orderby": "name"})


def _public_item(item: Dict[str, Any], relative_path: str) -> Dict[str, Any]:
    is_folder = isinstance(item.get("folder"), dict)
    file_info = item.get("file") if isinstance(item.get("file"), dict) else {}
    return {
        "id": item.get("id"), "name": item.get("name"),
        "relative_path": relative_path, "kind": "folder" if is_folder else "file",
        "size": int(item.get("size") or 0),
        "mime_type": file_info.get("mimeType"),
        "modified_at": item.get("lastModifiedDateTime"),
        "web_url": item.get("webUrl"), "etag": item.get("eTag"),
        "child_count": ((item.get("folder") or {}).get("childCount")
                        if is_folder else None),
    }


def _paged_children(route: str, token: str) -> Iterable[Dict[str, Any]]:
    """Recorre la paginacion de Graph, con corte propio ante un ciclo.

    El limite de elementos del consumidor no basta: si Graph devolviera paginas
    vacias encadenadas -o un `nextLink` que se apunta a si mismo- el cuerpo del
    consumidor no llegaria a ejecutarse nunca y el servidor giraria sin fin.
    El corte tiene que vivir aqui, donde se sigue el enlace.
    """
    next_link: Optional[str] = route
    vistos: set[str] = set()
    paginas = 0
    while next_link:
        clave = next_link.split("#", 1)[0]
        if clave in vistos:
            raise SharePointRequestError(
                "Graph repitio un enlace de paginacion ya visitado; se corta "
                "para no recorrer un ciclo.",
                details={"reason": "pagination_cycle", "pages": paginas})
        vistos.add(clave)
        paginas += 1
        if paginas > _MAX_PAGINAS:
            raise SharePointRequestError(
                "La paginacion de Graph supero el maximo de paginas admitido.",
                details={"reason": "pagination_runaway",
                         "max_pages": _MAX_PAGINAS})
        response = _graph_json(next_link, token)
        for item in response.get("value") or []:
            if isinstance(item, dict):
                yield item
        value = response.get("@odata.nextLink")
        next_link = str(value) if value else None


def list_folder(site_url: str, *, library: str = "Documents",
                folder_path: str = "", recursive: bool = False,
                max_items: int = 500) -> Dict[str, Any]:
    """Lista una carpeta de SharePoint, con paginacion y limite total."""
    max_items = int(validate_limit(max_items, "max_items", 5_000) or 500)
    token = _token()
    site, drive = _site_and_drive(token, site_url, library)
    root_segments = _folder_segments(folder_path)
    queue: List[Tuple[str, List[str], str]] = [
        (_children_route(str(drive["id"]), folder_segments=root_segments),
         root_segments, "")]
    items: List[Dict[str, Any]] = []
    truncated = False
    while queue:
        route, remote_segments, parent_relative = queue.pop(0)
        for raw in _paged_children(route, token):
            name = safe_paths.safe_identifier(
                str(raw.get("name") or ""), kind="nombre remoto de SharePoint")
            relative = f"{parent_relative}/{name}".strip("/")
            items.append(_public_item(raw, relative))
            if len(items) > max_items:
                items.pop()
                truncated = True
                queue.clear()
                break
            if recursive and isinstance(raw.get("folder"), dict):
                queue.append((
                    _children_route(str(drive["id"]), item_id=str(raw.get("id"))),
                    [*remote_segments, name], relative))
        if truncated:
            break
    return {
        "site": {"id": site.get("id"), "name": site.get("displayName"),
                 "web_url": site.get("webUrl")},
        "library": {"id": drive.get("id"), "name": drive.get("name"),
                    "web_url": drive.get("webUrl")},
        "folder_path": "/".join(root_segments), "recursive": bool(recursive),
        "count": len(items), "items": items, "truncated": truncated,
        "max_items": max_items,
    }


def _normalizar_extensiones(extensions: Optional[Sequence[str]]) -> Optional[set[str]]:
    if extensions is None:
        return None
    result = set()
    for raw in extensions:
        value = str(raw).strip().lower()
        if value and not value.startswith("."):
            value = "." + value
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", value):
            raise ValidationError(f"Extension no valida: '{raw}'.")
        result.add(value)
    if not result:
        raise ValidationError("extensions no puede ser una lista vacia.")
    return result


def _download_url(token: str, drive_id: str, item_id: str) -> str:
    route = (f"/drives/{quote(drive_id, safe='-_')}/items/"
             f"{quote(item_id, safe='-_')}?" +
             urlencode({"$select": "id,name,size,@microsoft.graph.downloadUrl"}))
    metadata = _graph_json(route, token)
    value = metadata.get("@microsoft.graph.downloadUrl")
    if not value:
        raise SharePointRequestError(
            f"Graph no devolvio URL de descarga para '{metadata.get('name') or item_id}'.")
    parsed = urlparse(str(value))
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise SharePointRequestError(
            "Graph devolvio una URL de descarga no segura.",
            details={"reason": "invalid_download_url"})
    return str(value)


#: Margen sobre el tamano declarado por Graph. Cubre una desviacion honesta
#: (metadato ligeramente desfasado) sin dejar que un archivo "de 10 bytes" se
#: coma el presupuesto del lote entero.
_HOLGURA_TAMANO = 64 * 1024


def _download_one(url: str, target: Path, *, expected_size: int,
                  max_bytes: int) -> Dict[str, Any]:
    """Descarga en streaming, acotada por el limite del ARCHIVO y por el global.

    El limite efectivo es el menor de los dos. Antes se pasaba solo el global
    restante: un archivo que declaraba 10 bytes y transmitia gigabytes agotaba
    el presupuesto de todo el lote antes de que la comprobacion de tamano
    declarado -que ocurre al final del stream- llegara siquiera a ejecutarse.
    """
    request = Request(url, headers={"User-Agent": "Horizun-PBI-MCP/1"})
    temporary: Optional[Path] = None
    digest = hashlib.sha256()
    total = 0
    limite = max_bytes
    if expected_size >= 0:
        limite = min(max_bytes, expected_size + _HOLGURA_TAMANO)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, prefix=f".{target.name}.",
                suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            try:
                with urlopen(request, timeout=60) as response:  # noqa: S310 - URL emitida por Graph
                    while True:
                        # No se lee mas alla del limite: un origen hostil no
                        # puede forzar la lectura de un megabyte por vuelta.
                        pendiente = limite - total + 1
                        chunk = response.read(min(1024 * 1024, max(1, pendiente)))
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limite:
                            raise SharePointLimitError(
                                "La descarga supera el limite autorizado para "
                                "este archivo.",
                                details={"file": target.name,
                                         "declared_size": expected_size,
                                         "file_limit_bytes": limite,
                                         "batch_remaining_bytes": max_bytes,
                                         "reason": ("declared_size_exceeded"
                                                    if limite < max_bytes
                                                    else "batch_budget_exceeded")})
                        output.write(chunk)
                        digest.update(chunk)
            except HTTPError as exc:
                raise SharePointRequestError(
                    "La URL temporal de SharePoint rechazo la descarga.",
                    details={"http_status": exc.code, "file": target.name}) from exc
            except (URLError, OSError, TimeoutError) as exc:
                raise SharePointRequestError(
                    "La descarga de SharePoint no pudo completarse.",
                    details={"file": target.name,
                             "error_type": type(exc).__name__}) from exc
            output.flush()
            os.fsync(output.fileno())
        if expected_size >= 0 and total != expected_size:
            raise SharePointRequestError(
                "El tamano descargado no coincide con el informado por Graph.",
                details={"file": target.name, "expected": expected_size,
                         "actual": total})
        os.replace(temporary, target)
        temporary = None
        reread = _sha256_file(target)
        if reread != digest.hexdigest():
            target.unlink(missing_ok=True)
            raise SharePointRequestError(
                "El archivo descargado cambio al releerlo.",
                details={"file": target.name})
        return {"path": str(target), "bytes": total,
                "sha256": digest.hexdigest()}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    """Relee un archivo en streaming; nunca carga una descarga completa en RAM."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_folder(site_url: str, *, library: str = "Documents",
                    folder_path: str = "", extensions: Optional[Sequence[str]] = None,
                    recursive: bool = True, max_files: int = 200,
                    max_total_mb: int = 500) -> Dict[str, Any]:
    """Descarga una carpeta a outputs/sharepoint; publica todo o nada."""
    max_files = int(validate_limit(max_files, "max_files", 2_000) or 200)
    max_total_mb = int(validate_limit(max_total_mb, "max_total_mb", 4_096) or 500)
    max_total_bytes = max_total_mb * 1024 * 1024
    allowed = _normalizar_extensiones(extensions)
    root_segments = _folder_segments(folder_path)

    # Se inventaria primero: ningun byte se publica si el lote ya excede los
    # limites por numero o por tamano declarado.
    listing = list_folder(
        site_url, library=library, folder_path=folder_path,
        recursive=recursive, max_items=min(5_000, max_files * 10 + 100))
    if listing["truncated"]:
        raise SharePointLimitError(
            "La carpeta es demasiado grande para planificar una descarga completa.",
            details={"listed": listing["count"], "max_files": max_files})
    files = [item for item in listing["items"] if item["kind"] == "file" and
             (allowed is None or Path(item["name"]).suffix.lower() in allowed)]
    if len(files) > max_files:
        raise SharePointLimitError(
            "La carpeta contiene mas archivos que max_files; no se descargo nada.",
            details={"file_count": len(files), "max_files": max_files})
    declared_total = sum(max(0, int(item.get("size") or 0)) for item in files)
    if declared_total > max_total_bytes:
        raise SharePointLimitError(
            "El tamano declarado de la carpeta supera max_total_mb; no se descargo nada.",
            details={"declared_bytes": declared_total,
                     "max_total_bytes": max_total_bytes})

    # Renueva el token despues del inventario y usa exactamente los ids que
    # produjo ese inventario, evitando resolver sitio/biblioteca dos veces.
    token = _token()
    site = listing["site"]
    drive = listing["library"]

    root = (get_settings().outputs_dir / "sharepoint").resolve()
    stage = root / f".stage_{uuid.uuid4().hex}"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    final = root / f"sharepoint_{stamp}_{uuid.uuid4().hex[:8]}"
    downloaded: List[Dict[str, Any]] = []
    used_targets = set()
    consumed = 0
    try:
        stage.mkdir(parents=True, exist_ok=False)
        for item in files:
            segments = item["relative_path"].split("/")
            for segment in segments:
                safe_paths.safe_identifier(segment, kind="nombre remoto de SharePoint")
            target = (stage.joinpath(*segments)).resolve()
            try:
                target.relative_to(stage.resolve())
            except ValueError as exc:
                raise ValidationError("Una ruta remota intenta salir del staging.") from exc
            identity = os.path.normcase(str(target))
            if identity in used_targets:
                raise ValidationError(
                    f"Dos elementos remotos colisionan en Windows: {item['relative_path']}.")
            used_targets.add(identity)
            remaining = max_total_bytes - consumed
            result = _download_one(
                _download_url(token, str(drive["id"]), str(item["id"])), target,
                expected_size=int(item.get("size") or 0), max_bytes=remaining)
            consumed += int(result["bytes"])
            downloaded.append({"relative_path": item["relative_path"], **result})
        root.mkdir(parents=True, exist_ok=True)
        os.replace(stage, final)
        # Las rutas devueltas deben apuntar a la ubicacion final, no al staging.
        for item in downloaded:
            item["path"] = str(final.joinpath(*item["relative_path"].split("/")))
        return {
            "output_path": str(final),
            "site": {"id": site.get("id"),
                     "name": site.get("name") or site.get("displayName"),
                     "web_url": site.get("web_url") or site.get("webUrl")},
            "library": {"id": drive.get("id"), "name": drive.get("name")},
            "folder_path": "/".join(root_segments), "recursive": bool(recursive),
            "extensions": sorted(allowed) if allowed is not None else None,
            "file_count": len(downloaded), "bytes": consumed,
            "files": downloaded, "verified": True,
        }
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        # Si el rename ocurrio, no puede haber una excepcion posterior que
        # oculte un lote publicado. Todo despues del rename es in-memory.
        raise
