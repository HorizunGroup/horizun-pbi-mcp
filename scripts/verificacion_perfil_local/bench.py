"""Banco del perfil local: 18 tareas tipicas contra un modelo pequeno.

Uso:
  python bench.py preparar <trabajo>
  python bench.py correr <trabajo> <motor> <perfil> [--solo N,M]
     motor: haiku (claude -p --model haiku) | qwen (Ollama qwen3:8b, bucle propio)
     perfil: local | full

Cada tarea corre sobre una COPIA del proyecto base (datos sinteticos), con su
propio outputs/ aislado. El acierto se juzga por el resultado en disco (TMDL /
PBIR) o, en las tareas de consulta, por la respuesta; nunca por lo que el
modelo dice que hizo.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
#: El checkout a medir; por defecto este mismo.
SRC = Path(os.environ.get("BANCO_SRC", RAIZ / "src"))
LIBS = Path(os.environ.get("HORIZUN_PBI_MCP_LIBS_DIR", RAIZ / "libs"))
PY = Path(sys.executable)
CLAUDE = Path(os.environ.get("CLAUDE_EXE", "claude"))
OLLAMA = "http://localhost:11434/api/chat"
MAX_TURNOS = 12


def env_servidor(outputs: Path, perfil: str) -> dict:
    return {"PYTHONPATH": str(SRC), "HORIZUN_PBI_MCP_OUTPUTS_DIR": str(outputs),
            "HORIZUN_PBI_MCP_BACKUPS_DIR": str(outputs / "backups"),
            "HORIZUN_PBI_MCP_LIBS_DIR": str(LIBS),
            "HORIZUN_PBI_MCP_PROFILE": perfil,
            "HORIZUN_PBI_MCP_LOG_LEVEL": "WARNING"}


def _fuera(proyecto: Path) -> Path:
    """outputs/ y backups/ FUERA del proyecto: el servidor rechaza backups dentro."""
    destino = proyecto.parent / f"_{proyecto.name}-outputs"
    if destino.exists():
        shutil.rmtree(destino)
    return destino


# --------------------------------------------------------------- tareas ---

def _tabla(p: Path, nombre: str) -> str:
    f = next(p.rglob(f"definition/tables/{nombre}.tmdl"), None)
    return f.read_text(encoding="utf-8-sig") if f else ""


def _relaciones(p: Path) -> str:
    f = next(p.rglob("definition/relationships.tmdl"), None)
    return f.read_text(encoding="utf-8-sig") if f else ""


def _visuales_de_pagina(p: Path, nombre: str) -> int:
    for page in p.rglob("definition/pages/*/page.json"):
        d = json.loads(page.read_text(encoding="utf-8-sig"))
        if d.get("displayName", "").casefold() == nombre.casefold():
            return len(list((page.parent / "visuals").glob("*/visual.json")))
    return -1


def _medida(nombre):
    return lambda p, r: re.search(rf"^\s*measure\s+'?{re.escape(nombre)}'?\s*=",
                                  _tabla(p, "Ventas"), re.M) is not None


def _rel(t1, c1, t2, c2):
    def ok(p, r):
        rel = _relaciones(p)
        return (f"fromColumn: {t1}.{c1}" in rel and f"toColumn: {t2}.{c2}" in rel)
    return ok


def _calc(nombre, columna):
    def ok(p, r):
        t = _tabla(p, nombre)
        return "= calculated" in t and f"sourceColumn: [{columna}]" in t
    return ok


TAREAS = [
    dict(id=1, tipo="medida", esperada="pbi_create_measure",
         texto="Crea en la tabla Ventas una medida llamada 'Promedio Monto' que sea el promedio de Ventas[monto].",
         ok=_medida("Promedio Monto")),
    dict(id=2, tipo="medida", esperada="pbi_create_measure",
         texto="Agrega la medida 'Num Ventas' en la tabla Ventas que cuente las filas de Ventas.",
         ok=_medida("Num Ventas")),
    dict(id=3, tipo="medida", esperada="pbi_create_measure",
         texto="Crea la medida 'Monto Max' en Ventas con el monto maximo de Ventas[monto].",
         ok=_medida("Monto Max")),
    dict(id=4, tipo="medida", esperada="pbi_create_measure",
         texto="Necesito una medida 'Monto Semana 1' en Ventas: la suma de Ventas[monto] solo para Ventas[semana] = 1.",
         ok=_medida("Monto Semana 1")),
    dict(id=5, tipo="tabla_calculada", esperada="pbi_create_calculated_table",
         texto="Crea una tabla calculada llamada Semanas con los valores distintos de Ventas[semana] (una columna 'semana').",
         ok=_calc("Semanas", "semana")),
    dict(id=6, tipo="tabla_calculada", esperada="pbi_create_calculated_table",
         texto="Crea una tabla calculada 'ProductosUnicos' con DISTINCT de Ventas[producto]; la columna se llama 'producto'.",
         ok=_calc("ProductosUnicos", "producto")),
    dict(id=7, tipo="tabla_calculada", esperada="pbi_create_calculated_table",
         texto="Haz una tabla calculada 'Meses' con CALENDAR(DATE(2026,1,1), DATE(2026,3,31)); su columna es 'Date' de tipo fecha.",
         ok=_calc("Meses", "Date")),
    dict(id=8, tipo="relacion", esperada="pbi_create_relationship",
         texto="Relaciona Ventas[producto] con Productos[producto] (muchos a uno).",
         ok=_rel("Ventas", "producto", "Productos", "producto")),
    dict(id=9, tipo="relacion", esperada="pbi_create_relationship",
         texto="Crea la relacion entre Ventas[fecha] y Calendario[Date].",
         ok=_rel("Ventas", "fecha", "Calendario", "Date")),
    dict(id=10, tipo="tabla+relacion", esperada="pbi_create_calculated_table",
         texto="Crea una tabla calculada Semanas con los valores distintos de Ventas[semana] (columna 'semana') y relaciona Ventas[semana] con Semanas[semana].",
         ok=lambda p, r: _calc("Semanas", "semana")(p, r) and _rel("Ventas", "semana", "Semanas", "semana")(p, r)),
    dict(id=11, tipo="pagina", esperada="pbi_create_page_from_spec",
         texto="Crea una pagina 'Resumen' con 3 visuales: una tarjeta con Ventas[Total Monto], un grafico de barras de Ventas[Total Monto] por Ventas[producto] y una tabla con Ventas[fecha] y Ventas[monto].",
         ok=lambda p, r: _visuales_de_pagina(p, "Resumen") == 3),
    dict(id=12, tipo="pagina", esperada="pbi_create_page_from_spec",
         texto="Agrega la pagina 'Tarjetas' con tres tarjetas: Ventas[Total Monto], Ventas[Total Monto] con titulo 'Monto 2' y Ventas[Total Monto] con titulo 'Monto 3'.",
         ok=lambda p, r: _visuales_de_pagina(p, "Tarjetas") == 3),
    dict(id=13, tipo="pagina", esperada="pbi_create_page_from_spec",
         texto="Crea la pagina 'Detalle' con un grafico de columnas de Ventas[Total Monto] por Ventas[semana], una tarjeta de Ventas[Total Monto] y una tabla de Productos[producto] y Productos[categoria].",
         ok=lambda p, r: _visuales_de_pagina(p, "Detalle") == 3),
    dict(id=14, tipo="validar", esperada="pbi_validate_tmdl",
         texto="Valida el TMDL del proyecto y dime si tiene errores. Responde al final con una linea 'ERRORES: si' o 'ERRORES: no'.",
         ok=lambda p, r: re.search(r"ERRORES:\s*no", r or "", re.I) is not None),
    dict(id=15, tipo="validar", esperada="pbi_validate_tmdl", base="defecto",
         texto="Valida el TMDL del proyecto y dime que columna tiene un problema. Responde al final con 'COLUMNA: <tabla>[<columna>]'.",
         ok=lambda p, r: re.search(r"COLUMNA:\s*'?Semanas'?\[semana\]", r or "", re.I) is not None),
    dict(id=16, tipo="columna", esperada="pbi_create_calculated_column",
         texto="Crea en Ventas la columna calculada 'Monto IVA' igual a Ventas[monto] * 1.19, tipo double.",
         ok=lambda p, r: re.search(r"column\s+'?Monto IVA'?\s*=", _tabla(p, "Ventas")) is not None),
    dict(id=17, tipo="consulta", esperada="pbi_list_tables",
         texto="Lista las tablas del modelo con sus columnas. Termina con una linea 'TABLAS: <n>' con el numero de tablas.",
         ok=lambda p, r: re.search(r"TABLAS:\s*3\b", r or "", re.I) is not None),
    dict(id=18, tipo="importar", esperada="pbi_add_table_from_file",
         texto="Importa el archivo datos/clientes.csv de la carpeta del proyecto como una tabla llamada Clientes.",
         ok=lambda p, r: "Csv.Document" in _tabla(p, "Clientes")),
]


# ---------------------------------------------------------- preparacion ---

async def _llamar_servidor(outputs: Path, cwd: Path, llamadas):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=str(PY), args=["-m", "horizun_pbi_mcp.server"],
                                   env={**os.environ, **env_servidor(outputs, "full")},
                                   cwd=str(cwd))
    salida = []
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for nombre, args in llamadas:
                res = await s.call_tool(nombre, args)
                texto = "".join(c.text for c in res.content if c.type == "text")
                salida.append((nombre, texto[:300]))
                if '"ok": false' in texto.replace("\n", ""):
                    raise SystemExit(f"{nombre} fallo: {texto[:800]}")
    return salida


def preparar(trabajo: Path) -> None:
    base = trabajo / "base"
    if base.exists():
        shutil.rmtree(base)
    (base / "datos").mkdir(parents=True)
    (base / "datos" / "ventas.csv").write_text(
        "fecha,semana,producto,monto\n2026-01-05,1,A,100\n2026-01-06,1,B,50\n"
        "2026-01-12,2,A,75\n2026-01-19,3,C,20\n", encoding="utf-8")
    (base / "datos" / "productos.csv").write_text(
        "producto,categoria\nA,Uno\nB,Uno\nC,Dos\n", encoding="utf-8")
    (base / "datos" / "clientes.csv").write_text(
        "cliente,ciudad\n1,Norte\n2,Sur\n", encoding="utf-8")
    asyncio.run(_llamar_servidor(_fuera(base), base, [
        ("pbi_create_pbip_project", {"out_dir": str(base), "name": "Banco",
                                     "subfolder": False}),
        ("pbi_add_table_from_file", {"path": str(base / "datos" / "ventas.csv"),
                                     "table_name": "Ventas"}),
        ("pbi_add_table_from_file", {"path": str(base / "datos" / "productos.csv"),
                                     "table_name": "Productos"}),
        ("pbi_create_calculated_table", {
            "name": "Calendario",
            "expression": "CALENDAR(DATE(2026,1,1), DATE(2026,1,31))",
            "columns": [{"name": "Date", "data_type": "dateTime"}]}),
        ("pbi_create_measure", {"table": "Ventas", "name": "Total Monto",
                                "expression": "SUM(Ventas[monto])", "mode": "pbip"}),
    ]))
    # Base con el defecto del punto 1: tabla calculada sin corchetes + relacion.
    defecto = trabajo / "base-defecto"
    if defecto.exists():
        shutil.rmtree(defecto)
    shutil.copytree(base, defecto)
    asyncio.run(_llamar_servidor(_fuera(defecto), defecto, [
        ("pbi_open_pbip_project", {"path": str(defecto)}),
        ("pbi_create_calculated_table", {
            "name": "Semanas",
            "expression": 'DISTINCT(SELECTCOLUMNS(Ventas, "semana", Ventas[semana]))',
            "columns": [{"name": "semana", "data_type": "int64"}]}),
        ("pbi_create_relationship", {"from_table": "Ventas", "from_column": "semana",
                                     "to_table": "Semanas", "to_column": "semana"}),
    ]))
    f = next(defecto.rglob("definition/tables/Semanas.tmdl"))
    f.write_text(f.read_text(encoding="utf-8-sig").replace(
        "sourceColumn: [semana]", "sourceColumn: semana"), encoding="utf-8")
    print("bases listas en", trabajo)


# ------------------------------------------------------------- motores ---

PROMPT = ("Trabajas con el servidor MCP de Power BI. El proyecto PBIP esta en "
          "{proyecto} (Power BI Desktop esta cerrado). Activa ese proyecto y "
          "luego haz esto:\n{tarea}")


def correr_haiku(dir_tarea: Path, prompt: str, perfil: str) -> dict:
    cfg = dir_tarea / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"pbi": {
        "type": "stdio", "command": str(PY), "args": ["-m", "horizun_pbi_mcp.server"],
        "env": env_servidor(_fuera(dir_tarea), perfil)}}}), encoding="utf-8")
    cmd = [str(CLAUDE), "-p", prompt, "--model", "haiku",
           "--mcp-config", str(cfg), "--strict-mcp-config", "--tools", "",
           "--allowedTools", "mcp__pbi", "--output-format", "stream-json",
           "--verbose", "--max-turns", str(MAX_TURNOS), "--no-session-persistence"]
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=dir_tarea, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=900)
    (dir_tarea / "stream.jsonl").write_text(proc.stdout, encoding="utf-8")
    llamadas, final, uso, errores = [], "", {}, 0
    for linea in proc.stdout.splitlines():
        try:
            ev = json.loads(linea)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for c in ev["message"].get("content", []):
                if c.get("type") == "tool_use":
                    llamadas.append(c["name"].replace("mcp__pbi__", ""))
        if ev.get("type") == "user":
            for c in ev["message"].get("content", []):
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    cuerpo = json.dumps(c.get("content"), ensure_ascii=False)
                    if c.get("is_error") or '\\"ok\\": false' in cuerpo:
                        errores += 1
        if ev.get("type") == "result":
            final = ev.get("result") or ""
            uso = ev.get("usage") or {}
    tokens_in = sum(uso.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens",
                                             "cache_creation_input_tokens"))
    return {"llamadas": llamadas, "respuesta": final, "tokens_in": tokens_in,
            "tokens_out": uso.get("output_tokens", 0),
            "segundos": round(time.monotonic() - t0, 1), "errores": errores,
            "stderr": proc.stderr[-500:]}


def _ollama(mensajes, herramientas):
    cuerpo = json.dumps({"model": "qwen3:8b", "messages": mensajes, "tools": herramientas,
                         "stream": False, "think": False,
                         "options": {"num_ctx": 32768, "temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA, data=cuerpo,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


async def _qwen(dir_tarea: Path, prompt: str, perfil: str) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=str(PY), args=["-m", "horizun_pbi_mcp.server"],
                                   env={**os.environ, **env_servidor(_fuera(dir_tarea), perfil)},
                                   cwd=str(dir_tarea))
    llamadas, tin, tout, final, errores = [], 0, 0, "", 0
    t0 = time.monotonic()
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            herramientas = [{"type": "function", "function": {
                "name": t.name, "description": t.description or "",
                "parameters": t.inputSchema}} for t in tools]
            mensajes = [{"role": "system", "content":
                         "Eres un asistente que usa herramientas para editar proyectos de Power BI. "
                         "Usa las herramientas; no inventes resultados."},
                        {"role": "user", "content": prompt}]
            for _ in range(MAX_TURNOS):
                resp = await asyncio.to_thread(_ollama, mensajes, herramientas)
                tin += resp.get("prompt_eval_count", 0)
                tout += resp.get("eval_count", 0)
                msg = resp["message"]
                mensajes.append(msg)
                if not msg.get("tool_calls"):
                    final = msg.get("content", "")
                    break
                for tc in msg["tool_calls"]:
                    nombre = tc["function"]["name"]
                    args = tc["function"].get("arguments") or {}
                    llamadas.append(nombre)
                    try:
                        res = await s.call_tool(nombre, args)
                        texto = "".join(c.text for c in res.content if c.type == "text")
                        if res.isError or '"ok": false' in texto:
                            errores += 1
                    except Exception as exc:  # noqa: BLE001
                        texto = f"error: {exc}"
                        errores += 1
                    mensajes.append({"role": "tool", "content": texto[:6000],
                                     "tool_name": nombre})
    (dir_tarea / "conversacion.json").write_text(
        json.dumps(mensajes, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"llamadas": llamadas, "respuesta": final, "tokens_in": tin, "tokens_out": tout,
            "segundos": round(time.monotonic() - t0, 1), "errores": errores}


ORIENTACION = {"pbi_open_pbip_project"}
#: Lecturas que un modelo puede hacer antes de actuar sin que sea un error.
LECTURA = ORIENTACION | {"pbi_list_tables", "pbi_start_here", "pbi_health_check",
                         "pbi_find_tool", "pbi_get_power_query"}


def _cerrar_desktop_de(dir_tarea: Path) -> list:
    """Cierra SOLO los Desktop que un modelo abrio sobre la copia de esta tarea."""
    import psutil
    cerrados = []
    marca = str(dir_tarea).casefold()
    for p in psutil.process_iter(["name", "cmdline"]):
        if (p.info["name"] or "").casefold() != "pbidesktop.exe":
            continue
        if any(marca in (a or "").casefold() for a in p.info["cmdline"] or []):
            p.terminate()
            cerrados.append(p.pid)
    return cerrados


def correr(trabajo: Path, motor: str, perfil: str, solo=None) -> None:
    resultados = []
    destino = trabajo / f"bench-{motor}-{perfil}.json"
    for t in TAREAS:
        if solo and t["id"] not in solo:
            continue
        dir_tarea = trabajo / "corridas" / f"{motor}-{perfil}" / f"t{t['id']:02d}"
        if dir_tarea.exists():
            shutil.rmtree(dir_tarea)
        shutil.copytree(trabajo / ("base-defecto" if t.get("base") == "defecto" else "base"),
                        dir_tarea)
        prompt = PROMPT.format(proyecto=dir_tarea / "Banco.pbip", tarea=t["texto"])
        try:
            r = (correr_haiku(dir_tarea, prompt, perfil) if motor == "haiku"
                 else asyncio.run(_qwen(dir_tarea, prompt, perfil)))
        except Exception as exc:  # noqa: BLE001
            r = {"llamadas": [], "respuesta": "", "tokens_in": 0, "tokens_out": 0,
                 "error": repr(exc)}
        r["desktop_cerrados"] = _cerrar_desktop_de(dir_tarea)
        utiles = [c for c in r["llamadas"] if c not in ORIENTACION]
        r.update(id=t["id"], tipo=t["tipo"], esperada=t["esperada"],
                 acierto=bool(t["ok"](dir_tarea, r.get("respuesta"))),
                 primera_correcta=bool(utiles) and utiles[0] == t["esperada"],
                 primera_accion_correcta=next((c for c in r["llamadas"] if c not in LECTURA),
                                              None) == t["esperada"],
                 n_llamadas=len(r["llamadas"]))
        resultados.append(r)
        print(f"t{t['id']:02d} {t['tipo']:16} acierto={r['acierto']!s:5} "
              f"primera={r['primera_correcta']!s:5} llamadas={r['n_llamadas']} "
              f"tok_in={r['tokens_in']} {r.get('error', '')}", flush=True)
        destino.write_text(json.dumps(resultados, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    n = len(resultados)
    print(json.dumps({"motor": motor, "perfil": perfil, "tareas": n,
                      "aciertos": sum(r["acierto"] for r in resultados),
                      "primera_correcta": sum(r["primera_correcta"] for r in resultados),
                      "primera_accion_correcta": sum(r["primera_accion_correcta"] for r in resultados),
                      "llamadas_con_error": sum(r.get("errores", 0) for r in resultados),
                      "llamadas_total": sum(r["n_llamadas"] for r in resultados),
                      "tokens_in_total": sum(r["tokens_in"] for r in resultados),
                      "tokens_out_total": sum(r["tokens_out"] for r in resultados)}))


if __name__ == "__main__":
    accion, trabajo = sys.argv[1], Path(sys.argv[2])
    if accion == "preparar":
        preparar(trabajo)
    else:
        solo = None
        if "--solo" in sys.argv:
            solo = {int(x) for x in sys.argv[sys.argv.index("--solo") + 1].split(",")}
        correr(trabajo, sys.argv[3], sys.argv[4], solo)
