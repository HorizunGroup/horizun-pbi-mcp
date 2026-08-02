"""Escritura multiarchivo con journal, verificacion y rollback.

QUE ES Y QUE NO ES: el sistema de archivos NO ofrece atomicidad multiarchivo.
Esto es una **transaccion compensada**: antes de tocar nada se respalda cada
objetivo en un journal; si algo falla, se deshace archivo por archivo. Entre el
primer y el ultimo `os.replace` existe una ventana en la que el proyecto esta a
medias. Lo que se garantiza es que (a) esa ventana es corta, (b) el journal
permite volver atras y (c) nunca se reporta exito si el rollback no fue limpio.

Ciclo de una operacion:

    PLAN      fingerprint de cada objetivo (sha256 + tamano, o "ausente")
    SNAPSHOT  copiar los objetivos al journal + escribir el manifiesto
    PRE-CHECK re-verificar el fingerprint justo antes de cada reemplazo
    WRITE     temporal -> flush -> fsync -> validar -> os.replace -> limpiar
    POST      releer y comparar con lo que se pretendia escribir
    COMMIT    cerrar el manifiesto   |   FALLO -> ROLLBACK

El fingerprint se verifica TRES veces: al planificar, inmediatamente antes de
reemplazar y despues de escribir. Nunca se usan marcas de tiempo.

ROLLBACK CONSCIENTE DE CONCURRENCIA: no se pisa un cambio externo posterior a
nuestra escritura. Un archivo solo se restaura si su contenido actual sigue
siendo EL QUE ESCRIBIMOS NOSOTROS. Si alguien lo cambio despues, se marca
`rollback_conflict` y se conserva el journal para recuperacion manual.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError, ValidationError
from services import paths as safe_paths

log = get_logger("txn")

MANIFEST_VERSION = 1

# Resultado por archivo tras un rollback.
RESTORED = "restored"                 # se devolvio a su contenido original
UNCHANGED = "unchanged"               # nunca se llego a escribir
ROLLBACK_CONFLICT = "rollback_conflict"   # cambio externo: NO se toco
ROLLBACK_FAILED = "rollback_failed"   # se intento restaurar y fallo
COMMITTED = "committed"


class BackupDestinationError(PowerBIMCPError):
    code = "backup_destination_invalid"


class TransactionError(PowerBIMCPError):
    code = "transaction_failed"


class RollbackIncompleteError(PowerBIMCPError):
    code = "rollback_incomplete"


# ------------------------------------------------------------ fingerprints ---
@dataclass
class Fingerprint:
    state: str                       # "present" | "absent"
    sha256: Optional[str] = None
    size: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"state": self.state}
        if self.state == "present":
            d["sha256"] = self.sha256
            d["size"] = self.size
        return d

    def matches(self, other: "Fingerprint") -> bool:
        if self.state != other.state:
            return False
        if self.state == "absent":
            return True
        return self.sha256 == other.sha256 and self.size == other.size


def fingerprint(path: Path) -> Fingerprint:
    """sha256 + tamano de un archivo, o estado 'absent'.

    `absent` es un estado de primera clase: crear un archivo que aparecio entre
    medias tambien es una colision.
    """
    p = Path(path)
    if not p.exists():
        return Fingerprint("absent")
    if p.is_dir():
        raise ValidationError(f"Se esperaba un archivo y es un directorio: {p}")
    h = hashlib.sha256()
    size = 0
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(131072), b""):
            h.update(chunk)
            size += len(chunk)
    return Fingerprint("present", h.hexdigest(), size)


def fingerprint_bytes(data: bytes) -> Fingerprint:
    return Fingerprint("present", hashlib.sha256(data).hexdigest(), len(data))


# --------------------------------------------------------- destino backups ---
def project_id(project_dir: Path) -> str:
    """Identificador ESTABLE del proyecto: hash de su ruta absoluta.

    Se usa el hash y no el nombre porque dos proyectos distintos pueden
    llamarse igual (`Informe.pbip` en dos carpetas). Compartir backups entre
    ellos seria una perdida de datos silenciosa.
    """
    normalized = os.path.normcase(str(Path(project_dir).resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def _slug(name: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in name)
    return (safe[:40] or "proyecto")


def resolve_backup_root(project_dir: Path,
                        configured: Optional[Path],
                        *, extra_forbidden: Iterable[Path] = ()) -> Path:
    """Resuelve y VALIDA el destino de los backups de un proyecto.

    Reglas, en orden:
      1. Nunca dentro del `.pbip`, ni del `.Report`, ni del `.SemanticModel`.
      2. Nunca una ubicacion no validada o no escribible.
      3. Un subdirectorio por proyecto identificado por HASH, no por nombre.
      4. Si no hay destino inequivocamente seguro y escribible, fallar de forma
         accionable ANTES de tocar el proyecto.
    """
    if configured is None:
        raise BackupDestinationError(
            "No hay carpeta de backups configurada y no se puede elegir una por "
            "defecto sin riesgo. Define PBI_MCP_BACKUPS_DIR apuntando a una "
            "carpeta FUERA del proyecto .pbip.",
            details={"project_dir": str(project_dir)})

    root = Path(configured).expanduser()
    proj = Path(project_dir).resolve()

    forbidden = [proj, *[Path(p).resolve() for p in extra_forbidden if p]]
    for bad in forbidden:
        if safe_paths.is_inside(bad, root):
            raise BackupDestinationError(
                "La carpeta de backups esta DENTRO del proyecto. Power BI podria "
                "interpretar esos archivos como parte del informe. Configura "
                "PBI_MCP_BACKUPS_DIR fuera del proyecto.",
                details={"backups_dir": str(root), "inside_of": str(bad)})
        if safe_paths.is_inside(root, bad):
            raise BackupDestinationError(
                "El proyecto esta dentro de la carpeta de backups: se respaldaria "
                "a si mismo de forma recursiva.",
                details={"backups_dir": str(root), "project_dir": str(bad)})

    target = root / f"{_slug(proj.name)}_{project_id(proj)}"
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise BackupDestinationError(
            f"La carpeta de backups no es escribible: {exc}. Configura "
            "PBI_MCP_BACKUPS_DIR a una ubicacion valida antes de modificar el "
            "proyecto.",
            details={"backups_dir": str(target)}) from exc
    return target


# ------------------------------------------------------- escritura durable ---
def durable_write(path: Path, data: bytes,
                  validator: Optional[Callable[[bytes], None]] = None) -> Fingerprint:
    """Escribe `data` en `path` de forma durable y verificada.

    temporal en el MISMO directorio (por tanto, mismo sistema de archivos) ->
    flush -> fsync -> validar el temporal -> os.replace -> limpiar el temporal
    en `finally` si sobrevivio.

    No se hace fsync del directorio: Windows no lo admite.
    """
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if validator is not None:
            validator(tmp.read_bytes())      # valida el TEMPORAL, no el destino
        os.replace(tmp, p)
    finally:
        # Si `os.replace` fallo (p.ej. otro proceso tiene el destino abierto:
        # WinError 5), el temporal seguiria dentro del proyecto del usuario.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:                  # pragma: no cover
                log.warning("No se pudo eliminar el temporal %s", tmp)
    return fingerprint_bytes(data)


def _json_validator(data: bytes) -> None:
    try:
        json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"El contenido generado no es JSON valido: {exc}") from exc


# ------------------------------------------------------------- transaccion ---
@dataclass
class FileRecord:
    key: str
    path: Path
    initial: Fingerprint
    written: Optional[Fingerprint] = None
    outcome: Optional[str] = None
    detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"path": self.key, **self.initial.to_dict()}
        if self.written:
            d["written_sha256"] = self.written.sha256
        if self.outcome:
            d["outcome"] = self.outcome
        if self.detail:
            d["detail"] = self.detail
        return d


class Transaction:
    """Transaccion compensada sobre un conjunto de archivos del proyecto."""

    def __init__(self, project_dir: Path, backup_root: Path, *,
                 tool: str, request_id: Optional[str] = None):
        self.project_dir = Path(project_dir).resolve()
        self.tool = tool
        self.request_id = request_id or uuid.uuid4().hex[:12]
        self.started = datetime.now().isoformat(timespec="seconds")
        self.records: Dict[str, FileRecord] = {}
        self.committed = False
        self._rolled_back = False
        # Directorios que NO existian al planificar y que la escritura creara.
        # Si hay rollback hay que retirarlos: un `<pageId>/` vacio sin page.json
        # dentro es basura que Power BI y nuestro propio lector interpretan mal.
        self._created_dirs: List[Path] = []

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.journal_dir = Path(backup_root) / f"{stamp}_{self.request_id}"
        self.files_dir = self.journal_dir / "files"
        self.manifest_path = self.journal_dir / "manifest.json"

    # -- planificacion ------------------------------------------------------
    def plan(self, targets: Iterable[Path]) -> None:
        """Fingerprint inicial de cada objetivo y snapshot en el journal."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        for target in targets:
            resolved = safe_paths.ensure_contained(
                self.project_dir, target, kind="objetivo de escritura")
            key = safe_paths.relative_key(self.project_dir, resolved)
            if key in self.records:
                continue
            fp = fingerprint(resolved)
            self.records[key] = FileRecord(key=key, path=resolved, initial=fp)
            if fp.state == "absent":
                self._note_missing_dirs(resolved.parent)
            if fp.state == "present":
                dest = self.files_dir / key
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resolved, dest)
                copia = fingerprint(dest)
                if not copia.matches(fp):
                    raise TransactionError(
                        "El respaldo del journal no coincide con el original; se "
                        "aborta antes de modificar nada.",
                        details={"file": key})
        self._write_manifest(status="open")

    def _note_missing_dirs(self, directory: Path) -> None:
        """Apunta los directorios inexistentes que habra que retirar al revertir."""
        cadena: List[Path] = []
        actual = Path(directory)
        while not actual.exists():
            if not safe_paths.is_inside(self.project_dir, actual):
                break
            cadena.append(actual)
            padre = actual.parent
            if padre == actual:                       # pragma: no cover
                break
            actual = padre
        # De mas profundo a mas superficial: es el orden en que hay que borrar.
        for d in cadena:
            if d not in self._created_dirs:
                self._created_dirs.append(d)

    def _remove_created_dirs(self) -> List[str]:
        """Retira los directorios que creamos, si quedaron vacios."""
        retirados: List[str] = []
        for d in sorted(self._created_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                if d.exists() and d.is_dir() and not any(d.iterdir()):
                    d.rmdir()
                    retirados.append(str(d))
            except OSError:                            # pragma: no cover
                # Si alguien dejo algo dentro, se respeta: no es nuestro.
                continue
        return retirados

    # -- escritura ----------------------------------------------------------
    def write_bytes(self, target: Path, data: bytes,
                    validator: Optional[Callable[[bytes], None]] = None) -> None:
        rec = self._record_for(target)

        # PRE-CHECK: el archivo debe seguir como estaba al planificar.
        actual = fingerprint(rec.path)
        if not actual.matches(rec.initial):
            raise TransactionError(
                "El archivo cambio despues de planificar la operacion (lo modifico "
                "otro proceso). No se sobrescribe.",
                details={"file": rec.key,
                         "expected": rec.initial.to_dict(),
                         "actual": actual.to_dict()})

        # Revalidacion anti-TOCTOU: un junction pudo cambiar de destino.
        safe_paths.assert_still_contained(self.project_dir, rec.path,
                                          kind="objetivo de escritura")

        written = durable_write(rec.path, data, validator)
        # Se registra ANTES de verificar: si la verificacion falla porque alguien
        # toco el archivo justo despues, el rollback debe saber que ese archivo
        # pasó por nuestras manos y marcarlo como conflicto, en vez de darlo por
        # intacto y declarar limpio un rollback que no lo es.
        rec.written = written

        # POST: releer del disco y comparar con lo que pretendiamos escribir.
        verificado = fingerprint(rec.path)
        if not verificado.matches(written):
            raise TransactionError(
                "Tras escribir, el archivo en disco no coincide con el contenido "
                "previsto (lo modifico otro proceso entre la escritura y la "
                "verificacion).",
                details={"file": rec.key, "expected": written.to_dict(),
                         "actual": verificado.to_dict()})

    def delete(self, target: Path) -> None:
        """Elimina un archivo dentro de la transaccion.

        Se comprueba antes que siga como al planificar: si otro proceso lo
        cambio, no se borra. El original vive en el journal, asi que el rollback
        puede devolverlo.
        """
        rec = self._record_for(target)
        if rec.initial.state == "absent":
            return                                    # ya no estaba: nada que hacer

        actual = fingerprint(rec.path)
        if not actual.matches(rec.initial):
            raise TransactionError(
                "El archivo cambio despues de planificar la operacion; no se "
                "elimina.",
                details={"file": rec.key, "expected": rec.initial.to_dict(),
                         "actual": actual.to_dict()})

        safe_paths.assert_still_contained(self.project_dir, rec.path,
                                          kind="objetivo de borrado")
        rec.path.unlink()
        # `written = absent` es como se representa "lo dejamos borrado": el
        # rollback comparara contra eso antes de restaurar.
        rec.written = Fingerprint("absent")

        verificado = fingerprint(rec.path)
        if verificado.state != "absent":              # pragma: no cover
            raise TransactionError(
                "El archivo sigue existiendo tras eliminarlo.",
                details={"file": rec.key})

    def write_json(self, target: Path, data: Any) -> None:
        from utils.json_utils import detect_newline

        # ANTES de escribir: que cumpla el esquema que el propio archivo
        # declara. Comprobar solo que "parsea" dejaba pasar un visual.json sin
        # `position`, o con `x` de tipo texto: Power BI lo rechaza al abrir, muy
        # lejos de la operacion que lo causo.
        # Si falla, la excepcion sale de la transaccion y se revierte el lote.
        self._validar_esquema(target, data)

        text = json.dumps(data, indent=2, ensure_ascii=False)
        # Power BI escribe los JSON del PBIR con CRLF. Reescribir con LF deja el
        # contenido identico pero el ARCHIVO distinto, y entonces una huella deja
        # de servir para demostrar que no se toco nada.
        datos = text.encode("utf-8").replace(b"\n", detect_newline(target))
        self.write_bytes(target, datos, _json_validator)

    def _validar_esquema(self, target: Path, data: Any) -> None:
        """Valida contra el esquema local. Solo aplica a archivos del PBIR.

        Un archivo sin `$schema` no se rechaza: en PBIR no todos lo llevan.
        Uno que declara un `$schema` DESCONOCIDO si, porque no se puede
        comprobar lo que se va a escribir.
        """
        from services import pbir_schema

        resultado = pbir_schema.validar(data, archivo=target)
        if resultado.get("validated"):
            self.schemas_validados = getattr(self, "schemas_validados", 0) + 1

    def write_text(self, target: Path, text: str) -> None:
        self.write_bytes(target, text.encode("utf-8"))

    def ensure_directory(self, target: Path) -> None:
        """Crea un directorio planificado como parte de la transaccion.

        Algunos formatos distinguen una carpeta vacia obligatoria de una
        carpeta ausente (por ejemplo ``definition/tables`` en un modelo recien
        creado). Se registra toda la cadena nueva para retirarla si hay
        rollback o falla el commit.
        """
        directory = safe_paths.ensure_contained(
            self.project_dir, target, kind="directorio de proyecto")
        if directory.exists():
            if not directory.is_dir():
                raise TransactionError(
                    "Se esperaba crear un directorio, pero la ruta es un archivo.",
                    details={"directory": safe_paths.relative_key(
                        self.project_dir, directory)})
            return
        self._note_missing_dirs(directory)
        directory.mkdir(parents=True, exist_ok=True)

    def _record_for(self, target: Path) -> FileRecord:
        resolved = safe_paths.ensure_contained(
            self.project_dir, target, kind="objetivo de escritura")
        key = safe_paths.relative_key(self.project_dir, resolved)
        rec = self.records.get(key)
        if rec is None:
            raise TransactionError(
                "Se intento escribir un archivo que no estaba en el plan de la "
                "transaccion; no hay respaldo suyo.",
                details={"file": key, "planned": sorted(self.records)})
        return rec

    # -- cierre -------------------------------------------------------------
    def commit(self) -> Dict[str, Any]:
        for rec in self.records.values():
            rec.outcome = COMMITTED if rec.written else UNCHANGED
        # El journal es parte del commit, no un log opcional. Declarar exito si
        # no se pudo cerrarlo deja un manifiesto "open" que parece una
        # transaccion interrumpida y rompe la recuperacion. Se marca committed
        # solo DESPUES de persistir el cierre; si falla, el context manager
        # compensa los archivos ya escritos.
        self._write_manifest(status="committed", strict=True)
        self.committed = True
        return self.summary()

    def compensate(self, cause: Optional[str] = None) -> Dict[str, Any]:
        """Deshace una transaccion YA CONFIRMADA.

        Existe para las operaciones compensadas entre dos destinos que no
        comparten transaccion: p.ej. escribir el TMDL en disco y despues fallar
        al aplicar el cambio en el modelo en memoria de Power BI Desktop. En ese
        caso hay que revertir lo ya confirmado en disco.

        Aplica exactamente la misma regla que el rollback: no se pisa un cambio
        externo posterior a nuestra escritura.
        """
        return self._undo(status="compensated", cause=cause)

    def rollback(self, cause: Optional[str] = None) -> Dict[str, Any]:
        """Deshace lo escrito SIN pisar cambios externos posteriores."""
        return self._undo(status="rolled_back", cause=cause)

    def _undo(self, status: str, cause: Optional[str] = None) -> Dict[str, Any]:
        self._rolled_back = True
        for rec in self.records.values():
            if rec.written is None:
                rec.outcome = UNCHANGED
                continue
            actual = fingerprint(rec.path)

            # Solo se toca si sigue estando EXACTAMENTE lo que escribimos.
            if not actual.matches(rec.written):
                rec.outcome = ROLLBACK_CONFLICT
                rec.detail = (
                    "El archivo cambio despues de nuestra escritura; no se toca "
                    "para no perder ese cambio externo.")
                continue

            try:
                if rec.initial.state == "absent":
                    # Lo creamos nosotros y sigue siendo nuestro: se elimina.
                    if rec.path.exists():
                        rec.path.unlink()
                    rec.outcome = RESTORED
                    rec.detail = "Archivo creado por la transaccion: eliminado."
                else:
                    origen = self.files_dir / rec.key
                    if not origen.exists():
                        rec.outcome = ROLLBACK_FAILED
                        rec.detail = "Falta el respaldo en el journal."
                        continue
                    # El directorio padre puede haberse eliminado dentro de la
                    # propia transaccion (al borrar el ultimo visual de una
                    # carpeta). Sin recrearlo, copy2 falla y el rollback deja
                    # el archivo perdido.
                    rec.path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(origen, rec.path)
                    restaurado = fingerprint(rec.path)
                    if restaurado.matches(rec.initial):
                        rec.outcome = RESTORED
                        rec.detail = "Restaurado byte a byte."
                    else:
                        rec.outcome = ROLLBACK_FAILED
                        rec.detail = (
                            "La restauracion no coincide con el original.")
            except OSError as exc:
                rec.outcome = ROLLBACK_FAILED
                rec.detail = f"{type(exc).__name__}: {exc}"

        # Retira los directorios que creamos y quedaron vacios. Sin esto, un
        # rollback de "crear pagina" dejaba un <pageId>/ huerfano sin page.json.
        self._removed_dirs = self._remove_created_dirs()
        # El rollback ya hizo el trabajo material. Si el disco tampoco permite
        # anotar su resultado, no se debe deshacer ni ocultar esa restauracion;
        # se registra el aviso y el resumen conserva el estado real en memoria.
        self._write_manifest(status=status, cause=cause, strict=False)
        return self.summary()

    # -- informe ------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        by_outcome: Dict[str, List[str]] = {}
        for rec in self.records.values():
            by_outcome.setdefault(rec.outcome or "unknown", []).append(rec.key)
        conflicts = by_outcome.get(ROLLBACK_CONFLICT, [])
        failures = by_outcome.get(ROLLBACK_FAILED, [])
        return {
            "request_id": self.request_id,
            "tool": self.tool,
            "committed": self.committed,
            "rolled_back": self._rolled_back,
            "clean": not conflicts and not failures,
            "journal": str(self.journal_dir),
            "files": [r.to_dict() for r in self.records.values()],
            "by_outcome": by_outcome,
            "removed_dirs": getattr(self, "_removed_dirs", []),
        }

    def _write_manifest(self, status: str, cause: Optional[str] = None, *,
                        strict: bool = True) -> None:
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "status": status,
            "created": self.started,
            "closed": datetime.now().isoformat(timespec="seconds"),
            "tool": self.tool,
            "request_id": self.request_id,
            "algorithm": "sha256",
            "source_root": str(self.project_dir),
            "project_id": project_id(self.project_dir),
            "scope": "targets",
            "files": [r.to_dict() for r in self.records.values()],
        }
        if cause:
            manifest["cause"] = cause
        try:
            self.journal_dir.mkdir(parents=True, exist_ok=True)
            durable_write(self.manifest_path,
                          (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
                          .encode("utf-8"), _json_validator)
        except OSError as exc:
            if strict:
                raise TransactionError(
                    "No se pudo persistir el estado del journal; la operacion "
                    "no se puede declarar confirmada.",
                    details={"manifest": str(self.manifest_path),
                             "status": status, "reason": str(exc)}) from exc
            log.warning("No se pudo escribir el manifiesto: %s", exc)


def list_journals(backup_root: Path, *, only_pending: bool = False) -> List[Dict[str, Any]]:
    """Enumera los journals de un proyecto, del mas reciente al mas antiguo.

    Un journal `pending` es el de una transaccion que ni se confirmo ni se
    revirtio: el proceso murio entre medias. Contiene los originales y permite
    recuperacion manual.
    """
    salida: List[Dict[str, Any]] = []
    root = Path(backup_root)
    if not root.exists():
        return salida
    for manifest_path in sorted(root.rglob("manifest.json"), reverse=True):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            salida.append({"journal": str(manifest_path.parent),
                           "status": "unreadable", "pending": True})
            continue
        estado = data.get("status", "unknown")
        pendiente = estado == "open"
        if only_pending and not pendiente:
            continue
        conflictos = [f for f in data.get("files", [])
                      if f.get("outcome") in (ROLLBACK_CONFLICT, ROLLBACK_FAILED)]
        salida.append({
            "journal": str(manifest_path.parent),
            "status": estado,
            "pending": pendiente,
            "needs_attention": pendiente or bool(conflictos),
            "tool": data.get("tool"),
            "created": data.get("created"),
            "request_id": data.get("request_id"),
            "file_count": len(data.get("files", [])),
            "conflicts": len(conflictos),
        })
    return salida


def read_journal(journal_dir: Path) -> Dict[str, Any]:
    """Lee un journal y compara su contenido con el estado ACTUAL del proyecto.

    Solo lectura: no restaura nada. Sirve para decidir si hace falta una
    recuperacion manual y sobre que archivos.
    """
    jdir = Path(journal_dir)
    manifest_path = jdir / "manifest.json"
    if not manifest_path.exists():
        raise ValidationError(f"No hay manifest.json en {jdir}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValidationError(f"Manifiesto ilegible: {exc}") from exc

    root = Path(data.get("source_root", ""))
    archivos = []
    for entrada in data.get("files", []):
        rel = entrada["path"]
        actual_path = root / rel
        respaldo = jdir / "files" / rel
        actual = fingerprint(actual_path) if root.exists() else Fingerprint("absent")
        original = Fingerprint(entrada.get("state", "present"),
                               entrada.get("sha256"), entrada.get("size"))
        archivos.append({
            "path": rel,
            "original": original.to_dict(),
            "current": actual.to_dict(),
            "matches_original": actual.matches(original),
            "backup_available": respaldo.exists(),
            "outcome": entrada.get("outcome"),
        })
    return {
        "journal": str(jdir),
        "status": data.get("status"),
        "tool": data.get("tool"),
        "created": data.get("created"),
        "request_id": data.get("request_id"),
        "source_root": str(root),
        "project_id": data.get("project_id"),
        "files": archivos,
        "restorable": all(f["backup_available"] or f["original"]["state"] == "absent"
                          for f in archivos),
    }


def project_backup_root(active) -> Path:
    """Destino de backups validado para el .pbip activo.

    Centraliza la resolucion para que ningun escritor la reinvente: las
    carpetas `.Report` y `.SemanticModel` se pasan como prohibidas explicitas,
    porque pueden estar fuera del directorio del proyecto.
    """
    from config import get_settings

    extra = [d for d in (active.report_dir, active.semantic_model_dir) if d]
    return resolve_backup_root(Path(active.project_dir),
                               get_settings().backups_dir,
                               extra_forbidden=extra)


def project_transaction(active, targets: Iterable[Path], *, tool: str,
                        request_id: Optional[str] = None,
                        validate_report: bool = True) -> "transaction":
    """Abre una transaccion sobre el .pbip activo, con backups ya validados.

    `validate_report`: ejecuta el validador oficial de Microsoft sobre el
    `.Report` completo despues de escribir el lote y ANTES de confirmar. Si
    encuentra errores NUEVOS, se revierte. Ver services/report_validator.py.
    """
    report_dir = None
    if validate_report:
        report_dir = getattr(active, "report_dir", None)
    return transaction(Path(active.project_dir), project_backup_root(active),
                       targets, tool=tool, request_id=request_id,
                       report_dir=Path(report_dir) if report_dir else None)


class transaction:
    """Context manager. Al salir: commit si todo fue bien, rollback si no."""

    def __init__(self, project_dir: Path, backup_root: Path, targets: Iterable[Path],
                 *, tool: str, request_id: Optional[str] = None,
                 report_dir: Optional[Path] = None):
        self.txn = Transaction(project_dir, backup_root, tool=tool,
                               request_id=request_id)
        self._targets = list(targets)
        self._report_dir = report_dir
        self.result: Optional[Dict[str, Any]] = None
        #: Diagnosticos del validador oficial ANTES de escribir (baseline).
        self.validation: Optional[Dict[str, Any]] = None
        self._baseline: Optional[list] = None

    def __enter__(self) -> Transaction:
        # El baseline se toma ANTES de tocar nada: sin el, los defectos que ya
        # traia el informe se atribuirian a esta operacion.
        self._baseline = self._diagnosticar()
        self.txn.plan(self._targets)
        return self.txn

    def _diagnosticar(self) -> Optional[list]:
        if self._report_dir is None:
            return None
        from services import report_validator as rv

        if not rv.estado()["available"]:
            return None
        try:
            resultado = rv.validar_informe(self._report_dir)
        except Exception as exc:                        # noqa: BLE001
            raise rv.ReportValidationFailed(
                "El validador oficial fallo antes de escribir; no se inicia "
                "una operacion cuyo resultado no se podria comprobar.",
                details={"cause": f"{type(exc).__name__}: {exc}"}) from exc
        if resultado.status in (rv.UNAVAILABLE, rv.TIMEOUT):
            raise rv.ReportValidationFailed(
                "El validador oficial no pudo establecer el estado inicial "
                "del informe; no se escribio ningun archivo.",
                details=resultado.to_envelope())
        return resultado.diagnostics

    def _validar_informe(self) -> None:
        """Valida el `.Report` tras escribir el lote y ANTES de confirmar.

        Solo bloquean los diagnosticos NUEVOS: el informe puede traer defectos
        propios —el de referencia trae 44— y atribuirlos a esta operacion seria
        falso. Ignorar los nuevos, en cambio, seria peligroso.
        """
        if self._report_dir is None or self._baseline is None:
            return
        from services import report_validator as rv

        resultado = rv.validar_informe(self._report_dir)
        if resultado.status in (rv.UNAVAILABLE, rv.TIMEOUT):
            raise rv.ReportValidationFailed(
                "El validador oficial no pudo comprobar el informe despues "
                "de escribir; el cambio se revierte.",
                details=resultado.to_envelope())

        comparacion = rv.comparar(self._baseline, resultado.diagnostics)
        self.validation = {**resultado.to_envelope(), **comparacion}
        if comparacion["blocks"]:
            introducidos = comparacion["introduced_error_count"]
            propagados = comparacion["propagated_error_count"]
            if introducidos:
                motivo = (f"{introducidos} error(es) que NO existian en el "
                          "informe antes de esta operacion")
            else:
                motivo = (f"{propagados} error(es) PROPAGADOS: son defectos que "
                          "el informe ya tenia y que esta operacion ha copiado "
                          "a archivos nuevos (tipico al duplicar una pagina que "
                          "ya venia con errores). No se han introducido, pero el "
                          "informe acaba con mas errores que antes")
            raise rv.ReportValidationFailed(
                f"El validador oficial de Microsoft encontro {motivo}. "
                f"El informe pasaria de {comparacion['errors_before']} a "
                f"{comparacion['errors_after']} errores.",
                details=self.validation)

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            try:
                self._validar_informe()
                self.result = self.txn.commit()
            except BaseException as fallo_commit:
                # El commit puede fallar por si mismo (escribir el manifiesto,
                # disco lleno, permisos) y para entonces TODO esta ya escrito.
                # Sin esto la excepcion salia de __exit__ sin revertir nada: el
                # proyecto quedaba modificado y la operacion parecia fallida.
                self.result = self.txn.rollback(
                    cause=f"fallo al confirmar: {type(fallo_commit).__name__}: "
                          f"{fallo_commit}")
                if not self.result["clean"]:
                    raise RollbackIncompleteError(
                        "Fallo al confirmar y el rollback no pudo dejar el "
                        "proyecto como estaba. Revisa el journal: contiene los "
                        "originales.",
                        details={"cause": str(fallo_commit),
                                 **self.result}) from fallo_commit
                raise
            return False

        self.result = self.txn.rollback(cause=f"{exc_type.__name__}: {exc}")
        if not self.result["clean"]:
            # El rollback no fue limpio: eso NO se puede reportar como un simple
            # fallo de la operacion. Se sustituye por un error que lo dice.
            raise RollbackIncompleteError(
                "La operacion fallo y el rollback no pudo dejar el proyecto como "
                "estaba. Revisa el journal: contiene los originales.",
                details={"cause": str(exc), **self.result}) from exc
        return False
