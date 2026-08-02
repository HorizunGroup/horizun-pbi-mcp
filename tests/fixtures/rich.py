"""Fase E5 — fixture PBIR representativo, sintetico y anonimizado.

Por que hace falta
------------------
El fixture `minimal` tiene una pagina y dos visuales, sin interacciones, sin
marcadores y sin nada que remapear. Varias pruebas de release pasaban sin
comprobar lo que decian comprobar: la duplicacion no tenia referencias que
arrastrar, y la validacion no veia estructuras reales.

Que NO se copia de ningun proyecto real
---------------------------------------
Nada. Ni nombres comerciales, ni de personas, ni DAX, ni datos, ni rutas, ni
GUID reutilizados. Los identificadores son literales inventados y legibles
(`pgprincipal000000001`), las medidas se llaman `TotalAmount` y `Ratio Pct`, y
las tablas `Fact` y `Calendar`. Se toma la FORMA del PBIR de la documentacion
publica de Microsoft y de los esquemas oficiales, no de un informe concreto.

Que aporta sobre `minimal`
--------------------------
- dos paginas, con `visualInteractions` reales entre visuales;
- marcadores (`bookmarks/`) que referencian pagina y visuales;
- navegacion de pagina y drillthrough;
- un visual personalizado (tipo no reconocido por el catalogo oficial);
- una referencia rota, para el reparador;
- CRLF en todos los JSON, como escribe Power BI Desktop;
- una pagina que declara un esquema NO publicado upstream, para ejercitar el
  bloqueo por `schema_unavailable` sin depender del PB4.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict

from tests.fixtures import synthetic

BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/"
S_VISUAL = BASE + "definition/visualContainer/2.7.0/schema.json"
S_VISUAL_NO_PUBLICADO = BASE + "definition/visualContainer/2.10.0/schema.json"
S_PAGE = BASE + "definition/page/2.1.0/schema.json"
S_PAGES = BASE + "definition/pagesMetadata/1.1.0/schema.json"
S_BOOKMARK = BASE + "definition/bookmark/2.1.0/schema.json"
S_BOOKMARKS = BASE + "definition/bookmarksMetadata/1.0.0/schema.json"

#: Identificadores inventados, legibles y estables. Ningun GUID reutilizado.
PAGINA_PRINCIPAL = "pgprincipal000000001"
PAGINA_DETALLE = "pgdetalle00000000002"
PAGINA_FUTURA = "pgfutura000000000003"

VISUAL_KPI = "vskpi00000000000001"
VISUAL_GRAFICO = "vsgrafico000000002"
VISUAL_PERSONALIZADO = "vscustom0000000003"
VISUAL_ROTO = "vsroto000000000004"
VISUAL_DETALLE = "vsdetalle000000005"

#: Tipo de visual personalizado inventado: no existe en el catalogo oficial, y
#: por eso el validador de Microsoft lo marca como PBIR_VISUAL_TYPE_UNKNOWN.
TIPO_PERSONALIZADO = "ejemploCustomVisualSintetico"

MEDIDA_ROTA = "MedidaQueNoExiste"


def _escribir(ruta: Path, datos: Dict[str, Any]) -> None:
    """CRLF siempre: es lo que escribe Power BI, y lo que hace que una huella
    byte a byte signifique algo."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False),
                    encoding="utf-8", newline="\r\n")


def _normalizar_crlf(raiz: Path) -> None:
    """`minimal` escribe algunos JSON con LF.

    Este fixture representa un PBIR real, y Power BI escribe CRLF. Con LF, una
    huella byte a byte deja de significar nada: cualquier reescritura marcaria
    el archivo entero como cambiado.
    """
    lf, crlf = b"\n", b"\r\n"
    for f in list(raiz.rglob("*.json")) + list(raiz.rglob("*.pbir")):
        crudo = f.read_bytes()
        if lf in crudo and crlf not in crudo:
            f.write_bytes(crudo.replace(lf, crlf))


def _visual(vid: str, tipo: str, x: int, y: int, *, medida: str = "TotalAmount",
            schema: str = S_VISUAL, titulo: str = "") -> Dict[str, Any]:
    return {
        "$schema": schema,
        "name": vid,
        "position": {"x": x, "y": y, "width": 320, "height": 200, "z": 0},
        "visual": {
            "visualType": tipo,
            "query": {"queryState": {"Values": {"projections": [{
                "field": {"Measure": {
                    "Expression": {"SourceRef": {"Entity": "Fact"}},
                    "Property": medida}},
                "queryRef": f"Fact.{medida}",
                "nativeQueryRef": medida,
            }]}}},
        },
    }


def materialize(dest_dir: Path) -> Path:
    """Crea el proyecto representativo y devuelve la ruta del `.pbip`."""
    pbip = synthetic.materialize(dest_dir)
    raiz = pbip.parent
    rep = next(p for p in raiz.iterdir() if p.name.endswith(".Report"))
    definicion = rep / "definition"
    paginas = definicion / "pages"

    # Se parte de cero en pages/: el minimal aporta el modelo semantico y la
    # estructura, pero las paginas las define este fixture.
    shutil.rmtree(paginas, ignore_errors=True)

    # --- pagina principal: interacciones, custom visual y referencia rota ----
    _escribir(paginas / PAGINA_PRINCIPAL / "page.json", {
        "$schema": S_PAGE,
        "name": PAGINA_PRINCIPAL,
        "displayName": "Principal",
        "width": 1280, "height": 720,
        "displayOption": "FitToPage",
        "visualInteractions": [
            {"source": VISUAL_KPI, "target": VISUAL_GRAFICO, "type": "DataFilter"},
            {"source": VISUAL_GRAFICO, "target": VISUAL_KPI, "type": "NoFilter"},
            {"source": VISUAL_GRAFICO, "target": VISUAL_PERSONALIZADO,
             "type": "HighlightFilter"},
        ],
    })
    _escribir(paginas / PAGINA_PRINCIPAL / "visuals" / VISUAL_KPI / "visual.json",
              _visual(VISUAL_KPI, "card", 16, 16))
    _escribir(paginas / PAGINA_PRINCIPAL / "visuals" / VISUAL_GRAFICO / "visual.json",
              _visual(VISUAL_GRAFICO, "clusteredColumnChart", 360, 16))
    _escribir(paginas / PAGINA_PRINCIPAL / "visuals" / VISUAL_PERSONALIZADO / "visual.json",
              _visual(VISUAL_PERSONALIZADO, TIPO_PERSONALIZADO, 16, 240))
    # Referencia rota: apunta a una medida que no existe en el modelo.
    _escribir(paginas / PAGINA_PRINCIPAL / "visuals" / VISUAL_ROTO / "visual.json",
              _visual(VISUAL_ROTO, "card", 360, 240, medida=MEDIDA_ROTA))

    # --- pagina de detalle: destino de drillthrough y navegacion ------------
    _escribir(paginas / PAGINA_DETALLE / "page.json", {
        "$schema": S_PAGE,
        "name": PAGINA_DETALLE,
        "displayName": "Detalle",
        "width": 1280, "height": 720,
        "displayOption": "FitToPage",
    })
    _escribir(paginas / PAGINA_DETALLE / "visuals" / VISUAL_DETALLE / "visual.json",
              _visual(VISUAL_DETALLE, "tableEx", 16, 16))

    # --- pagina con esquema NO publicado upstream --------------------------
    # Ejercita el bloqueo por schema_unavailable sin depender del PB4.
    _escribir(paginas / PAGINA_FUTURA / "page.json", {
        "$schema": S_PAGE,
        "name": PAGINA_FUTURA,
        "displayName": "Futura",
        "width": 1280, "height": 720,
        "displayOption": "FitToPage",
    })
    _escribir(paginas / PAGINA_FUTURA / "visuals" / "vsfuturo000000000006" / "visual.json",
              _visual("vsfuturo000000000006", "card", 16, 16,
                      schema=S_VISUAL_NO_PUBLICADO))

    _escribir(paginas / "pages.json", {
        "$schema": S_PAGES,
        "pageOrder": [PAGINA_PRINCIPAL, PAGINA_DETALLE, PAGINA_FUTURA],
        "activePageName": PAGINA_PRINCIPAL,
    })

    # --- marcadores: referencian pagina y visuales -------------------------
    marcadores = definicion / "bookmarks"
    _normalizar_crlf(rep)

    _escribir(marcadores / "bookmarks.json", {
        "$schema": S_BOOKMARKS,
        # SingleBookmarkMetadata solo admite `name` (additionalProperties:
        # false). El nombre visible va en el propio .bookmark.json.
        "items": [{"name": "bkvistainicial001"}],
    })
    _escribir(marcadores / "bkvistainicial001.bookmark.json", {
        "$schema": S_BOOKMARK,
        "name": "bkvistainicial001",
        "displayName": "Vista inicial",
        "explorationState": {
            "version": "1.0",
            "activeSection": PAGINA_PRINCIPAL,
            # `singleVisual` solo admite lo que define SingleVisualConfigState;
            # inventar una propiedad hace fallar la validacion oficial.
            "sections": {PAGINA_PRINCIPAL: {"visualContainers": {
                VISUAL_KPI: {"singleVisual": {"visualType": "card"}},
            }}},
        },
    })
    return pbip


def con_drillthrough(pbip: Path) -> None:
    """Anade drillthrough de la pagina principal a la de detalle."""
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    ruta = rep / "definition" / "pages" / PAGINA_DETALLE / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["pageBinding"] = {"type": "Drillthrough", "name": "drilldetalle0001"}
    _escribir(ruta, datos)


def con_referencia_no_remapeable(pbip: Path) -> str:
    """Introduce una referencia bajo una clave desconocida.

    Sirve para comprobar que la duplicacion BLOQUEA en vez de copiar una
    referencia que quedaria apuntando a la pagina original.
    """
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    ruta = rep / "definition" / "pages" / PAGINA_PRINCIPAL / "page.json"
    datos = json.loads(ruta.read_text(encoding="utf-8-sig"))
    datos["estructuraDesconocida"] = {"apuntaA": VISUAL_KPI}
    _escribir(ruta, datos)
    return VISUAL_KPI
