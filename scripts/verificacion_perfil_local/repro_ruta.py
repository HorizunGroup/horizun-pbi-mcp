"""Punto 4b: tabla desde archivo con ruta RELATIVA -> abrir, refrescar, contar.

Uso: python repro_ruta.py <variante> <src_dir> <trabajo_dir> <libs_dir>
Sin tabla calculada, para no mezclarlo con el punto 1.
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

VARIANTE, SRC, TRABAJO, LIBS = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]


async def main() -> None:
    (TRABAJO / "datos").mkdir(parents=True, exist_ok=True)
    (TRABAJO / "datos" / "ventas.csv").write_text(
        "fecha,semana,monto\n2026-01-05,1,100\n2026-01-06,1,50\n2026-01-12,2,75\n",
        encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(SRC), "HORIZUN_PBI_MCP_LIBS_DIR": LIBS,
           "HORIZUN_PBI_MCP_OUTPUTS_DIR": str(TRABAJO / "outputs"),
           "HORIZUN_PBI_MCP_BACKUPS_DIR": str(TRABAJO / "backups")}
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "horizun_pbi_mcp.server"],
                                   env=env, cwd=str(TRABAJO))
    pasos = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(nombre, **args):
                t0 = time.monotonic()
                res = await s.call_tool(nombre, args)
                texto = "".join(c.text for c in res.content if c.type == "text")
                try:
                    d = json.loads(texto)
                except ValueError:
                    d = {"_raw": texto}
                resumen = {k: d.get(k) for k in ("ok", "error", "message", "rows",
                                                 "rows_by_table", "warnings")
                           if k in d}
                pasos.append({"tool": nombre, "s": round(time.monotonic() - t0, 1),
                              **resumen})
                return d

            await call("pbi_create_pbip_project", out_dir=str(TRABAJO / "pbip"),
                       name="Ruta")
            await call("pbi_add_table_from_file", path="datos/ventas.csv",
                       table_name="Ventas")
            abierto = await call("pbi_open_in_desktop", timeout=120)
            if abierto.get("ok"):
                await call("pbi_refresh_model", confirm=True)
                await call("pbi_run_dax",
                           query='EVALUATE ROW("filas", COUNTROWS(Ventas), "monto", SUM(Ventas[monto]))')
                await call("pbi_close_desktop", confirm=True)
    salida = TRABAJO / f"ruta-{VARIANTE}.json"
    salida.write_text(json.dumps(pasos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(pasos, ensure_ascii=False, indent=1)[:4000])


asyncio.run(main())
