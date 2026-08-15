"""La clase de riesgo declarada tiene que coincidir con lo que el codigo hace.

`tools/risk.py` es una tabla escrita a mano, y una tabla a mano se desfasa. Estas
pruebas son el oraculo que lo impide: no comprueban que la tabla sea consistente
consigo misma —eso no probaria nada—, sino que **contrastan cada entrada contra
la evidencia del codigo**, leida por AST.

Los tres desfases que se pueden colar, y quien los caza aqui:

1. Alguien anade una tool y no la clasifica
   -> `test_toda_tool_registrada_esta_clasificada`.
2. Alguien marca de solo lectura algo que escribe
   -> `test_ninguna_clase_de_lectura_usa_guard_mutation` y
      `test_read_only_estricto_no_emite_ficheros`.
3. Alguien anade una tool destructiva y se olvida del `confirm`
   -> `test_las_destructivas_exigen_confirm` (invariante 6 de AGENTS.md).
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
from typing import Dict, Set

import pytest

from horizun_pbi_mcp.tools import risk

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src" / "horizun_pbi_mcp"

#: Marcador de escritura de fichero sin homonimo en la stdlib. Se eligio por eso:
#: `write_text` o `replace` habrian dado falsos positivos (`str.replace`).
MARCAS_DE_ESCRITURA = {"atomic_write_text", "atomic_write_json"}


# ------------------------------------------------------------- utilidades ---
def _llamadas(node: ast.AST) -> Set[str]:
    nombres = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            nombres.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return nombres


def _definiciones() -> Dict[str, list]:
    """Todas las funciones de src/, por nombre, para seguir el grafo de llamadas."""
    defs: Dict[str, list] = {}
    for p in SRC.rglob("*.py"):
        arbol = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(arbol):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(n.name, []).append(n)
    return defs


def _alcanza(node: ast.AST, objetivo: Set[str], defs: Dict[str, list],
             prof: int = 4, visto: Set[str] | None = None) -> bool:
    """Si desde `node` se llega a alguna funcion de `objetivo` en `prof` saltos."""
    visto = visto if visto is not None else set()
    for llamada in _llamadas(node):
        if llamada in objetivo:
            return True
        if prof > 0 and llamada in defs and llamada not in visto:
            visto.add(llamada)
            for sub in defs[llamada][:3]:
                if _alcanza(sub, objetivo, defs, prof - 1, visto):
                    return True
    return False


@pytest.fixture(scope="module")
def tools_ast() -> Dict[str, ast.FunctionDef]:
    """Cuerpo AST de cada tool `pbi_*`, leido del fuente."""
    encontradas: Dict[str, ast.FunctionDef] = {}
    for p in sorted((SRC / "tools").glob("*_tools.py")):
        arbol = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(arbol):
            if isinstance(n, ast.FunctionDef) and n.name.startswith("pbi_"):
                encontradas[n.name] = n
    return encontradas


@pytest.fixture(scope="module")
def anotaciones_publicadas() -> Dict[str, object]:
    """Lo que un cliente MCP recibe de verdad, no lo que creemos haber puesto."""
    import sys

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from horizun_pbi_mcp.server import build_server

    tools = asyncio.run(build_server().list_tools())
    return {t.name: t.annotations for t in tools}


@pytest.fixture(scope="module")
def defs_globales() -> Dict[str, list]:
    return _definiciones()


# ------------------------------------------------------- cobertura de tabla ---
def test_toda_tool_registrada_esta_clasificada(anotaciones_publicadas):
    """Una tool nueva sin clasificar tiene que romper la suite, no colarse."""
    sin_clasificar = sorted(set(anotaciones_publicadas) - set(risk.RISK_BY_TOOL))
    assert not sin_clasificar, (
        "Estas tools no tienen clase de riesgo en tools/risk.py: "
        f"{sin_clasificar}. Sin entrada se anuncian como destructivas, que es "
        "lo seguro, pero la tabla debe decirlo explicitamente.")


def test_la_tabla_no_tiene_entradas_muertas(anotaciones_publicadas):
    sobran = sorted(set(risk.RISK_BY_TOOL) - set(anotaciones_publicadas))
    assert not sobran, (
        f"tools/risk.py clasifica tools que ya no existen: {sobran}")


def test_toda_tool_publica_sus_anotaciones(anotaciones_publicadas):
    """El oraculo es `list_tools()`: da igual como se hayan puesto."""
    mudas = sorted(n for n, a in anotaciones_publicadas.items() if a is None)
    assert not mudas, f"Tools sin annotations en el handshake MCP: {mudas}"


# --------------------------------------------- la tabla contra el codigo ---
def test_ninguna_clase_de_lectura_usa_guard_mutation(tools_ast):
    """Si llama a `guard_mutation`, muta. No puede anunciarse como lectura."""
    mentirosas = []
    for nombre, clase in sorted(risk.RISK_BY_TOOL.items()):
        if clase not in risk.CLASES_DE_LECTURA:
            continue
        cuerpo = tools_ast.get(nombre)
        if cuerpo is not None and "guard_mutation" in _llamadas(cuerpo):
            mentirosas.append(f"{nombre} ({clase})")
    assert not mentirosas, (
        "Clasificadas como lectura pero usan guard_mutation: "
        f"{mentirosas}. O la clase esta mal, o la tool no deberia mutar.")


def test_toda_tool_que_muta_esta_clasificada_como_escritura(tools_ast):
    """El reverso del anterior: la evidencia manda sobre la tabla."""
    faltan = []
    for nombre, cuerpo in sorted(tools_ast.items()):
        if "guard_mutation" not in _llamadas(cuerpo):
            continue
        clase = risk.RISK_BY_TOOL.get(nombre)
        if clase in risk.CLASES_DE_LECTURA:
            faltan.append(f"{nombre} ({clase})")
    assert not faltan, (
        f"Usan guard_mutation pero se anuncian como lectura: {faltan}")


def test_read_only_estricto_no_emite_ficheros(tools_ast, defs_globales):
    """`read_only` promete que no deja rastro. Si escribe, es `read_only_emits_file`."""
    escriben = []
    for nombre, clase in sorted(risk.RISK_BY_TOOL.items()):
        if clase != risk.READ_ONLY:
            continue
        cuerpo = tools_ast.get(nombre)
        if cuerpo is not None and _alcanza(cuerpo, MARCAS_DE_ESCRITURA,
                                           defs_globales):
            escriben.append(nombre)
    assert not escriben, (
        f"Clasificadas read_only pero alcanzan una escritura de fichero: "
        f"{escriben}. Su clase es READ_ONLY_EMITS_FILE.")


def test_las_destructivas_exigen_confirm(tools_ast):
    """Invariante 6 de AGENTS.md, comprobado sobre la firma real.

    Cubria solo `WRITE_DESTRUCTIVE`, y por ese hueco se escapaban las dos de
    refresh —`WRITE_IRREVERSIBLE`—, que son las que MAS lo necesitan: son las
    unicas de las 134 que se anunciaban destructivas sin nada que confirmar.
    Desde CONTRACT-003 se exige a todas las que el cliente ve como
    destructivas.
    """
    sin_confirm = []
    for nombre, clase in sorted(risk.RISK_BY_TOOL.items()):
        if clase not in risk.CLASES_DESTRUCTIVAS:
            continue
        cuerpo = tools_ast.get(nombre)
        if cuerpo is None:
            continue
        params = {a.arg for a in cuerpo.args.args + cuerpo.args.kwonlyargs}
        if "confirm" not in params:
            sin_confirm.append(nombre)
    assert not sin_confirm, (
        f"Destructivas sin parametro confirm: {sin_confirm}")


def test_toda_tool_con_confirm_se_anuncia_destructiva(tools_ast):
    """Si el propio autor le puso `confirm`, el cliente merece saberlo."""
    mal = []
    for nombre, cuerpo in sorted(tools_ast.items()):
        params = {a.arg for a in cuerpo.args.args + cuerpo.args.kwonlyargs}
        if "confirm" not in params:
            continue
        if risk.RISK_BY_TOOL.get(nombre) not in risk.CLASES_DESTRUCTIVAS:
            mal.append(f"{nombre} ({risk.RISK_BY_TOOL.get(nombre)})")
    assert not mal, f"Piden confirm pero no se anuncian destructivas: {mal}"


# ------------------------------------------------------------ fallar cerrado ---
def test_una_tool_desconocida_se_anuncia_destructiva():
    """El descuido tiene que costar una advertencia de mas, nunca una de menos."""
    a = risk.annotations_for("pbi_tool_que_no_existe")
    assert a["readOnlyHint"] is False
    assert a["destructiveHint"] is True


def test_las_de_solo_lectura_se_anuncian_como_tales(anotaciones_publicadas):
    for nombre, clase in risk.RISK_BY_TOOL.items():
        anotacion = anotaciones_publicadas.get(nombre)
        if anotacion is None:
            continue
        esperado = clase in (risk.READ_ONLY, risk.READ_EXTERNAL)
        assert anotacion.readOnlyHint is esperado, (
            f"{nombre} ({clase}) publica readOnlyHint={anotacion.readOnlyHint}")


def test_solo_sharepoint_declara_dominio_abierto(anotaciones_publicadas):
    """Solo el conector Graph sale del equipo y debe decirlo honestamente."""
    abiertas = sorted(n for n, a in anotaciones_publicadas.items()
                      if a is not None and a.openWorldHint)
    assert abiertas == ["pbi_sharepoint_download_folder",
                        "pbi_sharepoint_list_folder"]
