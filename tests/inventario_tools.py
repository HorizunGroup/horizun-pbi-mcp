"""Inventario ejecutable de las tools — TEST-002 (G2.3 y G2.4).

    python -m tests.inventario_tools            # regenera docs/INVENTARIO_TOOLS.md
    python -m tests.inventario_tools --check    # falla si el documento esta desfasado

G2.3 pide «inventario tool por tool publicado» y G2.4, «toda tool tiene al menos
un caso negativo; las excepciones se declaran con motivo». Lo tentador es una
tabla escrita a mano. Duraria hasta la siguiente tool: un inventario a mano
envejece en la primera edicion que alguien haga y **nadie se entera**, porque el
documento sigue teniendo el mismo aspecto de completo.

Asi que aqui no se escribe el inventario: **se calcula**, y el caso negativo de
cada tool se calcula tambien, a partir de su propio esquema. El documento es un
volcado; si alguien anade una tool y no la cubre, el recuento cambia y la prueba
lo dice.

## De donde sale el caso negativo

| Situacion de la tool | Caso negativo | Por que es seguro |
|---|---|---|
| Tiene parametros requeridos | llamarla con `{}` | pydantic la rechaza **antes** del cuerpo |
| Tiene parametros, ninguno requerido | un valor de otro tipo en el primero | igual: rechazo en la validacion |
| No declara parametros | ejecutarla sin proyecto activo | salvo que sea destructiva |
| Solo depende de un adaptador del entorno | romperle el adaptador | se sustituye; no se toca Desktop |

La tercera fila es la unica que **ejecuta el cuerpo**, y por eso es la unica que
mira la clasificacion de riesgo de `tools/risk.py`. Una tool sin parametros no
admite entrada invalida: lo unico que puede salirle mal es el estado, y esa es
justo la respuesta que un cliente se encuentra el primer dia —pedir algo sin
haber abierto nada—.

Que ejecutarlas sea seguro no se supone: la prueba corre con `isolated_settings`
—salidas, respaldos y librerias dentro de `tmp_path`— y con `proyecto_cerrado`,
asi que no hay proyecto sobre el que actuar y lo unico que una tool podria
escribir cae en el directorio temporal de la prueba. Lo destructivo no se
ejecuta ni asi.

## Las dos que estuvieron declaradas, y por que ya no lo estan

`pbi_list_desktop_models` y `pbi_test_connection` se declararon como «no se
ejecutan» porque sondean el entorno real y su resultado dependia de si quien
corre la suite tiene Power BI Desktop abierto. **Eso era un problema de
determinismo, no de imposibilidad**, y declararlo como excepcion permanente
convertia una carencia de la prueba en una propiedad del producto. El inventario
decia 134 y ejecutaba 132.

* `pbi_test_connection` **no necesitaba nada**: sin modelo activo contesta
  `no_active_model` al instante, sin abrir ninguna conexion. Nunca llegaba a la
  red; se estaba evitando por si acaso.
* `pbi_list_desktop_models` necesita que se le sustituyan dos adaptadores —la
  enumeracion de procesos y la de archivos de puerto—. Con eso deja de mirar la
  maquina, y se le puede exigir lo que de verdad importa de una tool de
  descubrimiento: **si su adaptador revienta, el cliente recibe un sobre con
  codigo, no una traza**.

Ninguna de las dos abre, cierra ni consulta Power BI.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "INVENTARIO_TOOLS.md"
PAYLOADS = REPO_ROOT / "tests" / "golden" / "payloads_v1.json"

#: Un dict no es coercible a `str`, `int`, `float`, `bool` ni `array` en ningun
#: modo de pydantic. Para los parametros que SI esperan un objeto se usa una
#: cadena, por el mismo motivo al reves.
VALOR_IMPOSIBLE: Dict[str, Any] = {"__tipo_invalido__": True}
VALOR_IMPOSIBLE_PARA_OBJETO = "no-soy-un-objeto"

#: Tools cuyo unico modo de fallo esta en un ADAPTADOR del entorno, no en su
#: entrada ni en el estado del proyecto. Se les inyecta el fallo en ese
#: adaptador y se exige un sobre estructurado.
#:
#: `pbi_list_desktop_models` estuvo declarada como «no se ejecuta» porque sondea
#: los puertos de Analysis Services y su resultado depende de si quien corre la
#: suite tiene Desktop abierto. Eso era un problema de DETERMINISMO, no de
#: imposibilidad: sustituyendo la enumeracion de procesos y de archivos de
#: puerto, la tool se ejecuta igual que las demas y **sin tocar Desktop**. Lo
#: que se comprueba es la propiedad que importa de una tool de descubrimiento:
#: si su adaptador revienta, el cliente recibe un sobre con codigo, no una
#: traza.
ADAPTADORES = {
    "pbi_list_desktop_models": {
        "modulo": "horizun_pbi_mcp.powerbi.desktop_discovery",
        "vacio": ("_ports_from_processes", "_workspace_port_files"),
        "codigo": "unexpected",
    },
}

#: No declaran parametros Y no dependen de ningun estado: no hay entrada que
#: rechazar ni situacion que las haga fallar. Contestan lo mismo el primer dia
#: que el ultimo.
#:
#: **La exencion se comprueba, no se concede.** La prueba las ejecuta igual y
#: exige `ok: true`; el dia que una empiece a depender del proyecto abierto,
#: dejara de contestar que si y esta lista se quedara sin justificacion, que es
#: exactamente cuando hay que revisarla. Una exencion que nadie vuelve a mirar
#: es un agujero con nombre bonito.
SIN_MODO_DE_FALLO = {
    "pbi_capabilities", "pbi_health_check", "pbi_list_audit_rules",
    "pbi_list_autofix_rules", "pbi_list_page_presets", "pbi_list_themes",
    "pbi_propose_dashboard", "pbi_session_info",
}


def _tipo_declarado(spec: Dict[str, Any]) -> str | None:
    """Tipo JSON del parametro, resolviendo el `anyOf` de los opcionales.

    Un parametro opcional se declara `{"anyOf": [{"type": "string"}, {"type":
    "null"}]}`. Quedarse en que «no tiene type» dejaria sin caso negativo a 11
    tools por una cuestion de forma del esquema, no de fondo.
    """
    tipo = spec.get("type")
    if isinstance(tipo, str):
        return tipo
    for rama in spec.get("anyOf") or []:
        if isinstance(rama, dict) and rama.get("type") not in (None, "null"):
            return rama["type"]
    return None


def _valor_invalido(spec: Dict[str, Any]) -> Any:
    return (VALOR_IMPOSIBLE_PARA_OBJETO if _tipo_declarado(spec) == "object"
            else VALOR_IMPOSIBLE)


def caso_negativo(nombre: str, esquema: Dict[str, Any],
                  destructiva: bool) -> Dict[str, Any]:
    """Como se hace fallar a esta tool, o por que no se le hace fallar."""
    props = esquema.get("properties") or {}
    requeridos = esquema.get("required") or []

    if nombre in ADAPTADORES:
        return {"clase": "adaptador_roto", "args": {}, "campo": None,
                "motivo": None, "adaptador": ADAPTADORES[nombre]}

    if requeridos:
        return {"clase": "falta_requerido", "args": {}, "motivo": None,
                "campo": ", ".join(sorted(requeridos))}

    for campo, spec in props.items():
        if _tipo_declarado(spec) is None:
            continue
        return {"clase": "tipo_invalido", "campo": campo, "motivo": None,
                "args": {campo: _valor_invalido(spec)}}

    if props:
        return {"clase": "declarada", "args": None, "campo": None,
                "motivo": "ningun parametro declara un tipo concreto, asi que "
                          "no hay valor que el esquema garantice rechazar"}

    if destructiva:
        return {"clase": "declarada", "args": None, "campo": None,
                "motivo": "no declara parametros —no admite entrada invalida— y "
                          "esta clasificada como destructiva: verla fallar "
                          "exigiria ejecutarla, y no se ejecuta a ciegas nada "
                          "que pueda destruir"}

    if nombre in SIN_MODO_DE_FALLO:
        return {"clase": "sin_modo_de_fallo", "args": {}, "campo": None,
                "motivo": "no admite entrada invalida ni depende de estado: no "
                          "hay forma de hacerla fallar. Se ejecuta igual y se "
                          "exige que conteste `ok: true`, para que la exencion "
                          "caduque sola si algun dia empieza a depender de algo"}

    return {"clase": "estado_ausente", "args": {}, "campo": None, "motivo": None}


def _payloads_congelados() -> set[str]:
    if not PAYLOADS.is_file():
        return set()
    datos = json.loads(PAYLOADS.read_text(encoding="utf-8"))
    # Las claves del golden son `<tool>` o `<tool>.<escenario>`: se congela la
    # forma de una respuesta concreta, y una tool puede tener varias.
    return {clave.split(".", 1)[0] for clave in datos.get("payloads") or {}}


def inventario(mcp) -> List[Dict[str, Any]]:
    """Una fila por tool, calculada del servidor y de la clasificacion."""
    from horizun_pbi_mcp.tools.risk import annotations_for

    congelados = _payloads_congelados()
    filas = []
    for tool in sorted(mcp._tool_manager.list_tools(), key=lambda t: t.name):
        esquema = tool.parameters or {}
        props = esquema.get("properties") or {}
        anotacion = annotations_for(tool.name)
        solo_lectura = bool(anotacion.get("readOnlyHint"))
        filas.append({
            "tool": tool.name,
            "riesgo": ("solo lectura" if solo_lectura
                       else "destructiva" if anotacion.get("destructiveHint")
                       else "escritura"),
            "confirm": "confirm" in props,
            "parametros": len(props),
            "requeridos": len(esquema.get("required") or []),
            "payload_congelado": tool.name in congelados,
            "negativo": caso_negativo(tool.name, esquema,
                                      bool(anotacion.get("destructiveHint"))),
        })
    return filas


# ------------------------------------------------------------------ documento
CLASES = {
    "falta_requerido": "falta un requerido",
    "tipo_invalido": "tipo invalido",
    "estado_ausente": "sin proyecto activo",
    "sin_modo_de_fallo": "sin modo de fallo",
    "adaptador_roto": "adaptador roto",
    "declarada": "**declarada**",
}

CABECERA = """# Inventario de tools — TEST-002

**Este documento no se escribe: se calcula.** Lo genera
`python -m tests.inventario_tools` a partir del servidor MCP real, y
`tests/test_inventario_tools.py` falla si el archivo deja de coincidir con lo
que el servidor declara hoy. Un inventario escrito a mano envejece en la primera
edicion que alguien haga de una tool, y nadie se entera porque el documento
sigue teniendo aspecto de completo.

Cierra G2.3 («inventario tool por tool publicado») y G2.4 («toda tool tiene al
menos un caso negativo; las excepciones se declaran con motivo»).

## Como leer la columna «caso negativo»

| Valor | Que se hace | Que se exige |
|---|---|---|
| falta un requerido | llamarla por MCP con `{}` | la validacion la rechaza antes de ejecutar nada |
| tipo invalido | un valor de otro tipo en el parametro que se indica | lo mismo: rechazo en la validacion |
| sin proyecto activo | ejecutarla de verdad, sin nada abierto | responde un sobre `ok: false` con codigo, nunca una excepcion |
| sin modo de fallo | ejecutarla de verdad, sin nada abierto | responde `ok: true`: no hay entrada ni estado que la haga fallar |
| adaptador roto | se le rompe el adaptador del entorno que consulta | responde un sobre con codigo, **nunca una traza** |
| **declarada** | no se ejecuta | el motivo va en la tabla de abajo, que es lo que G2.4 exige |

Las cinco primeras **se ejecutan por MCP** (`call_tool`) cada vez que corre la
suite: la columna no es una promesa, es lo que acaba de pasar.

«Sin modo de fallo» no es un aprobado gratis. Son tools que contestan lo mismo
el primer dia que el ultimo —capacidades, reglas, temas—, y **la exencion se
comprueba**: si alguna empieza a depender del proyecto abierto dejara de
contestar `ok: true` y la prueba lo dira. Una exencion que nadie vuelve a mirar
es un agujero con nombre bonito.

## Lo que este inventario NO demuestra

Conviene decirlo aqui, y no en una nota al pie, porque es lo que alguien podria
dar por hecho al ver 134 filas en verde:

* **No prueba que las tools hagan bien su trabajo.** Prueba que rechazan lo que
  no deben aceptar y que, cuando no pueden trabajar, lo dicen con un codigo. Lo
  otro son las pruebas de cada dominio, que van por su cuenta.
* **Los casos negativos son de entrada y de estado, no de semantica.** «Un DAX
  con la sintaxis rota» o «un tema con un color imposible» no salen de un
  esquema: hay que escribirlos a mano, tool por tool, y varios ya viven en los
  archivos de su dominio.
* **La columna de payload congelado la llena CONTRACT-002**, no esto. El
  reparto tool por tool, con la dependencia MEDIDA de cada exclusion, esta en
  `docs/COBERTURA_PAYLOADS.md`.

"""


def _tabla(filas: List[Dict[str, Any]]) -> str:
    lineas = ["| Tool | Riesgo | Params | Req. | `confirm` | Payload congelado |"
              " Caso negativo | Campo |",
              "|---|---|---|---|---|---|---|---|"]
    for f in filas:
        n = f["negativo"]
        lineas.append(
            f"| `{f['tool']}` | {f['riesgo']} | {f['parametros']} | "
            f"{f['requeridos']} | {'sí' if f['confirm'] else '—'} | "
            f"{'sí' if f['payload_congelado'] else '—'} | "
            f"{CLASES[n['clase']]} | "
            f"{'`' + n['campo'] + '`' if n['campo'] else '—'} |")
    return "\n".join(lineas)


def documento(filas: List[Dict[str, Any]]) -> str:
    por_clase: Dict[str, int] = {}
    for f in filas:
        por_clase[f["negativo"]["clase"]] = por_clase.get(f["negativo"]["clase"], 0) + 1
    ejecutadas = len(filas) - por_clase.get("declarada", 0)
    negativas = ejecutadas - por_clase.get("sin_modo_de_fallo", 0)
    declaradas = [f for f in filas if f["negativo"]["clase"] == "declarada"]

    partes = [CABECERA, "## Cuentas\n", "| | |", "|---|---|",
              f"| Tools | **{len(filas)}** |",
              f"| Ejecutadas por MCP en cada corrida | **{ejecutadas}** |",
              f"| Con caso negativo que las hace fallar | **{negativas}** |",
              f"| Sin modo de fallo (ejecutadas, se exige `ok: true`) | "
              f"**{por_clase.get('sin_modo_de_fallo', 0)}** |",
              f"| Excepciones declaradas con motivo | **{len(declaradas)}** |",
              f"| De solo lectura | **{sum(1 for f in filas if f['riesgo'] == 'solo lectura')}** |",
              f"| Con `confirm` | **{sum(1 for f in filas if f['confirm'])}** |",
              f"| Con payload congelado | **{sum(1 for f in filas if f['payload_congelado'])}** |",
              ""]

    if declaradas:
        partes += ["## Excepciones, con su motivo\n",
                   "G2.4 admite lagunas **declaradas**. Estas son, y ninguna se "
                   "declara por comodidad: o el esquema no permite construir una "
                   "entrada invalida, o ejecutarla tocaria el entorno real.\n",
                   "| Tool | Motivo |", "|---|---|"]
        partes += [f"| `{f['tool']}` | {f['negativo']['motivo']} |"
                   for f in declaradas]
        partes.append("")

    partes += ["## Las tools, una por una\n", _tabla(filas), ""]
    return "\n".join(partes)


def _construir():
    from horizun_pbi_mcp.server import build_server

    return build_server()


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    filas = inventario(_construir())
    texto = documento(filas)

    if "--check" in argv:
        if not DOC.is_file():
            print(f"No existe {DOC}. Generalo con: "
                  "python -m tests.inventario_tools", file=sys.stderr)
            return 1
        if DOC.read_text(encoding="utf-8") == texto:
            print(f"El inventario esta al dia ({len(filas)} tools).")
            return 0
        print("El inventario publicado no coincide con el servidor. "
              "Regeneralo con: python -m tests.inventario_tools", file=sys.stderr)
        return 1

    DOC.write_text(texto, encoding="utf-8", newline="")
    print(f"Inventario regenerado: {DOC}")
    print(f"  {len(filas)} tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
