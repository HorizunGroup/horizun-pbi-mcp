"""Escritura de medidas en archivos TMDL (modo pbip, en disco).

Respeta la indentacion por tabs que usa Power BI:
- `table`      -> 0 tabs
- `measure X`  -> 1 tab
- propiedades  -> 2 tabs (formatString, displayFolder, description, lineageTag)
- expresion    -> 3 tabs (cuando es multilinea)
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import MeasureExistsError, MeasureNotFoundError
from pbip.tmdl_reader import _first_token, _indent, _unquote, find_table_file, parse_table_file
from services import project_state
from services import txn as txn_service
from utils.change_log import record_change
from utils.validation import tmdl_quote_name, validate_measure_expression, validate_object_name

log = get_logger("tmdl_writer")

_CHILD_KEYWORDS = ("measure", "column", "hierarchy", "partition", "calculationGroup")


def _build_measure_block(
    name: str,
    expression: str,
    format_string: Optional[str],
    display_folder: Optional[str],
    description: Optional[str],
    data_category: Optional[str] = None,
) -> List[str]:
    qname = tmdl_quote_name(name)
    lines: List[str] = []
    # En TMDL la descripcion es un doc-comment /// ENCIMA de la declaracion
    # (la propiedad 'description:' NO existe y Power BI rechaza el archivo).
    if description:
        for dline in str(description).splitlines():
            lines.append(f"\t/// {dline.strip()}")
    if "\n" in expression:
        lines.append(f"\tmeasure {qname} =")
        for exprline in expression.split("\n"):
            # Toda linea de la expresion se indenta a 3 tabs (incluidas las vacias,
            # como hace Power BI) para no romper el bloque TMDL.
            lines.append("\t\t\t" + exprline)
    else:
        lines.append(f"\tmeasure {qname} = {expression}")
    if format_string:
        lines.append(f"\t\tformatString: {format_string}")
    if display_folder:
        lines.append(f"\t\tdisplayFolder: {display_folder}")
    if data_category:
        # p.ej. ImageUrl: Power BI renderiza data-URIs SVG de la medida como imagen
        lines.append(f"\t\tdataCategory: {data_category}")
    lines.append(f"\t\tlineageTag: {uuid.uuid4()}")
    return lines


def _locate_measure(lines: List[str], name: str) -> Optional[Tuple[int, int]]:
    """Devuelve (inicio, fin) del bloque de una medida, o None.

    El inicio incluye los doc-comments `///` inmediatamente anteriores (son la
    descripcion de la medida), para que reemplazar/borrar no los duplique.
    """
    for i, line in enumerate(lines):
        if _indent(line) == 1 and _first_token(line) == "measure":
            header = line.strip()[len("measure"):].strip()
            nm = _unquote(header.split("=", 1)[0].strip())
            if nm == name:
                start = i
                while start > 0 and lines[start - 1].strip().startswith("///"):
                    start -= 1
                j = i + 1
                while j < len(lines):
                    lj = lines[j]
                    if lj.strip() == "":
                        j += 1
                        continue
                    if _indent(lj) <= 1:
                        break
                    j += 1
                return start, j
    return None


def _insertion_index(lines: List[str]) -> int:
    for i, line in enumerate(lines):
        if _indent(line) == 1 and _first_token(line) in _CHILD_KEYWORDS:
            return i
    return len(lines)


def _snapshot(file_path: Path, name: str) -> Optional[Dict[str, Any]]:
    try:
        parsed = parse_table_file(file_path)
        for m in parsed["measures"]:
            if m["name"] == name:
                return m
    except Exception:  # noqa: BLE001
        pass
    return None


def _render(lines: List[str]) -> str:
    text = "\n".join(lines)
    if not text.endswith("\n"):
        text += "\n"
    return text


def _write_transactional(active: ActivePbip, file_path: Path, lines: List[str],
                         *, tool: str) -> Dict[str, Any]:
    """Escribe el .tmdl dentro de una transaccion con journal y rollback.

    Se comprueba antes que Power BI Desktop no pueda tener el proyecto abierto:
    si lo tuviera, al guardar en Desktop sobrescribiria este archivo.
    """
    project_state.assert_writable(active, operation="Editar el modelo TMDL")
    with txn_service.project_transaction(active, [file_path], tool=tool) as t:
        t.write_text(file_path, _render(lines))
        return t.summary()


def render_measure_change(
    active: ActivePbip,
    table: str,
    name: str,
    expression: str,
    format_string: Optional[str] = None,
    description: Optional[str] = None,
    display_folder: Optional[str] = None,
    overwrite: bool = False,
    data_category: Optional[str] = None,
) -> Tuple[str, Path, str]:
    """Calcula el TMDL resultante SIN escribir. Devuelve (texto, ruta, accion).

    Es la mitad pura de `create_measure_pbip`: la usa el planificador para
    producir un diff sin tocar el disco, y la reutiliza la propia escritura para
    que plan y aplicacion no puedan divergir.
    """
    name = validate_object_name(name, "medida")
    expression = validate_measure_expression(expression)
    file_path = find_table_file(active, table)
    lines = file_path.read_text(encoding="utf-8-sig").splitlines()

    loc = _locate_measure(lines, name)
    action = "created"
    if loc is not None:
        if not overwrite:
            raise MeasureExistsError(
                f"La medida '{name}' ya existe en la tabla '{table}'. "
                "Usa overwrite=true para reemplazarla.")
        action = "updated"

    block = _build_measure_block(name, expression, format_string, display_folder,
                                 description, data_category)
    if loc is not None:
        start, end = loc
        new_lines = lines[:start] + block + lines[end:]
    else:
        idx = _insertion_index(lines)
        insert = list(block)
        if idx > 0 and lines[idx - 1].strip() != "":
            insert = [""] + insert
        insert = insert + [""]
        new_lines = lines[:idx] + insert + lines[idx:]

    return _render(new_lines), file_path, action


def create_measure_pbip(
    active: ActivePbip,
    table: str,
    name: str,
    expression: str,
    format_string: Optional[str] = None,
    description: Optional[str] = None,
    display_folder: Optional[str] = None,
    overwrite: bool = False,
    do_backup: bool = True,
    data_category: Optional[str] = None,
) -> Dict[str, Any]:
    name = validate_object_name(name, "medida")
    expression = validate_measure_expression(expression)
    file_path = find_table_file(active, table)
    lines = file_path.read_text(encoding="utf-8-sig").splitlines()

    loc = _locate_measure(lines, name)
    action = "created"
    before = None
    if loc is not None:
        if not overwrite:
            raise MeasureExistsError(
                f"La medida '{name}' ya existe en la tabla '{table}'. "
                "Usa overwrite=true para reemplazarla.")
        before = _snapshot(file_path, name)
        action = "updated"

    block = _build_measure_block(name, expression, format_string, display_folder,
                                 description, data_category)

    if loc is not None:
        start, end = loc
        new_lines = lines[:start] + block + lines[end:]
    else:
        idx = _insertion_index(lines)
        insert = list(block)
        if idx > 0 and lines[idx - 1].strip() != "":
            insert = [""] + insert
        insert = insert + [""]
        new_lines = lines[:idx] + insert + lines[idx:]

    result = _write_transactional(active, file_path, new_lines,
                                  tool="pbi_create_measure(pbip)")
    after = _snapshot(file_path, name)
    if do_backup:
        record_change("pbi_create_measure(pbip)",
                      f"Medida '{name}' {action} en tabla '{table}'.",
                      files=[str(file_path)], backup=result["journal"])
    return {"action": action, "mode": "pbip", "table": table, "file": str(file_path),
            "before": before, "after": after, "backup": result["journal"],
            "transaction": result}


def update_measure_pbip(
    active: ActivePbip,
    table: str,
    name: str,
    expression: Optional[str] = None,
    format_string: Optional[str] = None,
    description: Optional[str] = None,
    display_folder: Optional[str] = None,
    do_backup: bool = True,
) -> Dict[str, Any]:
    file_path = find_table_file(active, table)
    before = _snapshot(file_path, name)
    if before is None:
        raise MeasureNotFoundError(
            f"La medida '{name}' no existe en la tabla '{table}' (archivo TMDL).")
    # Fusiona: lo no especificado se conserva del estado actual.
    new_expr = expression if expression is not None else before["expression"]
    new_fmt = format_string if format_string is not None else before.get("format_string")
    new_folder = display_folder if display_folder is not None else before.get("display_folder")
    new_desc = description if description is not None else before.get("description")
    return create_measure_pbip(active, table, name, new_expr, new_fmt, new_desc, new_folder,
                               overwrite=True, do_backup=do_backup)


def delete_measure_pbip(active: ActivePbip, table: str, name: str) -> Dict[str, Any]:
    file_path = find_table_file(active, table)
    lines = file_path.read_text(encoding="utf-8-sig").splitlines()
    loc = _locate_measure(lines, name)
    if loc is None:
        raise MeasureNotFoundError(
            f"La medida '{name}' no existe en la tabla '{table}' (archivo TMDL).")
    before = _snapshot(file_path, name)
    start, end = loc
    # limpia una linea en blanco sobrante posterior
    while end < len(lines) and lines[end].strip() == "":
        end += 1
    new_lines = lines[:start] + lines[end:]
    result = _write_transactional(active, file_path, new_lines,
                                  tool="pbi_delete_measure(pbip)")
    record_change("pbi_delete_measure(pbip)",
                  f"Medida '{name}' eliminada de tabla '{table}'.",
                  files=[str(file_path)], backup=result["journal"])
    return {"action": "deleted", "mode": "pbip", "table": table, "file": str(file_path),
            "before": before, "backup": result["journal"], "transaction": result}
