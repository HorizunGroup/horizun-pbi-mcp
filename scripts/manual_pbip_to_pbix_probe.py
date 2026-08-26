"""Baseline MANUAL: ¿guarda Power BI Desktop un .pbip como .pbix?

    python scripts/manual_pbip_to_pbix_probe.py --preflight   # no lanza nada
    python scripts/manual_pbip_to_pbix_probe.py --run         # abre y espera

Por que existe
--------------
La automatizacion consigue que el desplegable de tipo diga `.pbix` y que la
lista se cierre -señal de que la aplicacion proceso la eleccion-, pero el
archivo que aparece sigue siendo un proyecto. Antes de atribuirle una
limitacion a Power BI Desktop hay que comparar contra una interaccion HUMANA
sobre el mismo proyecto. Este runner monta ese experimento y **no toca los tres
pasos que tiene que hacer una persona**: elegir el tipo, escribir el nombre y
pulsar Guardar.

Que NO hace, a proposito
------------------------
- No abre ningun proyecto real: crea un `.pbip` sintetico en una carpeta
  temporal nueva y solo abre ese.
- No cambia el proyecto activo del servidor MCP: no toca la sesion.
- No escribe en OneDrive, en el repositorio ni en ninguna carpeta del usuario.
- No cierra ningun proceso que ya estuviera abierto. Solo termina el que lanza
  el propio runner, y antes revalida PID, hora de arranque y que su linea de
  comandos apunte al sandbox.
- No selecciona el tipo, no escribe el nombre y no pulsa Guardar.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

#: Minutos que se espera la accion humana. Cinco es el minimo pedido; se deja
#: en seis para que no corra prisa elegir en el desplegable.
ESPERA_MANUAL_SEGUNDOS = 360

#: Nombre que hay que teclear a mano. SIN extension a proposito: la que ponga
#: Desktop delata el filtro que de verdad tenia activo.
NOMBRE_A_ESCRIBIR = "SinExtension"


def redactar(valor: Any) -> str:
    from horizun_pbi_mcp.services import redaction

    return redaction.rutas(str(valor))


def censo_desktop() -> Dict[int, Dict[str, Any]]:
    """{pid: {create_time, cmdline}} de los Desktop que YA estaban."""
    import psutil

    censo: Dict[int, Dict[str, Any]] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            if (proc.info.get("name") or "").casefold() != "pbidesktop.exe":
                continue
            censo[proc.info["pid"]] = {
                "create_time": float(proc.create_time()),
                "cmdline": " ".join(str(c) for c in (proc.cmdline() or [])),
            }
        except Exception:                                 # noqa: BLE001
            continue
    return censo


def crear_sandbox() -> Dict[str, Path]:
    """Carpeta temporal NUEVA con el .pbip sintetico versionado del repo."""
    from tests.fixtures import synthetic

    caja = Path(tempfile.mkdtemp(prefix="hz_baseline_"))
    pbip = synthetic.materialize(caja, name="desktop_openable")
    return {"sandbox": caja, "pbip": pbip}


def preflight(mostrar_sandbox: bool = True) -> Dict[str, Any]:
    """Todo lo que hay que poder revisar ANTES de lanzar nada."""
    censo = censo_desktop()
    datos: Dict[str, Any] = {
        "script": str(Path(__file__).resolve()),
        "desktop_preexistentes": {
            pid: {"create_time": info["create_time"],
                  "cmdline": redactar(info["cmdline"])[:120]}
            for pid, info in censo.items()},
        "espera_manual_segundos": ESPERA_MANUAL_SEGUNDOS,
        "nombre_a_escribir": NOMBRE_A_ESCRIBIR,
    }
    if mostrar_sandbox:
        caja = crear_sandbox()
        datos["sandbox"] = str(caja["sandbox"])
        datos["pbip_sintetico"] = str(caja["pbip"])
        datos["_rutas"] = caja
    return datos


def _es_nuestro(pid: int, creado: float, sandbox: Path,
                censo_previo: Dict[int, Dict[str, Any]]) -> bool:
    """Solo es nuestro si NO estaba antes y vive dentro del sandbox."""
    import psutil

    previo = censo_previo.get(pid)
    if previo and abs(previo["create_time"] - creado) < 1.0:
        return False                                      # preexistente
    try:
        proc = psutil.Process(pid)
        if (proc.name() or "").casefold() != "pbidesktop.exe":
            return False
        if abs(float(proc.create_time()) - creado) > 1.0:
            return False
        linea = " ".join(str(c) for c in (proc.cmdline() or []))
    except Exception:                                     # noqa: BLE001
        return False
    return str(sandbox).casefold() in linea.casefold()


def cerrar_lo_nuestro(censo_previo: Dict[int, Dict[str, Any]],
                      sandbox: Path) -> List[int]:
    import psutil

    cerrados: List[int] = []
    for pid, info in censo_desktop().items():
        if not _es_nuestro(pid, info["create_time"], sandbox, censo_previo):
            continue
        try:
            proc = psutil.Process(pid)
            hijos = proc.children(recursive=True)
            for objetivo in [*hijos, proc]:
                try:
                    objetivo.terminate()
                except Exception:                         # noqa: BLE001
                    continue
            psutil.wait_procs([*hijos, proc], timeout=30)
            cerrados.append(pid)
        except Exception:                                 # noqa: BLE001
            continue
    return cerrados


def ejecutar() -> int:
    from horizun_pbi_mcp.powerbi import desktop_launcher, desktop_ui

    datos = preflight()
    caja = datos.pop("_rutas")
    sandbox, pbip = caja["sandbox"], caja["pbip"]
    censo_previo = censo_desktop()

    print("=" * 68)
    print("PREFLIGHT")
    print("=" * 68)
    print(f"  script            : {datos['script']}")
    print(f"  sandbox           : {redactar(sandbox)}")
    print(f"  pbip sintetico    : {redactar(pbip)}")
    print(f"  espera manual     : {ESPERA_MANUAL_SEGUNDOS} s")
    print(f"  Desktop previos   : {sorted(censo_previo)} (NO se tocan)")
    for pid, info in censo_previo.items():
        print(f"      pid {pid}: {info['cmdline'][:90]}")

    # Nadie puede tener ya abierto ESE archivo: es nuevo. Se comprueba igual.
    ya_abierto = desktop_launcher.proceso_con_archivo_abierto(pbip)
    if ya_abierto:
        print(f"\nABORTA: el pid {ya_abierto} ya sirve ese archivo. No se "
              "reutiliza una ventana existente.")
        shutil.rmtree(sandbox, ignore_errors=True)
        return 2

    print("\nAbriendo SOLO el .pbip sintetico en Power BI Desktop...")
    abierto = None
    try:
        abierto = desktop_launcher.open_pbix(str(pbip), timeout=600,
                                             reuse_open=False)
        print(f"  Desktop lanzado: pid={abierto.desktop_pid} "
              f"(nuestro: {abierto.launched_by_us})")
        if not abierto.launched_by_us:
            print("ABORTA: la sesion no la lanzamos nosotros.")
            return 2

        adaptador = desktop_ui.Win32UIAdapter()
        ventana = adaptador.ventana_principal(abierto.desktop_pid,
                                              abierto.desktop_started)
        print(f"  ventana: hwnd={ventana.hwnd} "
              f"titulo={redactar(ventana.title)[:40]!r}")

        print("\nAbriendo el cuadro 'Guardar como' (F12)...")
        adaptador.abrir_guardar_como(ventana)
        dialogo = adaptador.esperar_dialogo_guardado(
            abierto.desktop_pid, timeout=60)
        tipos = adaptador.tipos_de_archivo(dialogo)
        print(f"  cuadro listo: hwnd={dialogo.hwnd}")
        print(f"  tipos ofrecidos: {tipos}")

        print("\n" + "=" * 68)
        print("TU TURNO — el cuadro 'Guardar como' esta visible")
        print("=" * 68)
        print("  1) Selecciona:  Archivo de Power BI (*.pbix)")
        print(f"  2) Escribe el nombre:  {NOMBRE_A_ESCRIBIR}   (SIN extension)")
        print(f"  3) Guarda en:  {redactar(sandbox)}")
        print("  4) Pulsa Guardar UNA vez")
        print("  Si aparece cualquier otro cuadro, NO lo respondas: avisame.")
        print("=" * 68)
        print(f"\nObservando {ESPERA_MANUAL_SEGUNDOS} s...", flush=True)

        aparecidos: List[str] = []
        limite = time.monotonic() + ESPERA_MANUAL_SEGUNDOS
        while time.monotonic() < limite:
            time.sleep(2.0)
            hallados = sorted(p.name for p in sandbox.iterdir()
                              if p.name.startswith(NOMBRE_A_ESCRIBIR))
            if hallados and hallados != aparecidos:
                aparecidos = hallados
                print(f"  [{int(time.monotonic() - (limite - ESPERA_MANUAL_SEGUNDOS))}s] "
                      f"aparecio: {hallados}", flush=True)
                if any(n.endswith(".pbix") for n in hallados):
                    break
            modales = adaptador.modales(abierto.desktop_pid,
                                        excluir=[dialogo.hwnd])
            if modales:
                print("\n  MODAL DETECTADO (no se responde):")
                for m in modales:
                    print(f"    {m.to_dict()}")
                break

        print("\n" + "=" * 68)
        print("RESULTADO")
        print("=" * 68)
        final = sorted(p.name for p in sandbox.iterdir())
        print(f"  archivos en el sandbox: {final}")
        pbix = (sandbox / f"{NOMBRE_A_ESCRIBIR}.pbix")
        pbip_out = (sandbox / f"{NOMBRE_A_ESCRIBIR}.pbip")
        if pbix.is_file():
            print(f"  CASO A: se creo {NOMBRE_A_ESCRIBIR}.pbix "
                  f"({pbix.stat().st_size} bytes)")
        elif pbip_out.exists():
            print(f"  CASO B: se creo {NOMBRE_A_ESCRIBIR}.pbip (proyecto)")
        else:
            print("  CASO C: no aparecio nada con ese nombre")
        try:
            from horizun_pbi_mcp.powerbi.desktop_capture import _enumerate_windows
            print("  titulos de ventana:",
                  [redactar(v.title)[:40] for v in
                   _enumerate_windows(abierto.desktop_pid)])
        except Exception:                                 # noqa: BLE001
            pass
        return 0
    except Exception as exc:                              # noqa: BLE001
        print(f"\nFALLO {type(exc).__name__}: {redactar(exc)[:300]}")
        detalles = getattr(exc, "details", None)
        if detalles:
            print(f"  detalles: {redactar(detalles)[:500]}")
        return 1
    finally:
        cerrados = cerrar_lo_nuestro(censo_previo, sandbox)
        print(f"\n  cerrados por el runner (solo suyos): {cerrados}")
        vivos = sorted(censo_desktop())
        print(f"  Desktop vivos ahora: {vivos}")
        print(f"  preexistentes intactos: "
              f"{sorted(censo_previo) == [p for p in vivos if p in censo_previo]}")
        time.sleep(2.0)
        shutil.rmtree(sandbox, ignore_errors=True)
        print(f"  sandbox borrado: {not sandbox.exists()}")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preflight", action="store_true",
                   help="muestra lo que haria; no lanza Power BI Desktop")
    p.add_argument("--run", action="store_true",
                   help="abre el cuadro y espera la accion manual")
    args = p.parse_args(argv)

    if args.preflight:
        datos = preflight(mostrar_sandbox=False)
        print("PREFLIGHT (no se lanzo nada)")
        for clave, valor in datos.items():
            print(f"  {clave}: {valor}")
        print("\n  El sandbox se crea al ejecutar con --run.")
        return 0
    if args.run:
        return ejecutar()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
