"""Deteccion y contencion de secretos antes de publicar un proyecto.

El problema
-----------
Convertir un `.pbix` a `.pbip` **desempaqueta** lo que estaba comprimido y lo
deja como texto legible en una carpeta: el JSON del informe, el TMDL del
modelo y, dentro de las particiones, las consultas M tal como las escribio
quien creo el informe. Ahi es donde aparecen los tokens pegados a mano:

    Web.Contents(url, [Headers=[Authorization="Bearer eyJhbG..."]])

Mientras vivia dentro del `.pbix` eso no se veia. Publicado en una carpeta que
casi siempre acaba en Git, se ve, se versiona y se comparte.

Las reglas de la casa que este modulo respeta a rajatabla
---------------------------------------------------------
1. **El valor NUNCA sale de aqui.** No se devuelve, no se registra, no se
   escribe en el journal y no viaja dentro de una excepcion. Lo que sale es:
   que regla salto, en que archivo relativo, en que linea aproximada, como se
   clasifico y una huella corta IRREVERSIBLE para poder hablar de "el mismo
   hallazgo" sin nombrarlo.
2. **Conservador antes que exhaustivo.** Un falso positivo que bloquea una
   conversion legitima cuesta mas que un secreto de baja confianza reportado
   como aviso. Por eso solo bloquea la ALTA confianza, y la alta confianza
   exige estructura verificable (un JWT que decodifica, un valor largo y con
   mezcla de tipos de caracter), no la mera presencia de la palabra "token".
3. **El base64 no es un secreto por ser base64.** Se decodifica una sola vez,
   con tope de tamano, y solo cuenta si lo que hay dentro dispara alguna de
   las otras reglas. Sin recursion sin limite: es exactamente como se
   construye una bomba de descompresion.
4. **PII y secretos son cosas distintas.** Un correo dentro de un bloque de
   credenciales es un dato personal, no una llave: se reporta aparte, con su
   propia clasificacion, y nunca bloquea.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("secret_scan")

#: Estados posibles del bloque `security_scan`.
CLEAN, WARNING, BLOCKED = "clean", "warning", "blocked"

#: Clasificaciones. Un secreto se contiene; un dato personal se avisa.
SECRET, PII = "secret", "pii"

#: Confianzas. Solo `high` impide publicar.
HIGH, LOW = "high", "low"

#: Sal fija del proyecto. No hace la huella reversible ni comparable con las
#: de otro sistema; solo impide que una tabla arcoiris generica la resuelva.
_SAL = b"horizun-pbi-mcp/secret-scan/v1|"

#: Tope por archivo. Un TMDL enorme se analiza hasta aqui y se DECLARA
#: truncado: callarlo seria afirmar que el resto esta limpio sin haberlo visto.
MAX_BYTES_POR_ARCHIVO = 2 * 1024 * 1024
#: Tope de longitud de un candidato base64 antes de intentar decodificarlo.
MAX_BASE64 = 1024
#: Candidatos base64 examinados por archivo.
MAX_BASE64_CANDIDATOS = 200
#: Profundidad de decodificacion. UNA pasada: lo que hay dentro de lo que hay
#: dentro de un base64 ya no se persigue, se declara.
PROFUNDIDAD_BASE64 = 1

#: Extensiones de texto de un proyecto Power BI. Lo demas no se abre.
EXTENSIONES_TEXTO = frozenset({
    ".json", ".tmdl", ".pbir", ".pbism", ".pbip", ".bim", ".txt", ".md",
    ".xml", ".config", ".ini", ".yaml", ".yml", ".m", ".csv", ".platform",
})
#: Archivos sin extension que si son texto en un .pbip.
NOMBRES_TEXTO = frozenset({".platform", "definition.pbir", "definition.pbism"})

# --------------------------------------------------------------- patrones ---
#: JWT: tres segmentos base64url separados por puntos, empezando por `eyJ`
#: (que es `{"` codificado). El prefijo es lo que evita tratar cualquier
#: cadena con dos puntos como un token.
_RE_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}\b")

#: Claves que anuncian un secreto. Se comparan sin distinguir mayusculas y
#: admitiendo separadores: `api_key`, `apiKey`, `api-key`, `Api Key`.
_CLAVES = (
    "password", "passwd", "pwd", "secret", "clientsecret", "client_secret",
    "apikey", "api_key", "accesstoken", "access_token", "refreshtoken",
    "refresh_token", "token", "sastoken", "sas_token", "authorization",
    "privatekey", "private_key", "connectionpassword",
)
#: Entre la clave y el separador puede haber comillas y barras: el texto que se
#: analiza es a veces JSON dentro de JSON (`{\"token\": \"...\"}`), que es
#: justo como Power BI guarda la configuracion de cada visual.
_RE_ASIGNACION = re.compile(
    r"(?P<clave>" + "|".join(
        k.replace("_", r"[_\-\s]?") for k in sorted(_CLAVES, key=len, reverse=True)
    ) + r")"
    r"[\"'\\\s]*(?:=|:)\s*"
    r"(?P<valor>[^\r\n,;)\]}]{1,512})",
    re.IGNORECASE)

#: Bordes que hay que quitar del valor capturado: comillas, barras de escape y
#: espacios. Sin esto, `\"abc\"` se mide como si empezara por una barra.
_BORDES = "\\\"' \t"

#: Lo que parece un secreto pero es un hueco por rellenar. No se reporta.
_RE_PLACEHOLDER = re.compile(
    r"^(?:null|none|nil|true|false|empty|nothing|n/?a|"
    r"\*+|x+|\.+|-+|_+|0+|"
    r"changeme|placeholder|redacted|dummy|sample|example|test|"
    r"your[\w\-]*|my[\w\-]*|<[^>]*>|\{\{.*\}\}|\{[\w\.\-]*\}|"
    r"\$\{.*\}|%[\w\.\-]*%|#[\(\"].*|@[\w\.\-]+|\[[\w\.\-]*\]|"
    r"\[redactado\]|env[\w\.\-]*)$",
    re.IGNORECASE)

#: Correo electronico. Deliberadamente simple: no valida el RFC, detecta.
_RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

#: Palabras que convierten una linea en "bloque de configuracion o
#: credenciales". Un correo suelto en una columna de datos NO es un hallazgo.
_RE_CONTEXTO_CREDENCIAL = re.compile(
    r"(?:user(?:name|id)?|account|login|credential|principal|identity|"
    r"password|secret|token|apikey|api[_\-\s]?key|auth|owner|contact|"
    r"mail(?:box|address)?|smtp|upn)", re.IGNORECASE)


def fingerprint(valor: str) -> str:
    """Huella corta e irreversible de un valor. NUNCA su contenido."""
    return hashlib.sha256(_SAL + str(valor).encode("utf-8",
                                                   "surrogatepass")).hexdigest()[:12]


def _hallazgo(rule: str, archivo: str, linea: int, valor: str, *,
              clasificacion: str, confianza: str,
              detalle: Optional[str] = None) -> Dict[str, Any]:
    """Construye el hallazgo SIN el valor. `valor` solo alimenta la huella."""
    salida = {
        "rule": rule,
        "file": archivo,
        "line": linea,
        "classification": clasificacion,
        "confidence": confianza,
        "fingerprint": fingerprint(valor),
        "value_length": len(valor),
    }
    if detalle:
        salida["detail"] = detalle
    return salida


def _clases_de_caracter(valor: str) -> int:
    return sum([
        bool(re.search(r"[a-z]", valor)),
        bool(re.search(r"[A-Z]", valor)),
        bool(re.search(r"[0-9]", valor)),
        bool(re.search(r"[^A-Za-z0-9]", valor)),
    ])


def _es_placeholder(valor: str) -> bool:
    return not valor or bool(_RE_PLACEHOLDER.match(valor.strip()))


def _confianza_de_valor(valor: str) -> Optional[str]:
    """Alta, baja o ninguna, segun lo que el valor parezca de verdad."""
    limpio = valor.strip()
    if _es_placeholder(limpio):
        return None
    if len(limpio) >= 16 and _clases_de_caracter(limpio) >= 2:
        return HIGH
    if len(limpio) >= 8:
        return LOW
    return None


def _jwt_confianza(token: str) -> str:
    """ALTA solo si la cabecera decodifica a un JSON con `alg` o `typ`.

    Sin esa comprobacion, cualquier cadena con dos puntos y el prefijo `eyJ`
    -que puede ser un JSON base64 perfectamente inocente- bloquearia la
    publicacion.
    """
    cabecera = token.split(".", 1)[0]
    relleno = "=" * (-len(cabecera) % 4)
    try:
        crudo = base64.urlsafe_b64decode(cabecera + relleno).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return LOW
    return HIGH if ('"alg"' in crudo or '"typ"' in crudo) else LOW


# ---------------------------------------------------------------- escaneo ---
def scan_text(texto: str, *, file: str = "", profundidad: int = 0
              ) -> List[Dict[str, Any]]:
    """Hallazgos de un texto. El texto NUNCA se devuelve ni se registra."""
    hallazgos: List[Dict[str, Any]] = []
    if not texto:
        return hallazgos

    for numero, linea in enumerate(texto.splitlines(), start=1):
        for m in _RE_JWT.finditer(linea):
            token = m.group(0)
            hallazgos.append(_hallazgo(
                "jwt", file, numero, token, clasificacion=SECRET,
                confianza=_jwt_confianza(token),
                detalle="cadena con estructura de JWT (tres segmentos)"))

        for m in _RE_ASIGNACION.finditer(linea):
            valor = (m.group("valor") or "").strip(_BORDES)
            if _RE_JWT.search(valor):
                continue                    # ya reportado como jwt
            confianza = _confianza_de_valor(valor)
            if confianza is None:
                continue
            hallazgos.append(_hallazgo(
                "secret_assignment", file, numero, valor,
                clasificacion=SECRET, confianza=confianza,
                detalle=f"asignacion a '{m.group('clave').strip()}'"))

        if _RE_CONTEXTO_CREDENCIAL.search(linea):
            for m in _RE_EMAIL.finditer(linea):
                hallazgos.append(_hallazgo(
                    "personal_email", file, numero, m.group(0),
                    clasificacion=PII, confianza=LOW,
                    detalle="correo dentro de un bloque de configuracion o "
                            "credenciales"))

        if profundidad < PROFUNDIDAD_BASE64:
            hallazgos.extend(_escanear_base64(linea, numero, file, profundidad))

    return hallazgos


def _escanear_base64(linea: str, numero: int, archivo: str,
                     profundidad: int) -> List[Dict[str, Any]]:
    """Base64 que, decodificado UNA vez, contiene senales de secreto."""
    hallazgos: List[Dict[str, Any]] = []
    examinados = 0
    for m in re.finditer(r"(?<![A-Za-z0-9+/=])([A-Za-z0-9+/]{24,}={0,2})"
                         r"(?![A-Za-z0-9+/=])", linea):
        if examinados >= MAX_BASE64_CANDIDATOS:
            break
        examinados += 1
        candidato = m.group(1)
        if len(candidato) > MAX_BASE64 or len(candidato) % 4:
            continue
        try:
            crudo = base64.b64decode(candidato, validate=True)
        except (binascii.Error, ValueError):
            continue
        if len(crudo) > MAX_BASE64:
            continue
        try:
            interior = crudo.decode("utf-8")
        except UnicodeDecodeError:
            continue                        # binario: no es "base64 legible"
        dentro = scan_text(interior, file=archivo,
                           profundidad=profundidad + 1)
        senales = [h for h in dentro if h["classification"] == SECRET]
        if not senales:
            continue
        # La huella es la del texto CODIFICADO tal como aparece en el archivo:
        # lo decodificado no se conserva ni se vuelve a mirar.
        hallazgos.append(_hallazgo(
            "base64_encoded_secret", archivo, numero, candidato,
            clasificacion=SECRET,
            confianza=HIGH if any(h["confidence"] == HIGH for h in senales)
                      else LOW,
            detalle=("base64 que decodifica a texto con senales de "
                     f"credencial ({', '.join(sorted({h['rule'] for h in senales}))})")))
    return hallazgos


def _es_texto(ruta: Path) -> bool:
    return (ruta.suffix.casefold() in EXTENSIONES_TEXTO
            or ruta.name.casefold() in NOMBRES_TEXTO)


def scan_tree(root: Path | str, *,
              extra: Optional[Iterable[Path]] = None) -> Dict[str, Any]:
    """Escanea el arbol de texto de un proyecto ya construido.

    Devuelve el bloque `security_scan` completo. `checked` es False solo si la
    raiz no existe: "no habia nada" y "no se pudo mirar" no son lo mismo.
    """
    raiz = Path(root)
    if not raiz.is_dir():
        return {"checked": False, "status": CLEAN, "finding_count": 0,
                "findings": [], "files_scanned": 0,
                "reason": "no existe la carpeta a escanear"}

    hallazgos: List[Dict[str, Any]] = []
    revisados = 0
    omitidos: List[Dict[str, Any]] = []
    truncados: List[str] = []

    candidatos = sorted(p for p in raiz.rglob("*") if p.is_file())
    for archivo in candidatos:
        relativa = archivo.relative_to(raiz).as_posix()
        if not _es_texto(archivo):
            continue
        try:
            crudo = archivo.read_bytes()
        except OSError as exc:
            omitidos.append({"file": relativa,
                             "reason": f"{type(exc).__name__}"})
            continue
        if len(crudo) > MAX_BYTES_POR_ARCHIVO:
            crudo = crudo[:MAX_BYTES_POR_ARCHIVO]
            truncados.append(relativa)
        try:
            texto = crudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                texto = crudo.decode("utf-16")
            except (UnicodeDecodeError, UnicodeError):
                omitidos.append({"file": relativa,
                                 "reason": "no se pudo decodificar como texto"})
                continue
        revisados += 1
        hallazgos.extend(scan_text(texto, file=relativa))

    for ruta in extra or ():
        try:
            texto = Path(ruta).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        revisados += 1
        hallazgos.extend(scan_text(texto, file=str(ruta)))

    return build_result(hallazgos, files_scanned=revisados,
                        skipped=omitidos, truncated=truncados)


def build_result(hallazgos: List[Dict[str, Any]], *, files_scanned: int = 0,
                 skipped: Optional[List[Dict[str, Any]]] = None,
                 truncated: Optional[List[str]] = None) -> Dict[str, Any]:
    """Arma el bloque `security_scan` a partir de una lista de hallazgos."""
    hallazgos = _deduplicar(hallazgos)
    altos = [h for h in hallazgos if h["classification"] == SECRET
             and h["confidence"] == HIGH]
    estado = BLOCKED if altos else (WARNING if hallazgos else CLEAN)
    resultado: Dict[str, Any] = {
        "checked": True,
        "status": estado,
        "finding_count": len(hallazgos),
        "findings": hallazgos,
        "files_scanned": files_scanned,
        "high_confidence_count": len(altos),
        "pii_count": sum(1 for h in hallazgos if h["classification"] == PII),
        "note": ("Solo se devuelve la regla, el archivo, la linea aproximada y "
                 "una huella irreversible: el valor detectado no sale de este "
                 "proceso."),
    }
    if skipped:
        resultado["skipped_files"] = skipped
    if truncated:
        resultado["truncated_files"] = truncated
        resultado["warnings"] = [
            f"{len(truncated)} archivo(s) se analizaron solo hasta "
            f"{MAX_BYTES_POR_ARCHIVO} bytes; el resto no se comprobo."]
    return resultado


def _deduplicar(hallazgos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    vistos = set()
    salida = []
    for h in hallazgos:
        clave = (h["rule"], h["file"], h["line"], h["fingerprint"])
        if clave in vistos:
            continue
        vistos.add(clave)
        salida.append(h)
    return sorted(salida, key=lambda h: (h["file"], h["line"], h["rule"]))


def resumen(resultado: Dict[str, Any]) -> str:
    """Frase para el usuario. Sin valores, sin nombres de campo del origen."""
    if resultado["status"] == CLEAN:
        return "No se detectaron secretos ni datos personales."
    reglas = sorted({h["rule"] for h in resultado["findings"]})
    return (f"{resultado['finding_count']} hallazgo(s) de seguridad "
            f"({', '.join(reglas)}) en {resultado['files_scanned']} archivo(s) "
            "analizados.")
