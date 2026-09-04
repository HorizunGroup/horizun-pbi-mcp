"""Que runtime hay, cual fue el ultimo bueno, y como fue el ultimo intento.

Vive en la RAIZ del directorio de datos, no dentro de una carpeta de version:
las carpetas de version se renombran en cada promocion, y un archivo de estado
dentro de lo que se mueve no puede describir el movimiento.

**Tres hechos independientes, tres campos.** Esto es lo que faltaba y por lo que
`install-status.json` no bastaba: ese archivo mezclaba "como fue el ultimo
intento" con "hay algo que arranque", asi que una actualizacion fallida escribia
`failed` y con eso el lanzador entendia que no habia NADA -aunque el runtime
anterior siguiera entero en disco, con sus 134 tools-. Los dos hechos tienen que
poder coexistir: *la actualizacion de N fallo* y *N−1 sigue sirviendo* son
verdad a la vez, y es exactamente el caso que mas importa.

  activo           el runtime que se esta sirviendo ahora
  last_known_good  el ultimo que SUPERO el handshake MCP, con su evidencia
  ultimo_intento   como acabo la ultima instalacion, con su error si lo hubo

Un registro sin evidencia no es un registro. Que una carpeta contenga un
`python.exe` no la hace un runtime al que volver: puede ser una siembra a
medias, un venv sin el paquete o los restos de una actualizacion rota. Por eso
`last_known_good` guarda QUE se comprobo -servidor, version y cuantas tools
contesto- y quien lo lee exige que ese registro este completo antes de
proponerlo como alternativa.

Este modulo solo lee y escribe. La politica -si la carpeta sigue contenida en
la raiz y si el interprete existe- vive en `plugin_bootstrap`, que es quien ya
conoce la forma de las rutas y la usan tanto el instalador como el lanzador.

Solo biblioteca estandar: corre con el Python anfitrion antes de que exista
ningun entorno.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

NOMBRE = "runtime-state.json"

#: Version del formato. Lo que no cuadre se trata como "no hay estado", que es
#: seguro: se vuelve a construir en la siguiente instalacion buena.
ESQUEMA = 1

#: Resultados posibles del ultimo intento.
RESULTADOS = ("ok", "failed")


class EstadoRuntimeCorrupto(RuntimeError):
    """El estado existe, pero no puede reescribirse sin destruir evidencia."""


def vacio() -> dict[str, Any]:
    return {"esquema": ESQUEMA, "activo": None, "last_known_good": None,
            "ultimo_intento": None, "degradado": None}


def evidencia(carpeta: str, *, version: str, servidor: str, tools: int,
              verificado: float | None = None) -> dict[str, Any]:
    """Un registro de runtime comprobado. `carpeta` es un NOMBRE, no una ruta.

    Igual que en el journal de promocion, y por el mismo motivo: un archivo del
    directorio de datos no puede decidir a que ruta absoluta apunta el
    lanzador. Quien lo lee reconstruye la ruta bajo la raiz y la valida.
    """
    return {"carpeta": carpeta, "version": version, "servidor": servidor,
            "tools": int(tools),
            "verificado": time.time() if verificado is None else verificado}


def _registro(valor: Any) -> dict[str, Any] | None:
    """Normaliza un registro leido de disco. Incompleto = inexistente."""
    if not isinstance(valor, dict):
        return None
    carpeta, servidor = valor.get("carpeta"), valor.get("servidor")
    version = valor.get("version")
    for texto in (carpeta, servidor, version):
        if not isinstance(texto, str) or not texto:
            return None
    try:
        tools = int(valor["tools"])
    except (KeyError, TypeError, ValueError):
        return None
    if tools < 1:
        return None
    return {"carpeta": carpeta, "version": version, "servidor": servidor,
            "tools": tools, "verificado": valor.get("verificado")}


def leer(root: Path) -> dict[str, Any]:
    """El estado, siempre normalizado. Nunca lanza: un estado ilegible es vacio."""
    try:
        datos = json.loads((Path(root) / NOMBRE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return vacio()
    if not isinstance(datos, dict) or datos.get("esquema") != ESQUEMA:
        return vacio()
    salida = vacio()
    salida["activo"] = _registro(datos.get("activo"))
    salida["last_known_good"] = _registro(datos.get("last_known_good"))
    intento = datos.get("ultimo_intento")
    if isinstance(intento, dict) and intento.get("resultado") in RESULTADOS:
        salida["ultimo_intento"] = intento
    degradado = datos.get("degradado")
    if isinstance(degradado, dict) and degradado.get("carpeta") \
            and degradado.get("motivo"):
        salida["degradado"] = degradado
    return salida


def escribir(root: Path, estado: dict[str, Any]) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    estado = dict(estado, esquema=ESQUEMA)
    destino = root / NOMBRE
    if destino.exists():
        try:
            json.loads(destino.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EstadoRuntimeCorrupto(
                f"{destino} existe pero no contiene JSON legible; se conserva "
                "intacto para diagnostico"
            ) from exc

    # Nombre unico: un temporal fijo podia pisar el vestigio de otra escritura
    # interrumpida antes de que el cerrojo llegara a diagnosticarla.
    tmp = destino.with_name(f".{destino.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("x", encoding="utf-8") as archivo:
            archivo.write(json.dumps(estado, indent=2, ensure_ascii=False))
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(tmp, destino)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def registrar_promocion(root: Path, *, nuevo: dict[str, Any],
                        anterior_apartado: str | None) -> dict[str, Any]:
    """Publica `nuevo` como activo y decide que pasa a ser el last-known-good.

    La regla, y merece explicarse porque es donde estaba el agujero: el
    last-known-good pasa a ser el runtime que ACABAMOS de apartar, pero **solo
    si ese era el activo comprobado**. Si el destino que se aparto era otra
    cosa -una carpeta recien creada con el `install-status.json` y nada mas,
    que es justo lo que hay al actualizar desde una version distinta-, apartarlo
    no genera ningun N−1 al que volver, y quedarse con el como last-known-good
    seria peor que no tener ninguno: mentiria.
    """
    estado = leer(root)
    activo_previo = estado["activo"]

    if activo_previo:
        if anterior_apartado and activo_previo["carpeta"] == nuevo["carpeta"]:
            # Reinstalacion de la MISMA version: lo que estaba sirviendo es
            # justo lo que se acaba de apartar. Cambia de nombre y sigue siendo
            # el N−1, con su evidencia intacta.
            estado["last_known_good"] = dict(activo_previo,
                                             carpeta=anterior_apartado)
        elif activo_previo["carpeta"] != nuevo["carpeta"]:
            # Salto de version: el runtime anterior vive en OTRA carpeta y
            # nadie la ha tocado. Sigue siendo el ultimo bueno, con su nombre.
            # Sin esta rama, actualizar de 1.5.4 a 2.0.0 dejaba el estado sin
            # N−1 y la limpieza se llevaba por delante la unica carpeta que
            # arrancaba.
            estado["last_known_good"] = activo_previo
    # Si no habia activo previo se conserva el last-known-good que hubiera:
    # quien lo lee comprueba que su carpeta siga existiendo, asi que un
    # registro que se quede sin carpeta se descarta solo.

    estado["activo"] = nuevo
    estado["ultimo_intento"] = {"resultado": "ok", "version": nuevo["version"],
                                "ts": time.time()}
    # Promover es la salida de una degradacion: lo que acaba de superar el
    # handshake no puede seguir marcado como roto. Si no se limpiara aqui, la
    # reinstalacion arreglaria el runtime y dejaria el estado mintiendo.
    estado["degradado"] = None
    escribir(root, estado)
    return estado


def registrar_degradacion(root: Path, *, carpeta: str, motivo: str,
                          fase: str | None = None) -> dict[str, Any]:
    """Marca que el runtime de `carpeta` ya NO es operativo, y por que.

    Va aparte de `ultimo_intento` porque son hechos distintos y de momentos
    distintos: *la ultima instalacion salio bien* puede convivir con *lo que
    instalo ya no arranca*. Un runtime se corrompe DESPUES de instalarse -un
    antivirus se lleva un archivo, alguien borra el paquete-, y meter las dos
    cosas en un campo obligaba a que una borrase a la otra.
    """
    estado = leer(root)
    estado["degradado"] = {"carpeta": carpeta, "motivo": motivo, "fase": fase,
                           "ts": time.time()}
    escribir(root, estado)
    return estado


def registrar_fallo(root: Path, *, version: str, error: str,
                    paso: str | None = None) -> dict[str, Any]:
    """Anota que el intento fallo SIN tocar activo ni last-known-good.

    Esta es la mitad que faltaba: fallar una actualizacion no puede borrar la
    constancia de que hay algo que si arranca.
    """
    estado = leer(root)
    estado["ultimo_intento"] = {"resultado": "failed", "version": version,
                                "error": error, "paso": paso, "ts": time.time()}
    escribir(root, estado)
    return estado
