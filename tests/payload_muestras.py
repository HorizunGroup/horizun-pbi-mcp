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

Ahora se recorren las 134 en dos escenarios deterministas y se **clasifica cada
una por lo que de verdad la bloquea**:

| Escenario | Qué hay | Qué se obtiene |
|---|---|---|
| `sin-proyecto` | nada abierto | el error de dominio que ve un cliente el primer día |
| `con-proyecto` | un `.pbip` sintético en `tmp_path` | la respuesta buena de todo lo que sabe leer del disco |

Y lo que queda fuera queda **con su motivo medido**, no supuesto:

* `requiere-argumentos`: el esquema rechaza `{}`. No la bloquea Desktop: le
  faltan argumentos válidos, que es trabajo, no un impedimento.
* `bloqueada-modelo-vivo`: contesta `no_active_model`. Su payload de ÉXITO sí
  necesita Desktop; su payload de error queda congelado igual.
* `no-se-ejecuta`: destructiva o de escritura sin proyecto. No se ejecuta a
  ciegas nada que pueda destruir.

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
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple

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
    (proj / "MyReport.pbip").write_text(json.dumps({
        "version": "1.0",
        "artifacts": [{"report": {"path": "MyReport.Report"}}]}), encoding="utf-8")
    return proj / "MyReport.pbip"


def _payload(salida: Any) -> Any:
    if isinstance(salida, dict) and "result" in salida:
        return salida["result"]
    return salida


def _llamar(mcp, nombre: str, args: Dict[str, Any]) -> Tuple[str, Any]:
    """`('rechazo', motivo)` o `('payload', dict)`. Nunca revienta."""
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        respuesta = asyncio.run(mcp.call_tool(nombre, args))
    except ToolError as exc:
        return "rechazo", str(exc)
    except Exception as exc:                                 # noqa: BLE001
        return "excepcion", f"{type(exc).__name__}: {exc}"
    return "payload", _payload(respuesta[1] if isinstance(respuesta, tuple)
                               else respuesta)


def _clasificar(nombre: str, riesgo: str, requeridos: int,
                por_escenario: Dict[str, Any]) -> Dict[str, Any]:
    """El estado de UNA tool, con la dependencia que la bloquea si la hay."""
    exitos = [e for e, p in por_escenario.items()
              if isinstance(p, dict) and p.get("ok") is True]
    errores = {e: p for e, p in por_escenario.items()
               if isinstance(p, dict) and p.get("ok") is False}

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
    if requeridos:
        return {"estado": "pendiente", "escenarios": [],
                "bloqueo": f"requiere-argumentos: {requeridos} parametro(s) "
                           "obligatorio(s) que hay que construir a mano"}
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
        from horizun_pbi_mcp.powerbi import desktop_discovery
        from horizun_pbi_mcp.services import project_state, tmdl_validate

        previos = (desktop_discovery._ports_from_processes,
                   desktop_discovery._workspace_port_files,
                   project_state.detect,
                   tmdl_validate.parse_with_tom)

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
             tmdl_validate.parse_with_tom) = previos
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
