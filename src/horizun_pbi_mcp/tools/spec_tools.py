"""Tools del constructor declarativo de paginas (Macrofase D).

Flujo: building blocks -> spec -> validar -> preview -> diff -> plan -> apply
-> verificar. El plan token garantiza que se aplica lo mismo que se aprobo.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session, get_settings
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import operations, page_spec, page_update, planning
from horizun_pbi_mcp.tools._common import guard, guard_mutation
from horizun_pbi_mcp.tools.visual_tools import _model_data
from horizun_pbi_mcp.utils.file_utils import atomic_write_text, timestamp


def _session():
    return get_session()


def _active():
    return get_session().require_active_pbip()


def _compilar(spec: Dict[str, Any], seed: str = ""):
    return page_spec.compile_spec(_active(), spec, _model_data(), seed=seed)


def register(mcp) -> None:

    @mcp.tool()
    def pbi_list_page_presets() -> Dict[str, Any]:
        """Presets de pagina disponibles, con los bloques que compone cada uno.

        Un preset describe la INTENCION de la pagina (KPIs arriba, grafico
        protagonista, detalle abajo). Los campos concretos los eliges tu.
        """
        def _impl():
            presets = page_spec.list_presets()
            return {"count": len(presets), "presets": presets,
                    "schema_version": page_spec.SCHEMA_VERSION}
        return guard(_impl)

    @mcp.tool()
    def pbi_generate_page_spec(page_name: str, preset: str = "executive",
                               measures: Optional[List[str]] = None,
                               category: Optional[str] = None,
                               width: int = 1280, height: int = 720) -> Dict[str, Any]:
        """Genera un borrador de spec a partir de un preset y unos campos.

        `preset`: executive | financial | sales | operations | evm | detail.
        `measures`: medidas para los KPIs y los graficos. `category`: columna
        para el eje de los graficos. El resultado es un spec editable: revisalo
        y pasalo por pbi_validate_page_spec.
        """
        def _impl():
            if preset not in page_spec.PRESETS:
                raise ValidationError(
                    f"Preset desconocido: '{preset}'.",
                    details={"available": sorted(page_spec.PRESETS)})
            definicion = page_spec.PRESETS[preset]
            medidas = list(measures or [])
            visuals: List[Dict[str, Any]] = []
            avisos: List[str] = []

            for bloque in definicion["blocks"]:
                for i in range(bloque["count"]):
                    if bloque["role"] == "kpi":
                        if i >= len(medidas):
                            avisos.append(
                                f"El preset '{preset}' pide {bloque['count']} KPIs y "
                                f"solo hay {len(medidas)} medidas: se omiten los "
                                "restantes.")
                            break
                        visuals.append({"type": bloque["type"], "title": medidas[i],
                                        "fields": {"values": [f"[{medidas[i]}]"]}})
                    else:
                        if not medidas or not category:
                            avisos.append(
                                f"El bloque '{bloque['role']}' necesita una medida y "
                                "una categoria; se omite.")
                            break
                        visuals.append({
                            "type": bloque["type"],
                            "title": f"{medidas[0]} por {category.split('[')[-1].rstrip(']')}",
                            "fields": {"category": category,
                                       "values": [f"[{medidas[0]}]"]}})

            spec = {
                "schema_version": page_spec.SCHEMA_VERSION,
                "page": {"name": page_name, "width": width, "height": height},
                "layout": {"preset": preset, "gap": 16},
                "visuals": visuals,
                "filters": [],
                "interactions": [],
            }
            return {"spec": spec, "preset": preset,
                    "description": definicion["description"],
                    "visual_count": len(visuals), "warnings": avisos}
        return guard(_impl)

    @mcp.tool()
    def pbi_validate_page_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
        """Valida un spec: esquema, referencias contra el modelo y geometria.

        Los errores traen su JSON path (`$.visuals[2].fields.values[0]`) para
        que se puedan corregir sin adivinar. No escribe nada.
        """
        def _impl():
            errores = page_spec.validate_schema(spec)
            if errores:
                return {"valid": False, "stage": "schema", "errors": errores,
                        "schema_version": page_spec.SCHEMA_VERSION}
            try:
                page_spec.assert_soportado(spec)
            except page_spec.UnsupportedSpecFeature as exc:
                return {"valid": False, "stage": "unsupported_feature",
                        "error": exc.code, "message": exc.message,
                        **exc.details}
            refs = page_spec.resolve_references(spec, _model_data())
            if refs["errors"]:
                return {"valid": False, "stage": "references",
                        "errors": refs["errors"], "warnings": refs["warnings"]}
            compilado = _compilar(spec)
            return {"valid": True, "stage": "complete",
                    "page_name": compilado["page_name"],
                    "canvas": compilado["canvas"],
                    "visual_count": len(compilado["visuals"]),
                    "references": compilado["references"],
                    "layout_issues": compilado["layout_issues"]["issue_count"],
                    "warnings": compilado["warnings"]}
        return guard(_impl)

    @mcp.tool()
    def pbi_preview_page_spec(spec: Dict[str, Any], seed: str = "") -> Dict[str, Any]:
        """Maqueta HTML del spec con las posiciones FINALES. No escribe al .pbip.

        Lo que muestra el preview es exactamente lo que se escribiria: tipos,
        titulos, campos, tamanos y posiciones salen del mismo compilado que usa
        la aplicacion.
        """
        def _impl():
            compilado = _compilar(spec, seed)
            html = page_spec.preview(_active(), compilado)
            destino = get_settings().outputs_dir / f"spec_preview_{timestamp()}.html"
            atomic_write_text(destino, html)
            return {"output_path": str(destino),
                    "page_name": compilado["page_name"],
                    "visual_count": len(compilado["visuals"]),
                    "canvas": compilado["canvas"],
                    "positions": compilado["positions"],
                    "layout_issues": compilado["layout_issues"],
                    "warnings": compilado["warnings"]}
        return guard(_impl)

    @mcp.tool()
    def pbi_diff_page_spec(spec: Dict[str, Any],
                           page: Optional[str] = None) -> Dict[str, Any]:
        """Compara el spec con una pagina existente antes de aplicarlo.

        Dice que visuales se anadirian, cuales sobrarian y cuantos quedan
        igual. Si la pagina no existe, informa que se creara.
        """
        def _impl():
            compilado = _compilar(spec)
            return page_spec.diff_against_page(_active(), compilado, page)
        return guard(_impl)

    @mcp.tool()
    def pbi_apply_page_spec(spec: Dict[str, Any], seed: str = "",
                            dry_run: bool = False, page: str = "",
                            sync_mode: str = "merge",
                            request_id: str = "") -> Dict[str, Any]:
        """Materializa el spec como una pagina PBIR, en UNA transaccion.

        Cuatro desenlaces explicitos: `create` (no existe), `update` (existe y
        el spec cambia algo), `no_change` (ya coincide) y `conflict` (el nombre
        no identifica una sola pagina).

        `page`: id o nombre visible de la pagina a actualizar. Si se omite, se
        usa el nombre del spec. Al actualizar se CONSERVAN el id de la pagina y
        el de cada visual que siga representando lo mismo.

        `sync_mode`: `merge` (por defecto) anade y actualiza pero no borra lo
        que el spec no menciona; `replace` ademas elimina los visuales
        ausentes. El defecto es conservador para que un spec parcial no pueda
        vaciar una pagina por omision.

        `interactions`: que le hace un visual a otro al seleccionar en el.
        Cada visual del spec se puede senalar por su POSICION en `visuals`
        (0, 1, 2...), por el `id` que se le ponga en el spec, o por su titulo;
        no hace falta conocer los ids finales, que se generan aqui. Tipos:
        `Default`, `DataFilter`, `HighlightFilter`, `NoFilter` (tambien valen
        `filter`, `highlight` y `none`).

        `dry_run=true` devuelve un plan con `plan_token` y no escribe nada;
        aplicalo despues con pbi_apply_plan.
        """
        def _impl():
            active = _active()
            compilado = _compilar(spec, seed)

            if dry_run:
                # Se planifica por el registro comun para que el sobre lleve
                # `affected_files` con los bytes finales. Antes guardaba solo el
                # spec y pbi_apply_plan moria buscando 'files'.
                resultado = planning.plan(
                    _session(), "apply_page_spec",
                    {"spec": spec, "seed": seed, "page": page or None,
                     "sync_mode": sync_mode, "_model_data": _model_data()})
                meta = resultado.get("meta", {})
                return {"planned": True,
                        "plan_token": resultado["plan_token"],
                        "plan_version": resultado["plan_version"],
                        "expires_at": resultado["expires_at"],
                        "page_name": meta.get("page_name", compilado["page_name"]),
                        "visual_count": len(compilado["visuals"]),
                        "canvas": compilado["canvas"],
                        "layout_issues": compilado["layout_issues"],
                        "warnings": compilado["warnings"],
                        "changes": resultado["changes"],
                        "files": resultado["files"],
                        "note": ("Nada se ha escrito. Revisa el diff y aplica "
                                 "con pbi_apply_plan(plan_token).")}

            # El camino directo tambien pasa por el mismo planificador: asi
            # `update` no puede volver a convertirse en una creacion silenciosa
            # segun por donde se entre.
            resultado = page_spec.apply_spec(
                active, compilado, page=page or None, sync_mode=sync_mode)
            if resultado.get("change") != page_update.NO_CHANGE:
                resultado["validation"] = page_spec.validate_generated_page(
                    active, resultado["page_id"], _model_data())
            resultado["warnings"] = compilado["warnings"]
            return resultado
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_validate_generated_page(page: str) -> Dict[str, Any]:
        """Verifica una pagina YA escrita: referencias rotas y geometria.

        Se usa despues de aplicar un spec para comprobar que el resultado es
        valido de verdad, no solo que la escritura no fallo.
        """
        return guard(lambda: page_spec.validate_generated_page(
            _active(), page, _model_data()))
