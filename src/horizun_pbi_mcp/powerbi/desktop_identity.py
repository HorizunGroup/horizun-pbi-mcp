"""Quien es cada instancia de Power BI Desktop, y con que evidencia.

El listado de modelos abiertos decia `pid`, y ese `pid` era el de
`msmdsrv.exe` -el motor tabular- no el de `PBIDesktop.exe`. Son dos procesos
distintos: el motor es un HIJO de la ventana, arranca despues y muere antes.
Confundirlos tiene consecuencias concretas: se cierra el proceso equivocado,
se atribuye un modelo a la ventana equivocada, o se da por buena para
`C:\\a\\Mi.pbip` una instancia que en realidad esta sirviendo `C:\\b\\Otro.pbix`
solo porque aparecio mientras esperabamos.

Que se puede demostrar y que no
-------------------------------
- **Con un .pbix o .pbit**: Desktop mantiene el archivo ABIERTO mientras el
  informe esta cargado. El descriptor lo delata y la ruta es un hecho.
- **Con un .pbip**: NO hay ningun descriptor sobre la carpeta del proyecto.
  Lo unico correlacionable es el TITULO de la ventana, que da el nombre pero
  nunca la ruta. Aqui la ruta se queda en `null` y la confianza baja: preferir
  `unknown` a una ruta inventada es justo el punto de este modulo.

Nada de lo que hay aqui escribe, cierra ni toca ningun proceso: es lectura.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("desktop_identity")

#: Extensiones que Power BI Desktop abre como documento.
DOCUMENTOS = (".pbix", ".pbit", ".pbip")

#: Confianzas posibles de la identificacion.
HIGH, MEDIUM, LOW, UNKNOWN = "high", "medium", "low", "unknown"

#: Sufijo que Power BI Desktop pone al nombre del documento en el titulo de
#: la ventana. Se quita antes de comparar: el nombre del documento es lo que
#: identifica, y el sufijo cambia con el idioma y con el producto.
_SUFIJO_TITULO = re.compile(r"\s*[-–—]\s*power bi(\s+desktop)?\s*$",
                            re.IGNORECASE)

#: Titulos que Desktop muestra MIENTRAS carga o cuando aun no hay documento.
#: No identifican nada: verlos significa "todavia no", no "es otro".
_TITULOS_PROVISIONALES = frozenset({
    "", "sin titulo", "sin título", "untitled", "sans titre", "ohne titel",
    "senza titolo", "sem titulo", "sem título", "sense titol", "sense títol",
    # El nombre del producto a secas: la ventana de arranque y los dialogos
    # genericos de Desktop se titulan asi. No nombran ningun documento.
    "power bi desktop", "power bi",
})

#: Estados del sondeo de identidad de una ventana.
IDENTIDAD_ASENTADA = "settled_match"
IDENTIDAD_PROVISIONAL = "provisional"
IDENTIDAD_OTRO_DOCUMENTO = "mismatch"
IDENTIDAD_SIN_TITULO = "no_window"
IDENTIDAD_TIMEOUT = "timeout"


def nombre_de_documento(titulo: str) -> str:
    """El nombre del documento que hay en un titulo de ventana, normalizado.

    `Demo - Power BI Desktop` y `Demo` son la misma ventana en dos momentos:
    con el sufijo mientras arranca la interfaz y sin el despues. Comparar el
    titulo entero hacia que una ventana ya cargada pareciera OTRO documento
    solo por el sufijo.
    """
    return _SUFIJO_TITULO.sub("", str(titulo or "").strip()).strip().casefold()


def titulo_provisional(titulo: str) -> bool:
    """True si el titulo dice 'cargando', no 'este documento'."""
    return nombre_de_documento(titulo) in _TITULOS_PROVISIONALES


def _normalizar(valor: str | Path) -> str:
    texto = str(valor)
    if texto.startswith("\\\\?\\"):
        texto = texto[4:]
    return os.path.normcase(os.path.normpath(texto))


def proceso_desktop(engine_pid: Optional[int]) -> Optional[Any]:
    """El `PBIDesktop.exe` ANTECESOR del motor. Nunca el motor mismo."""
    if not engine_pid:
        return None
    import psutil

    try:
        proceso = psutil.Process(int(engine_pid))
        for padre in proceso.parents():
            if (padre.name() or "").casefold() == "pbidesktop.exe":
                return padre
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return None
    return None


def documentos_abiertos(pid: Optional[int]) -> List[str]:
    """Documentos que ese proceso tiene abiertos. Vacio no prueba nada."""
    if not pid:
        return []
    import psutil

    try:
        proceso = psutil.Process(int(pid))
        return sorted({a.path for a in proceso.open_files()
                       if Path(a.path).suffix.casefold() in DOCUMENTOS})
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        return []


def titulos_de_ventana(pid: Optional[int]) -> List[str]:
    """Titulos de las ventanas del proceso. Solo existe en Windows."""
    if not pid or os.name != "nt":
        return []
    try:
        from horizun_pbi_mcp.powerbi.desktop_capture import _enumerate_windows
    except Exception as exc:                              # noqa: BLE001
        log.debug("Sin enumeracion de ventanas: %s", exc)
        return []
    try:
        return [w.title.strip() for w in _enumerate_windows(int(pid))
                if w.title and w.title.strip()]
    except Exception as exc:                              # noqa: BLE001
        log.debug("No se pudieron leer los titulos del pid %s: %s", pid, exc)
        return []


def _titulo_coincide(titulos: List[str], objetivo: Path) -> bool:
    """Coincidencia EXACTA con el nombre del proyecto, sin extension.

    Parcial no vale: un titulo que solo CONTIENE el nombre puede ser otro
    informe, y equivocarse aqui significa escribir sobre el que no era. Lo
    unico que se tolera es el sufijo del producto, que no es parte del nombre.
    """
    esperado = objetivo.stem.strip().casefold()
    return any(nombre_de_documento(t) == esperado for t in titulos)


def clasificar_titulos(titulos: List[str], objetivo: Path) -> str:
    """Que dicen los titulos de una ventana sobre el documento pedido.

    Tres respuestas y ninguna se confunde con otra: coincide, es provisional
    (todavia cargando) o es demostrablemente OTRO documento. Un titulo
    provisional NO autoriza a actuar, pero tampoco condena: se sigue
    esperando. Uno distinto y estable si condena.
    """
    if not titulos:
        return IDENTIDAD_SIN_TITULO
    if _titulo_coincide(titulos, objetivo):
        return IDENTIDAD_ASENTADA
    if any(titulo_provisional(t) for t in titulos):
        # Un Desktop sirve UN documento; si su ventana aun dice "cargando",
        # los demas titulos son dialogos, no otro documento.
        return IDENTIDAD_PROVISIONAL
    return IDENTIDAD_OTRO_DOCUMENTO


def esperar_identidad_de_ventana(pid: Optional[int], objetivo: str | Path, *,
                                 timeout: float = 60.0,
                                 cada: float = 1.0,
                                 titulos: Any = None) -> Dict[str, Any]:
    """Sondea el titulo de la ventana hasta que se ASIENTE, con tope.

    Que el motor tabular responda no significa que la ventana haya terminado
    de abrir: durante medio minuto el titulo dice `Sin titulo` y despues pasa
    al nombre del documento. Rechazar en ese intervalo era el fallo real -la
    misma peticion funcionaba treinta segundos despues-.

    Devuelve SIEMPRE la evidencia: estado final, titulos observados, cuantas
    lecturas y cuanto se espero. Un titulo estable de OTRO documento termina
    el sondeo enseguida con `mismatch`: esperar mas no lo va a convertir en el
    nuestro, y actuar sobre el seria escribir en el informe equivocado.
    """
    leer = titulos or titulos_de_ventana
    destino = Path(objetivo)
    inicio = time.monotonic()
    limite = inicio + max(0.0, float(timeout))
    observados: List[str] = []
    lecturas = 0
    estado = IDENTIDAD_SIN_TITULO
    while True:
        actuales = [str(t) for t in (leer(pid) or [])]
        lecturas += 1
        for t in actuales:
            if t not in observados:
                observados.append(t)
        estado = clasificar_titulos(actuales, destino)
        if estado in (IDENTIDAD_ASENTADA, IDENTIDAD_OTRO_DOCUMENTO):
            break
        if time.monotonic() >= limite:
            estado = IDENTIDAD_TIMEOUT if estado != IDENTIDAD_ASENTADA                 else estado
            break
        time.sleep(max(0.05, min(cada, limite - time.monotonic())))
    return {
        "status": estado,
        "settled": estado == IDENTIDAD_ASENTADA,
        "expected_document": destino.stem,
        "titles_observed": observados[:10],
        "last_titles": actuales[:10],
        "polls": lecturas,
        "waited_seconds": round(time.monotonic() - inicio, 1),
        "timeout": float(timeout),
    }


def identify(instance: Dict[str, Any], *,
             target: Optional[str | Path] = None) -> Dict[str, Any]:
    """Identidad verificable de una instancia del motor.

    `target`, si se indica, es la ruta que se quiere confirmar. Lo que no se
    pueda demostrar sale como `null` o `unknown`; nunca se adivina.
    """
    engine_pid = instance.get("pid")
    evidencia: List[Dict[str, Any]] = []
    salida: Dict[str, Any] = {
        "engine_pid": engine_pid,
        "desktop_pid": None,
        "desktop_process_started": None,
        "desktop_window_title": None,
        "project_path": None,
        "path_match": None,
        "identity_confidence": UNKNOWN,
        "identity_evidence": evidencia,
    }
    if engine_pid is None:
        evidencia.append({"signal": "engine_pid", "status": "missing",
                          "detail": "la instancia se descubrio por archivo de "
                                    "puerto, sin proceso asociado"})
        return salida
    evidencia.append({"signal": "engine_pid", "status": "ok",
                      "value": int(engine_pid),
                      "detail": "proceso msmdsrv.exe que sirve el modelo"})

    desktop = proceso_desktop(engine_pid)
    if desktop is None:
        evidencia.append({"signal": "desktop_parent", "status": "not_found",
                          "detail": "no se pudo remontar del motor a un "
                                    "PBIDesktop.exe antecesor"})
        return salida

    import psutil

    salida["desktop_pid"] = desktop.pid
    try:
        salida["desktop_process_started"] = float(desktop.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError, ValueError):
        salida["desktop_process_started"] = None
    evidencia.append({"signal": "desktop_parent", "status": "ok",
                      "value": desktop.pid,
                      "detail": "PBIDesktop.exe antecesor del motor"})
    confianza = LOW

    titulos = titulos_de_ventana(desktop.pid)
    if titulos:
        salida["desktop_window_title"] = titulos[0]
        evidencia.append({"signal": "window_title", "status": "ok",
                          "value": titulos[0]})
    else:
        evidencia.append({"signal": "window_title", "status": "unavailable"})

    documentos = documentos_abiertos(desktop.pid)
    if documentos:
        salida["project_path"] = documentos[0]
        evidencia.append({"signal": "open_document", "status": "ok",
                          "value": documentos[0],
                          "detail": "descriptor abierto sobre el archivo"})
        confianza = HIGH
    else:
        evidencia.append({
            "signal": "open_document", "status": "not_found",
            "detail": ("un .pbip no deja descriptor sobre la carpeta del "
                       "proyecto; la ruta no se puede demostrar asi")})

    if target is None:
        salida["identity_confidence"] = confianza
        return salida

    objetivo = Path(target).expanduser()
    try:
        objetivo = objetivo.resolve()
    except OSError:                                       # pragma: no cover
        pass

    if documentos:
        # Si hay documento probado, MANDA el documento. Una instancia que
        # sirve otro archivo no vale para esta ruta por mucho que haya
        # aparecido durante nuestra espera.
        coincide = any(_normalizar(d) == _normalizar(objetivo)
                       for d in documentos)
        salida["path_match"] = coincide
        salida["identity_confidence"] = HIGH
        evidencia.append({
            "signal": "path_match", "status": "ok", "value": coincide,
            "detail": ("el documento abierto es el pedido" if coincide else
                       "el documento abierto es OTRO archivo")})
        return salida

    if titulos and _titulo_coincide(titulos, objetivo):
        salida["path_match"] = True
        salida["identity_confidence"] = MEDIUM
        evidencia.append({
            "signal": "path_match", "status": "by_title",
            "value": True,
            "detail": ("el titulo de la ventana coincide exactamente con el "
                       "nombre del proyecto; la RUTA sigue sin demostrarse")})
        return salida

    if titulos:
        salida["path_match"] = False
        salida["identity_confidence"] = MEDIUM
        evidencia.append({
            "signal": "path_match", "status": "by_title", "value": False,
            "detail": "ninguna ventana se llama como el proyecto pedido"})
        return salida

    salida["path_match"] = None
    salida["identity_confidence"] = LOW
    evidencia.append({
        "signal": "path_match", "status": "undetermined",
        "detail": ("sin descriptor ni titulo legible no hay forma de ligar "
                   "esta instancia con la ruta pedida")})
    return salida


def annotate(instances: List[Dict[str, Any]], *,
             target: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Anade la identidad a cada instancia, sin quitarle nada."""
    salida = []
    for instancia in instances:
        datos = dict(instancia)
        try:
            datos.update(identify(instancia, target=target))
        except Exception as exc:                          # noqa: BLE001
            # Identificar es informacion adicional: que falle no puede tumbar
            # un listado que ya es correcto sin ella.
            log.debug("No se pudo identificar la instancia %s: %s",
                      instancia.get("port"), exc)
            datos.setdefault("identity_confidence", UNKNOWN)
            datos.setdefault("identity_evidence",
                             [{"signal": "error", "status": "failed",
                               "detail": f"{type(exc).__name__}"}])
        salida.append(datos)
    return salida
