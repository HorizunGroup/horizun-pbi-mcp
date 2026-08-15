"""Inventario de cobertura de payloads — CONTRACT-002 / G2.2.

    python -m tests.cobertura_payloads            # regenera docs/COBERTURA_PAYLOADS.md
    python -m tests.cobertura_payloads --check    # falla si el documento esta desfasado

El golden congelaba **dos tools publicas de 134** y con eso se dijo que «el
resto necesita Power BI Desktop». Era una hipotesis, no una medicion. Este
documento la sustituye por el recorrido de las 134 por `call_tool`, y a cada
exclusion le pone la dependencia que de verdad la bloquea:

* si contesta `no_active_model` o similar, lo que falta **es** Desktop;
* si el esquema rechaza `{}`, lo que falta son **argumentos**, que es trabajo;
* si es destructiva, no se ejecuta a ciegas y eso tambien se dice.

«Pendiente sin justificacion» no existe como categoria comoda: toda tool sin
payload congelado sale aqui con su motivo, y el recuento se publica.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = REPO_ROOT / "docs" / "COBERTURA_PAYLOADS.md"

ESTADOS = {
    "exito-congelado": "éxito congelado",
    "error-congelado": "error de dominio congelado",
    "pendiente": "**pendiente**",
}

CABECERA = """# Cobertura de payloads — CONTRACT-002

**Este documento no se escribe: se calcula.** Lo genera
`python -m tests.cobertura_payloads` recorriendo las 134 tools por
`call_tool`, y `tests/test_contrato_de_payload.py` falla si deja de coincidir.

## Por qué existe

El golden congelaba `pbi_health_check`, `pbi_capabilities` y `guide.situacion`
—o sea **dos tools públicas de 134**— y de ahí se concluyó que «el resto
necesita Power BI Desktop». Nadie lo había comprobado tool por tool. Medido, el
reparto es otro: lo que bloquea a la mayoría no es Desktop, son **argumentos que
hay que construir**, y eso es trabajo, no un impedimento.

## Qué significa cada estado

| Estado | Qué hay congelado | Qué falta |
|---|---|---|
| éxito congelado | la forma de una respuesta buena | nada |
| error de dominio congelado | la forma del error que contesta | el éxito, y la columna «bloqueo» dice por qué |
| **pendiente** | nada | lo que diga la columna «bloqueo» |

Los dos escenarios son deterministas y ninguno toca Power BI: `sin-proyecto`
—nada abierto— y `con-proyecto` —un `.pbip` sintético en un temporal—. El
descubrimiento de Desktop se sustituye para que el golden no dependa de si quien
lo genera tiene un informe abierto.

"""


def _tabla(resumen: Dict[str, Any]) -> str:
    lineas = ["| Tool | Estado | Escenarios | Bloqueo medido |",
              "|---|---|---|---|"]
    for nombre, dato in sorted(resumen.items()):
        escenarios = ", ".join(dato["escenarios"]) or "—"
        lineas.append(f"| `{nombre}` | {ESTADOS[dato['estado']]} | {escenarios} "
                      f"| {dato['bloqueo'] or '—'} |")
    return "\n".join(lineas)


def documento(resumen: Dict[str, Any]) -> str:
    por_estado: Dict[str, int] = {}
    for dato in resumen.values():
        por_estado[dato["estado"]] = por_estado.get(dato["estado"], 0) + 1
    bloqueos: Dict[str, int] = {}
    for dato in resumen.values():
        if dato["bloqueo"]:
            bloqueos[dato["bloqueo"].split(":")[0]] = (
                bloqueos.get(dato["bloqueo"].split(":")[0], 0) + 1)

    congeladas = (por_estado.get("exito-congelado", 0)
                  + por_estado.get("error-congelado", 0))
    partes = [CABECERA, "## Cuentas\n", "| | |", "|---|---|",
              f"| Tools | **{len(resumen)}** |",
              f"| Con payload congelado | **{congeladas}** |",
              f"| — de éxito | **{por_estado.get('exito-congelado', 0)}** |",
              f"| — solo de error de dominio | **{por_estado.get('error-congelado', 0)}** |",
              f"| Sin payload congelado | **{por_estado.get('pendiente', 0)}** |",
              ""]
    partes += ["### De qué depende cada exclusión\n", "| Dependencia | Tools |",
               "|---|---|"]
    for clave, cuantas in sorted(bloqueos.items(), key=lambda x: -x[1]):
        partes.append(f"| {clave} | **{cuantas}** |")
    partes += ["",
               "**`requiere-argumentos` no es un bloqueo externo**: es trabajo "
               "de escribir una llamada válida por tool, y mientras esté ahí, "
               "G2.2 no está cumplido.", "",
               "## Las tools, una por una\n", _tabla(resumen), ""]
    return "\n".join(partes)


def main(argv: List[str] | None = None) -> int:
    from tests.payload_muestras import recorrer

    argv = list(sys.argv[1:] if argv is None else argv)
    texto = documento(recorrer()[1])

    if "--check" in argv:
        if not DOC.is_file():
            print(f"No existe {DOC}. Generalo con: "
                  "python -m tests.cobertura_payloads", file=sys.stderr)
            return 1
        if DOC.read_text(encoding="utf-8") == texto:
            print("La cobertura publicada esta al dia.")
            return 0
        print("La cobertura publicada no coincide con el recorrido real. "
              "Regenerala con: python -m tests.cobertura_payloads",
              file=sys.stderr)
        return 1

    DOC.write_text(texto, encoding="utf-8", newline="")
    print(f"Cobertura regenerada: {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
