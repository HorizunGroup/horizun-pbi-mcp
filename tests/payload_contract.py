"""CONTRACT-002 — congelar el INTERIOR del payload, no solo su envoltorio.

`tests/golden/tools_v1.json` congela el `output_shape` **declarado** de cada
tool, y para las que devuelven el envelope genérico —`{"result": ...}`— ese
declarado no dice nada del contenido. Consecuencia: **retirar o renombrar una
clave del payload rompe a un cliente y hoy pasa en verde.**

Añadir claves es un cambio permitido, así que la ceguera no duele en esa
dirección. El problema es la contraria, y es justo el modo de fallo contra el
que esta red existe: la suite da la misma sensación de seguridad en los dos
casos.

Lo que se congela es la **forma**, no los valores: el conjunto de claves de cada
objeto, recursivamente, y el tipo de cada hoja. Los valores cambian con el
proyecto de prueba, la máquina y la hora; las claves son el contrato.

    python -m tests.payload_contract --write     # tras un cambio DELIBERADO

Alcance, dicho claro: cubre las respuestas obtenibles **sin Power BI Desktop**.
Las que exigen un modelo vivo son TEST-003 y su bloque de gates. Un golden
parcial que se sepa parcial vale mucho más que ninguno; lo que no vale es
creerlo completo.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_PATH = REPO_ROOT / "tests" / "golden" / "payloads_v1.json"


def forma(valor: Any, _profundidad: int = 0) -> Any:
    """La FORMA de un payload: claves y tipos, nunca valores.

    Las listas se reducen a la forma de su primer elemento y no a su longitud:
    que una lista traiga tres o cuatro elementos depende del proyecto de prueba,
    y congelarlo convertiría cada fixture nuevo en una ruptura de contrato
    fingida.
    """
    if _profundidad > 6:
        return "<...>"
    if isinstance(valor, dict):
        return {k: forma(v, _profundidad + 1) for k, v in sorted(valor.items())}
    if isinstance(valor, list):
        return [forma(valor[0], _profundidad + 1)] if valor else []
    if valor is None:
        return "null"
    return type(valor).__name__


def _claves(nodo: Any, prefijo: str = "") -> set:
    """Toda ruta de clave del payload, para poder decir cuáles faltan."""
    salida = set()
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            ruta = f"{prefijo}.{k}" if prefijo else k
            salida.add(ruta)
            salida |= _claves(v, ruta)
    elif isinstance(nodo, list) and nodo:
        salida |= _claves(nodo[0], f"{prefijo}[]")
    return salida


def diferencias(antes: Dict[str, Any], ahora: Dict[str, Any]) -> Dict[str, List[str]]:
    """Qué se retiró, qué se añadió y qué cambió de tipo, por tool.

    Se distinguen a propósito: **retirar o renombrar rompe** a un cliente que ya
    leía esa clave; **añadir** es un cambio permitido y sale aparte para que se
    vea sin bloquear nada.
    """
    rupturas: List[str] = []
    compatibles: List[str] = []

    for tool in sorted(set(antes) | set(ahora)):
        if tool not in ahora:
            rupturas.append(f"{tool}: la tool ya no produce payload")
            continue
        if tool not in antes:
            compatibles.append(f"{tool}: payload nuevo congelado")
            continue
        viejas, nuevas = _claves(antes[tool]), _claves(ahora[tool])
        for k in sorted(viejas - nuevas):
            rupturas.append(f"{tool}: desaparecio la clave `{k}`")
        for k in sorted(nuevas - viejas):
            compatibles.append(f"{tool}: clave nueva `{k}`")
        for k in sorted(viejas & nuevas):
            a, b = _tipo_en(antes[tool], k), _tipo_en(ahora[tool], k)
            if a != b and isinstance(a, str) and isinstance(b, str):
                rupturas.append(f"{tool}: `{k}` paso de {a} a {b}")
    return {"breaking": rupturas, "compatible": compatibles}


def _tipo_en(nodo: Any, ruta: str) -> Any:
    for parte in ruta.split("."):
        if parte.endswith("[]"):
            parte = parte[:-2]
            if parte:
                nodo = nodo.get(parte) if isinstance(nodo, dict) else None
            nodo = nodo[0] if isinstance(nodo, list) and nodo else None
        elif isinstance(nodo, dict):
            nodo = nodo.get(parte)
        else:
            return None
    return nodo


def cargar() -> Dict[str, Any]:
    if not GOLDEN_PATH.exists():
        return {}
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["payloads"]


def escribir(payloads: Dict[str, Any]) -> Path:
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_text(json.dumps(
        {"payload_contract_version": 2,
         "muestras": len(payloads),
         "tools_cubiertas": len({k.split(".", 1)[0] for k in payloads
                                 if k.startswith("pbi_")}),
         "note": ("Forma de los payloads: claves y tipos, nunca valores ni "
                  "longitudes. Una clave por `<tool>.<escenario>`. La cobertura "
                  "tool por tool, con la dependencia medida de cada exclusion, "
                  "esta en docs/COBERTURA_PAYLOADS.md."),
         "payloads": dict(sorted(payloads.items()))},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return GOLDEN_PATH


def main(argv: List[str] | None = None) -> int:
    from tests.payload_muestras import capturar

    argv = list(sys.argv[1:] if argv is None else argv)
    actual = capturar()
    if "--write" in argv:
        ruta = escribir(actual)
        print(f"Golden de payloads regenerado: {ruta}")
        print(f"  {len(actual)} payload(s) congelados")
        return 0

    d = diferencias(cargar(), actual)
    for linea in d["compatible"]:
        print(f"  [+] {linea}")
    for linea in d["breaking"]:
        print(f"  [!] {linea}")
    if d["breaking"]:
        print("\nSi el cambio es DELIBERADO y esta ratificado, regenera con:")
        print("  python -m tests.payload_contract --write")
        return 1
    print("El interior de los payloads no cambio.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
