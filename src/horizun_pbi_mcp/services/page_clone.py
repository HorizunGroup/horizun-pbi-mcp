"""Fase E4 — duplicar una pagina remapeando sus referencias internas.

El defecto
----------
`duplicate_page()` copiaba cada visual con un id NUEVO y no tocaba nada mas.
Todo lo que apuntara a los ids viejos —interacciones entre visuales, grupos,
drillthrough, navegacion— seguia apuntando a la pagina ORIGINAL. La copia se
creaba sin error y el destrozo solo se veia al abrir el informe: filtros
cruzados que afectan a la pagina equivocada, o que no hacen nada.

La regla
--------
Un id viejo dentro de la pagina duplicada solo puede acabar de dos maneras:

1. **remapeado**, si esta bajo una clave que sabemos interpretar;
2. **bloqueado** con `unsupported_page_structure`, si aparece en cualquier otro
   sitio.

Nunca se copia tal cual. Copiar una referencia a un id de otra pagina es
exactamente el defecto que esto corrige, y hacerlo en silencio seria peor que
no duplicar.

Por que no un reemplazo a ciegas
--------------------------------
Sustituir toda cadena que coincida con un id viejo parece mas simple y es
peligroso: un id de 20 hex puede aparecer como parte de un literal, de una
expresion DAX serializada o del nombre de un recurso. Se remapea solo bajo
claves conocidas, y lo demas se denuncia.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set, Tuple

from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

#: Claves cuyo VALOR es el id de un visual, y por tanto hay que remapear.
CLAVES_ID_VISUAL = {
    "source", "target",              # visualInteractions
    "visualName", "visualContainerName",
    "parentGroupName",               # pertenencia a un grupo de visuales
    "activeVisual",
    "sourceVisual", "targetVisual",
}

#: Claves cuyo valor es el id de una PAGINA.
CLAVES_ID_PAGINA = {
    "activePageName", "pageName", "page", "targetPageName", "sourcePageName",
}

#: Claves que se sabe que NO llevan ids aunque su valor lo parezca.
CLAVES_IGNORADAS = {"$schema", "displayName", "title", "description"}

#: En la RAIZ, `name` es la identidad del propio documento (el id de la pagina
#: en page.json, el del visual en visual.json), no una referencia a otro. Quien
#: duplica lo sobrescribe explicitamente, asi que tratarlo como referencia
#: pendiente seria un falso positivo que bloquearia toda duplicacion.
CLAVES_IDENTIDAD_RAIZ = {"name"}


class UnsupportedPageStructure(PowerBIMCPError):
    """La pagina tiene referencias que no se pueden remapear con garantias."""

    code = "unsupported_page_structure"


def construir_mapa(visuales: List[Dict[str, Any]],
                   nuevo_id: str) -> Tuple[Dict[str, str], str]:
    """old_id -> new_id para los visuales, mas el de la pagina."""
    from horizun_pbi_mcp.pbip import pbir_writer

    mapa = {v["id"]: pbir_writer.new_id() for v in visuales}
    return mapa, nuevo_id


def _remapear(nodo: Any, mapa: Dict[str, str], mapa_pagina: Dict[str, str],
              ruta: str, sin_remapear: List[Dict[str, str]]) -> Any:
    """Copia el nodo sustituyendo ids bajo claves conocidas.

    Lo que no se pueda remapear se ANOTA en `sin_remapear`; quien llama decide
    si eso bloquea. Aqui no se adivina ni se ignora.
    """
    if isinstance(nodo, dict):
        salida = {}
        for clave, valor in nodo.items():
            hijo = f"{ruta}.{clave}"
            if clave in CLAVES_IGNORADAS:
                salida[clave] = valor
                continue
            if ruta == "$" and clave in CLAVES_IDENTIDAD_RAIZ:
                salida[clave] = valor          # identidad propia, no referencia
                continue
            if isinstance(valor, str):
                if clave in CLAVES_ID_VISUAL and valor in mapa:
                    salida[clave] = mapa[valor]
                    continue
                if clave in CLAVES_ID_PAGINA and valor in mapa_pagina:
                    salida[clave] = mapa_pagina[valor]
                    continue
                if valor in mapa or valor in mapa_pagina:
                    # Un id conocido bajo una clave que no sabemos interpretar.
                    sin_remapear.append({"path": hijo, "key": clave,
                                         "kind": "visual" if valor in mapa
                                                 else "page"})
                salida[clave] = valor
                continue
            salida[clave] = _remapear(valor, mapa, mapa_pagina, hijo, sin_remapear)
        return salida

    if isinstance(nodo, list):
        return [_remapear(x, mapa, mapa_pagina, f"{ruta}[{i}]", sin_remapear)
                for i, x in enumerate(nodo)]

    if isinstance(nodo, str) and (nodo in mapa or nodo in mapa_pagina):
        sin_remapear.append({"path": ruta, "key": "(valor suelto)",
                             "kind": "visual" if nodo in mapa else "page"})
    return nodo


def remapear_documento(datos: Any, mapa: Dict[str, str],
                       mapa_pagina: Dict[str, str],
                       origen: str) -> Tuple[Any, List[Dict[str, str]]]:
    """Documento remapeado y lista de referencias que no se pudieron resolver."""
    sin_remapear: List[Dict[str, str]] = []
    salida = _remapear(copy.deepcopy(datos), mapa, mapa_pagina, "$", sin_remapear)
    for x in sin_remapear:
        x["file"] = origen
    return salida, sin_remapear


def assert_remapeable(sin_remapear: List[Dict[str, str]], *,
                      pagina: str) -> None:
    if not sin_remapear:
        return
    raise UnsupportedPageStructure(
        f"La pagina '{pagina}' contiene {len(sin_remapear)} referencia(s) a "
        "identificadores que este servidor no sabe remapear. Duplicarla dejaria "
        "la copia apuntando a la pagina original. No se duplica.",
        details={"page": pagina, "unmapped": sin_remapear[:20],
                 "unmapped_count": len(sin_remapear),
                 "known_keys": sorted(CLAVES_ID_VISUAL | CLAVES_ID_PAGINA)})


def verificar_copia(documentos: Dict[Any, Any], mapa: Dict[str, str],
                    mapa_pagina: Dict[str, str]) -> Dict[str, Any]:
    """Comprueba que en la copia no quede ningun id viejo, y que los nuevos
    sean unicos. Es la red de seguridad de todo lo anterior."""
    import json

    viejos = set(mapa) | set(mapa_pagina)
    residuos = []
    for ruta, datos in documentos.items():
        texto = json.dumps(datos, ensure_ascii=False)
        for viejo in viejos:
            if f'"{viejo}"' in texto:
                residuos.append({"file": str(ruta), "old_id": viejo})

    nuevos = list(mapa.values()) + list(mapa_pagina.values())
    duplicados = [x for x in set(nuevos) if nuevos.count(x) > 1]
    return {"leftover_old_ids": residuos, "duplicate_new_ids": duplicados,
            "clean": not residuos and not duplicados}
