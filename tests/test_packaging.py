"""Fase 1A.1 — el paquete distribuible esta completo.

Probar `pip install -e .` NO basta: la instalacion editable resuelve todo desde
`src/` y oculta cualquier omision en `pyproject.toml`. Estas pruebas construyen
un wheel real y lo instalan en un entorno aislado.

Asi se detectaron dos defectos reales:
  - `services*` no estaba en `packages.find.include`;
  - `reporting` no estaba en `py-modules`, y el servidor moria al importar
    `tools.documentation_tools`.

Son lentas (compilan e instalan). Se marcan `packaging` para poder excluirlas:
    python -m pytest -m "not packaging"
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

import branding as _branding
from tests import test_tool_contract as _contrato

REPO = Path(__file__).resolve().parent.parent

MODULOS_SERVICES = [
    "services/__init__.py",
    "services/paths.py",
    "services/dax_guard.py",
    "services/project_state.py",
    "services/txn.py",
    "services/dual_mode.py",
    "services/envelope.py",
    "services/telemetry.py",
    "services/operations.py",
    "services/planning.py",
]
MODULOS_RAIZ = ["config.py", "logging_config.py", "reporting.py",
                "server.py", "branding.py"]

#: Se toma del contrato para que no haya dos numeros que mantener a mano.
TOOLS_ESPERADAS = _contrato.EXPECTED_COUNT

pytestmark = pytest.mark.packaging


def _correr(args, cwd=None, timeout=600):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory):
    """Construye el wheel una sola vez para todo el modulo."""
    destino = tmp_path_factory.mktemp("wheel")
    res = _correr([sys.executable, "-m", "pip", "wheel", "--no-deps",
                   "--wheel-dir", str(destino), str(REPO)], cwd=str(REPO))
    if res.returncode != 0:
        pytest.skip(f"no se pudo construir el wheel: {res.stderr[-400:]}")
    wheels = list(destino.glob("*.whl"))
    assert wheels, "pip no produjo ningun .whl"
    return wheels[0]


# ------------------------------------------------------- contenido del wheel ---
def test_el_wheel_contiene_los_modulos_de_services(wheel):
    nombres = set(zipfile.ZipFile(wheel).namelist())
    faltan = [m for m in MODULOS_SERVICES if m not in nombres]
    assert not faltan, (
        f"El wheel no incluye {faltan}. Revisa packages.find.include en "
        "pyproject.toml: sin services*, el servidor instalado no arranca.")


def test_el_wheel_contiene_los_modulos_raiz(wheel):
    nombres = set(zipfile.ZipFile(wheel).namelist())
    faltan = [m for m in MODULOS_RAIZ if m not in nombres]
    assert not faltan, (
        f"El wheel no incluye {faltan}. Revisa py-modules en pyproject.toml.")


def test_el_wheel_lleva_licencia_apache_y_notice(wheel):
    nombres = set(zipfile.ZipFile(wheel).namelist())
    assert any(nombre.endswith("/licenses/LICENSE") for nombre in nombres), (
        "el wheel no incluye LICENSE")
    assert any(nombre.endswith("/licenses/NOTICE") for nombre in nombres), (
        "el wheel no incluye NOTICE")


def test_el_wheel_lleva_el_manifiesto_de_esquemas(wheel):
    """El MANIFIESTO va en el paquete; los esquemas NO.

    Los esquemas oficiales no declaran permiso de redistribucion, asi que se
    instalan aparte con scripts/fetch_pbir_schemas.py. Lo que si tiene que
    viajar es el manifiesto con URLs y SHA-256, o el instalador no sabria que
    descargar ni contra que verificarlo.
    """
    nombres = set(zipfile.ZipFile(wheel).namelist())
    assert "services/schemas/pbir_manifest.json" in nombres, (
        "el wheel no lleva el manifiesto de esquemas PBIR")

    vendidos = [n for n in nombres if n.startswith("services/schemas/pbir/")]
    assert not vendidos, (
        f"el wheel redistribuye esquemas de terceros: {vendidos}")

    import json as _json
    manifiesto = _json.loads(
        zipfile.ZipFile(wheel).read("services/schemas/pbir_manifest.json"))
    assert len(manifiesto["documents"]) >= 5
    assert all(len(d["sha256"]) == 64 for d in manifiesto["documents"])


def test_el_wheel_contiene_los_paquetes_existentes(wheel):
    nombres = zipfile.ZipFile(wheel).namelist()
    for pkg in ("powerbi", "pbip", "tools", "utils"):
        assert any(n.startswith(f"{pkg}/") for n in nombres), f"falta {pkg}/"


def test_el_wheel_no_incluye_datos_ni_binarios(wheel):
    """Ni DLLs, ni fixtures, ni salidas: solo codigo."""
    nombres = zipfile.ZipFile(wheel).namelist()
    prohibidos = [n for n in nombres
                  if n.endswith((".dll", ".pbix", ".pbip", ".csv", ".xlsx"))
                  or n.startswith(("libs/", "outputs/", "backups/", "tests/"))]
    assert not prohibidos, f"el wheel arrastra artefactos indebidos: {prohibidos}"


# ------------------------------------------------- instalacion en aislamiento ---
@pytest.fixture(scope="module")
def entorno_instalado(wheel, tmp_path_factory):
    """venv temporal con el wheel instalado."""
    venv = tmp_path_factory.mktemp("venv") / "env"
    # --system-site-packages reutiliza mcp/psutil/dotenv del entorno padre.
    # El paquete bajo prueba NO esta instalado ahi, asi que solo puede venir
    # del wheel: es lo que se quiere aislar.
    res = _correr([sys.executable, "-m", "venv", "--system-site-packages", str(venv)])
    if res.returncode != 0:
        pytest.skip(f"no se pudo crear el venv: {res.stderr[-300:]}")
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.exists():
        pytest.skip("no se encontro el interprete del venv")
    # El venv hereda dependencias del entorno padre. Si ese entorno tiene un
    # checkout editable de la misma version, pip puede declarar el requisito
    # satisfecho y NO instalar el wheel que esta prueba pretende verificar.
    # --ignore-installed obliga a que los imports salgan del artefacto.
    res = _correr([str(py), "-m", "pip", "install", "--ignore-installed",
                   "--no-deps", "-q", str(wheel)])
    if res.returncode != 0:
        pytest.skip(f"no se pudo instalar el wheel: {res.stderr[-400:]}")
    return py


def test_el_paquete_instalado_importa_y_registra_todas_las_tools(
        entorno_instalado, tmp_path):
    """Arranque real desde el wheel, fuera del repositorio."""
    script = tmp_path / "verificar.py"
    script.write_text(
        "import json, importlib.util as u, asyncio\n"
        "faltan = [m for m in ("
        "  'services','services.paths','services.dax_guard',"
        "  'services.project_state','services.txn','server','config','reporting')\n"
        "  if u.find_spec(m) is None]\n"
        "import server\n"
        "tools = asyncio.run(server.build_server().list_tools())\n"
        "print(json.dumps({'faltan': faltan, 'n': len(tools),\n"
        "                  'nombres': sorted(t.name for t in tools)}))\n",
        encoding="utf-8")
    # cwd fuera del repo: nada puede resolverse desde src/
    res = _correr([str(entorno_instalado), str(script)], cwd=str(tmp_path))
    assert res.returncode == 0, f"el paquete instalado no arranca:\n{res.stderr[-1500:]}"

    datos = json.loads(res.stdout.strip().splitlines()[-1])
    assert datos["faltan"] == [], f"modulos no importables desde el wheel: {datos['faltan']}"
    assert datos["n"] == TOOLS_ESPERADAS, (
        f"se esperaban {TOOLS_ESPERADAS} tools y hay {datos['n']}")
    assert all(n.startswith("pbi_") for n in datos["nombres"])


def test_el_paquete_instalado_responde_al_handshake_mcp(entorno_instalado, tmp_path):
    """tools/list por stdio contra el servidor instalado desde el wheel."""
    script = tmp_path / "servidor.py"
    script.write_text("import server; server.main()\n", encoding="utf-8")

    proc = subprocess.Popen(
        [str(entorno_instalado), str(script)], cwd=str(tmp_path),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1,
        env={"PATH": "", "PBI_MCP_LOG_LEVEL": "ERROR", "SYSTEMROOT": "C:\\Windows"}
        if sys.platform == "win32" else None)
    try:
        def enviar(obj):
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()

        enviar({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "packaging", "version": "1"}}})
        init = json.loads(proc.stdout.readline())
        enviar({"jsonrpc": "2.0", "method": "notifications/initialized"})
        enviar({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        lista = json.loads(proc.stdout.readline())
    finally:
        proc.terminate()

    info = init["result"]["serverInfo"]
    assert info["name"] == "horizun-pbi-mcp"
    assert info["version"] == _branding.VERSION, (
        "serverInfo debe reportar la version del PRODUCTO, no la de la "
        "libreria mcp")
    assert len(lista["result"]["tools"]) == TOOLS_ESPERADAS


# ------------------------------------------------ source distribution (J4) ---
@pytest.fixture(scope="module")
def sdist(tmp_path_factory):
    """Construye el sdist una sola vez, en un venv propio.

    Se probaba solo el wheel. Un sdist roto no se nota hasta que alguien
    instala desde el tarball (o desde GitHub sin rueda precompilada), y
    entonces falla en su maquina, no en la nuestra.

    El interprete del equipo no trae `setuptools` ni `build` —el wheel se
    construye porque `pip wheel` aisla la compilacion—, asi que se levanta un
    venv temporal y ahi se instala `build`. NO se toca el entorno del usuario.
    Si no hay red para descargarlo, se omite diciendo por que.
    """
    entorno = tmp_path_factory.mktemp("venv_build") / "env"
    res = _correr([sys.executable, "-m", "venv", str(entorno)])
    if res.returncode != 0:
        pytest.skip(f"no se pudo crear el venv de compilacion: {res.stderr[-300:]}")
    py = entorno / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    res = _correr([str(py), "-m", "pip", "install", "-q", "build"], timeout=900)
    if res.returncode != 0:
        pytest.skip("no se pudo instalar 'build' en el venv temporal (sin red?): "
                    f"{res.stderr[-300:]}")

    destino = tmp_path_factory.mktemp("sdist")
    res = _correr([str(py), "-m", "build", "--sdist", "--outdir", str(destino),
                   str(REPO)], cwd=str(REPO), timeout=900)
    if res.returncode != 0:
        pytest.skip(f"no se pudo construir el sdist: {res.stderr[-500:]}")

    tarballs = list(destino.glob("*.tar.gz"))
    assert tarballs, ("build --sdist termino con exito pero no dejo ningun "
                      ".tar.gz: revisa pyproject.toml")
    return tarballs[0]


def test_el_sdist_lleva_el_codigo_y_la_licencia(sdist):
    import tarfile

    with tarfile.open(sdist) as tar:
        nombres = tar.getnames()

    def hay(sufijo):
        return any(n.endswith(sufijo) for n in nombres)

    for necesario in ("/src/server.py", "/src/branding.py", "/LICENSE", "/NOTICE",
                      "/pyproject.toml", "/src/services/txn.py"):
        assert hay(necesario), f"el sdist no incluye {necesario}"

    prohibidos = [n for n in nombres
                  if n.endswith((".dll", ".pbix", ".pbip"))
                  or "/libs/" in n or "/backups/" in n or "/outputs/" in n]
    assert not prohibidos, f"el sdist arrastra artefactos indebidos: {prohibidos}"


def test_el_sdist_se_instala_y_arranca(sdist, tmp_path_factory):
    """Instalacion en entorno limpio DESDE EL TARBALL, no desde el repo."""
    venv = tmp_path_factory.mktemp("venv_sdist") / "env"
    res = _correr([sys.executable, "-m", "venv", "--system-site-packages", str(venv)])
    if res.returncode != 0:
        pytest.skip(f"no se pudo crear el venv: {res.stderr[-300:]}")
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not py.exists():
        pytest.skip("no se encontro el interprete del venv")

    res = _correr([str(py), "-m", "pip", "install", "--no-deps", "-q", str(sdist)])
    if res.returncode != 0:
        pytest.skip(f"no se pudo instalar el sdist: {res.stderr[-500:]}")

    trabajo = tmp_path_factory.mktemp("fuera_del_repo")
    script = trabajo / "verificar.py"
    script.write_text(
        "import json, asyncio, server\n"
        "tools = asyncio.run(server.build_server().list_tools())\n"
        "print(json.dumps({'n': len(tools),\n"
        "                  'version': server.build_server()._mcp_server.version}))\n",
        encoding="utf-8")
    res = _correr([str(py), str(script)], cwd=str(trabajo))
    assert res.returncode == 0, f"el sdist instalado no arranca:\n{res.stderr[-1500:]}"

    datos = json.loads(res.stdout.strip().splitlines()[-1])
    assert datos["n"] == TOOLS_ESPERADAS
    assert datos["version"] == _branding.VERSION


# --------------------------------------------------- dependencias declaradas --
def _normalizar_requisito(linea: str) -> str:
    """Nombre del paquete, sin el especificador de version."""
    import re

    return re.split(r"[<>=!~\[]", linea, maxsplit=1)[0].strip()


def _dependencias_declaradas() -> dict:
    """`project.dependencies` de pyproject.toml -> {paquete: requisito}."""
    try:
        import tomllib
    except ModuleNotFoundError:                              # pragma: no cover
        import tomli as tomllib                              # Python 3.10

    raiz = Path(__file__).resolve().parents[1]
    datos = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
    return {_normalizar_requisito(d): d.strip()
            for d in datos["project"]["dependencies"]}


def _requirements_txt() -> dict:
    raiz = Path(__file__).resolve().parents[1]
    salida = {}
    for linea in (raiz / "requirements.txt").read_text(encoding="utf-8").splitlines():
        limpia = linea.split("#")[0].strip()
        if limpia:
            salida[_normalizar_requisito(limpia)] = limpia
    return salida


def test_requirements_declara_lo_mismo_que_pyproject():
    """Los dos archivos describen la MISMA instalacion, topes incluidos.

    El README ofrece `pip install -r requirements.txt` como primera opcion, asi
    que si divergen hay dos instalaciones distintas y una de ellas no se prueba
    nunca. Es lo que paso: `requirements.txt` se quedo atras en las seis
    dependencias. `mcp>=1.10` sin tope instalaba la 2.0.0 —donde
    `mcp.server.fastmcp` ya no existe— y el servidor no llegaba ni a importar;
    `jsonschema` y `referencing` faltaban del todo. Ninguna prueba lo veia
    porque todas corren sobre el entorno de desarrollo, que ya estaba bien.
    """
    pyproject, requirements = _dependencias_declaradas(), _requirements_txt()

    faltan = sorted(set(pyproject) - set(requirements))
    assert not faltan, (
        f"declaradas en pyproject y ausentes de requirements.txt: {faltan}")

    sobran = sorted(set(requirements) - set(pyproject))
    assert not sobran, (
        f"en requirements.txt y no en pyproject: {sobran}")

    distintas = {n: (pyproject[n], requirements[n]) for n in pyproject
                 if pyproject[n] != requirements[n]}
    assert not distintas, f"mismo paquete, distinta restriccion: {distintas}"


def test_toda_dependencia_de_runtime_tiene_tope_de_version():
    """Sin tope, un salto mayor entra solo y rompe una instalacion nueva.

    No es hipotetico: paso con `mcp` 2.0.0 el mismo dia que se escribio esta
    prueba, y solo se vio instalando en una maquina limpia.
    """
    sin_tope = [d for d in _dependencias_declaradas().values()
                if "<" not in d]
    assert not sin_tope, (
        f"dependencias sin tope superior: {sin_tope}. Acotalas: una version "
        "mayor puede retirar la API de la que dependemos.")


def test_el_doctor_comprueba_todas_las_dependencias_de_runtime():
    """Un diagnostico que no mira lo que importa es peor que no tenerlo.

    `doctor.py` llevaba una lista escrita a mano de tres modulos y las
    dependencias eran seis. Una instalacion sin `jsonschema` reportaba
    "Dependencias de Python: OK" y despues fallaba cada escritura PBIR con
    `schema_unavailable`.
    """
    import importlib.util

    raiz = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "doctor_bajo_prueba", raiz / "scripts" / "doctor.py")
    doctor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(doctor)

    comprobados = {pkg for _mod, pkg in doctor.DEPENDENCIAS}
    declaradas = set(_dependencias_declaradas())
    # `pythonnet` se comprueba aparte: solo hace falta para la capa en vivo.
    faltan = declaradas - comprobados - {"pythonnet"}

    assert not faltan, (
        f"doctor.py no comprueba estas dependencias: {sorted(faltan)}")
