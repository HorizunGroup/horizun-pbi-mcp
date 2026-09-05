"""Prepara el runtime aislado usado por los plugins de Codex y Claude.

Solo usa la biblioteca estándar del Python anfitrión. Las dependencias del
producto, las DLL de Microsoft y los esquemas PBIR quedan bajo el directorio de
datos del plugin, nunca dentro del checkout ni de un proyecto del usuario.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

#: Duplicada a proposito: el bootstrap corre ANTES de que el paquete
#: exista, asi que no puede importar `branding`. Una prueba compara
#: las dos y falla si se separan, que es como se caza este olvido.
VERSION = "2.1.0"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _cargar_modulo_del_paquete(nombre: str):
    """Carga un modulo de `src/horizun_pbi_mcp/lifecycle/` POR RUTA.

    No se hace `import horizun_pbi_mcp.lifecycle` a proposito. Este archivo
    corre con el Python ANFITRION, antes de que exista el entorno aislado:
    importar el paquete ejecutaria su `__init__` y con el sus dependencias, que
    todavia no estan instaladas. Cargar el modulo suelto evita esa cadena.

    El nucleo vive en el paquete y no aqui para que exista UNA implementacion:
    la misma que usara la CLI empaquetada cuando alguien instale por `pip`.
    """
    import importlib.util

    ruta = PLUGIN_ROOT / "src" / "horizun_pbi_mcp" / "lifecycle" / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"_horizun_lifecycle_{nombre}", ruta)
    if spec is None or spec.loader is None:            # pragma: no cover
        raise ImportError(f"no se pudo cargar el nucleo de ciclo de vida: {ruta}")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


_promocion = _cargar_modulo_del_paquete("promotion")
_cerrojos = _cargar_modulo_del_paquete("locking")
_salud = _cargar_modulo_del_paquete("healthcheck")
_estado = _cargar_modulo_del_paquete("runtime_state")
_procesos = _cargar_modulo_del_paquete("procesos")

#: Lo reconstruible: vive bajo `<raiz>/<VERSION>` y se descarga verificado por
#: hash. Borrarlo cuesta una reinstalacion, nunca un dato del usuario.
CACHE = ("runtime", "libs", "schemas", "validator")

#: Margen para que el instalador hijo cree su lock despues de que el lanzador
#: haya marcado `installing`. Pasado ese arranque, manda el lock.
GRACIA_ARRANQUE = 90.0


def data_dir() -> Path:
    """Raiz ESTABLE de datos, la instale quien la instale.

    Antes mandaba `CLAUDE_PLUGIN_DATA`, y ese nombre lo elige el cliente: en
    una misma maquina paso de `horizun-pbi-mcp-horizun` a
    `horizun-pbi-mcp-inline` en menos de 15 horas. Cada cambio de nombre
    reconstruia el runtime entero (venv, pip, DLL de Analysis Services,
    esquemas PBIR y los 586 archivos del validador npm) y dejaba atras la
    carpeta anterior. La ruta del runtime no puede depender de un nombre que no
    controlamos: solo la respeta el override explicito, que es el que usan las
    pruebas y las instalaciones a medida.
    """
    override = os.environ.get("HORIZUN_PBI_PLUGIN_DATA")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return (Path(base) / "HorizunPbiMcp" / "plugin").resolve()


def paths(base: Path | None = None, cache: Path | None = None) -> dict[str, Path]:
    """Rutas del ciclo de vida.

    `cache` permite apuntar los componentes al STAGING mientras se construye,
    sin mover el estado ni los datos del usuario: el status sigue escribiendose
    en la ubicacion viva para que el lanzador vea el avance, y `outputs` y
    `backups` cuelgan de la raiz y no se versionan nunca.
    """
    root = base or data_dir()
    cache = Path(cache) if cache is not None else root / VERSION
    runtime = cache / "runtime"
    py = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return {
        "root": root,
        "cache": cache,
        "runtime": runtime,
        "python": py,
        "status": cache / "install-status.json",
        # En la RAIZ, no en la cache: un cerrojo dentro de la carpeta que la
        # promocion renombra no protege la promocion.
        "lock": root / _cerrojos.NOMBRE,
        # En la RAIZ por la MISMA razon, y esta costo una instalacion. El
        # lanzador abre este archivo y se lo pasa al instalador detachado como
        # stdout, asi que el proceso lo tiene abierto de principio a fin.
        # Estando dentro de `cache`, la promocion intentaba renombrar la
        # carpeta que contenia el stdout del propio proceso que renombraba: en
        # Windows eso es ERROR_ACCESS_DENIED siempre, no a veces. La
        # instalacion manual no lo veia porque ahi el stdout es la consola.
        "log": root / "install.log",
        "libs": cache / "libs",
        "schemas": cache / "schemas" / "pbir",
        "validator": cache / "validator",
        # Lo del usuario NO se versiona: sus exportaciones y sus respaldos
        # sobreviven a cada actualizacion, y siempre en el mismo sitio.
        "outputs": root / "outputs",
        "backups": root / "backups",
    }


class EstadoStatusCorrupto(RuntimeError):
    """El status existe, pero reescribirlo destruiria evidencia diagnostica."""


def read_status(base: Path | None = None) -> dict[str, Any]:
    p = paths(base)
    try:
        result = json.loads(p["status"].read_text(encoding="utf-8"))
    except FileNotFoundError:
        result = {"state": "not_installed", "ready": False, "version": VERSION}
    except OSError as exc:
        result = {
            "state": "unreadable", "ready": False, "version": VERSION,
            "message": (f"No se pudo leer {p['status']}: {type(exc).__name__}. "
                        "El archivo se conserva intacto para diagnostico."),
        }
    except (ValueError, TypeError) as exc:
        result = {
            "state": "corrupt", "ready": False, "version": VERSION,
            "message": (f"{p['status']} no contiene JSON valido "
                        f"({type(exc).__name__}). El archivo se conserva "
                        "intacto; inspeccionalo o apartalo antes de reinstalar."),
        }
    if not isinstance(result, dict):
        result = {
            "state": "corrupt", "ready": False, "version": VERSION,
            "message": (f"{p['status']} no contiene un objeto JSON. El archivo "
                        "se conserva intacto; inspeccionalo o apartalo antes de "
                        "reinstalar."),
        }
    result["data_dir"] = str(p["root"])
    result["runtime_dir"] = str(p["cache"])
    result["log"] = str(p["log"])

    # Lo que `install-status.json` por si solo no puede decir: si hay algo
    # sirviendo. Antes, un `failed` se leia como "no hay nada", aunque el
    # runtime anterior siguiera entero. Los dos hechos van juntos y separados:
    # como fue el ultimo intento, y que se esta sirviendo de verdad.
    seleccion = seleccionar_runtime(base)
    estado = _estado.leer(p["root"])

    # `state` deja de ser el resultado del ultimo intento y pasa a ser el estado
    # OPERATIVO. G3.3 lo pide literalmente: corromper el runtime y exigir
    # `state != ready`. Antes se cambiaba `sirviendo` a last-known-good y
    # `state` seguia en `ready`, o sea que el campo que un cliente mira para
    # saber si esto funciona seguia diciendo que si sobre algo que ya no
    # arranca. El resultado del ultimo intento no se pierde: se muda a
    # `estado_instalacion`.
    result["estado_instalacion"] = result.get("state")
    degradacion = estado["degradado"]
    if degradacion and degradacion["carpeta"] != p["cache"].name:
        degradacion = None                # es de otra version: ya no aplica
    if (result.get("state") == "ready" and not degradacion
            and seleccion["modo"] != "activo"):
        # Degradacion que se ve sin arrancar nada -falta el interprete, faltan
        # los entry points-. No hace falta esperar a que alguien la anote.
        degradacion = {"carpeta": p["cache"].name, "fase": "estructura",
                       "motivo": "el runtime activo ya no esta completo en "
                                 "disco (interprete o entry points)",
                       "ts": None}
    if degradacion and result.get("state") == "ready":
        result["state"] = "degraded"
        result["ready"] = False
    result["degradacion"] = degradacion

    result["sirviendo"] = seleccion["modo"]
    result["sirviendo_version"] = seleccion["version"]
    # La evidencia de LO QUE SE VA A EJECUTAR. `last_known_good` de abajo es el
    # campo del estado y puede estar vacio -tras la primera instalacion buena
    # todavia no hay un N−1-, y aun asi haber algo que servir: el propio
    # `activo`. Quien diagnostica necesita saber que se ejecutaria, no que
    # campo esta relleno.
    result["sirviendo_evidencia"] = seleccion["evidencia"]
    result["last_known_good"] = estado["last_known_good"]
    result["ultimo_intento"] = estado["ultimo_intento"]
    return result


#: Plazo del preflight de ARRANQUE. Mas corto que el de instalacion (180 s) a
#: proposito: aqui hay un cliente MCP esperando, y su propio plazo de arranque
#: es del orden del minuto. Pasado eso el cliente ya se habria rendido, asi que
#: seguir esperando solo retrasa el momento de servirle N−1.
PREFLIGHT_TIMEOUT = 60


def _degradar(root: Path, carpeta: str, veredicto: dict[str, Any]) -> bool:
    """Anota que ese runtime ya no es operativo. **Bajo el cerrojo.**

    Escribir `runtime-state.json` es tocar el estado del ciclo de vida, y si hay
    una instalacion en curso su dueño lo esta reescribiendo: pisarlo desde el
    lanzador seria la misma carrera que INSTALL-011 acaba de cerrar por el otro
    lado. Si el cerrojo es de otro, **no se escribe**, y no pasa nada: el estado
    degradado tambien se DEDUCE al leerlo, asi que el diagnostico no depende de
    haber podido anotarlo.
    """
    with _cerrojos.CerrojoDeCicloDeVida(root, etiqueta="degradar") as cerrojo:
        if not cerrojo.adquirido:
            return False
        motivo = veredicto.get("error") or "no supero el handshake MCP"
        _estado.registrar_degradacion(root, carpeta=carpeta, motivo=motivo,
                                      fase=veredicto.get("fase"))
        return True


def elegir_runtime_verificado(base: Path | None = None) -> dict[str, Any]:
    """El runtime al que se le puede entregar el stdio del cliente. O ninguno.

    **INSTALL-012.** El lanzador ejecutaba el activo heredandole el stdio del
    cliente y, si moria pronto con codigo distinto de cero, arrancaba N−1 sobre
    esa MISMA conexion, con el argumento de que "no llego a escribir nada". Eso
    no se medía: el hijo escribe directamente en el stdout del cliente, asi que
    el lanzador no ve un solo byte de lo que emite. Un runtime que contesta
    `initialize` y se cae a los dos segundos dejaba al cliente hablando con dos
    servidores en el mismo canal.

    Aqui se verifica ANTES de entregar nada, en un proceso aparte y con
    tuberias propias. Al que salga de esta funcion se le da el stdio del
    cliente, y a partir de ese momento no se arranca nada mas sobre esa
    conexion, pase lo que pase.

    El precio es un arranque de servidor extra por sesion. Se paga a gusto: la
    alternativa era un proxy que intermediara el stdio durante toda la sesion, y
    eso añade un salto de tuberias a cada mensaje, para siempre, en vez de unos
    segundos una vez.
    """
    p = paths(base)
    root = p["root"]
    descartadas: set[str] = set()

    while True:
        seleccion = seleccionar_runtime(base, excluir=descartadas)
        if seleccion["modo"] == "ninguno":
            return seleccion

        py = Path(seleccion["python"])
        sp = paths(root, cache=py.parent.parent.parent)
        veredicto = _salud.verificar(py, env=runtime_env(sp), cwd=root,
                                     timeout=PREFLIGHT_TIMEOUT)
        if veredicto["ok"]:
            seleccion["preflight"] = {"tools": veredicto["tools"],
                                      "servidor": veredicto.get("servidor"),
                                      "version": veredicto.get("version")}
            return seleccion

        _degradar(root, seleccion["carpeta"], veredicto)
        descartadas.add(seleccion["carpeta"])


def _runtime_arrancable(root: Path, registro: dict[str, Any] | None) -> Path | None:
    """Interprete de un registro, o `None`. Evidencia + contencion + disco.

    Las tres condiciones, y ninguna sobra. **La evidencia**, porque que una
    carpeta contenga un `python.exe` no la convierte en un runtime al que
    volver: puede ser una siembra a medias o un venv sin el paquete, y elegirla
    como alternativa cambiaria "la actualizacion fallo" por "ademas rompi lo
    que funcionaba". **La contencion**, porque el nombre sale de un archivo del
    directorio de datos y se valida como el del journal. **El disco**, porque
    un registro puede sobrevivir a la carpeta que describe.
    """
    if not registro:
        return None
    try:
        carpeta = _promocion.bajo_root(Path(root), registro.get("carpeta"),
                                       que="carpeta")
    except _promocion.PromocionError:
        return None
    if carpeta.name.startswith(_promocion.PREFIJO_STAGING):
        return None                       # a medio construir: nunca se sirve
    if not carpeta.is_dir():
        return None
    py = paths(root, cache=carpeta)["python"]
    if not py.is_file():
        return None
    # Estructural, no un handshake: arrancar el servidor en cada inicio del
    # cliente costaria segundos en cada sesion. El oraculo completo -el
    # handshake MCP contra el contrato- se ejecuta al instalar, y `doctor` lo
    # repite a peticion. Aqui solo se descarta lo que es visiblemente inservible.
    if any(not e.exists() for e in _salud.entry_points(py.parent.parent)):
        return None
    return py


def seleccionar_runtime(base: Path | None = None, *,
                        excluir: "set[str] | None" = None) -> dict[str, Any]:
    """Que runtime debe ejecutar el lanzador AHORA.

    Este es el corazon de la correccion. El lanzador comprobaba solo tres
    cosas -status `ready`, version igual a la actual, y que existiera el
    interprete de ESA version- y si alguna fallaba servia el MCP de bootstrap,
    con sus dos tools. O sea que despues de una actualizacion rota, Codex o
    Claude recibian dos tools aunque la version anterior siguiera en disco con
    las 134: el fallback existia en el disco y no existia en el codigo.
    """
    p = paths(base)
    root = p["root"]
    estado = _estado.leer(root)

    try:
        status = _status_crudo(p)
    except (OSError, EstadoStatusCorrupto):                  # pragma: no cover
        status = {}

    # `excluir` lleva las carpetas que el preflight ya rechazo en este mismo
    # arranque. Sin ellas, el segundo intento volveria a elegir exactamente lo
    # mismo: el status sigue diciendo `ready` y la carpeta sigue teniendo su
    # interprete y sus entry points.
    fuera = set(excluir or ())
    # Lo que YA se midio que no arranca no se vuelve a elegir. Sin esto,
    # `sirviendo` seguia apuntando al runtime que `state` acababa de declarar
    # degradado -las dos cosas en la misma respuesta, contradiciendose- porque
    # la comprobacion estructural no puede ver que le falta el paquete. La marca
    # la levanta una instalacion buena, que es la salida real de una degradacion.
    if estado["degradado"]:
        fuera.add(estado["degradado"]["carpeta"])

    def _descartada(registro: dict[str, Any] | None) -> bool:
        return bool(registro and registro.get("carpeta") in fuera)

    # 1. La version que toca, si el status la respalda y arranca.
    if (status.get("ready") and status.get("version") == VERSION
            and p["cache"].name not in fuera):
        py = _runtime_arrancable(root, estado["activo"] or _estado.evidencia(
            p["cache"].name, version=VERSION, servidor="-", tools=1))
        if py is not None and py == p["python"]:
            return {"modo": "activo", "python": str(py),
                    "carpeta": p["cache"].name, "version": VERSION,
                    "evidencia": estado["activo"]}

    # 2. y 3. Lo ultimo que SI supero el handshake. `activo` va primero porque
    # una actualizacion a una version NUEVA que falla no llega a apartar nada:
    # el runtime que estaba sirviendo sigue en su carpeta, con su evidencia, y
    # es el mejor candidato aunque el estado no lo llame todavia N−1.
    for registro in (estado["activo"], estado["last_known_good"]):
        if _descartada(registro):
            continue
        py = _runtime_arrancable(root, registro)
        if py is None:
            continue
        return {"modo": "last-known-good", "python": str(py),
                "carpeta": registro["carpeta"], "version": registro["version"],
                "evidencia": registro}

    return {"modo": "ninguno", "python": None, "carpeta": None,
            "version": None, "evidencia": None}


def _status_crudo(p: dict[str, Path]) -> dict[str, Any]:
    """El archivo tal cual, sin los campos DERIVADOS que añade `read_status`.

    `read_status` calcula `sirviendo`, `last_known_good` y `ultimo_intento` en
    el momento de leer. Persistirlos los convertiria en copias que envejecen: el
    dia que no cuadren con el disco, el archivo diria una cosa y la realidad otra.
    """
    try:
        datos = json.loads(p["status"].read_text(encoding="utf-8"))
    except FileNotFoundError:
        datos = {"state": "not_installed", "ready": False, "version": VERSION}
    except (ValueError, TypeError) as exc:
        raise EstadoStatusCorrupto(
            f"{p['status']} existe pero no contiene JSON valido; se conserva "
            "intacto para diagnostico"
        ) from exc
    if not isinstance(datos, dict):
        raise EstadoStatusCorrupto(
            f"{p['status']} existe pero no contiene un objeto JSON; se conserva "
            "intacto para diagnostico"
        )
    return datos


def _write_status(p: dict[str, Path], **values: Any) -> None:
    p["cache"].mkdir(parents=True, exist_ok=True)
    current = _status_crudo(p)
    current.update(values, version=VERSION, updated=time.time())
    tmp = p["status"].with_name(
        f".{p['status'].name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as archivo:
            archivo.write(json.dumps(current, indent=2, ensure_ascii=False))
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(tmp, p["status"])
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            # No se oculta el resultado publicado por un fallo de limpieza;
            # el nombre unico evita que este vestigio pise otro intento.
            pass


def runtime_env(p: dict[str, Path]) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HORIZUN_PBI_MCP_LIBS_DIR": str(p["libs"]),
        "HORIZUN_PBI_MCP_SCHEMAS_DIR": str(p["schemas"]),
        "HORIZUN_PBI_MCP_REPORT_VALIDATOR_DIR": str(p["validator"]),
        "HORIZUN_PBI_MCP_OUTPUTS_DIR": str(p["outputs"]),
        "HORIZUN_PBI_MCP_BACKUPS_DIR": str(p["backups"]),
        "HORIZUN_PBI_MCP_LOG_FILE": str(p["outputs"] / "horizun-pbi-mcp.log"),
    })
    return env


#: La UNICA implementacion, en el paquete. Aqui solo se reexporta con el nombre
#: de siempre: lo usan `plugin_launcher` y una prueba que vigila que ningun
#: subproceso del instalador se olvide de pasarlo.
flags_sin_ventana = _procesos.sin_ventana





def instalacion_en_curso(base: Path | None = None,
                         status: dict[str, Any] | None = None) -> bool:
    """`installing` solo cuenta si hay alguien instalando de verdad."""
    p = paths(base)
    if _cerrojos.lock_vivo(p["lock"]):
        return True
    status = read_status(base) if status is None else status
    if status.get("state") != "installing":
        return False
    # Ventana de arranque: el lanzador ya marco `installing` y el hijo todavia
    # no ha creado su lock.
    try:
        edad = time.time() - float(status.get("updated") or 0)
    except (TypeError, ValueError):
        return False
    return edad < GRACIA_ARRANQUE


def _versiones_en_disco(root: Path) -> list[Path]:
    """Cache de otras versiones que haya bajo la misma raiz."""
    fuera = {"outputs", "backups", *CACHE}
    try:
        hijos = list(root.iterdir())
    except OSError:
        return []
    reservados = (_promocion.PREFIJO_STAGING, _promocion.PREFIJO_ANTERIOR)
    return [d for d in hijos
            if d.is_dir() and d.name not in fuera
            and not d.name.startswith(reservados)
            and ((d / "install-status.json").is_file() or (d / "runtime").is_dir())]


def _semilla(destino_cache: Path, root: Path, nombre_py: Path) -> str | None:
    """Reaprovecha un runtime ya presente en disco antes de bajar 1 GB otra vez.

    **Copia. No mueve.** Ahi estaba INSTALL-001: la version anterior de esta
    funcion hacia `shutil.move` de `runtime`, `libs`, `schemas` y `validator`
    desde el donante ANTES de validar nada. Si un paso posterior fallaba -pip,
    una descarga, la red-, el estado quedaba en `failed` y el runtime N-1 ya no
    existia: se lo habia llevado la siembra. La persona se quedaba sin
    instalacion anterior a la que volver.

    Copiar cuesta espacio en disco mientras dura la actualizacion, y a cambio el
    runtime anterior sigue entero y arrancable todo el tiempo. Si algo falla no
    hay nada que restaurar, porque no se toco nada.
    """
    candidatos = sorted(_versiones_en_disco(root),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    for donante in [*candidatos, root]:
        if donante == destino_cache or not (donante / "runtime" / nombre_py).is_file():
            continue
        if _promocion.semillar(destino_cache, donante, CACHE):
            return str(donante)
    return None


#: Un lock por combinacion soportada de interprete y plataforma. Lo genera
#: `scripts/generar_lock.py`; sin ellos, `pip install <repo>` resuelve de cero
#: en cada instalacion.
LOCKS = PLUGIN_ROOT / "scripts" / "locks"


def combinacion_de(python: Path) -> tuple[str, str] | None:
    """Version y plataforma **del interprete que va a instalar**, no del actual.

    Preguntarselo al de aqui seria el error entero: el instalador corre con el
    Python anfitrion y crea un venv que puede ser otro. Un lock elegido por la
    version equivocada es peor que ninguno, porque `--require-hashes` falla a
    mitad y la instalacion acaba cayendo al resolutor igual, habiendo perdido el
    tiempo.
    """
    try:
        salida = subprocess.run(
            [str(python), "-c",
             "import sys,sysconfig;"
             "print(f'{sys.version_info.major}.{sys.version_info.minor}');"
             "print(sysconfig.get_platform().replace('-','_').replace('.','_'))"],
            capture_output=True, text=True, timeout=120, check=True,
            **flags_sin_ventana())
    except (OSError, subprocess.SubprocessError):
        return None
    lineas = [l.strip() for l in salida.stdout.splitlines() if l.strip()]
    if len(lineas) != 2:
        return None
    return lineas[0], lineas[1]


def lock_para(python: Path) -> tuple[Path | None, str]:
    """El lock EXACTO de esa combinacion, o `None` y el motivo.

    Coincidencia exacta y nada mas. Un lock de 3.13 aplicado a 3.10 no es «casi
    correcto»: lleva ruedas compiladas para otro ABI -aqui difieren siete
    entradas entre 3.10 y 3.14- y `--require-hashes` lo rechaza entero.
    """
    combinacion = combinacion_de(python)
    if combinacion is None:
        return None, "no se pudo determinar la version del interprete de destino"
    version, plataforma = combinacion
    ruta = LOCKS / f"requirements-py{version.replace('.', '')}-{plataforma}.lock"
    if not ruta.is_file():
        return None, (f"no hay lock para py{version}/{plataforma}: la matriz "
                      f"soportada esta en {LOCKS}")
    return ruta, ""


def _instalar_dependencias(sp: dict[str, Path], env: dict[str, str]) -> dict[str, Any]:
    """Instala las dependencias FIJADAS, y el paquete propio aparte.

    INSTALL-009. `pip install <PLUGIN_ROOT>` resolvia las dependencias de cero
    en cada instalacion: dos maquinas -o la misma en dos semanas- acababan con
    entornos distintos que nadie pidio, y cuando una falla y la otra no, no hay
    forma de saber en que se diferencian.

    Con el lock, las dependencias se instalan con `--require-hashes`: version
    exacta y SHA-256 comprobado. El paquete propio va aparte con `--no-deps`,
    porque es la fuente local y no tiene hash publicado; inventarle uno para que
    la linea quede bonita seria falsificar la garantia.

    **El lock se elige por coincidencia EXACTA de interprete y plataforma.**
    Un lock resuelto en 3.14 no sirve para 3.10: entre esas dos combinaciones
    difieren siete entradas -ruedas compiladas para otro ABI- y
    `--require-hashes` rechaza el archivo entero. Elegir «el mas parecido»
    habria sido peor que no elegir: falla igual, mas tarde y con peor mensaje.

    **Si no hay lock para esta combinacion, cae al resolutor y lo dice.** Fallar
    la instalacion entera por una garantia que no aplica seria peor que la
    garantia; quedarse callado, tambien. El estado registra cual de los dos
    caminos se tomo, y en el segundo dice sin rodeos que esa instalacion **no es
    reproducible**.

    El intento fijado gasta los MISMOS reintentos que gastaria el camino de
    siempre. Con menos, una carrera DNS -que aqui esta medida y es frecuente-
    tumbaria el lock por un motivo que no tiene nada que ver con el lock, y la
    instalacion saldria sin fijar habiendo un lock perfectamente valido.
    """
    lock, motivo = lock_para(sp["python"])
    if lock is not None:
        try:
            _run([str(sp["python"]), "-m", "pip", "install", "--require-hashes",
                  "-r", str(lock)], env=env)
            _run([str(sp["python"]), "-m", "pip", "install", "--no-deps",
                  str(PLUGIN_ROOT)], env=env)
            return {"source": "lock", "lock": str(lock), "reason": None,
                    "note": "Versiones fijadas y verificadas por SHA-256.",
                    "export_extra": _instalar_extra_export(sp, env)}
        except Exception as exc:                             # noqa: BLE001
            motivo = f"{type(exc).__name__}: {exc}"

    _run([str(sp["python"]), "-m", "pip", "install", str(PLUGIN_ROOT)], env=env)
    return {"source": "resolver", "reason": motivo, "lock": None,
            "export_extra": _instalar_extra_export(sp, env),
            "note": ("Esta instalacion NO es reproducible: las dependencias se "
                     "resolvieron en el momento y no estan fijadas por hash. "
                     "Anade la combinacion a la matriz de "
                     "scripts/generar_lock.py y regenera.")}


def _instalar_extra_export(sp: dict[str, Path], env: dict[str, str]) -> dict[str, Any]:
    """Instala `comtypes` si se puede, y si no, lo dice sin tumbar nada.

    Es el extra `export`: solo hace falta para convertir un `.pbip` en `.pbix`
    conduciendo el cuadro de guardado de Power BI Desktop. Todo lo demas -DAX,
    TMDL, PBIR, auditorias- funciona igual sin el, asi que fallar la instalacion
    entera porque no se pudo bajar seria cambiar una capacidad por todas.

    No entra en el lock A PROPOSITO. El lock fija las dependencias declaradas,
    y esta es opcional: meterla ahi la volveria obligatoria en las cinco
    combinaciones de la matriz, incluidas las que no exportan nada.

    Si no queda instalado, `pbi_capabilities` lo dice en `pbix_export` y el
    doctor lo marca como aviso, con la orden exacta para instalarlo.
    """
    if os.name != "nt":
        return {"installed": False, "reason": "solo aplica en Windows",
                "required": False}
    try:
        _run([str(sp["python"]), "-m", "pip", "install", "comtypes>=1.4,<2"],
             env=env)
        return {"installed": True, "reason": None, "required": False}
    except Exception as exc:                                 # noqa: BLE001
        return {"installed": False, "required": False,
                "reason": f"{type(exc).__name__}: {exc}"[:200],
                "impact": ("Solo se pierde `pbi_export_pbix` / "
                           "`pbi_finalize_delivery`. Se instala despues con: "
                           'pip install "horizun-pbi-mcp[export]"')}


def _adoptar_runtime_existente(root: Path) -> dict[str, Any] | None:
    """Le pone evidencia al runtime que ya estaba instalado, comprobandolo.

    El caso: alguien tiene 1.5.4 instalada por una version del instalador
    ANTERIOR a este estado, actualiza a 1.5.5 y la actualizacion falla. Sin
    esto, el estado no tendria ningun `activo`, el lanzador no encontraria
    fallback y le serviria el MCP de bootstrap con dos tools, teniendo en disco
    una instalacion entera y sana. Justo el defecto que se esta corrigiendo,
    reaparecido por la puerta de atras de la migracion.

    Y se hace de la unica forma que vale: **comprobandolo**. No se adopta una
    carpeta por tener un `python.exe` -esa suposicion es el defecto- sino
    ejecutando contra ella el mismo handshake MCP que se le exige a cualquier
    runtime antes de promoverlo. Cuesta unos segundos y ocurre una sola vez,
    porque el resultado queda escrito.
    """
    estado = _estado.leer(root)
    if estado["activo"] or estado["last_known_good"]:
        return None

    candidatos = sorted(_versiones_en_disco(root),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    for carpeta in candidatos:
        sp = paths(root, cache=carpeta)
        if not sp["python"].is_file():
            continue
        salud = _salud.verificar(sp["python"], env=runtime_env(sp), cwd=root)
        if not salud["ok"]:
            continue
        registro = _estado.evidencia(carpeta.name, version=carpeta.name,
                                     servidor=salud.get("servidor") or "",
                                     tools=salud["tools"])
        _estado.escribir(root, dict(estado, activo=registro))
        return registro
    return None


def _borrar(ruta: Path) -> bool:
    try:
        if ruta.is_dir():
            shutil.rmtree(ruta)
        else:
            ruta.unlink()
    except OSError:
        return False          # sigue en uso: no es motivo para fallar nada
    return True


def _carpetas_de_cliente() -> list[Path]:
    """Carpetas que nos asigno el cliente con un nombre que elige el.

    Se miran tambien las hermanas del mismo prefijo porque el sufijo cambia
    solo: en la maquina donde se diagnostico esto convivian
    `horizun-pbi-mcp-horizun` (vacia) y `horizun-pbi-mcp-inline` (1 GB de
    runtime), y la primera se iba a quedar ahi para siempre.
    """
    salida: list[Path] = []
    for nombre in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"):
        valor = os.environ.get(nombre)
        if not valor:
            continue
        actual = Path(valor).expanduser().resolve()
        candidatos = [actual]
        try:
            candidatos += [h for h in actual.parent.iterdir()
                           if h.is_dir() and h.name.startswith("horizun-pbi-mcp")]
        except OSError:
            pass
        salida += [c for c in candidatos if c not in salida]
    return salida


def _es_nuestra(ruta: Path) -> bool:
    """Solo se borra lo que reconocemos como propio (o lo que quedo vacio)."""
    try:
        contenido = list(ruta.iterdir())
    except OSError:
        return False
    if not contenido:
        return True
    status = ruta / "install-status.json"
    if status.is_file():
        try:
            return isinstance(json.loads(status.read_text(encoding="utf-8")), dict)
        except (OSError, ValueError):
            return False
    return (ruta / "runtime").is_dir()


def _rescatar_datos(origen: Path, p: dict[str, Path]) -> None:
    """Lo que genero el usuario no se va con la carpeta que lo alojaba."""
    for clave in ("outputs", "backups"):
        viejo = origen / clave
        if not viejo.is_dir():
            continue
        p[clave].mkdir(parents=True, exist_ok=True)
        try:
            elementos = list(viejo.iterdir())
        except OSError:
            continue
        for item in elementos:
            destino = p[clave] / item.name
            if destino.exists():
                continue                 # ya hay uno con ese nombre: gana el vigente
            try:
                shutil.move(str(item), str(destino))
            except OSError:
                pass


def _orden_de_version(carpeta: Path) -> tuple[tuple[int, ...], float]:
    """Ordena versiones por NUMERO, y solo desempata por fecha.

    Antes se elegia el N−1 a indultar con `max(..., key=st_mtime)`, y dos
    carpetas creadas en el mismo tick del reloj **empatan**: el desempate lo
    decidia el orden de `iterdir`, o sea el sistema de archivos. CI lo destapo
    -verde en 3.13, rojo en 3.10, la misma prueba- y el sintoma era el peor
    posible: se indultaba `0.9.0` en vez de `1.0.0` y se borraba el unico N−1
    utilizable.

    Ampliar el margen -tocar las fechas con mas separacion en la prueba- habria
    escondido el problema sin arreglarlo. N−1 significa **la version anterior**,
    no «la carpeta que se toco mas tarde», asi que se ordena por lo que de
    verdad define el orden y la fecha queda de desempate para nombres que no
    son versiones.
    """
    partes = []
    for trozo in carpeta.name.split("."):
        try:
            partes.append(int(trozo))
        except ValueError:
            partes.append(-1)             # no es una version: al fondo
    try:
        fecha = carpeta.stat().st_mtime
    except OSError:                       # pragma: no cover
        fecha = 0.0
    return tuple(partes), fecha


def _limpiar_huerfanos(p: dict[str, Path]) -> list[str]:
    """Borra lo que ya no puede servirle a nadie.

    Cache de otras versiones, restos del diseño viejo en la raiz y carpetas de
    datos que dejo un nombre anterior del plugin. Nunca toca `outputs` ni
    `backups`: eso es del usuario, y si esta en una carpeta condenada se
    rescata antes.
    """
    borrados: list[str] = []
    viejas = [d for d in _versiones_en_disco(p["root"]) if d != p["cache"]]

    # INSTALL-001: la limpieza no puede dejar la instalacion sin N-1 arrancable.
    #
    # Lo destapo el ensayo real, no las pruebas unitarias, y por un motivo que
    # conviene recordar: aquellas probaban `promover()` aislada y los caminos de
    # FALLO, mientras que esto solo ocurre en el camino de EXITO. Al actualizar
    # desde `1.5.4`, la promocion conservaba como `.previous-` lo que hubiera en
    # el destino -que en una actualizacion de version distinta es una carpeta
    # recien creada con el status y nada mas- y acto seguido esta limpieza
    # borraba `1.5.4`, que era el unico runtime completo que quedaba. Resultado:
    # `ready`, N-1 "conservado" y ni un interprete al que volver.
    #
    # Asi que antes de borrar se comprueba si lo conservado sirve de verdad, y
    # si no sirve se indulta la version vieja mas reciente que si tenga runtime.
    # Ahora el N−1 tiene NOMBRE: el last-known-good del estado. Antes se
    # deducia -"¿hay algun .previous- con una carpeta runtime dentro?"- y esa
    # deduccion es la que fallaba: la promocion conservaba como `.previous-` lo
    # que hubiera en el destino, que al actualizar desde otra version es una
    # carpeta recien creada con el status y nada mas. Contaba como N−1 y no
    # arrancaba nada.
    lkg = _estado.leer(p["root"])["last_known_good"]
    protegidas = set()
    if _runtime_arrancable(p["root"], lkg) is not None:
        protegidas.add((p["root"] / lkg["carpeta"]).resolve())
    else:
        # Sin last-known-good arrancable se indulta la version vieja mas
        # reciente que si tenga runtime, para no quedarse sin nada a lo que
        # volver mientras el estado se reconstruye.
        con_runtime = [d for d in viejas if (d / "runtime").is_dir()]
        if con_runtime:
            protegidas.add(max(con_runtime, key=_orden_de_version).resolve())
    viejas = [d for d in viejas if d.resolve() not in protegidas]

    for viejo in viejas:
        if _borrar(viejo):
            borrados.append(str(viejo))
    for nombre in (*CACHE, "install-status.json", "install.lock", "install.log"):
        resto = p["root"] / nombre
        if resto.exists() and _borrar(resto):
            borrados.append(str(resto))
    borrados += _promocion.limpiar(p["root"], proteger=protegidas)
    for ajena in _carpetas_de_cliente():
        # Nunca una carpeta que contenga a la nuestra, ni al reves.
        if (ajena == p["root"] or p["root"].is_relative_to(ajena)
                or ajena.is_relative_to(p["root"]) or not _es_nuestra(ajena)):
            continue
        _rescatar_datos(ajena, p)
        if _borrar(ajena):
            borrados.append(str(ajena))
    return borrados


#: Version minima de Node que el validador PBIR oficial de Microsoft acepta.
NODE_MINIMO = 20


def version_de_node() -> tuple[int | None, str]:
    """(mayor, crudo). `None` si no hay Node o no se le entiende la respuesta.

    Preguntar la version es barato y no instala nada: `node --version` imprime
    `v20.11.1` y termina. Lo que NO se hace es deducirla de que el ejecutable
    exista, que era el defecto: `shutil.which("node")` dice que hay un Node,
    no que sirva.
    """
    node = shutil.which("node")
    if not node:
        return None, ""
    try:
        salida = subprocess.run([node, "--version"], capture_output=True,
                                text=True, timeout=30,
                                **flags_sin_ventana()).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None, ""
    crudo = salida.lstrip("vV")
    try:
        return int(crudo.split(".")[0]), salida
    except (ValueError, IndexError):
        return None, salida


def preflight_validador(include_validator: bool) -> tuple[bool, str, str]:
    """(se_puede, motivo, version). Decide ANTES de ejecutar nada.

    INSTALL-002: el validador PBIR es OPCIONAL y su ausencia no puede tumbar la
    instalacion. Antes se comprobaba solo que `node` y `npm` existieran, se
    lanzaba la instalacion y, si Node era viejo, `_run` acababa lanzando y el
    `except` general dejaba TODO en `failed` -sin runtime, sin DLL y sin
    esquemas- por un componente que el producto declara prescindible.

    Ahora se decide antes: si el preflight sabe que es incompatible, no se
    ejecuta. Y el motivo se registra, que es lo que permite a `doctor` decir por
    que falta en vez de dejar un hueco mudo.
    """
    if not include_validator:
        return False, "not_requested", ""
    mayor, crudo = version_de_node()
    if mayor is None:
        return False, "skipped_node_unavailable", crudo
    if mayor < NODE_MINIMO:
        return False, "skipped_node_too_old", crudo
    if not shutil.which("npm"):
        return False, "skipped_npm_unavailable", crudo
    return True, "eligible", crudo


def _instalar_validador(sp: dict[str, Path], env: dict[str, str],
                        include_validator: bool, avisar) -> dict[str, Any]:
    """Instala el validador opcional SIN poder tumbar la instalacion.

    Solo `--require-validator` convierte un fallo aqui en fatal, y en ese caso
    la excepcion sube como cualquier otra.
    """
    se_puede, motivo, version = preflight_validador(include_validator)
    if not se_puede:
        return {"state": motivo, "node": version}

    avisar(state="installing", ready=False, step="report-validator",
           message="Instalando el validador PBIR opcional.")
    try:
        _run([str(sp["python"]),
              str(PLUGIN_ROOT / "scripts/fetch_report_validator.py"),
              "--dest", str(sp["validator"])], env=env)
    except Exception as exc:
        if os.environ.get("HORIZUN_PBI_REQUIRE_VALIDATOR") == "1":
            raise
        # Un opcional que falla es un aviso, no una instalacion perdida. El
        # motivo se guarda entero para que `doctor` no tenga que adivinarlo.
        return {"state": "failed_optional", "node": version,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"state": "installed", "node": version}


def _run(command: list[str], *, env: dict[str, str], intentos: int = 3) -> None:
    """Ejecuta un paso de la instalacion, con reintentos si es una descarga.

    Casi todos los pasos que pasan por aqui son descargas (PyPI, NuGet,
    developer.microsoft.com, npm) y el equipo tiene MEDIDA una carrera DNS
    IPv6 que las tumba de forma intermitente. Un fallo transitorio no puede
    costar la instalacion entera: se reintenta con espera creciente, y solo
    el tercer fallo consecutivo es un fallo de verdad. Las descargas se
    verifican por hash y son idempotentes, asi que reintentar es gratis.
    """
    for intento in range(1, intentos + 1):
        try:
            subprocess.run(command, cwd=str(PLUGIN_ROOT), env=env, check=True,
                           **flags_sin_ventana())
            return
        except subprocess.CalledProcessError:
            if intento == intentos:
                raise
            time.sleep(4 * intento)


def install(base: Path | None = None, *, include_validator: bool = True) -> int:
    """Prepara en un staging aparte, verifica, y solo entonces promueve.

    El runtime vigente NO se toca en ningun momento de la preparacion. Si algo
    falla -y falla a menudo: son descargas- el staging se descarta y lo que
    habia sigue exactamente donde estaba, arrancable. Ese es INSTALL-001.
    """
    p = paths(base)
    root = p["root"]
    root.mkdir(parents=True, exist_ok=True)

    with _cerrojos.CerrojoDeCicloDeVida(root, etiqueta="install") as cerrojo:
        if not cerrojo.adquirido:
            # INSTALL-011, punto 6. Antes se escribia aqui un status
            # `installing` con el mensaje "Ya hay una instalación en curso", y
            # ese mensaje PISABA el del instalador que si tiene el cerrojo:
            # borraba su `step`, su avance y su `staging`. El que no pudo
            # entrar no sabe nada que el dueño no sepa mejor, asi que no
            # escribe. `read_status()` devuelve el estado real del dueño, que es
            # justo lo que quiere ver quien pregunte.
            return 0

        # Un status que existe pero no parsea no equivale a "sin instalar".
        # Se valida antes de recuperar promociones, adoptar runtimes o crear un
        # staging: abortar aqui garantiza que el intento no deja ningun efecto.
        try:
            _status_crudo(p)
        except EstadoStatusCorrupto as exc:
            print(f"bootstrap: {exc}", file=sys.stderr)
            return 1

        # INSTALL-011. Esto va DENTRO del cerrojo, y el orden no es un detalle:
        # `recuperar()` renombra el runtime vigente. Hacerlo antes de adquirir
        # el cerrojo -que es lo que se hacia- ponia precisamente la operacion
        # mas destructiva del ciclo de vida fuera de la exclusion mutua, donde
        # podia solaparse con la promocion de otro instalador sobre las mismas
        # carpetas.
        try:
            recuperado = _promocion.recuperar(root)
        except _promocion.PromocionError as exc:
            _write_status(p, state="failed", ready=False,
                          message=f"Promocion interrumpida sin recuperar: {exc}")
            return 1

        adoptado = _adoptar_runtime_existente(root)

        staging = None
        try:
            if sys.version_info < (3, 10):
                raise RuntimeError("Se requiere Python 3.10 o posterior.")

            staging = _promocion.crear_staging(root, VERSION)
            sp = paths(root, cache=staging)
            env = runtime_env(sp)
            _write_status(p, state="installing", ready=False, step="python-runtime",
                          staging=str(staging),
                          message="Creando el entorno aislado (en preparación).")

            nombre_py = p["python"].relative_to(p["runtime"])
            if not sp["python"].is_file():
                heredado = _semilla(staging, root, nombre_py)
                if heredado:
                    _write_status(p, state="installing", ready=False,
                                  step="python-runtime", heredado_de=heredado,
                                  message=f"Reutilizando el runtime de {heredado}.")
            if not sp["python"].is_file():
                # `venv.EnvBuilder(with_pip=True)` lanza ensurepip en un proceso
                # aparte POR SU CUENTA y sin flags: esa era la ultima ventana que
                # quedaba, y la unica que la persona veia titulada con la ruta del
                # runtime. Creandolo nosotros, hereda la consola oculta.
                _run([sys.executable, "-m", "venv", str(sp["runtime"])],
                     env=env, intentos=1)

            _write_status(p, state="installing", ready=False, step="python-packages",
                          message="Instalando el paquete abierto y sus dependencias.")
            _run([str(sp["python"]), "-m", "pip", "install", "--upgrade", "pip",
                  "setuptools"], env=env)
            dependencias = _instalar_dependencias(sp, env)

            _write_status(p, state="installing", ready=False, step="analysis-services",
                          message="Descargando y verificando las DLL de Microsoft.")
            _run([str(sp["python"]), str(PLUGIN_ROOT / "scripts/fetch_libs.py"),
                  "--dest", str(sp["libs"])], env=env)

            _write_status(p, state="installing", ready=False, step="pbir-schemas",
                          message="Descargando y verificando los esquemas PBIR.")
            _run([str(sp["python"]), str(PLUGIN_ROOT / "scripts/fetch_pbir_schemas.py"),
                  "--dest", str(sp["schemas"])], env=env)

            validator = _instalar_validador(sp, env, include_validator,
                                            lambda **kw: _write_status(p, **kw))

            # INSTALL-010. Que ningun paso haya lanzado no demuestra que el
            # servidor ARRANQUE: un venv al que le falte una dependencia
            # transitiva da exactamente el mismo silencio. El oraculo tiene que
            # ser el protocolo, y se ejecuta contra el STAGING para que un
            # runtime que no arranca no llegue a sustituir al que si funciona.
            _write_status(p, state="installing", ready=False, step="healthcheck",
                          message="Comprobando que el runtime preparado arranca.")
            salud = _salud.verificar(sp["python"], env=env, cwd=root,
                                     version_esperada=VERSION)
            if not salud["ok"]:
                raise RuntimeError(
                    f"el runtime preparado no supero el handshake MCP "
                    f"(fase={salud['fase']}): {salud.get('error')}")

            _write_status(p, state="installing", ready=False, step="promotion",
                          message="Publicando el runtime preparado.")
            resultado = _promocion.promover(root, staging, p["cache"])
            staging = None                      # ya no existe: se convirtio en destino

            # La evidencia con la que se promovio, guardada en la RAIZ. De aqui
            # sale el last-known-good que servira el lanzador si la proxima
            # actualizacion se rompe, y por eso lleva QUE se comprobo y no solo
            # que carpeta es.
            apartado = resultado["anterior"]
            _estado.registrar_promocion(
                root,
                nuevo=_estado.evidencia(
                    p["cache"].name, version=VERSION,
                    servidor=salud.get("servidor") or "", tools=salud["tools"]),
                anterior_apartado=Path(apartado).name if apartado else None)

            for key in ("outputs", "backups"):
                p[key].mkdir(parents=True, exist_ok=True)
            _write_status(p, state="ready", ready=True, step="complete",
                          python=str(p["python"]), validator=validator,
                          handshake={"tools": salud["tools"],
                                     "servidor": salud.get("servidor"),
                                     "version": salud.get("version")},
                          anterior_conservado=resultado["anterior"],
                          dependencias=dependencias,
                          recuperacion_previa=recuperado.get("accion"),
                          runtime_adoptado=adoptado,
                          # Un journal rechazado no impide instalar -no se toco
                          # nada de lo que decia-, pero tiene que verse: es la
                          # unica senal de que algo escribio ahi un journal que
                          # este binario no reconoce.
                          recuperacion_motivo=recuperado.get("motivo"),
                          recuperacion_cuarentena=recuperado.get("cuarentena"),
                          limpiado=_limpiar_huerfanos(p),
                          message="Runtime listo. Reinicia Codex o Claude.")
            return 0

        except Exception as exc:
            # El vigente no se toco, asi que "rollback" es tirar el staging.
            descartado = False
            if staging is not None:
                descartado = _borrar(staging)
            # Anotar el fallo NO puede borrar la constancia de lo que si
            # arranca: son dos hechos distintos y tienen que convivir.
            _estado.registrar_fallo(root, version=VERSION,
                                    error=f"{type(exc).__name__}: {exc}")
            seleccion = seleccionar_runtime(base)
            sirve = seleccion["modo"] != "ninguno"
            _write_status(
                p, state="failed", ready=False, staging_descartado=descartado,
                # Dos hechos distintos, dos campos. `runtime_anterior_utilizable`
                # es el literal: la carpeta anterior sigue ahi con su interprete,
                # o sea que la actualizacion no destruyo nada -que es lo que
                # afirma INSTALL-001-. `sirviendo_tras_el_fallo` es el estricto:
                # que se va a EJECUTAR, decidido con la evidencia del handshake.
                # Meter los dos en un campo fue el error original: "hay un
                # python.exe" acabo leyendose como "hay algo que funciona".
                runtime_anterior_utilizable=p["python"].is_file(),
                sirviendo_tras_el_fallo=seleccion["modo"],
                message=(
                    f"{type(exc).__name__}: {exc}. La instalación anterior NO "
                    "se tocó" + (
                        f": se sigue sirviendo {seleccion['version']} "
                        f"({seleccion['modo']}). "
                        if sirve else " y no hay ningún runtime utilizable. ") +
                    # El mensaje anterior decia "Relanzar REANUDA desde este
                    # paso", y no era verdad: el staging se descarta, asi que
                    # relanzar vuelve a empezar -reaprovechando por copia lo
                    # que ya este verificado en disco, que no es lo mismo que
                    # reanudar-. Peor aun, invitaba a reiniciar el cliente una y
                    # otra vez esperando que continuara solo.
                    "Reintentar NO reanuda: descarta lo preparado y empieza de "
                    "nuevo, reaprovechando lo ya verificado en disco. Para "
                    "reintentar, llama a la tool pbi_install_runtime."))
            return 1


#: Lo que es del USUARIO y sobrevive a cualquier desinstalacion salvo que lo
#: pida explicitamente. `outputs` son sus exportaciones e informes; `backups`,
#: los respaldos de SUS proyectos. Borrarlos por defecto al desinstalar seria
#: convertir "quitar el plugin" en "perder tu trabajo".
DATOS_DEL_USUARIO = ("outputs", "backups")


def _pesar(ruta: Path) -> int:
    if ruta.is_file():
        try:
            return ruta.stat().st_size
        except OSError:                                      # pragma: no cover
            return 0
    total = 0
    for hijo in ruta.rglob("*"):
        try:
            if hijo.is_file():
                total += hijo.stat().st_size
        except OSError:                                      # pragma: no cover
            pass
    return total


def inventario(base: Path | None = None) -> dict[str, Any]:
    """Que hay bajo el data root, que es cada cosa y cuanto ocupa.

    **Enumerar antes de borrar** (G4.5). Un `purge` que empieza borrando y
    despues informa no le da a nadie la oportunidad de decir que no, y el data
    root guarda cosas de dos dueños distintos: lo reconstruible, que se puede
    tirar sin coste, y lo del usuario, que no.
    """
    root = paths(base)["root"]
    if not root.is_dir():
        return {"data_dir": str(root), "exists": False, "entries": [],
                "total_bytes": 0, "user_bytes": 0}

    entradas = []
    for hijo in sorted(root.iterdir()):
        if hijo.name in DATOS_DEL_USUARIO:
            clase, tuyo = "datos-del-usuario", True
        elif hijo.name.startswith(_promocion.PREFIJO_ANTERIOR):
            clase, tuyo = "runtime-anterior", False
        elif hijo.name.startswith(_promocion.PREFIJO_STAGING):
            clase, tuyo = "preparacion-a-medias", False
        elif hijo.is_dir() and (hijo / "runtime").is_dir():
            clase, tuyo = "runtime", False
        else:
            clase, tuyo = "estado-o-registro", False
        entradas.append({"name": hijo.name, "kind": clase,
                         "user_data": tuyo, "bytes": _pesar(hijo)})
    return {
        "data_dir": str(root), "exists": True, "entries": entradas,
        "total_bytes": sum(e["bytes"] for e in entradas),
        "user_bytes": sum(e["bytes"] for e in entradas if e["user_data"]),
    }


def desinstalar(base: Path | None = None, *, incluir_datos: bool = False,
                confirmado: bool = False) -> dict[str, Any]:
    """Retira la instalacion. Sin `confirmado`, solo dice lo que haria.

    Nunca sale del data root: se recorren SUS hijos y se borra por nombre, que
    es la misma regla que impide a la promocion escribir fuera (INSTALL-011).

    Se toma el cerrojo del ciclo de vida: borrar mientras otro proceso instala
    dejaria al instalador publicando sobre un directorio que desaparece.
    """
    root = paths(base)["root"]
    plan = inventario(base)
    if not plan["exists"]:
        return {**plan, "removed": [], "kept": [], "confirmed": confirmado,
                "note": "No hay nada instalado en esa ruta."}

    a_borrar = [e for e in plan["entries"]
                if incluir_datos or not e["user_data"]]
    conservar = [e["name"] for e in plan["entries"] if e not in a_borrar]

    if not confirmado:
        return {**plan, "would_remove": [e["name"] for e in a_borrar],
                "kept": conservar, "confirmed": False,
                "freed_bytes": sum(e["bytes"] for e in a_borrar),
                "note": ("Ejecucion en seco: no se ha borrado nada. Repite con "
                         "--confirm para aplicarlo.")}

    with _cerrojos.CerrojoDeCicloDeVida(root, etiqueta="uninstall") as cerrojo:
        if not cerrojo.adquirido:
            return {**plan, "removed": [], "kept": conservar, "confirmed": True,
                    "error": "Hay una instalacion en curso; no se desinstala "
                             "nada mientras otro proceso tiene el cerrojo."}
        borrados = []
        for entrada in a_borrar:
            if _borrar(root / entrada["name"]):
                borrados.append(entrada["name"])

    resto = inventario(base)
    return {**resto, "removed": borrados, "kept": conservar, "confirmed": True,
            "residual_bytes": resto["total_bytes"],
            "note": ("Quedan solo tus datos." if not incluir_datos
                     else "Se retiro todo, incluidos tus datos.")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-validator", action="store_true")
    parser.add_argument("--require-validator", action="store_true",
                        help="convierte en fatal un fallo del validador "
                             "opcional; por defecto solo avisa")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--inventory", action="store_true",
                        help="enumera el directorio de datos con tamaños")
    parser.add_argument("--uninstall", action="store_true",
                        help="retira el runtime y conserva outputs/ y backups/")
    parser.add_argument("--purge", action="store_true",
                        help="como --uninstall pero TAMBIEN tus datos")
    parser.add_argument("--confirm", action="store_true",
                        help="sin esto, --uninstall y --purge solo enumeran")
    args = parser.parse_args()

    if args.status:
        print(json.dumps(read_status(args.data_dir), indent=2, ensure_ascii=False))
        return 0
    if args.inventory:
        print(json.dumps(inventario(args.data_dir), indent=2, ensure_ascii=False))
        return 0
    if args.uninstall or args.purge:
        # El seco es el DEFECTO a proposito: quien escribe `--purge` y ve la
        # lista todavia puede arrepentirse. Pedir la confirmacion aparte
        # convierte un error de dedo en un susto en vez de en una perdida.
        resultado = desinstalar(args.data_dir, incluir_datos=args.purge,
                                confirmado=args.confirm)
        print(json.dumps(resultado, indent=2, ensure_ascii=False))
        return 1 if resultado.get("error") else 0

    if args.require_validator:
        os.environ["HORIZUN_PBI_REQUIRE_VALIDATOR"] = "1"
    return install(args.data_dir, include_validator=not args.no_validator)


if __name__ == "__main__":
    raise SystemExit(main())
