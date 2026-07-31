"""Clasificador conservador de consultas de solo lectura.

ALCANCE, PARA QUE NADIE SE CONFIE: esto **no es un parser de DAX**. Es un
clasificador lexico deliberadamente estrecho. Reconoce un conjunto cerrado de
formas y rechaza TODO lo demas, incluido lo que probablemente seria inofensivo.
Politica fail-closed: ante la duda, se rechaza.

Funciona en dos pasos, y ese orden es la clave:

1. ESCANEO LEXICO. Se recorre el texto caracter a caracter reconociendo
   comentarios (`//`, `--`, un solo nivel de `/* */`), cadenas (`"..."`, escape
   `""`), identificadores citados (`'...'`, escape `''`) y entre corchetes
   (`[...]`, escape `]]`). Su CONTENIDO se sustituye por un centinela opaco.
   Por eso `EVALUATE ROW("DROP TABLE", 1)` sigue siendo una lectura: la palabra
   peligrosa vive dentro de una cadena y nunca llega al clasificador.

2. CLASIFICACION. Solo sobre el residuo. La consulta se permite unicamente si
   su ESTRUCTURA COMPLETA encaja en una forma reconocida. Encontrar un
   `EVALUATE` en cualquier posicion no basta.

Formas permitidas:
    EVALUATE ...                 (uno o varios bloques)
    DEFINE ... EVALUATE ...      (definiciones de ambito de consulta)
    SELECT ... FROM $SYSTEM....  (DMV de descubrimiento)

No hay escape ni override: en la Fase 1A la politica no se puede desactivar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from powerbi.errors import PowerBIMCPError


class DaxNotReadOnlyError(PowerBIMCPError):
    """La consulta no encaja en ninguna forma reconocida de solo lectura.

    Se define aqui, y no en `powerbi.errors`, para no ampliar la jerarquia
    publica fuera del alcance autorizado de la Fase 1A. Hereda de
    `PowerBIMCPError`, asi que `tools._common.guard()` la serializa igual que
    cualquier otro error de dominio.
    """

    code = "dax_not_read_only"


# Centinela con el que se sustituye el contenido de literales y comentarios.
# Es un caracter de control: no puede aparecer en el residuo por otra via, y
# va rodeado de espacios para no pegarse a los tokens vecinos.
_SENTINEL = "\x01"

# Palabra: admite `$SYSTEM.TMSCHEMA_TABLES` y letras Unicode.
_WORD = re.compile(r"\$?[^\W\d]\w*(?:\.[^\W\d]\w*)*", re.UNICODE)

# Tokens que implican modificacion o ejecucion fuera de una consulta de lectura.
_FORBIDDEN = {
    "CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE", "MERGE",
    "REFRESH", "BACKUP", "RESTORE", "ATTACH", "DETACH", "SYNCHRONIZE",
    "PROCESS", "CALL", "EXECUTE", "EXEC", "IMAGELOAD", "IMAGESAVE",
    "TRUNCATE", "GRANT", "REVOKE", "SET", "USE",
}

_DMV_PREFIX = "$SYSTEM."


@dataclass
class ScanResult:
    residual: str
    error: Optional[str] = None
    literals: int = 0
    comments: int = 0


@dataclass
class Classification:
    allowed: bool
    form: Optional[str] = None
    reason: Optional[str] = None
    tokens: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        out = {"allowed": self.allowed, "form": self.form}
        if self.reason:
            out["reason"] = self.reason
        return out


def scan(text: str) -> ScanResult:
    """Neutraliza comentarios y literales. Devuelve el residuo estructural."""
    if text.startswith("﻿"):          # BOM: artefacto de codificacion
        text = text[1:]

    out: List[str] = []
    i, n = 0, len(text)
    literals = comments = 0

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        # --- comentarios de linea ---
        if (ch == "/" and nxt == "/") or (ch == "-" and nxt == "-"):
            comments += 1
            while i < n and text[i] not in "\r\n":
                i += 1
            out.append(" ")
            continue

        # --- comentario de bloque (DAX no los anida) ---
        if ch == "/" and nxt == "*":
            comments += 1
            end = text.find("*/", i + 2)
            if end == -1:
                return ScanResult("", error="comentario de bloque sin cerrar (/*)")
            i = end + 2
            out.append(" ")
            continue

        # --- literales y identificadores delimitados ---
        if ch in ('"', "'", "["):
            closing = {'"': '"', "'": "'", "[": "]"}[ch]
            j = i + 1
            cerrado = False
            while j < n:
                if text[j] == closing:
                    if j + 1 < n and text[j + 1] == closing:   # escape por duplicado
                        j += 2
                        continue
                    cerrado = True
                    break
                j += 1
            if not cerrado:
                nombre = {'"': "cadena", "'": "identificador citado",
                          "[": "identificador entre corchetes"}[ch]
                return ScanResult("", error=f"{nombre} sin cerrar ({ch})")
            literals += 1
            out.append(f" {_SENTINEL} ")
            i = j + 1
            continue

        out.append(ch)
        i += 1

    return ScanResult("".join(out), literals=literals, comments=comments)


def _reject(reason: str, tokens: List[str]) -> Classification:
    return Classification(allowed=False, reason=reason, tokens=tokens)


def classify(query: str) -> Classification:
    """Clasifica una consulta. `allowed=True` solo para formas reconocidas."""
    if not isinstance(query, str) or not query.strip():
        return _reject("La consulta esta vacia.", [])

    stripped = query.lstrip("﻿ \t\r\n")

    # XMLA / SOAP: ADOMD acepta XML como texto de comando.
    if stripped.startswith("<"):
        return _reject(
            "Parece una peticion XMLA (empieza por '<'). Solo se admiten "
            "consultas DAX de lectura o DMVs de $SYSTEM.", [])

    sc = scan(query)
    if sc.error:
        return _reject(
            f"No se pudo analizar la consulta: {sc.error}. Se rechaza por "
            "precaucion: un delimitador sin cerrar cambia el significado del resto.",
            [])

    residual = sc.residual

    # `;` como separador de sentencias es ambiguo: podria colar una segunda
    # sentencia no reconocida. DAX no lo necesita.
    if ";" in residual:
        return _reject(
            "La consulta contiene ';' fuera de cadenas y comentarios. Podria "
            "encadenar varias sentencias; se rechaza por ambiguo.", [])

    words = [w.upper() for w in _WORD.findall(residual)]
    if not words:
        return _reject(
            "Tras descartar comentarios y literales no queda ninguna sentencia.", [])

    prohibidas = [w for w in words if w in _FORBIDDEN]
    if prohibidas:
        return _reject(
            f"Contiene la palabra reservada de modificacion '{prohibidas[0]}' "
            "fuera de cadenas y comentarios.", words)

    first = words[0]

    if first == "DEFINE":
        if "EVALUATE" not in words:
            return _reject(
                "DEFINE sin EVALUATE: no es una consulta completa. Las "
                "definiciones son de ambito de consulta y necesitan un EVALUATE "
                "que las use.", words)
        if "SELECT" in words:
            return _reject(
                "Mezcla DEFINE/EVALUATE con SELECT; se rechaza por ambiguo.", words)
        return Classification(True, form="define_evaluate", tokens=words)

    if first == "EVALUATE":
        if "SELECT" in words:
            return _reject(
                "Mezcla EVALUATE con SELECT; se rechaza por ambiguo.", words)
        return Classification(True, form="evaluate", tokens=words)

    if first == "SELECT":
        if "FROM" not in words:
            return _reject(
                "SELECT sin FROM: no se reconoce como DMV de solo lectura.", words)
        idx = words.index("FROM")
        if idx + 1 >= len(words):
            return _reject("SELECT ... FROM sin origen.", words)
        origen = words[idx + 1]
        if not origen.startswith(_DMV_PREFIX) or len(origen) <= len(_DMV_PREFIX):
            return _reject(
                f"Solo se admiten DMVs de descubrimiento: el origen debe empezar "
                f"por '$SYSTEM.' y nombrar un rowset. Se recibio '{origen}'.", words)
        if words.count("FROM") > 1:
            return _reject(
                "Varios FROM en una DMV; se rechaza por ambiguo.", words)
        return Classification(True, form="dmv_select", tokens=words)

    return _reject(
        f"La consulta no empieza por EVALUATE, DEFINE ni SELECT (empieza por "
        f"'{first}'). Solo se admiten esas formas.", words)


def assert_read_only(query: str) -> Classification:
    """Clasifica y lanza `DaxNotReadOnlyError` si no esta permitida."""
    result = classify(query)
    if not result.allowed:
        raise DaxNotReadOnlyError(
            f"Consulta rechazada por la politica de solo lectura: {result.reason}",
            details={
                "policy": "read_only_fail_closed",
                "allowed_forms": ["EVALUATE ...",
                                  "DEFINE ... EVALUATE ...",
                                  "SELECT ... FROM $SYSTEM...."],
            },
        )
    return result
