"""Que archivo se abre, y por que ese y no otro.

El defecto que cierra
---------------------
`_find_pbip_file` hacia esto ante una carpeta con varios proyectos::

    matches = sorted(p.glob("*.pbip"))
    return matches[0]

Es decir: **elegia por orden alfabetico y no lo decia**. Con `Antiguo.pbip` y
`Nuevo.pbip` en la misma carpeta, quien pedia "prepara esta carpeta" acababa
editando `Antiguo.pbip`, y todo lo que viniera despues -medidas, paginas,
publicacion- caia en el proyecto equivocado con la respuesta en verde. El
mismo patron estaba en los respaldos de `*.Report` y `*.SemanticModel`.

La politica, en una linea
-------------------------
**Una ruta explicita siempre gana. Una carpeta solo se resuelve si tiene
exactamente un candidato. Con dos, se falla enumerandolos.**

No hay desempate por fecha, por tamano ni por nombre: cualquiera de esos
criterios es una suposicion sobre lo que alguien queria, y equivocarse aqui
cuesta el trabajo de otro. Preguntar es barato.

Por que vive en `services/`
---------------------------
Porque la usan tres capas distintas -el localizador de proyectos, la
publicacion y las tools- y tener tres copias de "elige un archivo" es tener
tres politicas que divergen. La primera que se relaje deja un agujero por el
que se cuela el proyecto equivocado.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError


class AmbiguousProjectError(PowerBIMCPError):
    """Hay mas de un candidato y ninguno esta indicado."""

    code = "ambiguous_pbip_project"


class ProjectNotFoundError(PowerBIMCPError):
    code = "project_not_found"


#: Por que se eligio lo que se eligio. Viaja en la respuesta para que la
#: decision sea auditable sin tener que reproducirla.
EXPLICIT_FILE = "explicit_file"
FOLDER_SINGLE_CANDIDATE = "folder_single_candidate"
ACTIVE_PROJECT = "active_project"
CONVERTED_FROM_PBIX = "converted_from_pbix"

#: Extensiones que Power BI Desktop abre como documento.
EXTENSIONES_PROYECTO = (".pbip", ".pbix", ".pbit")


def normalizar(ruta: Path | str) -> str:
    """Clave de comparacion de rutas. Dos basenames iguales NO bastan.

    `C:\\a\\Informe.pbip` y `C:\\b\\Informe.pbip` son proyectos distintos, y
    compararlos por `stem` -que es lo que hace el titulo de una ventana- los
    confunde. Aqui se compara la ruta absoluta normalizada, siempre.
    """
    texto = str(ruta)
    if texto.startswith("\\\\?\\"):
        texto = texto[4:]
    try:
        texto = str(Path(texto).expanduser().resolve())
    except OSError:                                       # pragma: no cover
        texto = str(Path(texto).expanduser())
    return os.path.normcase(os.path.normpath(texto))


def misma_ruta(a: Optional[Path | str], b: Optional[Path | str]) -> bool:
    if a is None or b is None:
        return False
    return normalizar(a) == normalizar(b)


def candidatos(carpeta: Path, patron: str, *,
               solo_directorios: bool = False) -> List[Path]:
    try:
        encontrados = carpeta.glob(patron)
    except OSError:                                       # pragma: no cover
        return []
    return sorted(p for p in encontrados
                  if (p.is_dir() if solo_directorios else p.is_file()))


def unico(carpeta: Path, patron: str, *, kind: str,
          solo_directorios: bool = False,
          obligatorio: bool = True) -> Optional[Path]:
    """El UNICO candidato de la carpeta. Con dos, error; nunca el primero.

    `obligatorio=False` devuelve `None` cuando no hay ninguno -util para un
    respaldo opcional-, pero la ambiguedad sigue siendo un error: "no hay" y
    "hay varios y elegi uno" no son la misma respuesta ni de lejos.
    """
    hallados = candidatos(carpeta, patron, solo_directorios=solo_directorios)
    if len(hallados) == 1:
        return hallados[0]
    if not hallados:
        if obligatorio:
            raise ProjectNotFoundError(
                f"No hay ningun {kind} en {carpeta}.",
                details={"folder": str(carpeta), "pattern": patron,
                         "candidates": []})
        return None
    raise AmbiguousProjectError(
        f"Hay {len(hallados)} {kind} en {carpeta} y ninguno esta indicado. "
        "Indica la ruta exacta del que quieres: elegir el primero por orden "
        "alfabetico seria operar sobre el proyecto equivocado sin decirlo.",
        details={"folder": str(carpeta), "pattern": patron,
                 "candidates": [c.name for c in hallados],
                 "resolved_candidates": [str(c) for c in hallados]})


def resolver_entrada(path: str | Path) -> Tuple[Path, str]:
    """Resuelve lo que pidio quien llama a UN archivo, con su motivo.

    Devuelve `(ruta, selection_reason)`. Un archivo se respeta tal cual -sea
    `.pbip`, `.pbix` o `.pbit`-; una carpeta solo se resuelve si tiene
    exactamente un candidato.
    """
    p = Path(str(path)).expanduser()
    if p.is_file():
        if p.suffix.casefold() not in EXTENSIONES_PROYECTO:
            raise ProjectNotFoundError(
                f"'{p.name}' no es un proyecto de Power BI. Se esperaba "
                f"{', '.join(EXTENSIONES_PROYECTO)}.",
                details={"path": str(p), "suffix": p.suffix})
        return p.resolve(), EXPLICIT_FILE

    if not p.exists():
        raise ProjectNotFoundError(
            f"La ruta no existe: {p}.", details={"path": str(p)})

    if not p.is_dir():                                    # pragma: no cover
        raise ProjectNotFoundError(
            f"La ruta no es un archivo ni una carpeta: {p}.",
            details={"path": str(p)})

    # Carpeta. Se busca .pbip primero: es el formato de trabajo. Solo si no
    # hay ninguno se mira si contiene un .pbix suelto que convertir.
    pbips = candidatos(p, "*.pbip")
    if len(pbips) == 1:
        return pbips[0].resolve(), FOLDER_SINGLE_CANDIDATE
    if len(pbips) > 1:
        raise AmbiguousProjectError(
            f"La carpeta {p} tiene {len(pbips)} proyectos .pbip y no se "
            "indico cual. Pasa la ruta exacta del .pbip: elegir uno por orden "
            "alfabetico seria trabajar sobre el proyecto equivocado.",
            details={"folder": str(p),
                     "candidates": [c.name for c in pbips],
                     "resolved_candidates": [str(c) for c in pbips]})

    pbix = candidatos(p, "*.pbix")
    if len(pbix) == 1:
        return pbix[0].resolve(), FOLDER_SINGLE_CANDIDATE
    if len(pbix) > 1:
        raise AmbiguousProjectError(
            f"La carpeta {p} tiene {len(pbix)} archivos .pbix y no se indico "
            "cual. Pasa la ruta exacta del que quieres preparar.",
            details={"folder": str(p),
                     "candidates": [c.name for c in pbix],
                     "resolved_candidates": [str(c) for c in pbix]})

    raise ProjectNotFoundError(
        f"En {p} no hay ningun .pbip ni .pbix que preparar.",
        details={"folder": str(p), "candidates": []})


def describir_seleccion(requested: Optional[str | Path],
                        resolved: Path, reason: str) -> Dict[str, Any]:
    """Bloque comun de trazabilidad de la seleccion."""
    return {
        "requested_path": str(requested) if requested else None,
        "resolved_path": str(resolved),
        "selection_reason": reason,
        # `path_match` responde a "¿es EXACTAMENTE lo que pedi?". Con una
        # carpeta o sin ruta explicita es `false` a proposito: se resolvio
        # algo, y quien lea la respuesta tiene derecho a notarlo.
        "path_match": misma_ruta(requested, resolved) if requested else False,
    }
