"""Punto 3: con la rama, Desktop muestra «Se encontraron problemas» y la tool
devuelve su texto en vez de esperar al timeout.

Con la rama el preflight ya frena el proyecto roto ANTES de lanzar Desktop
(punto 2), asi que para ver el dialogo hay que saltarse el preflight: se
sustituye `_preflight_pbip_model` por una funcion vacia SOLO en este proceso.

Uso: python dialogo.py <ruta.pbip> <outputs_aislado>
"""
from __future__ import annotations

import json
import os
import sys
import time

os.environ["HORIZUN_PBI_MCP_OUTPUTS_DIR"] = sys.argv[2]

from horizun_pbi_mcp.powerbi import desktop_launcher  # noqa: E402

desktop_launcher._preflight_pbip_model = lambda _p: None
t0 = time.monotonic()
try:
    abierto = desktop_launcher.open_pbix(sys.argv[1], timeout=120)
    print(json.dumps({"abrio": True, "segundos": round(time.monotonic() - t0, 1)}))
    print(json.dumps(desktop_launcher.close(abierto), ensure_ascii=False, default=str))
except Exception as exc:  # noqa: BLE001
    print(json.dumps({"abrio": False, "segundos": round(time.monotonic() - t0, 1),
                      "code": getattr(exc, "code", type(exc).__name__),
                      "message": str(exc),
                      "details": getattr(exc, "details", None)},
                     ensure_ascii=False, indent=1, default=str))
