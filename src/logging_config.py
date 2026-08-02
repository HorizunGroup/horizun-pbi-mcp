"""Configuracion de logging para Horizun PBI MCP.

IMPORTANTE: el servidor MCP habla por **stdout** (JSON-RPC sobre stdio).
Por eso TODO el logging va a **stderr** o a un archivo; nunca a stdout,
o se corrompe el protocolo y el cliente (Claude Code) pierde la conexion.

Rotacion en Windows
-------------------
`RotatingFileHandler` renombra `x.log` -> `x.log.1` al rotar. En Windows eso
falla con `WinError 32` si CUALQUIER otro proceso tiene el archivo abierto:
otra sesion del servidor, el cliente de OneDrive sincronizando, o un antivirus
leyendolo. Y falla dentro de `emit()`, no al abrir, asi que el `try/except` de
la apertura no lo cubria: `logging.Handler.handleError` escupia el traceback
por stderr en mitad de una operacion.

Dos medidas, no una:

1. **Un archivo por proceso** (`powerbi_mcp.<pid>.log`). Varios servidores
   arrancando a la vez dejan de pelearse por el mismo nombre, que es la causa
   real de la carrera. La retencion se hace por barrido, no por rename.
2. **Rotacion que no puede tumbar nada**: si el rename falla igualmente
   (OneDrive, antivirus), se sigue escribiendo en el mismo archivo y se cuenta
   el fallo. Se degrada, no se rompe, y nunca imprime un traceback.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

from branding import LOGGER_NAME  # noqa: E402

_CONFIGURED = False

#: Cuantos archivos de log conservar en total (todos los procesos).
MAX_ARCHIVOS_LOG = 12
#: Y cuanto tiempo, en dias.
MAX_DIAS_LOG = 14

_RE_LOG = re.compile(r"^(?P<base>.+?)\.(?P<pid>\d+)\.log(?:\.\d+)?$")


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler que jamas propaga un fallo de rotacion.

    `doRollover` puede fallar en varios puntos (os.remove del `.N`, os.rename
    del base), todos por la misma causa en Windows: otro proceso retiene el
    archivo. Cuando ocurre, lo correcto NO es perder el log ni ensuciar stderr
    con un traceback: es seguir escribiendo donde estabamos y anotarlo.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fallos_rotacion = 0
        self.errores_emision = 0

    def doRollover(self) -> None:                        # noqa: N802 - API stdlib
        try:
            super().doRollover()
        except OSError:
            self.fallos_rotacion += 1
            # super() cierra el stream antes de renombrar; si reventó a medias
            # hay que reabrirlo o el handler queda mudo para siempre.
            if self.stream is None:
                try:
                    self.stream = self._open()
                except OSError:
                    self.stream = None      # se degrada a solo stderr

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802
        """Nunca un traceback por stderr.

        stderr es el canal de diagnostico del servidor MCP y lo lee una
        persona; un traceback de logging ahi parece un fallo del producto.
        """
        self.errores_emision += 1


def ruta_log_de_este_proceso(log_file: str) -> Path:
    """`outputs/powerbi_mcp.log` -> `outputs/powerbi_mcp.<pid>.log`.

    Si el nombre ya trae un pid (reentrada), se respeta.
    """
    p = Path(log_file)
    if _RE_LOG.match(p.name):
        return p
    return p.with_name(f"{p.stem}.{os.getpid()}{p.suffix or '.log'}")


def purgar_logs(directorio: Path, base: str, *,
                max_archivos: int = MAX_ARCHIVOS_LOG,
                max_dias: int = MAX_DIAS_LOG) -> List[Path]:
    """Retencion acotada por barrido, no por rename.

    Con un archivo por proceso, `backupCount` ya no acota nada global: hay que
    barrer los de procesos muertos. Se borran los caducados y, si aun sobran,
    los mas antiguos. Nunca el de este proceso.
    """
    if not directorio.exists():
        return []

    mio = ruta_log_de_este_proceso(str(directorio / f"{base}.log")).name
    candidatos = []
    for f in directorio.glob(f"{base}.*.log*"):
        if f.name == mio or not f.is_file():
            continue
        try:
            candidatos.append((f.stat().st_mtime, f))
        except OSError:                                  # pragma: no cover
            continue

    limite = time.time() - max_dias * 86400
    borrar = [f for mt, f in candidatos if mt < limite]
    quedan = sorted((x for x in candidatos if x[1] not in borrar),
                    key=lambda x: -x[0])
    borrar += [f for _mt, f in quedan[max_archivos:]]

    borrados = []
    for f in borrar:
        try:
            f.unlink()
            borrados.append(f)
        except OSError:
            # Otro proceso lo tiene abierto: no es un error, se intentara luego.
            continue
    return borrados


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> logging.Logger:
    """Configura (una sola vez) el logger raiz del proyecto.

    - Consola -> stderr (para no romper el canal stdio del MCP).
    - Archivo rotativo opcional, uno por proceso.
    """
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    if _CONFIGURED:
        return logger

    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logger.setLevel(lvl)
    logger.propagate = False

    # JSON por defecto (una linea por evento, con redaccion); texto plano si
    # PBI_MCP_LOG_FORMAT=text, util para leerlo a ojo durante el desarrollo.
    from services.telemetry import JsonFormatter, use_json_logging

    if use_json_logging():
        fmt: logging.Formatter = JsonFormatter()
    else:
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    stderr_handler.setLevel(lvl)
    logger.addHandler(stderr_handler)

    if log_file:
        try:
            destino = ruta_log_de_este_proceso(log_file)
            destino.parent.mkdir(parents=True, exist_ok=True)
            file_handler = SafeRotatingFileHandler(
                destino, maxBytes=2_000_000, backupCount=3, encoding="utf-8",
                delay=True,
            )
            file_handler.setFormatter(fmt)
            file_handler.setLevel(lvl)
            logger.addHandler(file_handler)
            purgar_logs(destino.parent, Path(log_file).stem)
        except OSError as exc:
            # El arranque del servidor NO depende de poder escribir el log.
            logger.warning("No se pudo abrir el archivo de log %s: %s", log_file, exc)

    _CONFIGURED = True
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Devuelve un logger hijo del logger del proyecto."""
    if name:
        return logging.getLogger(f"{LOGGER_NAME}.{name}")
    return logging.getLogger(LOGGER_NAME)


def _reset_para_pruebas() -> None:
    """Deshace la configuracion. Solo para pruebas."""
    global _CONFIGURED
    logger = logging.getLogger(LOGGER_NAME)
    for h in list(logger.handlers):
        try:
            h.close()
        except Exception:                                # noqa: BLE001
            pass
        logger.removeHandler(h)
    _CONFIGURED = False
