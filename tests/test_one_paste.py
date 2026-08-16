"""INSTALL-003 — el bloque de un pegado no ejecuta nada que no haya verificado.

El one-paste anterior era `irm .../main/scripts/instalar.ps1 | iex`: descargaba
de una RAMA y ejecutaba lo que llegara. Dos defectos distintos en una linea. La
rama significa que los bytes pueden cambiar bajo el mismo enlace sin que nadie
lo note; el `iex` significa que se ejecutan sin haberlos mirado. HTTPS resuelve
ninguno de los dos: dice QUIEN te los da, no QUE te da.

Estas pruebas no tocan internet. Levantan un **servidor HTTP local** que sirve
lo que cada escenario necesita -bytes correctos, bytes corruptos, una cabecera
mentirosa, un stream que no para, una descarga cortada a la mitad, un 500- y
comprueban la unica cosa que de verdad importa en cada caso: **si el proceso
descargado llego a ejecutarse o no**. El oraculo es un centinela en disco que
solo puede escribir el propio script servido.

Lo que se prueba es el bloque CANONICO (`scripts/one_paste.ps1`) con exactamente
dos lineas sustituidas -la URL y el hash esperado-, porque el resto es la
logica bajo prueba. La sustitucion se verifica: si algun dia esas dos lineas
dejan de existir tal cual, la prueba falla en vez de probar otra cosa.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CANONICO = RAIZ / "scripts" / "one_paste.ps1"
INSTALADOR = RAIZ / "scripts" / "instalar.ps1"
MANIFEST = RAIZ / "scripts" / "downloads_manifest.json"

#: Documentos VIVOS que ofrecen el bloque. Los historicos quedan como estan.
DOCUMENTOS = ("docs/INSTALL.md", "skills/horizun-pbi-setup/SKILL.md")

requiere_windows = pytest.mark.skipif(
    sys.platform != "win32",
    reason="el bloque es PowerShell y necesita powershell.exe")


# ------------------------------------------------------------- utilidades ---
def _snippet_canonico() -> str:
    """El cuerpo ejecutable del bloque: el archivo sin su cabecera de comentarios."""
    lineas = CANONICO.read_text(encoding="ascii").splitlines()
    i = next(n for n, l in enumerate(lineas) if l.strip() and not l.startswith("#"))
    return "\n".join(lineas[i:]).rstrip("\n")


def _entrada_manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["downloads"]["instalar.ps1"]


def _adaptar(url: str, sha: str) -> str:
    """El bloque canonico con SOLO la URL y el hash cambiados."""
    texto = _snippet_canonico()
    texto, n_url = re.subn(r"^\$url = '[^']*'$", f"$url = '{url}'", texto, flags=re.M)
    assert n_url == 1, "no se encontro exactamente una linea $url en el bloque canonico"
    texto, n_sha = re.subn(r"^\$sha = '[^']*'$", f"$sha = '{sha}'", texto, flags=re.M)
    assert n_sha == 1, "no se encontro exactamente una linea $sha en el bloque canonico"
    return texto


class _Servidor(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    escenario = "ok"
    cuerpo = b""

    def log_message(self, *a):                                  # silencio
        pass

    def do_GET(self):
        modo = _Servidor.escenario
        cuerpo = _Servidor.cuerpo

        if modo == "http_500":
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if modo == "content_length_excesivo":
            # Anuncia mucho mas de lo permitido. El bloque tiene que rendirse
            # ANTES de leer un solo byte del cuerpo.
            self.send_response(200)
            self.send_header("Content-Length", str(64 * 1024 * 1024))
            self.end_headers()
            return

        if modo == "stream_excesivo":
            # Sin Content-Length (chunked): la cabecera no delata nada y el
            # unico freno posible es contar los bytes MIENTRAS bajan.
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            trozo = b"A" * 16384
            try:
                for _ in range(20):                             # 320 KiB
                    self.wfile.write(b"%X\r\n" % len(trozo) + trozo + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass                                            # el cliente corto
            return

        if modo == "truncada":
            self.send_response(200)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo[: len(cuerpo) // 2])
            self.close_connection = True
            return

        self.send_response(200)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)


@pytest.fixture
def servidor():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Servidor)
    hilo = threading.Thread(target=httpd.serve_forever, daemon=True)
    hilo.start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def escenario(servidor, tmp_path):
    """Devuelve un lanzador: elige el modo, sirve un payload y corre el bloque."""
    temp = tmp_path / "temp"
    temp.mkdir()
    centinela = tmp_path / "EJECUTADO.txt"

    def lanzar(modo: str, *, salida: int = 0, hash_falso: str | None = None,
               preludio: str = "", entorno_extra: dict | None = None,
               mutar=None):
        payload = f'"ejecutado" | Set-Content -LiteralPath "{centinela}"\n'
        if salida:
            payload += f"exit {salida}\n"
        crudo = payload.encode("ascii")

        _Servidor.escenario = modo
        _Servidor.cuerpo = crudo

        sha = hash_falso if hash_falso is not None else hashlib.sha256(crudo).hexdigest()
        if modo == "hash_incorrecto":
            sha = "0" * 64
            _Servidor.escenario = "ok"

        puerto = servidor.server_address[1]
        texto = _adaptar(f"http://127.0.0.1:{puerto}/instalar.ps1", sha)
        if mutar is not None:
            texto = mutar(texto)
        if preludio:
            texto = preludio.rstrip("\n") + "\n" + texto
        bloque = tmp_path / "bloque.ps1"
        bloque.write_text(texto, encoding="ascii")

        import os
        entorno = dict(os.environ, TEMP=str(temp), TMP=str(temp))
        entorno.update(entorno_extra or {})
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(bloque)],
            capture_output=True, text=True, timeout=180, env=entorno)
        return res

    lanzar.centinela = centinela
    lanzar.temp = temp
    return lanzar


@pytest.fixture
def perfil_frio(tmp_path):
    """Entorno de un perfil RECIEN creado: sin cache de analisis de modulos.

    `ModuleAnalysisCache` -el indice que le ahorra a PowerShell recorrer el
    `PSModulePath` entero para resolver un comando- vive bajo `LOCALAPPDATA`.
    Apuntando `LOCALAPPDATA` a un directorio vacio, el interprete arranca sin
    ese indice, exactamente como en la primera ejecucion de una maquina nueva.
    Se quita ademas la entrada de modulos del usuario del `PSModulePath`, que
    en un perfil nuevo tampoco existe.

    Esto no es decorado: es el estado en el que la resolucion de comandos es
    mas fragil, y por tanto donde un bloque que dependiera de resolver un cmdlet
    tendria mas papeletas para abortar sin motivo.
    """
    import os

    frio = tmp_path / "perfil-frio"
    (frio / "Local").mkdir(parents=True)
    (frio / "Roaming").mkdir(parents=True)
    sistema = [p for p in (os.environ.get("PSModulePath") or "").split(os.pathsep)
               if p and ("system32" in p.lower() or "program files" in p.lower())]
    return {"LOCALAPPDATA": str(frio / "Local"),
            "APPDATA": str(frio / "Roaming"),
            "PSModulePath": os.pathsep.join(sistema)}


def _sobrantes(temp: Path) -> list:
    return [p.name for p in temp.glob("horizun-*.ps1")]


# ------------------------------------------------- el camino que SI ejecuta --
@requiere_windows
def test_hash_correcto_ejecuta_exactamente_una_vez(escenario):
    res = escenario("ok")
    assert res.returncode == 0, f"salida {res.returncode}:\n{res.stdout}\n{res.stderr}"
    assert escenario.centinela.exists(), "el instalador verificado no se ejecuto"
    assert escenario.centinela.read_text(encoding="utf-8").strip() == "ejecutado", (
        "el centinela dice otra cosa: se ejecuto mas de una vez?")
    assert "SHA-256 verificado" in res.stdout


@requiere_windows
def test_el_temporal_se_borra_tambien_cuando_todo_va_bien(escenario):
    escenario("ok")
    assert _sobrantes(escenario.temp) == [], "quedo el script descargado en TEMP"


# ---------------------------------------------- los caminos que NO ejecutan --
@requiere_windows
@pytest.mark.parametrize("modo", [
    "hash_incorrecto",
    "content_length_excesivo",
    "stream_excesivo",
    "truncada",
    "http_500",
])
def test_ningun_camino_defectuoso_llega_a_ejecutar(escenario, modo):
    res = escenario(modo)
    assert not escenario.centinela.exists(), (
        f"con '{modo}' se ejecuto el script descargado")
    assert res.returncode != 0, f"con '{modo}' el bloque se despidio en verde"
    assert "abortada" in (res.stdout + res.stderr).lower(), (
        f"con '{modo}' no se explico por que se aborto:\n{res.stdout}\n{res.stderr}")


@requiere_windows
@pytest.mark.parametrize("modo", [
    "hash_incorrecto", "content_length_excesivo", "stream_excesivo",
    "truncada", "http_500",
])
def test_el_temporal_se_borra_tambien_en_el_error(escenario, modo):
    escenario(modo)
    assert _sobrantes(escenario.temp) == [], (
        f"con '{modo}' quedo basura en TEMP: {_sobrantes(escenario.temp)}")


@requiere_windows
@pytest.mark.parametrize("malformado", ["", "no-es-un-hash", "abc123", "0" * 63])
def test_un_hash_ausente_o_malformado_no_ejecuta_nada(escenario, malformado):
    """Un hash vacio no puede significar 'no comprobar'."""
    res = escenario("ok", hash_falso=malformado)
    assert not escenario.centinela.exists(), (
        f"con el hash {malformado!r} se ejecuto igualmente")
    assert res.returncode != 0


@requiere_windows
def test_una_salida_no_cero_del_instalador_se_reporta(escenario):
    """Ejecutarlo no basta: hay que mirar como termino."""
    res = escenario("ok", salida=3)
    assert escenario.centinela.exists(), "el escenario no llego a ejecutar nada"
    assert res.returncode != 0, "el bloque ignoro el codigo de salida del instalador"
    assert "3" in res.stdout + res.stderr


# ------------------------------- el hash no puede depender del ambiente ------
#
# Lo que estas pruebas cierran, dicho con precision porque una version anterior
# de este comentario lo exageraba: el bloque calculaba el SHA-256 con
# `Get-FileHash`, que es un CMDLET. Usar un cmdlet obliga a resolverlo, y la
# resolucion depende de cosas que quien pega el bloque no controla -modulos
# cargados, PSModulePath, la cache de analisis de modulos-.
#
# Lo que eso NO era: un agujero de integridad. Con
# `$ErrorActionPreference = 'Stop'`, un comando que no resuelve LANZA, y el
# bloque abortaba en el catch. Se comprobo: sustituyendo el nombre por uno
# inexistente y dejando el hash correcto, el resultado es «abortado», nunca
# «ejecutado». Nunca hubo un camino por el que se ejecutara un instalador sin
# verificar.
#
# Lo que si era: una dependencia ambiental en el unico paso que no se puede
# saltar, capaz de convertir una instalacion buena en una fallida por como este
# la sesion de quien pega. Quitarla es hardening, y como tal se documenta.
#
# El oraculo no persigue un fallo intermitente: fija la propiedad. Se ejecuta el
# bloque con un detector que se dispara si alguien invoca `Get-FileHash`, y se
# exige que verifique igual sin haberlo llamado.

def _detector_de_get_filehash(marca: Path) -> str:
    """Preludio que deja constancia en disco si alguien invoca `Get-FileHash`.

    Un punto de interrupcion POR NOMBRE DE COMANDO, no una funcion que lo
    tape. Se probaron las dos y la diferencia importa: una funcion
    `Get-FileHash` definida en el script deja de tener efecto en cuanto
    cualquier cosa -un `New-Object`, un `Get-Command`- provoca la importacion
    de `Microsoft.PowerShell.Utility`, porque la del modulo vuelve a ganar. Un
    oraculo que se desactiva solo a mitad del guion es peor que no tenerlo:
    daba por bueno el bloque VIEJO, que si llamaba al cmdlet.

    El punto de interrupcion se dispara mire quien mire, antes de ejecutar el
    comando, y sobrevive a que el modulo se reimporte.
    """
    return ("Set-PSBreakpoint -Command Get-FileHash -Action { 'si' | "
            f"Set-Content -LiteralPath '{marca}' " + "} | Out-Null\n")


@requiere_windows
def test_el_bloque_nunca_invoca_get_filehash(escenario, tmp_path):
    """Verifica el hash Y no llama al comando. Las dos cosas a la vez."""
    marca = tmp_path / "GET_FILEHASH_LLAMADO.txt"
    res = escenario("ok", preludio=_detector_de_get_filehash(marca))

    assert not marca.exists(), (
        "el bloque calculo el hash invocando Get-FileHash: la comprobacion de "
        "integridad vuelve a depender de que el entorno resuelva un comando")
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "SHA-256 verificado" in res.stdout
    assert escenario.centinela.exists(), "no llego a ejecutar el instalador verificado"


@requiere_windows
def test_sin_invocar_get_filehash_un_hash_malo_sigue_sin_ejecutar(escenario, tmp_path):
    """No depender del entorno NO puede significar 'se salta la comprobacion'."""
    marca = tmp_path / "GET_FILEHASH_LLAMADO.txt"
    res = escenario("hash_incorrecto", preludio=_detector_de_get_filehash(marca))
    assert not marca.exists()
    assert not escenario.centinela.exists(), "ejecuto un archivo que no cuadra"
    assert res.returncode != 0
    assert "abortada" in (res.stdout + res.stderr).lower()


@requiere_windows
@pytest.mark.parametrize("modo,ejecuta", [("ok", True), ("hash_incorrecto", False)])
def test_en_un_perfil_frio_el_bloque_se_comporta_igual(escenario, perfil_frio,
                                                       modo, ejecuta):
    """TEMP/TMP aislados y sin cache de analisis de modulos: mismo veredicto."""
    res = escenario(modo, entorno_extra=perfil_frio)
    assert escenario.centinela.exists() is ejecuta, (
        f"en un perfil frio, con '{modo}', el veredicto cambio:\n"
        f"{res.stdout}\n{res.stderr}")
    assert (res.returncode == 0) is ejecuta
    assert _sobrantes(escenario.temp) == [], "quedo basura en el TEMP aislado"


@requiere_windows
def test_si_el_hash_no_se_puede_calcular_no_se_ejecuta_nada(escenario):
    """Fail-closed: 'no se pudo comprobar' tiene que valer lo mismo que 'no cuadra'.

    Se rompe A PROPOSITO el calculo -el tipo de la BCL se sustituye por uno que
    no existe- y se exige que el bloque no ejecute ni un byte. La sustitucion se
    verifica: si el bloque deja de calcular asi, la prueba falla en vez de pasar
    sin comprobar nada, que es exactamente el defecto que persigue.
    """
    def romper(texto: str) -> str:
        nuevo, n = re.subn(r"\[Security\.Cryptography\.SHA256\]",
                           "[Security.Cryptography.NoExisteEsteTipo]", texto)
        assert n == 1, "el bloque ya no calcula el SHA-256 con la BCL"
        return nuevo

    res = escenario("ok", mutar=romper)
    assert not escenario.centinela.exists(), (
        "con el calculo del hash roto, el bloque ejecuto igualmente")
    assert res.returncode != 0
    assert "abortada" in (res.stdout + res.stderr).lower()
    assert _sobrantes(escenario.temp) == []


def test_el_bloque_no_calcula_el_hash_con_un_cmdlet():
    """La forma, ademas del comportamiento: aqui se lee de un vistazo."""
    texto = _snippet_canonico()
    assert "Get-FileHash" not in texto, (
        "el bloque volvio a calcular el hash con un cmdlet: la comprobacion "
        "vuelve a depender de que el entorno resuelva comandos")
    assert "[Security.Cryptography.SHA256]::Create()" in texto, (
        "el hash ya no se calcula con la BCL")
    assert "SHA256Managed" not in texto, (
        "SHA256Managed lanza en una maquina con FIPS activado; ::Create() no")


# ------------------- el mensaje final tiene que decir la verdad --------------
#
# El bloque terminaba SIEMPRE con la misma frase: «No se ejecuto nada que no
# coincidiera con el hash publicado». Literalmente cierta y engañosa en lo que
# la persona entiende. Si el instalador se descargaba, verificaba, ejecutaba y
# fallaba a mitad —dejando medio sistema tocado— el mensaje seguia sonando a
# «tranquilo, aqui no se ha ejecutado nada».

@requiere_windows
def test_si_el_instalador_se_ejecuta_y_falla_el_mensaje_lo_dice(escenario):
    res = escenario("ok", salida=3)
    salida = res.stdout + res.stderr

    assert escenario.centinela.exists(), "el escenario no llego a ejecutar nada"
    assert res.returncode != 0
    assert "SI llego a ejecutarse" in salida, (
        f"el mensaje no reconoce que el instalador se ejecuto:\n{salida}")
    assert "No se ejecuto nada" not in salida, (
        f"el mensaje sigue afirmando que no se ejecuto nada:\n{salida}")
    assert "3" in salida, "no dice con que codigo termino"


@requiere_windows
@pytest.mark.parametrize("modo,fase", [
    ("http_500", "descarga"),
    ("content_length_excesivo", "descarga"),
    ("stream_excesivo", "descarga"),
    # La descarga cortada a la mitad TERMINA de leer sin error -el servidor
    # cierra la conexion y el stream devuelve 0-, asi que no la caza la fase de
    # descarga sino el hash. Que la fase reportada sea 'verificacion' no es un
    # detalle: dice donde estuvo de verdad la red de seguridad.
    ("truncada", "verificacion"),
    ("hash_incorrecto", "verificacion"),
])
def test_cuando_no_se_ejecuta_nada_el_mensaje_dice_en_que_fase_se_corto(
        escenario, modo, fase):
    res = escenario(modo)
    salida = res.stdout + res.stderr

    assert not escenario.centinela.exists()
    assert "No se ejecuto nada" in salida, salida
    assert f"fase '{fase}'" in salida, (
        f"con '{modo}' se esperaba la fase '{fase}':\n{salida}")
    assert "SI llego a ejecutarse" not in salida


# --------------- el ejecutable no se resuelve por nombre ---------------------
#: `powershell` como funcion Y como alias. Las dos tienen prioridad sobre el
#: PATH, asi que cualquiera de ellas secuestraria un `& powershell`.
POWERSHELL_HOSTIL = (
    "function powershell { throw 'secuestrado por una funcion' }\n"
    "Set-Alias -Name powershell -Value cmd.exe -Scope Script -Force\n")


@requiere_windows
def test_el_instalador_se_lanza_por_ruta_y_no_por_nombre(escenario):
    """Con `powershell` secuestrado en la sesion, el bloque tiene que funcionar.

    Es el mismo razonamiento que llevo a quitar `Get-FileHash`: lo que decide
    QUE se ejecuta no puede salir del descubrimiento de comandos de la sesion.
    Aqui la diferencia es que aqui si habria consecuencia real -se ejecutaria
    otro programa con el script verificado como argumento-.
    """
    res = escenario("ok", preludio=POWERSHELL_HOSTIL)

    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert escenario.centinela.exists(), (
        "el bloque resolvio `powershell` por nombre y lo secuestro la sesion")
    assert "secuestrado" not in (res.stdout + res.stderr)


def test_el_bloque_usa_el_powershell_de_PSHOME():
    texto = _snippet_canonico()
    assert "$PSHOME" in texto, (
        "el bloque no toma el ejecutable de $PSHOME")
    assert "[IO.File]::Exists($ps)" in texto, (
        "no comprueba que la ruta sea un archivo antes de invocarla")


# ------------------------------------------------- forma del bloque ----------
def test_el_bloque_no_usa_invoke_expression():
    texto = _snippet_canonico()
    for prohibido in ("iex", "Invoke-Expression", "IEX"):
        assert not re.search(rf"\b{prohibido}\b", texto), (
            f"el bloque canonico usa {prohibido}")
    assert "& $ps" in texto, "el bloque no ejecuta con &"
    assert not re.search(r"&\s+powershell\b", texto), (
        "el bloque volvio a invocar `powershell` POR NOMBRE: un nombre lo "
        "resuelven antes los alias y las funciones de la sesion que el PATH")
    assert "$LASTEXITCODE" in texto, "el bloque no mira el codigo de salida"
    assert "finally" in texto and "Remove-Item" in texto, (
        "el temporal no se borra en un finally")


def test_el_bloque_descarga_de_una_url_inmutable():
    url = re.search(r"^\$url = '([^']+)'$", _snippet_canonico(), re.M).group(1)
    assert url.startswith("https://"), url
    for movil in ("/main/", "/master/", "/HEAD/", "/latest/", "/develop/"):
        assert movil not in url, f"la URL del bloque es mutable: {url}"
    assert re.search(r"/releases/download/v\d+\.\d+\.\d+/", url), (
        f"la URL no apunta a un asset de release versionado: {url}")


def _bloques_de_codigo(texto: str) -> list:
    return re.findall(r"^```[a-zA-Z]*\n(.*?)^```", texto, re.M | re.S)


def test_ningun_documento_vivo_ofrece_iex():
    """La regresion mas facil: alguien reescribe el one-paste 'para acortarlo'.

    Se miran los BLOQUES EJECUTABLES, no la prosa. El README explica a proposito
    por que `irm <url> | iex` es mala idea, y prohibir la palabra obligaria a
    quitar la explicacion junto con el defecto.
    """
    culpables = []
    for rel in DOCUMENTOS:
        for bloque in _bloques_de_codigo((RAIZ / rel).read_text(encoding="utf-8")):
            if re.search(r"\|\s*iex\b", bloque, re.I) or "Invoke-Expression" in bloque:
                culpables.append(f"{rel}: {bloque.strip().splitlines()[0][:70]}")
    assert not culpables, f"bloques ejecutables que aun usan iex: {culpables}"


def test_ningun_bloque_ejecutable_descarga_desde_una_rama():
    """`raw.githubusercontent.com/.../main/` es la otra mitad del defecto."""
    culpables = []
    for rel in DOCUMENTOS:
        for bloque in _bloques_de_codigo((RAIZ / rel).read_text(encoding="utf-8")):
            for movil in ("/main/", "/master/", "/HEAD/", "/latest/"):
                if "raw.githubusercontent.com" in bloque and movil in bloque:
                    culpables.append(f"{rel}: {movil}")
    assert not culpables, f"bloques que descargan de una referencia movil: {culpables}"


# --------------------------------------- una sola fuente, sin divergencias ---
@pytest.mark.parametrize("rel", DOCUMENTOS)
def test_cada_documento_incrusta_el_bloque_canonico_palabra_por_palabra(rel):
    texto = (RAIZ / rel).read_text(encoding="utf-8")
    assert _snippet_canonico() in texto, (
        f"{rel} no lleva el bloque canonico de scripts/one_paste.ps1 tal cual. "
        "No lo edites en el documento: edita el canonico. "
        "Para repararlo: python scripts/sync_one_paste.py")


def test_ninguna_copia_del_bloque_diverge_del_canonico():
    """Cada documento que ofrece el bloque debe copiar el canonico completo."""
    sys.path.insert(0, str(RAIZ / "scripts"))
    import sync_one_paste

    canonico = sync_one_paste.snippet_canonico()
    divergentes = []
    for rel in DOCUMENTOS:
        texto = (RAIZ / rel).read_text(encoding="utf-8")
        assert sync_one_paste.HUELLA in texto, f"{rel} ya no lleva el bloque"
        _, cambiados = sync_one_paste.sincronizar(texto, canonico)
        if cambiados:
            divergentes.append(f"{rel} ({cambiados} bloque/s)")
    assert not divergentes, (
        f"copias del bloque que no son el canonico: {divergentes}. "
        "Reparalo con: python scripts/sync_one_paste.py")


def test_la_prosa_solo_cita_documentos_que_llevan_el_bloque():
    """Quien anuncia donde esta el bloque no puede nombrar un archivo que no lo
    lleva: el agente que sigue la instruccion al pie de la letra no lo encuentra
    y acaba reconstruyendolo de memoria, que es lo que la instruccion evita.

    Paso en DOS sitios a la vez -la frase de la skill y la cabecera del propio
    archivo canonico-, lo que descarta el despiste: ambos citaban `README.md`
    mucho despues de que el README dejara de incrustarlo y pasara a solo
    enlazar a `docs/INSTALL.md`. Los bloques se vigilan por hash; la prosa que
    los anuncia no la vigilaba nadie.

    Se miran los dos textos que dicen DONDE esta el bloque, no cualquier
    mencion del README: en otras partes se le nombra por motivos que no tienen
    que ver con incrustarlo.
    """
    # El punto que cierra la frase de la skill es el que va seguido de espacio
    # o fin: un `\.` a secas corta dentro de `one_paste.ps1` y deja fuera
    # precisamente los documentos que hay que revisar -el test pasaria solo-.
    skill = (RAIZ / "skills/horizun-pbi-setup/SKILL.md").read_text(encoding="utf-8")
    frase = re.search(r"El bloque exacto esta en.*?\.(?=\s|$)", skill, re.S)
    assert frase, "la SKILL ya no dice donde esta el bloque exacto"

    cabecera = "\n".join(
        l for l in CANONICO.read_text(encoding="ascii").splitlines()
        if l.startswith("#"))
    assert "incrustan ESTE archivo" in cabecera, (
        "la cabecera del canonico ya no enumera quien lo incrusta; si se "
        "reescribio, actualiza este test en vez de borrarlo")

    citados = set(re.findall(r"`([^`]+\.md)`", frase.group(0)))
    citados |= set(re.findall(r"\b([\w/\-]+\.md)\b", cabecera))
    intrusos = sorted(c for c in citados if c not in DOCUMENTOS)
    assert not intrusos, (
        f"se anuncia el bloque en {intrusos}, que no lo incrustan. "
        f"Los documentos que lo llevan son {list(DOCUMENTOS)}")


def test_el_manifest_y_el_bloque_declaran_la_misma_descarga():
    entrada = _entrada_manifest()
    bloque = _snippet_canonico()
    url = re.search(r"^\$url = '([^']+)'$", bloque, re.M).group(1)
    sha = re.search(r"^\$sha = '([^']+)'$", bloque, re.M).group(1)
    maximo = int(re.search(r"^\$max = (\d+)$", bloque, re.M).group(1))

    assert url == entrada["url"], "la URL del bloque y la del manifest divergen"
    assert sha == entrada["sha256"], "el hash del bloque y el del manifest divergen"
    assert maximo == entrada["max_bytes"], "el tope del bloque y el del manifest divergen"


def test_el_hash_publicado_es_el_de_los_bytes_reales_del_instalador():
    """El unico numero que no se puede escribir de memoria."""
    crudo = INSTALADOR.read_bytes()
    entrada = _entrada_manifest()

    assert hashlib.sha256(crudo).hexdigest() == entrada["sha256"], (
        "el SHA-256 publicado no es el de scripts/instalar.ps1. Si el "
        "instalador cambio, recalculalo y actualiza manifest y bloque.")
    assert len(crudo) == entrada["actual_bytes"], (
        f"el instalador ocupa {len(crudo)} bytes y el manifest dice "
        f"{entrada['actual_bytes']}")
    assert entrada["max_bytes"] >= entrada["actual_bytes"], (
        "el tope declarado es menor que el archivo: el one-paste se abortaria "
        "a si mismo")


def test_el_manifest_no_afirma_que_el_asset_remoto_ya_existe():
    """v2.0.0 no esta publicada. Decir lo contrario seria la mentira comoda."""
    entrada = _entrada_manifest()
    assert entrada["status"] == "pending_remote_release", (
        "si la release ya existe, verifica el asset descargandolo y cambia el "
        "status; no lo cambies porque el ciclo haya terminado")
    assert entrada.get("status_note"), "el estado pendiente no explica por que"


def test_el_bloque_canonico_es_ascii_sin_bom_y_con_lf():
    crudo = CANONICO.read_bytes()
    assert not crudo.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in crudo
    assert all(b < 128 for b in crudo), (
        "el bloque dejo de ser ASCII: PowerShell 5.1 lo leeria con la codepage "
        "OEM y destrozaria los acentos que la persona pega")
