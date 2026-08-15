"""Las respuestas reales de las que se congela la forma (CONTRACT-002).

No hay payloads escritos a mano: se **ejecutan las tools de verdad, por
`call_tool`**, y se congela la forma de lo que contestan. Un golden inventado
congela lo que alguien creía que devolvía la tool, que es peor que no tenerlo
porque además parece que lo tienes.

## Qué cambió, y por qué importa

La primera versión llamaba a las funciones registradas **directamente**, sin
pasar por FastMCP, y cubría **dos tools de 134**. Con eso se declaró que «el
resto necesita Power BI Desktop». Era una hipótesis, no una medición: nadie la
había comprobado tool por tool.

Ahora se recorren las 134 en **tres** escenarios deterministas:

| Escenario | Qué hay | Qué se obtiene |
|---|---|---|
| `sin-proyecto` | nada abierto | el error de dominio que ve un cliente el primer día |
| `con-proyecto` | un `.pbip` sintético en `tmp_path` | la respuesta buena de lo que sabe leer del disco |
| `con-argumentos` | una **copia fresca** por tool y una llamada válida | la respuesta real de las que exigen parámetros |

El tercero es el que cerró G2.2. Antes, 77 tools se quedaban fuera con el motivo
«requiere argumentos», que no es un impedimento externo: es trabajo. Las
llamadas están en `tests/payload_argumentos.py`, una por tool.

**Copia fresca por llamada**, porque muchas escriben: compartiendo proyecto, el
resultado de una dependería de cuáles se hubieran ejecutado antes y el golden
pasaría a depender del orden alfabético.

**Con la red y los procesos prohibidos**, porque eso es lo que convierte «esta
tool necesita Desktop» de suposición en medición: si una lo intenta, tropieza, y
queda registrada con su dependencia en vez de congelarse un payload amputado.

## Determinismo

El entorno se fija a propósito en «recién instalado, sin DLL y sin esquemas».
`pbi_health_check` enumera lo que falta: en una máquina con todo instalado ese
campo sale vacío y en una recién hecha sale poblado. Un golden que dependiera de
eso fallaría al cambiar de máquina y nadie sabría si se rompió el contrato o
solo el entorno.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

from tests.payload_argumentos import ARGUMENTOS

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Códigos que demuestran que lo que falta es un motor tabular vivo, o sea
#: Power BI Desktop. Es la única exclusión que puede invocar a TEST-003.
CODIGOS_MODELO_VIVO = {"no_active_model", "adomd_not_installed",
                       "tom_not_installed", "clr_not_available"}


def _proyecto_sintetico(raiz: Path) -> Path:
    """Un `.pbip` mínimo con la forma que el servidor sabe leer."""
    proj = raiz / "proj"
    rep = proj / "MyReport.Report"
    paginas = rep / "definition" / "pages"
    (paginas / "pg1" / "visuals").mkdir(parents=True)
    (rep / ".platform").write_text(json.dumps({
        "metadata": {"type": "Report", "displayName": "MyReport"},
        "config": {"version": "2.0"}}), encoding="utf-8")
    (rep / "definition.pbir").write_text(json.dumps({
        "version": "4.0",
        "datasetReference": {"byPath": {"path": "../MyReport.SemanticModel"}}}),
        encoding="utf-8")
    (paginas / "pages.json").write_text(json.dumps({
        "pageOrder": ["pg1"], "activePageName": "pg1"}), encoding="utf-8")
    (paginas / "pg1" / "page.json").write_text(json.dumps({
        "name": "pg1", "displayName": "P1", "width": 1280, "height": 720}),
        encoding="utf-8")
    (rep / "definition" / "report.json").write_text(json.dumps({
        "themeCollection": {}, "publicCustomVisuals": []}), encoding="utf-8")
    sm = proj / "MyReport.SemanticModel"
    (sm / "definition" / "tables").mkdir(parents=True)
    (sm / ".platform").write_text(json.dumps({
        "metadata": {"type": "SemanticModel", "displayName": "MyReport"},
        "config": {"version": "2.0"}}), encoding="utf-8")
    (sm / "definition.pbism").write_text(json.dumps({"version": "1.0"}),
                                         encoding="utf-8")
    (sm / "definition" / "model.tmdl").write_text(
        "model Model\n\tculture: es-ES\n", encoding="utf-8")
    # Una tabla de verdad: sin ella, las tools de modelo contestarian «no
    # existe» y lo congelado seria la forma de un error de nombre, no la de su
    # respuesta.
    (sm / "definition" / "tables" / "Ventas.tmdl").write_text(
        "table Ventas\n"
        "\tcolumn Importe\n\t\tdataType: double\n\t\tsourceColumn: Importe\n\n"
        "\tcolumn Unidades\n\t\tdataType: int64\n\t\tsourceColumn: Unidades\n\n"
        "\tmeasure Total = SUM(Ventas[Importe])\n",
        encoding="utf-8")
    # Y un visual, para las tools de informe.
    visual = paginas / "pg1" / "visuals" / "v1"
    visual.mkdir(parents=True, exist_ok=True)
    (visual / "visual.json").write_text(json.dumps({
        "name": "v1",
        "position": {"x": 0, "y": 0, "z": 0, "width": 200, "height": 100},
        "visual": {"visualType": "card"}}), encoding="utf-8")
    (proj / "MyReport.pbip").write_text(json.dumps({
        "version": "1.0",
        "artifacts": [{"report": {"path": "MyReport.Report"}}]}), encoding="utf-8")

    # Material para las tools que reciben una ruta de archivo.
    recursos = raiz / "recursos"
    recursos.mkdir(parents=True, exist_ok=True)
    (recursos / "datos.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    # PNG de 1x1 VALIDO, no un archivo con extension `.png`: varias tools lo
    # abren de verdad, y un archivo falso congelaria la forma de su error.
    (recursos / "logo.png").write_bytes(bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000d49444154789c6360000002000100ffff03000006"
        "000557bfabd40000000049454e44ae426082"))
    return proj / "MyReport.pbip"


def _resolver_args(plantilla: Dict[str, Any], copia: Path) -> Dict[str, Any]:
    """Sustituye `{tmp}` y `{pbip}` por las rutas de ESTA copia."""
    pbip = copia / "proj" / "MyReport.pbip"

    def _uno(v):
        if isinstance(v, str):
            return v.replace("{tmp}", str(copia).replace("\\", "/"))                     .replace("{pbip}", str(pbip).replace("\\", "/"))
        if isinstance(v, list):
            return [_uno(x) for x in v]
        if isinstance(v, dict):
            return {k: _uno(x) for k, x in v.items()}
        return v

    return {k: _uno(v) for k, v in plantilla.items()}


def _payload(salida: Any) -> Any:
    if isinstance(salida, dict) and "result" in salida:
        return salida["result"]
    return salida


#: Un solo bucle para toda la pasada, creado ANTES de prohibir el entorno.
#: `asyncio.run` monta un self-pipe por llamada, y en Windows eso pasa por
#: `socket`: con la prohibicion puesta, asyncio se rompia antes de que ninguna
#: tool llegara a intentar nada. El bucle unico deja la prohibicion para quien
#: tiene que tropezar con ella.
_BUCLE: Any = None


def _llamar(mcp, nombre: str, args: Dict[str, Any]) -> Tuple[str, Any]:
    """`('rechazo', motivo)` o `('payload', dict)`. Nunca revienta."""
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        corredor = (_BUCLE.run_until_complete if _BUCLE is not None
                    else asyncio.run)
        respuesta = corredor(mcp.call_tool(nombre, args))
    except ToolError as exc:
        return "rechazo", str(exc)
    except Exception as exc:                                 # noqa: BLE001
        return "excepcion", f"{type(exc).__name__}: {exc}"
    return "payload", _payload(respuesta[1] if isinstance(respuesta, tuple)
                               else respuesta)


#: Marca que solo aparece si una tool intento salir al entorno durante la
#: pasada con argumentos. No es un mensaje bonito: es el unico rastro que
#: distingue «esta tool necesita Desktop» -demostrado- de «supongo que lo
#: necesita».
SENTINELA_RED = "__HZ_RED_PROHIBIDA__"
SENTINELA_PROCESO = "__HZ_PROCESO_PROHIBIDO__"


def _sin_entorno():
    """Prohibe red y procesos, y devuelve como restaurarlo.

    Es lo que convierte la clasificacion en una medicion. Sin esto, decir que
    una tool «necesita Desktop» seria mirarle el nombre; con esto, la tool lo
    intenta y la prohibicion lo registra.
    """
    import socket
    import subprocess

    previos = (socket.socket, socket.create_connection, socket.getaddrinfo,
               subprocess.run, subprocess.Popen)

    def _red(*a, **k):
        raise RuntimeError(SENTINELA_RED)

    def _proceso(*a, **k):
        raise RuntimeError(SENTINELA_PROCESO)

    # `socket.socket` NO se prohibe: crear un socket no es salir a la red, y
    # asyncio crea los suyos para su propio self-pipe. Lo que se prohibe es
    # RESOLVER y CONECTAR, que es lo que hace falta para hablar con alguien.
    socket.create_connection = _red
    socket.getaddrinfo = _red
    subprocess.run = _proceso
    subprocess.Popen = _proceso

    def _restaurar():
        (socket.socket, socket.create_connection, socket.getaddrinfo,
         subprocess.run, subprocess.Popen) = previos

    return _restaurar


def _dependencia_del_entorno(payload: Any) -> str | None:
    """Si la tool tropezo con la prohibicion, dice con cual."""
    texto = json.dumps(payload, default=str) if payload is not None else ""
    if SENTINELA_RED in texto:
        return ("red: la tool intento resolver o abrir una conexion; su payload "
                "exige red o credenciales")
    if SENTINELA_PROCESO in texto:
        return ("proceso-externo: la tool intento arrancar un proceso -Power BI "
                "Desktop o npm-; su payload exige ese entorno")
    return None


def _clasificar(nombre: str, riesgo: str, requeridos: int,
                por_escenario: Dict[str, Any]) -> Dict[str, Any]:
    """El estado de UNA tool, con la dependencia que la bloquea si la hay."""
    exitos = [e for e, p in por_escenario.items()
              if isinstance(p, dict) and p.get("ok") is True
              and _dependencia_del_entorno(p) is None]
    errores = {e: p for e, p in por_escenario.items()
               if isinstance(p, dict) and p.get("ok") is False
               and _dependencia_del_entorno(p) is None}

    # Lo que tropezo con la prohibicion no se congela: su payload seria el de un
    # entorno amputado, no el que vera un cliente.
    del_entorno = [_dependencia_del_entorno(p) for p in por_escenario.values()]
    del_entorno = [d for d in del_entorno if d]

    if exitos:
        return {"estado": "exito-congelado", "bloqueo": None,
                "escenarios": sorted(por_escenario)}
    codigos = {p.get("error") for p in errores.values()}
    if codigos & CODIGOS_MODELO_VIVO:
        return {"estado": "error-congelado", "escenarios": sorted(por_escenario),
                "bloqueo": "modelo-vivo: el payload de exito exige Power BI "
                           f"Desktop sirviendo un modelo ({sorted(codigos)[0]})"}
    if errores:
        return {"estado": "error-congelado", "escenarios": sorted(por_escenario),
                "bloqueo": f"solo error de dominio en estos escenarios "
                           f"({sorted(codigos)[0]})"}
    if del_entorno:
        return {"estado": "error-congelado" if errores else "pendiente",
                "escenarios": sorted(e for e, p in por_escenario.items()
                                     if _dependencia_del_entorno(p) is None),
                "bloqueo": del_entorno[0]}
    if requeridos:
        return {"estado": "pendiente", "escenarios": [],
                "bloqueo": f"requiere-argumentos: {requeridos} parametro(s) "
                           "obligatorio(s) y ninguna llamada valida escrita en "
                           "tests/payload_argumentos.py"}
    return {"estado": "pendiente", "escenarios": [],
            "bloqueo": f"no-se-ejecuta: clasificada como {riesgo} y no se "
                       "ejecuta a ciegas sin proyecto"}


def recorrer() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Devuelve `(muestras, clasificacion)` recorriendo las 134 por MCP."""
    from horizun_pbi_mcp import config
    from horizun_pbi_mcp.config import Session, Settings
    from horizun_pbi_mcp.services import guide
    from horizun_pbi_mcp.server import build_server
    from horizun_pbi_mcp.tools.risk import annotations_for
    from tests.payload_contract import forma

    with tempfile.TemporaryDirectory(prefix="hz_payloads_") as tmp:
        raiz = Path(tmp)
        pbip = _proyecto_sintetico(raiz)
        previas = config._settings
        # La sesion es un SINGLETON de proceso, y el recorrido abre un proyecto
        # en un temporal que despues se borra. Sin resetearla, la segunda
        # llamada a `recorrer()` -y la suite hace varias- arranca apuntando a un
        # proyecto que ya no existe: las tools contestan `pbip_structure_error`
        # en vez de lo que contestarian de verdad, y el golden generado a mano
        # deja de coincidir con el que reconstruye la prueba. Costo de no verlo:
        # cuatro pruebas rojas que parecian del contrato y eran del entorno.
        previa_sesion = config._session
        config._session = None
        previo_esquemas = os.environ.get("HORIZUN_PBI_MCP_SCHEMAS_DIR")
        vacio = raiz / "sin-esquemas"
        vacio.mkdir()
        os.environ["HORIZUN_PBI_MCP_SCHEMAS_DIR"] = str(vacio)
        config._settings = Settings(
            libs_dir=raiz / "libs", outputs_dir=raiz / "outputs",
            backups_dir=raiz / "backups", max_rows=100, command_timeout=30,
            dotnet_runtime="netfx", log_level="CRITICAL", log_file=None,
            default_pbip=None)
        config._settings.ensure_dirs()
        # El golden no puede depender de si quien lo genera tiene Power BI
        # Desktop abierto. Con el descubrimiento vacio, las tools que sondean
        # el entorno contestan siempre lo mismo -y su respuesta sigue siendo
        # real: es la de una maquina sin Desktop, que es el caso mayoritario-.
        from horizun_pbi_mcp.powerbi import clr_bootstrap, desktop_discovery
        from horizun_pbi_mcp.powerbi.errors import TomNotInstalledError
        from horizun_pbi_mcp.services import (project_state, report_validator,
                                              tmdl_validate)

        previos = (desktop_discovery._ports_from_processes,
                   desktop_discovery._workspace_port_files,
                   project_state.detect,
                   tmdl_validate.parse_with_tom,
                   report_validator.estado,
                   clr_bootstrap.load_tom)

        # TOM: el mismo problema que el parseo, por otra puerta. Cargar el CLR
        # es irreversible dentro de un proceso, asi que una tool contesta
        # `tom_not_installed` o `no_active_model` segun si ALGUNA prueba
        # anterior lo cargo. Se fija «sin DLL», que es lo que tiene una
        # instalacion por `pip`: el wheel no puede traer binarios de Microsoft.
        def _sin_tom_cargado():
            raise TomNotInstalledError(
                "TOM no disponible (fijado para congelar el payload)")

        clr_bootstrap.load_tom = _sin_tom_cargado

        # El validador oficial es OPCIONAL -lo dice INSTALL-002- y consultarlo
        # lanza dos procesos. Se fija en «no instalado», que es el estado por
        # defecto de una instalacion por `pip`: el wheel no puede traerlo. Sin
        # esto, 19 tools de escritura tropezaban con la prohibicion de procesos
        # y quedaban clasificadas como si las bloqueara Desktop, que es falso:
        # solo estaban consultando algo prescindible.
        report_validator.estado = lambda: {
            "available": False, "reason": "no instalado (fijado para el golden)",
            "node": None, "cli": None, "version": None,
            "install_hint": "horizun-pbi-completar"}

        # El parseo con el serializador oficial de Microsoft depende de si el
        # CLR y las DLL de TOM se han cargado YA en este proceso, y eso lo
        # decide cualquier prueba anterior: una vez cargado no se descarga. Con
        # el CLR dentro, `parsed` es `bool` y `parse_skipped_reason` es `null`;
        # sin el, al reves. El golden no puede depender del orden de la suite,
        # asi que se fija el caso «sin TOM», que es el de una instalacion sin
        # las DLL de Microsoft -las que el wheel no puede traer-.
        def _sin_tom(definition):
            raise RuntimeError("TOM no disponible (fijado para el golden)")

        tmdl_validate.parse_with_tom = _sin_tom
        desktop_discovery._ports_from_processes = lambda: []
        desktop_discovery._workspace_port_files = lambda: []

        # El detector de «proyecto abierto en Desktop» tambien se fija, y con
        # una senal DENTRO: sus `signals[]` salen de enumerar procesos reales,
        # asi que su contenido cambia con la maquina y bajo la fixture
        # `proyecto_cerrado` de la suite sale vacio. Con la lista vacia se
        # perderia la forma del elemento, que es justo lo que un cliente lee
        # para saber por que no puede escribir.
        project_state.invalidate_cache()
        project_state.detect = lambda active, **k: project_state.ProjectOpenState(
            project_state.CLOSED, "high",
            "estado fijado para congelar la forma del payload",
            [{"signal": "cmdline", "pid": 0, "result": "denied"}])
        try:
            mcp = build_server()
            tools = {t.name: t for t in mcp._tool_manager.list_tools()}
            muestras: Dict[str, Any] = {}
            clasificacion: Dict[str, Any] = {}

            for escenario in ("sin-proyecto", "con-proyecto"):
                if escenario == "con-proyecto":
                    _llamar(mcp, "pbi_open_pbip_project", {"path": str(pbip)})
                for nombre, tool in sorted(tools.items()):
                    esquema = tool.parameters or {}
                    if esquema.get("required"):
                        continue                  # sin argumentos no hay payload
                    anotacion = annotations_for(nombre)
                    if anotacion.get("destructiveHint"):
                        continue                  # nunca a ciegas
                    if (escenario == "con-proyecto"
                            and not anotacion.get("readOnlyHint")):
                        # Con proyecto abierto, una tool de escritura ESCRIBIRIA.
                        # Es tmp_path, pero el golden dejaria de ser estable.
                        continue
                    clase, salida = _llamar(mcp, nombre, {})
                    if clase != "payload" or not isinstance(salida, dict):
                        continue
                    muestras[f"{nombre}.{escenario}"] = forma(salida)
                    clasificacion.setdefault(nombre, {})[escenario] = salida

            # ---- escenario 3: con argumentos validos, uno por tool ----
            # Cada llamada sobre una COPIA fresca: muchas de estas escriben, y
            # si compartieran proyecto el resultado de una dependeria de cuales
            # se hubieran ejecutado antes. Y con la red y los procesos
            # prohibidos, para que «necesita Desktop» sea una medicion.
            global _BUCLE
            _BUCLE = asyncio.new_event_loop()
            restaurar = _sin_entorno()
            try:
                for nombre in sorted(ARGUMENTOS):
                    if nombre not in tools:
                        continue
                    copia = raiz / "copias" / nombre
                    if copia.exists():
                        shutil.rmtree(copia, ignore_errors=True)
                    shutil.copytree(raiz / "proj", copia / "proj")
                    shutil.copytree(raiz / "recursos", copia / "recursos")
                    config._session = None
                    _llamar(mcp, "pbi_open_pbip_project",
                            {"path": str(copia / "proj" / "MyReport.pbip")})
                    args = _resolver_args(ARGUMENTOS[nombre], copia)
                    clase, salida = _llamar(mcp, nombre, args)
                    if clase != "payload" or not isinstance(salida, dict):
                        continue
                    clasificacion.setdefault(nombre, {})["con-argumentos"] = salida
                    if _dependencia_del_entorno(salida) is None:
                        muestras[f"{nombre}.con-argumentos"] = forma(salida)
            finally:
                restaurar()
                _BUCLE.close()
                _BUCLE = None
                config._session = None

            muestras["guide.situacion"] = forma(
                guide.situacion(Session(config._settings)))

            resumen = {}
            for nombre, tool in sorted(tools.items()):
                esquema = tool.parameters or {}
                anotacion = annotations_for(nombre)
                riesgo = ("solo lectura" if anotacion.get("readOnlyHint")
                          else "destructiva" if anotacion.get("destructiveHint")
                          else "escritura")
                resumen[nombre] = _clasificar(
                    nombre, riesgo, len(esquema.get("required") or []),
                    clasificacion.get(nombre, {}))
        finally:
            (desktop_discovery._ports_from_processes,
             desktop_discovery._workspace_port_files,
             project_state.detect,
             tmdl_validate.parse_with_tom,
             report_validator.estado,
             clr_bootstrap.load_tom) = previos
            project_state.invalidate_cache()
            config._session = previa_sesion
            config._settings = previas
            if previo_esquemas is None:
                os.environ.pop("HORIZUN_PBI_MCP_SCHEMAS_DIR", None)
            else:
                os.environ["HORIZUN_PBI_MCP_SCHEMAS_DIR"] = previo_esquemas
    return muestras, resumen


def capturar() -> Dict[str, Any]:
    """Solo las formas, que es lo que congela el golden."""
    return recorrer()[0]
