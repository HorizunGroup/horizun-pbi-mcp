"""Reproduce los 4 puntos de feat/perfil-local contra un servidor MCP real.

Uso:  python repro.py <variante> <src_dir> <trabajo_dir>
  variante: etiqueta (main | rama)
  src_dir:  carpeta src/ del checkout a probar (va a PYTHONPATH del servidor)
  trabajo:  carpeta vacia donde se crea el PBIP de prueba (datos sinteticos)

Habla con el servidor por stdio, como un cliente MCP. Las salidas del servidor
(session.json, backups) quedan aisladas dentro de <trabajo>/outputs.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

VARIANTE, SRC, TRABAJO = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
ABRIR = "--sin-desktop" not in sys.argv
LIBS = Path(sys.argv[sys.argv.index("--libs") + 1]) if "--libs" in sys.argv else None


def _env(perfil: str) -> dict:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(SRC),
        "HORIZUN_PBI_MCP_OUTPUTS_DIR": str(TRABAJO / "outputs"),
        "HORIZUN_PBI_MCP_BACKUPS_DIR": str(TRABAJO / "backups"),
        "HORIZUN_PBI_MCP_PROFILE": perfil,
        "HORIZUN_PBI_MCP_LOG_LEVEL": "WARNING",
    })
    if LIBS:
        env["HORIZUN_PBI_MCP_LIBS_DIR"] = str(LIBS)
    return env


def _texto(res) -> dict:
    partes = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    crudo = "\n".join(partes)
    try:
        return json.loads(crudo)
    except ValueError:
        return {"_raw": crudo}


async def catalogo(perfil: str) -> dict:
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "horizun_pbi_mcp.server"],
                                   env=_env(perfil), cwd=str(TRABAJO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            payload = json.dumps([t.model_dump(exclude_none=True) for t in tools],
                                 ensure_ascii=False)
            return {"profile": perfil, "tools": len(tools),
                    "catalog_chars": len(payload),
                    # ~4 caracteres por token: estimacion, no conteo exacto
                    "catalog_tokens_est": len(payload) // 4,
                    "has_relationship": any(t.name == "pbi_create_relationship"
                                            for t in tools)}


async def flujo() -> dict:
    out: dict = {"variante": VARIANTE}
    datos = TRABAJO / "datos"
    datos.mkdir(parents=True, exist_ok=True)
    (datos / "ventas.csv").write_text(
        "fecha,semana,monto\n2026-01-05,1,100\n2026-01-06,1,50\n"
        "2026-01-12,2,75\n2026-01-19,3,20\n", encoding="utf-8")
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "horizun_pbi_mcp.server"],
                                   env=_env("full"), cwd=str(TRABAJO))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(nombre, **args):
                t0 = time.monotonic()
                res = _texto(await s.call_tool(nombre, args))
                res["_seconds"] = round(time.monotonic() - t0, 1)
                out.setdefault("calls", []).append({"tool": nombre, "args": args,
                                                    "result": res})
                return res

            await call("pbi_create_pbip_project", out_dir=str(TRABAJO / "pbip"),
                       name="Verif", culture="es-ES")
            # Punto 4b: ruta RELATIVA al cwd del servidor.
            await call("pbi_add_table_from_file", path="datos/ventas.csv",
                       table_name="Ventas")
            # Punto 1: tabla calculada con columna explicita.
            await call("pbi_create_calculated_table", name="Semanas",
                       expression='DISTINCT(SELECTCOLUMNS(Ventas, "semana", Ventas[semana]))',
                       columns=[{"name": "semana", "data_type": "int64"}])
            await call("pbi_create_relationship", from_table="Ventas",
                       from_column="semana", to_table="Semanas",
                       to_column="semana")
            # Punto 2
            await call("pbi_validate_tmdl")
            if ABRIR:
                # Punto 3: abrir en Desktop (60 s para no esperar 300).
                await call("pbi_open_in_desktop", timeout=60)
    defn = next((TRABAJO / "pbip").rglob("definition/tables"))
    out["semanas_tmdl"] = (defn / "Semanas.tmdl").read_text(encoding="utf-8-sig")
    out["ventas_tmdl"] = (defn / "Ventas.tmdl").read_text(encoding="utf-8-sig")
    return out


async def main() -> None:
    TRABAJO.mkdir(parents=True, exist_ok=True)
    resultado = {"catalogos": [await catalogo("full"), await catalogo("local")],
                 "flujo": await flujo()}
    destino = TRABAJO / f"resultado-{VARIANTE}.json"
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    print(destino)


asyncio.run(main())
