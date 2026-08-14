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


def paths(base: Path | None = None) -> dict[str, Path]:
    root = base or data_dir()
    cache = root / VERSION
    runtime = cache / "runtime"
    py = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return {
        "root": root,
        "cache": cache,
        "runtime": runtime,
        "python": py,
        "status": cache / "install-status.json",
        "lock": cache / "install.lock",
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


def _proceso_vivo(pid: int) -> bool:
    """¿Ese PID sigue existiendo? Solo biblioteca estandar.

    En Windows `os.kill(pid, 0)` no pregunta nada: TERMINA el proceso. Aqui se
    abre un handle de solo sincronizacion y se consulta si ya acabo.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                      # existe; es de otro usuario
        return True

    import ctypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _lock_vivo(lock: Path) -> bool:
    """¿Hay un instalador de verdad detras de este lock?

    Un lock ilegible o sin PID no acredita a nadie: es el formato anterior, o
    un archivo a medio escribir.
    """
    try:
        pid = int(json.loads(lock.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return _proceso_vivo(pid)


def _tomar_lock(p: dict[str, Path]) -> bool:
    """Toma el lock de instalacion, robandolo si quedo huerfano.

    Apagar el PC a mitad de la instalacion dejaba un `install.lock` sin dueño:
    el instalador siguiente se rendia al verlo y el estado quedaba congelado en
    `installing` PARA SIEMPRE, porque el lanzador solo reintenta cuando ve
    `not_installed` o una version distinta. Ahora el lock dice quien lo tiene y
    se comprueba que ese proceso siga vivo.
    """
    for ultimo_intento in (False, True):
        try:
            fd = os.open(p["lock"], os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if ultimo_intento or _lock_vivo(p["lock"]):
                return False
            try:
                p["lock"].unlink()
            except OSError:
                return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": time.time(),
                       "version": VERSION}, fh)
        return True
    return False


def instalacion_en_curso(base: Path | None = None,
                         status: dict[str, Any] | None = None) -> bool:
    """`installing` solo cuenta si hay alguien instalando de verdad."""
    p = paths(base)
    if _lock_vivo(p["lock"]):
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
    return [d for d in hijos
            if d.is_dir() and d.name not in fuera
            and ((d / "install-status.json").is_file() or (d / "runtime").is_dir())]


def _semilla(p: dict[str, Path]) -> str | None:
    """Reaprovecha un runtime ya presente en disco antes de bajar 1 GB otra vez.

    Cubre la version anterior (`<raiz>/1.5.4`) y el diseño viejo, que dejaba el
    runtime suelto en la raiz. Todo lo que se mueve esta verificado por hash:
    pip actualiza el paquete dentro del venv y los descargadores saltan lo que
    ya coincide, asi que una actualizacion no vuelve a bajar las DLL de
    Microsoft, los esquemas PBIR ni el validador npm.
    """
    nombre_py = p["python"].relative_to(p["runtime"])
    candidatos = sorted(_versiones_en_disco(p["root"]),
                        key=lambda d: d.stat().st_mtime, reverse=True)
    for donante in [*candidatos, p["root"]]:
        if donante == p["cache"] or not (donante / "runtime" / nombre_py).is_file():
            continue
        p["cache"].mkdir(parents=True, exist_ok=True)
        movidos = 0
        for nombre in CACHE:
            origen, destino = donante / nombre, p["cache"] / nombre
            if not origen.exists() or destino.exists():
                continue
            try:
                shutil.move(str(origen), str(destino))
                movidos += 1
            except OSError:
                pass
        if movidos:
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
    for viejo in _versiones_en_disco(p["root"]):
        if viejo != p["cache"] and _borrar(viejo):
            borrados.append(str(viejo))
    for nombre in (*CACHE, "install-status.json", "install.lock", "install.log"):
        resto = p["root"] / nombre
        if resto.exists() and _borrar(resto):
            borrados.append(str(resto))
    for ajena in _carpetas_de_cliente():
        # Nunca una carpeta que contenga a la nuestra, ni al reves.
        if (ajena == p["root"] or p["root"].is_relative_to(ajena)
                or ajena.is_relative_to(p["root"]) or not _es_nuestra(ajena)):
            continue
        _rescatar_datos(ajena, p)
        if _borrar(ajena):
            borrados.append(str(ajena))
    return borrados


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
    p = paths(base)
    p["cache"].mkdir(parents=True, exist_ok=True)
    if not _tomar_lock(p):
        _write_status(p, state="installing", ready=False,
                      message="Ya hay una instalación en curso.")
        return 0

    try:
        if sys.version_info < (3, 10):
            raise RuntimeError("Se requiere Python 3.10 o posterior.")
        _write_status(p, state="installing", ready=False, step="python-runtime",
                      message="Creando el entorno aislado.")
        env = runtime_env(p)
        if not p["python"].is_file():
            heredado = _semilla(p)
            if heredado:
                _write_status(p, state="installing", ready=False,
                              step="python-runtime", heredado_de=heredado,
                              message=f"Reutilizando el runtime de {heredado}.")
        if not p["python"].is_file():
            # `venv.EnvBuilder(with_pip=True)` lanza ensurepip en un proceso
            # aparte POR SU CUENTA y sin flags: esa era la ultima ventana que
            # quedaba, y la unica que la persona veia titulada con la ruta del
            # runtime. Creandolo nosotros, hereda la consola oculta.
            _run([sys.executable, "-m", "venv", str(p["runtime"])],
                 env=env, intentos=1)

        _write_status(p, state="installing", ready=False, step="python-packages",
                      message="Instalando el paquete abierto y sus dependencias.")
        _run([str(p["python"]), "-m", "pip", "install", "--upgrade", "pip", "setuptools"], env=env)
        _run([str(p["python"]), "-m", "pip", "install", str(PLUGIN_ROOT)], env=env)

        _write_status(p, state="installing", ready=False, step="analysis-services",
                      message="Descargando y verificando las DLL de Microsoft.")
        _run([str(p["python"]), str(PLUGIN_ROOT / "scripts/fetch_libs.py"),
              "--dest", str(p["libs"])], env=env)

        _write_status(p, state="installing", ready=False, step="pbir-schemas",
                      message="Descargando y verificando los esquemas PBIR.")
        _run([str(p["python"]), str(PLUGIN_ROOT / "scripts/fetch_pbir_schemas.py"),
              "--dest", str(p["schemas"])], env=env)

        validator = "not_requested"
        if include_validator and shutil.which("node") and shutil.which("npm"):
            _write_status(p, state="installing", ready=False, step="report-validator",
                          message="Instalando el validador PBIR opcional.")
            _run([str(p["python"]), str(PLUGIN_ROOT / "scripts/fetch_report_validator.py"),
                  "--dest", str(p["validator"])], env=env)
            validator = "installed"
        elif include_validator:
            validator = "skipped_node_unavailable"

        for key in ("outputs", "backups"):
            p[key].mkdir(parents=True, exist_ok=True)
        _write_status(p, state="ready", ready=True, step="complete",
                      python=str(p["python"]), validator=validator,
                      limpiado=_limpiar_huerfanos(p),
                      message="Runtime listo. Reinicia Codex o Claude.")
        return 0
    except Exception as exc:
        _write_status(
            p, state="failed", ready=False,
            message=(f"{type(exc).__name__}: {exc}. Relanzar la instalacion "
                     "REANUDA desde este paso: lo ya descargado esta "
                     "verificado por hash y no se repite."))
        return 1
    finally:
        try:
            p["lock"].unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-validator", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    if args.status:
        print(json.dumps(read_status(args.data_dir), indent=2, ensure_ascii=False))
        return 0
    return install(args.data_dir, include_validator=not args.no_validator)


if __name__ == "__main__":
    raise SystemExit(main())
