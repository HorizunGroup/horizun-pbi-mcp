"""TEST-001 — el paquete distribuible, probado sin red de seguridad.

Probar `pip install -e .` NO basta: la instalacion editable resuelve todo desde
`src/` y oculta cualquier omision en `pyproject.toml`. Asi se detectaron dos
defectos reales en su dia: `services*` no estaba en `packages.find.include`, y
`reporting` no estaba en `py-modules`, con lo que el servidor moria al importar
`tools.documentation_tools`.

Pero la version anterior de este archivo tenia el defecto que TEST-001 nombra, y
era peor que los que cazaba: **convertia cada fallo en un skip**. Si el wheel no
se construia, skip. Si no se instalaba, skip. Si no habia venv, skip. Un
`pyproject.toml` roto salia AMARILLO, y amarillo en una suite de 2263 pruebas es
verde: nadie lo mira. Encima el venv se creaba con `--system-site-packages` y se
instalaba con `--no-deps`, de modo que las dependencias que la prueba daba por
buenas venian del entorno de desarrollo, no del paquete. Justo el modo de fallo
que `mcp 2.0.0` provoco en una maquina limpia y que aqui no se habria visto.

Lo que cambia:

* **Nada se salta.** Un fallo de build o de instalacion es un fallo, con el
  stdout y el stderr del comando en el mensaje. La unica salida es declarar el
  entorno con `PBI_MCP_PACKAGING_OFFLINE=1`, que un humano pone a mano y que CI
  tiene prohibido poner -hay una prueba que lo comprueba-.
* **Venv limpio de verdad.** Sin `--system-site-packages`.
* **Resolucion real.** Sin `--no-deps`: pip resuelve e instala las diez
  dependencias declaradas, que es lo unico que demuestra que se pueden resolver.
* **Wheel Y sdist**, construidos con `python -m build` -que compila el wheel A
  PARTIR del sdist- y ambos pasados por `twine check`.
* **Fuera del checkout.** Cada verificacion corre en un directorio ajeno, con
  `PYTHONPATH` vaciado, y comprueba que el modulo importado vive en el
  `site-packages` del venv y no en `src/`.
* **El entry point**, ejecutado: el handshake MCP va contra el ejecutable de
  consola instalado, no contra un `python -c`.

Lo que esto **no** demuestra, y no se va a insinuar que si: que una instalacion
`pip` quede operativa. El wheel no lleva DLL de Analysis Services, ni esquemas
PBIR, ni bootstrap. Eso es **INSTALL-005 y sigue abierta**; aqui hay una prueba
que lo deja escrito para que el verde de este archivo no se lea como lo que no
es.

Son lentas (compilan, descargan y resuelven). Se marcan `packaging`:
    python -m pytest -m "not packaging"
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

import horizun_pbi_mcp.branding as _branding
from tests import test_tool_contract as _contrato

REPO = Path(__file__).resolve().parent.parent

#: Unica causa declarada de skip, y la pone una persona, no la prueba.
OFFLINE = os.environ.get("PBI_MCP_PACKAGING_OFFLINE") == "1"

MODULOS_SERVICES = [
    "horizun_pbi_mcp/services/__init__.py",
    "horizun_pbi_mcp/services/paths.py",
    "horizun_pbi_mcp/services/dax_guard.py",
    "horizun_pbi_mcp/services/project_state.py",
    "horizun_pbi_mcp/services/txn.py",
    "horizun_pbi_mcp/services/dual_mode.py",
    "horizun_pbi_mcp/services/envelope.py",
    "horizun_pbi_mcp/services/telemetry.py",
    "horizun_pbi_mcp/services/operations.py",
    "horizun_pbi_mcp/services/planning.py",
]
MODULOS_RAIZ = ["horizun_pbi_mcp/config.py", "horizun_pbi_mcp/logging_config.py",
                "horizun_pbi_mcp/reporting.py", "horizun_pbi_mcp/server.py",
                "horizun_pbi_mcp/branding.py", "horizun_pbi_mcp/__init__.py"]

#: Se toma del contrato para que no haya dos numeros que mantener a mano.
TOOLS_ESPERADAS = _contrato.EXPECTED_COUNT

#: Los dos ejecutables de consola que declara pyproject.
ENTRY_POINTS = ("horizun-pbi-mcp", "powerbi-mcp")

pytestmark = pytest.mark.packaging


def _exigir(args, *, que, cwd=None, timeout=2400, env=None):
    """Ejecuta y EXIGE exito. Un fallo aqui es un fallo, no un skip."""
    res = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                         text=True, timeout=timeout, env=env,
                         encoding="utf-8", errors="replace")
    assert res.returncode == 0, (
        f"{que} fallo con codigo {res.returncode}.\n"
        f"--- comando ---\n{' '.join(str(a) for a in args)}\n"
        f"--- stdout ---\n{(res.stdout or '')[-4000:]}\n"
        f"--- stderr ---\n{(res.stderr or '')[-4000:]}")
    return res


def _entorno_limpio(venv: Path) -> dict:
    """Entorno del subproceso sin ninguna via de vuelta al checkout.

    `PYTHONPATH` vaciado -pytest pone `src` ahi- y `PYTHONHOME` fuera. El PATH
    se conserva porque pythonnet necesita encontrar el runtime de .NET, pero con
    los Scripts del venv por delante.
    """
    entorno = dict(os.environ)
    entorno.pop("PYTHONHOME", None)
    entorno["PYTHONPATH"] = ""
    entorno["PATH"] = str(_scripts(venv)) + os.pathsep + entorno.get("PATH", "")
    entorno["PBI_MCP_LOG_LEVEL"] = "ERROR"
    return entorno


def _scripts(venv: Path) -> Path:
    return venv / ("Scripts" if sys.platform == "win32" else "bin")


def _python_de(venv: Path) -> Path:
    return _scripts(venv) / ("python.exe" if sys.platform == "win32" else "python")


def _crear_venv(destino: Path, que: str) -> Path:
    """Venv LIMPIO. Sin --system-site-packages: es el punto de la prueba."""
    _exigir([sys.executable, "-m", "venv", destino], que=f"crear el venv de {que}")
    py = _python_de(destino)
    assert py.exists(), f"el venv de {que} no dejo interprete en {py}"
    _exigir([py, "-m", "pip", "install", "--upgrade", "-q", "pip"],
            que=f"actualizar pip en el venv de {que}")
    return py


# ------------------------------------------------------------- artefactos ---
@pytest.fixture(scope="module")
def artefactos(tmp_path_factory):
    """Wheel y sdist construidos UNA vez con `python -m build`, y `twine check`.

    `python -m build` sin argumentos construye el sdist y despues el wheel *a
    partir del sdist*. Es mas estricto que `pip wheel`: si algo falta en el
    sdist, el wheel que sale de el tambien lo pierde, y se ve aqui en vez de en
    la maquina de quien instale desde el tarball.
    """
    if OFFLINE:
        pytest.skip("PBI_MCP_PACKAGING_OFFLINE=1 declarado a mano: sin indice "
                    "no hay resolucion real que probar")

    entorno = tmp_path_factory.mktemp("venv_build") / "env"
    py = _crear_venv(entorno, "compilacion")
    _exigir([py, "-m", "pip", "install", "-q", "build", "twine"],
            que="instalar build y twine")

    destino = tmp_path_factory.mktemp("dist")
    _exigir([py, "-m", "build", "--outdir", destino, REPO], cwd=REPO,
            que="construir wheel y sdist con python -m build")

    wheels = list(destino.glob("*.whl"))
    tarballs = list(destino.glob("*.tar.gz"))
    assert len(wheels) == 1, f"se esperaba un wheel y hay {[w.name for w in wheels]}"
    assert len(tarballs) == 1, f"se esperaba un sdist y hay {[t.name for t in tarballs]}"

    res = _exigir([py, "-m", "twine", "check", "--strict",
                   destino / "*"], que="twine check --strict")
    assert "PASSED" in res.stdout.upper() or "passed" in res.stdout.lower(), (
        f"twine check no declaro PASSED:\n{res.stdout}")

    return {"wheel": wheels[0], "sdist": tarballs[0], "dist": destino,
            "python_build": py}


@pytest.fixture(scope="module")
def venv_wheel(artefactos, tmp_path_factory):
    """El wheel instalado en un venv limpio, CON resolucion real de dependencias."""
    entorno = tmp_path_factory.mktemp("venv_wheel") / "env"
    py = _crear_venv(entorno, "wheel")
    _exigir([py, "-m", "pip", "install", "-q", artefactos["wheel"]],
            que="instalar el wheel con sus dependencias resueltas")
    return entorno


@pytest.fixture(scope="module")
def venv_sdist(artefactos, tmp_path_factory):
    """Lo mismo desde el tarball: es el camino de quien instala sin rueda."""
    entorno = tmp_path_factory.mktemp("venv_sdist") / "env"
    py = _crear_venv(entorno, "sdist")
    _exigir([py, "-m", "pip", "install", "-q", artefactos["sdist"]],
            que="instalar el sdist con sus dependencias resueltas")
    return entorno


@pytest.fixture
def fuera_del_checkout(tmp_path):
    """Un directorio de trabajo que no es el repositorio ni esta debajo de el."""
    d = tmp_path / "fuera"
    d.mkdir()
    assert REPO not in d.parents, "el directorio de trabajo cae dentro del repo"
    return d


# ------------------------------------------------------- contenido del wheel ---
def test_el_wheel_contiene_los_modulos_de_services(artefactos):
    nombres = set(zipfile.ZipFile(artefactos["wheel"]).namelist())
    faltan = [m for m in MODULOS_SERVICES if m not in nombres]
    assert not faltan, (
        f"El wheel no incluye {faltan}. Revisa packages.find.include en "
        "pyproject.toml: sin services*, el servidor instalado no arranca.")


def test_el_wheel_contiene_los_modulos_raiz(artefactos):
    nombres = set(zipfile.ZipFile(artefactos["wheel"]).namelist())
    faltan = [m for m in MODULOS_RAIZ if m not in nombres]
    assert not faltan, (
        f"El wheel no incluye {faltan}. Revisa py-modules en pyproject.toml.")


def test_el_wheel_solo_ocupa_un_nombre_de_primer_nivel(artefactos):
    """Lo que se instala en site-packages es de todos, no solo nuestro.

    El wheel llego a instalar DIEZ nombres de primer nivel: `config`, `server`,
    `services`, `tools`, `utils`, `pbip`, `powerbi`, `reporting`,
    `logging_config` y `branding`. Cuatro de ellos estan entre los mas comunes
    de Python. En cualquier entorno donde otro paquete -o el propio script del
    usuario- hiciera `import config`, uno de los dos ganaba y el otro se
    rompia, en la direccion que tocara ese dia.
    """
    primer_nivel = set()
    for n in zipfile.ZipFile(artefactos["wheel"]).namelist():
        cabeza = n.split("/")[0]
        if cabeza.endswith((".dist-info", ".data")):
            continue
        primer_nivel.add(cabeza)

    assert primer_nivel == {"horizun_pbi_mcp"}, (
        f"El wheel instala {sorted(primer_nivel)} en site-packages. Debe "
        "instalar UN solo nombre: horizun_pbi_mcp.")


def test_el_wheel_lleva_licencia_apache_y_notice(artefactos):
    nombres = set(zipfile.ZipFile(artefactos["wheel"]).namelist())
    assert any(n.endswith("/licenses/LICENSE") for n in nombres), (
        "el wheel no incluye LICENSE")
    assert any(n.endswith("/licenses/NOTICE") for n in nombres), (
        "el wheel no incluye NOTICE")


def test_el_wheel_declara_los_dos_entry_points(artefactos):
    """Sin `entry_points.txt` no hay ejecutable de consola que instalar."""
    zf = zipfile.ZipFile(artefactos["wheel"])
    nombre = next((n for n in zf.namelist() if n.endswith("/entry_points.txt")), None)
    assert nombre, "el wheel no declara entry points"
    texto = zf.read(nombre).decode("utf-8")
    for ep in ENTRY_POINTS:
        assert f"{ep} = horizun_pbi_mcp.server:main" in texto, (
            f"falta el entry point {ep}:\n{texto}")


def test_el_wheel_lleva_el_manifiesto_de_esquemas(artefactos):
    """El MANIFIESTO va en el paquete; los esquemas NO.

    Los esquemas oficiales no declaran permiso de redistribucion, asi que se
    instalan aparte con scripts/fetch_pbir_schemas.py. Lo que si tiene que
    viajar es el manifiesto con URLs y SHA-256, o el instalador no sabria que
    descargar ni contra que verificarlo.
    """
    zf = zipfile.ZipFile(artefactos["wheel"])
    nombres = set(zf.namelist())
    assert "horizun_pbi_mcp/services/schemas/pbir_manifest.json" in nombres, (
        "el wheel no lleva el manifiesto de esquemas PBIR")

    vendidos = [n for n in nombres
                if n.startswith("horizun_pbi_mcp/services/schemas/pbir/")]
    assert not vendidos, f"el wheel redistribuye esquemas de terceros: {vendidos}"

    manifiesto = json.loads(
        zf.read("horizun_pbi_mcp/services/schemas/pbir_manifest.json"))
    assert len(manifiesto["documents"]) >= 5
    assert all(len(d["sha256"]) == 64 for d in manifiesto["documents"])


def test_el_wheel_contiene_los_paquetes_existentes(artefactos):
    nombres = zipfile.ZipFile(artefactos["wheel"]).namelist()
    # `lifecycle` es el nucleo compartido del ciclo de vida. Si no viaja en el
    # wheel, la CLI empaquetada no puede prepararse a si misma (INSTALL-005) y
    # volveriamos a tener una implementacion en `scripts/` y otra en el paquete.
    for pkg in ("powerbi", "pbip", "tools", "utils", "lifecycle"):
        assert any(n.startswith(f"horizun_pbi_mcp/{pkg}/") for n in nombres), (
            f"falta horizun_pbi_mcp/{pkg}/")


def test_el_wheel_no_incluye_datos_ni_binarios(artefactos):
    """Ni DLLs, ni fixtures, ni salidas: solo codigo."""
    nombres = zipfile.ZipFile(artefactos["wheel"]).namelist()
    prohibidos = [n for n in nombres
                  if n.endswith((".dll", ".pbix", ".pbip", ".csv", ".xlsx"))
                  or n.startswith(("libs/", "outputs/", "backups/", "tests/"))]
    assert not prohibidos, f"el wheel arrastra artefactos indebidos: {prohibidos}"


# ------------------------------------------------------- contenido del sdist ---
def test_el_sdist_lleva_el_codigo_y_la_licencia(artefactos):
    with tarfile.open(artefactos["sdist"]) as tar:
        nombres = tar.getnames()

    def hay(sufijo):
        return any(n.endswith(sufijo) for n in nombres)

    for necesario in ("/src/horizun_pbi_mcp/server.py",
                      "/src/horizun_pbi_mcp/branding.py", "/LICENSE", "/NOTICE",
                      "/pyproject.toml", "/src/horizun_pbi_mcp/services/txn.py"):
        assert hay(necesario), f"el sdist no incluye {necesario}"

    prohibidos = [n for n in nombres
                  if n.endswith((".dll", ".pbix", ".pbip"))
                  or "/libs/" in n or "/backups/" in n or "/outputs/" in n]
    assert not prohibidos, f"el sdist arrastra artefactos indebidos: {prohibidos}"


def test_el_sdist_lleva_el_manifiesto_de_esquemas(artefactos):
    """Si falta aqui, el wheel construido A PARTIR del sdist tampoco lo tendra."""
    with tarfile.open(artefactos["sdist"]) as tar:
        nombres = tar.getnames()
    assert any(n.endswith("/src/horizun_pbi_mcp/services/schemas/pbir_manifest.json")
               for n in nombres), "el sdist no lleva el manifiesto de esquemas"


# --------------------------------------------- el paquete instalado, de verdad ---
def _verificar_desde(venv: Path, trabajo: Path, extra: str = "") -> dict:
    script = trabajo / "verificar.py"
    script.write_text(
        "import json, importlib.util as u, asyncio, pathlib\n"
        "P = 'horizun_pbi_mcp.'\n"
        "faltan = [m for m in ("
        "  P[:-1], P+'services', P+'services.paths', P+'services.dax_guard',"
        "  P+'services.project_state', P+'services.txn', P+'server',"
        "  P+'config', P+'reporting')\n"
        "  if u.find_spec(m) is None]\n"
        "import horizun_pbi_mcp\n"
        "from horizun_pbi_mcp import server\n"
        "tools = asyncio.run(server.build_server().list_tools())\n"
        "print(json.dumps({'faltan': faltan, 'n': len(tools),\n"
        "                  'origen': str(pathlib.Path(horizun_pbi_mcp.__file__).resolve()),\n"
        "                  'nombres': sorted(t.name for t in tools)}))\n"
        + extra,
        encoding="utf-8")
    res = _exigir([_python_de(venv), script], cwd=trabajo,
                  env=_entorno_limpio(venv),
                  que="arrancar el paquete instalado fuera del checkout")
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("cual", ["wheel", "sdist"])
def test_el_paquete_instalado_importa_y_registra_todas_las_tools(
        cual, request, fuera_del_checkout):
    venv = request.getfixturevalue(f"venv_{cual}")
    datos = _verificar_desde(venv, fuera_del_checkout)

    assert datos["faltan"] == [], f"modulos no importables desde el {cual}: {datos['faltan']}"
    assert datos["n"] == TOOLS_ESPERADAS, (
        f"se esperaban {TOOLS_ESPERADAS} tools y hay {datos['n']}")
    assert all(n.startswith("pbi_") for n in datos["nombres"])


@pytest.mark.parametrize("cual", ["wheel", "sdist"])
def test_el_paquete_importado_viene_del_venv_y_no_del_checkout(
        cual, request, fuera_del_checkout):
    """La prueba mas facil de aprobar sin querer.

    Si `src/` se cuela por PYTHONPATH o por el cwd, todo lo demas pasa aunque el
    artefacto este vacio: se estaria probando el repositorio otra vez.
    """
    venv = request.getfixturevalue(f"venv_{cual}")
    origen = Path(_verificar_desde(venv, fuera_del_checkout)["origen"])

    assert venv.resolve() in origen.parents, (
        f"el paquete se importo desde {origen}, fuera del venv {venv}")
    assert (REPO / "src") not in origen.parents, (
        f"el paquete se importo del arbol de fuentes: {origen}")


@pytest.mark.parametrize("cual", ["wheel", "sdist"])
def test_las_dependencias_declaradas_estan_realmente_instaladas(
        cual, request, fuera_del_checkout):
    """`--no-deps` hacia que esto no significara nada.

    El entorno padre tenia las dependencias, el venv las heredaba con
    `--system-site-packages` y la prueba las daba por resueltas sin que pip
    hubiera resuelto una sola. Es el modo de fallo exacto de `mcp 2.0.0`: una
    restriccion mal declarada solo se ve cuando alguien resuelve de cero.
    """
    venv = request.getfixturevalue(f"venv_{cual}")
    esperadas = sorted(_dependencias_declaradas())

    script = fuera_del_checkout / "deps.py"
    script.write_text(
        "import json, importlib.metadata as m\n"
        "req = json.loads(open('esperadas.json').read())\n"
        "print(json.dumps({p: m.version(p) for p in req}))\n",
        encoding="utf-8")
    (fuera_del_checkout / "esperadas.json").write_text(
        json.dumps(esperadas), encoding="utf-8")

    res = _exigir([_python_de(venv), script], cwd=fuera_del_checkout,
                  env=_entorno_limpio(venv),
                  que=f"leer las versiones instaladas en el venv del {cual}")
    versiones = json.loads(res.stdout.strip().splitlines()[-1])
    assert sorted(versiones) == esperadas, (
        f"faltan dependencias en el venv del {cual}: "
        f"{sorted(set(esperadas) - set(versiones))}")


@pytest.mark.parametrize("cual", ["wheel", "sdist"])
@pytest.mark.parametrize("entry_point", ENTRY_POINTS)
def test_el_entry_point_instalado_responde_al_handshake_mcp(
        cual, entry_point, request, fuera_del_checkout):
    """tools/list por stdio contra el EJECUTABLE instalado.

    Antes esto se probaba con un `python -c "from ... import server; main()"`,
    que verifica el modulo pero no el ejecutable: un entry point mal declarado
    -o ausente- pasaba en verde y fallaba al instalar de verdad.
    """
    venv = request.getfixturevalue(f"venv_{cual}")
    exe = _scripts(venv) / (entry_point + (".exe" if sys.platform == "win32" else ""))
    assert exe.exists(), f"pip no instalo el ejecutable {exe}"

    proc = subprocess.Popen(
        [str(exe)], cwd=str(fuera_del_checkout), stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", bufsize=1, env=_entorno_limpio(venv))
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
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:                      # pragma: no cover
            proc.kill()

    info = init["result"]["serverInfo"]
    assert info["name"] == "horizun-pbi-mcp"
    assert info["version"] == _branding.VERSION, (
        "serverInfo debe reportar la version del PRODUCTO, no la de la "
        "libreria mcp")
    assert len(lista["result"]["tools"]) == TOOLS_ESPERADAS


# ------------------------------------- lo que el paquete NO trae (INSTALL-005) --
def test_el_wheel_no_finge_traer_lo_que_no_trae(artefactos):
    """TEST-001 no cierra INSTALL-005, y este archivo no puede sugerir que si.

    El wheel es codigo y nada mas: sin DLL de Analysis Services, sin esquemas
    PBIR y sin bootstrap. Una instalacion `pip` pura NO queda operativa, y el
    gate que exige que `pbi_health_check` lo enumere en vez de aparentar
    normalidad es G3.6, que sigue pendiente de una maquina limpia.

    Esta prueba fija la verdad de hoy: si algun dia el wheel EMPIEZA a traer
    esas piezas, falla y obliga a revisar INSTALL-005 en vez de dejar que el
    alcance cambie en silencio.
    """
    nombres = zipfile.ZipFile(artefactos["wheel"]).namelist()
    assert not [n for n in nombres if n.endswith(".dll")], (
        "el wheel empezo a traer DLLs: revisa INSTALL-005 y la licencia")
    assert not [n for n in nombres
                if n.startswith("horizun_pbi_mcp/services/schemas/pbir/")], (
        "el wheel empezo a traer esquemas PBIR: revisa la redistribucion")
    assert not [n for n in nombres if "/scripts/" in n or n.startswith("scripts/")], (
        "el wheel empezo a traer los scripts de instalacion: revisa INSTALL-005")


# ---------------------------------------- las reglas que hacen valer lo de arriba --
def _codigo_bajo_prueba() -> str:
    """El texto de este archivo SIN la zona de meta-comprobaciones.

    Sin este corte las dos guardas de abajo se citan a si mismas: buscan
    `--no-deps` y lo encuentran en su propia asercion, de modo que fallarian
    siempre y por el motivo equivocado. Se parte por la ULTIMA aparicion de la
    marca, porque la primera es la constante que la define.
    """
    texto = Path(__file__).read_text(encoding="utf-8")
    cuerpo, marca, _ = texto.rpartition(MARCA_META)
    assert marca, "falta la marca que separa las meta-comprobaciones"
    return cuerpo


MARCA_META = "# @@ META @@"


# @@ META @@
def test_ninguna_prueba_de_packaging_convierte_un_fallo_en_skip():
    """La regresion que TEST-001 nombra: volver a poner un `pytest.skip`.

    Se permite exactamente uno, el de `PBI_MCP_PACKAGING_OFFLINE`, que declara
    una persona y CI tiene prohibido poner.
    """
    skips = [l.strip() for l in _codigo_bajo_prueba().splitlines()
             if "pytest.skip(" in l and not l.strip().startswith("#")]
    assert len(skips) == 1, f"skips en test_packaging: {skips}"
    assert "OFFLINE" in skips[0], (
        f"el unico skip permitido es el de entorno declarado, y es: {skips[0]}")


def test_el_venv_de_packaging_es_limpio_y_resuelve_de_verdad():
    """Estatica sobre este archivo: las dos trampas de la version anterior."""
    codigo = "\n".join(l for l in _codigo_bajo_prueba().splitlines()
                       if not l.lstrip().startswith("#"))
    # Se buscan como ARGUMENTO entre comillas, no como palabra suelta: la prosa
    # de la cabecera los nombra a proposito para explicar que se quito.
    for trampa in ('"--system-site-packages"', "'--system-site-packages'",
                   '"--no-deps"', "'--no-deps'",
                   '"--ignore-installed"', "'--ignore-installed'"):
        assert trampa not in codigo, (
            f"volvio {trampa}: el venv deja de ser limpio o deja de resolver")


def test_ci_prueba_las_dos_versiones_de_python_declaradas():
    """G8.2/G8.3 se cumplen en CI o no se cumplen."""
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for version in ('"3.10"', '"3.13"'):
        assert version in ci, f"la matriz de CI no cubre Python {version}"
    assert "PBI_MCP_PACKAGING_OFFLINE" not in ci, (
        "CI no puede declararse offline: eso reintroduce el skip que TEST-001 "
        "vino a quitar")


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
    dependencias. `mcp>=1.10` sin tope instalaba la 2.0.0 -donde
    `mcp.server.fastmcp` ya no existe- y el servidor no llegaba ni a importar;
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
