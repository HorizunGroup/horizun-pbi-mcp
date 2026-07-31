"""Tools de auditoria integral y correcciones seleccionables (Macrofase E)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config import get_session, get_settings
from powerbi.errors import ValidationError
from services import report_audit
from tools._common import guard, guard_mutation
from tools.visual_tools import _model_data
from utils.file_utils import atomic_write_text, timestamp


def _active():
    return get_session().require_active_pbip()


def register(mcp) -> None:

    @mcp.tool()
    def pbi_audit_project(rules: Optional[List[str]] = None,
                          min_severity: str = "info",
                          formats: Optional[List[str]] = None) -> Dict[str, Any]:
        """Auditoria integral: modelo semantico + informe + layout.

        Devuelve puntaje global y por dominio, resumen ejecutivo, hallazgos
        priorizados con evidencia y recomendacion, y que reglas tienen
        correccion automatica.

        `formats`: ['markdown','html'] escribe tambien esos informes en
        outputs/ y devuelve sus rutas. `rules` y `min_severity` acotan.
        """
        def _impl():
            active = _active()
            resultado = report_audit.audit_project(
                active, _model_data(), rules=rules, min_severity=min_severity)

            salidas = {}
            for fmt in (formats or []):
                f = fmt.lower()
                if f not in ("markdown", "md", "html", "json"):
                    raise ValidationError(
                        f"Formato no soportado: '{fmt}'. Usa markdown|html|json.")
                sello = timestamp()
                if f in ("markdown", "md"):
                    ruta = get_settings().outputs_dir / f"audit_{sello}.md"
                    atomic_write_text(ruta, report_audit.to_markdown(resultado))
                    salidas["markdown"] = str(ruta)
                elif f == "html":
                    ruta = get_settings().outputs_dir / f"audit_{sello}.html"
                    atomic_write_text(ruta, report_audit.to_html(resultado))
                    salidas["html"] = str(ruta)
                else:
                    ruta = get_settings().outputs_dir / f"audit_{sello}.json"
                    atomic_write_text(ruta, json.dumps(
                        resultado, indent=2, ensure_ascii=False, default=str))
                    salidas["json"] = str(ruta)
            if salidas:
                resultado["outputs"] = salidas
            return resultado
        return guard(_impl)

    @mcp.tool()
    def pbi_audit_report_only() -> Dict[str, Any]:
        """Audita solo el informe PBIR (sin las reglas del modelo semantico).

        Cubre paginas vacias, visuales sin titulo, campos rotos, duplicados,
        tamanos de lienzo inconsistentes y la geometria de cada pagina.
        """
        return guard(lambda: report_audit.audit_report(_active(), _model_data()))

    @mcp.tool()
    def pbi_plan_audit_fixes(rules: List[str],
                             objects: Optional[List[str]] = None
                             ) -> Dict[str, Any]:
        """Planifica correcciones para reglas CONCRETAS. No escribe nada.

        No existe "arreglar todo": hay que indicar `rules` explicitamente.
        `objects` acota mas todavia (ids de visual o de pagina). Devuelve las
        acciones exactas que se aplicarian, con su motivo.
        """
        def _impl():
            active = _active()
            auditoria = report_audit.audit_project(active, _model_data())
            return report_audit.plan_fixes(active, auditoria, rules, objects)
        return guard(_impl)

    @mcp.tool()
    def pbi_apply_audit_fixes(actions: List[Dict[str, Any]],
                              confirm: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Aplica las acciones devueltas por pbi_plan_audit_fixes.

        Requiere confirm=true. Cada accion se aplica por su propia via segura
        (transaccion, verificacion y rollback); si una falla, se reporta sin
        detener las demas y sin ocultarlo.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Pasa confirm=true para aplicar las correcciones.")
            if not actions:
                raise ValidationError("No se recibio ninguna accion.")
            return report_audit.apply_fixes(_active(), actions)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_list_autofix_rules() -> Dict[str, Any]:
        """Reglas que tienen correccion automatica, y en que consiste cada una."""
        def _impl():
            return {"count": len(report_audit.AUTOFIXES),
                    "autofixes": [{"rule": k, **v}
                                  for k, v in sorted(report_audit.AUTOFIXES.items())]}
        return guard(_impl)
