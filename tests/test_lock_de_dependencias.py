"""INSTALL-009 / G4.6 — reproducibilidad en TODAS las versiones soportadas.

El defecto original cabe en una linea: `install()` ejecutaba
`pip install <PLUGIN_ROOT>`, que **resuelve las dependencias de cero cada vez**.

La primera remediacion trajo el suyo propio, y era mas sutil: **un solo lock**,
resuelto con el interprete de quien lo generase, cuya cabecera decia «Python
3.14 en win32». `pyproject` admite `>=3.10` y CI corre 3.10 y 3.13. En esas dos
`--require-hashes` falla —el lock no lista las ruedas que necesitan— y el
instalador cae al resolutor **sin hashes**. O sea: la garantia existia
exactamente en la maquina de quien la escribio, y en las demas se anunciaba en
una linea del estado que nadie lee.

Que no era una diferencia teorica lo dice el propio material: entre el lock de
3.10 y el de 3.14 **difieren siete entradas** —ruedas compiladas para otro ABI y
una version distinta de `rpds-py`—.

Lo que se comprueba aqui, y ninguna parte sobra:

1. **Cada lock de la matriz fija de verdad**: version exacta, SHA-256, sin
   repetidos, sin el paquete propio, y con todas las dependencias declaradas.
2. **La cabecera dice a que combinacion pertenece** y coincide con su nombre.
3. **La seleccion es exacta**: un interprete sin lock no recibe «el mas
   parecido».
4. **El fallback no es silencioso**: queda escrito en el estado, y dice que esa
   instalacion no es reproducible.
5. **`--check` es determinista y offline**: no vuelve a preguntarle a PyPI, que
   es lo que hacia salir 1 en cuanto alguien publicaba una version.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
LOCKS = RAIZ / "scripts" / "locks"

#: Unica causa declarada de skip para la prueba que instala de verdad, y la
#: pone una persona. Es la misma valvula de `test_packaging.py`, a proposito:
#: dos formas de declararse offline serian dos formas de no probar nada.
OFFLINE = os.environ.get("PBI_MCP_PACKAGING_OFFLINE") == "1"

LINEA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*"
                   r"==[A-Za-z0-9][A-Za-z0-9.\-+!]*"
                   r" --hash=sha256:[0-9a-f]{64}$")


def _cargar(nombre: str, archivo: str):
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "scripts" / archivo)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def bootstrap():
    return _cargar("bootstrap_bajo_prueba_i9", "plugin_bootstrap.py")


@pytest.fixture
def generar():
    return _cargar("generar_lock_bajo_prueba", "generar_lock.py")


def _entradas(texto: str) -> list[str]:
    return [l for l in texto.splitlines() if l.strip() and not l.startswith("#")]


def _nombre(linea: str) -> str:
    return linea.split("==", 1)[0].lower().replace("_", "-")


def _combinaciones():
    return _cargar("generar_lock_ids", "generar_lock.py").MATRIZ


# ===================== 1. Los locks del repositorio =======================

def test_la_matriz_cubre_lo_que_pyproject_declara_soportar(generar):
    """Todo lo que el producto PROMETE tiene lock. No una parte.

    La version anterior de esta prueba se conformaba con la minima y con lo que
    corre CI, y con eso la matriz cubria 3.10, 3.13 y 3.14 mientras los
    classifiers prometian tambien 3.11 y 3.12. En esas dos, `--require-hashes`
    fallaba y la instalacion caia al resolutor sin hashes: la garantia no
    existia justo donde nadie miraba.

    Ahora el oraculo son los **classifiers**, que es donde el producto declara
    a quien le sirve. Anadir un `Programming Language :: Python :: 3.x` sin su
    lock pone esto en rojo.
    """
    versiones = {v for v, _ in generar.MATRIZ}
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")

    declaradas = set(re.findall(
        r'"Programming Language :: Python :: (\d+\.\d+)"', pyproject))
    assert declaradas, "pyproject no declara ninguna version concreta de Python"
    faltan = sorted(declaradas - versiones)
    assert not faltan, (
        f"los classifiers prometen {faltan} y no hay lock para esas versiones: "
        f"la matriz cubre {sorted(versiones)}")

    minima = re.search(r'requires-python\s*=\s*">=([\d.]+)"', pyproject).group(1)
    assert minima in versiones, (
        f"pyproject promete funcionar desde {minima} y no hay lock para esa "
        f"version: {sorted(versiones)}")
    ci = (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    de_ci = set(re.findall(r'"(\d+\.\d+)"', ci.split("python-version:", 1)[1][:80]))
    assert de_ci <= versiones, (
        f"CI corre {sorted(de_ci)} y la matriz de locks cubre {sorted(versiones)}")


def test_la_matriz_no_promete_plataformas_que_el_producto_no_declara(generar):
    """G4.6, reevaluado: exigir un runner Linux era pedir de mas.

    `pyproject` declara `Operating System :: Microsoft :: Windows` y nada mas.
    Un lock para una plataforma que el producto no promete seria un archivo que
    nadie verifica, y mantener «falta un runner no-Windows» como bloqueo era
    exigir evidencia de un entorno no soportado.

    Si algun dia se declara otra plataforma, esto obliga a que traiga su lock.
    """
    plataformas = {pl for _, pl in generar.MATRIZ}
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")
    sistemas = set(re.findall(r'"Operating System :: ([^"]+)"', pyproject))

    assert sistemas == {"Microsoft :: Windows"}, (
        f"el producto declara {sistemas}: la matriz de locks tiene que cubrir "
        f"todas, y hoy cubre {plataformas}")
    assert plataformas == {"win_amd64"}, plataformas


@pytest.mark.parametrize("version,plataforma", _combinaciones())
def test_cada_lock_de_la_matriz_esta_integro(generar, version, plataforma):
    """Formato, hashes, duplicados, cabecera y dependencias declaradas."""
    fallos = generar.problemas_de(generar.ruta_de(version, plataforma),
                                  version, plataforma)
    assert not fallos, fallos


@pytest.mark.parametrize("version,plataforma", _combinaciones())
def test_cada_linea_fija_version_exacta_y_sha256(generar, version, plataforma):
    malas = [l for l in _entradas(
        generar.ruta_de(version, plataforma).read_text(encoding="utf-8"))
        if not LINEA.match(l)]
    assert not malas, (
        "estas lineas no fijan version+hash, asi que pip volveria a resolver "
        f"en el momento de instalar: {malas}")


def test_los_locks_no_son_copias_el_uno_del_otro(generar):
    """La prueba que justifica que haya matriz y no un archivo.

    Si los tres fueran identicos, la matriz seria burocracia. No lo son: entre
    3.10 y 3.14 cambian las ruedas compiladas -otro ABI- y alguna version.
    """
    por_combinacion = {
        f"py{v}": set(_entradas(generar.ruta_de(v, pl).read_text(encoding="utf-8")))
        for v, pl in generar.MATRIZ}
    a, b = por_combinacion["py3.10"], por_combinacion["py3.14"]
    assert a != b, (
        "el lock de 3.10 y el de 3.14 son identicos: o la resolucion cruzada no "
        "esta haciendo nada, o sobra la matriz")
    assert len(a ^ b) >= 2


@pytest.mark.parametrize("version,plataforma", _combinaciones())
def test_el_paquete_propio_no_figura_en_ningun_lock(generar, version, plataforma):
    """Es la fuente local: no tiene hash publicado y se instala con --no-deps."""
    nombres = {_nombre(l) for l in _entradas(
        generar.ruta_de(version, plataforma).read_text(encoding="utf-8"))}
    assert "horizun-pbi-mcp" not in nombres


def test_ya_no_queda_el_lock_unico():
    """El de una sola combinacion tenia que irse, no quedarse «por si acaso».

    Dos fuentes de verdad para lo mismo acaban divergiendo, y la vieja es la
    que alguien lee.
    """
    assert not (RAIZ / "scripts" / "requirements.lock").exists(), (
        "sigue ahi scripts/requirements.lock, que solo cubria una combinacion")


# ===================== 2. El generador ====================================

def _reporte(*paquetes: tuple[str, str, str | None]) -> dict:
    return {"install": [
        {"metadata": {"name": n, "version": v},
         "download_info": {"archive_info": {"hashes": {"sha256": h} if h else {}}}}
        for n, v, h in paquetes]}


def test_lineas_del_lock_fija_nombre_version_y_hash(generar):
    lineas = generar.lineas_del_lock(_reporte(("anyio", "4.14.2", "ab" * 32)))
    assert lineas == [f"anyio==4.14.2 --hash=sha256:{'ab' * 32}"]


def test_un_paquete_sin_hash_se_omite_en_vez_de_inventarselo(generar):
    lineas = generar.lineas_del_lock(
        _reporte(("horizun-pbi-mcp", "2.0.0", None), ("anyio", "4.14.2", "cd" * 32)))
    assert [_nombre(l) for l in lineas] == ["anyio"]


def test_el_mismo_conjunto_en_otro_orden_da_el_mismo_lock(generar):
    """pip no promete orden; el lock si. Sin esto, `--check` gritaria siempre."""
    a = ("anyio", "4.14.2", "11" * 32)
    b = ("pydantic", "2.9.0", "22" * 32)
    assert (generar.lineas_del_lock(_reporte(a, b))
            == generar.lineas_del_lock(_reporte(b, a)))


def test_el_nombre_del_archivo_codifica_la_combinacion(generar):
    assert generar.nombre_de("3.10", "win_amd64") == "requirements-py310-win_amd64.lock"
    assert generar.nombre_de("3.13", "manylinux") == "requirements-py313-manylinux.lock"


# ---------------------------------------- `--check` determinista y offline --

def _lock_falso(tmp_path: Path, generar, *, version="3.10", plataforma="win_amd64",
                entradas=None, cabecera=None) -> Path:
    entradas = entradas if entradas is not None else [
        f"anyio==4.14.2 --hash=sha256:{'11' * 32}"]
    lineas = cabecera or generar.cabecera(version, plataforma, len(entradas))
    ruta = tmp_path / generar.nombre_de(version, plataforma)
    ruta.write_text("\n".join(lineas + entradas) + "\n", encoding="utf-8",
                    newline="")
    return ruta


def test_check_no_toca_la_red(generar, monkeypatch, capsys):
    """La razon de existir de `--check`, y lo que la version anterior no hacia.

    Antes comparaba contra lo que pip resolveria HOY, asi que salia 1 en cuanto
    alguien publicaba una version nueva —paso en la misma sesion en que se
    genero el lock: `charset-normalizer` 3.5.0 -> 3.5.1 en dos horas—. Un check
    que grita por algo que no es un fallo acaba desactivado.
    """
    def _prohibido(*a, **k):
        raise AssertionError("`--check` intento resolver contra PyPI")

    monkeypatch.setattr(generar, "resolver", _prohibido)
    monkeypatch.setattr(generar.subprocess, "run", _prohibido)
    monkeypatch.setattr(sys, "argv", ["generar_lock.py", "--check"])

    assert generar.main() == 0
    assert "integros" in capsys.readouterr().out


def test_check_delata_un_hash_mal_formado(generar, tmp_path, monkeypatch):
    ruta = _lock_falso(tmp_path, generar,
                       entradas=["anyio==4.14.2 --hash=sha256:demasiado-corto"])
    fallos = generar.problemas_de(ruta, "3.10", "win_amd64")
    assert any("version+hash" in f for f in fallos), fallos


def test_check_delata_una_dependencia_declarada_y_sin_fijar(generar, tmp_path):
    """El lock viejo al que alguien le anadio una dependencia despues.

    Sin esto, `--require-hashes` revienta durante la instalacion de otra
    persona, que es el peor sitio para enterarse.
    """
    ruta = _lock_falso(tmp_path, generar)
    fallos = generar.problemas_de(ruta, "3.10", "win_amd64")
    assert any("declaradas y sin fijar" in f for f in fallos), fallos
    assert any("mcp" in f for f in fallos), fallos


def test_check_delata_una_cabecera_que_no_corresponde(generar, tmp_path):
    """Renombrar el archivo no convierte un lock de 3.14 en uno de 3.10."""
    ruta = _lock_falso(tmp_path, generar, version="3.10",
                       cabecera=generar.cabecera("3.14", "win_amd64", 1))
    fallos = generar.problemas_de(ruta, "3.10", "win_amd64")
    assert any("python-version" in f for f in fallos), fallos


def test_check_delata_un_duplicado(generar, tmp_path):
    ruta = _lock_falso(tmp_path, generar, entradas=[
        f"anyio==4.14.2 --hash=sha256:{'11' * 32}",
        f"anyio==4.15.0 --hash=sha256:{'22' * 32}"])
    fallos = generar.problemas_de(ruta, "3.10", "win_amd64")
    assert any("dos veces" in f for f in fallos), fallos


def test_check_delata_un_lock_ausente(generar, tmp_path):
    fallos = generar.problemas_de(tmp_path / "no-esta.lock", "3.10", "win_amd64")
    assert fallos and "no existe" in fallos[0]


# ===================== 3. La seleccion en el instalador ===================

def _grabador(monkeypatch, bootstrap, romper=None):
    ordenes: list[list[str]] = []

    def _run(command, *, env, intentos=3):
        ordenes.append(list(command))
        if romper and romper(command):
            raise RuntimeError("fallo inyectado")

    monkeypatch.setattr(bootstrap, "_run", _run)
    return ordenes


def _pip(ordenes: list[list[str]]) -> list[list[str]]:
    return [o for o in ordenes if "pip" in o and "install" in o]


def _finge_interprete(monkeypatch, bootstrap, version, plataforma):
    monkeypatch.setattr(bootstrap, "combinacion_de",
                        lambda python: (version, plataforma))


def test_elige_el_lock_EXACTO_de_su_interprete(bootstrap, tmp_path, monkeypatch):
    """Coincidencia exacta, no «el mas parecido».

    Un lock de 3.14 aplicado a 3.10 lleva ruedas de otro ABI y
    `--require-hashes` rechaza el archivo entero: elegir el mas parecido falla
    igual, mas tarde y con peor mensaje.
    """
    for version, plataforma in _combinaciones():
        _finge_interprete(monkeypatch, bootstrap, version, plataforma)
        ordenes = _grabador(monkeypatch, bootstrap)
        resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

        assert resultado["source"] == "lock", (version, resultado)
        esperado = LOCKS / f"requirements-py{version.replace('.', '')}-{plataforma}.lock"
        assert resultado["lock"] == str(esperado)
        fijado = _pip(ordenes)[0]
        assert "--require-hashes" in fijado and str(esperado) in fijado


def test_un_interprete_sin_lock_no_recibe_uno_parecido(bootstrap, tmp_path,
                                                       monkeypatch):
    """3.9 no esta soportada: no se le da el de 3.10 «que casi vale».

    Antes el ejemplo era 3.12, y dejo de servir cuando la matriz se amplio a
    las cinco versiones que los classifiers prometen. Se cambia por una que el
    producto NO declara soportar, que es el caso real: alguien con un Python
    fuera del rango.
    """
    _finge_interprete(monkeypatch, bootstrap, "3.9", "win_amd64")
    ordenes = _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert resultado["source"] == "resolver"
    assert resultado["lock"] is None
    assert "no hay lock para py3.9/win_amd64" in resultado["reason"]
    assert not any("--require-hashes" in o for o in ordenes), (
        "se intento un lock de otra combinacion")


def test_otra_plataforma_tampoco_recibe_el_de_windows(bootstrap, tmp_path,
                                                      monkeypatch):
    _finge_interprete(monkeypatch, bootstrap, "3.13", "linux_x86_64")
    _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})
    assert resultado["source"] == "resolver"
    assert "linux_x86_64" in resultado["reason"]


def test_si_no_se_puede_saber_la_version_no_se_adivina(bootstrap, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(bootstrap, "combinacion_de", lambda python: None)
    _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})
    assert resultado["source"] == "resolver"
    assert "no se pudo determinar" in resultado["reason"]


def test_el_fallback_dice_que_NO_es_reproducible(bootstrap, tmp_path, monkeypatch):
    """Un fallback silencioso es peor que no tener lock: deja creer que si."""
    _finge_interprete(monkeypatch, bootstrap, "3.9", "win_amd64")
    _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert "NO es reproducible" in resultado["note"]
    assert "generar_lock.py" in resultado["note"], (
        "el estado tiene que decir COMO se arregla, no solo que no lo esta")


def test_el_paquete_propio_va_aparte_y_sin_deps(bootstrap, tmp_path, monkeypatch):
    _finge_interprete(monkeypatch, bootstrap, "3.14", "win_amd64")
    ordenes = _grabador(monkeypatch, bootstrap)
    bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    fijado, propio = _pip(ordenes)
    assert "--no-deps" in propio and str(bootstrap.PLUGIN_ROOT) in propio
    assert not any(o[-1] == str(bootstrap.PLUGIN_ROOT) and "--no-deps" not in o
                   for o in _pip(ordenes)), (
        "sobrevive la orden que resuelve de cero: el lock no fija nada")


def test_si_el_lock_falla_cae_al_resolutor_y_lo_dice(bootstrap, tmp_path,
                                                    monkeypatch):
    _finge_interprete(monkeypatch, bootstrap, "3.14", "win_amd64")
    ordenes = _grabador(monkeypatch, bootstrap,
                        romper=lambda c: "--require-hashes" in c)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert resultado["source"] == "resolver"
    assert "fallo inyectado" in resultado["reason"]
    assert _pip(ordenes)[-1][-1] == str(bootstrap.PLUGIN_ROOT)


def test_el_intento_fijado_gasta_los_mismos_reintentos_que_el_de_siempre(
        bootstrap, tmp_path, monkeypatch):
    """Una carrera DNS no puede costar el pin."""
    _finge_interprete(monkeypatch, bootstrap, "3.14", "win_amd64")
    vistos: list[int] = []

    def _run(command, *, env, intentos=3):
        if "--require-hashes" in command:
            vistos.append(intentos)

    monkeypatch.setattr(bootstrap, "_run", _run)
    bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})
    assert vistos == [3]


def test_la_combinacion_se_le_pregunta_al_interprete_de_DESTINO(
        bootstrap, tmp_path, monkeypatch):
    """No al que corre el instalador, que es otro.

    El instalador corre con el Python anfitrion y crea un venv que puede ser de
    otra version. Preguntarselo al de aqui elegiria el lock equivocado.
    """
    visto = {}

    class _Salida:
        stdout = "3.13\nwin_amd64\n"

    def _run(cmd, **kwargs):
        visto["python"] = cmd[0]
        return _Salida()

    monkeypatch.setattr(bootstrap.subprocess, "run", _run)
    destino = tmp_path / "venv" / "python.exe"
    assert bootstrap.combinacion_de(destino) == ("3.13", "win_amd64")
    assert visto["python"] == str(destino)


# ===================== 4. El oraculo real =================================

def _venv_con_el_lock(destino: Path, lock: Path) -> dict[str, str]:
    subprocess.run([sys.executable, "-m", "venv", str(destino)], check=True,
                   capture_output=True, timeout=600)
    py = destino / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run([str(py), "-m", "pip", "install", "--require-hashes",
                        "-r", str(lock)], capture_output=True, text=True,
                       timeout=2400)
    assert r.returncode == 0, (
        f"{lock.name} no instala en el interprete de esta maquina:\n"
        f"{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    freeze = subprocess.run([str(py), "-m", "pip", "freeze"], check=True,
                            capture_output=True, text=True, timeout=600)
    quedaron = {}
    for linea in freeze.stdout.splitlines():
        if "==" in linea:
            n, v = linea.split("==", 1)
            quedaron[_nombre(n)] = v.strip()
    return quedaron


@pytest.mark.packaging
def test_dos_instalaciones_reales_dan_exactamente_las_mismas_versiones(
        tmp_path_factory):
    """G4.6 contra el oraculo real: pip, dos veces, y se comparan los conjuntos.

    Se usa el lock de ESTE interprete, que es el unico que se puede instalar
    aqui. Los otros dos de la matriz se verifican por integridad —formato,
    hashes, cabecera, dependencias— y su instalacion real ocurre en CI, que si
    corre 3.10 y 3.13.
    """
    if OFFLINE:
        pytest.skip("PBI_MCP_PACKAGING_OFFLINE=1 declarado a mano: sin indice "
                    "no hay instalacion real que comparar")

    generar = _cargar("generar_lock_oraculo", "generar_lock.py")
    mio = f"{sys.version_info.major}.{sys.version_info.minor}"
    lock = generar.ruta_de(mio, "win_amd64" if os.name == "nt" else "manylinux")
    if not lock.is_file():
        pytest.skip(f"no hay lock para {mio} en esta plataforma: {lock.name}")

    base = tmp_path_factory.mktemp("lock_dos_veces")
    primera = _venv_con_el_lock(base / "a", lock)
    segunda = _venv_con_el_lock(base / "b", lock)

    difieren = {n: (primera.get(n), segunda.get(n))
                for n in set(primera) | set(segunda)
                if primera.get(n) != segunda.get(n)}
    assert not difieren, (
        f"dos instalaciones consecutivas dieron versiones distintas: {difieren}")

    for linea in _entradas(lock.read_text(encoding="utf-8")):
        nombre, resto = linea.split("==", 1)
        version = resto.split(" ", 1)[0]
        assert primera.get(_nombre(nombre)) == version, (
            f"{nombre}: el lock fija {version} y quedo "
            f"{primera.get(_nombre(nombre))}")


# ===================== 5. Y queda escrito en el estado ====================

def _sembrar_runtime(carpeta: Path, bs) -> None:
    p = bs.paths(carpeta.parent, cache=carpeta)
    py = carpeta / "runtime" / p["python"].relative_to(p["runtime"])
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("#viejo", encoding="utf-8")
    for entrada in bs._salud.entry_points(carpeta / "runtime"):
        entrada.parent.mkdir(parents=True, exist_ok=True)
        entrada.write_bytes(b"")
    (carpeta / "libs").mkdir(parents=True, exist_ok=True)
    (carpeta / "libs" / "Microsoft.AnalysisServices.dll").write_text("dll")
    (carpeta / "schemas" / "pbir").mkdir(parents=True, exist_ok=True)
    (carpeta / "schemas" / "pbir" / "report.json").write_text("{}")


@pytest.mark.parametrize("combinacion,esperado", [
    (("3.14", "win_amd64"), "lock"),
    (("3.9", "win_amd64"), "resolver"),
], ids=["con-lock", "sin-lock"])
def test_el_estado_ready_registra_de_donde_salieron_las_dependencias(
        bootstrap, tmp_path, monkeypatch, combinacion, esperado):
    """La mitad que hace diagnosticable el problema.

    Cuando una maquina funciona y otra no, la primera pregunta es que se
    instalo en cada una. Si el estado no lo dice, la respuesta es «no se sabe».
    """
    raiz = tmp_path / "datos"
    _sembrar_runtime(raiz / bootstrap.VERSION, bootstrap)
    _finge_interprete(monkeypatch, bootstrap, *combinacion)
    _grabador(monkeypatch, bootstrap)
    monkeypatch.setattr(bootstrap._salud, "verificar",
                        lambda *a, **k: {"ok": True, "fase": "completo",
                                         "tools": 134,
                                         "servidor": "horizun-pbi-mcp",
                                         "version": bootstrap.VERSION})
    monkeypatch.setenv("HORIZUN_PBI_PLUGIN_DATA", str(raiz))

    assert bootstrap.install(raiz, include_validator=False) == 0
    estado = bootstrap.read_status(raiz)
    assert estado["state"] == "ready"
    assert estado["dependencias"]["source"] == esperado
    crudo = json.loads((raiz / bootstrap.VERSION / "install-status.json")
                       .read_text(encoding="utf-8"))
    assert crudo["dependencias"]["source"] == esperado
    if esperado == "resolver":
        assert "NO es reproducible" in crudo["dependencias"]["note"]
