"""INSTALL-003 — `instalar.ps1 -DryRun` no puede tener ni un efecto.

Un instalador que solo se puede probar instalando no se prueba nunca: quien lo
audita tiene que elegir entre leerlo y creerselo, o correrlo y modificar su
maquina. `-DryRun` existe para romper esa disyuntiva, y estas pruebas existen
para que `-DryRun` signifique algo comprobable en vez de una intencion escrita
en un comentario.

Como se demuestra el "cero efectos", que es la parte que importa:

1. **Sombras de ejecutable.** Un directorio con `winget.cmd`, `claude.cmd`,
   `npm.cmd` y `pip.cmd` va PRIMERO en el PATH. Cada uno anota su invocacion en
   un log y sale. Si el script llama a cualquiera de ellos, queda escrito. Las
   sondas de diagnostico permitidas (`py`, `python`, `git --version`,
   `node --version`) no se sombrean: leen y terminan, no instalan nada.
2. **Sombra de cmdlet.** `Set-ExecutionPolicy` se redefine como funcion antes de
   cargar el script. En PowerShell una funcion gana a un cmdlet del mismo
   nombre, asi que ni siquiera una regresion podria cambiar la politica de
   ejecucion de quien corre la suite. El intento tambien se anota.
3. **HOME desviado.** `USERPROFILE`, `APPDATA` y `LOCALAPPDATA` apuntan a un
   arbol temporal que se pesa entero (ruta -> SHA-256) antes y despues.
4. **El repositorio, igual.** `git status --porcelain` y el hash de cada archivo
   bajo `scripts/`, `.github/`, `.claude-plugin/` y `.agents/`.
5. **Procesos.** El conjunto de nombres vivos antes y despues.

El control positivo de que el arnes sirve para algo es el commit anterior:
contra `b2d851a` el script no tiene `param()`, PowerShell le pasa `-DryRun`
como argumento suelto, y el arnes lo caza instalando y registrando de verdad.
Sin ese rojo, estas pruebas solo demostrarian que el arnes no mira.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _version_declarada() -> str:
    """La version se LEE del proyecto; no se copia aqui.

    Estaba escrita a mano -`v2.1.0`- y la subida a 2.1.1 puso la suite en rojo
    acusando al instalador de un defecto que no tenia: `instalar.ps1` fija
    dentro el `ref` del marketplace que escribe, asi que ESE numero cambia en
    cada version por diseño. Un numero copiado a una prueba es el mismo
    duplicado que el hash del instalador copiado a la documentacion.
    """
    import re

    texto = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', texto, re.M).group(1)

INSTALADOR = RAIZ / "scripts" / "instalar.ps1"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="instalar.ps1 es el arranque de Windows y necesita powershell.exe")

#: Lo unico que -DryRun puede ejecutar: sondas que leen y terminan.
SONDAS_PERMITIDAS = ("py", "python", "git", "node")

#: Lo que -DryRun no puede ejecutar jamas. Se sombrean con un .cmd que delata.
EJECUTABLES_SOMBREADOS = ("winget", "claude", "npm", "pip")

PLANTILLA_SOMBRA = (
    "@echo off\r\n"
    ">>\"%HORIZUN_LOG_EFECTOS%\" echo {nombre} %*\r\n"
    "{extra}"
    "exit /b 0\r\n")

#: Arnes: desvia el entorno, sombrea el cmdlet y CARGA el script con dot-source
#: (hace falta para que la sombra de `Set-ExecutionPolicy` este en el mismo
#: ambito). `exit` del script termina este proceso, que es lo que se mide.
ARNES = r"""
param(
    [Parameter(Mandatory=$true)][string]$Script,
    [Parameter(Mandatory=$true)][string]$Sandbox,
    [Parameter(Mandatory=$true)][string]$Log,
    [switch]$PathMinimo,
    [switch]$Seco
)

$env:HORIZUN_LOG_EFECTOS = $Log
$env:USERPROFILE   = $Sandbox
$env:APPDATA       = Join-Path $Sandbox 'AppData\Roaming'
$env:LOCALAPPDATA  = Join-Path $Sandbox 'AppData\Local'
$env:HOME          = $Sandbox

$sombras = Join-Path $Sandbox '.local\bin'
if ($PathMinimo) {
    # PATH minimo: ni git, ni node, ni python. Asi se recorre la rama de winget,
    # que en esta maquina no se alcanzaria porque todo esta instalado.
    $env:Path = $sombras + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
} else {
    $env:Path = $sombras + ';' + $env:Path
}

# Una funcion gana a un cmdlet del mismo nombre en la resolucion de comandos de
# PowerShell. Con esto, ni una regresion podria tocar la politica real de quien
# corre la suite: el intento se anota y no pasa nada.
function Set-ExecutionPolicy {
    param([Parameter(ValueFromRemainingArguments=$true)]$Resto)
    ">>SET-EXECUTIONPOLICY " + ($Resto -join ' ') | Add-Content -LiteralPath $env:HORIZUN_LOG_EFECTOS
}

if ($Seco) { . $Script -DryRun } else { . $Script }
"""


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _pesar(raiz: Path, excluir: set[str] = frozenset()) -> dict:
    """ruta relativa -> SHA-256, para todo archivo bajo `raiz`."""
    if not raiz.exists():
        return {}
    salida = {}
    for p in sorted(raiz.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(raiz).as_posix()
        if rel in excluir:
            continue
        try:
            salida[rel] = _sha256(p)
        except OSError:                                    # pragma: no cover
            salida[rel] = "ILEGIBLE"
    return salida


def _pesar_repositorio() -> dict:
    salida = {}
    for sub in ("scripts", ".github", ".claude-plugin", ".agents"):
        for rel, h in _pesar(RAIZ / sub).items():
            salida[f"{sub}/{rel}"] = h
    return salida


def _git_status() -> str:
    res = subprocess.run(["git", "status", "--porcelain"], cwd=str(RAIZ),
                         capture_output=True, text=True)
    return res.stdout


def _politica_de_5_1() -> str:
    """La politica que el script podria tocar es la de Windows PowerShell 5.1.

    `pwsh` 7 guarda la suya en otra clave del registro, asi que preguntarsela a
    el no diria nada de lo que el script hace.
    """
    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-ExecutionPolicy -Scope CurrentUser"],
        capture_output=True, text=True)
    return res.stdout.strip()


def _procesos() -> set:
    res = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process).Name | Sort-Object -Unique"],
                         capture_output=True, text=True)
    return {l.strip() for l in res.stdout.splitlines() if l.strip()}


@pytest.fixture
def sandbox(tmp_path):
    """HOME desviado con las sombras ya puestas en `~/.local/bin`.

    Van justo ahi a proposito: `DetectarClaudeCode` rehace el PATH desde el
    registro y despues vuelve a anteponer `$env:USERPROFILE\\.local\\bin`. Si
    las sombras estuvieran en otro sitio, el propio script las expulsaria del
    PATH a mitad de la corrida y la prueba dejaria de mirar.
    """
    caja = tmp_path / "home"
    sombras = caja / ".local" / "bin"
    sombras.mkdir(parents=True)
    (caja / "AppData" / "Roaming").mkdir(parents=True)
    (caja / "AppData" / "Local").mkdir(parents=True)

    for nombre in EJECUTABLES_SOMBREADOS:
        extra = "echo claude-sombra 9.9.9\r\n" if nombre == "claude" else ""
        (sombras / f"{nombre}.cmd").write_text(
            PLANTILLA_SOMBRA.format(nombre=nombre, extra=extra), encoding="ascii")

    arnes = tmp_path / "arnes.ps1"
    arnes.write_text(ARNES, encoding="utf-8")
    log = tmp_path / "efectos.log"
    log.write_text("", encoding="utf-8")
    return {"home": caja, "arnes": arnes, "log": log, "tmp": tmp_path}


def _correr(sandbox, script: Path, seco: bool = True, path_minimo: bool = False):
    args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(sandbox["arnes"]),
            "-Script", str(script),
            "-Sandbox", str(sandbox["home"]),
            "-Log", str(sandbox["log"])]
    if seco:
        args.append("-Seco")
    if path_minimo:
        args.append("-PathMinimo")
    return subprocess.run(args, capture_output=True, text=True, timeout=300)


def _efectos_registrados(sandbox) -> list:
    texto = sandbox["log"].read_text(encoding="utf-8", errors="replace")
    return [l.strip() for l in texto.splitlines() if l.strip()]


# ------------------------------------------------------- cero efectos --------
def test_dry_run_no_invoca_ni_un_ejecutable_de_efecto(sandbox):
    """Ni winget, ni claude, ni npm, ni pip. Ninguno, ni una vez."""
    res = _correr(sandbox, INSTALADOR, seco=True)
    assert res.returncode == 0, f"-DryRun no salio 0:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"

    efectos = _efectos_registrados(sandbox)
    assert efectos == [], (
        "-DryRun ejecuto efectos que deberia haberse limitado a planear:\n"
        + "\n".join(efectos))


def test_dry_run_con_path_minimo_planea_winget_pero_no_lo_corre(sandbox):
    """La rama de instalacion se RECORRE; lo que no ocurre es la instalacion.

    Sin esto la prueba de arriba seria mas debil de lo que parece: en una
    maquina con todo instalado, `InstalarConWinget` no se alcanza nunca y el log
    saldria vacio aunque el gating no existiera.
    """
    res = _correr(sandbox, INSTALADOR, seco=True, path_minimo=True)
    assert res.returncode == 0, f"salida {res.returncode}:\n{res.stdout[-2000:]}"

    salida = res.stdout
    assert "winget install -e --id Python.Python.3.12" in salida, (
        "con PATH minimo el plan tiene que prever la instalacion de Python")
    assert "winget install -e --id Git.Git" in salida
    assert "winget install -e --id OpenJS.NodeJS.LTS" in salida

    assert _efectos_registrados(sandbox) == [], (
        "se planeo Y se ejecuto: el gating de winget no sirve")


def test_dry_run_no_toca_la_politica_de_ejecucion(sandbox):
    antes = _politica_de_5_1()
    _correr(sandbox, INSTALADOR, seco=True)
    despues = _politica_de_5_1()

    assert antes == despues, (
        f"la politica de ejecucion del usuario paso de {antes} a {despues}")
    assert not any(l.startswith(">>SET-EXECUTIONPOLICY")
                   for l in _efectos_registrados(sandbox)), (
        "-DryRun intento cambiar la politica de ejecucion")


def test_dry_run_no_escribe_ni_borra_nada_en_el_home_desviado(sandbox):
    antes = _pesar(sandbox["home"])
    _correr(sandbox, INSTALADOR, seco=True)
    despues = _pesar(sandbox["home"])

    nuevos = sorted(set(despues) - set(antes))
    perdidos = sorted(set(antes) - set(despues))
    cambiados = sorted(k for k in set(antes) & set(despues)
                       if antes[k] != despues[k])

    assert not nuevos, f"-DryRun creo archivos en el perfil: {nuevos}"
    assert not perdidos, f"-DryRun borro archivos del perfil: {perdidos}"
    assert not cambiados, f"-DryRun modifico archivos del perfil: {cambiados}"


def test_dry_run_no_toca_el_repositorio(sandbox):
    antes_git, antes_hash = _git_status(), _pesar_repositorio()
    _correr(sandbox, INSTALADOR, seco=True)
    despues_git, despues_hash = _git_status(), _pesar_repositorio()

    assert antes_git == despues_git, "-DryRun cambio el estado de git"
    assert antes_hash == despues_hash, (
        "-DryRun modifico archivos versionados del repositorio")


def test_dry_run_no_deja_procesos_nuevos(sandbox):
    """Procesos, medidos donde la medida significa algo.

    Comparar el conjunto global de nombres de proceso antes y despues no dice
    nada del script: en un escritorio vivo ese conjunto cambia solo, y la
    prueba acabaria acusando al instalador de arrancar el navegador de otra
    persona. Lo que si es atribuible es doble y determinista:

      - ningun proceso corriendo DESDE el sandbox, que es el unico sitio del
        que podria salir algo que el script hubiera lanzado o instalado;
      - el log de sombras vacio, que es el oraculo directo de "no se lanzo
        ningun ejecutable de efecto".
    """
    _correr(sandbox, INSTALADOR, seco=True)

    res = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"@(Get-Process | Where-Object {{ $_.Path -like '{sandbox['home']}*' }} |"
         " ForEach-Object { $_.Name + ':' + $_.Id }) -join ';'"],
        capture_output=True, text=True)
    vivos = [p for p in res.stdout.strip().split(";") if p]
    assert not vivos, f"-DryRun dejo procesos vivos desde el sandbox: {vivos}"
    assert _efectos_registrados(sandbox) == [], (
        "-DryRun lanzo un ejecutable de efecto")


# --------------------------------------------------------- el informe --------
def test_dry_run_informa_las_cinco_cosas_que_debe_informar(sandbox):
    res = _correr(sandbox, INSTALADOR, seco=True)
    salida = res.stdout

    for seccion in ("PREREQUISITOS DETECTADOS:",
                    "DEPENDENCIAS FALTANTES:",
                    "ACCIONES PREVISTAS",
                    "CLIENTES REGISTRABLES:",
                    "RESULTADO DEL PLAN:"):
        assert seccion in salida, f"el plan no informa '{seccion}':\n{salida[-1500:]}"


def test_dry_run_sale_cero_aunque_falten_dependencias(sandbox):
    """Un plan construido es exito. Faltar dependencias es su contenido."""
    res = _correr(sandbox, INSTALADOR, seco=True, path_minimo=True)
    assert res.returncode == 0, (
        f"con dependencias faltantes -DryRun salio {res.returncode}")
    assert "DEPENDENCIAS FALTANTES:" in res.stdout
    assert "(ninguna)" not in res.stdout.split("DEPENDENCIAS FALTANTES:")[1][:60], (
        "con PATH minimo tienen que faltar dependencias")


def test_dry_run_registra_el_marketplace_compartido_de_chatgpt_y_codex(sandbox):
    res = _correr(sandbox, INSTALADOR, seco=True)
    assert "registrar horizun-pbi-mcp en ~/.agents/plugins/marketplace.json" in res.stdout
    assert "ChatGPT Desktop: se registraria el marketplace personal" in res.stdout
    assert "Codex: cubierto por el marketplace personal de ChatGPT" in res.stdout


def _invocar_registro_chatgpt(tmp_path: Path, home: Path):
    """Carga solo las funciones del instalador y ejecuta el registrador."""
    texto = INSTALADOR.read_text(encoding="ascii")
    prefijo = texto.split("# --- 1. Politica de ejecucion", 1)[0]
    script = tmp_path / "registrar-chatgpt.ps1"
    script.write_text(
        prefijo + "\n"
        + f"$env:USERPROFILE = '{str(home).replace(chr(39), chr(39) * 2)}'\n"
        + "$r = RegistrarMarketplaceChatGPT\n"
        + "$r | ConvertTo-Json -Compress\n",
        encoding="ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=30)


def test_registro_chatgpt_preserva_el_marketplace_y_es_idempotente(tmp_path):
    home = tmp_path / "home"
    target = home / ".agents" / "plugins" / "marketplace.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "name": "mi-marketplace",
        "interface": {"displayName": "Mis plugins"},
        "plugins": [{"name": "otro", "source": "./plugins/otro"}],
    }), encoding="utf-8")

    first = _invocar_registro_chatgpt(tmp_path, home)
    assert first.returncode == 0, first.stderr
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["name"] == "mi-marketplace"
    assert data["interface"]["displayName"] == "Mis plugins"
    assert [item["name"] for item in data["plugins"]] == ["otro", "horizun-pbi-mcp"]
    horizun = data["plugins"][-1]
    assert horizun["source"]["ref"] == f"v{_version_declarada()}"
    backups = list(target.parent.glob("marketplace.json.bak-*"))
    assert len(backups) == 1

    second = _invocar_registro_chatgpt(tmp_path, home)
    assert second.returncode == 0, second.stderr
    assert len(list(target.parent.glob("marketplace.json.bak-*"))) == 1, (
        "un segundo registro identico no debe volver a escribir ni respaldar")


def test_registro_chatgpt_no_pisa_un_json_invalido(tmp_path):
    home = tmp_path / "home"
    target = home / ".agents" / "plugins" / "marketplace.json"
    target.parent.mkdir(parents=True)
    original = b'{"plugins": [ roto'
    target.write_bytes(original)

    result = _invocar_registro_chatgpt(tmp_path, home)
    assert result.returncode != 0 or "JSON valido" in (result.stdout + result.stderr)
    assert target.read_bytes() == original
    assert not list(target.parent.glob("marketplace.json.bak-*"))


# ------------------------------------------------ la puerta unica ------------
def test_todo_efecto_pasa_por_la_funcion_efecto():
    """Estatica: ningun ejecutable de efecto se invoca fuera de `Efecto`.

    Es la comprobacion que hace generalizable a las de arriba. Aquellas prueban
    los caminos que hoy se recorren; esta prueba que manana no se pueda anadir
    un camino nuevo por fuera de la puerta.
    """
    import re

    texto = INSTALADOR.read_text(encoding="ascii")

    def codigo_de(linea: str) -> str:
        """La linea sin comentario y sin literales de cadena.

        Sin esto la comprobacion acusa a un mensaje de ayuda: el instalador
        NOMBRA `Set-ExecutionPolicy` dentro del texto que le imprime a la
        persona cuando la politica sigue bloqueando. Nombrar no es invocar.
        """
        sin_cadenas = re.sub(r'"[^"]*"', '""', linea)
        sin_cadenas = re.sub(r"'[^']*'", "''", sin_cadenas)
        return sin_cadenas.split("#")[0]

    dentro_de_efecto = False
    infracciones = []
    for n, linea in enumerate(texto.splitlines(), 1):
        limpia = codigo_de(linea)
        if "-Accion" in limpia:
            dentro_de_efecto = True
        elif dentro_de_efecto and limpia.strip().startswith("})"):
            dentro_de_efecto = False

        if dentro_de_efecto:
            continue
        for ejecutable in EJECUTABLES_SOMBREADOS:
            if re.search(rf"&\s+{ejecutable}\b", limpia):
                infracciones.append(f"{n}: {linea.strip()}")
        if re.search(r"(?<!function )\bSet-ExecutionPolicy\b", limpia):
            infracciones.append(f"{n}: {linea.strip()}")

    assert not infracciones, (
        "efectos invocados fuera de la funcion Efecto:\n" + "\n".join(infracciones))


def test_el_instalador_declara_el_conmutador_dry_run():
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "param(" in texto and "[switch]$DryRun" in texto, (
        "instalar.ps1 no acepta -DryRun; sin el, auditarlo exige instalarlo")
    # `param` tiene que ser la primera sentencia real del archivo.
    codigo = [l for l in texto.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert codigo[0].strip().startswith("param("), (
        f"param() no es la primera sentencia: {codigo[0]!r}")


def test_el_instalador_sigue_siendo_ascii_sin_bom_y_con_lf():
    """PowerShell 5.1 lee UTF-8 sin BOM con la codepage OEM y destroza acentos.

    Ademas, los bytes de este archivo son los del asset publicado: si el CRLF
    entrara aqui, el SHA-256 congelado dejaria de coincidir con lo descargado.
    """
    crudo = INSTALADOR.read_bytes()
    assert not crudo.startswith(b"\xef\xbb\xbf"), "instalar.ps1 lleva BOM"
    assert b"\r" not in crudo, "instalar.ps1 tiene CRLF; .gitattributes exige LF"
    no_ascii = [b for b in crudo if b > 127]
    assert not no_ascii, f"instalar.ps1 dejo de ser ASCII ({len(no_ascii)} bytes)"


# ============================================================================
# INSTALL-004 — el instalador no puede declarar exito sobre algo que no sirve
# ============================================================================
def test_la_verificacion_del_plugin_no_es_una_subcadena_suelta():
    """`$lista -match 'horizun-pbi-mcp'` daba por bueno demasiado.

    Es una coincidencia sobre TODA la salida de `claude plugin list`. Un plugin
    deshabilitado, una version vieja o una linea de error que mencione el
    nombre la satisfacen igual que un plugin sano, y el instalador se despide
    en verde sobre una configuracion muerta.
    """
    # Se miran las lineas EJECUTABLES, no los comentarios: el comentario que
    # explica el defecto lo cita literalmente, y prohibir la cadena obligaria a
    # borrar la explicacion junto con el defecto. Es la tercera vez que aparece
    # esta trampa en el repositorio; conviene reconocerla a la primera.
    lineas = [l for l in INSTALADOR.read_text(encoding="ascii").splitlines()
              if not l.lstrip().startswith("#")]
    codigo = chr(10).join(lineas)
    assert "$lista -match 'horizun-pbi-mcp'" not in codigo, (
        "la verificacion vuelve a ser una subcadena sobre toda la salida")
    assert "$lista -split" in codigo, (
        "no se aisla la LINEA del plugin antes de juzgarla")


@pytest.mark.parametrize("marca", ["disabled", "deshabilitad", "inactive",
                                   "desactivad", "error"])
def test_la_verificacion_reconoce_los_estados_que_no_sirven(marca):
    texto = INSTALADOR.read_text(encoding="ascii")
    assert marca in texto, (
        f"la verificacion no reconoce «{marca}» como un plugin que no sirve")


def test_los_limites_regex_son_texto_y_no_bytes_de_control():
    """Un `\b` escrito como backspace vuelve inocua toda la deteccion."""
    crudo = INSTALADOR.read_bytes()
    assert b"\x08" not in crudo, (
        "instalar.ps1 contiene backspace ASCII donde esperaba un limite regex")
    texto = crudo.decode("ascii")
    assert r"\b(disabled|deshabilitad|inactive|desactivad)\b" in texto


def test_la_verificacion_juzga_el_registro_completo_y_falla_en_rojo():
    """Claude pone `Status: disabled` debajo de la linea con el nombre."""
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "$lineas[$indice..$fin]" in texto, (
        "solo se sigue mirando la linea del nombre, no el bloque con Status")
    assert "FalloVerificacion" in texto
    assert "if ($script:FallosVerificacion.Count -gt 0)" in texto
    assert "exit 1" in texto


def test_plugin_realmente_deshabilitado_hace_fallar_el_instalador(sandbox):
    """G3.5: reproduce el formato multilinea real sin tocar Claude del usuario."""
    claude = sandbox["home"] / ".local" / "bin" / "claude.cmd"
    claude.write_text(
        "@echo off\r\n"
        ">>\"%HORIZUN_LOG_EFECTOS%\" echo claude %*\r\n"
        "if \"%1\"==\"--version\" echo 9.9.9 (Claude Code)\r\n"
        "if \"%1 %2\"==\"plugin list\" (\r\n"
        "  echo Installed plugins:\r\n"
        "  echo   ^> horizun-pbi-mcp@horizun\r\n"
        "  echo     Version: 2.0.1\r\n"
        "  echo     Scope: user\r\n"
        "  echo     Status: x disabled\r\n"
        ")\r\n"
        "exit /b 0\r\n",
        encoding="ascii",
    )

    entorno = os.environ.copy()
    entorno.update({
        "HORIZUN_LOG_EFECTOS": str(sandbox["log"]),
        "USERPROFILE": str(sandbox["home"]),
        "APPDATA": str(sandbox["home"] / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(sandbox["home"] / "AppData" / "Local"),
        "HOME": str(sandbox["home"]),
    })
    res = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(INSTALADOR)],
        capture_output=True, text=True, timeout=300, env=entorno,
    )

    assert res.returncode == 1, res.stdout[-2500:]
    assert "DESHABILITADO" in res.stdout
    assert "Status: x disabled" in res.stdout
    assert "Plugin registrado y habilitado" not in res.stdout


def test_un_formato_no_reconocido_no_se_da_por_bueno():
    """No reconocer no es aprobar: si Claude cambia el formato, se avisa."""
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "no reconocer no es aprobar" in texto.lower(), (
        "no consta la decision de que un formato desconocido no se aprueba")


def test_node_se_compara_contra_un_minimo_y_no_solo_se_imprime():
    """Imprimir `node --version` como Ok deja pasar un Node 18 en verde."""
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "NodeMinimo" in texto, "el instalador no declara una version minima de Node"
    assert 'Ok ("node " + (& node --version))' not in texto, (
        "node se vuelve a reportar en verde sin compararlo con nada")


def test_el_minimo_de_node_del_instalador_y_del_bootstrap_coinciden():
    """Si divergen, el instalador aprueba un Node que el bootstrap rechazara."""
    import re

    texto = INSTALADOR.read_text(encoding="ascii")
    del_instalador = int(re.search(r"\$script:NodeMinimo\s*=\s*(\d+)", texto).group(1))

    bootstrap = (RAIZ / "scripts" / "plugin_bootstrap.py").read_text(encoding="utf-8")
    del_bootstrap = int(re.search(r"^NODE_MINIMO\s*=\s*(\d+)", bootstrap, re.M).group(1))

    assert del_instalador == del_bootstrap, (
        f"instalar.ps1 exige Node {del_instalador} y plugin_bootstrap.py "
        f"{del_bootstrap}: el instalador aprobaria un Node que el bootstrap "
        "rechaza despues")


# ============================================================================
# INSTALL-007 — user-scope: explicito y consentible, no silencioso
# ============================================================================
def test_el_reintento_sin_scope_se_puede_prohibir():
    """El reintento existe a proposito -sin el se rompe el PC vacio- pero
    quien exija user-scope estricto tiene que poder decir que no."""
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "$SoloUserScope" in texto, (
        "no hay forma de prohibir la instalacion fuera de user-scope")
    assert "[switch]$SoloUserScope" in texto, "la bandera no se declara en param()"


def test_el_reintento_sin_scope_se_anuncia_antes():
    """Que sea razonable no lo hace invisible: quien pego esto leyo «nivel
    usuario»."""
    lineas = [l for l in INSTALADOR.read_text(encoding="ascii").splitlines()
              if not l.lstrip().startswith("#")]
    codigo = chr(10).join(lineas)
    i_aviso = codigo.find("no acepto --scope user")
    i_reintento = codigo.find("WingetIntento $id $false")
    assert i_aviso != -1, "el reintento sin --scope no se anuncia"
    assert i_aviso < i_reintento, (
        "se anuncia DESPUES de reintentar, que es no anunciarlo")


def test_tras_instalar_sin_scope_se_comprueba_donde_aterrizo():
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "ComprobarDondeAterrizo" in texto, (
        "se instala sin --scope y nadie comprueba si acabo en el perfil")
    assert "USERPROFILE" in texto


def test_el_cambio_de_politica_se_declara_permanente():
    texto = INSTALADOR.read_text(encoding="ascii")
    assert "PERMANENTE" in texto, (
        "el cambio de ExecutionPolicy no se declara permanente")


def test_el_runbook_dice_como_revertir_la_politica():
    runbook = (RAIZ / "docs" / "RUNBOOK_INSTALACION.md").read_text(encoding="utf-8")
    assert "Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy Restricted" in runbook, (
        "el runbook no dice como revertir la politica de ejecucion")
    assert "-SoloUserScope" in runbook, (
        "el runbook no menciona la bandera de user-scope estricto")
