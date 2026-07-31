"""Fase E3.2 — backend de validacion PBIR de Microsoft.

Que aporta sobre el validador interno
-------------------------------------
`services.pbir_schema` valida **documentos sueltos** contra su JSON Schema. No
puede ver lo que solo se aprecia mirando el informe entero: que un objeto de
formato no exista para ese tipo de visual, que una columna ocupe un rol que
solo admite medidas, que el nombre de un tema no cuadre con el que referencia
`report.json`. Sobre el informe de referencia el CLI oficial encuentra **44
errores y 12 avisos** de esa clase.

Que NO resuelve
---------------
El CLI **tampoco** puede validar `visualContainer/2.10.0`: lo descarga de la
misma URL que devuelve 404 y emite `PBIR_SCHEMA_UNREACHABLE`, saltandose la
validacion de esquema de esos archivos. Es una incompatibilidad **upstream**,
no de este servidor, y por eso E3/G10 siguen abiertos.

Ejecucion offline
-----------------
Por defecto el CLI descarga esquemas por red. Una mutacion no puede depender de
eso, asi que siempre se invoca con `--no-schema`. Medido: en ese modo conserva
los 44 errores semanticos y solo pierde el aviso de esquema inalcanzable —que
ya cubre el validador interno para los esquemas disponibles—.

Seguridad del subproceso
------------------------
Lista de argumentos, `shell=False`, ruta absoluta validada, cwd controlado,
timeout, stdout y stderr capturados por separado y acotados. La salida del CLI
NUNCA se escribe en stdout del proceso: ahi va el JSON-RPC del MCP.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("report_validator")

#: Version exacta del CLI. No se usa `@latest` en operaciones normales.
VERSION_REQUERIDA = "0.1.4"
PAQUETE_NPM = "@microsoft/powerbi-report-authoring-cli"
BINARIO = "powerbi-report-author"

#: Node minimo que declara el paquete (`engines.node`).
NODE_MINIMO = 20

TIMEOUT_SEGUNDOS = 120
#: Tope de salida capturada. Un informe enorme podria emitir megas.
MAX_BYTES_SALIDA = 8 * 1024 * 1024

PASSED = "passed"
PASSED_WITH_WARNINGS = "passed_with_warnings"
FAILED = "failed"
UNAVAILABLE = "unavailable"
TIMEOUT = "timeout"
INCOMPATIBLE_VERSION = "incompatible_version"

#: Niveles de validacion, de menor a mayor cobertura.
NIVEL_NINGUNO = "none"
NIVEL_ESQUEMA = "official_schema"          # solo el validador interno
NIVEL_COMPLETO = "official_schema+report"  # interno + CLI de Microsoft


class ValidatorUnavailable(PowerBIMCPError):
    """Se necesita el validador oficial y no esta disponible."""

    code = "validator_unavailable"


class ReportValidationFailed(PowerBIMCPError):
    """El validador oficial encontro errores NUEVOS introducidos por nosotros."""

    code = "report_validation_failed"


@dataclass
class Diagnostico:
    """Un hallazgo, normalizado para poder compararlo antes y despues.

    NO se compara el mensaje humano: lleva rutas absolutas y texto variable.
    Se comparan codigo, archivo relativo, ruta JSON y severidad.
    """

    code: str
    severity: str
    file: str = ""
    path: str = ""

    def clave(self) -> tuple:
        return (self.code, self.severity, self.file, self.path)

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "severity": self.severity,
                "file": self.file, "path": self.path}


@dataclass
class Resultado:
    status: str
    backend: str = "microsoft-cli"
    validator_name: str = PAQUETE_NPM
    validator_version: str = ""
    level: str = NIVEL_NINGUNO
    errors: int = 0
    warnings: int = 0
    diagnostics: List[Diagnostico] = field(default_factory=list)
    duration_ms: float = 0.0
    detail: str = ""

    def to_envelope(self) -> Dict[str, Any]:
        """Campos ADITIVOS para la respuesta MCP. No cambia la forma existente."""
        return {
            "validation_backend": self.backend,
            "validator_name": self.validator_name,
            "validator_version": self.validator_version,
            "validation_level": self.level,
            "validation_status": self.status,
            "errors": self.errors,
            "warnings": self.warnings,
            "duration_ms": round(self.duration_ms, 1),
            "detail": self.detail,
        }


# ------------------------------------------------------------- localizacion ---
def _ruta_configurada() -> Optional[Path]:
    import branding

    valor = branding.env("REPORT_VALIDATOR")
    return Path(valor) if valor else None


def cli_dir() -> Path:
    """Donde `scripts/fetch_report_validator.py` instala el CLI."""
    import branding

    personalizado = branding.env("REPORT_VALIDATOR_DIR")
    if personalizado:
        return Path(personalizado)
    en_repo = Path(__file__).resolve().parents[2] / "validator_cache"
    if en_repo.exists():
        return en_repo
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.cache")
    return Path(base) / "horizun-pbi-mcp" / "validator"


def localizar() -> Optional[Path]:
    """Ruta ABSOLUTA del `cli.js`, o None. Nunca se busca en el PATH.

    Buscar en el PATH permitiria que un ejecutable ajeno con ese nombre
    acabara procesando el proyecto del usuario.
    """
    directa = _ruta_configurada()
    if directa and directa.is_file():
        return directa.resolve()

    candidato = (cli_dir() / "node_modules" / "@microsoft" /
                 "powerbi-report-authoring-cli" / "dist" / "cli.js")
    return candidato.resolve() if candidato.is_file() else None


def _node() -> Optional[str]:
    import shutil

    return shutil.which("node")


def _version_node() -> Optional[int]:
    node = _node()
    if not node:
        return None
    try:
        r = subprocess.run([node, "--version"], capture_output=True, text=True,
                           timeout=20, shell=False)
        return int(r.stdout.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):  # pragma: no cover
        return None


def estado() -> Dict[str, Any]:
    """Diagnostico del backend. No lanza: lo usan doctor y capabilities."""
    node = _node()
    mayor = _version_node() if node else None
    cli = localizar()

    disponible = bool(node and cli and mayor and mayor >= NODE_MINIMO)
    version = ""
    compatible = False
    if disponible:
        version = _version_cli(cli) or ""
        compatible = version == VERSION_REQUERIDA

    if not node:
        motivo = "Node no esta instalado (el CLI oficial lo necesita)"
    elif mayor is not None and mayor < NODE_MINIMO:
        motivo = f"Node {mayor} < {NODE_MINIMO}, que es lo que exige el paquete"
    elif not cli:
        motivo = "el CLI oficial no esta instalado"
    elif not compatible:
        motivo = (f"version instalada {version!r}, se esperaba "
                  f"{VERSION_REQUERIDA!r}")
    else:
        motivo = "disponible"

    return {"available": disponible and compatible, "node": bool(node),
            "node_major": mayor, "cli_path": str(cli) if cli else None,
            "version": version, "expected_version": VERSION_REQUERIDA,
            "compatible": compatible, "reason": motivo,
            "install_hint": "python scripts/fetch_report_validator.py"}


def _version_cli(cli: Path) -> Optional[str]:
    node = _node()
    if not node:
        return None
    try:
        r = subprocess.run([node, str(cli), "--version"], capture_output=True,
                           text=True, timeout=60, shell=False,
                           cwd=str(cli.parent))
        return r.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):        # pragma: no cover
        return None


# -------------------------------------------------------------- ejecucion ----
def _normalizar(datos: Dict[str, Any], raiz: Path) -> List[Diagnostico]:
    """Diagnosticos del CLI -> forma comparable, sin rutas personales."""
    salida: List[Diagnostico] = []
    for codigo, info in (datos.get("diagnostics") or {}).items():
        severidad = str(info.get("severity", "error"))
        for item in info.get("items", []) or []:
            bruto = str(item.get("file") or "")
            try:
                relativo = str(Path(bruto).resolve().relative_to(raiz.resolve()))
            except (ValueError, OSError):
                relativo = Path(bruto).name if bruto else ""
            salida.append(Diagnostico(
                code=str(codigo), severity=severidad,
                file=relativo.replace("\\", "/"),
                path=str(item.get("jsonPath") or item.get("path") or "")))
    return salida


def validar_informe(report_dir: Path, *, timeout: int = TIMEOUT_SEGUNDOS,
                    ) -> Resultado:
    """Ejecuta el CLI oficial sobre un `.Report`. Siempre offline."""
    inicio = time.perf_counter()
    est = estado()
    if not est["available"]:
        return Resultado(status=UNAVAILABLE, level=NIVEL_NINGUNO,
                         validator_version=est["version"], detail=est["reason"],
                         duration_ms=(time.perf_counter() - inicio) * 1000)

    raiz = Path(report_dir).resolve()
    if not raiz.is_dir():
        raise ValidatorUnavailable(
            f"No existe el directorio de informe {raiz}",
            details={"report_dir": str(raiz)})

    cli = Path(est["cli_path"])
    salida_json = raiz.parent / f".hz_validate_{os.getpid()}.json"
    # Lista de argumentos, sin shell. `--no-schema`: una mutacion no puede
    # depender de que haya red.
    argumentos = [_node(), str(cli), "validate", str(raiz),
                  "--format", "json", "--no-schema",
                  "--out", str(salida_json)]

    try:
        proc = subprocess.run(
            argumentos, capture_output=True, shell=False, timeout=timeout,
            cwd=str(raiz.parent), text=False)
    except subprocess.TimeoutExpired:
        salida_json.unlink(missing_ok=True)
        return Resultado(status=TIMEOUT, level=NIVEL_NINGUNO,
                         validator_version=est["version"],
                         detail=f"el validador oficial no respondio en {timeout}s",
                         duration_ms=(time.perf_counter() - inicio) * 1000)
    except OSError as exc:
        salida_json.unlink(missing_ok=True)
        return Resultado(status=UNAVAILABLE, level=NIVEL_NINGUNO,
                         detail=f"no se pudo ejecutar el validador: {exc}",
                         duration_ms=(time.perf_counter() - inicio) * 1000)

    try:
        datos = _leer_salida(salida_json, proc)
    finally:
        salida_json.unlink(missing_ok=True)

    if datos is None:
        from services import redaction

        stderr = (proc.stderr or b"")[:2000].decode("utf-8", "replace")
        return Resultado(
            status=UNAVAILABLE, level=NIVEL_NINGUNO,
            validator_version=est["version"],
            detail=("el validador oficial no devolvio JSON interpretable: "
                    + redaction.texto(stderr)),
            duration_ms=(time.perf_counter() - inicio) * 1000)

    diags = _normalizar(datos, raiz)
    errores = sum(1 for d in diags if d.severity == "error")
    avisos = sum(1 for d in diags if d.severity != "error")
    # `result` del CLI se usa como referencia, pero el recuento manda: el
    # codigo de salida del proceso es 0 incluso con result="failed".
    if errores:
        estado_final = FAILED
    elif avisos:
        estado_final = PASSED_WITH_WARNINGS
    else:
        estado_final = PASSED

    return Resultado(
        status=estado_final, level=NIVEL_COMPLETO,
        validator_version=est["version"], errors=errores, warnings=avisos,
        diagnostics=diags,
        duration_ms=(time.perf_counter() - inicio) * 1000,
        detail=str(datos.get("result", "")))


def _leer_salida(salida_json: Path, proc) -> Optional[Dict[str, Any]]:
    """Envelope COMPLETO del CLI, leido de `--out`. None si no sirve.

    Se lee SOLO de `--out`. Con `--out`, el CLI escribe ahi el envelope entero y
    en stdout deja unicamente un RESUMEN sin `diagnostics`. Caer a stdout cuando
    el archivo falla daria cero diagnosticos y, por tanto, un `passed` limpio:
    un fallback silencioso a "todo bien" justo cuando algo ha ido mal.
    """
    try:
        if not salida_json.exists():
            return None
        if salida_json.stat().st_size > MAX_BYTES_SALIDA:
            log.warning("La salida del validador supera %d bytes; se descarta.",
                        MAX_BYTES_SALIDA)
            return None
        datos = json.loads(salida_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(datos, dict):
        return None
    cuerpo = datos.get("data", datos)
    if not isinstance(cuerpo, dict):
        return None
    # Un envelope sin recuento ni diagnosticos no es un resultado de validacion:
    # es un resumen, y tratarlo como tal reportaria cero errores.
    if "diagnostics" not in cuerpo and "errorCount" not in cuerpo:
        return None
    return cuerpo


# -------------------------------------------------- diagnosticos preexistentes ---
def comparar(antes: List[Diagnostico],
             despues: List[Diagnostico]) -> Dict[str, Any]:
    """Que diagnosticos son NUESTROS y cuales ya estaban.

    El informe de referencia trae 44 errores propios. Atribuirlos a la
    operacion seria falso; ignorar los nuevos, peligroso. Se comparan claves
    normalizadas (codigo, severidad, archivo relativo, ruta JSON), nunca el
    mensaje humano: lleva rutas absolutas y texto variable.
    """
    conteo_antes: Dict[tuple, int] = {}
    for d in antes:
        conteo_antes[d.clave()] = conteo_antes.get(d.clave(), 0) + 1

    nuevos: List[Diagnostico] = []
    restante = dict(conteo_antes)
    for d in despues:
        k = d.clave()
        if restante.get(k):
            restante[k] -= 1
        else:
            nuevos.append(d)

    resueltos = sum(restante.values())
    errores_antes = sum(1 for d in antes if d.severity == "error")
    errores_despues = sum(1 for d in despues if d.severity == "error")

    # Un diagnostico "nuevo" puede ser de dos clases muy distintas, y
    # confundirlas hace que el mensaje culpe a quien no debe:
    #
    #  - INTRODUCIDO: su combinacion codigo+severidad no existia antes. Lo
    #    causamos nosotros.
    #  - PROPAGADO: ese mismo defecto ya estaba en el informe, en otro archivo.
    #    Es lo que pasa al duplicar una pagina que ya venia con errores: se
    #    copian fielmente y reaparecen en rutas nuevas.
    #
    # Las dos bloquean —el informe acaba con mas errores que antes—, pero el
    # usuario merece saber cual de las dos esta viendo.
    clases_previas = {(d.code, d.severity) for d in antes}
    introducidos = [d for d in nuevos if (d.code, d.severity) not in clases_previas]
    propagados = [d for d in nuevos if (d.code, d.severity) in clases_previas]

    return {
        "preexisting_diagnostics": len(antes),
        "new_diagnostics": [d.to_dict() for d in nuevos],
        "new_error_count": sum(1 for d in nuevos if d.severity == "error"),
        "introduced_diagnostics": [d.to_dict() for d in introducidos],
        "introduced_error_count": sum(1 for d in introducidos
                                      if d.severity == "error"),
        "propagated_diagnostics": [d.to_dict() for d in propagados],
        "propagated_error_count": sum(1 for d in propagados
                                      if d.severity == "error"),
        "resolved_count": resueltos,
        "errors_before": errores_antes,
        "errors_after": errores_despues,
        # Cualquiera de las tres bloquea: error nuevo, mas errores que antes, o
        # el mismo numero pero en otro sitio (lo capta `new_diagnostics`).
        "blocks": bool([d for d in nuevos if d.severity == "error"])
                  or errores_despues > errores_antes,
    }
