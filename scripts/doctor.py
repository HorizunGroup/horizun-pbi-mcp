"""Diagnostico de instalacion de Horizun PBI MCP. ESTRICTAMENTE DE SOLO LECTURA.

No instala, no descarga, no escribe en el proyecto del usuario y no modifica
ningun modelo. Solo comprueba y reporta.

Codigos de salida:
    0  instalacion operativa (los requisitos OBLIGATORIOS pasan)
    1  fallo de un requisito obligatorio
    2  error de uso (argumentos invalidos)

Cada comprobacion distingue su naturaleza, para no confundir "falta una DLL"
(rompe todo) con "Power BI Desktop no esta abierto" (normal si no hay informe).

Uso:
    python scripts/doctor.py
    python scripts/doctor.py --json
    python scripts/doctor.py --require-desktop
    python scripts/doctor.py --check-dax
    python scripts/doctor.py --check-pbip "C:/ruta/MiInforme.pbip"
    python scripts/doctor.py --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"

# Estados posibles de una comprobacion.
OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

# DLLs minimas para que la capa en vivo funcione.
REQUIRED_DLLS = [
    "Microsoft.AnalysisServices.AdomdClient.dll",
    "Microsoft.AnalysisServices.Tabular.dll",
    "Microsoft.AnalysisServices.Core.dll",
]

def _identidad() -> dict:
    """Identidad del producto, leida del propio paquete."""
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        from horizun_pbi_mcp import branding
        return branding.identity()
    except Exception:  # noqa: BLE001
        return {"product": "Horizun PBI MCP", "version": "?",
                "server_name": "horizun-pbi-mcp"}


def _producto() -> str:
    return _identidad()["product"]


def _version() -> str:
    return _identidad()["version"]


def _expected_tool_count() -> int:
    """Numero de tools esperado, leido del golden del contrato.

    No se codifica a mano: cada macrofase anade tools, y un numero fijo aqui se
    desincroniza y hace fallar el diagnostico por un motivo falso.
    """
    try:
        import json

        golden = PROJECT_ROOT / "tests" / "golden" / "tools_v1.json"
        return int(json.loads(golden.read_text(encoding="utf-8"))["tool_count"])
    except Exception:  # noqa: BLE001
        return 0          # 0 = desconocido: la comprobacion se vuelve informativa


class Report:
    """Acumula resultados. Un fallo OBLIGATORIO es lo unico que cambia el exit code."""

    def __init__(self, verbose: bool = False):
        self.checks: List[Dict[str, Any]] = []
        self.verbose = verbose

    def add(self, check_id: str, title: str, status: str, detail: str,
            *, required: bool, hint: str = "", data: Any = None) -> str:
        self.checks.append({
            "id": check_id, "title": title, "status": status, "detail": detail,
            "required": required, "hint": hint, "data": data,
        })
        return status

    @property
    def failed_required(self) -> List[Dict[str, Any]]:
        return [c for c in self.checks if c["required"] and c["status"] == FAIL]

    @property
    def exit_code(self) -> int:
        return 1 if self.failed_required else 0

    def to_json(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for c in self.checks:
            counts[c["status"]] = counts.get(c["status"], 0) + 1
        return {
            "ok": self.exit_code == 0,
            "exit_code": self.exit_code,
            "summary": counts,
            "failed_required": [c["id"] for c in self.failed_required],
            "checks": self.checks,
            "environment": {
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "project_root": str(PROJECT_ROOT),
            },
        }

    def render(self) -> str:
        glyph = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]", SKIP: "[skip]"}
        lines = ["", f"{_producto()} {_version()} — diagnostico de instalacion (solo lectura)",
                 f"  raiz    : {PROJECT_ROOT}",
                 f"  python  : {sys.version.split()[0]}  ({platform.system()} {platform.release()})",
                 ""]
        for c in self.checks:
            mark = "" if c["required"] else "  (opcional)"
            lines.append(f"{glyph[c['status']]} {c['title']}{mark}")
            if c["detail"]:
                lines.append(f"         {c['detail']}")
            if c["hint"] and c["status"] in (FAIL, WARN):
                lines.append(f"         -> {c['hint']}")
            if self.verbose and c["data"] is not None:
                lines.append(f"         datos: {json.dumps(c['data'], ensure_ascii=False)[:400]}")
        lines.append("")
        if self.failed_required:
            lines.append(f"RESULTADO: {len(self.failed_required)} requisito(s) obligatorio(s) "
                         f"fallando: {', '.join(c['id'] for c in self.failed_required)}")
        else:
            warns = sum(1 for c in self.checks if c["status"] == WARN)
            extra = f" ({warns} aviso(s) no bloqueante(s))" if warns else ""
            lines.append(f"RESULTADO: instalacion operativa{extra}")
        lines.append("")
        return "\n".join(lines)


# --------------------------------------------------------------- checks ------
def check_python(rep: Report) -> None:
    v = sys.version_info
    if (v.major, v.minor) >= (3, 10):
        rep.add("python", f"Python {v.major}.{v.minor}.{v.micro} (>=3.10)", OK, "",
                required=True)
    else:
        rep.add("python", f"Python {v.major}.{v.minor}", FAIL,
                "Se requiere Python 3.10 o superior.", required=True,
                hint="Instala Python 3.10+ y recrea el entorno.")


def check_platform(rep: Report) -> None:
    if platform.system() == "Windows":
        rep.add("platform", f"Sistema operativo: {platform.system()}", OK, "", required=False)
    else:
        rep.add("platform", f"Sistema operativo: {platform.system()}", WARN,
                "Power BI Desktop solo existe en Windows: la capa EN VIVO no funcionara.",
                required=False,
                hint="La capa EN DISCO (.pbip / TMDL / PBIR) si funciona en cualquier SO.")


#: Modulo importable -> paquete de `project.dependencies`. `pythonnet` va
#: aparte porque solo hace falta para la capa EN VIVO.
#:
#: La lista se escribe a mano para poder nombrar el modulo, que no siempre se
#: llama como el paquete (`dotenv` / `python-dotenv`), pero
#: `tests/test_packaging.py` comprueba que las cubre TODAS. Antes solo miraba
#: tres: una instalacion sin `jsonschema` reportaba "Dependencias: OK" y luego
#: fallaba cada escritura PBIR con `schema_unavailable`.
DEPENDENCIAS = (
    ("mcp", "mcp"),
    ("psutil", "psutil"),
    ("dotenv", "python-dotenv"),
    ("jsonschema", "jsonschema"),
    ("referencing", "referencing"),
    ("openpyxl", "openpyxl"),
    ("reportlab", "reportlab"),
    ("pypdf", "pypdf"),
    ("msal", "msal"),
)


def check_dependencies(rep: Report) -> None:
    import importlib.util
    missing, present = [], {}
    for mod, pkg in DEPENDENCIAS:
        if importlib.util.find_spec(mod) is None:
            missing.append(pkg)
        else:
            try:
                import importlib.metadata as md
                present[pkg] = md.version(pkg)
            except Exception:  # noqa: BLE001
                present[pkg] = "?"
    if missing:
        rep.add("dependencies", "Dependencias de Python", FAIL,
                f"Faltan: {', '.join(missing)}", required=True,
                hint="python -m pip install -r requirements.txt", data=present)
    else:
        rep.add("dependencies", "Dependencias de Python", OK,
                ", ".join(f"{k}=={v}" for k, v in present.items()), required=True,
                data=present)

    # pythonnet es obligatorio SOLO para la capa en vivo.
    if importlib.util.find_spec("clr") is None:
        rep.add("pythonnet", "pythonnet (interop .NET)", WARN,
                "No disponible: la capa EN VIVO (DAX/TOM) no funcionara.",
                required=False,
                hint="python -m pip install pythonnet   (necesario para ADOMD.NET y TOM)")
    else:
        try:
            import importlib.metadata as md
            ver = md.version("pythonnet")
        except Exception:  # noqa: BLE001
            ver = "?"
        rep.add("pythonnet", f"pythonnet (interop .NET) {ver}", OK, "", required=False)


def check_dlls(rep: Report) -> None:
    libs_dir = Path(os.environ.get("PBI_MCP_LIBS_DIR") or (PROJECT_ROOT / "libs"))
    if not libs_dir.exists():
        rep.add("dlls", "DLLs de Analysis Services (ADOMD.NET / TOM)", FAIL,
                f"No existe la carpeta {libs_dir}", required=True,
                hint="python scripts/fetch_libs.py")
        return
    found = {p.name for p in libs_dir.glob("*.dll")}
    missing = [d for d in REQUIRED_DLLS if d not in found]

    # Fase J3: ademas de estar, deben coincidir con el manifiesto fijado. Una
    # DLL de otra version arranca igual y falla mas tarde, de forma rara.
    if not missing:
        try:
            import importlib.util as _u

            spec = _u.spec_from_file_location(
                "_fetch_libs", PROJECT_ROOT / "scripts" / "fetch_libs.py")
            mod = _u.module_from_spec(spec)
            spec.loader.exec_module(mod)
            est = mod.estado()
            if not est["ready"]:
                rep.add("dlls", "DLLs de Analysis Services (ADOMD.NET / TOM)",
                        WARN,
                        f"presentes pero no coinciden con la version fijada "
                        f"{est.get('pinned_version')}: {est['reason']}",
                        required=False,
                        hint="python scripts/fetch_libs.py", data=est)
                return
        except Exception:  # noqa: BLE001 - el diagnostico no puede tumbarse
            pass
    if missing:
        rep.add("dlls", "DLLs de Analysis Services (ADOMD.NET / TOM)", FAIL,
                f"Faltan {len(missing)}: {', '.join(missing)}", required=True,
                hint="python scripts/fetch_libs.py",
                data={"encontradas": sorted(found)})
    else:
        rep.add("dlls", "DLLs de Analysis Services (ADOMD.NET / TOM)", OK,
                f"{len(found)} DLL en {libs_dir}", required=True,
                data={"encontradas": sorted(found)})



def check_pbir_schemas(rep: Report) -> None:
    """Los esquemas oficiales del PBIR (Fase E3.1).

    No son obligatorios para arrancar, pero SIN ellos toda escritura PBIR falla
    cerrada con `schema_unavailable`: es mejor decirlo aqui que descubrirlo al
    intentar guardar un cambio.
    """
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        from horizun_pbi_mcp.services import pbir_schema
    except Exception as exc:  # noqa: BLE001
        rep.add("pbir_schemas", "Esquemas oficiales del PBIR", WARN,
                f"no se pudo comprobar: {exc}", required=False)
        return

    estado = pbir_schema.estado_cache()
    if estado["ready"]:
        no_pub = len(pbir_schema.no_publicados())
        detalle = f"{estado['expected']} documento(s) verificados por hash"
        if no_pub:
            detalle += (f"; {no_pub} que Power BI declara NO estan publicados "
                        "por Microsoft (limitacion conocida)")
        rep.add("pbir_schemas", "Esquemas oficiales del PBIR", OK, detalle,
                required=False, data=estado)
    else:
        rep.add("pbir_schemas", "Esquemas oficiales del PBIR", WARN,
                f"sin instalar o alterados: {estado['reason']}. Las escrituras "
                "PBIR fallaran con schema_unavailable.",
                required=False, hint="python scripts/fetch_pbir_schemas.py",
                data=estado)


def check_report_validator(rep: Report) -> None:
    """Validador oficial de Microsoft (Fase E3.2).

    Matiz importante: `[OK]` solo si el CLI compatible esta ahi. Con solo el
    validador interno la instalacion funciona, pero NO cubre relaciones entre
    archivos, asi que decir `[OK]` seria enganoso.
    """
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        from horizun_pbi_mcp.services import report_validator as rv
    except Exception as exc:  # noqa: BLE001
        rep.add("report_validator", "Validador PBIR oficial (Microsoft)", SKIP,
                f"no se pudo comprobar: {exc}", required=False)
        return

    est = rv.estado()
    if est["available"]:
        rep.add("report_validator", "Validador PBIR oficial (Microsoft)", OK,
                f"{rv.PAQUETE_NPM} {est['version']} (Node {est['node_major']})",
                required=False, data=est)
    elif not est["node"]:
        rep.add("report_validator", "Validador PBIR oficial (Microsoft)", SKIP,
                "Node no esta instalado; solo hay validacion interna por "
                "esquema, que no cubre relaciones entre archivos.",
                required=False, hint=est["install_hint"], data=est)
    else:
        rep.add("report_validator", "Validador PBIR oficial (Microsoft)", WARN,
                f"{est['reason']}. Solo hay validacion interna por esquema: no "
                "cubre objetos de formato, roles ni temas.",
                required=False, hint=est["install_hint"], data=est)

def check_server_boots(rep: Report) -> Optional[List[Any]]:
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        import asyncio
        from horizun_pbi_mcp.server import build_server
        t0 = time.perf_counter()
        mcp = build_server()
        tools = asyncio.run(mcp.list_tools())
        ms = (time.perf_counter() - t0) * 1000
        rep.add("server_boot", "El servidor MCP arranca", OK,
                f"{len(tools)} tools registradas en {ms:.0f} ms", required=True)
        return tools
    except Exception as exc:  # noqa: BLE001
        rep.add("server_boot", "El servidor MCP arranca", FAIL,
                f"{type(exc).__name__}: {exc}", required=True,
                hint="Revisa el traceback ejecutando: "
                          "python -m horizun_pbi_mcp.server")
        return None


def check_contract(rep: Report, tools: Optional[List[Any]]) -> None:
    if tools is None:
        rep.add("contract", "Contrato MCP (34 tools congeladas)", SKIP,
                "El servidor no arranco.", required=True)
        return
    esperadas = _expected_tool_count()
    if esperadas and len(tools) != esperadas:
        rep.add("contract", "Contrato MCP", FAIL,
                f"Se esperaban {esperadas} tools (segun el golden) y hay {len(tools)}.",
                required=True,
                hint="python -m pytest tests/test_tool_contract.py -q")
        return
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    try:
        from tests import contract_utils
        current = contract_utils.build_snapshot(tools)
        if not contract_utils.GOLDEN_PATH.exists():
            rep.add("contract", "Contrato MCP", WARN,
                    "No existe el golden; no se pudo comparar.", required=False,
                    hint="python -m tests.contract_utils --write")
            return
        breaking, compatible = contract_utils.diff_snapshots(
            contract_utils.load_golden(), current)
        if breaking:
            rep.add("contract", "Contrato MCP", FAIL,
                    f"{len(breaking)} ruptura(s) de compatibilidad.", required=True,
                    hint="python -m tests.contract_utils   (muestra el detalle)",
                    data=breaking)
        elif compatible:
            rep.add("contract", "Contrato MCP", WARN,
                    f"{len(compatible)} cambio(s) compatible(s) sin congelar.",
                    required=False,
                    hint="python -m tests.contract_utils --write", data=compatible)
        else:
            rep.add("contract", f"Contrato MCP ({esperadas} tools congeladas)",
                    OK, "Coincide con el golden.", required=True)
    except Exception as exc:  # noqa: BLE001
        rep.add("contract", "Contrato MCP", WARN,
                f"No se pudo verificar: {type(exc).__name__}: {exc}", required=False)


def check_desktop(rep: Report, require: bool) -> List[Dict[str, Any]]:
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        from horizun_pbi_mcp.powerbi import desktop_discovery
        instances = desktop_discovery.discover_instances()
    except Exception as exc:  # noqa: BLE001
        rep.add("desktop", "Power BI Desktop abierto", FAIL if require else WARN,
                f"No se pudo descubrir: {type(exc).__name__}: {exc}", required=require)
        return []

    if not instances:
        rep.add("desktop", "Power BI Desktop abierto", FAIL if require else SKIP,
                "No hay ninguna instancia local abierta.", required=require,
                hint="Abre tu informe en Power BI Desktop. Sin el, la capa EN VIVO "
                     "no aplica; la capa EN DISCO (.pbip) si funciona.")
        return []

    alive = [i for i in instances if i.get("status") == "ok"]
    dead = [i for i in instances if i.get("status") != "ok"]

    if not alive:
        rep.add("desktop", "Power BI Desktop abierto", FAIL if require else WARN,
                f"{len(dead)} instancia(s) detectada(s) pero ninguna responde "
                "(puertos muertos de un Desktop cerrado o caido).",
                required=require,
                hint="Cierra y reabre Power BI Desktop.",
                data=[{"port": i["port"], "warnings": i.get("warnings")} for i in dead])
        return instances

    detail = "; ".join(
        f"puerto {i['port']} (pid {i.get('pid')}, {i.get('table_count')} tablas)"
        for i in alive)
    if len(alive) > 1:
        rep.add("desktop", "Power BI Desktop abierto", WARN,
                f"{len(alive)} instancias activas: {detail}", required=False,
                hint="Con varias instancias hay que elegir explicitamente: "
                     "pbi_select_model(port=<puerto>). El servidor NO elige solo.",
                data=[i["port"] for i in alive])
    else:
        rep.add("desktop", "Power BI Desktop abierto", OK, detail, required=False,
                data=[i["port"] for i in alive])
    if dead:
        rep.add("desktop_dead_ports", "Puertos obsoletos detectados", WARN,
                f"{len(dead)} puerto(s) no responden: "
                f"{', '.join(str(i['port']) for i in dead)}", required=False,
                hint="Suelen ser archivos msmdsrv.port.txt de sesiones anteriores.")
    return instances


def check_session_freshness(rep: Report, instances: List[Dict[str, Any]]) -> None:
    outputs = Path(os.environ.get("PBI_MCP_OUTPUTS_DIR") or (PROJECT_ROOT / "outputs"))
    session_file = outputs / "session.json"
    if not session_file.exists():
        rep.add("session", "Sesion persistida", SKIP, "No hay session.json.", required=False)
        return
    try:
        data = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        rep.add("session", "Sesion persistida", WARN,
                f"session.json CORRUPTO: {exc}. El servidor lo conserva sin "
                "tocar y arranca con la sesion vacia; no persistira nada "
                "mientras siga asi.", required=False,
                hint="Miralo y borralo tu si no te dice nada. No se "
                     f"sobreescribe solo, a proposito: {session_file}")
        return

    problems = []
    model = data.get("active_model") or {}
    if model.get("port"):
        live_ports = {i["port"] for i in instances if i.get("status") == "ok"}
        if not live_ports:
            problems.append(f"apunta al puerto {model['port']} pero no hay instancias vivas")
        elif model["port"] not in live_ports:
            problems.append(
                f"apunta al puerto {model['port']}, que ya no existe "
                f"(vivos: {sorted(live_ports)})")

    pbip = data.get("active_pbip") or {}
    if pbip.get("pbip_path") and not Path(pbip["pbip_path"]).exists():
        problems.append("el .pbip activo ya no existe en disco")

    if problems:
        rep.add("session", "Sesion persistida", WARN,
                "Sesion OBSOLETA: " + "; ".join(problems), required=False,
                hint="Ejecuta pbi_select_model / pbi_open_pbip_project de nuevo, "
                     f"o borra {session_file}.")
    else:
        rep.add("session", "Sesion persistida", OK, "Coherente con el estado actual.",
                required=False)


def check_dax(rep: Report, instances: List[Dict[str, Any]]) -> None:
    """Consulta DAX ESTRICTAMENTE de solo lectura contra la instancia viva."""
    alive = [i for i in instances if i.get("status") == "ok"]
    if not alive:
        rep.add("dax", "Consulta DAX de solo lectura", SKIP,
                "No hay ninguna instancia viva.", required=False)
        return
    if len(alive) > 1:
        rep.add("dax", "Consulta DAX de solo lectura", SKIP,
                f"Hay {len(alive)} instancias: no se elige ninguna automaticamente.",
                required=False,
                hint="Vuelve a ejecutar con Power BI Desktop con un solo informe abierto.")
        return
    inst = alive[0]
    try:
        from horizun_pbi_mcp.powerbi.adomd_client import AdomdClient
        with AdomdClient(inst["connection_string"], inst.get("catalog")) as client:
            cols, rows, _t, ms = client.execute_reader(
                'EVALUATE ROW("ok", 1, "probe", "doctor")', max_rows=1)
        rep.add("dax", "Consulta DAX de solo lectura", OK,
                f"EVALUATE ROW respondio en {ms:.0f} ms (puerto {inst['port']})",
                required=False, data={"columns": cols, "rows": rows})
    except Exception as exc:  # noqa: BLE001
        msg = getattr(exc, "Message", None) or str(exc)
        rep.add("dax", "Consulta DAX de solo lectura", FAIL,
                f"{type(exc).__name__}: {msg}", required=False,
                hint="La conexion existe pero el motor rechazo la consulta.")


def check_pbip(rep: Report, path: str) -> None:
    """Abre y valida un .pbip. SOLO LECTURA: no escribe nada en el proyecto."""
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))
    try:
        import tempfile
        from horizun_pbi_mcp.config import Session, Settings
        from horizun_pbi_mcp.pbip import pbir_reader, project_locator

        # Sesion desechable en un temporal: no toca outputs/ del proyecto.
        tmp = Path(tempfile.mkdtemp(prefix="pbimcp_doctor_"))
        settings = Settings(
            libs_dir=PROJECT_ROOT / "libs", outputs_dir=tmp / "out",
            backups_dir=tmp / "bk", max_rows=10, command_timeout=30,
            dotnet_runtime="netfx", log_level="ERROR", log_file=None, default_pbip=None)
        settings.ensure_dirs()
        session = Session(settings)

        summary = project_locator.open_project(session, path)
        validation = project_locator.validate_project(session)
        detail = (f"pbir={summary['has_pbir']} tmdl={summary['has_tmdl']} "
                  f"valido={validation['valid']}")
        pages_info = None
        if summary["has_pbir"]:
            pages = pbir_reader.list_pages(session.require_active_pbip())
            pages_info = [{"id": p["name"], "name": p.get("display_name"),
                           "visuals": p.get("visual_count")} for p in pages]
            detail += f", {len(pages)} pagina(s)"

        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

        status = OK if validation["valid"] else WARN
        rep.add("pbip", f"Proyecto .pbip: {Path(path).name}", status, detail,
                required=False,
                hint="; ".join(validation.get("warnings") or []),
                data={"checks": validation["checks"], "pages": pages_info})
    except Exception as exc:  # noqa: BLE001
        rep.add("pbip", f"Proyecto .pbip: {path}", FAIL,
                f"{type(exc).__name__}: {exc}", required=False,
                hint="Comprueba que la ruta apunta a un .pbip valido.")


def check_mcp_registration(rep: Report) -> None:
    """Informativo: ¿este servidor esta registrado en algun cliente MCP?"""
    local = PROJECT_ROOT / ".mcp.json"
    if local.exists():
        rep.add("registration", "Registro MCP en este repositorio", OK,
                f"Existe {local.name}", required=False)
    else:
        rep.add("registration", "Registro MCP en este repositorio", WARN,
                "No hay .mcp.json: ningun cliente MCP usara este servidor todavia.",
                required=False,
                hint="python scripts/make_mcp_config.py --client claude-code --write")


# ------------------------------------------------------------------ main -----
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Diagnostico de instalacion de PowerBI-MCP (solo lectura).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true",
                    help="Salida en JSON (para automatizacion).")
    ap.add_argument("--require-desktop", action="store_true",
                    help="Falla si Power BI Desktop no esta abierto.")
    ap.add_argument("--check-dax", action="store_true",
                    help="Ejecuta una consulta DAX de SOLO LECTURA contra el modelo abierto.")
    ap.add_argument("--check-pbip", metavar="PATH",
                    help="Abre y valida un .pbip (solo lectura).")
    ap.add_argument("--verbose", action="store_true",
                    help="Incluye los datos crudos de cada comprobacion.")
    args = ap.parse_args(argv)

    rep = Report(verbose=args.verbose)

    check_python(rep)
    check_platform(rep)
    check_dependencies(rep)
    check_dlls(rep)
    tools = check_server_boots(rep)
    check_contract(rep, tools)
    instances = check_desktop(rep, require=args.require_desktop)
    check_session_freshness(rep, instances)
    check_pbir_schemas(rep)
    check_report_validator(rep)
    check_mcp_registration(rep)

    if args.check_dax:
        check_dax(rep, instances)
    if args.check_pbip:
        check_pbip(rep, args.check_pbip)

    if args.json:
        print(json.dumps(rep.to_json(), indent=2, ensure_ascii=False))
    else:
        print(rep.render())
    return rep.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
