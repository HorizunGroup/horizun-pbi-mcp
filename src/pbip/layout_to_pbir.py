"""Traduccion del informe HEREDADO (`Report/Layout`) al formato PBIR.

El formato heredado es un unico JSON gigante en UTF-16 donde varias propiedades
llevan **otro JSON serializado dentro de un string** (`config`, `filters`,
`query`, `dataTransforms`). PBIR es lo contrario: un arbol de archivos, un JSON
por pagina y por visual, todo tipado por esquema.

Las cuatro diferencias reales entre ambos, y como se resuelven aqui:

1. **Referencias a tablas.** El heredado usa alias (`SourceRef: {Source: "c"}`)
   que se resuelven contra el `From` de cada `prototypeQuery`. PBIR nombra la
   tabla directamente (`SourceRef: {Entity: "Cronograma"}`). Se reescribe todo
   el arbol de expresiones (`_resolver_alias`).
2. **Proyecciones.** El heredado guarda `projections` (solo `queryRef`) y por
   separado el `prototypeQuery.Select` con la expresion real. PBIR fusiona
   ambos: cada proyeccion lleva su `field` completo. Se cruzan por `Name`.
3. **Enumeraciones numericas** (`displayOption`, `Direction`, tipos de recurso)
   pasan a cadenas.
4. **Orden.** `prototypeQuery.OrderBy` pasa a `query.sortDefinition`.

Las equivalencias se verificaron contra un par real: el mismo informe guardado
por Power BI Desktop en ambos formatos (ver tests/test_pbix_convert.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from logging_config import get_logger

log = get_logger("layout_to_pbir")

SCHEMA_BASE = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
SCHEMA_REPORT = f"{SCHEMA_BASE}/report/3.3.0/schema.json"
SCHEMA_PAGE = f"{SCHEMA_BASE}/page/2.1.0/schema.json"
SCHEMA_PAGES = f"{SCHEMA_BASE}/pagesMetadata/1.1.0/schema.json"
SCHEMA_VISUAL = f"{SCHEMA_BASE}/visualContainer/2.7.0/schema.json"
SCHEMA_VERSION = f"{SCHEMA_BASE}/versionMetadata/1.0.0/schema.json"
PBIR_DEFINITION_VERSION = "2.0.0"

#: `displayOption` de pagina. El orden es el del enum interno de Power BI y se
#: confirmo con un informe real guardado en los dos formatos (1 -> FitToPage).
_DISPLAY_OPTION = {
    0: "DeprecatedDynamic",
    1: "FitToPage",
    2: "FitToWidth",
    3: "ActualSize",
    4: "ActualSizeTopLeft",
}
_SORT_DIRECTION = {1: "Ascending", 2: "Descending"}
_RESOURCE_PACKAGE_TYPE = {0: "CustomVisual", 1: "RegisteredResources",
                          2: "SharedResources",
                          3: "OrganizationalStoreCustomVisual"}
_RESOURCE_ITEM_TYPE = {
    5: "CustomVisualMetadata",
    100: "Image",
    200: "CustomTheme",
    201: "CustomTheme",
    202: "BaseTheme",
}
_THEME_TYPE = {1: "RegisteredResources", 2: "SharedResources"}
_GROUP_MODE = {0: "ScaleMode", 1: "ScrollMode"}
#: `reportVersionAtImport` es obligatorio en PBIR y el heredado a veces no
#: guarda ninguna version. Se declara la mas antigua, que es lo que era cierto.
_THEME_VERSION_POR_DEFECTO = {"visual": "1.0.0", "page": "1.0.0", "report": "1.0.0"}
#: Como se creo un filtro. En el heredado es numerico; en PBIR, cadena.
_FILTER_HOW_CREATED = {0: "Auto", 1: "User", 2: "Drill", 3: "Include",
                       4: "Exclude", 5: "Drillthrough"}
_PAGE_VISIBILITY = {0: "AlwaysVisible", 1: "HiddenInViewMode"}
_PAGE_TYPES = ("Drillthrough", "Tooltip")
_EXPORT_DATA_MODE = {0: "AllowSummarized", 1: "AllowSummarizedAndUnderlying",
                     2: "None"}
_QUERY_LIMIT_OPTION = {0: "None", 1: "Shared", 2: "Premium", 3: "SQLServerAS",
                       4: "AzureAS", 5: "Custom", 6: "Auto"}
#: Ajustes de informe admitidos por PBIR (`additionalProperties: false`).
_SETTINGS_KEYS = (
    "isPersistentUserStateDisabled", "hideVisualContainerHeader",
    "useStylableVisualContainerHeader", "exportDataMode",
    "isReportAnnotationsDisabled", "defaultFilterActionIsDataFilter",
    "defaultDrillFilterOtherVisuals", "useCrossReportDrillthrough",
    "allowChangeFilterTypes", "allowInlineExploration", "useEnhancedTooltips",
    "useScaledTooltips", "filterPaneHiddenInEditMode", "disableFilterPaneSearch",
    "pagesPosition", "allowAutomatedInsightsNotification",
    "useDefaultAggregateDisplayName", "enableDeveloperMode", "pauseQueries",
    "queryLimitOption", "customMemoryLimit", "customTimeoutLimit",
    "fieldParameterReportSettings", "defaultDataExplorePerspective", "locale",
    "defaultDisplayUnitsToNone",
)
#: Ajustes que solo existian en el formato heredado. Se descartan sin ruido:
#: en PBIR su comportamiento es el unico posible.
_SETTINGS_OBSOLETAS = frozenset({"useNewFilterPaneExperience",
                                 "optOutNewFilterPaneExperience"})

#: Claves admitidas por `RoleProjection` (el esquema prohibe cualquier otra).
_PROJECTION_KEYS = ("field", "queryRef", "nativeQueryRef", "displayName",
                    "format", "active", "hidden")
#: Claves admitidas en cada filtro de `filterConfig` (`additionalProperties: false`).
_FILTER_KEYS = ("name", "displayName", "ordinal", "field", "type", "filter",
                "restatement", "howCreated", "isHiddenInViewMode",
                "isLockedInViewMode", "objects")
_POSITION_KEYS = ("x", "y", "z", "height", "width", "tabOrder", "angle")

_NOMBRE_SEGURO = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass
class LayoutConversion:
    """Resultado de convertir un `Report/Layout` completo."""

    #: Ruta relativa dentro de `<Nombre>.Report/definition/` -> objeto JSON.
    files: Dict[str, Any] = field(default_factory=dict)
    pages: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    #: Cosas del informe que NO tienen equivalente en PBIR y se perdieron.
    dropped: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def visual_count(self) -> int:
        return sum(p["visual_count"] for p in self.pages)

    def stats(self) -> Dict[str, Any]:
        return {
            "pages": len(self.pages),
            "visuals": self.visual_count,
            "files": len(self.files),
            "dropped": len(self.dropped),
        }


def _json_embebido(valor: Any, contexto: str,
                   avisos: List[str]) -> Any:
    """Parsea una propiedad que en el formato heredado viene como string JSON."""
    if valor is None or valor == "":
        return None
    if not isinstance(valor, str):
        return valor
    import json

    try:
        return json.loads(valor)
    except ValueError as exc:
        avisos.append(f"{contexto}: JSON embebido ilegible, se omite ({exc}).")
        return None


def _alias_de_tablas(prototype_query: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """`From` de una consulta -> {alias: nombre de tabla}."""
    aliases: Dict[str, str] = {}
    for origen in (prototype_query or {}).get("From", []) or []:
        nombre = origen.get("Name")
        entidad = origen.get("Entity")
        if nombre and entidad:
            aliases[nombre] = entidad
    return aliases


def _resolver_alias(nodo: Any, aliases: Dict[str, str]) -> Any:
    """Sustituye `SourceRef: {Source: alias}` por `SourceRef: {Entity: tabla}`.

    Recorre el arbol completo porque el `SourceRef` puede estar a cualquier
    profundidad (dentro de `Aggregation`, `HierarchyLevel`, `Measure`...).
    Un alias sin entrada en el `From` se deja como esta: es preferible un
    `Source` que Desktop marque como roto a inventarse una tabla.
    """
    if isinstance(nodo, dict):
        origen = nodo.get("SourceRef")
        if isinstance(origen, dict) and "Source" in origen and "Entity" not in origen:
            entidad = aliases.get(origen["Source"])
            if entidad:
                resto = {k: v for k, v in nodo.items() if k != "SourceRef"}
                salida = {"SourceRef": {"Entity": entidad}}
                salida.update({k: _resolver_alias(v, aliases) for k, v in resto.items()})
                return salida
        return {k: _resolver_alias(v, aliases) for k, v in nodo.items()}
    if isinstance(nodo, list):
        return [_resolver_alias(x, aliases) for x in nodo]
    return nodo


def _indice_de_select(prototype_query: Optional[Dict[str, Any]],
                      aliases: Dict[str, str]) -> Dict[str, Tuple[Dict[str, Any], Optional[str]]]:
    """`Select` de la consulta -> {Name: (expresion, NativeReferenceName)}.

    `Name` es exactamente el `queryRef` que usan las proyecciones: es la llave
    que une las dos mitades que PBIR fusiona en un solo objeto.
    """
    indice: Dict[str, Tuple[Dict[str, Any], Optional[str]]] = {}
    for seleccion in (prototype_query or {}).get("Select", []) or []:
        if not isinstance(seleccion, dict):
            continue
        nombre = seleccion.get("Name")
        if not nombre:
            continue
        expresion = {k: v for k, v in seleccion.items()
                     if k not in ("Name", "NativeReferenceName")}
        indice[nombre] = (_resolver_alias(expresion, aliases),
                          seleccion.get("NativeReferenceName"))
    return indice


def _campo_por_query_ref(query_ref: str) -> Optional[Dict[str, Any]]:
    """Ultimo recurso: deduce `Tabla.Columna` cuando no hay entrada en `Select`.

    Solo sirve para referencias simples. Una agregacion como
    `Min(Tabla.Columna)` no se puede reconstruir sin el `Select`, y devolver
    algo aproximado seria peor que declararlo perdido.
    """
    if not query_ref or "(" in query_ref:
        return None
    partes = query_ref.split(".")
    if len(partes) != 2:
        return None
    tabla, propiedad = partes
    if not tabla or not propiedad:
        return None
    return {"Column": {"Expression": {"SourceRef": {"Entity": tabla}},
                       "Property": propiedad}}


def _convertir_proyeccion(proyeccion: Dict[str, Any],
                          indice: Dict[str, Tuple[Dict[str, Any], Optional[str]]],
                          contexto: str,
                          avisos: List[str],
                          perdidos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    query_ref = proyeccion.get("queryRef")
    if not query_ref:
        perdidos.append({"where": contexto, "what": "proyeccion sin queryRef"})
        return None

    entrada = indice.get(query_ref)
    if entrada is not None:
        campo, nativo = entrada
    else:
        campo = _campo_por_query_ref(query_ref)
        nativo = None
        if campo is None:
            perdidos.append({
                "where": contexto,
                "what": f"campo '{query_ref}' sin definicion en prototypeQuery.Select",
            })
            return None
        avisos.append(
            f"{contexto}: el campo '{query_ref}' no estaba en prototypeQuery.Select; "
            "se reconstruyo como referencia simple a columna.")

    salida: Dict[str, Any] = {"field": campo, "queryRef": query_ref}
    if nativo:
        salida["nativeQueryRef"] = nativo
    elif proyeccion.get("nativeQueryRef"):
        salida["nativeQueryRef"] = proyeccion["nativeQueryRef"]
    else:
        # El CLI oficial exige este identificador para cada proyeccion. Los
        # Layout heredados no siempre incluyen `NativeReferenceName` (sobre
        # todo en agregaciones), pero `queryRef` si es la identidad que enlaza
        # la proyeccion con `prototypeQuery.Select`. Reutilizarla conserva la
        # referencia real; no se deduce ni se inventa ningun campo del modelo.
        salida["nativeQueryRef"] = query_ref
    for clave in ("displayName", "format", "active", "hidden"):
        if proyeccion.get(clave) is not None:
            salida[clave] = proyeccion[clave]
    sobrantes = set(proyeccion) - set(_PROJECTION_KEYS) - {"nativeQueryRef"}
    if sobrantes:
        log.debug("%s: claves de proyeccion ignoradas: %s", contexto, sorted(sobrantes))
    return salida


def _convertir_query_state(single_visual: Dict[str, Any],
                           contexto: str,
                           avisos: List[str],
                           perdidos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    proyecciones = single_visual.get("projections") or {}
    if not proyecciones:
        return None
    prototipo = single_visual.get("prototypeQuery")
    aliases = _alias_de_tablas(prototipo)
    indice = _indice_de_select(prototipo, aliases)
    mostrar_todo = set(single_visual.get("showAllRoles") or [])

    estado: Dict[str, Any] = {}
    for rol, lista in proyecciones.items():
        convertidas = []
        for proyeccion in lista or []:
            if not isinstance(proyeccion, dict):
                continue
            resultado = _convertir_proyeccion(
                proyeccion, indice, f"{contexto} rol '{rol}'", avisos, perdidos)
            if resultado is not None:
                convertidas.append(resultado)
        if not convertidas:
            continue
        rol_salida: Dict[str, Any] = {"projections": convertidas}
        if rol in mostrar_todo:
            rol_salida["showAll"] = True
        estado[rol] = rol_salida
    return estado or None


def _convertir_sort(single_visual: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    prototipo = single_visual.get("prototypeQuery") or {}
    order_by = prototipo.get("OrderBy") or []
    if not order_by:
        return None
    aliases = _alias_de_tablas(prototipo)
    orden = []
    for item in order_by:
        expresion = item.get("Expression")
        if expresion is None:
            continue
        orden.append({
            "field": _resolver_alias(expresion, aliases),
            "direction": _SORT_DIRECTION.get(item.get("Direction"), "Ascending"),
        })
    if not orden:
        return None
    salida: Dict[str, Any] = {"sort": orden}
    if single_visual.get("hasDefaultSort"):
        salida["isDefaultSort"] = True
    return salida


def _nombre_de_filtro(filtro: Dict[str, Any], posicion: int) -> str:
    """Identificador de 20 hex derivado del filtro, al estilo de Power BI."""
    import hashlib
    import json

    semilla = json.dumps(filtro, sort_keys=True, ensure_ascii=False,
                         default=str) + f"#{posicion}"
    return hashlib.sha1(semilla.encode("utf-8")).hexdigest()[:20]


def _convertir_filtros(crudo: Any, contexto: str,
                       avisos: List[str]) -> Optional[Dict[str, Any]]:
    """`filters` heredado -> `filterConfig`.

    Dos cambios: la expresion filtrada pasa de `expression` a `field`, y
    `howCreated` deja de ser un numero. El esquema declara
    `additionalProperties: false`, asi que cualquier otra clave se descarta.
    """
    filtros = _json_embebido(crudo, contexto, avisos)
    if not filtros:
        return None
    if not isinstance(filtros, list):
        avisos.append(f"{contexto}: 'filters' no era una lista; se omite.")
        return None
    salida = []
    for posicion, filtro in enumerate(filtros):
        if not isinstance(filtro, dict):
            continue
        convertido = {k: v for k, v in filtro.items() if k in _FILTER_KEYS}
        if "expression" in filtro:
            convertido["field"] = filtro["expression"]
        if not convertido.get("name"):
            # En PBIR el nombre identifica al filtro y es obligatorio; el
            # heredado lo omitia. Se deriva del contenido para que sea estable
            # entre conversiones del mismo informe.
            convertido["name"] = _nombre_de_filtro(filtro, posicion)
        creado = filtro.get("howCreated")
        if isinstance(creado, bool) or creado is None:
            convertido.pop("howCreated", None)
        elif isinstance(creado, int):
            equivalente = _FILTER_HOW_CREATED.get(creado)
            if equivalente:
                convertido["howCreated"] = equivalente
            else:
                convertido.pop("howCreated", None)
                avisos.append(f"{contexto}: filtro con howCreated={creado} "
                              "desconocido; se omite esa propiedad.")
        salida.append(convertido)
    return {"filters": salida} if salida else None


def _posicion(contenedor: Dict[str, Any],
              config: Dict[str, Any]) -> Dict[str, Any]:
    """Posicion del visual. `layouts[0].position` manda; el resto es respaldo."""
    disposiciones = config.get("layouts") or []
    posicion: Dict[str, Any] = {}
    if disposiciones and isinstance(disposiciones[0], dict):
        posicion = dict(disposiciones[0].get("position") or {})
    for clave in ("x", "y", "z", "width", "height", "tabOrder"):
        if posicion.get(clave) is None and contenedor.get(clave) is not None:
            posicion[clave] = contenedor[clave]
    salida = {k: posicion[k] for k in _POSITION_KEYS if posicion.get(k) is not None}
    salida.setdefault("x", 0)
    salida.setdefault("y", 0)
    salida.setdefault("width", 0)
    salida.setdefault("height", 0)
    return salida


def _limpiar_nodo_expansion(nodo: Any, es_raiz: bool) -> Optional[Dict[str, Any]]:
    """Poda un nodo del arbol de expansion (matrices y jerarquias).

    El heredado escribe `identityValues: null` donde no hay identidad; PBIR
    espera una lista, y en los nodos hijos la exige. Un hijo sin identidad no
    identifica ninguna fila, asi que se descarta en vez de inventarle una.
    """
    if not isinstance(nodo, dict):
        return None
    salida: Dict[str, Any] = {}
    valores = nodo.get("identityValues")
    if isinstance(valores, list):
        salida["identityValues"] = valores
    elif not es_raiz:
        return None
    if nodo.get("isToggled") is not None:
        salida["isToggled"] = nodo["isToggled"]
    hijos = [h for h in (_limpiar_nodo_expansion(x, False)
                         for x in (nodo.get("children") or [])) if h]
    if hijos:
        salida["children"] = hijos
    return salida or None


def _limpiar_expansiones(estados: Any) -> Optional[List[Dict[str, Any]]]:
    if not isinstance(estados, list) or not estados:
        return None
    salida = []
    for estado in estados:
        if not isinstance(estado, dict) or not estado.get("roles"):
            continue
        convertido: Dict[str, Any] = {"roles": estado["roles"]}
        if estado.get("levels"):
            convertido["levels"] = estado["levels"]
        raiz = _limpiar_nodo_expansion(estado.get("root"), True)
        if raiz:
            convertido["root"] = raiz
        salida.append(convertido)
    return salida or None


def _nombre_seguro(nombre: str, respaldo: str) -> str:
    """Nombre usable como carpeta. Power BI ya usa tokens hexadecimales."""
    limpio = _NOMBRE_SEGURO.sub("_", (nombre or "").strip())
    return limpio or respaldo


def _convertir_visual(contenedor: Dict[str, Any], indice_visual: int,
                      contexto_pagina: str, avisos: List[str],
                      perdidos: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    config = _json_embebido(contenedor.get("config"), f"{contexto_pagina} visual", avisos)
    if not isinstance(config, dict):
        perdidos.append({"where": contexto_pagina,
                         "what": f"visual #{indice_visual} sin config legible"})
        return None

    nombre = _nombre_seguro(config.get("name") or "", f"visual{indice_visual}")
    contexto = f"{contexto_pagina} / visual '{nombre}'"
    salida: Dict[str, Any] = {
        "$schema": SCHEMA_VISUAL,
        "name": nombre,
        "position": _posicion(contenedor, config),
    }

    single = config.get("singleVisual")
    grupo = config.get("singleVisualGroup")
    if isinstance(grupo, dict):
        # Grupo de visuales: no tiene consulta, solo agrupa a sus hijos. En
        # PBIR el grupo solo describe la agrupacion; que este oculto es
        # propiedad del contenedor, no del grupo.
        convertido: Dict[str, Any] = {
            "displayName": grupo.get("displayName") or nombre,
            "groupMode": _GROUP_MODE.get(grupo.get("groupMode"), "ScaleMode"),
        }
        if grupo.get("objects"):
            convertido["objects"] = grupo["objects"]
        salida["visualGroup"] = convertido
        if grupo.get("isHidden"):
            salida["isHidden"] = True
    elif isinstance(single, dict):
        visual: Dict[str, Any] = {"visualType": single.get("visualType") or "card"}
        estado = _convertir_query_state(single, contexto, avisos, perdidos)
        if estado:
            consulta: Dict[str, Any] = {"queryState": estado}
            orden = _convertir_sort(single)
            if orden:
                consulta["sortDefinition"] = orden
            visual["query"] = consulta
        if single.get("objects"):
            visual["objects"] = single["objects"]
        if single.get("vcObjects"):
            visual["visualContainerObjects"] = single["vcObjects"]
        if single.get("drillFilterOtherVisuals") is not None:
            visual["drillFilterOtherVisuals"] = single["drillFilterOtherVisuals"]
        if single.get("syncGroup"):
            visual["syncGroup"] = single["syncGroup"]
        expansiones = _limpiar_expansiones(single.get("expansionStates"))
        if expansiones:
            visual["expansionStates"] = expansiones
        if single.get("autoSelectVisualType") is not None:
            visual["autoSelectVisualType"] = single["autoSelectVisualType"]
        salida["visual"] = visual
    else:
        perdidos.append({"where": contexto,
                         "what": "visual sin singleVisual ni singleVisualGroup"})
        return None

    if config.get("parentGroupName"):
        salida["parentGroupName"] = config["parentGroupName"]
    filtros = _convertir_filtros(contenedor.get("filters"), contexto, avisos)
    if filtros:
        salida["filterConfig"] = filtros
    if config.get("howCreated"):
        salida["howCreated"] = config["howCreated"]

    if len(config.get("layouts") or []) > 1:
        avisos.append(
            f"{contexto}: el visual tenia {len(config['layouts'])} disposiciones "
            "(movil/escritorio); PBIR solo conserva la de escritorio.")
    return salida


def _convertir_pagina(seccion: Dict[str, Any], indice: int,
                      avisos: List[str],
                      perdidos: List[Dict[str, Any]]) -> Dict[str, Any]:
    nombre = _nombre_seguro(seccion.get("name") or "", f"pagina{indice}")
    display = seccion.get("displayName") or nombre
    contexto = f"pagina '{display}'"
    config = _json_embebido(seccion.get("config"), contexto, avisos) or {}

    pagina: Dict[str, Any] = {
        "$schema": SCHEMA_PAGE,
        "name": nombre,
        "displayName": display,
        "displayOption": _DISPLAY_OPTION.get(seccion.get("displayOption"), "FitToPage"),
    }
    for clave in ("height", "width"):
        if seccion.get(clave) is not None:
            pagina[clave] = seccion[clave]
    if config.get("objects"):
        pagina["objects"] = config["objects"]
    if config.get("visualInteractions"):
        pagina["visualInteractions"] = config["visualInteractions"]
    if config.get("type") in _PAGE_TYPES:
        pagina["type"] = config["type"]
    # Una pagina oculta se marca en el `config` de la seccion, no en la seccion.
    visibilidad = config.get("visibility", seccion.get("visibility"))
    if isinstance(visibilidad, int) and not isinstance(visibilidad, bool):
        equivalente = _PAGE_VISIBILITY.get(visibilidad)
        if equivalente:
            pagina["visibility"] = equivalente
        else:
            avisos.append(f"{contexto}: visibility={visibilidad} desconocida; "
                          "la pagina queda visible.")
    elif isinstance(visibilidad, bool):
        pagina["visibility"] = "HiddenInViewMode" if visibilidad else "AlwaysVisible"
    filtros = _convertir_filtros(seccion.get("filters"), contexto, avisos)
    if filtros:
        pagina["filterConfig"] = filtros

    visuales = []
    vistos: Dict[str, int] = {}
    for i, contenedor in enumerate(seccion.get("visualContainers") or []):
        if not isinstance(contenedor, dict):
            continue
        visual = _convertir_visual(contenedor, i, contexto, avisos, perdidos)
        if visual is None:
            continue
        # Dos visuales con el mismo nombre chocarian al ser carpetas hermanas.
        nombre_visual = visual["name"]
        if nombre_visual in vistos:
            vistos[nombre_visual] += 1
            visual["name"] = f"{nombre_visual}_{vistos[nombre_visual]}"
            avisos.append(
                f"{contexto}: habia dos visuales llamados '{nombre_visual}'; "
                f"el segundo se renombro a '{visual['name']}'.")
        else:
            vistos[nombre_visual] = 0
        visuales.append(visual)

    return {"name": nombre, "display_name": display, "page": pagina,
            "visuals": visuales, "visual_count": len(visuales),
            "ordinal": seccion.get("ordinal", indice)}


def _convertir_recursos(paquetes: Any, avisos: List[str]) -> Optional[List[Dict[str, Any]]]:
    """`resourcePackages` heredado (con enums numericos) -> el de PBIR."""
    if not paquetes:
        return None
    salida = []
    for envoltorio in paquetes:
        paquete = envoltorio.get("resourcePackage") if isinstance(envoltorio, dict) else None
        if not isinstance(paquete, dict):
            continue
        if paquete.get("disabled"):
            continue
        tipo = _RESOURCE_PACKAGE_TYPE.get(paquete.get("type"))
        if tipo is None:
            avisos.append(
                f"Paquete de recursos '{paquete.get('name')}' con tipo desconocido "
                f"({paquete.get('type')}); se omite.")
            continue
        elementos = []
        for item in paquete.get("items") or []:
            tipo_item = _RESOURCE_ITEM_TYPE.get(item.get("type"))
            if tipo_item is None:
                avisos.append(
                    f"Recurso '{item.get('name')}' con tipo desconocido "
                    f"({item.get('type')}); se omite.")
                continue
            ruta = item.get("path")
            if not isinstance(ruta, str) or not ruta:
                avisos.append(f"Recurso sin ruta valida en el paquete "
                              f"'{paquete.get('name')}'; se omite.")
                continue
            # El heredado admite nombres numericos; PBIR exige cadena. El
            # nombre solo etiqueta al recurso, asi que basta con formatearlo.
            nombre_item = item.get("name")
            if not isinstance(nombre_item, str):
                nombre_item = ruta if nombre_item is None else str(nombre_item)
            elementos.append({"name": nombre_item, "path": ruta,
                              "type": tipo_item})
        if elementos:
            salida.append({"name": paquete.get("name"), "type": tipo, "items": elementos})
    return salida or None


def _convertir_settings(ajustes: Any, avisos: List[str]) -> Optional[Dict[str, Any]]:
    """`settings` del informe: se filtran los obsoletos y se destipan los enums."""
    if not isinstance(ajustes, dict) or not ajustes:
        return None
    salida: Dict[str, Any] = {}
    for clave, valor in ajustes.items():
        if clave in _SETTINGS_OBSOLETAS:
            continue
        if clave not in _SETTINGS_KEYS:
            avisos.append(f"Ajuste de informe '{clave}' sin equivalente en PBIR; "
                          "se descarta.")
            continue
        if clave == "exportDataMode" and isinstance(valor, int):
            valor = _EXPORT_DATA_MODE.get(valor)
        elif clave == "queryLimitOption" and isinstance(valor, int):
            valor = _QUERY_LIMIT_OPTION.get(valor)
        if valor is None:
            avisos.append(f"Ajuste de informe '{clave}' con valor desconocido "
                          f"({ajustes[clave]}); se descarta.")
            continue
        salida[clave] = valor
    return salida or None


def _convertir_tema(coleccion: Any, avisos: List[str]) -> Dict[str, Any]:
    """`themeCollection`: los `type` numericos pasan a cadena.

    `themeCollection` es obligatorio en report.json, asi que si el informe no
    trae ninguno se deja el tema base por defecto de Power BI.
    """
    por_defecto = {"baseTheme": {"name": "CY24SU10", "type": "SharedResources",
                                 "reportVersionAtImport": _THEME_VERSION_POR_DEFECTO}}
    if not isinstance(coleccion, dict) or not coleccion:
        return por_defecto

    # Si un tema no declara version se usa la del otro del mismo informe:
    # ambos se importaron a la vez, asi que es el dato mas cercano al real.
    versiones = [t["version"] for t in coleccion.values()
                 if isinstance(t, dict) and isinstance(t.get("version"), dict)]
    respaldo = versiones[0] if versiones else _THEME_VERSION_POR_DEFECTO

    salida: Dict[str, Any] = {}
    for clave in ("baseTheme", "customTheme"):
        tema = coleccion.get(clave)
        if not isinstance(tema, dict) or not tema.get("name"):
            continue
        tipo = _THEME_TYPE.get(tema.get("type"))
        if tipo is None:
            # `type` es obligatorio: se deduce de donde vive cada tema, que es
            # lo que distingue a los dos tipos.
            tipo = "RegisteredResources" if clave == "customTheme" else "SharedResources"
            avisos.append(f"Tema '{tema['name']}' con tipo desconocido "
                          f"({tema.get('type')}); se asume '{tipo}'.")
        version = tema["version"] if isinstance(tema.get("version"), dict) else respaldo
        if not isinstance(tema.get("version"), dict):
            avisos.append(f"Tema '{tema['name']}' sin version registrada; se "
                          "declara la del otro tema del informe o la mas antigua.")
        salida[clave] = {"name": tema["name"], "type": tipo,
                         "reportVersionAtImport": version}
    return salida or por_defecto


def convert_layout(layout: Dict[str, Any]) -> LayoutConversion:
    """Convierte un `Report/Layout` completo al arbol de archivos PBIR.

    Devuelve rutas RELATIVAS a `<Nombre>.Report/definition/`.
    """
    resultado = LayoutConversion()
    avisos = resultado.warnings
    perdidos = resultado.dropped

    config = _json_embebido(layout.get("config"), "informe", avisos) or {}

    informe: Dict[str, Any] = {
        "$schema": SCHEMA_REPORT,
        "themeCollection": _convertir_tema(config.get("themeCollection"), avisos),
    }
    if config.get("objects"):
        informe["objects"] = config["objects"]
    ajustes = _convertir_settings(config.get("settings"), avisos)
    if ajustes:
        informe["settings"] = ajustes
    if layout.get("publicCustomVisuals"):
        informe["publicCustomVisuals"] = layout["publicCustomVisuals"]
    recursos = _convertir_recursos(layout.get("resourcePackages"), avisos)
    if recursos:
        informe["resourcePackages"] = recursos
    filtros = _convertir_filtros(layout.get("filters"), "informe", avisos)
    if filtros:
        informe["filterConfig"] = filtros
    if config.get("slowDataSourceSettings"):
        informe["slowDataSourceSettings"] = config["slowDataSourceSettings"]

    resultado.files["report.json"] = informe
    resultado.files["version.json"] = {
        "$schema": SCHEMA_VERSION, "version": PBIR_DEFINITION_VERSION}

    secciones = layout.get("sections") or []
    paginas = []
    for i, seccion in enumerate(secciones):
        if not isinstance(seccion, dict):
            continue
        paginas.append(_convertir_pagina(seccion, i, avisos, perdidos))

    paginas.sort(key=lambda p: p["ordinal"] if p["ordinal"] is not None else 0)
    for pagina in paginas:
        base = f"pages/{pagina['name']}"
        resultado.files[f"{base}/page.json"] = pagina["page"]
        for visual in pagina["visuals"]:
            resultado.files[f"{base}/visuals/{visual['name']}/visual.json"] = visual
    resultado.pages = paginas

    orden = [p["name"] for p in paginas]
    metadatos: Dict[str, Any] = {"$schema": SCHEMA_PAGES, "pageOrder": orden}
    if orden:
        indice_activo = config.get("activeSectionIndex")
        if isinstance(indice_activo, int) and 0 <= indice_activo < len(orden):
            metadatos["activePageName"] = orden[indice_activo]
        else:
            metadatos["activePageName"] = orden[0]
    resultado.files["pages/pages.json"] = metadatos

    # Los marcadores viven en el `config` del informe heredado y en PBIR son
    # archivos propios bajo `bookmarks/`, con un modelo de estado distinto.
    marcadores = config.get("bookmarks") or []
    if marcadores:
        perdidos.append({
            "where": "informe",
            "what": f"{len(marcadores)} marcador(es); PBIR los guarda en "
                    "'bookmarks/' con otro formato de estado y no se traducen.",
        })

    log.info("Layout convertido: %s paginas, %s visuales, %s avisos",
             len(paginas), resultado.visual_count, len(avisos))
    return resultado
