"""Lanzar subprocesos sin estrenarle una ventana a nadie.

Vivia por triplicado: `plugin_bootstrap.flags_sin_ventana`,
`healthcheck._flags_sin_ventana` y, por importacion, en el descargador del
validador. Tres copias de cuatro lineas no parecen un problema hasta que una se
queda atras: la que se olvide vuelve a abrir la ventana, y el sintoma —una
consola que parpadea al abrir Claude— no se parece en nada a su causa.

Vive en `lifecycle/` y no en `services/` porque lo necesitan los dos lados: el
producto instalado y el instalador, que corre con el Python anfitrion antes de
que exista el entorno aislado y carga estos modulos por ruta.
"""
from __future__ import annotations

import os
import subprocess
from typing import Any


def sin_ventana() -> dict[str, Any]:
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
