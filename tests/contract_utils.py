"""Congelado y comparacion del contrato MCP de PowerBI-MCP.

El "contrato" es lo que otro LLM o cliente MCP ve y de lo que depende:
nombre de la tool, descripcion, parametros, tipos, obligatoriedad, valores por
defecto y metadatos declarados. Si algo de eso cambia sin querer, se rompen los
clientes ya configurados.

Lo que SI se congela:
  - nombre
  - descripcion (normalizada: sin espacios al final de linea)
  - por parametro: tipo, obligatoriedad, valor por defecto, enum si lo hay
  - conjunto de parametros requeridos (como conjunto ORDENADO, no como lista
    en el orden que devuelva la libreria)
  - forma del outputSchema
  - annotations / meta cuando existen

Lo que NO se congela (volatil, cambia sin alterar el contrato):
  - `title` autogenerados por pydantic ("Format String", "pbi_xArguments")
  - orden de las tools y de las claves
  - `icons`, `execution` y demas campos que la libreria pueda anadir vacios

Regenerar el golden despues de un cambio DELIBERADO:
    python -m tests.contract_utils --write
"""
from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "tools_v1.json"

#: Claves autogeneradas que no forman parte del contrato.
_VOLATILE_KEYS = {"title"}


# --------------------------------------------------------------- normalizar ---
def _strip_titles(node: Any) -> Any:
    """Quita recursivamente las claves volatiles y ordena las claves restantes."""
    if isinstance(node, dict):
        return {k: _strip_titles(v) for k, v in sorted(node.items())
                if k not in _VOLATILE_KEYS}
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


def _normalize_description(text: str | None) -> str:
    """Normaliza la descripcion para que NO dependa de la version de Python.

    Las descripciones salen del docstring de cada tool, y Python 3.13 cambio
    como se guardan: desde esa version el compilador les quita la sangria
    (gh-81283). En 3.10-3.12 el `__doc__` conserva los espacios de indentacion
    de cada linea de continuacion; en 3.13+ no.

    Sin dedentar aqui, el golden generado con un interprete no cuadra con el
    de otro: el CI en 3.10 reportaba las 90 descripciones como "modificadas",
    con exactamente los bytes de sangria de diferencia (130 -> 138 en una tool
    con una linea de continuacion a 8 espacios).

    `inspect.cleandoc` hace justo eso: quita la sangria comun de las lineas
    siguientes y normaliza los extremos. Despues se limpian los espacios al
    final de cada linea, que ninguna version toca.
    """
    if not text:
        return ""
    limpio = inspect.cleandoc(text)
    return "\n".join(line.rstrip() for line in limpio.splitlines()).strip()


def _param_contract(name: str, schema: Dict[str, Any], required: set) -> Dict[str, Any]:
    """Extrae de un parametro solo lo que es contrato."""
    clean = _strip_titles(schema)
    entry: Dict[str, Any] = {
        "required": name in required,
        "type": _describe_type(clean),
    }
    if "default" in clean:
        entry["default"] = clean["default"]
    if "enum" in clean:
        entry["enum"] = sorted(clean["enum"], key=str)
    return entry


def _describe_type(schema: Dict[str, Any]) -> str:
    """Describe el tipo de forma estable y legible ('string', 'string|null', ...)."""
    if "type" in schema:
        base = schema["type"]
        if base == "array":
            items = schema.get("items") or {}
            return f"array[{_describe_type(items) if items else 'any'}]"
        return str(base)
    if "anyOf" in schema:
        parts = sorted(_describe_type(s) for s in schema["anyOf"])
        return "|".join(parts)
    if "$ref" in schema:
        return f"ref:{schema['$ref']}"
    return "any"


def tool_contract(tool: Any) -> Dict[str, Any]:
    """Contrato normalizado de UNA tool."""
    dumped = tool.model_dump(exclude_none=True)
    input_schema = dumped.get("inputSchema") or {}
    props = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])

    entry: Dict[str, Any] = {
        "name": dumped["name"],
        "description": _normalize_description(dumped.get("description")),
        "params": {pname: _param_contract(pname, pschema, required)
                   for pname, pschema in sorted(props.items())},
        "required": sorted(required),
        "output_shape": _describe_output(dumped.get("outputSchema")),
    }
    # Metadatos declarados: solo se registran si existen de verdad.
    for key in ("annotations", "meta"):
        if dumped.get(key):
            entry[key] = _strip_titles(dumped[key])
    return entry


def _describe_output(schema: Dict[str, Any] | None) -> Any:
    if not schema:
        return None
    clean = _strip_titles(schema)
    return {
        "type": clean.get("type"),
        "properties": sorted((clean.get("properties") or {}).keys()),
        "required": sorted(clean.get("required") or []),
    }


def build_snapshot(tools: List[Any]) -> Dict[str, Any]:
    """Snapshot canonico y determinista de todas las tools."""
    contracts = sorted((tool_contract(t) for t in tools), key=lambda c: c["name"])
    return {
        "contract_version": 1,
        "tool_count": len(contracts),
        "tools": contracts,
    }


def snapshot_from_server() -> Dict[str, Any]:
    """Arranca el servidor en memoria y devuelve el snapshot de sus tools."""
    import asyncio

    src = str(REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from server import build_server  # import diferido: necesita src en sys.path

    mcp = build_server()
    tools = asyncio.run(mcp.list_tools())
    return build_snapshot(tools)


def load_golden() -> Dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def write_golden(snapshot: Dict[str, Any]) -> Path:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8")
    return GOLDEN_PATH


# ------------------------------------------------------------------- diffing ---
def diff_snapshots(golden: Dict[str, Any],
                   current: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Compara dos snapshots.

    Devuelve (rupturas, cambios_compatibles) como listas de lineas legibles.
    Una "ruptura" invalida a un cliente ya configurado; un cambio compatible no.
    """
    breaking: List[str] = []
    compatible: List[str] = []

    g_tools = {t["name"]: t for t in golden.get("tools", [])}
    c_tools = {t["name"]: t for t in current.get("tools", [])}

    removed = sorted(set(g_tools) - set(c_tools))
    added = sorted(set(c_tools) - set(g_tools))

    for name in removed:
        breaking.append(f"TOOL ELIMINADA: {name}  (rompe a cualquier cliente que la use)")
    for name in added:
        compatible.append(f"tool nueva: {name}")

    for name in sorted(set(g_tools) & set(c_tools)):
        g, c = g_tools[name], c_tools[name]

        if g["description"] != c["description"]:
            compatible.append(
                f"{name}: descripcion modificada "
                f"({len(g['description'])} -> {len(c['description'])} chars)")

        g_params, c_params = g.get("params", {}), c.get("params", {})
        for pname in sorted(set(g_params) - set(c_params)):
            breaking.append(f"{name}: parametro ELIMINADO '{pname}'")
        for pname in sorted(set(c_params) - set(g_params)):
            newp = c_params[pname]
            if newp.get("required"):
                breaking.append(
                    f"{name}: parametro NUEVO OBLIGATORIO '{pname}' "
                    "(las llamadas existentes fallaran)")
            else:
                compatible.append(
                    f"{name}: parametro nuevo opcional '{pname}' "
                    f"(default={newp.get('default')!r})")

        for pname in sorted(set(g_params) & set(c_params)):
            gp, cp = g_params[pname], c_params[pname]
            if gp["type"] != cp["type"]:
                breaking.append(
                    f"{name}.{pname}: tipo {gp['type']} -> {cp['type']}")
            if gp["required"] != cp["required"]:
                if cp["required"]:
                    breaking.append(
                        f"{name}.{pname}: paso a ser OBLIGATORIO (antes opcional)")
                else:
                    compatible.append(
                        f"{name}.{pname}: paso a ser opcional (antes obligatorio)")
            if gp.get("default") != cp.get("default"):
                breaking.append(
                    f"{name}.{pname}: valor por defecto "
                    f"{gp.get('default')!r} -> {cp.get('default')!r} "
                    "(cambia el comportamiento sin que el cliente lo pida)")
            if gp.get("enum") != cp.get("enum"):
                g_enum = set(gp.get("enum") or [])
                c_enum = set(cp.get("enum") or [])
                lost = sorted(g_enum - c_enum)
                gained = sorted(c_enum - g_enum)
                if lost:
                    breaking.append(f"{name}.{pname}: valores retirados del enum: {lost}")
                if gained:
                    compatible.append(f"{name}.{pname}: valores nuevos en el enum: {gained}")

        if g.get("output_shape") != c.get("output_shape"):
            breaking.append(
                f"{name}: forma de la respuesta modificada "
                f"{g.get('output_shape')} -> {c.get('output_shape')}")

    return breaking, compatible


def format_diff(breaking: List[str], compatible: List[str]) -> str:
    """Reporte legible del diff (no un volcado de dos JSON distintos)."""
    if not breaking and not compatible:
        return "El contrato MCP no cambio."
    out: List[str] = []
    if breaking:
        out.append(f"RUPTURAS DE COMPATIBILIDAD ({len(breaking)}):")
        out += [f"  [X] {line}" for line in breaking]
    if compatible:
        if out:
            out.append("")
        out.append(f"Cambios compatibles ({len(compatible)}):")
        out += [f"  [+] {line}" for line in compatible]
    if breaking:
        out.append("")
        out.append("Si estos cambios son DELIBERADOS y estan aprobados, regenera el golden:")
        out.append("  python -m tests.contract_utils --write")
    return "\n".join(out)


# ---------------------------------------------------------------------- cli ---
def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    current = snapshot_from_server()

    if "--write" in argv:
        path = write_golden(current)
        print(f"Golden regenerado: {path}")
        print(f"  {current['tool_count']} tools congeladas")
        return 0

    if not GOLDEN_PATH.exists():
        print(f"No existe el golden ({GOLDEN_PATH}). Crealo con --write.", file=sys.stderr)
        return 2

    breaking, compatible = diff_snapshots(load_golden(), current)
    print(format_diff(breaking, compatible))
    return 1 if breaking else 0


if __name__ == "__main__":
    raise SystemExit(main())
