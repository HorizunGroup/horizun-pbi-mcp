"""Un escritor de transacción en un proceso APARTE.

CORE-006 es una carrera **entre procesos** —Codex y Claude apuntando al mismo
`.pbip`—, así que no se puede reproducir con hilos: lo que falta es un cerrojo
interproceso, y un `threading.Lock` no dice nada sobre eso.

Este script hace una lectura-modificación-escritura sobre el mismo archivo, con
una pausa en medio para forzar el solapamiento. Dos procesos leyendo `0` a la
vez y escribiendo `1` cada uno dejan el contador en **1** habiendo declarado los
dos que aplicaron un incremento: el *lost update* del hallazgo, con las dos
respuestas en verde.

    python tests/escritor_de_prueba.py <proyecto> <backups> <objetivo> <pausa>

Imprime una línea JSON con el resultado, para que quien lo lanza pueda
distinguir «esperó su turno», «falló limpio» y «aplicó».
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))


class _Activo:
    """Lo mínimo que `project_transaction` mira de un `.pbip` activo."""

    def __init__(self, proyecto: Path):
        self.project_dir = str(proyecto)
        self.pbip_path = str(proyecto / "p.pbip")
        self.report_dir = None
        self.semantic_model_dir = None


def main() -> int:
    proyecto, backups = Path(sys.argv[1]), Path(sys.argv[2])
    objetivo, pausa = Path(sys.argv[3]), float(sys.argv[4])

    # Antes de importar nada del paquete: `Settings.load()` lee esto y cachea.
    os.environ["PBI_MCP_BACKUPS_DIR"] = str(backups)
    from horizun_pbi_mcp.services import txn as txn_service

    activo = _Activo(proyecto)
    inicio = time.monotonic()
    try:
        cm = txn_service.project_transaction(
            activo, [objetivo], tool="prueba_de_carrera",
            validate_report=False)
        with cm as t:
            entro = time.monotonic() - inicio
            actual = int(objetivo.read_text(encoding="utf-8").strip())
            # La ventana. Sin cerrojo, el otro proceso lee AQUI el mismo valor.
            time.sleep(pausa)
            t.write_text(objetivo, str(actual + 1))
        print(json.dumps({"resultado": "aplicado", "leyo": actual,
                          "escribio": actual + 1,
                          "espero_s": round(entro, 2)}))
        return 0
    except Exception as exc:                                  # noqa: BLE001
        print(json.dumps({"resultado": "fallo",
                          "error": type(exc).__name__,
                          "mensaje": str(exc)[:300]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
