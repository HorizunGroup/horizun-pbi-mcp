"""Cerrojo interproceso del ciclo de vida (INSTALL-001, punto 11).

El lock que habia vivia en `<raiz>/<VERSION>/install.lock`, y esa carpeta es
justo la que la promocion renombra. Un cerrojo dentro de lo que se esta moviendo
no protege el movimiento: dos instaladores concurrentes podian estar cada uno en
su carpeta creyendo que tenian el paso libre, y colisionar al promover sobre el
mismo destino.

El del ciclo de vida vive en la RAIZ, que no se mueve nunca.

Se roba si quedo huerfano. Apagar el equipo a mitad de una instalacion dejaba un
lock sin dueño y el instalador siguiente se rendia al verlo: el estado se
congelaba en `installing` para siempre. Por eso el lock dice QUIEN lo tiene y se
comprueba que ese proceso siga vivo, en vez de confiar en que el archivo exista.

Solo biblioteca estandar: esto corre con el Python anfitrion, antes de que
exista ningun entorno.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

NOMBRE = "lifecycle.lock"


def proceso_vivo(pid: int) -> bool:
    """¿Ese PID sigue existiendo?

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


def lock_vivo(lock: Path) -> bool:
    """Un lock ilegible o sin PID no acredita a nadie."""
    try:
        pid = int(json.loads(lock.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return proceso_vivo(pid)


class CerrojoDeCicloDeVida:
    """Context manager. `adquirido` dice si se obtuvo, sin lanzar.

    No lanza a proposito: que otro proceso este instalando no es un error, es
    una respuesta. Quien llama decide si espera, informa o se va.
    """

    def __init__(self, root: Path, *, etiqueta: str = "setup") -> None:
        self.root = Path(root)
        self.ruta = self.root / NOMBRE
        self.etiqueta = etiqueta
        self.adquirido = False

    def __enter__(self) -> "CerrojoDeCicloDeVida":
        self.root.mkdir(parents=True, exist_ok=True)
        for ultimo_intento in (False, True):
            try:
                fd = os.open(self.ruta, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if ultimo_intento or lock_vivo(self.ruta):
                    return self
                try:
                    self.ruta.unlink()
                except OSError:
                    return self
                continue
            except OSError:
                return self
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"pid": os.getpid(), "started": time.time(),
                           "etiqueta": self.etiqueta}, fh)
            self.adquirido = True
            return self
        return self

    def __exit__(self, *_exc) -> None:
        if not self.adquirido:
            return
        try:
            self.ruta.unlink()
        except FileNotFoundError:
            pass

    def duenno(self) -> dict | None:
        try:
            return json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
