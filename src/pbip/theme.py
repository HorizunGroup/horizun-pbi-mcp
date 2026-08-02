"""Temas de informe: paletas validadas y su registro en el PBIR.

Un tema de Power BI es un JSON que vive en `StaticResources/RegisteredResources/`
y al que `report.json` apunta desde `themeCollection.customTheme`. Registrar el
archivo sin declararlo en `resourcePackages` no basta: Desktop lo ignora en
silencio, asi que aqui se hacen las tres cosas o ninguna.

Sobre las paletas: el orden de los colores NO es cosmetico. Es el mecanismo que
garantiza que dos series contiguas se distingan tambien con daltonismo, asi que
los `dataColors` se declaran en un orden concreto y no se barajan. Los colores de
estado (`good`/`neutral`/`bad`) son fijos en los tres temas: el semaforo de una
auditoria significa lo mismo se pinte donde se pinte, y un color de estado nunca
se reutiliza como color de serie.

Las paletas se verificaron con el validador de la skill `dataviz` (banda de
luminosidad, suelo de croma, separacion bajo protanopia/deuteranopia/tritanopia,
suelo de vision normal y contraste contra la superficie).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError
from services import paths as safe_paths
from services import txn as txn_service
from utils.json_utils import read_json

log = get_logger("theme")

#: Colores de estado. Fijos, nunca tematizados: son significado, no estilo.
ESTADO = {"good": "#0CA30C", "neutral": "#FAB219", "bad": "#D03B3B"}
#: Version de tema que se declara al importar. PBIR la exige.
VERSION_AL_IMPORTAR = {"visual": "2.4.0", "page": "2.3.0", "report": "3.0.0"}
#: En report/2.0.0 la misma propiedad era una sola cadena; desde 3.x es el
#: objeto de tres componentes de arriba. La escritura transaccional valida el
#: esquema completo y no puede inventar una forma unica para ambas versiones.
VERSION_AL_IMPORTAR_V2 = "1.0.0"

#: Paleta categorica para superficie clara. Peor par contiguo: ΔE 9.1 protan,
#: 19.6 vision normal. Tres colores quedan bajo 3:1 contra el fondo, asi que
#: los visuales que los usen necesitan etiqueta visible.
_CATEGORICA_CLARA = ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100",
                     "#E87BA4", "#008300", "#4A3AA7", "#E34948"]
#: La misma familia de tonos re-escalonada para superficie oscura (todos >= 3:1).
_CATEGORICA_OSCURA = ["#3987E5", "#D95926", "#199E70", "#C98500",
                      "#D55181", "#008300", "#9085E9", "#E66767"]
#: Rampa secuencial de un solo tono, para magnitud (mapas de calor, matrices).
_SECUENCIAL_AZUL = ["#CDE2FB", "#9EC5F4", "#6DA7EC", "#3987E5",
                    "#256ABF", "#184F95", "#0D366B"]


class ThemeError(PowerBIMCPError):
    code = "theme_error"


def _texto(tamano: int, color: str) -> Dict[str, Any]:
    """Clase de texto del tema.

    Ojo con `color`: en `textClasses` va un hex PLANO. La forma
    `{"solid": {"color": ...}}` que usan los `visualStyles` aqui es invalida, y
    Power BI la rechaza (PBIR_THEME_TEXT_COLOR_INVALID).
    """
    return {"fontSize": tamano, "color": color}


def _estilos(fondo: str, tinta: str, tinta2: str,
             linea: str) -> Dict[str, Any]:
    """Estilos de visual comunes: rejilla discreta y texto con tokens de texto.

    La rejilla y los ejes van en un tono apagado a proposito: compiten con los
    datos si se pintan al mismo peso. Y el texto NUNCA lleva el color de la
    serie; la identidad la carga la marca de color que tiene al lado.
    """
    return {
        "*": {
            "*": {
                "background": [{"show": True, "color": {"solid": {"color": fondo}},
                                "transparency": 0}],
                "border": [{"show": False}],
                "title": [{"show": True, "fontColor": {"solid": {"color": tinta}},
                           "fontSize": 12, "alignment": "left"}],
                "labels": [{"color": {"solid": {"color": tinta2}}, "fontSize": 9}],
                "categoryAxis": [{"show": True, "gridlineShow": False,
                                  "labelColor": {"solid": {"color": tinta2}},
                                  "fontSize": 9}],
                "valueAxis": [{"show": True,
                               "gridlineColor": {"solid": {"color": linea}},
                               "gridlineStyle": "solid",
                               "labelColor": {"solid": {"color": tinta2}},
                               "fontSize": 9}],
                "legend": [{"show": True, "position": "Top",
                            "labelColor": {"solid": {"color": tinta2}},
                            "fontSize": 9}],
            }
        },
        "page": {"*": {"background": [{"color": {"solid": {"color": fondo}},
                                       "transparency": 0}],
                       "outspace": [{"color": {"solid": {"color": fondo}}}]}},
    }


def _tema(nombre: str, fondo: str, tinta: str, tinta2: str, linea: str,
          paleta: List[str], acento: str) -> Dict[str, Any]:
    return {
        "name": nombre,
        "dataColors": list(paleta),
        **ESTADO,
        "background": fondo,
        "foreground": tinta,
        "tableAccent": acento,
        "secondaryBackground": linea,
        "textClasses": {
            "title": _texto(20, tinta),
            "header": _texto(13, tinta),
            "label": _texto(10, tinta2),
            "callout": _texto(34, tinta),
        },
        "visualStyles": _estilos(fondo, tinta, tinta2, linea),
    }


#: Los tres temas ofrecidos. Cada uno resuelve un escenario distinto de uso.
PRESETS: Dict[str, Dict[str, Any]] = {
    "control_room": {
        "titulo": "Control Room (oscuro)",
        "para": "Pantalla grande en sala de coordinacion, con luz baja.",
        "tema": _tema("Control Room", "#1A1A19", "#FFFFFF", "#C3C2B7", "#33332F",
                      _CATEGORICA_OSCURA, "#3987E5"),
    },
    "claro": {
        "titulo": "Claro corporativo",
        "para": "Exportar a PDF y repartir. Imprime bien y gasta poca tinta.",
        "tema": _tema("Claro corporativo", "#FCFCFB", "#0B0B0B", "#52514E",
                      "#E3E2DD", _CATEGORICA_CLARA, "#2A78D6"),
    },
    "semaforo": {
        "titulo": "Semaforo primero",
        "para": ("Datos en una sola rampa azul y el color saturado reservado al "
                 "estado: lo unico que salta a la vista es lo que esta mal."),
        "tema": _tema("Semaforo primero", "#F5F4F1", "#0B0B0B", "#52514E",
                      "#DEDCD6", _SECUENCIAL_AZUL, "#256ABF"),
    },
}


def list_presets() -> List[Dict[str, Any]]:
    """Temas disponibles, con para que sirve cada uno y su paleta."""
    return [{"preset": clave, "title": v["titulo"], "for": v["para"],
             "background": v["tema"]["background"],
             "data_colors": v["tema"]["dataColors"],
             "status_colors": {k: v["tema"][k] for k in ESTADO}}
            for clave, v in PRESETS.items()]


def build_theme(preset: str = "control_room",
                name: Optional[str] = None,
                data_colors: Optional[List[str]] = None) -> Dict[str, Any]:
    """Devuelve el JSON del tema. `data_colors` sustituye la paleta categorica."""
    if preset not in PRESETS:
        raise ThemeError(
            f"Tema desconocido: '{preset}'. Disponibles: {sorted(PRESETS)}.",
            details={"available": sorted(PRESETS)})
    tema = copy.deepcopy(PRESETS[preset]["tema"])
    if data_colors:
        malos = [c for c in data_colors
                 if not (isinstance(c, str) and c.startswith("#") and len(c) in (4, 7))]
        if malos:
            raise ThemeError(
                f"Colores no validos (se esperaba #RRGGBB): {malos}.")
        tema["dataColors"] = list(data_colors)
        log.info("Paleta sustituida por la del llamante (%s colores); el orden "
                 "deja de estar verificado contra daltonismo.", len(data_colors))
    if name:
        tema["name"] = name
    return tema


def _paquete_recursos(paquetes: Any, archivo: str) -> List[Dict[str, Any]]:
    """Declara el tema dentro de `RegisteredResources`, creandolo si no existe."""
    paquetes = list(paquetes or [])
    item = {"name": archivo, "path": archivo, "type": "CustomTheme"}
    for paquete in paquetes:
        if paquete.get("type") == "RegisteredResources":
            items = [i for i in (paquete.get("items") or [])
                     if i.get("path") != archivo]
            items.append(item)
            paquete["items"] = items
            return paquetes
    paquetes.append({"name": "RegisteredResources",
                     "type": "RegisteredResources", "items": [item]})
    return paquetes


def apply_theme(active: ActivePbip, tema: Dict[str, Any],
                file_name: Optional[str] = None) -> Dict[str, Any]:
    """Escribe el tema y lo deja declarado en `report.json`.

    Devuelve las rutas tocadas. Si el informe ya tenia un tema propio, se
    sustituye: un informe solo puede declarar un `customTheme`.
    """
    if not active.report_dir:
        raise ThemeError("El proyecto no tiene carpeta .Report.")
    # El nombre declarado DENTRO del tema tiene que ser IGUAL al del archivo,
    # extension incluida, y al que se pone en `themeCollection.customTheme`.
    # Los tres han de coincidir o Power BI avisa (PBIR_THEME_FILE_NAME_MISMATCH);
    # se comprobo probando las dos variantes contra el validador oficial.
    tema = dict(tema)
    if file_name:
        nombre_archivo = file_name if file_name.endswith(".json") else file_name + ".json"
    else:
        saneado = "".join(c if c.isalnum() or c in "-_" else "_"
                          for c in str(tema.get("name", "Tema"))).strip("_") or "Tema"
        nombre_archivo = saneado + ".json"
    tema["name"] = nombre_archivo

    safe_paths.safe_identifier(nombre_archivo, kind="nombre de archivo de tema")
    report_dir = Path(active.report_dir)
    recursos_dir = report_dir / "StaticResources" / "RegisteredResources"
    destino = safe_paths.safe_join(recursos_dir, nombre_archivo,
                                   kind="ruta de tema del informe")

    informe_path = report_dir / "definition" / "report.json"
    if not informe_path.exists():
        raise ThemeError(f"No se encontro report.json en {informe_path}.")
    informe = read_json(informe_path)

    coleccion = informe.get("themeCollection")
    if not isinstance(coleccion, dict):
        coleccion = {}
    anterior = (coleccion.get("customTheme") or {}).get("name")
    version = ((coleccion.get("baseTheme") or {}).get("reportVersionAtImport")
               or (coleccion.get("customTheme") or {}).get("reportVersionAtImport"))
    if version is None:
        esquema = str(informe.get("$schema") or "")
        version = (VERSION_AL_IMPORTAR_V2 if "/report/2.0.0/" in esquema
                   else VERSION_AL_IMPORTAR)
    coleccion["customTheme"] = {"name": nombre_archivo,
                                "type": "RegisteredResources",
                                "reportVersionAtImport": version}
    informe["themeCollection"] = coleccion
    informe["resourcePackages"] = _paquete_recursos(
        informe.get("resourcePackages"), nombre_archivo)

    # El archivo y sus dos declaraciones en report.json forman una sola unidad:
    # cualquiera de las dos mitades sin la otra hace que Desktop ignore el tema.
    # La misma puerta bloquea la escritura si la version PBIR no es soportada o
    # si Desktop puede tener abierto el proyecto.
    from services.pbir_edit import assert_escritura_pbir

    assert_escritura_pbir(active, operation="Aplicar un tema")
    cm = txn_service.project_transaction(
        active, [destino, informe_path], tool="pbi_apply_theme")
    with cm as tx:
        tx.write_json(destino, tema)
        tx.write_json(informe_path, informe)

    log.info("Tema '%s' aplicado (%s)", tema.get("name"), nombre_archivo)
    return {
        "theme_name": tema.get("name"),
        "file": str(destino),
        "report_json": str(informe_path),
        "replaced": anterior,
        "data_colors": tema.get("dataColors"),
        "status_colors": {k: tema.get(k) for k in ESTADO},
        "backup": cm.result["journal"],
        "transaction": cm.result,
        "validation_report": cm.validation,
    }
