"""Ediciones de modelo en archivos TMDL (modo pbip): ocultar columnas,
dirección de relaciones y auto fecha/hora.

Complementa a tmdl_writer (medidas). Respeta la indentacion por tabs y hace
backup antes de escribir. Estas ediciones son DURABLES (quedan en el .pbip);
Power BI las carga al reabrir el proyecto.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.pbip.tmdl_reader import (_definition_dir, _first_token, _indent, _unquote,
                              find_table_file)
from horizun_pbi_mcp.services import project_state
from horizun_pbi_mcp.services import txn as txn_service
from horizun_pbi_mcp.utils.change_log import record_change

log = get_logger("model_edit")


def _write_transactional(active: ActivePbip, path: Path, lines: List[str],
                         *, tool: str) -> Dict[str, Any]:
    """Escribe el .tmdl con journal, verificacion y rollback.

    Antes se comprueba que Power BI Desktop no pueda tener el proyecto abierto:
    de tenerlo, al guardar desde Desktop se perderia esta edicion.
    """
    project_state.assert_writable(active, operation="Editar el modelo TMDL")
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    with txn_service.project_transaction(active, [path], tool=tool) as t:
        t.write_text(path, text)
    return t.summary()


def _column_block(lines: List[str], column: str) -> Optional[Tuple[int, int]]:
    for i, line in enumerate(lines):
        if _indent(line) == 1 and _first_token(line) == "column":
            nm = _unquote(line.strip()[len("column"):].split("=", 1)[0].strip())
            if nm == column:
                j = i + 1
                while j < len(lines) and (lines[j].strip() == "" or _indent(lines[j]) > 1):
                    j += 1
                return i, j
    return None


def set_column_hidden_pbip(active: ActivePbip, table: str, column: str,
                           hidden: bool = True, do_backup: bool = True) -> Dict[str, Any]:
    """Una sola columna. Para varias, usa `set_columns_hidden_pbip_bulk`."""
    fp = find_table_file(active, table)
    lines = fp.read_text(encoding="utf-8-sig").splitlines()
    loc = _column_block(lines, column)
    if loc is None:
        raise ValidationError(f"La columna '{column}' no existe en la tabla '{table}'.")
    start, end = loc
    has_hidden = any(lines[k].strip() == "isHidden" for k in range(start, end))
    changed = False
    if hidden and not has_hidden:
        lines.insert(start + 1, "\t\tisHidden")
        changed = True
    elif not hidden and has_hidden:
        lines = [l for k, l in enumerate(lines)
                 if not (start <= k < end and l.strip() == "isHidden")]
        changed = True
    result = None
    if changed:
        result = _write_transactional(active, fp, lines,
                                      tool="pbi_set_column_visibility(pbip)")
        if do_backup:
            record_change("pbi_set_column_visibility(pbip)",
                          f"Columna '{table}[{column}]' {'oculta' if hidden else 'visible'}.",
                          files=[str(fp)], backup=result["journal"])
    return {"changed": changed, "table": table, "column": column,
            "hidden": hidden, "file": str(fp),
            "backup": result["journal"] if result else None,
            "transaction": result}


def _aplicar_visibilidad(lines: List[str], column: str,
                         hidden: bool) -> Tuple[List[str], bool]:
    """Aplica isHidden sobre `lines` EN MEMORIA. Devuelve (lineas, cambio)."""
    loc = _column_block(lines, column)
    if loc is None:
        raise ValidationError(f"La columna '{column}' ya no se localiza en el archivo.")
    start, end = loc
    tiene = any(lines[k].strip() == "isHidden" for k in range(start, end))
    if hidden and not tiene:
        nuevas = list(lines)
        nuevas.insert(start + 1, "\t\tisHidden")
        return nuevas, True
    if not hidden and tiene:
        nuevas = [l for k, l in enumerate(lines)
                  if not (start <= k < end and l.strip() == "isHidden")]
        return nuevas, True
    return lines, False


def plan_columns_hidden_pbip(active: ActivePbip, entries: List[Dict[str, str]],
                             hidden: bool) -> Dict[str, Any]:
    """Valida TODAS las entradas y agrupa los cambios por archivo .tmdl.

    No escribe nada. Si cualquier entrada es invalida, lanza con el indice, la
    tabla y la columna: asi la tool puede fallar sin haber tocado el disco.
    """
    por_archivo: Dict[str, Dict[str, Any]] = {}
    plan: List[Dict[str, Any]] = []

    for idx, e in enumerate(entries):
        table, column = e["table"], e["column"]
        try:
            fp = find_table_file(active, table)
        except Exception as exc:                      # noqa: BLE001
            raise ValidationError(
                f"Entrada {idx} ({table}[{column}]): no existe la tabla '{table}' "
                f"en el modelo TMDL.",
                details={"index": idx, "table": table, "column": column}) from exc

        clave = str(fp)
        if clave not in por_archivo:
            por_archivo[clave] = {
                "path": fp,
                "lines": fp.read_text(encoding="utf-8-sig").splitlines(),
                "entries": [],
            }
        # Se valida contra las lineas ORIGINALES: la existencia de la columna
        # no depende de los cambios que se apliquen despues.
        if _column_block(por_archivo[clave]["lines"], column) is None:
            raise ValidationError(
                f"Entrada {idx} ({table}[{column}]): la columna '{column}' no "
                f"existe en la tabla '{table}'.",
                details={"index": idx, "table": table, "column": column,
                         "file": clave})
        por_archivo[clave]["entries"].append({"index": idx, "table": table,
                                              "column": column})
        plan.append({"index": idx, "table": table, "column": column,
                     "file": clave})

    return {"por_archivo": por_archivo, "plan": plan}


def set_columns_hidden_pbip_bulk(active: ActivePbip, entries: List[Dict[str, str]],
                                 hidden: bool = True,
                                 do_backup: bool = True) -> Dict[str, Any]:
    """Cambia la visibilidad de VARIAS columnas en UNA SOLA transaccion.

    Cada archivo .tmdl se lee una vez, se muta en memoria con todos sus cambios
    y se incluye una sola vez entre los objetivos. Si falla cualquiera, se
    restaura el conjunto completo.
    """
    if not entries:
        return {"changed": 0, "results": [], "files": [], "transaction": None}

    planificado = plan_columns_hidden_pbip(active, entries, hidden)
    por_archivo = planificado["por_archivo"]

    # --- mutar en memoria; nada toca el disco todavia ---------------------
    resultados: Dict[int, Dict[str, Any]] = {}
    for datos in por_archivo.values():
        lines = datos["lines"]
        for e in datos["entries"]:
            antes = _column_block(lines, e["column"])
            estaba = any(lines[k].strip() == "isHidden"
                         for k in range(antes[0], antes[1]))
            lines, cambio = _aplicar_visibilidad(lines, e["column"], hidden)
            resultados[e["index"]] = {
                "table": e["table"], "column": e["column"],
                "before_hidden": estaba, "after_hidden": bool(hidden),
                "changed": cambio, "file": str(datos["path"]),
            }
        datos["final"] = lines

    con_cambio = [d for d in por_archivo.values()
                  if any(resultados[e["index"]]["changed"] for e in d["entries"])]

    resumen = None
    transaccion = None
    if con_cambio:
        project_state.assert_writable(active, operation="Editar el modelo TMDL")
        objetivos = [d["path"] for d in con_cambio]
        cm = txn_service.project_transaction(
            active, objetivos, tool="pbi_hide_columns(pbip)")
        with cm as t:
            for d in con_cambio:
                texto = "\n".join(d["final"])
                if not texto.endswith("\n"):
                    texto += "\n"
                t.write_text(d["path"], texto)
        resumen = cm.result
        # Se conserva la transaccion: en modo 'both', si el lado en vivo falla
        # despues, hay que compensar esta escritura ya confirmada.
        transaccion = cm.txn

        if do_backup:
            record_change(
                "pbi_hide_columns(pbip)",
                f"{sum(1 for r in resultados.values() if r['changed'])} columnas "
                f"{'ocultas' if hidden else 'visibles'} en {len(con_cambio)} archivo(s).",
                files=[str(d["path"]) for d in con_cambio],
                backup=resumen["journal"])

    return {
        "changed": sum(1 for r in resultados.values() if r["changed"]),
        "results": [resultados[i] for i in sorted(resultados)],
        "files": [str(d["path"]) for d in con_cambio],
        "backup": resumen["journal"] if resumen else None,
        "transaction": resumen,
        "txn_object": transaccion,
    }


def _rel_blocks(lines: List[str]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for i, line in enumerate(lines):
        if _indent(line) == 0 and _first_token(line) == "relationship":
            if cur is not None:
                cur["end"] = i
                blocks.append(cur)
            cur = {"start": i, "end": None}
    if cur is not None:
        cur["end"] = len(lines)
        blocks.append(cur)
    return blocks


def _table_of(ref: str) -> Optional[str]:
    ref = ref.strip()
    if "." in ref:
        return _unquote(ref.split(".", 1)[0])
    return None


def set_relationship_direction_pbip(active: ActivePbip, from_table: str, to_table: str,
                                    direction: str = "single",
                                    do_backup: bool = True) -> Dict[str, Any]:
    """direction: 'single' (oneDirection) o 'both' (bothDirections).

    Empareja la relacion por el par de tablas (sin importar el orden).
    """
    direction = direction.lower()
    if direction not in ("single", "both"):
        raise ValidationError("direction debe ser 'single' o 'both'.")
    rp = _definition_dir(active) / "relationships.tmdl"
    if not rp.exists():
        raise ValidationError("El proyecto no tiene relationships.tmdl.")
    lines = rp.read_text(encoding="utf-8-sig").splitlines()
    wanted = {from_table, to_table}

    matched = 0
    for blk in _rel_blocks(lines):
        f_tbl = t_tbl = None
        cf_idx = None
        for k in range(blk["start"], blk["end"]):
            s = lines[k].strip()
            if s.startswith("fromColumn:"):
                f_tbl = _table_of(s.split(":", 1)[1])
            elif s.startswith("toColumn:"):
                t_tbl = _table_of(s.split(":", 1)[1])
            elif s.startswith("crossFilteringBehavior:"):
                cf_idx = k
        if {f_tbl, t_tbl} != wanted:
            continue
        matched += 1
        blk["cf_idx"] = cf_idx
        blk["header"] = blk["start"]

    if matched == 0:
        raise ValidationError(
            f"No se encontro relacion entre '{from_table}' y '{to_table}'.")

    # Reconstruye aplicando el cambio (recorriendo de nuevo para indices estables)
    changed = 0
    new_lines: List[str] = []
    blocks = _rel_blocks(lines)

    def _block_tables(b):
        ft = tt = None
        for k in range(b["start"], b["end"]):
            s = lines[k].strip()
            if s.startswith("fromColumn:"):
                ft = _table_of(s.split(":", 1)[1])
            elif s.startswith("toColumn:"):
                tt = _table_of(s.split(":", 1)[1])
        return {ft, tt}

    target_ranges = [(b["start"], b["end"]) for b in blocks if _block_tables(b) == wanted]

    k = 0
    while k < len(lines):
        in_target = next(((s, e) for (s, e) in target_ranges if s <= k < e), None)
        if in_target is None:
            new_lines.append(lines[k])
            k += 1
            continue
        s, e = in_target
        block = lines[s:e]
        has_cf_line = any(l.strip().startswith("crossFilteringBehavior:") for l in block)
        out_block: List[str] = []
        for l in block:
            if l.strip().startswith("crossFilteringBehavior:"):
                if direction == "both":
                    out_block.append("\tcrossFilteringBehavior: bothDirections")
                    changed += 1
                else:
                    # single -> quitar la linea (default = oneDirection)
                    changed += 1
                    continue
            else:
                out_block.append(l)
        if direction == "both" and not has_cf_line:
            # insertar tras la cabecera 'relationship ...'
            out_block = [out_block[0], "\tcrossFilteringBehavior: bothDirections"] + out_block[1:]
            changed += 1
        new_lines.extend(out_block)
        k = e

    result = _write_transactional(active, rp, new_lines,
                                  tool="pbi_set_relationship_direction(pbip)")
    if do_backup:
        record_change("pbi_set_relationship_direction(pbip)",
                      f"Relacion {from_table}<->{to_table} -> {direction}.",
                      files=[str(rp)], backup=result["journal"])
    return {"changed": bool(changed), "from_table": from_table, "to_table": to_table,
            "direction": direction, "matched": matched, "file": str(rp),
            "backup": result["journal"], "transaction": result}


def set_auto_datetime_pbip(active: ActivePbip, enabled: bool = False,
                           do_backup: bool = True) -> Dict[str, Any]:
    """Activa/desactiva 'Auto fecha y hora' (annotation __PBI_TimeIntelligenceEnabled).

    Al desactivarlo, Power BI elimina las tablas de fecha automaticas
    (LocalDateTable_*/DateTableTemplate_*) al reabrir el proyecto, aligerando el modelo.
    """
    mp = _definition_dir(active) / "model.tmdl"
    if not mp.exists():
        raise ValidationError("El proyecto no tiene model.tmdl.")
    lines = mp.read_text(encoding="utf-8-sig").splitlines()
    val = "1" if enabled else "0"
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith("annotation __PBI_TimeIntelligenceEnabled"):
            lines[i] = f"annotation __PBI_TimeIntelligenceEnabled = {val}"
            found = True
            break
    if not found:
        insert_at = next((i for i, l in enumerate(lines)
                          if l.startswith("annotation") or l.startswith("ref table")),
                         len(lines))
        lines.insert(insert_at, f"annotation __PBI_TimeIntelligenceEnabled = {val}")
        lines.insert(insert_at + 1, "")

    result = _write_transactional(active, mp, lines,
                                  tool="pbi_set_auto_datetime(pbip)")
    if do_backup:
        record_change("pbi_set_auto_datetime(pbip)",
                      f"Auto fecha/hora -> {'ON' if enabled else 'OFF'}.",
                      files=[str(mp)], backup=result["journal"])
    return {"changed": True, "enabled": enabled, "file": str(mp),
            "backup": result["journal"], "transaction": result,
            "note": "Reabre el proyecto en Power BI Desktop para que elimine las "
                    "tablas de fecha automaticas."}
