"""Argumentos que la tool no declara: nunca se descartan en silencio.

FastMCP valida los argumentos con un modelo pydantic que IGNORA las claves que
no conoce. Auditoria del 2026-11-07 contra Desktop real: un agente llamo
`pbi_align_visuals(..., alignment="top")` —el parametro es `edge`—, la clave se
perdio sin aviso, la tool alineo con el defecto (`edge="left"`) y monto un
grafico sobre otro. La respuesta decia `ok: true`.

Regla, aplicada en un solo sitio para las 140 tools:

- Tool que escribe (todo lo que `tools/risk.py` no declara de solo lectura):
  se rechaza ANTES de ejecutar nada con `unknown_arguments`, nombrando cada
  clave desconocida y el parametro que probablemente se quiso decir. Ejecutar
  con defectos lo que el cliente no pidio es exactamente el fallo.
- Tool de solo lectura: se ejecuta y la respuesta lo dice en
  `ignored_arguments` y en `warnings`. Leer de mas no cambia nada.

Es aditivo para el contrato: ningun parametro cambia, y una llamada con
argumentos validos se comporta igual que antes.
"""
from __future__ import annotations

import difflib
from typing import Any, Dict, Iterable, List

from horizun_pbi_mcp.services import envelope

CODIGO = "unknown_arguments"


def _sugerencias(extras: Iterable[str], conocidos: Iterable[str]) -> Dict[str, List[str]]:
    candidatos = sorted(conocidos)
    return {e: difflib.get_close_matches(e, candidatos, n=2, cutoff=0.5)
            for e in extras}


def _mensaje(nombre: str, extras: List[str], sugerencias: Dict[str, List[str]],
             conocidos: Iterable[str]) -> str:
    partes = []
    for e in extras:
        s = sugerencias.get(e) or []
        partes.append(f"'{e}' (quisiste decir '{s[0]}'?)" if s else f"'{e}'")
    return (f"{nombre} no tiene {'el parametro' if len(extras) == 1 else 'los parametros'} "
            f"{', '.join(partes)}. Parametros validos: {', '.join(sorted(conocidos))}.")


def instalar(mcp) -> None:
    """Envuelve `call_tool` del gestor de tools de FastMCP."""
    from horizun_pbi_mcp.tools.risk import annotations_for

    manager = mcp._tool_manager
    original = manager.call_tool

    async def call_tool(name: str, arguments: Dict[str, Any], context=None,
                        convert_result: bool = False) -> Any:
        tool = manager.get_tool(name)
        argumentos = arguments or {}
        if tool is None or not isinstance(argumentos, dict):
            return await original(name, arguments, context=context,
                                  convert_result=convert_result)
        conocidos = set(((tool.parameters or {}).get("properties") or {}).keys())
        extras = sorted(k for k in argumentos if k not in conocidos)
        if not extras:
            return await original(name, arguments, context=context,
                                  convert_result=convert_result)

        sugerencias = _sugerencias(extras, conocidos)
        mensaje = _mensaje(name, extras, sugerencias, conocidos)
        detalles = {"unknown_arguments": extras, "suggestions": sugerencias,
                    "valid_arguments": sorted(conocidos)}

        if not annotations_for(name).get("readOnlyHint"):
            rid = argumentos.get("request_id")
            resultado: Any = envelope.failure(
                CODIGO, mensaje + " No se ejecuto nada.", detalles,
                operation=name,
                request_id=rid if isinstance(rid, str) and rid else envelope.new_request_id(),
                duration_ms=0.0)
        else:
            resultado = await tool.run(arguments, context=context, convert_result=False)
            if isinstance(resultado, dict):
                resultado = dict(resultado)
                resultado["ignored_arguments"] = detalles
                avisos = list(resultado.get("warnings") or [])
                avisos.append(mensaje + " Se ignoraron y la lectura uso los valores por defecto.")
                resultado["warnings"] = avisos
                if resultado.get("status") == envelope.SUCCESS:
                    resultado["status"] = envelope.WARNING
        if convert_result:
            return tool.fn_metadata.convert_result(resultado)
        return resultado

    manager.call_tool = call_tool
