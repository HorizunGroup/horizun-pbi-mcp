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
import venv
from pathlib import Path
from typing import Any

VERSION = "1.4.0"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    for name in ("HORIZUN_PBI_PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"):
        value = os.environ.get(name)
        if value:
            return Path(value).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return (Path(base) / "HorizunPbiMcp" / "plugin").resolve()


def paths(base: Path | None = None) -> dict[str, Path]:
    root = base or data_dir()
    runtime = root / "runtime"
    py = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return {
        "root": root,
        "runtime": runtime,
        "python": py,
        "status": root / "install-status.json",
        "lock": root / "install.lock",
        "log": root / "install.log",
        "libs": root / "libs",
        "schemas": root / "schemas" / "pbir",
        "validator": root / "validator",
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
    result["log"] = str(p["log"])
    return result


def _write_status(p: dict[str, Path], **values: Any) -> None:
    p["root"].mkdir(parents=True, exist_ok=True)
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


def _run(command: list[str], *, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=str(PLUGIN_ROOT), env=env, check=True)


def install(base: Path | None = None, *, include_validator: bool = True) -> int:
    p = paths(base)
    p["root"].mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(p["lock"], os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        _write_status(p, state="installing", ready=False,
                      message="Ya hay una instalación en curso.")
        return 0

    try:
        if sys.version_info < (3, 10):
            raise RuntimeError("Se requiere Python 3.10 o posterior.")
        _write_status(p, state="installing", ready=False, step="python-runtime",
                      message="Creando el entorno aislado.")
        if not p["python"].is_file():
            venv.EnvBuilder(with_pip=True, clear=False).create(p["runtime"])

        env = runtime_env(p)
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
                      message="Runtime listo. Reinicia Codex o Claude.")
        return 0
    except Exception as exc:
        _write_status(p, state="failed", ready=False,
                      message=f"{type(exc).__name__}: {exc}")
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
