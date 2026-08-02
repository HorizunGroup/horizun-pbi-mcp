"""Tools operativas: salud, capacidades, sesion, journals y plan/apply.

Son las que un agente necesita para saber QUE puede hacer antes de intentarlo,
y para recuperarse cuando algo quedo a medias.
"""
from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import branding
from config import get_session, get_settings
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError, ValidationError
from services import dual_mode, operations, planning, project_state
from services import txn as txn_service
from tools._common import guard, guard_mutation


class PurgeFailedError(PowerBIMCPError):
    code = "bulk_apply_failed"


class PurgePartialError(PurgeFailedError):
    code = "bulk_partially_applied"


def _exigir_purga_completa(resultado: Dict[str, Any]) -> Dict[str, Any]:
    fallos = resultado.get("failed") or []
    if not fallos:
        return resultado
    error = PurgePartialError if resultado.get("deleted_count") else PurgeFailedError
    raise error(
        f"No se pudieron eliminar {len(fallos)} journal(s) de backup.",
        details=resultado)

log = get_logger("ops_tools")


def _pbip_activo_seguro():
    """Proyecto activo, o None. No lanza: estas tools son de diagnostico."""
    try:
        return get_session().active_pbip
    except Exception:  # noqa: BLE001 - pragma: no cover
        return None



def _cap_esquema_interno() -> Dict[str, Any]:
    """Validador propio: JSON Schema oficial, offline, documento a documento."""
    from services import pbir_schema

    estado = pbir_schema.estado_cache()
    no_pub = pbir_schema.no_publicados() if estado["ready"] else {}
    return {
        "available": estado["ready"],
        "reason": ("disponible" if estado["ready"] else
                   f"{estado['reason']}. Ejecuta: python scripts/fetch_pbir_schemas.py"),
        "documents": estado.get("expected", 0),
        "unavailable_upstream": sorted(no_pub),
        "note": ("Valida cada documento contra su JSON Schema oficial, sin red. "
                 "No cubre relaciones entre archivos."),
    }


def _cap_validador_oficial() -> Dict[str, Any]:
    """CLI de Microsoft: valida el .Report entero, incluidas relaciones."""
    from services import pbir_schema, report_validator as rv

    estado = rv.estado()
    no_pub = sorted(pbir_schema.no_publicados()) if pbir_schema.estado_cache()["ready"] else []
    return {
        "available": estado["available"],
        "reason": estado["reason"],
        "version": estado["version"],
        "expected_version": estado["expected_version"],
        "install_hint": estado["install_hint"],
        # Lo que de verdad importa saber antes de intentar escribir.
        "can_write_recent_schemas": False,
        "blocked_reason": (
            "Ni el validador interno ni el CLI oficial pueden comprobar "
            f"{len(no_pub)} esquema(s) que Power BI declara y Microsoft no "
            "publica (404). Las escrituras sobre archivos que los declaren se "
            "bloquean con schema_unavailable."),
        "unvalidatable_schemas": no_pub,
        "note": ("Valida el informe completo: objetos de formato por tipo de "
                 "visual, roles, temas y referencias cruzadas."),
    }

def register(mcp) -> None:

    @mcp.tool()
    def pbi_health_check() -> Dict[str, Any]:
        """Estado general del servidor: dependencias, DLLs, sesion y proyecto.

        Solo lectura. Es lo primero que conviene llamar: dice si la capa EN VIVO
        esta disponible, si hay un proyecto .pbip abierto y si algo requiere
        atencion (sesion obsoleta, journals pendientes).
        """
        def _impl():
            from powerbi.clr_bootstrap import diagnostics

            session = get_session()
            settings = get_settings()
            checks: List[Dict[str, Any]] = []

            def add(nombre, ok, detalle, requerido=True):
                checks.append({"check": nombre, "ok": bool(ok),
                               "detail": detalle, "required": requerido})

            v = sys.version_info
            add("python", (v.major, v.minor) >= (3, 10),
                f"{v.major}.{v.minor}.{v.micro}")
            add("platform", platform.system() == "Windows",
                platform.system(), requerido=False)

            libs = settings.libs_dir
            dlls = sorted(p.name for p in libs.glob("*.dll")) if libs.exists() else []
            add("analysis_services_dlls", len(dlls) >= 3, f"{len(dlls)} DLL")

            diag = diagnostics()
            add("clr", bool(diag.get("clr_available")), diag.get("runtime"),
                requerido=False)

            modelo = session.active_model
            if modelo is None:
                add("active_model", False, "sin modelo activo", requerido=False)
                sesion_estado = None
            else:
                from powerbi.desktop_discovery import verify_model

                sesion_estado = verify_model(modelo)
                add("active_model", sesion_estado["status"] == "ok",
                    f"puerto {modelo.port}: {sesion_estado['status']}",
                    requerido=False)

            pbip = session.active_pbip
            add("active_pbip", pbip is not None,
                f"{Path(pbip.pbip_path).name}" if pbip else "sin proyecto activo",
                requerido=False)

            pendientes = []
            if pbip is not None:
                try:
                    root = txn_service.project_backup_root(pbip)
                    pendientes = txn_service.list_journals(root, only_pending=True)
                except Exception as exc:  # noqa: BLE001
                    log.debug("No se pudieron listar journals: %s", exc)
            add("pending_journals", not pendientes,
                f"{len(pendientes)} pendiente(s)", requerido=False)

            obligatorios_fallando = [c["check"] for c in checks
                                     if c["required"] and not c["ok"]]
            avisos = [c["check"] for c in checks if not c["required"] and not c["ok"]]
            return {
                "server": branding.identity(),
                "healthy": not obligatorios_fallando,
                "checks": checks,
                "failing_required": obligatorios_fallando,
                "warnings": avisos,
                "pending_journals": pendientes,
                "session_status": sesion_estado,
            }
        return guard(_impl)

    @mcp.tool()
    def pbi_capabilities() -> Dict[str, Any]:
        """Que puede hacerse AHORA MISMO, y que no, con el motivo.

        Un agente deberia consultarla antes de planificar: dice si la capa en
        vivo esta disponible, si se puede escribir en el .pbip, y que
        capacidades dependen de la version del motor.
        """
        def _impl():
            session = get_session()
            settings = get_settings()
            modelo = session.active_model
            pbip = session.active_pbip

            libs = settings.libs_dir
            hay_dlls = libs.exists() and len(list(libs.glob("*.dll"))) >= 3

            live_ok, live_motivo = False, "sin modelo activo"
            if modelo is not None:
                from powerbi.desktop_discovery import verify_model

                estado = verify_model(modelo)
                live_ok = estado["status"] == "ok" and hay_dlls
                live_motivo = (estado.get("reason") if estado["status"] != "ok"
                               else ("faltan las DLLs de Analysis Services"
                                     if not hay_dlls else "disponible"))

            pbir_ok, pbir_motivo = False, "sin proyecto .pbip activo"
            estado_proyecto = None
            if pbip is not None:
                estado_proyecto = project_state.detect(pbip)
                if not pbip.has_pbir:
                    pbir_motivo = "el proyecto no usa formato PBIR"
                elif not estado_proyecto.writable:
                    pbir_motivo = (
                        f"Power BI Desktop: {estado_proyecto.state}. La politica "
                        "estricta solo permite escribir con el proyecto cerrado.")
                else:
                    pbir_ok, pbir_motivo = True, "disponible"

            tmdl_ok = bool(pbip and pbip.has_tmdl and
                           (estado_proyecto.writable if estado_proyecto else False))

            return {
                "server": branding.identity(),
                "capabilities": {
                    "dax_query": {"available": live_ok, "reason": live_motivo},
                    "model_read_live": {"available": live_ok, "reason": live_motivo},
                    "model_write_live": {"available": live_ok, "reason": live_motivo},
                    "model_read_tmdl": {
                        "available": bool(pbip and pbip.has_tmdl),
                        "reason": "disponible" if (pbip and pbip.has_tmdl)
                        else "sin TMDL en el proyecto"},
                    "model_write_tmdl": {
                        "available": tmdl_ok,
                        "reason": "disponible" if tmdl_ok else pbir_motivo},
                    "report_read_pbir": {
                        "available": bool(pbip and pbip.has_pbir),
                        "reason": "disponible" if (pbip and pbip.has_pbir)
                        else "sin PBIR"},
                    "report_write_pbir": {"available": pbir_ok, "reason": pbir_motivo},
                    "internal_schema_validator": _cap_esquema_interno(),
                    "microsoft_report_validator": _cap_validador_oficial(),
                    "dual_mode_both": {
                        "available": False,
                        "reason": ("Deshabilitado: 'live' necesita Power BI Desktop "
                                   "abierto y 'pbip' lo necesita cerrado. Elige uno."),
                        "unsupported": True},
                    "cloud_fabric": {
                        "available": False,
                        "reason": "No implementado en esta version (sin autenticacion).",
                        "unsupported": True},
                },
                "modes": {"live": live_ok, "pbip": tmdl_ok or pbir_ok, "both": False},
                "project_state": estado_proyecto.to_dict() if estado_proyecto else None,
                "planned_operations": planning.operaciones_disponibles(),
            }
        return guard(_impl)

    @mcp.tool()
    def pbi_session_info() -> Dict[str, Any]:
        """Detalle de la sesion: modelo activo, proyecto activo y su frescura.

        Distingue una sesion valida de una obsoleta (`stale`) o de otra que
        ocupo el mismo puerto (`mismatch`).
        """
        def _impl():
            session = get_session()
            modelo = session.active_model
            pbip = session.active_pbip

            info_modelo = None
            if modelo is not None:
                from powerbi.desktop_discovery import verify_model

                estado = verify_model(modelo)
                info_modelo = {**modelo.to_dict(), "verification": estado["status"],
                               "reason": estado.get("reason")}

            info_pbip = None
            if pbip is not None:
                estado_proyecto = project_state.detect(pbip)
                info_pbip = {**pbip.to_dict(),
                             "desktop_state": estado_proyecto.to_dict(),
                             "writable": estado_proyecto.writable}

            return {
                "active_model": info_modelo,
                "active_pbip": info_pbip,
                "outputs_dir": str(get_settings().outputs_dir),
                "live_plans": operations.registro().planes_vivos(),
            }
        return guard(_impl)

    @mcp.tool()
    def pbi_list_pending_journals(only_pending: bool = True) -> Dict[str, Any]:
        """Lista los journals del proyecto activo.

        Un journal `pending` es el de una operacion que ni se confirmo ni se
        revirtio (el proceso murio en medio). Contiene los originales.
        `only_pending=false` lista tambien los ya cerrados.
        """
        def _impl():
            pbip = get_session().require_active_pbip()
            root = txn_service.project_backup_root(pbip)
            journals = txn_service.list_journals(root, only_pending=only_pending)
            atencion = [j for j in journals if j.get("needs_attention")]
            return {"backup_root": str(root), "count": len(journals),
                    "journals": journals, "needs_attention": len(atencion),
                    "warnings": ([f"{len(atencion)} journal(s) requieren revision"]
                                 if atencion else [])}
        return guard(_impl)

    @mcp.tool()
    def pbi_inspect_journal(journal: str) -> Dict[str, Any]:
        """Inspecciona un journal y lo compara con el estado ACTUAL del proyecto.

        Solo lectura: no restaura nada. Por cada archivo dice si sigue como el
        original, si hay respaldo disponible y cual fue su desenlace.
        `journal`: ruta devuelta por pbi_list_pending_journals.
        """
        def _impl():
            pbip = get_session().require_active_pbip()
            root = txn_service.project_backup_root(pbip)
            jdir = Path(journal)
            # El journal debe pertenecer a la raiz de backups de ESTE proyecto.
            from services import paths as safe_paths

            if not safe_paths.is_inside(root, jdir):
                raise ValidationError(
                    "El journal indicado no pertenece al proyecto activo.",
                    details={"journal": str(jdir), "backup_root": str(root)})
            return txn_service.read_journal(jdir)
        return guard(_impl)

    @mcp.tool()
    def pbi_recover_from_journal(journal: str, confirm: bool = False,
                                 force_conflict: bool = False,
                                 request_id: str = "") -> Dict[str, Any]:
        """Restaura los originales guardados en un journal. DESTRUCTIVA.

        Sin `confirm` devuelve la VISTA PREVIA: que archivos se restaurarian,
        cual es su estado actual y si alguien los cambio despues.

        Estados: `recoverable`, `recovered`, `conflict`, `incomplete`,
        `corrupted`. Si un archivo cambio despues de la transaccion, se rechaza
        con `recovery_conflict` en vez de pisar ese trabajo; `force_conflict`
        lo aplica de todas formas.

        Cada archivo se verifica byte a byte tras restaurarlo, y se recrean los
        directorios padre que hubieran desaparecido.
        """
        def _impl():
            from services import recovery

            return recovery.recover(get_session().require_active_pbip(),
                                    Path(journal), confirm=confirm,
                                    force_conflict=force_conflict)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_purge_backups(days: int = 30, max_journals: int = 50,
                          confirm: bool = False,
                          request_id: str = "") -> Dict[str, Any]:
        """Aplica la politica de retencion a los backups. DESTRUCTIVA.

        Sin `confirm` devuelve el MANIFIESTO de lo que se eliminaria, sin tocar
        nada. Solo se borran directorios de journal reconocibles (con su
        `manifest.json`) dentro de la carpeta de backups del proyecto activo:
        nunca un archivo suelto, ni un enlace simbolico, ni una raiz amplia.

        Se conserva siempre el journal mas reciente y TODOS los pendientes:
        un journal pendiente guarda los unicos originales de una transaccion
        que no llego a cerrarse.
        """
        def _impl():
            from services import recovery

            resultado = recovery.purge(
                get_session().require_active_pbip(), days=days,
                max_journals=max_journals, confirm=confirm)
            return _exigir_purga_completa(resultado)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_plan_change(operation: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Calcula un PLAN sin aplicar nada, y devuelve un `plan_token`.

        `operation`: una de las que lista pbi_capabilities en
        `planned_operations`. `arguments`: los mismos que aceptaria la tool.

        El plan incluye el diff por archivo y una huella del estado sobre el que
        se calculo. Si el proyecto cambia despues, pbi_apply_plan lo rechaza.
        """
        def _impl():
            return planning.plan(get_session(), operation, arguments or {})
        return guard(_impl)

    @mcp.tool()
    def pbi_apply_plan(plan_token: str, confirm: bool = True,
                       expected_operation: str = "", request_id: str = "") -> Dict[str, Any]:
        """Aplica un plan calculado con pbi_plan_change o con un dry_run.

        Verifica que el proyecto siga en el estado sobre el que se planifico; si
        cambio, rechaza el plan en vez de aplicar algo distinto de lo aprobado.

        `expected_operation` es opcional: si lo indicas, el plan solo se aplica
        si fue generado para esa operacion (`plan_operation_mismatch` si no).
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Pasa confirm=true para aplicar el plan.")
            return planning.apply(get_session(), plan_token, expected_operation)
        return guard_mutation(_impl)
