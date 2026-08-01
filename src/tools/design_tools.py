"""Tools de la capa de diseno y del punto de entrada.

Dos huecos distintos que se resuelven en el mismo sitio porque comparten la
misma idea: entre tener las piezas y saber usarlas hay una distancia, y esa
distancia tambien es trabajo del servidor.

`pbi_start_here` responde «¿y ahora que?» mirando el estado real.
`pbi_compose_page` responde «¿como lo coloco?» sin que nadie tenga que decidir
margenes a ojo.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session
from services import design, guide, page_spec
from tools._common import guard, guard_mutation
from tools.visual_tools import _model_data


def register(mcp) -> None:

    # ------------------------------------------------------- punto de entrada --
    @mcp.tool()
    def pbi_start_here(request_id: str = "") -> Dict[str, Any]:
        """Por donde empezar. Mira el estado real y dice los siguientes pasos.

        Ciento doce tools con buen nombre siguen siendo ciento doce tools.
        Esta responde «¿y ahora que?» con tres o cuatro pasos concretos, cada
        uno con el nombre exacto de la tool y **por que** toca ahora: si hay
        proyecto activo, si tiene modelo o solo informe, si esta vacio, y si
        Power BI Desktop lo tiene abierto —que impide escribir el TMDL—.

        Devuelve tambien `common_tasks`: tareas frecuentes y la secuencia de
        tools que las resuelve.

        No escribe nada. Empieza por aqui cuando no sepas que sigue.
        """
        def _impl():
            return guide.situacion(get_session())
        return guard(_impl)

    # -------------------------------------------------------- capa de diseno --
    @mcp.tool()
    def pbi_list_design_systems(request_id: str = "") -> Dict[str, Any]:
        """Sistemas de diseno disponibles: para que sirve cada uno y que trae.

        Un sistema decide a la vez el tema (color y tipografia, con paletas ya
        verificadas contra daltonismo), el tamano del lienzo, la rejilla sobre
        la que se coloca todo y la escala de texto. Son la misma decision: un
        tablero de sala se lee a cuatro metros y uno en PDF a cuarenta
        centimetros, y eso no es el mismo diseno con otro color.

        Eligelo ANTES de la primera pagina; cambiarlo despues obliga a
        recolocarlo todo.
        """
        def _impl():
            return {"systems": design.list_systems(),
                    "default": "informe"}
        return guard(_impl)

    @mcp.tool()
    def pbi_apply_design_system(system: str,
                                request_id: str = "") -> Dict[str, Any]:
        """Aplica el sistema al informe y devuelve su rejilla.

        Escribe el tema y lo declara en `report.json`. Devuelve el lienzo, la
        rejilla (columnas, margen y medianil) y la escala tipografica, para que
        lo que se coloque a mano despues use las mismas guias que
        `pbi_compose_page`.

        Escribe en el informe (PBIR): conviene tener el proyecto CERRADO en
        Power BI Desktop.
        """
        def _impl():
            active = get_session().require_active_pbip()
            return design.aplicar(active, system)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_compose_page(system: str,
                         title: str,
                         subtitle: str = "",
                         kpis: Optional[List[Any]] = None,
                         hero: Optional[Dict[str, Any]] = None,
                         supports: Optional[List[Dict[str, Any]]] = None,
                         detail: Optional[Dict[str, Any]] = None,
                         page_name: str = "",
                         dry_run: bool = False,
                         request_id: str = "") -> Dict[str, Any]:
        """Compone una pagina entera sobre la rejilla del sistema de diseno.

        Se describe la INTENCION y el servidor decide la geometria:

        - `title` / `subtitle`: banda de cabecera, con el tamano y el color del
          sistema y la altura minima que el texto necesita para no cortarse.
        - `kpis`: lista de campos (`"[Medida]"` o `{"field":..., "title":...}`).
          Se reparten la fila entera sin dejar huecos.
        - `hero`: el grafico protagonista.
          `{"type":"lineChart", "category":"T[C]", "values":["[M]"], "title":...}`.
        - `supports`: los que van apilados a su derecha, mismo formato.
        - `detail`: tabla al pie. `{"values": [...], "title": ...}`.

        La composicion es siempre la misma de arriba abajo, a proposito: la
        coherencia entre paginas sale de que ninguna pueda inventarse su propio
        orden. Si algo no cabe se dice con la cuenta hecha, en vez de encogerlo
        hasta que no se lea.

        `dry_run=true` devuelve el spec con todas las posiciones y no escribe.
        Aplica el mismo camino que `pbi_apply_page_spec`, asi que pasa por la
        misma validacion y la misma transaccion.
        """
        def _impl():
            active = get_session().require_active_pbip()
            spec = design.componer(
                system, title=title, subtitle=subtitle, kpis=kpis, hero=hero,
                supports=supports, detail=detail, page_name=page_name)
            if dry_run:
                return {"dry_run": True, "system": system, "spec": spec,
                        "visual_count": len(spec["visuals"])}
            compilado = page_spec.compile_spec(active, spec, _model_data())
            resultado = page_spec.apply_spec(active, compilado)
            return {"dry_run": False, "system": system, "spec": spec,
                    **resultado}

        return guard(_impl) if dry_run else guard_mutation(_impl)
