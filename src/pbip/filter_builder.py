"""Filtros de informe/pagina/visual e interacciones entre visuales.

Un filtro PBIR tiene dos mitades que es facil confundir:

- `field`, la columna o medida sobre la que se filtra, en la forma de campo
  normal (`SourceRef.Entity`);
- `filter`, una consulta semantica COMPLETA con su propio `From` y su `Where`,
  donde las tablas se referencian por ALIAS (`SourceRef.Source`), no por
  nombre. Escribir ahi el nombre de la tabla es el error tipico y Power BI lo
  rechaza sin explicar por que.

Las interacciones son mucho mas simples: una lista en la pagina que dice, por
cada par de visuales, si el primero filtra al segundo.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

from logging_config import get_logger
from powerbi.errors import PowerBIMCPError

log = get_logger("filter_builder")

#: Como interactua un visual con otro cuando se selecciona algo en el.
INTERACCIONES = ("NoFilter", "Filter", "Highlight")
#: Tipos de filtro que este servidor sabe CONSTRUIR. Otros se pueden pasar
#: tal cual con `raw`, pero no se generan a partir de valores.
TIPOS = ("Categorical", "Advanced", "TopN", "Range", "RelativeDate", "Passthrough")


class FilterBuildError(PowerBIMCPError):
    code = "filter_build_error"


def _alias(tabla: str) -> str:
    """Alias corto y estable para una tabla dentro de la consulta del filtro."""
    limpio = "".join(c for c in str(tabla) if c.isalnum()) or "t"
    return limpio[0].lower()


def _literal(valor: Any) -> Dict[str, Any]:
    if valor is None:
        return {"Literal": {"Value": "null"}}
    if isinstance(valor, bool):
        return {"Literal": {"Value": "true" if valor else "false"}}
    if isinstance(valor, int):
        return {"Literal": {"Value": f"{valor}L"}}
    if isinstance(valor, float):
        return {"Literal": {"Value": f"{valor}D"}}
    return {"Literal": {"Value": "'" + str(valor).replace("'", "''") + "'"}}


def _campo_por_alias(tabla: str, columna: str) -> Dict[str, Any]:
    return {"Column": {"Expression": {"SourceRef": {"Source": _alias(tabla)}},
                       "Property": columna}}


def _nombre_estable(contenido: Dict[str, Any]) -> str:
    semilla = json.dumps(contenido, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(semilla.encode("utf-8")).hexdigest()[:20]


def build_categorical(table: str, column: str, values: List[Any], *,
                      exclude: bool = False) -> Dict[str, Any]:
    """Filtro de lista: "la columna esta (o no esta) en estos valores".

    Es el filtro mas comun y el que Power BI crea al marcar casillas.
    """
    if not values:
        raise FilterBuildError(
            "Un filtro categorico necesita al menos un valor. Para 'no vacio' "
            "usa exclude=True con values=[None].")
    campo = _campo_por_alias(table, column)
    condicion: Dict[str, Any] = {
        "In": {"Expressions": [campo],
               "Values": [[_literal(v)] for v in values]}}
    if exclude:
        condicion = {"Not": {"Expression": condicion}}
    return {
        "Version": 2,
        "From": [{"Name": _alias(table), "Entity": table, "Type": 0}],
        "Where": [{"Condition": condicion}],
    }


def build_filter(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Un elemento de `filterConfig.filters` a partir de una descripcion simple.

    `spec`: {field: 'Tabla[Columna]', type?, values?, exclude?, raw?,
             hidden?, locked?, display_name?}
    Con `raw` se pasa una consulta semantica ya construida, sin interpretarla:
    la valvula de escape para lo que este constructor no cubre.
    """
    referencia = str(spec.get("field") or "").strip()
    if "[" not in referencia or not referencia.endswith("]"):
        raise FilterBuildError(
            f"'field' debe tener la forma 'Tabla[Columna]'; se recibio "
            f"{spec.get('field')!r}.")
    tabla = referencia[:referencia.index("[")].strip()
    columna = referencia[referencia.index("[") + 1:-1]
    if not tabla or not columna:
        raise FilterBuildError(f"Referencia de campo incompleta: {referencia!r}.")

    tipo = spec.get("type") or ("Categorical" if spec.get("values") else "Advanced")
    if tipo not in TIPOS:
        raise FilterBuildError(
            f"Tipo de filtro no soportado: '{tipo}'. Usa uno de {list(TIPOS)}.")

    salida: Dict[str, Any] = {
        "field": {"Column": {"Expression": {"SourceRef": {"Entity": tabla}},
                             "Property": columna}},
        "type": tipo,
    }
    if spec.get("raw") is not None:
        salida["filter"] = spec["raw"]
    elif spec.get("values"):
        salida["filter"] = build_categorical(tabla, columna, list(spec["values"]),
                                             exclude=bool(spec.get("exclude")))
    # Sin `filter` el elemento describe un filtro presente pero sin seleccion:
    # es lo que Power BI escribe cuando el campo esta en el panel sin acotar.

    if spec.get("display_name"):
        salida["displayName"] = spec["display_name"]
    if spec.get("hidden"):
        salida["isHiddenInViewMode"] = True
    if spec.get("locked"):
        salida["isLockedInViewMode"] = True
    salida["name"] = spec.get("name") or _nombre_estable(salida)
    return salida


def build_filter_config(specs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """`filterConfig` completo para un informe, una pagina o un visual."""
    filtros = [build_filter(s) for s in specs or [] if isinstance(s, dict)]
    return {"filters": filtros} if filtros else None


def build_interactions(specs: List[Dict[str, Any]],
                       visuales_validos: Optional[List[str]] = None
                       ) -> Optional[List[Dict[str, Any]]]:
    """`visualInteractions` de una pagina.

    Cada entrada dice que le hace `source` a `target` al seleccionar en el.
    Se comprueba que ambos existan: una interaccion que apunta a un visual
    inexistente no falla al abrir, simplemente no hace nada, y encontrarlo
    despues es muy caro.
    """
    salida = []
    conocidos = set(visuales_validos or [])
    for s in specs or []:
        if not isinstance(s, dict):
            continue
        origen, destino = s.get("source"), s.get("target")
        tipo = s.get("type", "NoFilter")
        if not origen or not destino:
            raise FilterBuildError(
                "Una interaccion necesita 'source' y 'target' (ids de visual).")
        if tipo not in INTERACCIONES:
            raise FilterBuildError(
                f"Interaccion no soportada: '{tipo}'. Usa una de {list(INTERACCIONES)}.")
        if conocidos:
            faltan = [v for v in (origen, destino) if v not in conocidos]
            if faltan:
                raise FilterBuildError(
                    f"La interaccion apunta a visuales que no existen en la "
                    f"pagina: {faltan}.", details={"known": sorted(conocidos)})
        salida.append({"source": origen, "target": destino, "type": tipo})
    return salida or None
