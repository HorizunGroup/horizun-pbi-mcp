"""Refresh local del modelo activo (Power BI Desktop) via TOM.

IMPORTANTE: esto refresca el modelo LOCAL abierto en Power BI Desktop, no el
Power BI Service. Requiere que las credenciales de origen esten configuradas en
Desktop; si no, el motor devolvera un error de credenciales/origen.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.clr_bootstrap import load_tom
from horizun_pbi_mcp.powerbi.errors import (RefreshError, RefreshTimeoutError,
                                            TableNotFoundError, ValidationError)
from horizun_pbi_mcp.powerbi.model_reader import connect, lease_active_model

log = get_logger("refresh")

#: Plazo por defecto del refresh, en segundos.
#:
#: No es el valor propuesto en el informe de campo (120 s): un modelo mediano
#: tarda legitimamente mas de dos minutos y cancelarlo seria romper lo que
#: funcionaba. 600 s sigue estando muy por debajo de los 1800 s a los que el
#: cliente MCP se rinde, que es el problema real: en vez de perder la sesion
#: entera sin explicacion, se devuelve un error accionable.
_TIMEOUT_POR_DEFECTO = 600
#: Margen para que el motor atienda la cancelacion antes de dar por perdido
#: el hilo. Sirve para poder DECIR si el comando quedo corriendo o no.
_GRACIA_TRAS_CANCELAR = 15.0

#: Conectores de Power Query que exigen credenciales configuradas en Desktop.
#: Un refresh lanzado por XMLA no puede mostrar el dialogo que las pide.
_CONECTORES_CON_AUTENTICACION = (
    "SharePoint.Files", "SharePoint.Contents", "SharePoint.Tables",
    "Web.Contents", "Web.BrowserContents", "OData.Feed", "Odbc.DataSource",
    "Odbc.Query", "Sql.Database", "Sql.Databases", "AnalysisServices.Database",
    "AzureStorage.Blobs", "AzureStorage.DataLake", "AzureStorage.Tables",
    "Salesforce.Data", "Salesforce.Reports", "Snowflake.Databases",
    "GoogleAnalytics.Accounts", "Dynamics365.Data", "PowerBI.Datasets",
    "PostgreSQL.Database", "MySQL.Database", "Oracle.Database",
    "Exchange.Contents", "Facebook.Graph",
)

_TYPE_ALIASES = {
    "full": "Full",
    "calculate": "Calculate",
    "clear_values": "ClearValues",
    "clearvalues": "ClearValues",
    "automatic": "Automatic",
    "data_only": "DataOnly",
    "dataonly": "DataOnly",
}


def refresh_model(
    session: Session,
    refresh_type: str = "full",
    tables: Optional[List[str]] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    key = str(refresh_type).lower().strip()
    if timeout_seconds is None:
        timeout_seconds = _TIMEOUT_POR_DEFECTO
    try:
        timeout_seconds = int(timeout_seconds)
    except (TypeError, ValueError):
        raise ValidationError(
            "timeout_seconds debe ser un entero de segundos (0 lo desactiva).",
            details={"parameter": "timeout_seconds"}) from None
    if timeout_seconds > 86_400:
        raise ValidationError(
            "timeout_seconds no puede superar 86400 (24 h).",
            details={"parameter": "timeout_seconds"})
    if key not in _TYPE_ALIASES:
        raise ValidationError(
            f"Tipo de refresh no valido: '{refresh_type}'. "
            f"Usa uno de: {sorted(set(_TYPE_ALIASES))}."
        )

    # Una lista vacia no es lo mismo que omitir `tables`. Interpretarla como
    # falso hacia que una peticion sin objetivos refrescara TODO el modelo.
    if tables is not None and not tables:
        raise ValidationError(
            "tables no puede ser una lista vacia. Omite el parametro para "
            "refrescar todo el modelo.",
            details={"parameter": "tables"},
        )
    if tables is not None:
        for index, name in enumerate(tables):
            if not isinstance(name, str) or not name.strip():
                raise ValidationError(
                    f"tables[{index}] debe ser un nombre de tabla no vacio.",
                    details={"parameter": "tables", "index": index},
                )

    # Las entradas invalidas se rechazan antes de descubrir/conectar al motor.
    # El lease queda por fuera del traductor de errores de refresh: una sesion
    # obsoleta debe seguir saliendo como `stale_session`, no disfrazada de un
    # fallo de credenciales/origen.
    with lease_active_model(session) as model:
        TOM = load_tom()
        rt = getattr(TOM.RefreshType, _TYPE_ALIASES[key])
        start = time.perf_counter()
        try:
            with connect(model) as (server, _db, mdl):
                refreshed: List[str] = []
                if tables is not None:
                    by_name = {str(t.Name).casefold(): t for t in mdl.Tables}
                    targets = []
                    seen = set()
                    # Resolver y validar TODO antes de llamar RequestRefresh.
                    # Esa llamada deja una operacion pendiente en el objeto
                    # TOM aunque todavia no se haya ejecutado SaveChanges.
                    for tn in tables:
                        key_name = tn.strip().casefold()
                        tobj = by_name.get(key_name)
                        if tobj is None:
                            raise TableNotFoundError(
                                f"La tabla '{tn}' no existe en el modelo.",
                                details={
                                    "available": [t.Name for t in mdl.Tables]},
                            )
                        if key_name not in seen:
                            targets.append(tobj)
                            seen.add(key_name)
                    for tobj in targets:
                        tobj.RequestRefresh(rt)
                        refreshed.append(tobj.Name)
                else:
                    mdl.RequestRefresh(rt)
                    refreshed = ["<todo el modelo>"]
                # Dispara el refresh de forma sincrona, con plazo y
                # cancelacion: sin esto un origen sin credenciales cuelga la
                # llamada para siempre.
                _guardar_con_plazo(server, mdl, timeout_seconds)
                objetivo_conteo = (_nombres_de_tablas(mdl)
                                   if refreshed == ["<todo el modelo>"]
                                   else list(refreshed))
        except (TableNotFoundError, ValidationError, RefreshTimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001
            from horizun_pbi_mcp.services import redaction

            msg = getattr(exc, "Message", None) or str(exc)
            raise RefreshError(
                f"Fallo el refresh local: {redaction.texto(msg)}",
                details={"refresh_type": _TYPE_ALIASES[key], "tables": tables},
            ) from exc

        # El conteo va DENTRO del lease y contra el MISMO modelo que se
        # refresco. Fuera, otra llamada concurrente a pbi_select_model podia
        # cambiar la sesion entre el SaveChanges y el conteo, y rows_by_table
        # habria listado las tablas de un modelo que jamas se refresco,
        # presentado como verificacion del refresh. Es la carrera exacta que
        # el lease existe para impedir.
        duration_s = round(time.perf_counter() - start, 2)
        conteos, avisos_conteo = _contar_filas(model, objetivo_conteo)
    salida = {
        "status": "ok",
        "refresh_type": _TYPE_ALIASES[key],
        "tables": refreshed,
        "duration_s": duration_s,
        "scope": "local (Power BI Desktop)",
        "note": _nota_de_persistencia(session),
    }
    if conteos is not None:
        salida["rows_by_table"] = conteos
        vacias = sorted(t for t, n in conteos.items() if n == 0)
        if vacias:
            avisos_conteo.append(
                "El refresh termino bien pero estas tablas cargaron CERO "
                f"filas: {vacias}. Revisa el origen o el filtro de la consulta.")
    if avisos_conteo:
        salida["warnings"] = avisos_conteo
    return salida


def _expresiones_m(mdl) -> List[str]:
    """Todo el texto M del modelo: expresiones compartidas y particiones."""
    textos: List[str] = []
    try:
        for expresion in mdl.Expressions:
            texto = getattr(expresion, "Expression", None)
            if texto:
                textos.append(str(texto))
    except Exception:                                        # noqa: BLE001
        pass
    try:
        for tabla in mdl.Tables:
            for particion in tabla.Partitions:
                texto = getattr(getattr(particion, "Source", None),
                                "Expression", None)
                if texto:
                    textos.append(str(texto))
    except Exception:                                        # noqa: BLE001
        pass
    return textos


def _origenes_que_piden_credenciales(mdl) -> List[str]:
    """Origenes del modelo que REQUIEREN credenciales guardadas en Desktop.

    Ojo con lo que esta funcion NO hace: **no comprueba si las credenciales
    estan configuradas**. Power BI Desktop las guarda en su propio almacen, al
    que TOM no da acceso, asi que no hay forma soportada de verificarlo. Decir
    "este origen no tiene credenciales" seria inventarse una comprobacion.

    Lo que si se puede afirmar es cuales las PIDEN, y eso basta para convertir
    un cuelgue mudo en un mensaje que dice donde mirar.

    Nunca lanza: es informacion de apoyo para un error, no puede ser la causa
    de uno nuevo.
    """
    encontrados: Dict[str, None] = {}
    try:
        for texto in _expresiones_m(mdl):
            for conector in _CONECTORES_CON_AUTENTICACION:
                for coincidencia in re.finditer(
                        re.escape(conector) + r"\s*\(\s*\"([^\"]{0,400})\"",
                        texto):
                    destino = coincidencia.group(1)
                    host = ""
                    try:
                        analizada = urlparse(destino)
                        host = analizada.netloc or ""
                    except ValueError:
                        host = ""
                    encontrados[f"{conector}({host})" if host else conector] = None
                if conector in texto and not any(
                        clave.startswith(conector) for clave in encontrados):
                    encontrados[conector] = None
    except Exception:                                        # noqa: BLE001
        return []
    return sorted(encontrados)


def _cancelar_comando(server) -> bool:
    """Pide al motor que cancele el comando en curso. True si la llamada paso."""
    try:
        server.CancelCommand()
        return True
    except Exception as exc:                                 # noqa: BLE001
        log.warning("No se pudo cancelar el refresh: %s", type(exc).__name__)
        return False


def _guardar_con_plazo(server, mdl, timeout_seconds: int) -> None:
    """Ejecuta `SaveChanges()` con plazo, cancelando si se agota.

    `SaveChanges()` es SINCRONO y bloqueante: es la llamada que dispara el
    refresh. Si un origen espera credenciales, el motor no responde y esta
    llamada no vuelve nunca -1800 s medidos, hasta que el cliente MCP se
    rindio-. Por eso corre en un hilo aparte: es la unica forma de recuperar
    el control para pedir la cancelacion.

    `timeout_seconds <= 0` desactiva el plazo y restaura el comportamiento
    bloqueante anterior, para quien tenga un refresh legitimamente larguisimo.
    """
    if timeout_seconds <= 0:
        mdl.SaveChanges()
        return

    caja: Dict[str, Any] = {}

    def trabajo() -> None:
        try:
            mdl.SaveChanges()
            caja["ok"] = True
        except BaseException as exc:                         # noqa: BLE001
            caja["exc"] = exc

    hilo = threading.Thread(target=trabajo, name="horizun-refresh", daemon=True)
    hilo.start()
    hilo.join(timeout_seconds)

    if not hilo.is_alive():
        if "exc" in caja:
            raise caja["exc"]
        return

    cancelado = _cancelar_comando(server)
    hilo.join(_GRACIA_TRAS_CANCELAR)
    sigue_corriendo = hilo.is_alive()

    origenes = _origenes_que_piden_credenciales(mdl)
    detalle = (f" Origenes que requieren credenciales: {origenes}."
               if origenes else "")
    cola = ("" if not sigue_corriendo else
            " El motor no confirmo la cancelacion dentro del margen: el "
            "comando puede seguir ejecutandose en Power BI Desktop.")
    raise RefreshTimeoutError(
        f"El refresh supero los {timeout_seconds} s y se pidio cancelarlo. Un "
        "refresh lanzado por XMLA no puede mostrar el dialogo de credenciales "
        "de Power BI Desktop: si un origen no las tiene guardadas, el motor "
        "espera indefinidamente y no hay ninguna ventana que cerrar. Abre el "
        "informe en Desktop y autentica el origen una vez (Cuenta "
        f"organizativa); despues este refresh funciona.{detalle}{cola}",
        details={
            "timeout_seconds": timeout_seconds,
            "cancel_requested": cancelado,
            "cancel_confirmed": not sigue_corriendo,
            "sources_requiring_credentials": origenes,
            # Se enumeran los origenes que PIDEN credenciales; no se comprueba
            # si estan guardadas, porque TOM no expone el almacen de Desktop.
            "credentials_verified": False,
        })


def _nombres_de_tablas(model) -> List[str]:
    """Tablas del modelo TOM, como texto."""
    return [str(t.Name) for t in model.Tables]


def _contar_filas(modelo, objetivo: List[str]):
    """Filas por tabla despues del refresh. `(conteos|None, avisos)`.

    Por que existe: un refresh puede terminar en `success` y haber cargado
    CERO filas -credenciales que devuelven un conjunto vacio, un filtro de
    fecha que no alcanza ninguna fila, un origen que cambio de esquema-. Decir
    "ok" sin mirar el resultado es justo lo que el servidor promete no hacer:
    no reportar trabajo que no se verifico.

    `modelo` es el ActiveModel DEL LEASE bajo el que se refresco, y `objetivo`
    los nombres ya resueltos contra ese mismo modelo: esta funcion no vuelve a
    consultar la sesion a proposito, porque entre el SaveChanges y el conteo la
    sesion puede haber cambiado de modelo y el conteo debe ser del que se
    refresco, no del que este activo ahora.

    Nunca lanza. Si no se puede contar, se devuelve `None` y un aviso: es
    preferible admitir que no se comprobo a inventarse un numero, y desde
    luego a tumbar un refresh que si funciono.
    """
    from horizun_pbi_mcp.powerbi.adomd_client import AdomdClient

    avisos: List[str] = []
    if not objetivo:
        return {}, avisos

    conteos: Dict[str, int] = {}
    try:
        with AdomdClient(modelo.connection_string, modelo.catalog) as cli:
            for nombre in objetivo:
                # Una consulta por tabla, no una UNION: si una falla -una tabla
                # DirectQuery, una calculada con error- se pierde solo ese dato
                # en vez de quedarnos sin ninguno.
                escapado = str(nombre).replace("'", "''")
                try:
                    valor = cli.execute_scalar(
                        f"EVALUATE ROW(\"n\", COUNTROWS('{escapado}'))")
                except Exception:                        # noqa: BLE001
                    avisos.append(f"No se pudo contar las filas de '{nombre}'.")
                    continue
                conteos[str(nombre)] = int(valor) if valor is not None else 0
    except Exception as exc:                             # noqa: BLE001
        return None, [f"No se pudieron contar las filas: {type(exc).__name__}."]

    return conteos, avisos


#: Lo que guardar SI persiste, segun el formato del proyecto abierto.
_NOTA_PBIP = (
    "Refresh local aplicado. Los datos viven en la sesion de Power BI Desktop: "
    "un proyecto .pbip guarda solo la DEFINICION (TMDL + PBIR), no los datos, "
    "asi que al reabrirlo el modelo viene vacio y hay que refrescar otra vez. "
    "Lo que si persiste al guardar son los cambios de definicion."
)
_NOTA_SIN_PROYECTO = (
    "Refresh local aplicado. Si el informe abierto es un .pbip, los datos NO se "
    "guardan con el proyecto (solo la definicion) y habra que refrescar al "
    "reabrir; un .pbix si los almacena."
)


def _nota_de_persistencia(session: Session) -> str:
    """Que sobrevive a guardar, segun el formato REAL del proyecto activo.

    La nota anterior era una sola frase -«Guarda en Power BI Desktop para
    persistirlo»- y en `.pbip` es sencillamente falsa: ese formato guarda
    definicion (TMDL + PBIR), no datos. Al reabrir, el modelo viene vacio y hay
    que refrescar de nuevo.

    Importa mas de lo que parece porque una nota dentro de una respuesta de
    tool no se lee como una opinion: el agente la repite al usuario como un
    hecho. Si no se sabe el formato no se afirma ninguno de los dos: se explica
    la diferencia y ya.
    """
    try:
        activo = session.active_pbip
    except Exception:                                    # noqa: BLE001
        return _NOTA_SIN_PROYECTO
    if activo is None:
        return _NOTA_SIN_PROYECTO
    return _NOTA_PBIP
