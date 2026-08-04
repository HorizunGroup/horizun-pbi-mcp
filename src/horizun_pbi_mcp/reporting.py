"""Generacion de documentacion Markdown y analisis de calidad del modelo."""
from __future__ import annotations

import re
from typing import Any, Dict, List

from horizun_pbi_mcp.utils.file_utils import iso_now

# Token de ID separado (ID, _id, "Codigo", "Key", etc.) al final del nombre.
_ID_TOKEN = re.compile(r"(?i)(^|[ _])(id|c[oó]digo|code|key|llave|clave)([ _0-9]*)$")
# Sufijo camelCase tipo "ClienteID" / "ProductoKey" (parte final en mayuscula inicial).
_ID_CAMEL = re.compile(r".+(ID|Id|Key|KEY)$")
_CALENDAR_HINT = re.compile(r"(?i)calendar|fecha|date|dim[_ ]?date")


def _looks_like_id(name: str) -> bool:
    return bool(_ID_TOKEN.search(name or "") or _ID_CAMEL.match(name or ""))
_LONG_DAX_CHARS = 1500
_LONG_DAX_LINES = 30


# ------------------------------------------------------------------ calidad ---
def analyze_model_quality(model_data: Dict[str, Any]) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []

    def add(severity: str, category: str, message: str, obj: str = None):
        issues.append({"severity": severity, "category": category,
                       "message": message, "object": obj})

    measures = model_data.get("measures", [])
    tables = model_data.get("tables", [])
    rels = model_data.get("relationships", [])

    # medidas sin carpeta de visualizacion
    for m in measures:
        if not m.get("display_folder"):
            add("info", "medida_sin_carpeta",
                f"La medida '{m['name']}' no tiene carpeta de visualizacion.",
                f"{m.get('table')}[{m['name']}]")
        expr = m.get("expression") or ""
        if len(expr) > _LONG_DAX_CHARS or expr.count("\n") > _LONG_DAX_LINES:
            add("warning", "dax_muy_largo",
                f"La medida '{m['name']}' tiene DAX muy largo "
                f"({len(expr)} chars); considera variables o dividirla.",
                f"{m.get('table')}[{m['name']}]")
        name = m["name"]
        if name != name.strip() or "  " in name:
            add("warning", "nombre_inconsistente",
                f"La medida '{name}' tiene espacios sobrantes.", name)

    # relaciones bidireccionales / inactivas
    for r in rels:
        if str(r.get("cross_filtering")) in ("BothDirections", "bothDirections"):
            add("warning", "relacion_bidireccional",
                f"Relacion bidireccional {r.get('from_table')} <-> {r.get('to_table')} "
                "(puede afectar rendimiento y correctitud).",
                r.get("name"))
        if r.get("is_active") is False:
            add("info", "relacion_inactiva",
                f"Relacion inactiva {r.get('from_table')} -> {r.get('to_table')} "
                "(requiere USERELATIONSHIP para usarse).",
                r.get("name"))

    # tablas / columnas
    has_calendar = False
    for t in tables:
        tname = t["name"]
        if t.get("is_date_table") or _CALENDAR_HINT.search(tname):
            has_calendar = True
        for c in t.get("columns", []):
            if c.get("column_type") == "Calculated":
                add("info", "columna_calculada",
                    f"Columna calculada '{tname}[{c['name']}]' "
                    "(evalua si conviene mover el calculo a Power Query/medida).",
                    f"{tname}[{c['name']}]")
            if not c.get("is_hidden") and _looks_like_id(c["name"]):
                add("info", "id_visible",
                    f"Columna de ID visible '{tname}[{c['name']}]' "
                    "(suele ocultarse a los usuarios).",
                    f"{tname}[{c['name']}]")

    if not has_calendar:
        add("warning", "sin_calendario",
            "No se detecto una tabla calendario (marca de fecha o nombre Calendario/"
            "Fecha/Date). La inteligencia de tiempo puede fallar.")

    # tablas huerfanas (sin relaciones)
    related = set()
    for r in rels:
        related.add(r.get("from_table"))
        related.add(r.get("to_table"))
    for t in tables:
        if t["name"] not in related and not t.get("is_date_table") \
                and not _CALENDAR_HINT.search(t["name"]):
            if t.get("measure_count", 0) == 0:
                add("info", "tabla_huerfana",
                    f"La tabla '{t['name']}' no participa en ninguna relacion.",
                    t["name"])

    counts: Dict[str, int] = {}
    for it in issues:
        counts[it["severity"]] = counts.get(it["severity"], 0) + 1

    return {"issue_count": len(issues), "by_severity": counts, "issues": issues,
            "has_calendar_table": has_calendar}


# -------------------------------------------------------------- markdown ---
def _md_escape(text: Any) -> str:
    return str(text).replace("|", "\\|") if text is not None else ""


def document_model_markdown(model_data: Dict[str, Any], quality: Dict[str, Any] = None) -> str:
    info = model_data.get("model", {})
    tables = model_data.get("tables", [])
    measures = model_data.get("measures", [])
    rels = model_data.get("relationships", [])
    hierarchies = model_data.get("hierarchies", [])
    roles = model_data.get("roles", [])

    out: List[str] = []
    out.append(f"# Documentacion del modelo — {info.get('name') or 'Modelo'}")
    out.append("")
    out.append(f"_Generado: {iso_now()}_")
    out.append("")
    out.append("## Resumen")
    out.append("")
    out.append(f"- **Modelo:** {info.get('name')}")
    out.append(f"- **Cultura:** {info.get('culture')}")
    out.append(f"- **Tablas:** {len(tables)}")
    out.append(f"- **Medidas:** {len(measures)}")
    out.append(f"- **Relaciones:** {len(rels)}")
    out.append(f"- **Jerarquias:** {len(hierarchies)}")
    out.append(f"- **Roles (RLS):** {len(roles)}")
    out.append("")

    out.append("## Tablas")
    out.append("")
    out.append("| Tabla | Oculta | Columnas | Medidas | Fecha |")
    out.append("|---|---|---|---|---|")
    for t in tables:
        out.append(f"| {_md_escape(t['name'])} | {'si' if t['is_hidden'] else 'no'} "
                   f"| {t.get('column_count')} | {t.get('measure_count')} "
                   f"| {'si' if t.get('is_date_table') else ''} |")
    out.append("")

    for t in tables:
        out.append(f"### {t['name']}")
        if t.get("description"):
            out.append(f"> {t['description']}")
        out.append("")
        if t.get("columns"):
            out.append("| Columna | Tipo dato | Tipo | Oculta | SummarizeBy |")
            out.append("|---|---|---|---|---|")
            for c in t["columns"]:
                out.append(f"| {_md_escape(c['name'])} | {c.get('data_type')} "
                           f"| {c.get('column_type')} | {'si' if c['is_hidden'] else 'no'} "
                           f"| {c.get('summarize_by')} |")
            out.append("")

    if measures:
        out.append("## Medidas")
        out.append("")
        for m in measures:
            folder = f" _(carpeta: {m['display_folder']})_" if m.get("display_folder") else ""
            out.append(f"### {m['table']}[{m['name']}]{folder}")
            if m.get("description"):
                out.append(f"> {m['description']}")
            if m.get("format_string"):
                out.append(f"- Formato: `{m['format_string']}`")
            out.append("")
            out.append("```dax")
            out.append((m.get("expression") or "").strip())
            out.append("```")
            out.append("")

    if rels:
        out.append("## Relaciones")
        out.append("")
        out.append("| Desde | Hacia | Cardinalidad | Filtro cruzado | Activa |")
        out.append("|---|---|---|---|---|")
        for r in rels:
            card = f"{r.get('from_cardinality')}-{r.get('to_cardinality')}"
            out.append(f"| {r.get('from_table')}[{r.get('from_column')}] "
                       f"| {r.get('to_table')}[{r.get('to_column')}] | {card} "
                       f"| {r.get('cross_filtering')} | {'si' if r.get('is_active') else 'no'} |")
        out.append("")

    if hierarchies:
        out.append("## Jerarquias")
        out.append("")
        for h in hierarchies:
            levels = " > ".join(lv["name"] for lv in h["levels"])
            out.append(f"- **{h['table']}[{h['name']}]**: {levels}")
        out.append("")

    if roles:
        out.append("## Roles / Seguridad a nivel de fila (RLS)")
        out.append("")
        for role in roles:
            out.append(f"### {role['name']}")
            for p in role.get("table_permissions", []):
                out.append(f"- `{p['table']}`: `{p['filter']}`")
            out.append("")

    if quality:
        out.append("## Advertencias de calidad del modelo")
        out.append("")
        out.append(f"Total: {quality['issue_count']} "
                   f"({quality.get('by_severity')})")
        out.append("")
        for it in quality["issues"]:
            out.append(f"- **[{it['severity']}] {it['category']}** — {it['message']}")
        out.append("")

    return "\n".join(out)


def document_layout_markdown(project: Dict[str, Any],
                             pages: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    out.append(f"# Layout del informe — {project.get('report_name')}")
    out.append("")
    out.append(f"_Generado: {iso_now()}_")
    out.append("")
    for page in pages:
        out.append(f"## Pagina: {page.get('display_name') or page.get('name')}")
        out.append("")
        out.append(f"- Id interno: `{page.get('name')}`")
        out.append(f"- Tamano: {page.get('width')}x{page.get('height')}")
        out.append(f"- Visuales: {len(page.get('visuals', []))}")
        out.append("")
        if page.get("visuals"):
            out.append("| Tipo | Titulo | x | y | ancho | alto | Campos |")
            out.append("|---|---|---|---|---|---|---|")
            for v in page["visuals"]:
                pos = v.get("position", {})
                fields = ", ".join(v.get("measures", []) + v.get("columns", []))
                out.append(f"| {v.get('type')} | {_md_escape(v.get('title') or '')} "
                           f"| {pos.get('x')} | {pos.get('y')} | {pos.get('width')} "
                           f"| {pos.get('height')} | {_md_escape(fields)} |")
            out.append("")
    return "\n".join(out)
