"""El brief de intencion: para que existe este tablero, dicho por su dueño.

La pieza que faltaba un nivel por encima de todas las tools: el usuario sabe
para que quiere el tablero y no habia donde ponerlo. Sin ese dato, la
propuesta se deduce solo del modelo, el sistema de diseño se elige a ciegas y
una auditoria puede decir "hay 12 hallazgos" pero nunca "esto no cumple lo que
dijiste que querias".

Decisiones de diseño (tomadas con el usuario, 2026-08-03):

- **Vive como artefacto versionado DENTRO del proyecto** (`pbi-brief.json`,
  junto al `.pbip`): sobrevive a la sesion, viaja con el repositorio, y el
  tablero lleva dentro su porque. No en memoria del servidor ni en un
  servicio externo.
- **Las respuestas son del humano.** El agente PREGUNTA -para que es, quien
  lo mira, que decisiones sostiene- y escribe lo que le contesten. Un brief
  inventado por el agente es peor que ninguno: fija en un archivo con
  autoridad lo que nadie dijo.
- **Los consumidores son la razon de ser**: la guia lo enseña, la propuesta lo
  adjunta y el sistema de diseño se recomienda desde `delivery`. Un brief que
  nada lee es un formulario, no una herramienta.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError

log = get_logger("brief")

#: Nombre del artefacto, junto al .pbip. FUERA de .Report/.SemanticModel a
#: proposito: esos arboles los reescribe Power BI Desktop al guardar, y un
#: archivo ajeno alli puede desaparecer sin aviso. La raiz del proyecto es del
#: usuario (y de su git); Desktop no la toca.
BRIEF_FILENAME = "pbi-brief.json"

SCHEMA_VERSION = "1.0"

#: Como se va a ver el tablero. Decide el sistema de diseño recomendado:
#: no es una preferencia estetica, es legibilidad fisica (a que distancia se
#: lee) — la misma razon por la que existen los tres sistemas.
DELIVERY = ("pantalla_sala", "escritorio", "lectura_pdf", "movil")

_DELIVERY_A_SISTEMA = {
    "pantalla_sala": ("sala", "se lee a metros y con luz baja: lienzo "
                      "1920x1080 y tipografia grande"),
    "escritorio": ("informe", "se lee de cerca en pantalla: lienzo 1280x720 "
                   "y tipografia de lectura"),
    "lectura_pdf": ("informe", "se exporta y se lee de cerca: el sistema de "
                    "lectura es el que aguanta la impresion"),
    "movil": ("informe", "no hay sistema de lienzo movil todavia: 'informe' "
              "es el que menos sufre reescalado — limite conocido, no "
              "una recomendacion entusiasta"),
}


class BriefError(PowerBIMCPError):
    code = "brief_error"


def brief_path(active) -> Path:
    return Path(active.project_dir) / BRIEF_FILENAME


# ------------------------------------------------------------- validacion ---
def _lista_de_textos(valor: Any, campo: str, obligatoria: bool = False) -> List[str]:
    if valor is None:
        if obligatoria:
            raise ValidationError(f"El brief necesita '{campo}'.")
        return []
    if isinstance(valor, str):
        valor = [valor]
    if (not isinstance(valor, list)
            or any(not isinstance(x, str) or not x.strip() for x in valor)):
        raise ValidationError(
            f"'{campo}' debe ser una lista de textos no vacios.")
    return [x.strip() for x in valor]


def validate_brief(datos: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza y valida. Devuelve el brief canonico que se escribira."""
    if not isinstance(datos, dict):
        raise ValidationError("El brief debe ser un objeto.")

    proposito = str(datos.get("purpose") or "").strip()
    if not proposito:
        raise ValidationError(
            "El brief necesita 'purpose': para que existe este tablero. Si no "
            "lo sabes, PREGUNTALO — un proposito inventado por el agente fija "
            "con autoridad lo que nadie dijo.")
    audiencia = str(datos.get("audience") or "").strip()
    if not audiencia:
        raise ValidationError(
            "El brief necesita 'audience': quien lo va a mirar. Cambia el "
            "sistema de diseño, el nivel de detalle y que es 'grave'.")

    entrega = datos.get("delivery")
    if entrega is not None:
        entrega = str(entrega).strip().casefold()
        if entrega not in DELIVERY:
            raise ValidationError(
                f"delivery '{datos.get('delivery')}' no existe. Opciones: "
                f"{list(DELIVERY)}.")

    criticos: List[Dict[str, Any]] = []
    for i, c in enumerate(datos.get("critical_fields") or []):
        if not isinstance(c, dict) or not str(c.get("field") or "").strip():
            raise ValidationError(
                f"critical_fields[{i}] necesita 'field' (Tabla[Campo] o "
                "[Medida]).")
        entrada = {"field": str(c["field"]).strip(),
                   "why": str(c.get("why") or "").strip()}
        for lim in ("min", "max"):
            if c.get(lim) is not None:
                if not isinstance(c[lim], (int, float)):
                    raise ValidationError(
                        f"critical_fields[{i}].{lim} debe ser numerico.")
                entrada[lim] = c[lim]
        criticos.append(entrada)

    canonico: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "purpose": proposito,
        "audience": audiencia,
        "decisions": _lista_de_textos(datos.get("decisions"), "decisions"),
        "key_questions": _lista_de_textos(datos.get("key_questions"),
                                          "key_questions"),
        "non_goals": _lista_de_textos(datos.get("non_goals"), "non_goals"),
        "updated": date.today().isoformat(),
    }
    if entrega:
        canonico["delivery"] = entrega
    if datos.get("update_cadence"):
        canonico["update_cadence"] = str(datos["update_cadence"]).strip()
    if criticos:
        canonico["critical_fields"] = criticos
    return canonico


# ------------------------------------------------------------ lectura/uso ---
def read_brief(active) -> Optional[Dict[str, Any]]:
    """El brief del proyecto, o None si no se ha definido. Nunca lanza por
    ausencia; SI lanza por un brief corrupto -callarselo seria peor-."""
    ruta = brief_path(active)
    if not ruta.exists():
        return None
    from horizun_pbi_mcp.utils.json_utils import read_json

    datos = read_json(ruta)
    if not isinstance(datos, dict) or not datos.get("purpose"):
        raise BriefError(
            f"El brief en {BRIEF_FILENAME} existe pero no tiene 'purpose': "
            "esta corrupto o editado a mano. Re-defínelo con pbi_define_brief.")
    return datos


def recommended_system(brief: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Sistema de diseño recomendado por el brief, con su motivo fisico."""
    if not brief or not brief.get("delivery"):
        return None
    sistema, motivo = _DELIVERY_A_SISTEMA[brief["delivery"]]
    return {"system": sistema, "why": motivo, "from_delivery": brief["delivery"]}


def write_brief(active, datos: Dict[str, Any]) -> Dict[str, Any]:
    """Valida y escribe el brief, transaccionado como todo lo del proyecto."""
    from horizun_pbi_mcp.services import txn as txn_service

    canonico = validate_brief(datos)
    ruta = brief_path(active)
    existia = ruta.exists()
    cm = txn_service.project_transaction(active, [ruta],
                                         tool="pbi_define_brief")
    with cm as tx:
        tx.write_json(ruta, canonico)
    log.info("Brief %s: %s", "actualizado" if existia else "creado",
             canonico["purpose"][:60])
    return {"brief": canonico, "path": str(ruta),
            "created": not existia, "updated": existia,
            "recommended_design_system": recommended_system(canonico),
            "transaction": cm.result}
