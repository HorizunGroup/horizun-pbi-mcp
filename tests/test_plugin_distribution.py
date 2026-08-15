"""Contrato de distribución directa como plugin de Codex y Claude."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from horizun_pbi_mcp import branding
REPO = Path(__file__).resolve().parent.parent


def _json(relative: str) -> dict:
    return json.loads((REPO / relative).read_text(encoding="utf-8"))


def _bootstrap():
    """Carga scripts/plugin_bootstrap.py, que no es parte del paquete."""
    ruta = REPO / "scripts" / "plugin_bootstrap.py"
    spec = importlib.util.spec_from_file_location("_plugin_bootstrap_bajo_prueba", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _pid_muerto() -> int:
    """Un PID que existio y ya no: mas fiable que inventarse un numero."""
    proceso = subprocess.Popen([sys.executable, "-c", "pass"])
    proceso.wait()
    return proceso.pid


def test_los_dos_manifiestos_son_coherentes_y_apache():
    for relative in (".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        manifest = _json(relative)
        assert manifest["name"] == branding.MCP_SERVER_NAME
        assert manifest["version"] == branding.VERSION
        assert manifest["license"] == "Apache-2.0"
        server = manifest["mcpServers"][branding.MCP_SERVER_NAME]
        # `cmd` + launch.cmd y NO `python` a secas: el alias de la Microsoft
        # Store se hacia pasar por Python y el plugin moria mudo antes de
        # poder autodiagnosticarse (medido en campo, sesion 2026-08-12).
        assert server["command"] == "cmd"
        assert server["args"] == ["/d", "/c",
                                  "${CLAUDE_PLUGIN_ROOT}/scripts/launch.cmd"]


def test_ningun_fichero_publicado_lleva_acentos_rotos():
    """Mojibake: UTF-8 leido como ANSI. Se publico de verdad en la 1.5.2.

    Un bump de version hecho con PowerShell 5.1 (`Get-Content -Raw` lee con la
    codepage ANSI y `WriteAllText` escribe UTF-8) convirtio 'auditoria' con
    tilde en 'auditorA-a' dentro de la descripcion de los DOS plugin.json y de
    los mensajes de instalacion que la persona ve en pantalla. Ningun test lo
    vio porque todos comprueban el contenido, no como esta codificado.

    Se revisan los ficheros que el usuario acaba leyendo: manifiestos, textos
    del instalador y documentacion de portada.
    """
    sospechosas = ("Ã", "â€", "Â\xa0", "ï»¿")
    objetivos = [
        ".claude-plugin/plugin.json", ".codex-plugin/plugin.json",
        ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json",
        ".mcp/server.json", "scripts/plugin_bootstrap.py",
        "scripts/plugin_launcher.py", "README.md", "docs/INSTALL.md",
        "CHANGELOG.md",
    ]
    sucios = []
    for relativo in objetivos:
        ruta = REPO / relativo
        if not ruta.exists():
            continue
        texto = ruta.read_text(encoding="utf-8")
        for aguja in sospechosas:
            if aguja in texto:
                linea = next((n for n, l in enumerate(texto.splitlines(), 1)
                              if aguja in l), 0)
                sucios.append(f"{relativo}:{linea} contiene {aguja!r}")
    assert not sucios, (
        "acentos rotos por codificacion (edita con UTF-8 explicito, nunca con "
        "Get-Content/WriteAllText de PowerShell):\n  " + "\n  ".join(sucios))


def test_el_lanzador_cmd_esquiva_el_alias_de_la_store():
    """launch.cmd debe resolver un Python REAL y explicar el fallo si no hay."""
    contenido = (REPO / "scripts/launch.cmd").read_text(encoding="ascii")
    assert "plugin_launcher.py" in contenido, "debe delegar en el launcher real"
    assert "WindowsApps" in contenido, "debe filtrar el alias de la Store"
    assert "1>&2" in contenido, "el remedio sale por stderr, no por el stdio MCP"
    # Un candidato se acepta por CORRER, no por existir: un py.exe huerfano
    # supera cualquier prueba de presencia y luego muere con codigo 103.
    assert "-c " in contenido, "cada candidato se prueba ejecutandolo"
    # Sin CALL, un candidato .bat/.cmd (shims de pyenv-win, wrappers
    # corporativos) se lleva el control y no lo devuelve: el lanzador moria
    # mudo justo donde promete no hacerlo.
    assert "call %*" in contenido, "los candidatos se invocan con call"
    assert "call %PYREAL%" in contenido, "el arranque final tambien usa call"


def test_el_piso_de_version_del_lanzador_sigue_al_de_pyproject():
    """Si pyproject sube el minimo, el lanzador tiene que enterarse.

    El lanzador comprueba la version ANTES de arrancar el servidor; si se
    queda atras, un Python demasiado viejo pasa el filtro y falla mucho mas
    tarde, dentro del servidor, con un error que no menciona la version.
    """
    import re

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    declarado = re.search(r'requires-python\s*=\s*">=\s*(\d+)\.(\d+)"', pyproject)
    assert declarado, "pyproject.toml debe declarar requires-python"
    esperado = (int(declarado.group(1)), int(declarado.group(2)))

    lanzador = (REPO / "scripts/launch.cmd").read_text(encoding="ascii")
    usado = re.search(r"version_info\[:2\]\s*>=\s*\((\d+),\s*(\d+)\)", lanzador)
    assert usado, "launch.cmd debe comprobar la version del candidato"
    assert (int(usado.group(1)), int(usado.group(2))) == esperado, (
        f"launch.cmd exige {usado.group(1)}.{usado.group(2)} y pyproject "
        f"{esperado[0]}.{esperado[1]}")


def _correr_lanzador(tmp_path, candidatos: dict[str, int]) -> subprocess.CompletedProcess:
    """Corre launch.cmd viendo SOLO los candidatos dados (Windows).

    `candidatos` mapea nombre -> codigo de salida del shim, para simular las
    formas medidas de "tener python y no tenerlo". Tambien se vacian las
    carpetas conocidas, o el Python real de la maquina rescataria la prueba.
    """
    binarios = tmp_path / "bin"
    binarios.mkdir(parents=True)
    for nombre, codigo in candidatos.items():
        (binarios / f"{nombre}.cmd").write_text(
            f"@echo off\nexit /b {codigo}\n", encoding="ascii")
    vacio = tmp_path / "sin-python"
    vacio.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = str(binarios)
    env["LOCALAPPDATA"] = str(vacio)
    env["ProgramFiles"] = str(vacio)
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    return subprocess.run(
        ["cmd", "/d", "/c", str(REPO / "scripts/launch.cmd")],
        env=env, input="", capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(os.name != "nt", reason="launch.cmd es el arranque de Windows")
def test_el_lanzador_distingue_python_viejo_de_python_ausente(tmp_path):
    """Los dos fallos tienen remedios distintos y no pueden dar el mismo texto."""
    viejo = _correr_lanzador(tmp_path / "viejo", {"python": 9, "py": 9})
    assert viejo.returncode == 1
    assert "anterior a 3.10" in viejo.stderr, viejo.stderr
    assert viejo.stdout == "", "un fallo no puede ensuciar el stdio MCP"

    ausente = _correr_lanzador(tmp_path / "ausente", {})
    assert ausente.returncode == 1
    assert "No hay un Python real" in ausente.stderr, ausente.stderr


@pytest.mark.skipif(os.name != "nt", reason="launch.cmd es el arranque de Windows")
def test_el_lanzador_no_se_queda_con_un_py_huerfano(tmp_path):
    """py.exe sobrevive a la desinstalacion de Python: existe y no sirve.

    Antes se elegia por existir, se moria con 'Python 3.x not found' (103) y
    el plugin quedaba mudo. Ahora ese candidato se descarta como cualquier
    otro que no corre, y el mensaje es el de siempre, con su remedio.
    """
    huerfano = _correr_lanzador(tmp_path, {"py": 103})
    assert huerfano.returncode == 1
    assert "No hay un Python real" in huerfano.stderr, huerfano.stderr
    assert "103" not in huerfano.stderr, "el codigo crudo no es un mensaje util"


def test_el_instalador_de_un_pegado_es_ascii_y_sin_admin():
    """instalar.ps1: PS 5.1 lee UTF-8 sin BOM con codepage OEM — solo ASCII.

    Y nunca debe pedir elevacion: el publico objetivo NO tiene administrador.
    """
    crudo = (REPO / "scripts/instalar.ps1").read_bytes()
    crudo.decode("ascii")  # explota si alguien cuela un acento
    texto = crudo.decode("ascii")
    assert "--scope user" in texto, "toda instalacion winget se intenta a nivel usuario"
    assert "RunAs" not in texto and "Start-Process -Verb" not in texto, (
        "el instalador no puede pedir elevacion")
    assert "CurrentUser" in texto, "la politica de ejecucion se toca solo del usuario"
    docs = (REPO / "docs/INSTALL.md").read_text(encoding="utf-8")
    # Lo que esta linea exige es que INSTALL.md ABRA con el pegado, no la forma
    # concreta que tenia. Exigia literalmente `instalar.ps1 | iex`, que era el
    # defecto de INSTALL-003: descargar de una rama y ejecutar sin mirar. Ahora
    # se comprueba lo mismo contra la fuente canonica del bloque, de modo que
    # el dia que el bloque vuelva a cambiar esta prueba siga midiendo la
    # intencion y no una cadena que ya no existe.
    canonico = (REPO / "scripts/one_paste.ps1").read_text(encoding="ascii")
    primera = next(l for l in canonico.splitlines()
                   if l.strip() and not l.startswith("#"))
    assert primera in docs, "INSTALL.md debe abrir con el pegado"
    assert "instalar.ps1 | iex" not in docs, (
        "INSTALL.md volvio al pegado que ejecuta lo que devuelva una rama")


def test_el_instalador_no_se_rinde_si_winget_no_ofrece_scope_user():
    """`--scope user` depende de como este etiquetado un manifiesto AJENO.

    Cuando un paquete no publica instalador marcado como 'user', winget
    responde `No applicable installer found` (0x8A150044) y se planta, aunque
    su instalador por defecto si deje todo en el perfil del usuario. Colgar el
    PC vacio de esa etiqueta es apostar a un dato que Microsoft puede cambiar
    sin avisar, asi que se intentan las DOS formas antes de rendirse.

    Verificado con un winget de mentira que rechaza el scope: la primera
    llamada lleva `--scope user`, la segunda no, y la instalacion sale adelante.
    """
    texto = (REPO / "scripts/instalar.ps1").read_text(encoding="ascii")
    assert "function WingetIntento" in texto, (
        "el intento de winget debe ser reutilizable para poder reintentarlo")
    assert "$conScope" in texto, "hay que poder llamar a winget sin --scope"
    # El respaldo sin scope tiene que estar EN el bucle de reintento, no como
    # comentario aspiracional.
    assert texto.count("WingetIntento $id") >= 2, (
        "faltan los dos intentos (con scope y sin scope)")
    assert "1978335189" in texto, "'ya estaba instalado' cuenta como exito"
    # Y sigue sin haber elevacion en ninguna de las dos formas.
    assert "--scope machine" not in texto, "nunca se pide instalacion por maquina"


# ------------------------------------ la instalacion no se ve ni se repite ---
#: Scripts que corren DENTRO de la instalacion automatica, es decir con un
#: padre sin consola. Cualquier subproceso que lancen tiene que pedir
#: explicitamente que no se abra ventana.
SCRIPTS_DEL_BOOTSTRAP = ("plugin_bootstrap.py", "fetch_report_validator.py",
                         "fetch_libs.py", "fetch_pbir_schemas.py")


def _llamadas_a_subprocess(ruta: Path):
    """(funcion que la contiene, nodo, ¿declara creationflags?) por llamada."""
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    for contenedor in ast.walk(arbol):
        if not isinstance(contenedor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for nodo in ast.walk(contenedor):
            if not isinstance(nodo, ast.Call):
                continue
            f = nodo.func
            if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id == "subprocess"
                    and f.attr in {"run", "Popen", "call", "check_call",
                                   "check_output"}):
                continue
            declara = any(
                k.arg == "creationflags"
                or (k.arg is None and isinstance(k.value, ast.Call)
                    and getattr(k.value.func, "id",
                                getattr(k.value.func, "attr", "")) == "flags_sin_ventana")
                for k in nodo.keywords)
            yield contenedor.name, nodo, declara


@pytest.mark.parametrize("script", SCRIPTS_DEL_BOOTSTRAP)
def test_ningun_subproceso_de_la_instalacion_estrena_ventana(script):
    """El defecto que veia la persona: una consola de `pip` en cada arranque.

    El instalador corre con DETACHED_PROCESS, o sea sin consola. Windows le
    crea una VISIBLE a cada hijo de consola salvo que ESA llamada pida
    CREATE_NO_WINDOW; el flag del padre no se hereda y redirigir stdout no
    cambia nada. Se comprueba en el AST y no en la ejecucion porque el fallo es
    justo el descuido de olvidar el flag en el siguiente script que se agregue.
    """
    ruta = REPO / "scripts" / script
    sospechosas = [f"{ruta.name}:{n.lineno} en {donde}()"
                   for donde, n, declara in _llamadas_a_subprocess(ruta) if not declara]
    assert not sospechosas, (
        "subprocesos de la instalacion sin CREATE_NO_WINDOW (usa "
        f"**flags_sin_ventana()): {sospechosas}")


def test_el_lanzador_oculta_al_instalador_y_no_al_servidor():
    """Los dos subprocesos del lanzador quieren cosas OPUESTAS.

    El instalador va en segundo plano con su salida a un log: necesita el flag
    o estrena ventana. El servidor real NO redirige el stdio, porque son las
    tuberias del cliente: darle CREATE_NO_WINDOW le regala una consola nueva,
    de la que lee en vez de escuchar al cliente, y el handshake MCP se cuelga
    para siempre. Medido en campo antes de escribir esto.
    """
    ruta = REPO / "scripts" / "plugin_launcher.py"
    por_funcion = {donde: declara for donde, _n, declara in _llamadas_a_subprocess(ruta)}
    assert por_funcion.get("_start_install") is True, (
        "el instalador en segundo plano debe pedir CREATE_NO_WINDOW")
    assert por_funcion.get("main") is False, (
        "el servidor real hereda el stdio del cliente: sin creationflags")


def test_el_venv_se_crea_en_un_subproceso_propio():
    """La ventana que sobrevivio al primer arreglo, medida en pantalla.

    `venv.EnvBuilder(with_pip=True)` arranca `ensurepip` en un proceso aparte
    por su cuenta, sin flags que podamos pasarle: con el instalador sin consola,
    Windows le regalaba una VISIBLE, titulada con la ruta del runtime. Creando
    el venv nosotros, hereda la consola oculta como el resto de los pasos.
    """
    arbol = ast.parse((REPO / "scripts" / "plugin_bootstrap.py").read_text(encoding="utf-8"))
    usos = [n for n in ast.walk(arbol)
            if (isinstance(n, ast.Attribute) and n.attr == "EnvBuilder")
            or (isinstance(n, ast.Name) and n.id == "EnvBuilder")]
    assert not usos, (
        "EnvBuilder lanza ensurepip sin flags: crea el venv con `python -m venv`")
    argumentos = [ast.literal_eval(e) for n in ast.walk(arbol)
                  if isinstance(n, ast.List)
                  for e in n.elts if isinstance(e, ast.Constant)]
    assert "venv" in argumentos, "el venv tiene que crearse por subproceso"


def test_la_raiz_de_datos_no_depende_del_nombre_que_elija_el_cliente(monkeypatch):
    """El bucle de reinstalacion: la carpeta cambiaba de nombre sola.

    Claude entrego primero `...-horizun` y luego `...-inline` para el MISMO
    plugin. Con la ruta colgada de ese nombre, el runtime se reconstruia entero
    (venv, pip, DLL, esquemas, validador) y la carpeta anterior quedaba
    huerfana. Solo el override propio puede mover la raiz.
    """
    bootstrap = _bootstrap()
    monkeypatch.delenv("HORIZUN_PBI_PLUGIN_DATA", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))

    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", r"X:\datos\horizun-pbi-mcp-horizun")
    primera = bootstrap.data_dir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", r"X:\datos\horizun-pbi-mcp-inline")
    assert bootstrap.data_dir() == primera, (
        "la raiz del runtime sigue al nombre que da el cliente")
    assert "HorizunPbiMcp" in str(primera)

    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", r"X:\mio")
    assert bootstrap.data_dir() == Path(r"X:\mio").expanduser().resolve()


def test_el_runtime_se_aisla_por_version(tmp_path):
    """Dos instalaciones de versiones distintas no pueden pisarse el venv."""
    bootstrap = _bootstrap()
    p = bootstrap.paths(tmp_path)
    assert p["cache"] == tmp_path / bootstrap.VERSION
    assert p["runtime"].parent == p["cache"]
    # Lo del usuario NO se versiona: sobrevive a cada actualizacion.
    assert p["outputs"] == tmp_path / "outputs"
    assert p["backups"] == tmp_path / "backups"


def test_un_lock_huerfano_no_congela_la_instalacion(tmp_path):
    """Apagar el PC a mitad dejaba el estado en `installing` para siempre.

    El lock sin dueño bloqueaba al instalador y el estado bloqueaba al
    lanzador, que solo reintenta con `not_installed` o version distinta.
    """
    bootstrap = _bootstrap()
    p = bootstrap.paths(tmp_path)
    p["cache"].mkdir(parents=True)
    bootstrap._write_status(p, state="installing", ready=False)
    # Se envejece el estado a mano: `_write_status` siempre sella la hora
    # actual, y con el margen de gracia recien puesto no se veria el defecto.
    envejecido = json.loads(p["status"].read_text(encoding="utf-8")) | {"updated": 0.0}
    p["status"].write_text(json.dumps(envejecido), encoding="utf-8")

    p["lock"].write_text(json.dumps({"pid": _pid_muerto()}), encoding="utf-8")
    assert bootstrap.instalacion_en_curso(tmp_path) is False
    with bootstrap._cerrojos.CerrojoDeCicloDeVida(tmp_path) as cerrojo:
        assert cerrojo.adquirido is True, "el lock huerfano debe poder robarse"
        assert bootstrap.instalacion_en_curso(tmp_path) is True, (
            "ahora lo tiene este proceso, que si esta vivo")


def test_el_cerrojo_del_ciclo_de_vida_vive_en_la_raiz(tmp_path):
    """Un cerrojo dentro de la carpeta que la promocion renombra no protege nada.

    Vivia en `<raiz>/<VERSION>/install.lock`, y esa es justo la carpeta que
    `promover()` aparta con un `rename`. Dos instaladores concurrentes podian
    estar cada uno en su cache creyendo que tenian el paso libre y colisionar
    al promover sobre el mismo destino.
    """
    bootstrap = _bootstrap()
    p = bootstrap.paths(tmp_path)
    assert p["lock"].parent == p["root"], p["lock"]
    assert p["lock"].parent != p["cache"]


def test_un_lock_vacio_del_formato_anterior_tambien_se_recupera(tmp_path):
    """Las instalaciones ya bloqueadas por la 1.5.4 tienen que destrabarse."""
    bootstrap = _bootstrap()
    p = bootstrap.paths(tmp_path)
    p["root"].mkdir(parents=True, exist_ok=True)
    p["lock"].write_text("", encoding="utf-8")
    assert bootstrap._cerrojos.lock_vivo(p["lock"]) is False
    with bootstrap._cerrojos.CerrojoDeCicloDeVida(tmp_path) as cerrojo:
        assert cerrojo.adquirido is True


def test_un_instalador_vivo_conserva_su_lock(tmp_path):
    bootstrap = _bootstrap()
    with bootstrap._cerrojos.CerrojoDeCicloDeVida(tmp_path) as primero:
        assert primero.adquirido is True
        with bootstrap._cerrojos.CerrojoDeCicloDeVida(tmp_path) as segundo:
            assert segundo.adquirido is False, "no puede haber dos instaladores"


@pytest.mark.parametrize("donante", ["1.0.0", ""])
def test_la_actualizacion_copia_el_runtime_y_NO_destruye_el_anterior(tmp_path, donante):
    """INSTALL-001, en una asercion: la siembra COPIA, no mueve.

    Subir de version no puede costar otra descarga de 1 GB, pero tampoco puede
    costar el runtime que ya funcionaba. La version anterior de `_semilla`
    hacia `shutil.move` del donante ANTES de validar nada: si pip o una descarga
    fallaban despues, el estado quedaba en `failed` y N-1 ya no existia.

    La linea que cambia de signo es la ultima: antes se exigia
    `not viejo_python.exists()` -o sea, que el donante hubiera quedado
    destripado- y ahora se exige justo lo contrario.
    """
    bootstrap = _bootstrap()
    p = bootstrap.paths(tmp_path)
    origen = tmp_path / donante if donante else tmp_path
    relativa = p["python"].relative_to(p["runtime"])
    viejo_python = origen / "runtime" / relativa
    viejo_python.parent.mkdir(parents=True)
    viejo_python.write_text("#", encoding="utf-8")
    (origen / "libs").mkdir(parents=True)
    (origen / "libs" / "Microsoft.AnalysisServices.dll").write_text("x", encoding="utf-8")

    staging = bootstrap._promocion.crear_staging(tmp_path, bootstrap.VERSION)
    sp = bootstrap.paths(tmp_path, cache=staging)

    assert bootstrap._semilla(staging, tmp_path, relativa) == str(origen)
    assert sp["python"].is_file(), "el venv no se copio al staging"
    assert (sp["libs"] / "Microsoft.AnalysisServices.dll").is_file()

    assert viejo_python.is_file(), (
        "la siembra destruyo el runtime anterior: si el resto de la "
        "instalacion falla, no queda N-1 al que volver")
    assert (origen / "libs" / "Microsoft.AnalysisServices.dll").is_file()


def test_la_limpieza_borra_lo_huerfano_y_conserva_lo_del_usuario(tmp_path, monkeypatch):
    """Ni un 'horizun-pbi-mcp-horizun' vacio para siempre, ni un backup perdido."""
    bootstrap = _bootstrap()
    raiz = tmp_path / "datos"
    p = bootstrap.paths(raiz)
    p["cache"].mkdir(parents=True)

    vieja = raiz / "1.0.0" / "runtime"
    vieja.mkdir(parents=True)
    resto = raiz / "runtime"                       # diseño viejo, en la raiz
    resto.mkdir(parents=True)
    (raiz / "outputs").mkdir()
    (raiz / "outputs" / "mio.txt").write_text("mio", encoding="utf-8")

    cliente = tmp_path / "plugins" / "horizun-pbi-mcp-inline"
    (cliente / "runtime").mkdir(parents=True)
    (cliente / "install-status.json").write_text('{"state": "ready"}', encoding="utf-8")
    (cliente / "backups").mkdir()
    (cliente / "backups" / "PB4.zip").write_text("respaldo", encoding="utf-8")
    abandonada = tmp_path / "plugins" / "horizun-pbi-mcp-horizun"
    abandonada.mkdir()
    ajena = tmp_path / "plugins" / "otro-plugin"
    ajena.mkdir()
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(cliente))

    borrados = bootstrap._limpiar_huerfanos(p)

    assert not (raiz / "1.0.0").exists() and not resto.exists()
    assert not cliente.exists() and not abandonada.exists()
    assert ajena.exists(), "no se tocan las carpetas de otros plugins"
    assert p["cache"].is_dir(), "la version vigente se queda"
    assert (raiz / "outputs" / "mio.txt").read_text(encoding="utf-8") == "mio"
    assert (p["backups"] / "PB4.zip").read_text(encoding="utf-8") == "respaldo", (
        "un respaldo del usuario no puede irse con la carpeta que lo alojaba")
    assert str(cliente) in borrados, "la limpieza se reporta en el status"


def test_los_marketplaces_publican_el_mismo_plugin():
    claude = _json(".claude-plugin/marketplace.json")
    codex = _json(".agents/plugins/marketplace.json")
    assert claude["name"] == codex["name"] == "horizun"
    assert claude["plugins"][0]["name"] == branding.MCP_SERVER_NAME
    assert codex["plugins"][0]["name"] == branding.MCP_SERVER_NAME
    assert claude["plugins"][0]["source"]["url"].startswith("https://github.com/HorizunGroup/")
    assert codex["plugins"][0]["source"]["url"].startswith("https://github.com/HorizunGroup/")


def test_skill_de_instalacion_no_conserva_placeholders():
    skill = (REPO / "skills/horizun-pbi-setup/SKILL.md").read_text(encoding="utf-8")
    assert "[TODO" not in skill
    assert "pbi_install_runtime" in skill
    assert "pbi_install_status" in skill


def test_launcher_limpio_expone_el_instalador_por_stdio(tmp_path):
    env = os.environ.copy()
    env["HORIZUN_PBI_PLUGIN_DATA"] = str(tmp_path / "plugin-data")
    env["HORIZUN_PBI_PLUGIN_NO_AUTO_INSTALL"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(REPO / "scripts/plugin_launcher.py")],
        cwd=str(REPO), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8")
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                             "clientInfo": {"name": "pytest", "version": "1"}}}
    listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    stdout, stderr = process.communicate(
        json.dumps(initialize) + "\n" + json.dumps(listing) + "\n", timeout=10)
    assert process.returncode == 0, stderr
    replies = [json.loads(line) for line in stdout.splitlines()]
    assert replies[0]["result"]["serverInfo"]["version"] == branding.VERSION
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == [
        "pbi_install_runtime", "pbi_install_status"]
    assert stderr == ""
