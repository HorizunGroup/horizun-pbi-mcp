"""Dejar listo EXACTAMENTE el proyecto que se pidio, y decir cual es.

El punto de entrada que faltaba. Hasta ahora, "trabaja sobre esto" se
resolvia de tres maneras distintas segun la tool: unas exigian `.pbip`, otras
convertian, y varias caian en el proyecto activo cuando la ruta no cuadraba.
Esa ultima es la peligrosa: una ruta explicita que falla y un proyecto activo
que la sustituye producen una respuesta en verde sobre el archivo equivocado.

Las reglas, en orden:

1. Una ruta EXPLICITA siempre gana: sobre el proyecto activo, sobre la sesion
   restaurada, sobre los archivos vecinos y sobre lo que haya abierto Desktop.
2. Un `.pbix` se convierte y se activa **el `.pbip` que produjo esa
   conversion**, no uno que se le parezca.
3. Una carpeta solo se resuelve si tiene exactamente un candidato.
4. Un lote nunca activa uno de los resultados por su cuenta.

Y sobre todo: si algo falla, el proyecto activo se queda **como estaba**.
Dejar la sesion apuntando a medio camino de una operacion fallida es como se
acaba escribiendo en el proyecto que no era.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.services import project_resolver

log = get_logger("project_prepare")


class ProjectPrepareError(PowerBIMCPError):
    code = "project_prepare_failed"


def _activo(session: Session) -> Optional[str]:
    activo = getattr(session, "active_pbip", None)
    return activo.pbip_path if activo is not None else None


def prepare(session: Session, path: str, *,
            out_dir: Optional[str] = None,
            project_name: Optional[str] = None,
            overwrite: bool = False,
            open_result: bool = False,
            include_model: bool = True) -> Dict[str, Any]:
    """Resuelve, prepara y activa exactamente un proyecto.

    `include_model` no lo expone la tool -convertir de verdad necesita el
    modelo, y sacarlo del .pbix obliga a abrir Power BI Desktop-. Existe como
    costura para que las pruebas ejerciten la RESOLUCION sin lanzar una
    ventana: una suite que abre Desktop deja de ser una suite.
    """
    from horizun_pbi_mcp.pbip import project_locator

    if not str(path or "").strip():
        raise ValidationError(
            "Indica `path`: el archivo o la carpeta que quieres preparar. "
            "Sin ruta no se adivina cual, y usar el proyecto activo aqui "
            "seria justo lo que esta tool existe para evitar.",
            details={"parameter": "path"})

    anterior = _activo(session)
    origen, motivo = project_resolver.resolver_entrada(path)
    salida: Dict[str, Any] = {
        **project_resolver.describir_seleccion(path, origen, motivo),
        "previous_active_project": anterior,
        "active_project": anterior,
        "converted": False,
        "opened": False,
    }

    if origen.suffix.casefold() == ".pbit":
        raise ProjectPrepareError(
            f"'{origen.name}' es una PLANTILLA (.pbit): no lleva datos ni es "
            "un proyecto. Abrela en Power BI Desktop y guardala como .pbix "
            "antes de prepararla.",
            details={**salida, "suffix": origen.suffix})

    if origen.suffix.casefold() == ".pbix":
        pbip = _convertir(origen, out_dir=out_dir, project_name=project_name,
                          overwrite=overwrite, salida=salida,
                          include_model=include_model)
        salida["selection_reason"] = project_resolver.CONVERTED_FROM_PBIX
        # `path_match` sigue midiendo contra lo PEDIDO: se pidio un .pbix y se
        # activa un .pbip, asi que no coincide y se dice.
        salida["path_match"] = False
        salida["source_pbix"] = str(origen)
        salida["resolved_path"] = str(pbip)
        objetivo = pbip
    else:
        objetivo = origen

    resumen = project_locator.open_project(session, str(objetivo))
    salida["active_project"] = resumen.get("pbip_path")
    salida["project"] = resumen
    salida["warnings"] = list(resumen.get("warnings") or [])

    # La activacion no puede acabar en otro archivo que el resuelto.
    if not project_resolver.misma_ruta(salida["active_project"], objetivo):
        raise ProjectPrepareError(
            "El proyecto activado no es el resuelto. No se continua con una "
            "sesion que apunta a otro sitio.",
            details={**salida, "expected": str(objetivo)})

    if open_result:
        salida["desktop"] = _abrir_y_verificar(session, objetivo, salida)
        salida["opened"] = bool(salida["desktop"].get("path_verified"))

    log.info("Proyecto preparado: %s (motivo=%s)", objetivo,
             salida["selection_reason"])
    return salida


def _convertir(pbix: Path, *, out_dir: Optional[str],
               project_name: Optional[str], overwrite: bool,
               salida: Dict[str, Any], include_model: bool = True) -> Path:
    """Convierte ESE .pbix y devuelve el .pbip que produjo la conversion.

    No se busca despues "un .pbip en la carpeta": se usa la ruta que la propia
    conversion declara. Si la carpeta tuviera otro proyecto de antes, buscarlo
    seria volver a elegir por orden alfabetico.
    """
    from horizun_pbi_mcp.pbip import pbix_to_pbip

    destino = Path(out_dir).expanduser() if out_dir else pbix.parent
    resultado = pbix_to_pbip.convert(
        pbix, destino, project_name=project_name, overwrite=overwrite,
        include_model=include_model)
    datos = resultado.to_dict()
    salida["conversion"] = datos
    salida["converted"] = True
    if datos.get("security_scan"):
        salida["security_scan"] = datos["security_scan"]

    producido = Path(datos["pbip_path"])
    if not producido.is_file():
        raise ProjectPrepareError(
            "La conversion declaro un .pbip que no existe en disco; no se "
            "activa nada.",
            details={**salida, "expected_pbip": str(producido)})
    return producido


def _abrir_y_verificar(session: Session, objetivo: Path,
                       salida: Dict[str, Any]) -> Dict[str, Any]:
    """Abre en Desktop y comprueba que la ventana sirve ESE archivo."""
    from horizun_pbi_mcp.powerbi import desktop_identity, desktop_launcher

    abierto = desktop_launcher.open_pbix(str(objetivo), reuse_open=True)
    identidad = desktop_identity.identify(abierto.instance, target=objetivo)
    return {
        "path": abierto.pbix_path,
        "launched_by_us": abierto.launched_by_us,
        "reused_open_session": not abierto.launched_by_us,
        "instance": {**abierto.instance, **identidad},
        "identity": identidad,
        # `path_verified` solo es True cuando la identidad lo DEMUESTRA. Un
        # .pbip no deja descriptor: ahi se queda en None y se dice.
        "path_verified": identidad.get("path_match"),
        "note": ("Un proyecto .pbip no deja descriptor sobre su carpeta: la "
                 "ruta se corrobora por el titulo de la ventana y eso es "
                 "confianza media, no prueba."),
    }
