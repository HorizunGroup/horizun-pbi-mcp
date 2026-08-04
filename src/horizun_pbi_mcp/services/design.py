"""Capa de diseño: una sola fuente para el color, la tipografia y la rejilla.

El problema que cierra
----------------------
Habia dos mitades que no se hablaban. `pbip.theme` sabe de color y de clases de
texto —con paletas verificadas contra daltonismo— y no sabe nada de donde va
cada cosa. `pbip.layout_engine` calcula posiciones con `ceil(sqrt(n))` y no sabe
de que color es el fondo sobre el que las pone. Entre las dos no habia rejilla,
ni margenes constantes, ni banda de titulo, ni escala tipografica aplicada a la
PAGINA: solo a los visuales.

El resultado se notaba: paginas correctas y sin criterio. Cada una con sus
margenes, los titulos puestos a ojo, y ningun parentesco entre la primera y la
quinta.

Un **sistema de diseño** posee las dos mitades a la vez: de que tema saca el
color, sobre que rejilla se coloca todo, que alturas tienen las bandas y que
tamaños tiene cada nivel de texto. `componer()` traduce una intencion —«un
titulo, cuatro indicadores, un grafico protagonista y dos de apoyo»— en un spec
de pagina con posiciones exactas.

Por que no inventa paletas
--------------------------
El color sale de `pbip.theme.PRESETS`, que ya esta verificado (banda de
luminosidad, suelo de croma, separacion bajo protanopia/deuteranopia/
tritanopia). Duplicarlo aqui seria tener dos verdades sobre el mismo color, y
una de las dos envejeceria mal.

Lo que esto NO hace
-------------------
No mira el resultado. Coloca sobre una rejilla y elige tamaños coherentes, que
es lo que se puede comprobar sin renderizar; que la pagina se VEA bien sigue
exigiendo abrirla.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.pbip import theme as theme_mod
from horizun_pbi_mcp.pbip import visual_factory
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("design")


class DesignError(PowerBIMCPError):
    code = "design_error"


#: Los sistemas ofrecidos. Cada uno resuelve un escenario de uso distinto, y
#: eso decide TODO lo demas: el tema, el tamaño del lienzo y la escala de texto.
#: Un tablero de sala se lee a cuatro metros; uno que se exporta a PDF, a
#: cuarenta centimetros. No es el mismo diseño con otro color.
SISTEMAS: Dict[str, Dict[str, Any]] = {
    "sala": {
        "titulo": "Sala de control",
        "para": ("Pantalla grande, luz baja, se lee de lejos. Lienzo 1920x1080 "
                 "y tipografia grande."),
        "theme": "control_room",
        "canvas": {"width": 1920, "height": 1080},
        "grid": {"columns": 12, "margin": 40, "gutter": 20},
        "bandas": {"kpi": 168, "apoyo": 300},
        # `kpi` es el numero de la tarjeta, y en una sala es el texto que MAS
        # se lee: sin fijarlo sale al tamano por defecto —el mas pequeno de la
        # pagina— y no se lee a cuatro metros. Se descubrio abriendo, no
        # validando: el JSON era perfecto.
        "tipografia": {"titulo": 36, "subtitulo": 16, "seccion": 13, "kpi": 44},
    },
    "informe": {
        "titulo": "Informe para repartir",
        "para": ("Se exporta a PDF y se lee de cerca. Lienzo 1280x720 y "
                 "tipografia de lectura."),
        "theme": "claro",
        "canvas": {"width": 1280, "height": 720},
        "grid": {"columns": 12, "margin": 24, "gutter": 16},
        "bandas": {"kpi": 112, "apoyo": 200},
        "tipografia": {"titulo": 24, "subtitulo": 12, "seccion": 11, "kpi": 28},
    },
    "foco": {
        "titulo": "Estado primero",
        "para": ("Los datos en una rampa y el color saturado reservado al "
                 "semaforo: lo unico que salta es lo que esta mal."),
        "theme": "semaforo",
        "canvas": {"width": 1280, "height": 720},
        "grid": {"columns": 12, "margin": 24, "gutter": 16},
        "bandas": {"kpi": 112, "apoyo": 200},
        "tipografia": {"titulo": 24, "subtitulo": 12, "seccion": 11, "kpi": 28},
    },
}

#: Cuantas columnas ocupa el protagonista cuando hay visuales de apoyo al lado.
_COLUMNAS_HERO = 8


# --------------------------------------------------------------- consulta ----
def list_systems() -> List[Dict[str, Any]]:
    """Sistemas disponibles, para que sirve cada uno y que color trae."""
    salida = []
    for clave, s in SISTEMAS.items():
        paleta = theme_mod.PRESETS[s["theme"]]["tema"]
        salida.append({
            "system": clave, "title": s["titulo"], "for": s["para"],
            "theme": s["theme"], "canvas": dict(s["canvas"]),
            "grid": dict(s["grid"]),
            "typography": dict(s["tipografia"]),
            "background": paleta["background"],
            "foreground": paleta["foreground"],
            "data_colors": list(paleta["dataColors"]),
            "status_colors": {k: paleta[k] for k in theme_mod.ESTADO},
        })
    return salida


def tokens(system: str) -> Dict[str, Any]:
    """Todos los tokens del sistema, color incluido. Copia: no se comparte estado."""
    if system not in SISTEMAS:
        raise DesignError(
            f"Sistema de diseño desconocido: '{system}'. "
            f"Disponibles: {sorted(SISTEMAS)}.",
            details={"available": sorted(SISTEMAS)})
    s = copy.deepcopy(SISTEMAS[system])
    paleta = theme_mod.PRESETS[s["theme"]]["tema"]
    s["color"] = {
        "background": paleta["background"],
        "foreground": paleta["foreground"],
        "accent": paleta["tableAccent"],
        "line": paleta.get("secondaryBackground"),
        "data": list(paleta["dataColors"]),
        **{k: paleta[k] for k in theme_mod.ESTADO},
    }
    return s


# ---------------------------------------------------------------- rejilla ----
def columna(system: str, inicio: int, ancho: int) -> Dict[str, float]:
    """Traduce «de la columna `inicio`, `ancho` columnas» a pixeles.

    Es la unica cuenta que decide un ancho en todo el modulo. Que este en un
    solo sitio es justo lo que hace que dos paginas del mismo sistema esten
    alineadas entre si sin que nadie lo vigile.
    """
    s = SISTEMAS.get(system)
    if s is None:
        raise DesignError(f"Sistema de diseño desconocido: '{system}'.")
    g, lienzo = s["grid"], s["canvas"]
    n = g["columns"]
    if inicio < 0 or ancho < 1 or inicio + ancho > n:
        raise DesignError(
            f"La rejilla de '{system}' tiene {n} columnas; se pidio de "
            f"{inicio} a {inicio + ancho}.",
            details={"columns": n, "start": inicio, "span": ancho})
    util = lienzo["width"] - 2 * g["margin"]
    ancho_col = (util - (n - 1) * g["gutter"]) / n
    paso = ancho_col + g["gutter"]
    return {"x": round(g["margin"] + inicio * paso),
            "width": round(ancho * ancho_col + (ancho - 1) * g["gutter"])}


def _reparto(total_columnas: int, cuantos: int) -> List[Dict[str, int]]:
    """Reparte `total_columnas` entre `cuantos` bloques sin dejar huecos.

    El resto se da a los primeros bloques en vez de dejar un margen suelto a la
    derecha: una fila de indicadores que no llega al borde se ve como un error,
    no como una decision.
    """
    base, resto = divmod(total_columnas, cuantos)
    if base == 0:
        raise DesignError(
            f"No caben {cuantos} bloques en {total_columnas} columnas. "
            "Reduce la cantidad o repartelos en dos filas.",
            details={"columns": total_columnas, "blocks": cuantos})
    salida, cursor = [], 0
    for i in range(cuantos):
        ancho = base + (1 if i < resto else 0)
        salida.append({"start": cursor, "span": ancho})
        cursor += ancho
    return salida


# -------------------------------------------------------------- composicion --
def tinta(system: str, palette: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Colores de texto que hay que usar. `palette` gana sobre la del sistema.

    El color es del INFORME y la geometria de la PAGINA: un informe solo puede
    declarar un tema. Componer una pagina con un sistema distinto al tema
    aplicado y escribir el color del sistema es lo que pintaba el titulo en
    #0B0B0B sobre un fondo #1A1A19 —contraste 1:1, invisible—, y no lo veia ni
    el validador oficial ni las pruebas de geometria. Solo se ve abriendo.
    """
    base = theme_mod.PRESETS[SISTEMAS[system]["theme"]]["tema"]
    fuerte = (palette or {}).get("foreground") or base["foreground"]
    suave = ((palette or {}).get("label")
             or base["textClasses"]["label"]["color"])
    return {"strong": str(fuerte), "muted": str(suave)}


def paleta_del_informe(active: Any) -> Optional[Dict[str, str]]:
    """Colores del tema que el informe declara AHORA. None si no hay ninguno.

    Es lo que permite que una pagina compuesta con un sistema se lea sobre el
    tema que el informe tenga puesto, sea cual sea.
    """
    from horizun_pbi_mcp.utils.json_utils import read_json

    if not getattr(active, "report_dir", None):
        return None
    report_dir = Path(active.report_dir)
    informe = report_dir / "definition" / "report.json"
    if not informe.exists():
        return None
    try:
        nombre = ((read_json(informe).get("themeCollection") or {})
                  .get("customTheme") or {}).get("name")
        if not nombre:
            return None
        tema = read_json(report_dir / "StaticResources" / "RegisteredResources"
                         / nombre)
    except Exception as exc:                                 # noqa: BLE001
        log.info("No se pudo leer el tema aplicado: %s", exc)
        return None
    etiqueta = ((tema.get("textClasses") or {}).get("label") or {}).get("color")
    return {"theme_file": nombre, "foreground": tema.get("foreground"),
            "label": etiqueta or tema.get("foreground"),
            "background": tema.get("background")}


def _texto(system: str, texto: str, nivel: str, y: float, *,
           inicio: int = 0, ancho: Optional[int] = None,
           id_: str = "", palette: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    """Un bloque de texto del sistema: tamaño, color y altura, ya resueltos.

    La altura no se elige: es el piso que exige el tamaño de fuente. Por debajo
    Power BI mete barra de scroll y corta el texto, y eso ningun validador de
    esquema lo ve.
    """
    s = SISTEMAS[system]
    tam = s["tipografia"][nivel]
    colores = tinta(system, palette)
    color = colores["strong"] if nivel == "titulo" else colores["muted"]
    geo = columna(system, inicio, ancho if ancho is not None else s["grid"]["columns"])
    return {
        "id": id_ or f"texto_{nivel}",
        "type": "textbox",
        "position": {**geo, "y": round(y),
                     "height": visual_factory.piso_de_texto(tam)},
        "options": {"text": texto, "font_size": tam, "color": color,
                    "bold": nivel == "titulo"},
    }


def _campo(entrada: Any) -> str:
    """Acepta `"[Medida]"` o `{"field": ..., "title": ...}`."""
    if isinstance(entrada, str):
        return entrada
    if isinstance(entrada, dict) and entrada.get("field"):
        return str(entrada["field"])
    raise DesignError(
        f"Se esperaba un campo ('Tabla[Campo]' o '[Medida]') o un objeto con "
        f"'field'; se recibio {entrada!r}.")


def _rotulo(entrada: Any, defecto: str) -> str:
    if isinstance(entrada, dict) and entrada.get("title"):
        return str(entrada["title"])
    if isinstance(entrada, str):
        # `Ventas[Importe Total]` -> `Importe Total`: el nombre de la tabla no
        # aporta nada encima de una tarjeta y roba sitio al numero.
        return entrada[entrada.index("[") + 1:-1] if "[" in entrada else entrada
    return defecto


def componer(system: str, *, title: str, subtitle: str = "",
             kpis: Optional[List[Any]] = None,
             hero: Optional[Dict[str, Any]] = None,
             supports: Optional[List[Dict[str, Any]]] = None,
             detail: Optional[Dict[str, Any]] = None,
             page_name: str = "",
             palette: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Traduce una intencion en un spec de pagina completo y colocado.

    La composicion es siempre la misma, de arriba abajo: banda de titulo,
    fila de indicadores, protagonista con sus apoyos al lado, y detalle al pie.
    Es deliberadamente rigida: la coherencia entre paginas sale de que ninguna
    pueda inventarse su propio orden.

    El spec que devuelve lleva TODAS las posiciones puestas, asi que
    `page_spec` no vuelve a calcular layout y lo que se ve es lo que se pidio.
    """
    s = tokens(system)
    grid, lienzo = s["grid"], s["canvas"]
    columnas, margen, medianil = grid["columns"], grid["margin"], grid["gutter"]

    visuales: List[Dict[str, Any]] = []
    y = float(margen)

    # --- banda de titulo -----------------------------------------------------
    cabecera = _texto(system, title, "titulo", y, id_="titulo", palette=palette)
    visuales.append(cabecera)
    y += cabecera["position"]["height"]
    if subtitle:
        sub = _texto(system, subtitle, "subtitulo", y, id_="subtitulo",
                     palette=palette)
        visuales.append(sub)
        y += sub["position"]["height"]
    y += medianil

    # --- fila de indicadores -------------------------------------------------
    kpis = list(kpis or [])
    if kpis:
        alto = s["bandas"]["kpi"]
        for i, (bloque, kpi) in enumerate(zip(_reparto(columnas, len(kpis)), kpis)):
            geo = columna(system, bloque["start"], bloque["span"])
            visuales.append({
                "id": f"kpi_{i}", "type": "card",
                "title": _rotulo(kpi, f"Indicador {i + 1}"),
                "position": {**geo, "y": round(y), "height": alto},
                "fields": {"values": [_campo(kpi)]},
                # El numero al tamano del sistema, y sin la etiqueta de
                # categoria: repite el titulo que ya lleva la tarjeta encima y
                # salia MAS grande que el propio dato.
                "options": {"value_font_size": s["tipografia"]["kpi"],
                            "bold_value": True,
                            "show_category_label": False},
            })
        y += alto + medianil

    # --- protagonista y apoyos ----------------------------------------------
    supports = list(supports or [])
    alto_detalle = s["bandas"]["apoyo"] if detail else 0
    resto = lienzo["height"] - margen - y - (alto_detalle + medianil if detail else 0)
    if hero and resto < 120:
        raise DesignError(
            "No queda alto para el grafico protagonista: quedan "
            f"{resto:.0f}px y hacen falta 120. Quita indicadores, el detalle, "
            "o usa un lienzo mas alto.",
            details={"remaining_height": round(resto), "canvas": lienzo})

    if hero:
        ancho_hero = _COLUMNAS_HERO if supports else columnas
        geo = columna(system, 0, ancho_hero)
        visuales.append(_grafico(hero, "hero", geo, y, resto))
        if supports:
            libre = columnas - _COLUMNAS_HERO
            alto_cada = (resto - (len(supports) - 1) * medianil) / len(supports)
            if alto_cada < 90:
                raise DesignError(
                    f"{len(supports)} visuales de apoyo no caben en "
                    f"{resto:.0f}px de alto. Con {len(supports)} hacen falta "
                    f"{round(len(supports) * 90 + (len(supports) - 1) * medianil)}px.",
                    details={"supports": len(supports),
                             "available_height": round(resto)})
            geo_apoyo = columna(system, _COLUMNAS_HERO, libre)
            for i, apoyo in enumerate(supports):
                visuales.append(_grafico(
                    apoyo, f"apoyo_{i}", geo_apoyo,
                    y + i * (alto_cada + medianil), alto_cada))
        y += resto + medianil

    # --- detalle al pie ------------------------------------------------------
    if detail:
        geo = columna(system, 0, columnas)
        campos = [_campo(c) for c in (detail.get("values") or [])]
        if not campos:
            raise DesignError("El detalle necesita al menos un campo en 'values'.")
        visuales.append({
            "id": "detalle", "type": "table",
            "title": detail.get("title") or "Detalle",
            "position": {**geo, "y": round(y), "height": round(alto_detalle)},
            "fields": {"values": campos},
        })

    return {
        "schema_version": "1.0",
        "page": {"name": page_name or title, "displayName": title,
                 "width": lienzo["width"], "height": lienzo["height"]},
        "visuals": visuales,
    }


def _grafico(descripcion: Dict[str, Any], id_: str, geo: Dict[str, float],
             y: float, alto: float) -> Dict[str, Any]:
    """Un visual con datos, colocado en la celda que le toca."""
    if not isinstance(descripcion, dict):
        raise DesignError(
            f"Se esperaba un objeto con 'type', 'category' y 'values'; se "
            f"recibio {descripcion!r}.")
    valores = descripcion.get("values") or []
    if not valores:
        raise DesignError(f"El visual '{id_}' necesita al menos un campo en "
                          "'values'.")
    campos: Dict[str, Any] = {"values": [_campo(v) for v in valores]}
    if descripcion.get("category"):
        campos["category"] = [_campo(descripcion["category"])]
    if descripcion.get("legend"):
        campos["legend"] = [_campo(descripcion["legend"])]
    return {
        "id": id_,
        "type": descripcion.get("type") or "columnChart",
        "title": descripcion.get("title") or "",
        "position": {**geo, "y": round(y), "height": round(alto)},
        "fields": campos,
    }


# ------------------------------------------------------------------ aplicar --
def aplicar(active: Any, system: str) -> Dict[str, Any]:
    """Deja el informe con el tema del sistema, y devuelve la rejilla.

    Aplicar el sistema es aplicar su tema: son la misma decision. Devolver la
    rejilla junto al tema es lo que permite que quien coloque algo a mano lo
    haga sobre las mismas guias.
    """
    s = tokens(system)
    resultado = theme_mod.apply_theme(active, theme_mod.build_theme(s["theme"]))
    log.info("Sistema de diseño '%s' aplicado (tema %s).", system, s["theme"])
    return {"system": system, "title": s["titulo"], "canvas": s["canvas"],
            "grid": s["grid"], "typography": s["tipografia"],
            "color": s["color"], **resultado}
