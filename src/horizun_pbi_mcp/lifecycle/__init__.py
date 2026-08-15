"""Nucleo del ciclo de vida de instalacion, compartido por todos los caminos.

    plan -> preflight -> staging -> instalacion verificable -> healthcheck MCP
         -> promocion atomica -> ready -> conservacion N-1

Y ante fallo:

    fallo -> rollback -> ultimo runtime bueno utilizable -> estado preciso
          -> evidencia recuperable -> nunca un `ready` falso

Vive DENTRO del paquete, no en `scripts/`, y esa decision resuelve dos cosas a
la vez. La primera es INSTALL-005: lo que se instala con `pip` se queda hoy sin
bootstrap, de modo que el wheel no sabe prepararse a si mismo; si el nucleo
viaja en el paquete, la CLI empaquetada puede hacer exactamente lo mismo que el
plugin. La segunda es que deja de haber implementaciones divergentes: el plugin
de Claude, el de Codex y la instalacion desde wheel ejecutan este codigo.

**Solo biblioteca estandar.** `scripts/plugin_bootstrap.py` lo carga con el
Python ANFITRION, antes de que exista el entorno aislado y por tanto antes de
que exista ninguna dependencia. Cualquier import de tercero aqui rompe el
arranque en frio, que es justo el caso que el bootstrap existe para cubrir.
"""
from __future__ import annotations

from .locking import CerrojoDeCicloDeVida, lock_vivo, proceso_vivo
from .promotion import (
    CONSERVAR_ANTERIORES,
    JOURNAL,
    PREFIJO_ANTERIOR,
    PREFIJO_STAGING,
    PromocionError,
    anteriores,
    crear_staging,
    limpiar,
    promover,
    recuperar,
    restaurar_anterior,
    semillar,
)

__all__ = [
    "CONSERVAR_ANTERIORES",
    "CerrojoDeCicloDeVida",
    "JOURNAL",
    "PREFIJO_ANTERIOR",
    "PREFIJO_STAGING",
    "PromocionError",
    "anteriores",
    "crear_staging",
    "limpiar",
    "lock_vivo",
    "promover",
    "proceso_vivo",
    "recuperar",
    "restaurar_anterior",
    "semillar",
]
