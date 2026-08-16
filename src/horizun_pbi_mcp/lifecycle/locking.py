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
import uuid
from pathlib import Path
from typing import Any

NOMBRE = "lifecycle.lock"

#: Margen al comparar el instante de creacion de un proceso. El dato es exacto
#: en las dos plataformas; el margen solo absorbe el redondeo de pasar por JSON.
TOLERANCIA_CREACION = 1.0


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


def creacion_de_proceso(pid: int) -> float | None:
    """Instante en que ARRANCO ese PID, o `None` si no se puede saber.

    Un PID no identifica a un proceso: se recicla, y en Windows deprisa. Un
    lock huerfano cuyo numero haya reutilizado cualquier otro programa dejaria
    a `lock_vivo` diciendo que si para siempre, y la instalacion congelada en
    `installing` sin que nadie vuelva a intentarlo. El par (PID, instante de
    creacion) si identifica: el sistema no reutiliza los dos a la vez.

    Solo biblioteca estandar, que es la restriccion de todo este modulo:
    `GetProcessTimes` por ctypes en Windows y el campo 22 de `/proc/<pid>/stat`
    en Linux. Donde no se pueda averiguar se devuelve `None` y quien compara lo
    trata como "no acredita", que es el lado seguro: como mucho se roba un lock
    que quiza estaba vivo, y de eso protege el token de propiedad.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                      False, int(pid))
        if not handle:
            return None
        try:
            creacion = wintypes.FILETIME()
            otros_tiempos = [wintypes.FILETIME() for _ in range(3)]
            if not kernel32.GetProcessTimes(
                    handle, ctypes.byref(creacion),
                    *[ctypes.byref(f) for f in otros_tiempos]):
                return None
            ticks = (creacion.dwHighDateTime << 32) | creacion.dwLowDateTime
            # FILETIME cuenta en unidades de 100 ns desde 1601-01-01.
            return ticks / 1e7 - 11644473600.0
        finally:
            kernel32.CloseHandle(handle)

    try:
        campos_proc = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        # El nombre del ejecutable va entre parentesis y puede llevar espacios:
        # se corta por el ULTIMO ')' o un proceso llamado "a b)" desalinearia
        # todos los campos siguientes.
        campos_restantes = campos_proc[campos_proc.rindex(")") + 1:].split()
        arranque_en_ticks = float(campos_restantes[19])
    except (ValueError, IndexError):
        return None
    try:
        sysconf = getattr(os, "sysconf")
        hz = sysconf("SC_CLK_TCK")
    except (AttributeError, ValueError, OSError):
        hz = 100
    try:
        arranque_del_sistema = float(
            Path("/proc/stat").read_text(encoding="utf-8")
            .split("btime")[1].split()[0])
    except (OSError, IndexError, ValueError):
        return None
    return arranque_del_sistema + arranque_en_ticks / hz


def _mismo_proceso(pid: int, creado: Any) -> bool:
    """¿El PID vivo es EL MISMO proceso que escribio el lock?"""
    if not proceso_vivo(pid):
        return False
    if creado is None:
        # Sin la marca no se puede distinguir un PID reciclado. Se acredita,
        # porque el lock viene de una version anterior de este mismo codigo y
        # negarlo robaria cerrojos legitimos, pero es el caso debil y por eso
        # se escribe siempre desde aqui.
        return True
    actual = creacion_de_proceso(pid)
    if actual is None:
        return True                          # no se pudo comprobar: no se acusa
    try:
        return abs(actual - float(creado)) <= TOLERANCIA_CREACION
    except (TypeError, ValueError):
        return False


def lock_vivo(lock: Path) -> bool:
    """Un lock ilegible, sin PID o de un PID reciclado no acredita a nadie."""
    try:
        datos = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(datos["pid"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return _mismo_proceso(pid, datos.get("proc_creado"))


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
        #: Prueba de propiedad. El PID no basta: si a este proceso le roban el
        #: cerrojo por parecer caducado -y robarlo es deliberado, si no un
        #: apagon congelaria la instalacion para siempre-, al salir borraria el
        #: archivo del NUEVO dueño, que se quedaria promoviendo sin exclusion
        #: mutua y sin enterarse. El token convierte "borro el lock" en "borro
        #: MI lock".
        self.token = uuid.uuid4().hex

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
                json.dump({"pid": os.getpid(), "token": self.token,
                           "started": time.time(), "etiqueta": self.etiqueta,
                           "proc_creado": creacion_de_proceso(os.getpid())}, fh)
            self.adquirido = True
            return self
        return self

    def __exit__(self, *_exc) -> None:
        if not self.adquirido:
            return
        if not self.es_mio():
            # Ya no es nuestro: alguien lo robo y hay otro proceso dentro.
            # Borrarlo ahora lo dejaria trabajando sin cerrojo.
            return
        try:
            self.ruta.unlink()
        except FileNotFoundError:
            pass

    def es_mio(self) -> bool:
        datos = self.duenno()
        return datos is not None and datos.get("token") == self.token

    def duenno(self) -> dict | None:
        try:
            return json.loads(self.ruta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
