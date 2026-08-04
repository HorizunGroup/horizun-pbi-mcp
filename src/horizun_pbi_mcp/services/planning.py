"""Plan / apply: calcular un cambio sin aplicarlo, aprobarlo y ejecutarlo.

El flujo es siempre el mismo:

    plan(operacion, argumentos)
        -> valida TODO
        -> calcula el contenido final de cada archivo, en memoria
        -> devuelve el diff y un `plan_token` que captura el estado de partida

    apply(plan_token)
        -> valida el sobre (version, caducidad, integridad, proyecto)
        -> vuelve a calcular la huella del estado
        -> si cambio, RECHAZA: el plan aprobado ya no describe lo que pasaria
        -> despacha por `operation` y escribe en una sola transaccion

Solo se registran aqui operaciones que escriben EN DISCO (`pbip`). Las de la
capa en vivo no pasan por un plan token porque el modelo en memoria de Power BI
Desktop puede cambiar por debajo sin que se note.

Todos los planes comparten el sobre de `services.plan_contract`. El aplicador
**despacha por `operation`**: no da por hecho que dos operaciones se apliquen
igual solo porque ambas escriben archivos.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import operations, plan_contract, project_state
from horizun_pbi_mcp.services import txn as txn_service

log = get_logger("planning")

#: operacion -> {"planner", "applier", "description", "target"}
_REGISTRO: Dict[str, Dict[str, Any]] = {}


def registrar(nombre: str, planner: Callable[..., Dict[str, Any]],
              descripcion: str,
              applier: Optional[Callable[..., Dict[str, Any]]] = None) -> None:
    """Registra un planificador.

    `planner(session, active, args)` debe devolver ``{"files": {ruta: contenido}}``
    donde el contenido es texto (TMDL) o un objeto JSON (PBIR). Opcionalmente
    ``meta``, ``warnings`` y ``ensure_dirs``.

    `applier` solo hace falta si la operacion no se aplica escribiendo los
    archivos del sobre. Por defecto se usa `aplicar_archivos_del_sobre`.
    """
    _REGISTRO[nombre] = {"planner": planner, "description": descripcion,
                         "target": "pbip",
                         "applier": applier or aplicar_archivos_del_sobre}


def operaciones_disponibles() -> List[Dict[str, str]]:
    return [{"operation": k, "description": v["description"], "target": v["target"]}
            for k, v in sorted(_REGISTRO.items())]


def _kind_de(contenido: Any) -> str:
    return "text" if isinstance(contenido, str) else "json"


def _diff_de(path: Path, nuevo: str) -> Dict[str, Any]:
    """Diff legible entre el archivo actual y el contenido propuesto."""
    if path.exists():
        actual = path.read_text(encoding="utf-8-sig").splitlines()
        estado = "modified"
    else:
        actual, estado = [], "created"
    propuesto = nuevo.splitlines()
    if actual == propuesto:
        return {"path": str(path), "change": "unchanged", "diff": []}
    lineas = list(difflib.unified_diff(actual, propuesto, lineterm="",
                                       fromfile="actual", tofile="propuesto", n=2))
    return {"path": str(path), "change": estado,
            "added": sum(1 for l in lineas if l.startswith("+") and not l.startswith("+++")),
            "removed": sum(1 for l in lineas if l.startswith("-") and not l.startswith("---")),
            "diff": lineas[:120]}


def plan(session, operation: str, arguments: Dict[str, Any],
         request_id: Optional[str] = None) -> Dict[str, Any]:
    """Calcula el plan de una operacion sin escribir nada."""
    entrada = _REGISTRO.get(operation)
    if entrada is None:
        raise ValidationError(
            f"Operacion no planificable: '{operation}'.",
            details={"available": [o["operation"] for o in operaciones_disponibles()]})

    active = session.require_active_pbip()
    resultado = entrada["planner"](session, active, arguments)
    archivos: Dict[Any, Any] = resultado["files"]

    afectados, diffs = [], []
    for ruta, contenido in archivos.items():
        p = Path(ruta)
        kind = _kind_de(contenido)
        af = plan_contract.archivo_afectado(
            p, contenido, kind=kind,
            estado_previo="present" if p.exists() else "absent")
        afectados.append(af)
        diffs.append(_diff_de(p, plan_contract.contenido_como_texto(af)))

    con_cambio = [d for d in diffs if d["change"] != "unchanged"]
    borrados = [str(x) for x in resultado.get("deletes", [])]
    # Las rutas a borrar entran en la huella: si alguien las toca entre
    # planificar y aplicar, el plan queda obsoleto igual que si cambiara un
    # archivo que ibamos a escribir.
    huella = operations.state_fingerprint_of(
        [Path(r) for r in archivos] + [Path(x) for x in borrados])

    sobre = plan_contract.build_envelope(
        operation=operation,
        project_root=_raiz_de(active),
        payload=arguments,
        affected_files=afectados,
        preconditions={"state_fingerprint": huella},
        expected_effects={
            "changes": len(con_cambio) + len(borrados),
            "files_written": [d["path"] for d in con_cambio],
            "files_deleted": borrados,
            "meta": resultado.get("meta", {}),
            "ensure_dirs": [str(x) for x in resultado.get("ensure_dirs", [])],
        },
        request_id=request_id)

    p = operations.registro().crear_plan(operation, arguments, huella, sobre)

    return {
        "planned": True,
        "deletes": borrados,
        "plan_token": p.plan_token,
        "plan_version": plan_contract.PLAN_VERSION,
        "operation": operation,
        "expires_at": sobre["expires_at"],
        "changes": len(con_cambio),
        "files": diffs,
        "state_fingerprint": huella,
        "meta": resultado.get("meta", {}),
        "warnings": resultado.get("warnings", []),
        "note": ("Nada se ha escrito. Aplica con pbi_apply_plan(plan_token). "
                 "Si el proyecto cambia antes, el plan se rechazara."),
    }


def _raiz_de(active) -> str:
    """Ruta que identifica al proyecto activo."""
    for attr in ("pbip_path", "project_path", "root", "path"):
        valor = getattr(active, attr, None)
        if valor:
            return str(valor)
    return str(active)


def aplicar_archivos_del_sobre(session, active, sobre: Dict[str, Any],
                               ) -> Dict[str, Any]:
    """Aplicador por defecto: escribe `affected_files` en UNA transaccion."""
    operacion = sobre["operation"]
    cambiantes: List[Dict[str, Any]] = []
    for entrada in sobre["affected_files"]:
        ruta = Path(entrada["path"])
        texto = plan_contract.contenido_como_texto(entrada)
        if not ruta.exists() or ruta.read_text(encoding="utf-8-sig") != texto:
            cambiantes.append(entrada)

    hay_borrados = bool(sobre.get("expected_effects", {}).get("files_deleted"))
    if not cambiantes and not hay_borrados:
        return {"applied": 0, "status": "no_change", "operation": operacion,
                "note": "El plan no implicaba ningun cambio real."}

    borrados = [Path(x) for x in
                sobre.get("expected_effects", {}).get("files_deleted", [])
                if Path(x).exists()]
    destinos = [Path(e["path"]) for e in cambiantes] + borrados
    cm = txn_service.project_transaction(
        active, destinos, tool=f"pbi_apply_plan({operacion})")
    with cm as t:
        for entrada in cambiantes:
            ruta = Path(entrada["path"])
            if entrada["kind"] == "json":
                t.write_json(ruta, entrada["content"])
            else:
                t.write_text(ruta, entrada["content"])
        for ruta in borrados:
            t.delete(ruta)
            # La carpeta del visual sin visual.json es un informe invalido
            # para el validador oficial: se retira dentro de la transaccion.
            try:
                if ruta.parent.exists() and not any(ruta.parent.iterdir()):
                    ruta.parent.rmdir()
            except OSError:                           # pragma: no cover
                pass
        for d in sobre.get("expected_effects", {}).get("ensure_dirs", []):
            t.ensure_directory(Path(d))

    return {"applied": len(cambiantes), "status": "applied", "operation": operacion,
            "files": [str(x) for x in destinos],
            "backup": cm.result["journal"], "transaction": cm.result,
            "meta": sobre.get("expected_effects", {}).get("meta", {})}


def apply(session, plan_token: str,
          expected_operation: str = "") -> Dict[str, Any]:
    """Aplica un plan previamente calculado, si sigue siendo valido."""
    active = session.require_active_pbip()
    registro = operations.registro()

    sobre = registro.plan_por_token(plan_token)
    if sobre is None:
        raise operations.PlanNotFoundError(
            f"El plan '{plan_token}' no existe o expiro. Vuelve a generarlo.",
            details={"plan_token": plan_token})

    # -- el sobre, antes de mirar el disco -----------------------------------
    plan_contract.validate_envelope(sobre)
    plan_contract.assert_operacion(sobre, expected_operation)
    plan_contract.assert_no_expirado(sobre)
    plan_contract.assert_payload_integro(sobre)
    plan_contract.assert_mismo_proyecto(sobre, _raiz_de(active))

    entrada = _REGISTRO.get(sobre["operation"])
    if entrada is None:
        raise ValidationError(
            f"Este servidor ya no sabe aplicar la operacion "
            f"'{sobre['operation']}'. Genera el plan de nuevo.",
            details={"operation": sobre["operation"],
                     "available": [o["operation"] for o in operaciones_disponibles()]})

    # -- el disco: nada pudo cambiar desde que se aprobo ---------------------
    huella_actual = operations.state_fingerprint_of(plan_contract.rutas(sobre))
    registro.obtener_plan(plan_token, huella_actual)          # valida frescura

    project_state.assert_writable(
        active, operation=f"Aplicar plan {sobre['operation']}")

    resultado = entrada["applier"](session, active, sobre)
    registro.consumir_plan(plan_token)
    resultado.setdefault("plan_version", plan_contract.PLAN_VERSION)
    return resultado


# ------------------------------------------------------------ planificadores ---
def _planificar_visibilidad(session, active, args: Dict[str, Any]) -> Dict[str, Any]:
    """Plan de `hide_columns` en modo pbip."""
    from horizun_pbi_mcp.pbip import model_edit

    columnas = args.get("columns")
    if columnas is None and args.get("table") and args.get("column"):
        columnas = [{"table": args["table"], "column": args["column"]}]
    if not columnas:
        raise ValidationError("Se requiere 'columns' (o 'table' y 'column').")
    hidden = bool(args.get("hidden", True))

    planificado = model_edit.plan_columns_hidden_pbip(active, columnas, hidden)
    archivos: Dict[Path, str] = {}
    detalle = []
    for datos in planificado["por_archivo"].values():
        lineas = datos["lines"]
        for e in datos["entries"]:
            lineas, cambio = model_edit._aplicar_visibilidad(  # noqa: SLF001
                lineas, e["column"], hidden)
            detalle.append({"table": e["table"], "column": e["column"],
                            "changed": cambio})
        texto = "\n".join(lineas)
        if not texto.endswith("\n"):
            texto += "\n"
        archivos[datos["path"]] = texto
    return {"files": archivos, "meta": {"columns": detalle, "hidden": hidden}}


def _planificar_medida(session, active, args: Dict[str, Any]) -> Dict[str, Any]:
    """Plan de `create_measure` en modo pbip."""
    from horizun_pbi_mcp.pbip import tmdl_writer

    for obligatorio in ("table", "name", "expression"):
        if not args.get(obligatorio):
            raise ValidationError(f"Falta '{obligatorio}'.")

    texto, ruta, accion = tmdl_writer.render_measure_change(
        active, args["table"], args["name"], args["expression"],
        args.get("format_string"), args.get("description"),
        args.get("display_folder"), bool(args.get("overwrite", False)),
        args.get("data_category"))
    return {"files": {ruta: texto},
            "meta": {"table": args["table"], "measure": args["name"],
                     "action": accion}}


def _planificar_page_spec(session, active, args: Dict[str, Any]) -> Dict[str, Any]:
    """Plan de `apply_page_spec`: materializa page.json, pages.json y visuales.

    Antes este plan guardaba el spec y nada mas, asi que el aplicador generico
    reventaba al buscar `files`. Ahora se compila y se materializa aqui: el
    sobre describe exactamente los bytes que se escribiran.
    """
    from horizun_pbi_mcp.pbip import pbir_writer
    from horizun_pbi_mcp.services import page_spec

    spec = args.get("spec")
    if not isinstance(spec, dict):
        raise ValidationError("Se requiere 'spec' (objeto).")
    seed = args.get("seed") or ""

    model_data = args.get("_model_data")
    compilado = page_spec.compile_spec(active, spec, model_data, seed=seed)

    # Antes esto llamaba siempre a plan_page_with_visuals, que ante una pagina
    # ya existente no hacia NADA y decia que todo habia ido bien. Ahora se
    # despacha por desenlace: create / update / no_change / conflict.
    from horizun_pbi_mcp.services import page_update

    plan = page_update.planificar(
        active, compilado, page=args.get("page"),
        sync_mode=args.get("sync_mode") or page_update.MERGE)

    return {"files": plan["files"],
            "deletes": plan["deletes"],
            "ensure_dirs": plan["ensure_dirs"],
            "warnings": compilado["warnings"],
            "meta": {"page_name": compilado["page_name"],
                     "page_id": plan["page_id"],
                     "change": plan["change"],
                     "sync_mode": plan["sync_mode"],
                     "visual_count": len(compilado["visuals"]),
                     "canvas": compilado["canvas"],
                     "layout_issues": compilado["layout_issues"],
                     "added": plan["added"], "updated": plan["updated"],
                     "kept": plan["kept"], "removed": plan.get("removed", []),
                     "not_removed": plan.get("not_removed", []),
                     "summary": page_update.resumen(plan)}}


def registrar_planificadores_por_defecto() -> None:
    registrar("hide_columns",
              _planificar_visibilidad,
              "Ocultar o mostrar columnas en los archivos TMDL (mode=pbip).")
    registrar("create_measure",
              _planificar_medida,
              "Crear o reemplazar una medida DAX en el TMDL (mode=pbip).")
    registrar("apply_page_spec",
              _planificar_page_spec,
              "Materializar un page spec como pagina PBIR completa.")


registrar_planificadores_por_defecto()
