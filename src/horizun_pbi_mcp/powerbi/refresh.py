"""Refresh local del modelo activo (Power BI Desktop) via TOM.

IMPORTANTE: esto refresca el modelo LOCAL abierto en Power BI Desktop, no el
Power BI Service. Requiere que las credenciales de origen esten configuradas en
Desktop; si no, el motor devolvera un error de credenciales/origen.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.clr_bootstrap import load_tom
from horizun_pbi_mcp.powerbi.errors import RefreshError, TableNotFoundError, ValidationError
from horizun_pbi_mcp.powerbi.model_reader import connect, lease_active_model

log = get_logger("refresh")

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
) -> Dict[str, Any]:
    key = str(refresh_type).lower().strip()
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
            with connect(model) as (_server, _db, mdl):
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
                mdl.SaveChanges()  # dispara el refresh de forma sincrona
                objetivo_conteo = (_nombres_de_tablas(mdl)
                                   if refreshed == ["<todo el modelo>"]
                                   else list(refreshed))
        except (TableNotFoundError, ValidationError):
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
