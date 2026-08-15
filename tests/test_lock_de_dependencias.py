"""INSTALL-009 / G4.6 — dos instalaciones consecutivas dan las mismas versiones.

El defecto cabe en una linea: `install()` ejecutaba `pip install <PLUGIN_ROOT>`,
que **resuelve las dependencias de cero cada vez**. La misma maquina, dos
semanas despues, acaba con un conjunto distinto sin que nadie lo pida ni lo
note; dos maquinas, con dos productos distintos. Y cuando una funciona y la
otra no, no hay forma de saber en que se diferencian, porque nadie escribio en
ningun sitio que se instalo.

Lo que se comprueba aqui son las dos mitades del arreglo, y ninguna sobra:

1. **El lock fija de verdad.** Version exacta y SHA-256 por dependencia, el
   paquete propio fuera -no tiene hash publicado- y sin nombres repetidos.
2. **El instalador lo usa, y cuando no puede, lo dice.** Un lock que existe
   pero que el instalador no llega a usar no fija nada; y un fallback silencioso
   es peor que no tener lock, porque deja creer que la instalacion esta fijada
   cuando no lo esta. Por eso la mitad de estas pruebas van del camino
   degradado, no del feliz.
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
LOCK = RAIZ / "scripts" / "requirements.lock"

#: Unica causa declarada de skip para la prueba que instala de verdad, y la
#: pone una persona. Es la misma valvula de `test_packaging.py`, a proposito:
#: dos formas de declararse offline serian dos formas de no probar nada.
OFFLINE = os.environ.get("PBI_MCP_PACKAGING_OFFLINE") == "1"

#: `nombre==version --hash=sha256:<64 hex>`. Sin rangos, sin `>=`, sin lineas
#: sin hash: cualquiera de las tres cosas devuelve la resolucion al momento de
#: instalar, que es justo lo que INSTALL-009 quita de en medio.
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


# ===================== 1. El lock del repositorio =========================

def test_el_lock_existe_y_se_versiona():
    """Sin archivo no hay determinismo, por muy bien escrito que este el codigo."""
    assert LOCK.is_file(), (
        "falta scripts/requirements.lock. Generalo con: "
        "python scripts/generar_lock.py")


def test_cada_linea_fija_version_exacta_y_sha256():
    malas = [l for l in _entradas(LOCK.read_text(encoding="utf-8"))
             if not LINEA.match(l)]
    assert not malas, (
        "estas lineas no fijan version+hash, asi que pip volveria a resolver "
        f"en el momento de instalar: {malas}")


def test_el_paquete_propio_no_figura_en_el_lock():
    """Es la fuente local: no tiene hash publicado y se instala con --no-deps.

    Inventarle un hash para que la linea "quede bonita" seria falsificar la
    unica garantia que el archivo ofrece.
    """
    nombres = {_nombre(l) for l in _entradas(LOCK.read_text(encoding="utf-8"))}
    assert "horizun-pbi-mcp" not in nombres


def test_ninguna_dependencia_aparece_dos_veces():
    """Dos versiones del mismo paquete = la resolucion decide, no el lock."""
    nombres = [_nombre(l) for l in _entradas(LOCK.read_text(encoding="utf-8"))]
    repetidos = sorted({n for n in nombres if nombres.count(n) > 1})
    assert not repetidos, f"fijadas dos veces: {repetidos}"


def test_el_lock_declara_con_que_interprete_se_resolvio():
    """El limite tiene que estar EN el archivo, no solo en la cabeza de alguien.

    Un lock resuelto en 3.14 puede no cubrir las ruedas que 3.10 necesita.
    Quien lo lea tiene que poder saberlo sin ir a buscar el commit.
    """
    cabecera = LOCK.read_text(encoding="utf-8").split("\n\n", 1)[0]
    assert re.search(r"Resuelto con Python \d+\.\d+", cabecera)
    assert "--no-deps" in cabecera


# ===================== 2. El generador del lock ===========================

def _reporte(*paquetes: tuple[str, str, str | None]) -> dict:
    return {"install": [
        {"metadata": {"name": n, "version": v},
         "download_info": {"archive_info": {"hashes": {"sha256": h} if h else {}}}}
        for n, v, h in paquetes]}


def test_lineas_del_lock_fija_nombre_version_y_hash(generar):
    lineas = generar.lineas_del_lock(_reporte(("anyio", "4.14.2", "ab" * 32)))
    assert lineas == [f"anyio==4.14.2 --hash=sha256:{'ab' * 32}"]


def test_un_paquete_sin_hash_se_omite_en_vez_de_inventarselo(generar):
    """El propio paquete llega asi: sin `download_info` con hash."""
    lineas = generar.lineas_del_lock(
        _reporte(("horizun-pbi-mcp", "1.5.5", None), ("anyio", "4.14.2", "cd" * 32)))
    assert [_nombre(l) for l in lineas] == ["anyio"]


def test_el_mismo_conjunto_en_otro_orden_da_el_mismo_lock(generar):
    """G4.6 en el generador: pip no promete orden, el lock si.

    Si el orden dependiera de como venga el reporte, `--check` gritaria en cada
    ejecucion y en dos dias alguien lo apagaria.
    """
    a = ("anyio", "4.14.2", "11" * 32)
    b = ("pydantic", "2.9.0", "22" * 32)
    assert (generar.lineas_del_lock(_reporte(a, b))
            == generar.lineas_del_lock(_reporte(b, a)))


def test_check_pasa_cuando_el_lock_coincide(generar, tmp_path, monkeypatch,
                                            capsys):
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"# cabecera\n\nanyio==4.14.2 --hash=sha256:{'11' * 32}\n",
                    encoding="utf-8")
    monkeypatch.setattr(generar, "LOCK", lock)
    monkeypatch.setattr(generar, "resolver",
                        lambda: _reporte(("anyio", "4.14.2", "11" * 32)))
    monkeypatch.setattr(sys, "argv", ["generar_lock.py", "--check"])
    assert generar.main() == 0
    assert "al dia" in capsys.readouterr().out


def test_check_delata_un_lock_desfasado(generar, tmp_path, monkeypatch, capsys):
    """Y dice QUE cambio: un `--check` que solo grita no lo arregla nadie."""
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"# cabecera\n\nanyio==4.14.2 --hash=sha256:{'11' * 32}\n",
                    encoding="utf-8")
    monkeypatch.setattr(generar, "LOCK", lock)
    monkeypatch.setattr(generar, "resolver",
                        lambda: _reporte(("anyio", "4.15.0", "33" * 32)))
    monkeypatch.setattr(sys, "argv", ["generar_lock.py", "--check"])
    assert generar.main() == 1
    salida = capsys.readouterr().out
    assert "[+] anyio==4.15.0" in salida
    assert "[-] anyio==4.14.2" in salida


def test_check_falla_si_no_hay_lock(generar, tmp_path, monkeypatch):
    monkeypatch.setattr(generar, "LOCK", tmp_path / "no-esta.lock")
    monkeypatch.setattr(generar, "resolver", lambda: _reporte())
    monkeypatch.setattr(sys, "argv", ["generar_lock.py", "--check"])
    assert generar.main() == 1


# ===================== 3. El instalador usa el lock =======================

def _grabador(monkeypatch, bootstrap, romper=None):
    """Sustituye `_run` por un grabador. `romper` es un predicado sobre el cmd."""
    ordenes: list[list[str]] = []

    def _run(command, *, env, intentos=3):
        ordenes.append(list(command))
        if romper and romper(command):
            raise RuntimeError("fallo inyectado")

    monkeypatch.setattr(bootstrap, "_run", _run)
    return ordenes


def _pip(ordenes: list[list[str]]) -> list[list[str]]:
    return [o for o in ordenes if "pip" in o and "install" in o]


def test_con_lock_se_instala_fijado_y_el_paquete_propio_aparte(
        bootstrap, tmp_path, monkeypatch):
    ordenes = _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias(
        {"python": tmp_path / "python.exe"}, {})

    assert resultado["source"] == "lock"
    fijado, propio = _pip(ordenes)
    assert "--require-hashes" in fijado and str(bootstrap.LOCK) in fijado
    assert "--no-deps" in propio and str(bootstrap.PLUGIN_ROOT) in propio
    # Lo que NO puede pasar: la orden de siempre, que resuelve de cero. Si
    # sobrevive junto al lock, el lock no fija nada.
    assert not any(o[-1] == str(bootstrap.PLUGIN_ROOT) and "--no-deps" not in o
                   for o in _pip(ordenes))


def test_dos_instalaciones_consecutivas_piden_exactamente_lo_mismo(
        bootstrap, tmp_path, monkeypatch):
    """G4.6 literal, y por eso se comparan los CONJUNTOS RESUELTOS.

    Que las dos ordenes sean iguales no basta por si solo -`pip install <repo>`
    tambien es igual las dos veces y resuelve distinto-. Lo que hace la
    diferencia es que la orden lleve dentro el conjunto entero fijado: mismas
    versiones, mismos hashes, las dos veces.
    """
    primera = _grabador(monkeypatch, bootstrap)
    bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})
    segunda = _grabador(monkeypatch, bootstrap)
    bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert primera == segunda
    pedido = _entradas(LOCK.read_text(encoding="utf-8"))
    assert pedido and all(LINEA.match(l) for l in pedido)


def test_sin_lock_cae_al_resolutor_y_lo_dice(bootstrap, tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "LOCK", tmp_path / "no-esta.lock")
    ordenes = _grabador(monkeypatch, bootstrap)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert resultado["source"] == "resolver"
    assert "no existe" in resultado["reason"]
    assert "NO estan fijadas" in resultado["note"]
    assert _pip(ordenes) == [[str(tmp_path / "py"), "-m", "pip", "install",
                             str(bootstrap.PLUGIN_ROOT)]]


def test_si_el_lock_no_cubre_el_entorno_no_finge_determinismo(
        bootstrap, tmp_path, monkeypatch):
    """El caso real: lock resuelto en 3.14, instalacion en 3.10.

    `--require-hashes` exige que TODO lo que vaya a instalarse este listado, asi
    que falla entero. Fallar la instalacion por una garantia que no aplica seria
    peor que la garantia; **quedarse callado, tambien**, y esto ultimo es lo que
    mide la prueba: la instalacion sale adelante, y el estado no dice `lock`.
    """
    ordenes = _grabador(monkeypatch, bootstrap,
                        romper=lambda c: "--require-hashes" in c)
    resultado = bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})

    assert resultado["source"] == "resolver"
    assert "fallo inyectado" in resultado["reason"]
    assert "generar_lock.py" in resultado["note"], (
        "el estado tiene que decir COMO se arregla, no solo que fallo")
    assert "fijadas" not in resultado.get("note", "").replace("NO estan fijadas", "")
    assert _pip(ordenes)[-1][-1] == str(bootstrap.PLUGIN_ROOT)


def test_el_intento_fijado_gasta_los_mismos_reintentos_que_el_de_siempre(
        bootstrap, tmp_path, monkeypatch):
    """Una carrera DNS no puede costar el pin.

    Con menos reintentos que el camino ordinario, un fallo de red -medido y
    frecuente en este proyecto- tumbaria el lock por un motivo que no tiene nada
    que ver con el lock, y la instalacion saldria sin fijar habiendo un lock
    perfectamente valido.
    """
    vistos: list[int] = []

    def _run(command, *, env, intentos=3):
        if "--require-hashes" in command:
            vistos.append(intentos)

    monkeypatch.setattr(bootstrap, "_run", _run)
    bootstrap._instalar_dependencias({"python": tmp_path / "py"}, {})
    assert vistos == [3]


# ============ 4. El oraculo real: instalar dos veces y comparar ===========

def _venv_con_el_lock(destino: Path) -> dict[str, str]:
    """Crea un venv limpio, instala el lock y devuelve lo que quedo dentro."""
    subprocess.run([sys.executable, "-m", "venv", str(destino)], check=True,
                   capture_output=True, timeout=600)
    py = destino / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    r = subprocess.run([str(py), "-m", "pip", "install", "--require-hashes",
                        "-r", str(LOCK)], capture_output=True, text=True,
                       timeout=2400)
    assert r.returncode == 0, (
        "el lock no instala en el interprete de esta maquina:\n"
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

    Las pruebas de arriba miden que se PIDE lo mismo. Esta mide que se OBTIENE
    lo mismo, que es lo que dice el gate y no se deduce de lo otro: pip podria
    resolver un extra, o el lock podria estar incompleto y nadie se enteraria
    hasta la maquina de otro.

    Es lenta y necesita indice: por eso lleva la marca `packaging` y la misma
    valvula manual que el resto de pruebas que instalan de verdad. Ninguna
    prueba se declara offline sola.
    """
    if OFFLINE:
        pytest.skip("PBI_MCP_PACKAGING_OFFLINE=1 declarado a mano: sin indice "
                    "no hay instalacion real que comparar")

    base = tmp_path_factory.mktemp("lock_dos_veces")
    primera = _venv_con_el_lock(base / "a")
    segunda = _venv_con_el_lock(base / "b")

    difieren = {n: (primera.get(n), segunda.get(n))
                for n in set(primera) | set(segunda)
                if primera.get(n) != segunda.get(n)}
    assert not difieren, (
        f"dos instalaciones consecutivas dieron versiones distintas: {difieren}")

    # Y coinciden con lo fijado, no solo entre si: dos instalaciones igual de
    # equivocadas tambien serian iguales.
    for linea in _entradas(LOCK.read_text(encoding="utf-8")):
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


@pytest.mark.parametrize("hay_lock, esperado", [(True, "lock"), (False, "resolver")],
                         ids=["con-lock", "sin-lock"])
def test_el_estado_ready_registra_de_donde_salieron_las_dependencias(
        bootstrap, tmp_path, monkeypatch, hay_lock, esperado):
    """La mitad que hace diagnosticable el problema.

    Cuando una maquina funciona y otra no, la primera pregunta es que se
    instalo en cada una. Si el estado no lo dice, la respuesta es "no se sabe" y
    el lock solo sirve para las que ya iban bien.
    """
    raiz = tmp_path / "datos"
    _sembrar_runtime(raiz / bootstrap.VERSION, bootstrap)
    if not hay_lock:
        monkeypatch.setattr(bootstrap, "LOCK", tmp_path / "no-esta.lock")
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
    # Y sobrevive al viaje por JSON: es lo que leera quien diagnostique.
    crudo = json.loads((raiz / bootstrap.VERSION / "install-status.json")
                       .read_text(encoding="utf-8"))
    assert crudo["dependencias"]["source"] == esperado
