"""INSTALL-010 — `ready` solo si el runtime sirve el PRODUCTO, no algo parecido.

El healthcheck ya hablaba MCP, que era el salto grande. Pero lo que exigia era
mas flojo de lo que el criterio documentado prometia:

  - `MINIMO_TOOLS = 100`, con el contrato en 134. Un runtime al que le faltaran
    34 tools pasaba.
  - `serverInfo` no se miraba: cualquier servidor MCP que hubiera quedado en el
    venv contaba como el nuestro.
  - bastaba con que todos los nombres empezaran por `pbi_`, asi que 134 nombres
    inventados con ese prefijo pasaban igual que los 134 de verdad.
  - la version que anunciaba el servidor no se comparaba con la que se acababa
    de preparar.
  - tras matar un proceso que no terminaba, `returncode = None` se aceptaba.

Los cinco fallan hacia el lado optimista, que es el peor: dan por buena una
instalacion rota y el error reaparece mucho despues, en el cliente, con un
mensaje que no menciona la instalacion.

Las pruebas usan runtimes FALSOS pero que arrancan de verdad
(`tests/runtime_falso.py`): un venv real y un servidor stdio de biblioteca
estandar al que se le pide que mienta de una forma concreta cada vez.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_falso                                          # noqa: E402

from horizun_pbi_mcp.lifecycle import healthcheck             # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
CONTRATO = runtime_falso.nombres_del_contrato()


@pytest.fixture
def runtime(tmp_path):
    """Fabrica de runtimes que arrancan. Devuelve la ruta del interprete."""
    contador = {"n": 0}

    def crear(**kw):
        contador["n"] += 1
        carpeta = tmp_path / f"rt{contador['n']}"
        return runtime_falso.crear(carpeta, **kw)

    return crear


def _verificar(python: Path, **kw):
    return healthcheck.verificar(python, cwd=python.parent, timeout=60, **kw)


# ============================================================================
# Una sola fuente para los 134 nombres
# ============================================================================
def test_el_baseline_empaquetado_y_el_golden_dicen_lo_mismo():
    """Dos listas de 134 nombres mantenidas a mano divergen. Esta se deriva.

    Si esta prueba falla, alguien regenero el golden sin regenerar el baseline:
      python -m tests.contract_utils --write
    """
    import contract_utils

    golden = json.loads(
        contract_utils.GOLDEN_PATH.read_text(encoding="utf-8"))
    baseline = json.loads(
        contract_utils.BASELINE_PATH.read_text(encoding="utf-8"))

    assert baseline == contract_utils.baseline_desde(golden), (
        "el contrato empaquetado no coincide con el golden. Regeneralo con: "
        "python -m tests.contract_utils --write")


def test_el_baseline_viaja_dentro_del_paquete_y_no_en_tests():
    """El runtime instalado no puede depender de que exista `tests/`."""
    assert healthcheck.BASELINE.is_file()
    assert healthcheck.BASELINE.parent.name == "lifecycle"
    assert "tests" not in healthcheck.BASELINE.parts

    declarado = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    assert "contract_baseline.json" in declarado, (
        "el contrato no esta declarado como package-data: el wheel se "
        "instalaria sin el y el healthcheck fallaria cerrado en cada equipo")


def test_el_contrato_declara_todas_las_tools_y_el_servidor():
    from tests.test_tool_contract import EXPECTED_COUNT

    c = healthcheck.contrato()
    assert c["server"] == "horizun-pbi-mcp"
    assert len(c["tools"]) == len(CONTRATO) == EXPECTED_COUNT
    assert set(c["tools"]) == set(CONTRATO)


# ============================================================================
# El camino bueno
# ============================================================================
def test_un_runtime_que_sirve_el_contrato_entero_pasa(runtime):
    py = runtime(version="2.0.0")
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is True, veredicto
    assert veredicto["tools"] == len(CONTRATO)
    assert veredicto["servidor"] == "horizun-pbi-mcp"


def test_las_tools_de_MAS_no_rompen_nada(runtime):
    """Ampliar el producto no puede convertirse en una instalacion fallida."""
    py = runtime(version="2.0.0", nombres=CONTRATO + ["pbi_tool_nueva"])
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is True, veredicto
    assert veredicto["extra"] == ["pbi_tool_nueva"]


# ============================================================================
# Lo que tiene que RECHAZAR
# ============================================================================
def test_cien_tools_ya_no_bastan(runtime):
    """El umbral viejo era 100, y el contrato ya iba por 134."""
    py = runtime(version="2.0.0", nombres=CONTRATO[:100])
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["tools"] == 100
    assert f"faltan {len(CONTRATO) - 100}" in veredicto["error"], veredicto


def test_una_tool_del_contrato_ausente_se_rechaza(runtime):
    """Una sola tool de menos ya rompe a quien la tuviera configurada."""
    py = runtime(version="2.0.0",
                 nombres=[n for n in CONTRATO if n != "pbi_run_dax"])
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["tools"] == len(CONTRATO) - 1
    assert veredicto["faltan"] == ["pbi_run_dax"], veredicto


def test_una_tool_del_contrato_sustituida_se_rechaza(runtime):
    """El caso mas dificil, y el que un recuento nunca vera: el numero CUADRA.

    Cualquier oraculo basado en "¿cuantas hay?" da esto por bueno. Solo mirar
    QUE tools son distingue el producto de algo que se le parece.
    """
    py = runtime(version="2.0.0",
                 nombres=[n for n in CONTRATO if n != "pbi_run_dax"]
                 + ["pbi_relleno"])
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["tools"] == len(CONTRATO)
    assert veredicto["faltan"] == ["pbi_run_dax"], veredicto


def test_los_nombres_pbi_equivocados_se_rechazan(runtime):
    """Contarlas y mirarles el prefijo no distingue el producto de un remedo."""
    py = runtime(version="2.0.0",
                 nombres=[f"pbi_inventada_{n}" for n in range(len(CONTRATO))])
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["tools"] == len(CONTRATO), "el numero cuadraba: por eso colaba"
    assert f"faltan {len(CONTRATO)}" in veredicto["error"], veredicto


def test_otro_servidor_mcp_en_el_venv_no_cuela(runtime):
    py = runtime(version="2.0.0", servidor="algun-otro-servidor")
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] == "server-info", veredicto
    assert "algun-otro-servidor" in veredicto["error"]


def test_una_version_distinta_de_la_preparada_no_cuela(runtime):
    """Promover 2.0.0 y que arranque 1.5.4 significa que pip no hizo nada."""
    py = runtime(version="1.5.4")
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] == "version", veredicto
    assert "1.5.4" in veredicto["error"] and "2.0.0" in veredicto["error"]


def test_sin_version_esperada_no_se_inventa_una(runtime):
    """Quien no sabe que version espera no puede exigir ninguna."""
    py = runtime(version="lo-que-sea")
    assert _verificar(py)["ok"] is True


def test_un_print_de_depuracion_en_stdout_se_rechaza(runtime):
    """stdout es el canal JSON-RPC: un `print` rompe al cliente sin romper una
    prueba unitaria."""
    py = runtime(version="2.0.0", basura=True)
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] == "stdout-sucio", veredicto


def test_la_basura_escrita_DESPUES_de_tools_list_se_rechaza(runtime):
    """El lector paraba en cuanto veía la respuesta con `id: 2`.

    Todo lo que el servidor escribiera después —un `print` de despedida, un
    `atexit`, el aviso de una librería al descargarse— quedaba sin mirar. Y eso
    es basura en el canal JSON-RPC igual que la del principio: llega al cliente
    en mitad de la sesión, no en el arranque, que es cuando es más difícil de
    diagnosticar.
    """
    py = runtime(version="2.0.0", basura_al_cerrar=True)
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False, (
        "dio por bueno un runtime que ensucia stdout al apagarse")
    assert veredicto["fase"] == "stdout-sucio", veredicto
    assert "apagando" in veredicto["error"], veredicto


def test_responder_en_otro_orden_no_es_un_falso_negativo(runtime):
    """`tools/list` antes que `initialize`: las dos válidas, solo desordenadas.

    Leer hasta EOF obliga a no depender del orden de llegada. Un falso negativo
    aquí rechazaría un runtime bueno y tumbaría una instalación que iba bien.
    """
    py = runtime(version="2.0.0", invierte=True)
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is True, veredicto
    assert veredicto["tools"] == len(CONTRATO)


def test_un_runtime_que_no_arranca_se_rechaza_sin_esperar_el_timeout(runtime):
    py = runtime(version="2.0.0", muere=True)
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] in ("sin-respuesta", "timeout"), veredicto


@pytest.mark.parametrize("cuerpo,fase", [
    ('{"jsonrpc":"2.0","id":2,"result":{"tools":"no-es-una-lista"}}',
     "tools-list-malformado"),
    ('{"jsonrpc":"2.0","id":2,"result":{"tools":[{"sin":"nombre"}]}}',
     "tools-list-malformado"),
    ('{"jsonrpc":"2.0","id":2,"result":{}}',
     "tools-list-malformado"),
    ('{"jsonrpc":"2.0","id":2,"error":{"code":-1,"message":"no"}}',
     "tools-list"),
])
def test_un_tools_list_malformado_se_rechaza(tmp_path, cuerpo, fase):
    """Un `result` que no es una lista de tools con nombre no lo puede usar
    ningun cliente, aunque el JSON-RPC sea impecable."""
    carpeta = tmp_path / "rt"
    py = runtime_falso.crear(carpeta, version="2.0.0")
    servidor = _servidor_a_medida(py, cuerpo)
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] == fase, (veredicto, servidor)


def _servidor_a_medida(python: Path, respuesta_tools: str) -> Path:
    """Reescribe el servidor del runtime para que conteste eso a tools/list."""
    sp = subprocess.run(
        [str(python), "-c",
         "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True, timeout=120).stdout.strip()
    destino = Path(sp) / "horizun_pbi_mcp" / "server.py"
    destino.write_text(
        "import json, sys\n"
        "for linea in sys.stdin:\n"
        "    linea = linea.strip()\n"
        "    if not linea:\n"
        "        continue\n"
        "    p = json.loads(linea)\n"
        "    if p.get('method') == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': p['id'],\n"
        "            'result': {'protocolVersion': '2024-11-05',\n"
        "                       'capabilities': {},\n"
        "                       'serverInfo': {'name': 'horizun-pbi-mcp',\n"
        "                                      'version': '2.0.0'}}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "    elif p.get('method') == 'tools/list':\n"
        f"        sys.stdout.write({respuesta_tools!r} + '\\n')\n"
        "        sys.stdout.flush()\n",
        encoding="utf-8")
    return destino


# ============================================================================
# Nunca un proceso suelto
# ============================================================================
def _servidor_que_ignora_el_cierre(python: Path) -> None:
    sp = subprocess.run(
        [str(python), "-c",
         "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        capture_output=True, text=True, check=True, timeout=120).stdout.strip()
    (Path(sp) / "horizun_pbi_mcp" / "server.py").write_text(
        "import json, sys, time\n"
        f"TOOLS = [{{'name': n, 'inputSchema': {{}}}} for n in {CONTRATO!r}]\n"
        "for linea in sys.stdin:\n"
        "    linea = linea.strip()\n"
        "    if not linea:\n"
        "        continue\n"
        "    p = json.loads(linea)\n"
        "    if p.get('method') == 'initialize':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': p['id'],\n"
        "            'result': {'protocolVersion': '2024-11-05',\n"
        "                       'capabilities': {},\n"
        "                       'serverInfo': {'name': 'horizun-pbi-mcp',\n"
        "                                      'version': '2.0.0'}}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "    elif p.get('method') == 'tools/list':\n"
        "        sys.stdout.write(json.dumps({'jsonrpc': '2.0', 'id': p['id'],\n"
        "            'result': {'tools': TOOLS}}) + '\\n')\n"
        "        sys.stdout.flush()\n"
        "# Se acabo el stdin y aun asi no se muere: el defecto que se persigue.\n"
        "time.sleep(600)\n",
        encoding="utf-8")


def test_un_runtime_que_no_muere_al_cerrar_stdin_se_rechaza_y_no_queda_suelto(
        tmp_path):
    """Contestar bien no basta si luego no se va.

    Un servidor que ignora el EOF de stdin deja un proceso vivo -con su runtime
    entero abierto- por CADA arranque del cliente. Antes esto pasaba el
    healthcheck: se le mataba, `returncode` quedaba en `None` y `None` estaba
    en la lista de codigos aceptables.
    """
    py = runtime_falso.crear(tmp_path / "rt", version="2.0.0")
    _servidor_que_ignora_el_cierre(py)

    veredicto = healthcheck.verificar(py, cwd=tmp_path, timeout=60,
                                      version_esperada="2.0.0")

    assert veredicto["ok"] is False, "dio por bueno un runtime que no se apaga"
    assert veredicto["fase"] == "no-termina", veredicto
    assert veredicto["tools"] == len(CONTRATO), "contesto bien: por eso colaba"
    assert _procesos_hijos_de(py) == [], (
        f"quedaron procesos sueltos: {_procesos_hijos_de(py)}")


def _procesos_hijos_de(python: Path) -> list:
    """PIDs vivos que esten ejecutando ESE interprete. Sin dependencias."""
    if os.name != "nt":
        salida = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True,
                                text=True, timeout=60).stdout
        return [l for l in salida.splitlines() if str(python) in l]
    consulta = (
        "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
        f"Where-Object {{ $_.ExecutablePath -eq '{python}' }} | "
        "ForEach-Object {{ $_.ProcessId }}")
    salida = subprocess.run(
        ["powershell", "-NoProfile", "-Command", consulta],
        capture_output=True, text=True, timeout=120).stdout
    return [l for l in salida.split() if l.strip()]


def test_el_healthcheck_falla_cerrado_si_falta_el_contrato(runtime, monkeypatch):
    """Sin contrato no se puede comprobar, y eso no puede valer por 'esta bien'."""
    py = runtime(version="2.0.0")
    monkeypatch.setattr(healthcheck, "BASELINE",
                        healthcheck.BASELINE.with_name("no-existe.json"))
    veredicto = _verificar(py, version_esperada="2.0.0")
    assert veredicto["ok"] is False
    assert veredicto["fase"] == "contrato", veredicto
