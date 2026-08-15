"""INSTALL-011 — la recuperacion no puede fiarse del journal ni correr suelta.

Dos defectos que se reproducen juntos porque se agravan juntos:

1. `promotion.recuperar()` sacaba `staging`, `destino` y `anterior` del
   `.promotion.json` como RUTAS ABSOLUTAS y las usaba tal cual. El journal es
   un archivo en disco: cualquiera que pueda escribir en el directorio de datos
   -o un journal de otra instalacion, o uno corrupto- decide a que ruta le hace
   `os.rename` un proceso que probablemente arranco solo. Un journal preparado
   en un directorio temporal consiguio mover `root/.staging-demo` a una carpeta
   HERMANA de la raiz. Un instalador que escribe fuera de su propio directorio
   de datos no es un instalador.

2. `plugin_bootstrap.install()` llamaba a `recuperar(root)` ANTES de adquirir
   el cerrojo. O sea que la parte que renombra el runtime vigente era
   exactamente la que corria sin exclusion mutua, y podia solaparse con la
   promocion de otro instalador sobre las mismas carpetas.

La correccion tiene dos mitades y las dos hacen falta: el journal deja de ser
autoridad sobre rutas -solo guarda NOMBRES de hijos directos, y se validan
lexica y resueltamente contra la raiz- y todo el ciclo de vida pasa a ocurrir
dentro del cerrojo.

Todo lo destructivo de este archivo ocurre bajo `tmp_path`. Las rutas "de
fuera" tambien son temporales: son hermanas de la raiz, no del sistema.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _cargar(nombre: str):
    ruta = RAIZ / "src" / "horizun_pbi_mcp" / "lifecycle" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_lc_{nombre}_{uuid.uuid4().hex}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def promo():
    return _cargar("promotion")


@pytest.fixture
def cerrojos():
    return _cargar("locking")


@pytest.fixture
def bootstrap():
    spec = importlib.util.spec_from_file_location(
        f"_bs_{uuid.uuid4().hex}", RAIZ / "scripts" / "plugin_bootstrap.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ------------------------------------------------------------- utilidades ---
def _escribir_journal_crudo(root: Path, **valores) -> None:
    """Escribe un journal SIN pasar por la API: es lo que hace un atacante."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "promotion_json_placeholder").unlink(missing_ok=True)
    (root / ".promotion.json").write_text(
        json.dumps(valores, indent=2), encoding="utf-8")


def _carpeta_con_marca(ruta: Path, marca: str) -> Path:
    ruta.mkdir(parents=True, exist_ok=True)
    (ruta / "MARCA.txt").write_text(marca, encoding="utf-8")
    return ruta


def _huella(ruta: Path) -> set:
    """Que hay dentro, con su contenido. Sirve para exigir 'no se toco nada'."""
    if not ruta.exists():
        return set()
    salida = set()
    for p in sorted(ruta.rglob("*")):
        salida.add((str(p.relative_to(ruta)),
                    p.read_text(encoding="utf-8") if p.is_file() else "<dir>"))
    return salida


def _enlace_de_directorio(enlace: Path, destino: Path) -> bool:
    """Junction en Windows, symlink en POSIX. False si el sistema no deja."""
    try:
        if os.name == "nt":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(enlace), str(destino)],
                               capture_output=True, text=True, timeout=30)
            return r.returncode == 0 and enlace.exists()
        os.symlink(destino, enlace, target_is_directory=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


# ============================================================================
# 1. El journal no manda sobre rutas
# ============================================================================
def test_un_destino_fuera_de_root_no_se_crea_ni_se_modifica(promo, tmp_path):
    """El caso exacto que reprodujo la revision independiente."""
    root = tmp_path / "datos"
    fuera = tmp_path / "OUTSIDE_DESTINATION"
    _carpeta_con_marca(fuera, "intacta")
    staging = _carpeta_con_marca(root / ".staging-demo", "staging")
    antes_fuera, antes_staging = _huella(fuera), _huella(staging)

    _escribir_journal_crudo(root, fase="preparada", staging=str(staging),
                            destino=str(fuera / "robado"), anterior=None,
                            ts=time.time())

    resultado = promo.recuperar(root)

    assert not (fuera / "robado").exists(), (
        "la recuperacion creo una ruta FUERA del directorio de datos")
    assert _huella(fuera) == antes_fuera, "toco una carpeta ajena"
    assert _huella(staging) == antes_staging, "movio el staging a donde le dijeron"
    assert resultado["accion"] == "journal-invalido", resultado


def test_un_staging_externo_no_se_renombra_ni_se_borra(promo, tmp_path):
    root = tmp_path / "datos"
    root.mkdir()
    ajeno = _carpeta_con_marca(tmp_path / "staging-ajeno", "de otro")
    antes = _huella(ajeno)

    # `destino` existe: por la rama antigua esto habria BORRADO el staging.
    destino = _carpeta_con_marca(root / "1.5.5", "vigente")
    _escribir_journal_crudo(root, fase="preparada", staging=str(ajeno),
                            destino=str(destino), anterior=None, ts=time.time())

    resultado = promo.recuperar(root)

    assert ajeno.is_dir() and _huella(ajeno) == antes, (
        "la recuperacion borro o movio una carpeta que no es suya")
    assert resultado["accion"] == "journal-invalido", resultado


def test_un_anterior_externo_no_se_renombra(promo, tmp_path):
    root = tmp_path / "datos"
    root.mkdir()
    ajeno = _carpeta_con_marca(tmp_path / ".previous-ajeno", "de otro")
    antes = _huella(ajeno)

    _escribir_journal_crudo(root, fase="anterior-apartado",
                            staging=str(root / ".staging-noexiste"),
                            destino=str(root / "1.5.5"), anterior=str(ajeno),
                            ts=time.time())

    resultado = promo.recuperar(root)

    assert ajeno.is_dir() and _huella(ajeno) == antes
    assert not (root / "1.5.5").exists(), "trajo dentro una carpeta de fuera"
    assert resultado["accion"] == "journal-invalido", resultado


@pytest.mark.parametrize("nombre", [
    "..",
    "../fuera",
    "..\\fuera",
    "sub/dir",
    "sub\\dir",
    "C:\\Windows\\Temp\\robado",
    "/etc/robado",
    "\\\\servidor\\compartido\\robado",
    "flujo:alterno",
    " espacio-al-borde ",
    "",
])
def test_ningun_nombre_tramposo_escapa_de_la_raiz(promo, tmp_path, nombre):
    """Lexico: `..`, separadores de los DOS sistemas, absolutas, UNC y ADS."""
    root = tmp_path / "datos"
    root.mkdir()
    _carpeta_con_marca(root / ".staging-x", "staging")
    hermana = _carpeta_con_marca(tmp_path / "fuera", "intacta")
    antes = _huella(hermana)

    _escribir_journal_crudo(root, esquema=2, fase="preparada",
                            staging=".staging-x", destino=nombre,
                            anterior=None, ts=time.time())

    resultado = promo.recuperar(root)

    assert resultado["accion"] == "journal-invalido", (
        f"{nombre!r} se acepto como destino: {resultado}")
    assert _huella(hermana) == antes
    assert (root / ".staging-x").is_dir(), "movio el staging igualmente"


@pytest.mark.skipif(os.name != "nt" and not hasattr(os, "symlink"),
                    reason="el sistema no crea enlaces de directorio")
def test_un_enlace_de_directorio_no_saca_la_promocion_de_la_raiz(promo, tmp_path):
    """Lo que la comprobacion lexica NO puede ver: el disco mintiendo.

    `root/.staging-trampa` es un nombre de hijo directo impecable. Si ademas es
    una junction que apunta fuera, seguirla saca la operacion de la raiz sin
    que ningun `..` aparezca en el journal. Por eso la validacion tambien
    RESUELVE.
    """
    root = tmp_path / "datos"
    root.mkdir()
    real = _carpeta_con_marca(tmp_path / "fuera-real", "de otro")
    enlace = root / ".staging-trampa"
    if not _enlace_de_directorio(enlace, real):
        pytest.skip("este sistema no permite crear junctions/symlinks")
    antes = _huella(real)

    _escribir_journal_crudo(root, esquema=2, fase="preparada",
                            staging=".staging-trampa", destino="1.5.5",
                            anterior=None, ts=time.time())

    resultado = promo.recuperar(root)

    assert resultado["accion"] == "journal-invalido", resultado
    assert _huella(real) == antes, "siguio el enlace y se llevo la carpeta real"


@pytest.mark.parametrize("journal,por_que", [
    ({}, "sin esquema ni fase"),
    ({"esquema": 1, "fase": "preparada", "staging": ".staging-x",
      "destino": "1.5.5"}, "esquema viejo"),
    ({"esquema": 99, "fase": "preparada", "staging": ".staging-x",
      "destino": "1.5.5"}, "esquema del futuro"),
    ({"esquema": 2, "fase": "inventada", "staging": ".staging-x",
      "destino": "1.5.5"}, "fase que no existe"),
    ({"esquema": 2, "fase": "completa", "staging": ".staging-x",
      "destino": "1.5.5"}, "fase que no deberia sobrevivir en disco"),
    ({"esquema": 2, "fase": "preparada", "staging": "1.5.5",
      "destino": "1.5.5"}, "staging sin su prefijo"),
    ({"esquema": 2, "fase": "preparada", "staging": ".staging-x",
      "destino": ".previous-x"}, "destino con prefijo reservado"),
    ({"esquema": 2, "fase": "anterior-apartado", "staging": ".staging-x",
      "destino": "1.5.5", "anterior": "otra-cosa"}, "anterior sin su prefijo"),
    ({"esquema": 2, "fase": "preparada", "staging": 12, "destino": "1.5.5"},
     "staging que no es texto"),
])
def test_un_journal_invalido_falla_de_forma_segura(promo, tmp_path, journal, por_que):
    """Invalido = no se toca NADA de lo que menciona, y se dice por que."""
    root = tmp_path / "datos"
    root.mkdir()
    vigente = _carpeta_con_marca(root / "1.5.5", "vigente")
    staging = _carpeta_con_marca(root / ".staging-x", "staging")
    previo = _carpeta_con_marca(root / ".previous-x", "previo")
    antes = (_huella(vigente), _huella(staging), _huella(previo))

    _escribir_journal_crudo(root, **journal)
    resultado = promo.recuperar(root)

    assert resultado["accion"] == "journal-invalido", f"{por_que}: {resultado}"
    assert resultado.get("motivo"), "no explico por que lo rechaza"
    assert (_huella(vigente), _huella(staging), _huella(previo)) == antes, (
        f"{por_que}: toco algo pese a rechazar el journal")


def test_el_journal_rechazado_queda_en_cuarentena_dentro_de_root(promo, tmp_path):
    """La evidencia no se tira: se aparta, y dentro de la raiz."""
    root = tmp_path / "datos"
    root.mkdir()
    _escribir_journal_crudo(root, fase="inventada", destino="../fuera")

    resultado = promo.recuperar(root)

    assert resultado["accion"] == "journal-invalido"
    cuarentena = resultado.get("cuarentena")
    assert cuarentena, "no aparto la evidencia"
    ruta = Path(cuarentena)
    assert ruta.is_file(), "la cuarentena no existe"
    assert ruta.parent == root, "aparto la evidencia FUERA de la raiz"
    assert not (root / ".promotion.json").exists(), (
        "dejo el journal invalido en su sitio: el siguiente arranque lo "
        "volveria a leer")
    assert "inventada" in ruta.read_text(encoding="utf-8")


def test_el_journal_que_escribe_la_promocion_no_lleva_rutas_absolutas(promo, tmp_path):
    """La otra mitad: si se escribiera absoluto, validarlo al leer no serviria."""
    root = tmp_path / "datos"
    staging = _carpeta_con_marca(root / ".staging-nuevo", "nuevo")
    destino = root / "1.5.5"

    visto = {}
    original = promo._escribir_journal

    def espia(r, **valores):
        visto.update(valores)
        return original(r, **valores)

    promo._escribir_journal = espia
    try:
        promo.promover(root, staging, destino)
    finally:
        promo._escribir_journal = original

    assert visto.get("esquema") == 2, f"el journal no declara esquema: {visto}"
    for clave in ("staging", "destino", "anterior"):
        valor = visto.get(clave)
        if valor is None:
            continue
        assert not os.path.isabs(valor), f"{clave} se escribio absoluto: {valor}"
        assert "/" not in valor and "\\" not in valor, (
            f"{clave} no es un nombre simple: {valor}")


def test_la_recuperacion_legitima_sigue_funcionando(promo, tmp_path):
    """Contener no puede significar dejar de recuperar."""
    root = tmp_path / "datos"
    staging = _carpeta_con_marca(root / ".staging-1.5.5-abc", "nuevo")
    anterior = _carpeta_con_marca(root / ".previous-1.5.5-9-abc", "viejo")
    destino = root / "1.5.5"

    _escribir_journal_crudo(root, esquema=2, fase="anterior-apartado",
                            staging=".staging-1.5.5-abc", destino="1.5.5",
                            anterior=".previous-1.5.5-9-abc", ts=time.time())

    resultado = promo.recuperar(root)

    assert resultado["accion"] == "reintentada", resultado
    assert (destino / "MARCA.txt").read_text(encoding="utf-8") == "nuevo"
    assert not (root / ".promotion.json").exists()


def test_sin_staging_la_recuperacion_devuelve_el_anterior(promo, tmp_path):
    root = tmp_path / "datos"
    root.mkdir()
    _carpeta_con_marca(root / ".previous-1.5.5-9-abc", "viejo")

    _escribir_journal_crudo(root, esquema=2, fase="anterior-apartado",
                            staging=".staging-perdido", destino="1.5.5",
                            anterior=".previous-1.5.5-9-abc", ts=time.time())

    resultado = promo.recuperar(root)

    assert resultado["accion"] == "revertida", resultado
    assert (root / "1.5.5" / "MARCA.txt").read_text(encoding="utf-8") == "viejo"


# ============================================================================
# 2. Nombres de `.previous-` que no colisionan
# ============================================================================
def test_dos_promociones_en_el_mismo_segundo_no_pisan_el_mismo_previous(
        promo, tmp_path, monkeypatch):
    """Con el sufijo de segundos, dos promociones seguidas compartian nombre.

    Y compartir nombre aqui no es un detalle estetico: el segundo `os.rename`
    sobre un destino existente falla en Windows, asi que la actualizacion se
    caia; en POSIX lo habria SOBRESCRITO, perdiendo el N-1 anterior.
    """
    monkeypatch.setattr(promo.time, "time", lambda: 1786000000.0)
    root = tmp_path / "datos"
    _carpeta_con_marca(root / "1.5.5", "vigente-de-partida")

    for i in range(2):
        staging = _carpeta_con_marca(root / f".staging-v-{i}", f"nuevo{i}")
        promo.promover(root, staging, root / "1.5.5")

    nombres = [d.name for d in promo.anteriores(root)]
    assert len(set(nombres)) == len(nombres) == 2, (
        f"dos promociones en el mismo segundo produjeron {nombres}")


# ============================================================================
# 3. El cerrojo acredita a un proceso, no a un numero
# ============================================================================
@pytest.fixture
def proceso_vivo_ajeno():
    """Un proceso real, de otro, que sigue vivo mientras dura la prueba."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        yield proc
    finally:
        proc.kill()
        proc.wait(timeout=30)


def test_el_lock_solo_lo_borra_quien_lo_tiene(cerrojos, tmp_path):
    """Un token de propiedad, o `__exit__` borra el cerrojo de otro.

    Sin token, este es el desenlace: A se cree dueño, su lock se roba por
    caducidad, B entra, A termina y al salir borra el archivo de B. B se queda
    promoviendo sin exclusion y sin saberlo.
    """
    root = tmp_path / "datos"
    with cerrojos.CerrojoDeCicloDeVida(root, etiqueta="a") as a:
        assert a.adquirido
        contenido = json.loads(a.ruta.read_text(encoding="utf-8"))
        assert contenido.get("token"), "el lock no dice QUIEN es, solo que existe"
        # Otro proceso se hace con el cerrojo (el archivo cambia de dueño).
        a.ruta.write_text(json.dumps({"pid": os.getpid(), "token": "de-otro",
                                      "started": time.time()}), encoding="utf-8")
    assert a.ruta.is_file(), (
        "al salir borro un cerrojo que ya no era suyo")
    assert json.loads(a.ruta.read_text(encoding="utf-8"))["token"] == "de-otro"


def test_un_pid_reciclado_no_acredita_al_dueno_del_lock(cerrojos, tmp_path,
                                                        proceso_vivo_ajeno):
    """Que EXISTA un proceso con ese PID no prueba que sea el mismo proceso.

    Los PID se reciclan, y en Windows deprisa. Un lock huerfano cuyo PID haya
    sido reutilizado por cualquier programa congelaria la instalacion para
    siempre: `lock_vivo` diria que si y nadie volveria a intentarlo.
    """
    lock = tmp_path / "lifecycle.lock"
    pid = proceso_vivo_ajeno.pid

    # El proceso existe Y la identidad cuadra: el lock acredita.
    lock.write_text(json.dumps({
        "pid": pid, "token": "x", "started": time.time(),
        "proc_creado": cerrojos.creacion_de_proceso(pid)}), encoding="utf-8")
    assert cerrojos.lock_vivo(lock) is True

    # Mismo PID vivo, otra identidad: es un PID reciclado, no el dueño.
    lock.write_text(json.dumps({
        "pid": pid, "token": "x", "started": time.time(),
        "proc_creado": 1.0}), encoding="utf-8")
    assert cerrojos.lock_vivo(lock) is False, (
        "acredito a un proceso distinto solo porque comparte el numero de PID")


def test_un_lock_de_un_proceso_muerto_se_roba(cerrojos, tmp_path):
    lock = tmp_path / "lifecycle.lock"
    muerto = subprocess.Popen([sys.executable, "-c", "pass"])
    muerto.wait(timeout=30)
    lock.write_text(json.dumps({"pid": muerto.pid, "token": "x",
                                "started": time.time(), "proc_creado": 1.0}),
                    encoding="utf-8")
    assert cerrojos.lock_vivo(lock) is False
    with cerrojos.CerrojoDeCicloDeVida(tmp_path, etiqueta="b") as b:
        assert b.adquirido, "un lock huerfano congelo la instalacion"


# ============================================================================
# 4. Todo el ciclo de vida ocurre DENTRO del cerrojo
# ============================================================================
def test_la_recuperacion_ocurre_con_el_cerrojo_en_la_mano(bootstrap, tmp_path,
                                                          monkeypatch):
    """El orden importa: recuperar renombra el runtime vigente."""
    root = tmp_path / "datos"
    root.mkdir()
    orden: list[str] = []

    original_recuperar = bootstrap._promocion.recuperar
    monkeypatch.setattr(bootstrap._promocion, "recuperar",
                        lambda r: (orden.append("recuperar"),
                                   original_recuperar(r))[1])
    entrar = bootstrap._cerrojos.CerrojoDeCicloDeVida.__enter__

    def espia_entrar(self):
        salida = entrar(self)
        orden.append("lock" if salida.adquirido else "lock-fallido")
        return salida

    monkeypatch.setattr(bootstrap._cerrojos.CerrojoDeCicloDeVida,
                        "__enter__", espia_entrar)
    monkeypatch.setattr(bootstrap, "_run",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corto")))

    bootstrap.install(root, include_validator=False)

    assert "lock" in orden and "recuperar" in orden, orden
    assert orden.index("lock") < orden.index("recuperar"), (
        f"se recupero ANTES de adquirir el cerrojo: {orden}")


def test_el_segundo_instalador_no_pisa_el_status_del_dueno(bootstrap, tmp_path,
                                                           proceso_vivo_ajeno):
    """Quien no tiene el cerrojo no escribe: el avance del dueño es el bueno."""
    root = tmp_path / "datos"
    p = bootstrap.paths(root)
    p["cache"].mkdir(parents=True, exist_ok=True)
    bootstrap._write_status(p, state="installing", ready=False,
                            step="python-packages",
                            message="Instalando el paquete abierto.")
    antes = p["status"].read_text(encoding="utf-8")

    lock = p["lock"]
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({
        "pid": proceso_vivo_ajeno.pid, "token": "del-dueño",
        "started": time.time(), "etiqueta": "install",
        "proc_creado": bootstrap._cerrojos.creacion_de_proceso(
            proceso_vivo_ajeno.pid)}), encoding="utf-8")

    assert bootstrap.install(root, include_validator=False) == 0

    assert p["status"].read_text(encoding="utf-8") == antes, (
        "el segundo instalador sobrescribio el avance del que tiene el cerrojo")
    assert lock.read_text(encoding="utf-8").count("del-due") == 1, (
        "ademas le toco el cerrojo")


def test_recuperacion_y_promocion_no_se_solapan(bootstrap, tmp_path,
                                                proceso_vivo_ajeno):
    """Con el cerrojo en manos ajenas, `install` no renombra NADA."""
    root = tmp_path / "datos"
    root.mkdir()
    staging = _carpeta_con_marca(root / ".staging-x", "staging")
    vigente = _carpeta_con_marca(root / "1.5.5", "vigente")
    _escribir_journal_crudo(root, esquema=2, fase="preparada",
                            staging=".staging-x", destino="1.5.5",
                            anterior=None, ts=time.time())
    antes = (_huella(staging), _huella(vigente),
             (root / ".promotion.json").read_text(encoding="utf-8"))

    lock = bootstrap.paths(root)["lock"]
    lock.write_text(json.dumps({
        "pid": proceso_vivo_ajeno.pid, "token": "del-dueño",
        "started": time.time(),
        "proc_creado": bootstrap._cerrojos.creacion_de_proceso(
            proceso_vivo_ajeno.pid)}), encoding="utf-8")

    assert bootstrap.install(root, include_validator=False) == 0

    assert (_huella(staging), _huella(vigente),
            (root / ".promotion.json").read_text(encoding="utf-8")) == antes, (
        "recupero mientras otro proceso tenia el cerrojo del ciclo de vida")
