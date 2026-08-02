"""Fase F / R5 — recuperar desde un journal y purgar backups con seguridad.

Que faltaba
-----------
Se podian LISTAR e INSPECCIONAR journals, pero no restaurar: ante una
transaccion que quedo abierta porque el proceso murio, el usuario tenia los
originales delante y ninguna forma de devolverlos a su sitio salvo copiarlos a
mano. Y R5 seguia abierto porque los backups crecian sin limite y no habia
ninguna purga que se atreviera a ejecutarse.

Recuperacion
------------
Cinco estados, y solo cinco:

============ ================================================================
recoverable  el journal esta completo y el proyecto encaja: se puede restaurar
recovered    se restauro y se verifico byte a byte
conflict     alguien cambio los archivos DESPUES; restaurar perderia su trabajo
incomplete   se restauro parte: queda intervencion manual
corrupted    el manifiesto o los respaldos no sirven
============ ================================================================

Antes de tocar nada se comprueba que el journal pertenezca a ESTE proyecto
(`project_id`), que el manifiesto sea legible y que cada respaldo exista con el
hash que declara. Restaurar un journal de otro proyecto seria sobreescribir
archivos ajenos con contenido que no les corresponde.

Purga
-----
Fail-closed en todo:

- `dry_run` por defecto: sin `confirm=True` no se borra nada;
- la raiz debe estar DENTRO de la carpeta de backups del proyecto activo;
- se rechazan raiz de unidad, HOME, el directorio del proyecto y cualquier
  ruta que no sea reconociblemente nuestra;
- solo se eliminan directorios de journal con su `manifest.json`: nunca un
  archivo suelto que alguien dejara ahi;
- enlaces simbolicos y puntos de reanalisis se saltan, no se siguen;
- se conserva siempre el mas reciente, aunque la politica pidiera borrarlo.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PathSecurityError, PowerBIMCPError
from services import paths as safe_paths

log = get_logger("recovery")

RECOVERABLE = "recoverable"
RECOVERED = "recovered"
CONFLICT = "conflict"
INCOMPLETE = "incomplete"
CORRUPTED = "corrupted"

#: Retencion por defecto.
DIAS_POR_DEFECTO = 30
MAXIMO_POR_DEFECTO = 50
#: Nunca se borra el journal mas reciente, diga lo que diga la politica.
CONSERVAR_SIEMPRE = 1


class RecoveryError(PowerBIMCPError):
    code = "recovery_failed"


class RecoveryConflict(PowerBIMCPError):
    code = "recovery_conflict"


class UnsafePurgeRoot(PowerBIMCPError):
    """La raiz de purga no es reconociblemente nuestra."""

    code = "unsafe_purge_root"


# ============================================================ recuperacion ====
def _corrupto(mensaje: str, **details: Any) -> "NoReturn":  # type: ignore[valid-type]
    raise RecoveryError(
        mensaje, details={"state": CORRUPTED, **details})


def _es_enlace_o_reparse(ruta: Path) -> bool:
    """Detecta enlaces, junctions y otros reparse points sin seguirlos."""
    try:
        if ruta.is_symlink() or os.path.islink(ruta):
            return True
        atributos = getattr(os.stat(ruta, follow_symlinks=False),
                            "st_file_attributes", 0)
        return bool(atributos & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    except FileNotFoundError:
        return False
    except OSError as exc:
        _corrupto("No se pudo inspeccionar una ruta del journal.",
                  path=str(ruta), reason=str(exc))


def _sin_enlaces_desde(raiz: Path, destino: Path, *, kind: str) -> None:
    """Rechaza cualquier componente enlazado por debajo de una raiz confiable."""
    base = Path(raiz).resolve()
    # ``absolute`` conserva los componentes lexicos: ``resolve`` borraria justo
    # el enlace que necesitamos detectar.
    candidato = Path(os.path.abspath(destino))
    try:
        relativa = candidato.relative_to(base)
    except ValueError:
        _corrupto(f"{kind} escapa de su raiz autorizada.",
                  root=str(base), path=str(candidato))
    actual = base
    for parte in relativa.parts:
        actual = actual / parte
        if _es_enlace_o_reparse(actual):
            _corrupto(f"{kind} contiene un enlace o punto de reanalisis.",
                      root=str(base), path=str(actual))


def _resolver_ruta_manifest(raiz: Path, relativa: Any, *, kind: str) -> Path:
    """Convierte una ruta NO confiable del manifiesto en un destino contenido."""
    if not isinstance(relativa, str) or not relativa.strip():
        _corrupto(f"{kind} no es una ruta relativa valida.", value=repr(relativa))

    # Los journals son portables y guardan '/'. Se admite '\\' de versiones
    # antiguas, pero cada componente debe seguir siendo un nombre simple.
    partes = relativa.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in partes):
        _corrupto(f"{kind} contiene componentes relativos o vacios.",
                  path=relativa)
    try:
        for parte in partes:
            safe_paths.safe_identifier(parte, kind=kind)
        candidato = Path(raiz).joinpath(*partes)
        resuelto = safe_paths.ensure_contained(raiz, candidato, kind=kind)
    except PathSecurityError as exc:
        _corrupto(f"{kind} escapa de su raiz autorizada.",
                  path=relativa, reason=exc.message)
    _sin_enlaces_desde(Path(raiz), candidato, kind=kind)
    return resuelto


def _leer_manifiesto(jdir: Path) -> Dict[str, Any]:
    f = jdir / "manifest.json"
    if not f.exists():
        raise RecoveryError(f"El journal {jdir} no tiene manifest.json.",
                            details={"journal": str(jdir), "state": CORRUPTED})
    try:
        datos = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError(f"Manifiesto ilegible en {jdir}: {exc}",
                            details={"journal": str(jdir),
                                     "state": CORRUPTED}) from exc
    if not isinstance(datos, dict):
        raise RecoveryError(
            f"El manifiesto de {jdir} no es un objeto JSON.",
            details={"journal": str(jdir), "state": CORRUPTED})
    return datos


def preview(active, journal_dir: Path) -> Dict[str, Any]:
    """Que se restauraria, y si se puede. NO escribe nada."""
    from services import txn as txn_service

    jdir_sin_resolver = Path(journal_dir).expanduser().absolute()
    root = txn_service.project_backup_root(active).resolve()
    try:
        jdir = safe_paths.ensure_contained(
            root, jdir_sin_resolver, kind="directorio de journal")
    except PathSecurityError as exc:
        raise RecoveryError(
            "Ese journal no pertenece al proyecto activo.",
            details={"journal": str(jdir_sin_resolver),
                     "backup_root": str(root), "state": CORRUPTED}) from exc
    _sin_enlaces_desde(root, jdir_sin_resolver, kind="directorio de journal")

    datos = _leer_manifiesto(jdir)

    proyecto = Path(active.project_dir).resolve()
    esperado = txn_service.project_id(Path(active.project_dir))
    if datos.get("project_id") != esperado:
        raise RecoveryError(
            "El journal es de otro proyecto. Restaurarlo sobreescribiria "
            "archivos ajenos con contenido que no les corresponde.",
            details={"journal": str(jdir),
                     "journal_project": datos.get("project_id"),
                     "active_project": esperado, "state": CORRUPTED})

    try:
        origen_declarado = Path(str(datos.get("source_root", ""))).resolve()
    except (OSError, ValueError):
        _corrupto("El source_root del journal no es una ruta valida.",
                  journal=str(jdir))
    if origen_declarado != proyecto:
        _corrupto(
            "El source_root del journal no coincide con el proyecto activo.",
            journal=str(jdir), declared_root=str(origen_declarado),
            authorized_root=str(proyecto))

    entradas = datos.get("files")
    if not isinstance(entradas, list):
        _corrupto("La lista de archivos del journal no es valida.",
                  journal=str(jdir))

    files_root = jdir / "files"
    _sin_enlaces_desde(jdir, files_root, kind="carpeta de respaldos")
    archivos, conflictos, faltantes, corruptos = [], [], [], []
    vistos = set()
    for entrada in entradas:
        if not isinstance(entrada, dict):
            _corrupto("Una entrada de archivo del journal no es un objeto.",
                      journal=str(jdir))
        rel_bruta = entrada.get("path")
        destino = _resolver_ruta_manifest(
            proyecto, rel_bruta, kind="destino de recuperacion")
        respaldo = _resolver_ruta_manifest(
            files_root, rel_bruta, kind="respaldo del journal")
        rel = safe_paths.relative_key(proyecto, destino)
        if rel in vistos:
            _corrupto("El journal repite un mismo archivo.", path=rel)
        vistos.add(rel)
        original = txn_service.Fingerprint(
            entrada.get("state", "present"), entrada.get("sha256"),
            entrada.get("size"))
        actual = txn_service.fingerprint(destino)
        escrito = entrada.get("written_sha256")

        hay_respaldo = respaldo.is_file() or original.state == "absent"
        respaldo_verificado = original.state == "absent"
        if not hay_respaldo:
            faltantes.append(rel)
        elif original.state == "present":
            huella_respaldo = txn_service.fingerprint(respaldo)
            respaldo_verificado = huella_respaldo.matches(original)
            if not respaldo_verificado:
                corruptos.append(rel)

        # Conflicto: lo que hay ahora no es ni el original ni lo que nosotros
        # escribimos. Alguien mas lo toco.
        ajeno = (not actual.matches(original)
                 and (escrito is None or actual.sha256 != escrito))
        if ajeno and actual.state != "absent":
            conflictos.append(rel)

        archivos.append({
            "path": rel,
            "action": ("delete" if original.state == "absent" else "restore"),
            "original": original.to_dict(),
            "current": actual.to_dict(),
            "already_original": actual.matches(original),
            "backup_available": hay_respaldo,
            "backup_verified": respaldo_verificado,
            "externally_modified": ajeno,
        })

    if faltantes or corruptos:
        estado = CORRUPTED
    elif conflictos:
        estado = CONFLICT
    elif datos.get("recovery", {}).get("status") == RECOVERED:
        estado = RECOVERED
    else:
        estado = RECOVERABLE

    return {
        "journal": str(jdir), "state": estado,
        "tool": datos.get("tool"), "created": datos.get("created"),
        "status": datos.get("status"),
        # Se informa la raiz AUTORIZADA, no la cadena no confiable que venia en
        # el manifiesto.
        "source_root": str(proyecto),
        "files": archivos,
        "file_count": len(archivos),
        "to_restore": sum(1 for f in archivos if not f["already_original"]),
        "conflicts": conflictos, "missing_backups": faltantes,
        "corrupt_backups": corruptos,
        "already_recovered": bool(datos.get("recovery")),
        "note": ("Nada se ha restaurado. Llama con confirm=true para aplicarlo."
                 if estado == RECOVERABLE else ""),
    }


def recover(active, journal_dir: Path, *, confirm: bool = False,
            force_conflict: bool = False) -> Dict[str, Any]:
    """Restaura los originales de un journal. Verifica byte a byte."""
    from services import txn as txn_service

    plan = preview(active, journal_dir)
    if not confirm:
        return {**plan, "recovered": False,
                "note": "Vista previa. Pasa confirm=true para restaurar."}

    if plan["state"] == CORRUPTED:
        raise RecoveryError(
            "El journal no esta completo o algun respaldo no coincide con su "
            "hash. No se restaura a medias.",
            details={"journal": plan["journal"], "state": CORRUPTED,
                     "missing_backups": plan["missing_backups"],
                     "corrupt_backups": plan["corrupt_backups"]})

    if plan["state"] == RECOVERED and not force_conflict:
        raise RecoveryConflict(
            "Este journal ya se recupero. Volver a aplicarlo pisaria lo que "
            "haya ahora con un original que ya se restauro una vez.",
            details={"journal": plan["journal"], "state": RECOVERED})

    if plan["state"] == CONFLICT and not force_conflict:
        raise RecoveryConflict(
            f"{len(plan['conflicts'])} archivo(s) cambiaron despues de esta "
            "transaccion. Restaurar perderia ese trabajo. Revisa el preview.",
            details={"journal": plan["journal"], "state": CONFLICT,
                     "conflicts": plan["conflicts"]})

    # Restaurar tambien es una escritura sobre el PBIP: Desktop no puede estar
    # abierto, porque su siguiente guardado volveria a pisar la recuperacion.
    from services import project_state

    project_state.assert_writable(active, operation="Recuperar desde un journal")

    jdir = Path(plan["journal"])
    origen = Path(active.project_dir).resolve()
    files_root = jdir / "files"
    restaurados, fallidos = [], []

    for f in plan["files"]:
        try:
            # Revalidacion anti-TOCTOU: ni el destino ni el respaldo pueden
            # haberse convertido en un enlace desde que se genero el preview.
            destino = _resolver_ruta_manifest(
                origen, f["path"], kind="destino de recuperacion")
            if f["action"] == "delete":
                # El original no existia: restaurar es volver a no existir.
                destino.unlink(missing_ok=True)
                restaurados.append(f["path"])
                continue

            esperado = txn_service.Fingerprint(
                f["original"]["state"], f["original"].get("sha256"),
                f["original"].get("size"))
            respaldo = _resolver_ruta_manifest(
                files_root, f["path"], kind="respaldo del journal")
            if not respaldo.is_file():
                raise OSError("falta el respaldo del journal")
            if not txn_service.fingerprint(respaldo).matches(esperado):
                raise OSError("el respaldo cambio o no coincide con su hash")
            contenido = respaldo.read_bytes()
            if not txn_service.fingerprint_bytes(contenido).matches(esperado):
                raise OSError("el respaldo cambio mientras se estaba leyendo")

            # Escritura atomica: copy2 podia truncar el destino y fallar a mitad.
            txn_service.durable_write(destino, contenido)
            comprobado = txn_service.fingerprint(destino)
            if comprobado.matches(esperado):
                restaurados.append(f["path"])
            else:
                fallidos.append({"path": f["path"],
                                 "reason": "el archivo restaurado no coincide "
                                           "con el original"})
        except OSError as exc:
            fallidos.append({"path": f["path"], "reason": str(exc)})

    estado = RECOVERED if not fallidos else INCOMPLETE
    _anotar_recuperacion(jdir, estado, restaurados, fallidos)

    resultado = {
        "journal": str(jdir), "state": estado, "recovered": True,
        "restored": restaurados, "restored_count": len(restaurados),
        "failed": fallidos,
        "verified_byte_for_byte": not fallidos,
    }
    if fallidos:
        raise RecoveryError(
            f"Se restauraron {len(restaurados)} archivo(s) y {len(fallidos)} "
            "fallaron. Queda intervencion manual.",
            details=resultado)
    return resultado


def _anotar_recuperacion(jdir: Path, estado: str, restaurados: List[str],
                         fallidos: List[Dict[str, str]]) -> None:
    """Deja constancia en el manifiesto: sin esto se podria recuperar dos veces."""
    f = jdir / "manifest.json"
    try:
        datos = json.loads(f.read_text(encoding="utf-8"))
        datos["recovery"] = {
            "status": estado, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "restored": len(restaurados), "failed": len(fallidos),
        }
        from services.txn import durable_write

        durable_write(f, json.dumps(datos, indent=2,
                                    ensure_ascii=False).encode("utf-8"))
    except (OSError, ValueError) as exc:                 # pragma: no cover
        log.warning("No se pudo anotar la recuperacion en %s: %s", jdir, exc)


# ================================================================== purga =====
def _es_journal(d: Path) -> bool:
    """Un directorio nuestro: tiene manifest.json y carpeta files/."""
    return d.is_dir() and (d / "manifest.json").is_file()


def _raiz_segura(active, root: Path) -> Path:
    """Valida la raiz de purga. Fail-closed."""
    from services import txn as txn_service

    root = Path(root).resolve()
    permitida = txn_service.project_backup_root(active).resolve()

    if root != permitida and not safe_paths.is_inside(permitida, root):
        raise UnsafePurgeRoot(
            "La raiz de purga debe estar dentro de la carpeta de backups del "
            "proyecto activo.",
            details={"root": str(root), "allowed": str(permitida)})

    # Cinturon y tirantes: aunque la comprobacion anterior ya lo cubre, estas
    # rutas no se aceptan jamas.
    prohibidas = {Path(root.anchor).resolve(),
                  Path.home().resolve(),
                  Path(active.project_dir).resolve()}
    if root in prohibidas or len(root.parts) <= 2:
        raise UnsafePurgeRoot(
            "Raiz de purga inaceptable: es una raiz de unidad, el directorio "
            "personal o el propio proyecto.",
            details={"root": str(root)})
    return root


def purge_preview(active, *, days: int = DIAS_POR_DEFECTO,
                  max_journals: int = MAXIMO_POR_DEFECTO,
                  root: Optional[Path] = None) -> Dict[str, Any]:
    """Manifiesto de lo que se eliminaria. No borra nada."""
    from services import txn as txn_service

    base = _raiz_segura(active, root or txn_service.project_backup_root(active))
    candidatos, saltados = [], []

    for d in base.iterdir():
        try:
            if d.is_symlink() or (d.exists() and os.path.islink(d)):
                saltados.append({"path": str(d), "reason": "enlace simbolico"})
                continue
            if os.name == "nt" and d.is_dir():
                atributos = os.stat(d, follow_symlinks=False).st_file_attributes
                if atributos & getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    saltados.append({"path": str(d), "reason": "punto de reanalisis"})
                    continue
        except OSError:                                  # pragma: no cover
            saltados.append({"path": str(d), "reason": "no se pudo inspeccionar"})
            continue

        if not _es_journal(d):
            saltados.append({"path": str(d), "reason": "no es un journal nuestro"})
            continue

        try:
            edad = (time.time() - d.stat().st_mtime) / 86400
            manifiesto = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saltados.append({"path": str(d), "reason": "manifiesto ilegible"})
            continue

        candidatos.append({
            "path": str(d), "age_days": round(edad, 1),
            "status": manifiesto.get("status"),
            "tool": manifiesto.get("tool"),
            "mtime": d.stat().st_mtime,
            "pending": manifiesto.get("status") == "open",
        })

    candidatos.sort(key=lambda c: -c["mtime"])
    conservar = {c["path"] for c in candidatos[:CONSERVAR_SIEMPRE]}
    # Un journal pendiente contiene los unicos originales de una transaccion
    # que no se cerro: borrarlo destruiria la unica via de recuperacion.
    conservar |= {c["path"] for c in candidatos if c["pending"]}

    a_borrar = []
    for i, c in enumerate(candidatos):
        if c["path"] in conservar:
            continue
        por_edad = c["age_days"] > days
        por_cantidad = i >= max_journals
        if por_edad or por_cantidad:
            a_borrar.append({**c, "reason": ("antiguedad" if por_edad
                                             else "supera el maximo")})

    return {
        "root": str(base), "policy": {"days": days, "max_journals": max_journals,
                                      "always_keep": CONSERVAR_SIEMPRE},
        "total": len(candidatos), "to_delete": a_borrar,
        "delete_count": len(a_borrar),
        "kept_pending": sorted(c["path"] for c in candidatos if c["pending"]),
        "skipped": saltados,
        "dry_run": True,
        "note": "Nada se ha borrado. Pasa confirm=true para aplicarlo.",
    }


def purge(active, *, days: int = DIAS_POR_DEFECTO,
          max_journals: int = MAXIMO_POR_DEFECTO,
          confirm: bool = False, root: Optional[Path] = None) -> Dict[str, Any]:
    """Aplica la retencion. Sin `confirm` es una vista previa."""
    plan = purge_preview(active, days=days, max_journals=max_journals, root=root)
    if not confirm:
        return plan

    borrados, fallidos = [], []
    for c in plan["to_delete"]:
        d = Path(c["path"])
        try:
            if not _es_journal(d):                       # revalidacion (TOCTOU)
                fallidos.append({"path": str(d),
                                 "reason": "dejo de ser un journal reconocible"})
                continue
            shutil.rmtree(d)
            borrados.append(str(d))
        except OSError as exc:
            fallidos.append({"path": str(d), "reason": str(exc)})

    log.info("Purga de backups: %d eliminado(s), %d fallido(s) en %s",
             len(borrados), len(fallidos), plan["root"])
    return {**plan, "dry_run": False, "deleted": borrados,
            "deleted_count": len(borrados), "failed": fallidos,
            "note": ""}
