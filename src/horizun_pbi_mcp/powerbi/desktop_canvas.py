"""Lo que muestra de verdad el lienzo de una ventana de Desktop.

Por que existe
--------------
`data_loaded` se calculaba contando filas EN EL MOTOR. Es una pregunta
distinta de la que importa en una captura: si los visuales de la ventana
pintaron esos datos. En la corrida real del «Comite de obra» las dos
respuestas se separaron: el refresh por XMLA dejo el motor con filas
(`data_loaded: true`) y, dos segundos despues, la ventana seguia mostrando
«(En blanco)» en todas las tarjetas, graficas vacias y dos avisos de Power BI:
«Se han modificado una o varias relaciones y es necesario actualizar
manualmente» y «Algunas de las tablas tienen datos incompletos o no tienen
datos». La tool respondio exito con esa foto.

Aqui se lee el texto accesible de la ventana (UI Automation, en el proceso
aparte de siempre) y se clasifica. Solo se devuelven CONTEOS y los avisos de
Power BI que coinciden; el texto del informe no sale de aqui.

Lo que se promete y lo que no
-----------------------------
- `not_rendered`: Power BI mismo dice que faltan datos o que hay que
  actualizar. Es evidencia fuerte: la captura no representa el informe.
- `blank_values`: hay valores «(En blanco)» sin aviso. Puede ser legitimo (una
  medida que devuelve BLANK()); se avisa, no se decide.
- `no_blank_signals`: no se vio ninguna de esas señales. NO demuestra que haya
  datos pintados: solo que no se vio lo contrario.
- `unknown`: no se pudo leer la ventana. No se afirma nada.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError

log = get_logger("desktop_canvas")

#: Avisos de la propia ventana de Power BI que dicen que lo pintado NO
#: corresponde a los datos del modelo. Español e ingles, que son los idiomas
#: medidos.
AVISOS_SIN_DATOS = (
    ("relationships_need_refresh", re.compile(
        r"(relaci[oó]n(es)?\b.*actualizar\s+manualmente"
        r"|relationships?\b.*(refresh|manually))", re.I)),
    ("tables_without_data", re.compile(
        r"(datos\s+incompletos|no\s+tienen\s+datos"
        r"|incomplete\s+data|(contain|have)\s+no\s+data"
        r"|(don'?t|do\s+not)\s+(contain|have)\s+(any\s+)?data)", re.I)),
)

#: El rotulo con que una tarjeta o una celda pinta un valor vacio.
VALOR_EN_BLANCO = re.compile(r"^\s*\(\s*(en\s+blanco|blank)\s*\)\s*$", re.I)

#: Estados posibles del lienzo, de peor a mejor evidencia.
NO_PINTADO = "not_rendered"
VALORES_EN_BLANCO = "blank_values"
SIN_SENALES = "no_blank_signals"
DESCONOCIDO = "unknown"

#: Cuanto se espera, como mucho, a que la ventana deje de mostrar los avisos
#: despues de un refresh. El plazo real lo recorta `capture_timeout`.
ESPERA_TRAS_REFRESH = 45.0
#: Cada cuanto se vuelve a mirar. Cada lectura es un proceso aparte (~1 s).
INTERVALO = 1.5


def clasificar_textos(textos: Iterable[str]) -> Dict[str, Any]:
    """Clasifica los textos accesibles de la ventana. Funcion pura.

    Devuelve conteos y los avisos reconocidos (texto recortado), nunca los
    textos del informe.
    """
    vistos = 0
    en_blanco = 0
    avisos: List[Dict[str, str]] = []
    claves_vistas = set()
    for texto in textos:
        if not isinstance(texto, str) or not texto.strip():
            continue
        vistos += 1
        if VALOR_EN_BLANCO.match(texto):
            en_blanco += 1
            continue
        for clave, patron in AVISOS_SIN_DATOS:
            if clave not in claves_vistas and patron.search(texto):
                claves_vistas.add(clave)
                avisos.append({"kind": clave, "text": texto.strip()[:160]})
    if not vistos:
        estado = DESCONOCIDO
    elif avisos:
        estado = NO_PINTADO
    elif en_blanco:
        estado = VALORES_EN_BLANCO
    else:
        estado = SIN_SENALES
    return {"state": estado, "texts_seen": vistos,
            "blank_values": en_blanco, "desktop_warnings": avisos}


def _leer_con_helper(pid: int, started: Optional[float],
                     timeout: float) -> Dict[str, Any]:
    """Lee la ventana en el proceso aparte. Puede lanzar."""
    from horizun_pbi_mcp.powerbi import desktop_helper

    return desktop_helper.ejecutar({
        "action": "read_canvas",
        "desktop_pid": int(pid),
        "desktop_started": started,
    }, timeout=timeout)


def leer_lienzo(opened: Any, *, timeout: float = 20.0) -> Dict[str, Any]:
    """Estado del lienzo de la ventana de `opened`. Nunca lanza."""
    pid = getattr(opened, "desktop_pid", None)
    if not pid:
        return {"state": DESCONOCIDO, "available": False,
                "reason": "la sesion no identifica el proceso de Desktop"}
    try:
        respuesta = _leer_con_helper(int(pid),
                                     getattr(opened, "desktop_started", None),
                                     timeout)
    except PowerBIMCPError as exc:
        return {"state": DESCONOCIDO, "available": False,
                "reason": f"{exc.code}: {str(exc.message)[:160]}"}
    except Exception as exc:                              # noqa: BLE001
        return {"state": DESCONOCIDO, "available": False,
                "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
    estado = respuesta.get("canvas") or {}
    if estado.get("state") not in (NO_PINTADO, VALORES_EN_BLANCO, SIN_SENALES,
                                   DESCONOCIDO):
        return {"state": DESCONOCIDO, "available": False,
                "reason": "el asistente de interfaz no clasifico el lienzo"}
    return {**estado, "available": estado["state"] != DESCONOCIDO}


def esperar_lienzo(opened: Any, *, plazo: float,
                   leer: Optional[Callable[[Any], Dict[str, Any]]] = None,
                   intervalo: float = INTERVALO,
                   reloj: Callable[[], float] = time.monotonic,
                   dormir: Callable[[float], None] = time.sleep
                   ) -> Dict[str, Any]:
    """Relee el lienzo mientras Power BI diga que faltan datos, hasta `plazo`.

    Es la sincronizacion que faltaba tras un refresh: dos fotogramas iguales
    no prueban nada si la ventana aun no empezo a repintar. Termina en cuanto
    el lienzo deja de estar `not_rendered` o no se puede leer; si el plazo se
    agota con los avisos puestos, lo devuelve tal cual para que se diga.
    """
    leer = leer or leer_lienzo
    inicio = reloj()
    lecturas = 0
    while True:
        estado = leer(opened)
        lecturas += 1
        transcurrido = reloj() - inicio
        if estado.get("state") != NO_PINTADO or transcurrido >= plazo:
            return {**estado, "reads": lecturas,
                    "waited_seconds": round(transcurrido, 1),
                    "wait_exhausted": estado.get("state") == NO_PINTADO}
        dormir(min(intervalo, max(0.0, plazo - transcurrido)))
