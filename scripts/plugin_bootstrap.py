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

VERSION = "1.5.5"
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
        "log": cache / "install.log",
        "libs": cache / "libs",
        "schemas": cache / "schemas" / "pbir",
        "validator": cache / "validator",
        # Lo del usuario NO se versiona: sus exportaciones y sus respaldos
        # sobreviven a cada actualizacion, y siempre en el mismo sitio.
        "outputs": root / "outputs",
        "backups": root / "backups",
    }


def read_status(base: Path | None = None) -> dict[str, Any]:
    p = paths(base)
    try:
        result = json.loads(p["status"].read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        result = {"state": "not_installed", "ready": False, "version": VERSION}
    result["data_dir"] = str(p["root"])
    result["runtime_dir"] = str(p["cache"])
    result["log"] = str(p["log"])
    return result


def _write_status(p: dict[str, Path], **values: Any) -> None:
    p["cache"].mkdir(parents=True, exist_ok=True)
    current = read_status(p["root"])
    current.update(values, version=VERSION, updated=time.time())
    tmp = p["status"].with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p["status"])


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


def flags_sin_ventana() -> dict[str, Any]:
    """Opciones de `subprocess` para que el hijo NO estrene consola.

    El instalador corre con DETACHED_PROCESS, o sea SIN consola propia. Cuando
    un proceso sin consola arranca una aplicacion de consola, Windows le crea
    una VISIBLE al hijo salvo que ESE CreateProcess pida lo contrario: el flag
    del padre no se hereda. Por eso cada `pip`, cada `npm` y cada descarga
    aparecian en pantalla al abrir Claude. Redirigir stdout no evita nada: la
    consola se asigna igual, tenga o no donde escribir.

    Todo subproceso de la instalacion pasa por aqui; el que se olvide, vuelve a
    abrir la ventana.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}





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
    hay_n1 = any((d / "runtime").is_dir() for d in _promocion.anteriores(p["root"]))
    if not hay_n1:
        con_runtime = [d for d in viejas if (d / "runtime").is_dir()]
        if con_runtime:
            reserva = max(con_runtime, key=lambda d: d.stat().st_mtime)
            viejas = [d for d in viejas if d != reserva]

    for viejo in viejas:
        if _borrar(viejo):
            borrados.append(str(viejo))
    for nombre in (*CACHE, "install-status.json", "install.lock", "install.log"):
        resto = p["root"] / nombre
        if resto.exists() and _borrar(resto):
            borrados.append(str(resto))
    borrados += _promocion.limpiar(p["root"])
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

    # Antes de nada: si una promocion quedo a medias (corte de luz entre los
    # dos renombrados), se completa o se deshace mirando el disco.
    try:
        recuperado = _promocion.recuperar(root)
    except _promocion.PromocionError as exc:
        _write_status(p, state="failed", ready=False,
                      message=f"Promocion interrumpida sin recuperar: {exc}")
        return 1

    with _cerrojos.CerrojoDeCicloDeVida(root, etiqueta="install") as cerrojo:
        if not cerrojo.adquirido:
            _write_status(p, state="installing", ready=False,
                          message="Ya hay una instalación en curso.")
            return 0

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
            _run([str(sp["python"]), "-m", "pip", "install", str(PLUGIN_ROOT)], env=env)

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
            salud = _salud.verificar(sp["python"], env=env, cwd=root)
            if not salud["ok"]:
                raise RuntimeError(
                    f"el runtime preparado no supero el handshake MCP "
                    f"(fase={salud['fase']}): {salud.get('error')}")

            _write_status(p, state="installing", ready=False, step="promotion",
                          message="Publicando el runtime preparado.")
            resultado = _promocion.promover(root, staging, p["cache"])
            staging = None                      # ya no existe: se convirtio en destino

            for key in ("outputs", "backups"):
                p[key].mkdir(parents=True, exist_ok=True)
            _write_status(p, state="ready", ready=True, step="complete",
                          python=str(p["python"]), validator=validator,
                          handshake={"tools": salud["tools"],
                                     "servidor": salud.get("servidor"),
                                     "version": salud.get("version")},
                          anterior_conservado=resultado["anterior"],
                          recuperacion_previa=recuperado.get("accion"),
                          limpiado=_limpiar_huerfanos(p),
                          message="Runtime listo. Reinicia Codex o Claude.")
            return 0

        except Exception as exc:
            # El vigente no se toco, asi que "rollback" es tirar el staging.
            descartado = False
            if staging is not None:
                descartado = _borrar(staging)
            anterior_utilizable = p["python"].is_file()
            _write_status(
                p, state="failed", ready=False, staging_descartado=descartado,
                runtime_anterior_utilizable=anterior_utilizable,
                message=(f"{type(exc).__name__}: {exc}. La instalación anterior "
                         "NO se tocó: sigue utilizable. Relanzar REANUDA desde "
                         "este paso; lo ya descargado está verificado por hash."))
            return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-validator", action="store_true")
    parser.add_argument("--require-validator", action="store_true",
                        help="convierte en fatal un fallo del validador "
                             "opcional; por defecto solo avisa")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(read_status(args.data_dir), indent=2, ensure_ascii=False))
        return 0
    if args.require_validator:
        os.environ["HORIZUN_PBI_REQUIRE_VALIDATOR"] = "1"
    return install(args.data_dir, include_validator=not args.no_validator)


if __name__ == "__main__":
    raise SystemExit(main())
