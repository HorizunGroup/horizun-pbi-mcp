"""Lectura de `filterConfig`: el inverso de `filter_builder`.

Existe para una sola pregunta: cuando se exporta el CONTENIDO de un visual,
que estaba filtrando lo que se ve en pantalla. Un export que ignora los
filtros da cifras que no cuadran con el tablero, y eso es peor que no
exportar: nadie duda de un Excel.

Por eso la salida separa tres cosas que no son lo mismo:

- `applied`: filtros que se entienden y se pueden llevar a DAX.
- `untranslated`: filtros que ESTAN y NO se supieron traducir. Quien exporta
  tiene que declararlos; callarlos convierte el numero en mentira.
- `unset`: campos presentes en el panel sin seleccion. No acotan nada, y
  contarlos como riesgo seria ruido.

Solo se traduce el filtro categorico -`In` y su negacion-, que es el que
Power BI escribe al marcar casillas. Todo lo demas cae en `untranslated` con
el motivo, a proposito: es preferible decir "esto no lo se aplicar" a
inventar una equivalencia parecida.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("filter_reader")


def _parse_literal(valor: Any) -> Any:
    """`{'Value': "'Abierto'"}` -> `Abierto`. El inverso de `_literal`."""
    texto = str(valor if valor is not None else "").strip()
    if texto == "" or texto == "null":
        return None
    if texto in ("true", "false"):
        return texto == "true"
    if len(texto) >= 2 and texto[0] == "'" and texto[-1] == "'":
        return texto[1:-1].replace("''", "'")
    if texto[-1:] in ("L", "D", "M") and texto[:-1].lstrip("-+").replace(".", "", 1).isdigit():
        cuerpo = texto[:-1]
        try:
            return int(cuerpo) if texto[-1] == "L" else float(cuerpo)
        except ValueError:                    # pragma: no cover - defensivo
            return cuerpo
    return texto


def _campo_de(field: Any) -> Tuple[Optional[str], bool]:
    """Referencia `Tabla[Columna]` y si el campo es una medida."""
    if not isinstance(field, dict):
        return None, False
    for clave, es_medida in (("Column", False), ("Measure", True)):
        if clave in field:
            nodo = field[clave] or {}
            entidad = ((nodo.get("Expression") or {}).get("SourceRef") or {}).get("Entity")
            propiedad = nodo.get("Property")
            if not propiedad:
                return None, es_medida
            return (f"{entidad}[{propiedad}]" if entidad else propiedad), es_medida
    return None, False


def _condicion_categorica(condicion: Any) -> Optional[Dict[str, Any]]:
    """`In` (o `Not(In)`) sobre UNA columna -> `{values, exclude}`."""
    if not isinstance(condicion, dict):
        return None
    excluir = False
    if "Not" in condicion:
        condicion = (condicion.get("Not") or {}).get("Expression")
        excluir = True
        if not isinstance(condicion, dict):
            return None
    nodo = condicion.get("In")
    if not isinstance(nodo, dict):
        return None
    expresiones = nodo.get("Expressions") or []
    # Un `In` sobre dos columnas a la vez ("estos pares") no equivale a filtrar
    # una columna por una lista. Traducirlo como si lo fuera cambiaria el dato.
    if len(expresiones) != 1:
        return None
    valores = []
    for fila in nodo.get("Values") or []:
        if not isinstance(fila, list) or len(fila) != 1:
            return None
        literal = (fila[0] or {}).get("Literal") if isinstance(fila[0], dict) else None
        if literal is None:
            return None
        valores.append(_parse_literal(literal.get("Value")))
    return {"values": valores, "exclude": excluir}


def leer_filtro(entrada: Any, *, scope: str) -> Dict[str, Any]:
    """Clasifica UN elemento de `filterConfig.filters`."""
    if not isinstance(entrada, dict):
        return {"state": "untranslated", "scope": scope, "field": None,
                "reason": "El filtro no es un objeto."}
    referencia, es_medida = _campo_de(entrada.get("field"))
    tipo = entrada.get("type") or "Advanced"
    base = {"scope": scope, "field": referencia, "type": tipo,
            "display_name": entrada.get("displayName")}

    if "filter" not in entrada or entrada.get("filter") is None:
        return {**base, "state": "unset"}
    if referencia is None:
        return {**base, "state": "untranslated",
                "reason": "No se pudo leer sobre que campo filtra."}
    if es_medida:
        return {**base, "state": "untranslated",
                "reason": "Filtra sobre una medida; no equivale a acotar una "
                          "columna y aplicarlo mal cambiaria el resultado."}

    consulta = entrada.get("filter") or {}
    condiciones = consulta.get("Where") or []
    if len(condiciones) != 1:
        return {**base, "state": "untranslated",
                "reason": f"La consulta del filtro tiene {len(condiciones)} "
                          "condiciones; solo se traduce una."}
    traducido = _condicion_categorica((condiciones[0] or {}).get("Condition"))
    if traducido is None:
        return {**base, "state": "untranslated",
                "reason": f"Condicion de tipo '{tipo}' que este lector no "
                          "traduce (solo lista de valores)."}
    return {**base, "state": "applied", **traducido}


def read_filters(nodo: Any, *, scope: str) -> List[Dict[str, Any]]:
    """Lee el `filterConfig` de un informe, una pagina o un visual."""
    if not isinstance(nodo, dict):
        return []
    config = nodo.get("filterConfig")
    if not isinstance(config, dict):
        return []
    return [leer_filtro(f, scope=scope) for f in (config.get("filters") or [])]


def resumen(filtros: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Agrupa por estado para que quien exporta pueda declararlo tal cual."""
    aplicados = [f for f in filtros if f.get("state") == "applied"]
    sin_traducir = [f for f in filtros if f.get("state") == "untranslated"]
    sin_seleccion = [f for f in filtros if f.get("state") == "unset"]
    return {"applied": aplicados, "untranslated": sin_traducir,
            "unset": sin_seleccion,
            "trustworthy": not sin_traducir}
