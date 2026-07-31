"""Tools de exploracion y auditoria del modelo semantico (Macrofase B).

Todas aceptan `source: live|pbip`, asi que funcionan con Power BI Desktop
abierto o sobre los archivos TMDL del proyecto. Son de SOLO LECTURA.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import get_session, get_settings
from powerbi import model_reader
from powerbi.errors import ValidationError
from pbip import tmdl_reader
from services import model_audit, model_explorer
from tools._common import guard
from utils.file_utils import atomic_write_text, timestamp

_FUENTES = ("live", "pbip")


def load_model(source: str = "live") -> Dict[str, Any]:
    """Lee el modelo de la fuente indicada y lo normaliza."""
    s = (source or "live").lower()
    if s not in _FUENTES:
        raise ValidationError(f"source invalido: '{source}'. Usa live|pbip.")
    session = get_session()
    if s == "pbip":
        return tmdl_reader.read_semantic_model(session.require_active_pbip())
    return model_reader.read_model(session)


def register(mcp) -> None:

    @mcp.tool()
    def pbi_model_summary(source: str = "live") -> Dict[str, Any]:
        """Resumen compacto del modelo, pensado para leerlo de un vistazo.

        Conteos, tablas con su tamano, medidas por tabla, columnas calculadas,
        tablas desconectadas, relaciones bidireccionales y referencias rotas.
        Es la primera tool que conviene llamar para orientarse en un modelo.
        `source`: 'live' (Desktop abierto) o 'pbip' (archivos TMDL).
        """
        return guard(lambda: model_explorer.summary(load_model(source)))

    @mcp.tool()
    def pbi_search_model(term: str, kinds: Optional[List[str]] = None,
                         limit: int = 50, source: str = "live") -> Dict[str, Any]:
        """Busca objetos del modelo por nombre (y en el DAX de las medidas).

        `term`: texto a buscar, sin distinguir mayusculas.
        `kinds`: filtra por tipo — table, column, measure, hierarchy, role.
        Para las medidas indica si coincidio el nombre o la expresion.
        """
        return guard(lambda: model_explorer.search(
            load_model(source), term, kinds=kinds, limit=limit))

    @mcp.tool()
    def pbi_get_object(kind: str, name: str, source: str = "live") -> Dict[str, Any]:
        """Devuelve un objeto del modelo con todo su detalle.

        `kind`: table | column | measure. Para una columna usa 'Tabla[Columna]'.
        En una medida incluye ademas las referencias que aparecen en su DAX.
        """
        return guard(lambda: model_explorer.get_object(load_model(source), kind, name))

    @mcp.tool()
    def pbi_measure_dependencies(name: str, depth: int = 3,
                                 source: str = "live") -> Dict[str, Any]:
        """De que depende una medida y quien depende de ella.

        Devuelve dependencias directas (medidas, columnas y referencias ROTAS),
        el cierre transitivo sobre medidas hasta `depth`, y la lista de medidas
        que la usan. Analisis lexico: detecta referencias escritas, no las
        construidas dinamicamente.
        """
        return guard(lambda: model_explorer.measure_dependencies(
            load_model(source), name, profundidad=depth))

    @mcp.tool()
    def pbi_column_dependencies(table: str, column: str,
                                source: str = "live") -> Dict[str, Any]:
        """Que usa una columna: medidas, columnas calculadas, relaciones y jerarquias.

        Util antes de ocultar o eliminar una columna: dice si algo se rompe.
        """
        return guard(lambda: model_explorer.column_dependencies(
            load_model(source), table, column))

    @mcp.tool()
    def pbi_list_hierarchies(source: str = "live") -> Dict[str, Any]:
        """Lista las jerarquias del modelo con sus niveles y columnas."""
        def _impl():
            md = load_model(source)
            h = md.get("hierarchies", [])
            return {"count": len(h), "hierarchies": h,
                    "warnings": ([] if h or source == "live" else
                                 ["El lector TMDL no extrae jerarquias; usa source='live'."])}
        return guard(_impl)

    @mcp.tool()
    def pbi_list_roles(source: str = "live") -> Dict[str, Any]:
        """Lista los roles de seguridad (RLS) y sus filtros por tabla."""
        def _impl():
            md = load_model(source)
            roles = md.get("roles", [])
            return {"count": len(roles), "roles": roles,
                    "warnings": ([] if roles or source == "live" else
                                 ["El lector TMDL no extrae roles; usa source='live'."])}
        return guard(_impl)

    @mcp.tool()
    def pbi_list_perspectives(source: str = "live") -> Dict[str, Any]:
        """Lista las perspectivas del modelo.

        Requiere la capa EN VIVO: el lector TMDL de este proyecto no las extrae.
        Si no hay ninguna, devuelve una lista vacia con la explicacion.
        """
        def _impl():
            md = load_model(source)
            p = md.get("perspectives", [])
            return {"count": len(p), "perspectives": p,
                    "supported": "perspectives" in md,
                    "warnings": ([] if "perspectives" in md else
                                 ["Las perspectivas no estan disponibles en esta "
                                  "fuente. Se reporta como no soportado, no como "
                                  "ausencia."])}
        return guard(_impl)

    @mcp.tool()
    def pbi_list_partitions(source: str = "live") -> Dict[str, Any]:
        """Lista las particiones por tabla (modo de almacenamiento y origen)."""
        def _impl():
            md = load_model(source)
            particiones = []
            for t in md.get("tables", []):
                for p in t.get("partitions", []) or []:
                    particiones.append({"table": t["name"], **p})
            return {"count": len(particiones), "partitions": particiones,
                    "supported": any("partitions" in t for t in md.get("tables", [])),
                    "warnings": ([] if particiones else
                                 ["No se detectaron particiones en esta fuente."])}
        return guard(_impl)

    @mcp.tool()
    def pbi_audit_model(source: str = "live", rules: Optional[List[str]] = None,
                        min_severity: str = "info") -> Dict[str, Any]:
        """Audita el modelo semantico con reglas de identificador estable.

        Cada hallazgo trae `rule`, `severity`, `object`, `evidence`,
        `recommendation` y `auto_fix_available`. Ninguna heuristica se presenta
        como certeza: la evidencia acompana siempre al hallazgo.
        `rules`: subconjunto de reglas (ver pbi_list_audit_rules).
        `min_severity`: info | warning | error.
        """
        return guard(lambda: model_audit.audit(
            load_model(source), rules=rules, min_severity=min_severity))

    @mcp.tool()
    def pbi_list_audit_rules() -> Dict[str, Any]:
        """Catalogo de reglas de auditoria disponibles, con su dominio y severidad."""
        def _impl():
            reglas = model_audit.reglas_disponibles()
            return {"count": len(reglas), "rules": reglas,
                    "domains": sorted({r["domain"] for r in reglas})}
        return guard(_impl)
