"""Envoltorio: la logica vive en `horizun_pbi_mcp.completado.esquemas`.

Se conserva la ruta porque el instalador del plugin invoca este archivo por
ruta y el README lo documenta. Lo que ya no vive aqui es el codigo: una
instalacion por `pip` no tiene `scripts/`, y el comando que `pbi_health_check`
recomienda tiene que existir en la misma instalacion que da el diagnostico.

    python scripts/fetch_pbir_schemas.py [...]     # equivale a:
    horizun-pbi-completar                # que ejecuta los tres
"""
from __future__ import annotations

import sys
from pathlib import Path

# Desde el clon, sin instalar. Instalado, el paquete ya esta en el path y esto
# no estorba: `sys.path` admite rutas que no existen.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from horizun_pbi_mcp.completado.esquemas import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
