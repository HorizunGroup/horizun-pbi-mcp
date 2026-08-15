"""INSTALL-005 — el comando que completa una instalacion por `pip`.

    horizun-pbi-completar              # completa lo que falte
    horizun-pbi-completar --check      # solo diagnostica, no descarga nada

El wheel **no puede** traerlo todo, y no por descuido: las DLL de Analysis
Services son binarios de Microsoft y los esquemas PBIR no declaran permiso de
redistribucion. Asi que `pip install horizun-pbi-mcp` deja un servidor que
arranca, habla MCP y contesta las 134 tools, y que **no puede trabajar**: la
capa en vivo no tiene con que hablarle al modelo y toda escritura PBIR falla con
`schema_unavailable`.

`pbi_health_check` ya distinguia «instalado» de «operativo» —el bloque
`completeness`—, pero el comando que ese diagnostico recomendaba era
`python scripts/fetch_libs.py`, y **`scripts/` no viaja en el wheel**. Se le
estaba diciendo a la gente que ejecutara un archivo que no tenia. Por eso los
tres descargadores viven ahora dentro del paquete y esto es un `console_script`:
el diagnostico y su remedio tienen que existir en la misma instalacion.

Los `scripts/fetch_*.py` del repositorio siguen ahi como envoltorios de una
linea, porque el instalador del plugin los invoca por ruta y el README los
documenta. La logica esta en un solo sitio.

Lo que hace, en orden, y por que ese orden:

1. **DLL de Analysis Services** — obligatorio. Sin ellas no hay capa en vivo.
2. **Esquemas PBIR** — obligatorio. Sin ellos no hay escritura.
3. **Validador oficial** — OPCIONAL a proposito. INSTALL-002 lo declara
   prescindible: si falla, se dice y se sigue. Presentarlo como obligatorio
   volveria a convertir su ausencia en una instalacion rota, que es el defecto
   que INSTALL-002 vino a quitar.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Dict, List

#: `(clave, titulo, obligatorio, quien_lo_instala)`. El orden es el de arriba.
COMPONENTES = (
    ("analysis_services_dlls", "DLL de Analysis Services (capa en vivo)", True),
    ("pbir_schemas", "esquemas PBIR (escritura de informes)", True),
    ("report_validator", "validador oficial de Microsoft (opcional)", False),
)


def _instalador(clave: str) -> Callable[[List[str]], int]:
    """Se importa tarde, y a proposito.

    Cada descargador arrastra su propio manifiesto y sus utilidades; importarlos
    los tres al arrancar el paquete cargaria en cada sesion del servidor codigo
    que solo se usa el dia que alguien completa la instalacion.
    """
    if clave == "analysis_services_dlls":
        from horizun_pbi_mcp.completado import libs

        return libs.main
    if clave == "pbir_schemas":
        from horizun_pbi_mcp.completado import esquemas

        return esquemas.main
    from horizun_pbi_mcp.completado import validador

    return validador.main


def diagnostico() -> Dict[str, Any]:
    """Lo MISMO que contesta `pbi_health_check`, no una segunda opinion.

    Dos diagnosticos distintos sobre la misma instalacion es como se acaba con
    un comando que dice que todo esta bien y una tool que dice que no.
    """
    from horizun_pbi_mcp.config import get_settings
    from horizun_pbi_mcp.tools.ops_tools import _completitud

    return _completitud(get_settings())


def _imprimir(estado: Dict[str, Any]) -> None:
    if estado["state"] == "operational":
        print("La instalacion esta completa: nada que descargar.")
        return
    print("Falta esto para que la instalacion sea OPERATIVA:\n")
    for pieza in estado["missing"]:
        marca = "OBLIGATORIO" if pieza["required"] else "opcional"
        print(f"  [{marca}] {pieza['component']}")
        print(f"      {pieza['impact']}")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="horizun-pbi-completar",
        description="Completa una instalacion por pip: descarga lo que el "
                    "wheel no puede traer.")
    p.add_argument("--check", action="store_true",
                   help="solo diagnostica; no descarga nada")
    args = p.parse_args(argv)

    estado = diagnostico()
    _imprimir(estado)
    if args.check:
        return 0 if estado["state"] == "operational" else 1
    if estado["state"] == "operational":
        return 0

    faltan = {pieza["component"] for pieza in estado["missing"]}
    fallos: List[str] = []
    for clave, titulo, obligatorio in COMPONENTES:
        if clave not in faltan:
            continue
        print(f"\n== {titulo} ==")
        try:
            codigo = _instalador(clave)([])
        except Exception as exc:                             # noqa: BLE001
            codigo, detalle = 1, f"{type(exc).__name__}: {exc}"
            print(f"FALLO: {detalle}", file=sys.stderr)
        if codigo == 0:
            continue
        if obligatorio:
            fallos.append(clave)
        else:
            # INSTALL-002: lo opcional que falla no tumba la instalacion. Se
            # dice, con su nombre, y se sigue.
            print(f"AVISO: {clave} no se pudo instalar y es opcional; el resto "
                  "del producto funciona.", file=sys.stderr)

    final = diagnostico()
    print()
    _imprimir(final)
    if fallos:
        print(f"\nNo se pudo completar: {', '.join(fallos)}", file=sys.stderr)
        return 1
    return 0 if final["state"] == "operational" else 1


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
