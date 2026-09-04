"""Lectura de modelos TMDL desde disco (.SemanticModel/definition).

Parser pragmatico por indentacion de tabs. Cubre lo que necesitan la
documentacion y la edicion de medidas en modo pbip: tablas, columnas, medidas
y relaciones. No pretende reimplementar TMDL completo (para metadatos ricos en
vivo se usa TOM en powerbi/model_reader.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.config import ActivePbip
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PbipStructureError, TableNotFoundError, ValidationError

log = get_logger("tmdl_reader")

_BLOCK_KEYWORDS = ("measure", "column", "hierarchy", "partition", "calculationGroup")
_MEASURE_PROP_KEYS = (
    "formatString", "displayFolder", "description", "lineageTag", "isHidden",
    "annotation", "changedProperty", "formatStringDefinition", "dataType",
    "detailRows", "kpi",
)


def _definition_dir(active: ActivePbip) -> Path:
    if not active.semantic_model_dir:
        raise PbipStructureError("El proyecto activo no tiene modelo semantico (.SemanticModel).")
    d = Path(active.semantic_model_dir) / "definition"
    if not d.exists():
        raise PbipStructureError(f"No existe la carpeta TMDL: {d}")
    return d


def _indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == "\t":
            n += 1
        elif line.startswith("    "):  # tolera 4 espacios como un tab
            return len(line) - len(line.lstrip(" "))
        else:
            break
    return n


def _first_token(line: str) -> str:
    s = line.strip()
    return s.split(" ", 1)[0].split("=", 1)[0].strip() if s else ""


def _unquote(name: str) -> str:
    name = name.strip()
    if name.startswith("'") and name.endswith("'") and len(name) >= 2:
        return name[1:-1].replace("''", "'")
    return name


def _parse_kv(line: str) -> Tuple[str, str]:
    s = line.strip()
    if ":" in s:
        k, v = s.split(":", 1)
        return k.strip(), v.strip()
    return s, ""


def _split_children(body_lines: List[str]) -> List[Dict[str, Any]]:
    """Agrupa las lineas hijas de una tabla en bloques (measure/column/...).

    Los doc-comments TMDL (`/// texto`) que preceden a un bloque se adjuntan
    como su descripcion (`doc`).
    """
    blocks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    pending_doc: List[str] = []
    for line in body_lines:
        if not line.strip():
            if current:
                current["lines"].append(line)
            continue
        ind = _indent(line)
        stripped = line.strip()
        if stripped.startswith("///"):
            # doc-comment: pertenece al siguiente bloque
            if current:
                blocks.append(current)
                current = None
            pending_doc.append(stripped[3:].strip())
            continue
        kw = _first_token(line)
        if ind == 1 and kw in _BLOCK_KEYWORDS:
            if current:
                blocks.append(current)
            current = {"keyword": kw, "lines": [line], "doc": pending_doc}
            pending_doc = []
        elif ind == 1:
            # propiedad/annotation a nivel de tabla: cierra el bloque actual
            if current:
                blocks.append(current)
                current = None
            pending_doc = []
        else:
            if current:
                current["lines"].append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_measure(block_lines: List[str]) -> Dict[str, Any]:
    header = block_lines[0].strip()
    # measure <name> [= inline]
    rest = header[len("measure"):].strip()
    name_part, _, inline = rest.partition("=")
    name = _unquote(name_part.strip())
    props: Dict[str, Any] = {"name": name, "expression": None, "format_string": None,
                             "display_folder": None, "description": None}
    expr_lines: List[str] = []
    if inline.strip():
        expr_lines.append(inline.strip())
    seen_prop = False
    for line in block_lines[1:]:
        s = line.strip()
        if not s:
            if not seen_prop and expr_lines:
                expr_lines.append("")
            continue
        key = s.split(":", 1)[0].split(" ", 1)[0]
        if key in _MEASURE_PROP_KEYS:
            seen_prop = True
            k, v = _parse_kv(line)
            if k == "formatString":
                props["format_string"] = _unquote(v)
            elif k == "displayFolder":
                props["display_folder"] = _unquote(v)
            elif k == "description":
                props["description"] = _unquote(v)
        elif not seen_prop:
            expr_lines.append(s)
    props["expression"] = "\n".join(expr_lines).strip() or None
    return props


def _parse_column(block_lines: List[str]) -> Dict[str, Any]:
    header = block_lines[0].strip()
    rest = header[len("column"):].strip()
    name_part, eq, inline = rest.partition("=")
    col: Dict[str, Any] = {
        "name": _unquote(name_part.strip()),
        "data_type": None,
        "is_hidden": False,
        "summarize_by": None,
        "source_column": None,
        "display_folder": None,
        "column_type": "Calculated" if eq else "Data",
        "expression": inline.strip() if inline.strip() else None,
    }
    for line in block_lines[1:]:
        s = line.strip()
        if not s:
            continue
        if s == "isHidden":
            col["is_hidden"] = True
        elif ":" in s:
            k, v = _parse_kv(line)
            if k == "dataType":
                col["data_type"] = v
            elif k == "summarizeBy":
                col["summarize_by"] = v
            elif k == "sourceColumn":
                col["source_column"] = v
            elif k == "displayFolder":
                col["display_folder"] = _unquote(v)
    return col


def parse_table_file(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    table_name = None
    header_idx = 0
    for i, line in enumerate(lines):
        if _indent(line) == 0 and _first_token(line) == "table":
            table_name = _unquote(line.strip()[len("table"):].strip())
            header_idx = i
            break
    if table_name is None:
        raise PbipStructureError(f"El archivo no parece una tabla TMDL: {path}")

    is_hidden = any(l.strip() == "isHidden" and _indent(l) == 1
                    for l in lines[header_idx + 1:])
    blocks = _split_children(lines[header_idx + 1:])
    measures, columns = [], []
    for b in blocks:
        if b["keyword"] == "measure":
            m = _parse_measure(b["lines"])
            m["table"] = table_name
            if not m.get("description") and b.get("doc"):
                m["description"] = " ".join(b["doc"]) or None
            measures.append(m)
        elif b["keyword"] == "column":
            columns.append(_parse_column(b["lines"]))
    return {
        "name": table_name,
        "file": str(path),
        "is_hidden": is_hidden,
        "columns": columns,
        "measures": measures,
        "column_count": len(columns),
        "measure_count": len(measures),
    }


def _parse_relationships(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    rels: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def _split_col(ref: str) -> Tuple[Optional[str], Optional[str]]:
        ref = ref.strip()
        if "." in ref:
            tbl, col = ref.split(".", 1)
            return _unquote(tbl), _unquote(col)
        return None, _unquote(ref)

    for line in lines:
        if _indent(line) == 0 and _first_token(line) == "relationship":
            if current:
                rels.append(current)
            current = {"name": line.strip()[len("relationship"):].strip(),
                       "is_active": True, "cross_filtering": "OneDirection"}
        elif current is not None and ":" in line:
            k, v = _parse_kv(line)
            if k == "fromColumn":
                current["from_table"], current["from_column"] = _split_col(v)
            elif k == "toColumn":
                current["to_table"], current["to_column"] = _split_col(v)
            elif k == "crossFilteringBehavior":
                current["cross_filtering"] = v
            elif k == "isActive":
                current["is_active"] = v.lower() != "false"
    if current:
        rels.append(current)
    return rels


def read_semantic_model(active: ActivePbip, *, strict: bool = True) -> Dict[str, Any]:
    """Lee todo el TMDL; por defecto no oculta tablas que no pudo parsear."""
    definition = _definition_dir(active)
    tables_dir = definition / "tables"
    tables: List[Dict[str, Any]] = []
    all_measures: List[Dict[str, Any]] = []
    ilegibles = []
    if tables_dir.exists():
        for tf in sorted(tables_dir.glob("*.tmdl")):
            try:
                parsed = parse_table_file(tf)
                tables.append(parsed)
                all_measures.extend(parsed["measures"])
            except Exception as exc:  # noqa: BLE001
                log.warning("No se pudo parsear %s: %s", tf, exc)
                ilegibles.append({"file": str(tf),
                                  "error": f"{type(exc).__name__}: {exc}"})

    if strict and ilegibles:
        raise ValidationError(
            f"No se pudieron leer {len(ilegibles)} tabla(s) TMDL; no se "
            "trabaja sobre un modelo incompleto.",
            details={"unreadable_tables": ilegibles,
                     "readable_count": len(tables)})

    relationships = _parse_relationships(definition / "relationships.tmdl")

    model_info: Dict[str, Any] = {"name": None, "culture": None}
    model_file = definition / "model.tmdl"
    if model_file.exists():
        for line in model_file.read_text(encoding="utf-8-sig").splitlines():
            if _first_token(line) == "model" and _indent(line) == 0:
                model_info["name"] = _unquote(line.strip()[len("model"):].strip())
            elif line.strip().startswith("culture:"):
                model_info["culture"] = line.split(":", 1)[1].strip()

    return {
        "model": model_info,
        "tables": tables,
        "measures": all_measures,
        "relationships": relationships,
        "source": "tmdl",
        "unreadable_tables": ilegibles,
        "warnings": ([f"{len(ilegibles)} tabla(s) TMDL no se pudieron leer; "
                      "el modelo es parcial."] if ilegibles else []),
    }


def find_table_file(active: ActivePbip, table_name: str) -> Path:
    """Devuelve el archivo .tmdl que define una tabla (por su nombre real)."""
    definition = _definition_dir(active)
    tables_dir = definition / "tables"
    wanted = table_name.casefold()
    if tables_dir.exists():
        for tf in sorted(tables_dir.glob("*.tmdl")):
            try:
                first = None
                for line in tf.read_text(encoding="utf-8-sig").splitlines():
                    if _indent(line) == 0 and _first_token(line) == "table":
                        first = _unquote(line.strip()[len("table"):].strip())
                        break
                if first is not None and first.casefold() == wanted:
                    return tf
            except OSError:
                continue
    raise TableNotFoundError(
        f"No se encontro el archivo TMDL de la tabla '{table_name}'.")
