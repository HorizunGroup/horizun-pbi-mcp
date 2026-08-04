"""Redaccion de datos sensibles en errores, detalles y logs.

Que se filtraba
---------------
- `ConnectionFailedError` incluia la connection string ENTERA en `details`;
- `DaxQueryError` incluia 2000 caracteres de la consulta;
- los mensajes del propio motor de Analysis Services suelen repetir la
  consulta completa dentro del texto del error.

Una consulta DAX no es inofensiva: lleva nombres de tablas y medidas del
negocio, y a menudo valores literales filtrados (un NIF, un cliente, un
importe). Una connection string lleva la ruta local del `.pbix` y, contra
servicios remotos, credenciales o tokens. Ninguna de las dos deberia acabar en
un log, en la respuesta a un cliente MCP, ni en un informe pegado en un ticket.

Que se conserva
---------------
Lo justo para diagnosticar: el host y el puerto de la conexion, la longitud y
la forma de la consulta, y el mensaje del motor con la consulta recortada. No
se oculta la causa del fallo —eso haria el error inutil—, solo el contenido.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

#: Marcador uniforme, para que se vea que hubo redaccion y no un fallo.
OCULTO = "[redactado]"

#: Claves de una connection string que jamas se devuelven.
_CLAVES_SECRETAS = ("password", "pwd", "access token", "accesstoken", "token",
                    "secret", "client secret", "clientsecret", "api key",
                    "apikey", "user id", "uid", "app id")

_RE_DATA_SOURCE = re.compile(r"data\s*source\s*=\s*([^;]+)", re.IGNORECASE)
_RE_CATALOG = re.compile(r"(initial\s+catalog|catalog|database)\s*=\s*([^;]+)",
                         re.IGNORECASE)
_RE_TOKEN = re.compile(
    r"\b(?:eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"
    r"|[A-Za-z0-9_\-]{32,})\b")


def connection_string(cs: Optional[str]) -> str:
    """Deja solo el destino: `Data Source=host:puerto`. Nada mas.

    Con Power BI Desktop el destino es siempre `localhost:<puerto efimero>`, que
    es exactamente lo que hace falta para diagnosticar y no dice nada sensible.
    """
    if not cs:
        return OCULTO
    m = _RE_DATA_SOURCE.search(str(cs))
    if not m:
        return OCULTO
    destino = m.group(1).strip()
    # Una ruta de archivo como Data Source tambien es informacion personal.
    if os.sep in destino or (":" in destino and not destino.split(":")[-1].isdigit()):
        return OCULTO
    return f"Data Source={destino}"


def dax(query: Optional[str], *, maximo: int = 120) -> Dict[str, Any]:
    """Descripcion de una consulta SIN el texto completo.

    Se conserva un prefijo corto porque sin el es imposible saber de que
    consulta hablamos cuando fallan varias seguidas.
    """
    if not query:
        return {"length": 0, "preview": ""}
    texto = str(query)
    prefijo = " ".join(texto[:maximo].split())
    return {"length": len(texto),
            "preview": prefijo + ("..." if len(texto) > maximo else ""),
            "truncated": len(texto) > maximo}


def texto(mensaje: Optional[str], *, query: Optional[str] = None) -> str:
    """Limpia un mensaje: connection strings, tokens, la consulta y rutas.

    Los mensajes del motor suelen incrustar la consulta entera; si se conoce, se
    sustituye explicitamente antes de pasar por los patrones genericos.
    """
    if not mensaje:
        return ""
    salida = str(mensaje)

    if query and len(str(query).strip()) >= 12:
        salida = salida.replace(str(query).strip(), OCULTO)

    for clave in _CLAVES_SECRETAS:
        salida = re.sub(rf"({re.escape(clave)}\s*=\s*)([^;]+)",
                        rf"\1{OCULTO}", salida, flags=re.IGNORECASE)

    salida = _RE_DATA_SOURCE.sub(lambda m: _sustituir_data_source(m), salida)
    salida = _RE_CATALOG.sub(rf"\1={OCULTO}", salida)
    salida = _RE_TOKEN.sub(OCULTO, salida)
    return rutas(salida)


def _sustituir_data_source(m: re.Match) -> str:
    destino = m.group(1).strip()
    if os.sep in destino or "/" in destino:
        return f"Data Source={OCULTO}"
    return m.group(0)


def rutas(valor: str) -> str:
    """Sustituye el directorio personal por `~`.

    Evita que un informe o un log publiquen `C:\\Users\\<nombre>\\...`, que
    identifica a una persona y a menudo tambien a su organizacion.
    """
    if not valor:
        return valor
    salida = str(valor)
    home = os.path.expanduser("~")
    for variante in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
        if variante and len(variante) > 3:
            salida = re.sub(re.escape(variante), "~", salida, flags=re.IGNORECASE)
    return salida


def detalles(d: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Limpia el dict de `details` de un error antes de devolverlo."""
    if not d:
        return {}
    salida: Dict[str, Any] = {}
    for clave, valor in d.items():
        bajo = str(clave).lower()
        if bajo in ("connection_string", "connectionstring"):
            salida[clave] = connection_string(valor)
        elif bajo in ("query", "dax", "expression"):
            salida[clave] = dax(valor)
        elif isinstance(valor, str):
            salida[clave] = texto(valor)
        else:
            salida[clave] = valor
    return salida
