"""Cerrojo interproceso sobre un archivo. UNA implementacion para todo el server.

Esto vivia dentro de `idempotency.py`, bien hecho y documentado, y se usaba solo
alli. El camino que escribe el proyecto —`txn`, y con el `planning`, que escribe
a traves de `txn`— no tenia ninguno (CORE-006). La respuesta a eso no era
escribir un segundo cerrojo: dos implementaciones del mismo mecanismo son dos
formas distintas de quedarse a medias, y la que se use menos sera la que tenga
el fallo que nadie ha visto. Se extrae la que ya estaba probada y la usan las dos.

Se reintenta sin bloquear en vez de usar la espera del sistema: en Windows
`LK_LOCK` bloquea diez segundos fijos y no hay forma de acotarlo, y colgar un
hilo del servidor sin limite no es una opcion.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterator, Optional

#: Cuanto se espera a que el otro suelte antes de rendirse.
TIMEOUT_POR_DEFECTO = 30.0

#: Cada cuanto se reintenta. Corto: la mayoria de las esperas son de milisegundos.
_PASO = 0.05

#: Exclusion entre HILOS del mismo proceso. `msvcrt.locking` y `flock` son por
#: descriptor, no por hilo, asi que sin esto dos hilos del mismo servidor
#: pasarian los dos.
_CERROJOS: Dict[str, threading.Lock] = {}
_CERROJOS_LOCK = threading.Lock()


def cerrojo_de_hilo(clave: str) -> threading.Lock:
    with _CERROJOS_LOCK:
        return _CERROJOS.setdefault(clave, threading.Lock())


if os.name == "nt":                                       # pragma: no cover
    import msvcrt

    def _intentar_tomar(fd: int) -> bool:
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _soltar(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:                                                     # pragma: no cover
    import fcntl

    def _intentar_tomar(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _soltar(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextlib.contextmanager
def cerrojo_de_archivo(ruta: Path, *, timeout: float = TIMEOUT_POR_DEFECTO,
                       al_agotarse: Optional[Callable[[float], Exception]] = None,
                       ) -> Iterator[None]:
    """Cerrojo del sistema sobre `ruta`, con espera acotada.

    `al_agotarse` construye la excepcion que se lanza si no se consigue dentro
    del plazo. Cada llamante tiene su propio error de dominio -uno habla de
    `request_id`, otro de proyecto- y forzar uno comun daria mensajes peores.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    banderas = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    fd = os.open(ruta, banderas, 0o600)
    try:
        limite = time.time() + timeout
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            if _intentar_tomar(fd):
                break
            if time.time() >= limite:
                if al_agotarse is not None:
                    raise al_agotarse(timeout)
                raise TimeoutError(
                    f"No se pudo tomar el cerrojo {ruta} en {timeout:.0f}s")
            time.sleep(_PASO)
        try:
            yield
        finally:
            _soltar(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def exclusion(ruta: Path, *, timeout: float = TIMEOUT_POR_DEFECTO,
              al_agotarse: Optional[Callable[[float], Exception]] = None,
              ) -> Iterator[None]:
    """Primero entre HILOS, luego entre PROCESOS.

    El orden es siempre este en todo el servidor; invertirlo en algun sitio
    seria un abrazo mortal entre dos procesos multihilo.
    """
    with cerrojo_de_hilo(str(ruta)):
        with cerrojo_de_archivo(ruta, timeout=timeout, al_agotarse=al_agotarse):
            yield
