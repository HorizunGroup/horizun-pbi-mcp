"""Exportar un `.pbip` a `.pbix` por el unico camino soportado.

Microsoft no publica ninguna API para convertir el formato. La respuesta a eso
no es rendirse ni fabricar el zip a mano -un `.pbix` cosido con TOM abre a
veces y rompe otras-, sino automatizar el flujo **oficial**: abrir el proyecto
en Power BI Desktop y usar `Archivo > Guardar como`.

Lo que hace que esto sea confiable no es la automatizacion, que es la parte
mecanica, sino lo que la rodea:

**Antes** (`preflight`): resolver la ruta EXACTA, validar el proyecto,
comprobar que el destino cabe en Windows y que se puede escribir, y respaldar
lo que se va a reemplazar. Todo eso ocurre **sin abrir Desktop**: descubrir
que el destino existe despues de haber lanzado una ventana es tarde y caro.

**Durante**: un dialogo visible NUNCA se reporta como timeout. Si Power BI
pide credenciales, avisa de un error de carga o pregunta si reemplazar, eso se
detecta, se clasifica y se devuelve con la accion sugerida.

**Despues**: que el dialogo desaparezca no es que se haya guardado. Se
comprueba que el archivo existe, que la extension es `.pbix` y no `.pbit`, que
pesa mas de cero, que su fecha es de ESTA ejecucion, que el lector de `.pbix`
del propio servidor lo puede abrir y que lleva informe y modelo. El hash se
calcula cuando la escritura ya termino.

Y si algo falla despues de haber reemplazado un destino que existia, se
restaura el respaldo; si la restauracion tampoco puede, se dice, en vez de
devolver un error limpio sobre un archivo que quedo destrozado.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import Session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.services import project_resolver

log = get_logger("pbix_export")

#: Modos de refresco. `auto` intenta y declara; `required` no exporta sin
#: datos; `skip` guarda lo que haya cargado ahora mismo.
REFRESH_AUTO, REFRESH_REQUIRED, REFRESH_SKIP = "auto", "required", "skip"
MODOS_REFRESH = (REFRESH_AUTO, REFRESH_REQUIRED, REFRESH_SKIP)

#: Formatos que `Guardar como` puede producir desde aqui. `.pbit` es una
#: plantilla: lleva informe y definicion del modelo, pero NO los datos.
FORMATO_PBIX, FORMATO_PBIT = "pbix", "pbit"
FORMATOS = (FORMATO_PBIX, FORMATO_PBIT)

#: Limite de Windows para la ruta de un archivo que Desktop debe poder abrir.
MAX_RUTA = 250

#: Cuanto se espera a que la ventana muestre el documento pedido antes de
#: conducirla. Que el motor tabular responda no significa que la interfaz
#: haya terminado de abrir: durante medio minuto el titulo dice `Sin titulo`.
ESPERA_IDENTIDAD = 90.0

#: Espera maxima a que el cuadro de guardado aparezca, en segundos.
ESPERA_DIALOGO = 60.0


class PbixExportError(PowerBIMCPError):
    code = "pbix_export_failed"


class PbixExportNotVerified(PbixExportError):
    """Se guardo algo, pero no se pudo demostrar que sea el entregable."""

    code = "pbix_export_not_verified"


class PbixWrongFormatError(PbixExportError):
    """Desktop guardo otra cosa: un proyecto o una plantilla, no un .pbix."""

    code = "pbix_wrong_format"


class PbixRestoreFailed(PbixExportError):
    """Fallo la exportacion Y fallo la restauracion del respaldo."""

    code = "pbix_restore_failed"


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


@dataclass
class Preflight:
    """Todo lo que se comprobo antes de tocar Power BI Desktop."""

    source_pbip: Path
    output_pbix: Path
    existia: bool = False
    backup: Optional[Path] = None
    validation: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_pbip": str(self.source_pbip),
            "output_pbix": str(self.output_pbix),
            "target_existed": self.existia,
            "backup": str(self.backup) if self.backup else None,
            "validation": self.validation,
            "warnings": self.warnings,
        }


def normalizar_formato(formato: Optional[str]) -> str:
    """`pbix` o `pbit`, en minusculas y sin punto. Otra cosa es un error."""
    valor = str(formato or FORMATO_PBIX).strip().casefold().lstrip(".")
    if valor not in FORMATOS:
        raise ValidationError(
            f"format='{formato}' no esta soportado. Usa {' o '.join(FORMATOS)}.",
            details={"parameter": "format", "valid": list(FORMATOS)})
    return valor


def _resolver_destino(pbip: Path, out_path: Optional[str],
                      formato: str = FORMATO_PBIX) -> Path:
    extension = f".{formato}"
    if out_path:
        destino = Path(str(out_path)).expanduser()
    else:
        destino = pbip.with_suffix(extension)
    if not destino.is_absolute():
        destino = (Path.cwd() / destino)
    destino = Path(os.path.normpath(str(destino)))
    if destino.suffix.casefold() != extension:
        # Sin comillas anidadas en el f-string: el repositorio soporta Python
        # 3.10 y ahi eso es un SyntaxError, no un detalle de estilo.
        tenia = destino.suffix or "(ninguna extension)"
        raise ValidationError(
            f"El destino debe terminar en {extension} y termina en '{tenia}'. "
            "La extension tiene que coincidir con el formato pedido: guardar "
            "con otra produce un archivo que Power BI no abre.",
            details={"parameter": "out_path", "out_path": str(destino),
                     "suffix": destino.suffix, "format": formato})
    return destino


def preflight(session: Session, *, pbip_path: Optional[str] = None,
              out_path: Optional[str] = None,
              overwrite: bool = False,
              formato: str = FORMATO_PBIX) -> Preflight:
    """Todo lo comprobable SIN abrir Power BI Desktop."""
    from horizun_pbi_mcp.pbip import project_locator
    from horizun_pbi_mcp.services import tmdl_validate

    if pbip_path:
        origen, _motivo = project_resolver.resolver_entrada(pbip_path)
        if origen.suffix.casefold() != ".pbip":
            raise ValidationError(
                f"Para exportar hace falta un proyecto .pbip y se recibio "
                f"'{origen.name}'. Convierte antes con pbi_prepare_project.",
                details={"path": str(origen)})
    else:
        activo = getattr(session, "active_pbip", None)
        if activo is None:
            raise ValidationError(
                "No hay proyecto activo y no se indico `pbip_path`. Abre uno "
                "con pbi_prepare_project o pasa la ruta exacta.",
                details={"parameter": "pbip_path"})
        origen = Path(activo.pbip_path)
    if not origen.is_file():
        raise ValidationError(f"El proyecto no existe: {origen}",
                              details={"path": str(origen)})

    destino = _resolver_destino(origen, out_path, formato)
    resultado = Preflight(source_pbip=origen, output_pbix=destino)

    if len(str(destino)) >= MAX_RUTA:
        sobra = len(str(destino)) - MAX_RUTA + 1
        raise ValidationError(
            f"La ruta de destino mide {len(str(destino))} caracteres y Power "
            f"BI Desktop no guarda ahi. Elige una carpeta al menos {sobra} "
            "caracteres mas corta (p.ej. C:\\pbix).",
            details={"out_path": str(destino), "limit": MAX_RUTA,
                     "length": len(str(destino))})

    # El proyecto se valida ANTES: exportar un .pbip que no abre produce un
    # .pbix que tampoco, despues de haber abierto una ventana para nada.
    activo_para_validar = project_locator._build_active_pbip(origen)  # noqa: SLF001
    if activo_para_validar.has_tmdl:
        definicion = Path(activo_para_validar.semantic_model_dir) / "definition"
        validacion = tmdl_validate.validate(definicion, use_tom=True)
        resultado.validation = {
            "tmdl_valid": validacion.get("valid"),
            "parse_checked": validacion.get("parse_checked"),
            "error_count": validacion.get("error_count"),
        }
        if not validacion.get("valid"):
            raise PbixExportError(
                f"El modelo TMDL tiene {validacion.get('error_count')} "
                "error(es): Power BI Desktop no abriria el proyecto, asi que "
                "no hay nada que exportar. Corrigelos y repite.",
                details={"findings": [f for f in validacion.get("findings", [])
                                      if f.get("severity") == "error"][:20],
                         **resultado.to_dict()})
    else:
        resultado.warnings.append(
            "El proyecto no tiene modelo TMDL propio; se exporta el informe "
            "tal cual, sin validacion de modelo.")

    resultado.existia = destino.exists()
    if resultado.existia and not overwrite:
        raise PbixExportError(
            f"El destino ya existe: {destino}. Usa overwrite=true si de "
            "verdad quieres reemplazarlo. No se abre Power BI Desktop para "
            "descubrir esto a mitad de camino.",
            details=resultado.to_dict())
    if resultado.existia:
        resultado.backup = _respaldar(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    return resultado


def _respaldar(destino: Path) -> Path:
    """Copia recuperable del destino que se va a reemplazar."""
    from horizun_pbi_mcp.config import get_settings

    raiz = Path(get_settings().backups_dir) / "pbix_export"
    raiz.mkdir(parents=True, exist_ok=True)
    copia = raiz / f"{destino.stem}_{int(time.time())}{destino.suffix}"
    shutil.copy2(destino, copia)
    log.info("Respaldo del destino en %s", copia)
    return copia


def _restaurar(preflight_: Preflight) -> Dict[str, Any]:
    """Devuelve el destino a como estaba. Si no puede, lo DICE."""
    if not preflight_.backup or not preflight_.backup.is_file():
        return {"restored": False, "reason": "no habia respaldo que restaurar"}
    try:
        shutil.copy2(preflight_.backup, preflight_.output_pbix)
        return {"restored": True, "from": str(preflight_.backup)}
    except OSError as exc:
        return {"restored": False, "from": str(preflight_.backup),
                "error": f"{type(exc).__name__}: {exc}",
                "action_required": (
                    "El destino quedo reemplazado y la restauracion fallo. La "
                    "copia original sigue en la ruta de 'from': recuperala a "
                    "mano antes de seguir.")}


# ------------------------------------------------------------------ refresh --
def _refrescar(session: Session, modo: str,
               instancia: Dict[str, Any]) -> Dict[str, Any]:
    """Intenta refrescar y declara EXACTAMENTE lo que paso."""
    from horizun_pbi_mcp.powerbi import refresh as refresh_mod

    salida: Dict[str, Any] = {
        "refresh_requested": modo,
        "refresh_checked": False,
        "refresh_succeeded": None,
        "data_loaded": None,
        "refresh_warnings": [],
    }
    estado = refresh_mod.estado_de_datos(
        instancia.get("connection_string", ""), instancia.get("catalog"))
    salida["data_loaded"] = estado.get("data_loaded")

    if modo == REFRESH_SKIP:
        salida["refresh_warnings"].append(
            "refresh='skip': se guarda el estado que el modelo tenga ahora "
            "mismo. Si no habia datos cargados, el .pbix saldra sin datos.")
        return salida

    try:
        detalle = refresh_mod.refresh_model(session)
        salida["refresh_checked"] = True
        salida["refresh_succeeded"] = True
        salida["refresh_detail"] = {k: detalle.get(k) for k in
                                    ("duration_ms", "refresh_type", "tables")
                                    if k in detalle}
    except Exception as exc:                              # noqa: BLE001
        salida["refresh_checked"] = True
        salida["refresh_succeeded"] = False
        salida["refresh_error"] = {
            "code": getattr(exc, "code", "unexpected"),
            "message": str(getattr(exc, "message", exc))[:300]}
        if modo == REFRESH_REQUIRED:
            raise PbixExportError(
                "refresh='required' y el refresco fallo: no se exporta un "
                ".pbix presentandolo como entregable con datos.",
                details=salida) from exc
        salida["refresh_warnings"].append(
            "El refresco fallo y refresh='auto': se exporta el estado actual. "
            "NO se afirma que el .pbix lleve datos actualizados.")
        return salida

    posterior = refresh_mod.estado_de_datos(
        instancia.get("connection_string", ""), instancia.get("catalog"))
    salida["data_loaded"] = posterior.get("data_loaded")
    if modo == REFRESH_REQUIRED and posterior.get("data_loaded") is not True:
        raise PbixExportError(
            "refresh='required' y no se pudo comprobar que el modelo tenga "
            "datos cargados. No se entrega un .pbix como si los tuviera.",
            details=salida)
    return salida


# ------------------------------------------------------------- verificacion --
def _inspeccionar_plantilla(destino: Path, *,
                            espera_modelo: Optional[bool] = None
                            ) -> Dict[str, Any]:
    """Estructura de un `.pbit`: informe, definicion del modelo, y SIN datos.

    Una plantilla es el mismo contenedor que un `.pbix` pero con la
    definicion del modelo (`DataModelSchema`) en lugar del modelo cargado
    (`DataModel`). No se fabrica quitandolos de un zip: se lee lo que Desktop
    escribio y se comprueba que tenga esa forma. `espera_modelo` dice si el
    ORIGEN tenia modelo: entonces la plantilla tiene que llevar su
    definicion, y si no la lleva no es la plantilla de ese proyecto. Un
    informe sin modelo propio no la lleva, y eso no es un fallo. Nunca se
    afirma que lleve datos, porque una plantilla nunca los lleva.
    """
    import zipfile

    try:
        with zipfile.ZipFile(destino) as zf:
            nombres = zf.namelist()
            tamanos = {n: zf.getinfo(n).file_size for n in nombres}
    except zipfile.BadZipFile as exc:
        raise PbixExportNotVerified(
            "El archivo existe pero no es un contenedor de Power BI valido.",
            details={"output_pbix": str(destino),
                     "cause": f"{type(exc).__name__}: {exc}"[:200]}) from exc
    tiene_informe = any(n.startswith("Report/") for n in nombres)
    modelo_datos = tamanos.get("DataModel", 0)
    tiene_esquema = any(n in ("DataModelSchema",) or n.startswith("DataModelSchema")
                        for n in nombres)
    resumen = {
        "path": str(destino),
        "report_format": ("pbir" if any(n.startswith("Report/definition/")
                                        for n in nombres)
                          else "layout" if "Report/Layout" in nombres
                          else "none"),
        "has_data_model": modelo_datos > 0,
        "data_model_size": modelo_datos,
        "has_model_schema": tiene_esquema,
        "parts": len(nombres),
        "template": True,
        "warnings": [],
    }
    if not tiene_informe:
        raise PbixExportNotVerified(
            "La plantilla guardada no contiene ningun informe.",
            details={"output_pbix": str(destino), "summary": resumen})
    if modelo_datos > 0:
        # Una plantilla no lleva datos por definicion. Si los lleva, Desktop
        # guardo otra cosa con esa extension, y eso no se entrega como .pbit.
        raise PbixExportNotVerified(
            "El archivo lleva un DataModel con contenido: no tiene la forma "
            "de una plantilla. Desktop guardo otro formato con extension "
            ".pbit; comprueba el tipo elegido en el cuadro.",
            details={"output_pbix": str(destino), "summary": resumen,
                     "reason": "template_has_data_model"})
    if espera_modelo and not tiene_esquema:
        raise PbixExportNotVerified(
            "El proyecto tiene modelo semantico y la plantilla guardada no "
            "lleva su definicion (DataModelSchema): no es la plantilla de "
            "este proyecto.",
            details={"output_pbix": str(destino), "summary": resumen,
                     "reason": "template_without_model_schema"})
    resumen["model_schema_expected"] = espera_modelo
    return resumen


def verificar_salida(destino: Path, *, desde: float,
                     antes: Optional[Dict[str, float]] = None,
                     formato: str = FORMATO_PBIX,
                     espera_modelo: Optional[bool] = None) -> Dict[str, Any]:
    """Que el archivo existe, es del formato pedido y es de ESTA ejecucion."""
    from horizun_pbi_mcp.pbip import pbix_reader

    extension = f".{formato}"
    comprobaciones: Dict[str, Any] = {"exists": destino.is_file()}
    if not comprobaciones["exists"]:
        raise PbixExportNotVerified(
            "El cuadro de guardado se cerro pero el archivo no esta en el "
            "destino. Que desaparezca un dialogo no es que se haya guardado.",
            details={"output_pbix": str(destino), "checks": comprobaciones})

    comprobaciones["extension"] = destino.suffix.casefold()
    if comprobaciones["extension"] != extension:
        raise PbixExportNotVerified(
            f"El archivo guardado es '{destino.suffix}', no '{extension}'.",
            details={"output_pbix": str(destino), "checks": comprobaciones})

    stat = destino.stat()
    comprobaciones["size"] = stat.st_size
    if stat.st_size <= 0:
        raise PbixExportNotVerified(
            "El archivo guardado esta vacio.",
            details={"output_pbix": str(destino), "checks": comprobaciones})

    comprobaciones["mtime"] = stat.st_mtime
    comprobaciones["mtime_in_this_run"] = stat.st_mtime >= desde - 2
    if not comprobaciones["mtime_in_this_run"]:
        raise PbixExportNotVerified(
            "El archivo del destino es ANTERIOR a esta ejecucion: lo que hay "
            "ahi no lo escribio este guardado.",
            details={"output_pbix": str(destino), "checks": comprobaciones,
                     "started_at": desde})

    if formato == FORMATO_PBIT:
        resumen = _inspeccionar_plantilla(destino, espera_modelo=espera_modelo)
        comprobaciones["report_format"] = resumen.get("report_format")
        comprobaciones["has_data_model"] = resumen.get("has_data_model")
        comprobaciones["template"] = True
        comprobaciones["inspected"] = True
        return {
            "checks": comprobaciones,
            "output_size": stat.st_size,
            "output_sha256": sha256_de(destino),
            "pbix_summary": resumen,
        }

    try:
        contenido = pbix_reader.read_pbix(destino)
    except Exception as exc:                              # noqa: BLE001
        raise PbixExportNotVerified(
            "El archivo existe pero el lector de .pbix no lo puede abrir; no "
            "se declara entregable algo que no se pudo inspeccionar.",
            details={"output_pbix": str(destino), "checks": comprobaciones,
                     "cause": f"{type(exc).__name__}: {exc}"[:200]}) from exc

    resumen = contenido.summary()
    comprobaciones["report_format"] = resumen.get("report_format")
    comprobaciones["has_data_model"] = resumen.get("has_data_model")
    comprobaciones["inspected"] = True
    if resumen.get("report_format") == "none":
        raise PbixExportNotVerified(
            "El .pbix guardado no contiene ningun informe.",
            details={"output_pbix": str(destino), "checks": comprobaciones})

    # El hash se calcula cuando la escritura YA termino y el archivo se pudo
    # abrir entero: un sha256 tomado a mitad de un guardado no identifica nada.
    return {
        "checks": comprobaciones,
        "output_size": stat.st_size,
        "output_sha256": sha256_de(destino),
        "pbix_summary": resumen,
    }


#: Cuanto se espera a que el archivo APAREZCA, aparte del plazo total. Power
#: BI Desktop lo crea en segundos tras cerrar el cuadro; si en dos minutos no
#: hay nada, no lo va a haber. Separarlo del plazo global es lo que evita que
#: un guardado fallido se lleve el presupuesto entero de la operacion.
GRACIA_APARICION = 120.0


def esperar_escritura_terminada(destino: Path, *, timeout: float,
                                adapter: Any = None, pid: Optional[int] = None,
                                excluir: Optional[List[int]] = None,
                                origen: Optional[Path] = None,
                                gracia: float = GRACIA_APARICION,
                                desde: Optional[float] = None
                                ) -> Dict[str, Any]:
    """Espera a que el archivo APAREZCA y termine de escribirse.

    Que el cuadro de guardado se cierre no es que el archivo este. Power BI
    Desktop cierra el dialogo y **escribe despues**, con su propia barra de
    progreso: comprobar en ese instante encuentra la carpeta vacia y declara
    un fallo que no lo es. Lo descubrio la primera ejecucion real, que fallo
    con "el cuadro se cerro pero el archivo no esta en el destino".

    Se espera a dos cosas, no a una: que exista y que su tamano deje de
    cambiar. Un `.pbix` a medio escribir existe y no se puede abrir.

    Mientras se espera se vigilan los modales: si Power BI abre un cuadro
    -disco lleno, archivo bloqueado- eso es la explicacion, no un plazo
    agotado.

    Los dos plazos son distintos a proposito. `timeout` cubre un archivo que
    ya aparecio y sigue creciendo, que en un modelo grande tarda; `gracia`
    cubre que aparezca, y eso Desktop lo hace en segundos. Cuando eran el
    mismo, un guardado que jamas iba a ocurrir se pasaba un cuarto de hora
    mirando una carpeta vacia antes de contarlo.
    """
    limite = time.monotonic() + float(timeout)
    inicio = time.monotonic()
    tope_aparicion = inicio + min(float(gracia), float(timeout))
    ultimo_tamano = -1
    estable_desde = None
    visto = False

    while time.monotonic() < limite:
        if adapter is not None and pid:
            try:
                modales = adapter.modales(pid, excluir=excluir or [])
            except Exception:                             # noqa: BLE001
                modales = []
            if modales:
                from horizun_pbi_mcp.powerbi import desktop_ui

                raise desktop_ui.DesktopModalError(
                    "Power BI Desktop abrio un dialogo mientras guardaba; el "
                    "guardado no termino.",
                    details={"modals": [m.to_dict() for m in modales],
                             "phase": "esperar_escritura",
                             "output_pbix": str(destino)})
        if destino.is_file():
            visto = True
            tamano = destino.stat().st_size
            if tamano > 0 and tamano == ultimo_tamano:
                if estable_desde is None:
                    estable_desde = time.monotonic()
                elif time.monotonic() - estable_desde >= 1.5:
                    # Ademas de estable, tiene que poder abrirse: si Desktop
                    # aun lo tiene bloqueado, la inspeccion posterior fallaria.
                    try:
                        with open(destino, "rb") as fh:
                            fh.read(4)
                        return {"waited": True, "size": tamano,
                                "stable": True}
                    except OSError:
                        estable_desde = None
            else:
                estable_desde = None
            ultimo_tamano = tamano
        elif time.monotonic() > tope_aparicion:
            break              # no aparecio: esperar mas no lo va a traer
        time.sleep(0.5)

    # La clave es `wait_reason` y no `reason` a proposito: quien fusiona este
    # diccionario en el error ya tiene su propio `reason`, y machacarlo
    # cambiaba el codigo de fallo por una frase.
    extraviado = artefacto_extraviado(destino, origen, desde=desde)
    if extraviado:
        raise PbixWrongFormatError(
            "Power BI Desktop guardo el archivo en la carpeta del PROYECTO en "
            f"vez de en la carpeta pedida: aparecio '{extraviado['found']}'. "
            "Pasa cuando el cuadro no llego a recibir la ruta completa: su "
            "nombre y su carpeta por defecto mandan.",
            details={"reason": "saved_in_source_folder",
                     "requested": str(destino), **extraviado})

    otro = artefacto_de_otro_formato(destino)
    if otro:
        raise PbixWrongFormatError(
            "Power BI Desktop guardo un PROYECTO en vez de un archivo .pbix: "
            f"aparecio '{otro['kind']}' junto al destino pedido. El "
            "desplegable de tipo del cuadro de guardado se puede leer y hasta "
            "cambiar de aspecto, pero Desktop siguio usando su formato "
            "anterior. Un .pbip no es un entregable, asi que esto NO se da "
            "por bueno.",
            details={"reason": "saved_in_wrong_format",
                     "requested": str(destino), **otro})
    esperado = round(time.monotonic() - inicio, 1)
    return {"waited": True, "stable": False, "appeared": visto,
            "timeout": timeout, "waited_seconds": esperado,
            "wait_reason": (
                f"el archivo aparecio pero no dejo de crecer en {esperado:.0f} s"
                if visto else
                f"el archivo nunca aparecio en el destino ({esperado:.0f} s)")}


#: Lo que Power BI deja cuando guarda un PROYECTO en vez de un archivo.
_RASTROS_DE_PROYECTO = (".pbip", ".Report", ".SemanticModel", ".pbit")


def artefacto_de_otro_formato(destino: Path) -> Optional[Dict[str, Any]]:
    """Detecta que se guardo OTRA cosa con el nombre que se pidio.

    Al pedir `Informe.pbix` con el filtro en `.pbip`, Desktop escribe
    `Informe.pbix.pbip` y sus carpetas `.Report` y `.SemanticModel`. Sin esta
    comprobacion el fallo se reporta como "el archivo no esta en el destino",
    que es cierto y no explica nada: la persona mira la carpeta, ve tres cosas
    con su nombre y no entiende que paso.
    """
    encontrados = []
    for sufijo in _RASTROS_DE_PROYECTO:
        candidato = Path(str(destino) + sufijo)
        if candidato.exists():
            encontrados.append(str(candidato))
        hermano = destino.with_suffix(sufijo)
        if hermano != destino and hermano.exists():
            encontrados.append(str(hermano))
    if not encontrados:
        return None
    return {"kind": "proyecto .pbip" if any(p.endswith(".pbip")
                                            for p in encontrados)
                    else "artefacto de otro formato",
            "found": sorted(set(encontrados))}


def artefacto_extraviado(destino: Path, origen: Optional[Path], *,
                         desde: Optional[float] = None
                         ) -> Optional[Dict[str, Any]]:
    """Busca el archivo pedido en la carpeta del .pbip, y SOLO alli.

    No se rastrea el disco: se mira el basename exacto de esta ejecucion en
    las dos unicas carpetas donde Desktop puede haberlo dejado -la pedida, que
    ya se comprobo, y la del proyecto de origen-. Buscar mas ancho es como
    aceptar cualquier archivo con nombre parecido: encontraria el de otra
    ejecucion y lo daria por bueno.

    Con `desde` se anade una segunda pasada por esa MISMA carpeta: archivos
    de la extension pedida escritos DURANTE esta ejecucion, aunque se llamen
    de otra forma. Hace falta porque el cuadro puede guardar con su nombre y
    su carpeta por defecto -paso en una prueba real: se pidio una ruta larga
    en otra carpeta y aparecio `Demo.pbix` junto al proyecto-, y entonces
    "el archivo nunca aparecio" es cierto y deja basura sin explicar.
    """
    if origen is None:
        return None
    carpeta = origen.parent if origen.suffix else origen
    try:
        if carpeta.resolve() == destino.parent.resolve():
            return None                     # es la misma: ya se comprobo
    except OSError:                                       # pragma: no cover
        return None
    candidato = carpeta / destino.name
    if candidato.is_file():
        return {"found": str(candidato), "source_folder": str(carpeta),
                "size": candidato.stat().st_size, "same_name": True}
    if desde is None:
        return None
    try:
        recientes = [p for p in carpeta.iterdir()
                     if p.is_file()
                     and p.suffix.casefold() == destino.suffix.casefold()
                     and p.stat().st_mtime >= desde - 2]
    except OSError:                                       # pragma: no cover
        return None
    if not recientes:
        return None
    elegido = max(recientes, key=lambda p: p.stat().st_mtime)
    return {"found": str(elegido), "source_folder": str(carpeta),
            "size": elegido.stat().st_size, "same_name": False,
            "requested_name": destino.name,
            "detail": ("el cuadro guardo con SU nombre por defecto en la "
                       "carpeta del proyecto, no con el que se le pidio")}


def _vecinos(carpeta: Path) -> Dict[str, float]:
    try:
        return {p.name: p.stat().st_mtime for p in carpeta.iterdir()
                if p.is_file()}
    except OSError:                                       # pragma: no cover
        return {}


def _colaterales(antes: Dict[str, float], carpeta: Path,
                 destino: Path) -> List[str]:
    """Otros archivos creados o pisados en la carpeta durante el guardado."""
    despues = _vecinos(carpeta)
    tocados = []
    for nombre, mtime in despues.items():
        if nombre == destino.name:
            continue
        if nombre not in antes or antes[nombre] != mtime:
            tocados.append(nombre)
    return sorted(tocados)


# ------------------------------------------------------------ orquestacion ---
def _abrir_o_reutilizar(objetivo: Path, *, timeout: int,
                        confirm_reuse: bool) -> Any:
    """Deja el proyecto servido por Desktop, exigiendo permiso si ya lo estaba.

    Conducir una ventana que abrio el usuario es distinto de conducir la
    nuestra: puede tener cambios sin guardar que este 'Guardar como'
    escribiria. Por eso hace falta `confirm_reuse=true` explicito.
    """
    from horizun_pbi_mcp.powerbi import desktop_launcher

    ya_abierto = desktop_launcher.proceso_con_archivo_abierto(objetivo)
    if ya_abierto and not confirm_reuse:
        raise PbixExportError(
            f"El proyecto ya esta abierto en Power BI Desktop (pid "
            f"{ya_abierto}) y esa ventana es del usuario: puede tener cambios "
            "sin guardar que este 'Guardar como' escribiria. Pasa "
            "confirm_reuse=true si quieres conducir esa ventana.",
            details={"path": str(objetivo), "desktop_pid": ya_abierto,
                     "reason": "desktop_session_belongs_to_user"})
    return desktop_launcher.open_pbix(str(objetivo), timeout=timeout,
                                      reuse_open=True)


def _identidad_verificada(abierto: Any, objetivo: Path, *,
                          timeout: float = ESPERA_IDENTIDAD) -> Dict[str, Any]:
    """Identidad de la ventana, ESPERANDO a que se asiente antes de juzgar.

    `Sin titulo - Power BI Desktop` es la ventana cargando, no otra ventana.
    Rechazarla al instante hacia que la misma peticion fallara y, treinta
    segundos despues, funcionara. Se sondea el titulo con tope: un titulo
    provisional espera; uno estable de OTRO documento se rechaza enseguida,
    porque esperar no lo va a convertir en el nuestro.
    """
    from horizun_pbi_mcp.powerbi import desktop_identity

    identidad = desktop_identity.identify(abierto.instance, target=objetivo)
    if identidad.get("desktop_pid") is None:
        raise PbixExportError(
            "No se pudo identificar el proceso de Power BI Desktop que sirve "
            "este proyecto. No se conduce una ventana sin saber cual es.",
            details={"path": str(objetivo), "identity": identidad})

    titulo = identidad.get("desktop_window_title")
    # Solo se espera cuando lo que hay es "todavia no": sin documento probado
    # y con un titulo ausente o provisional. Un documento abierto distinto, o
    # un titulo estable de otro informe, es una respuesta, no una espera.
    pendiente = (identidad.get("path_match") is not True
                 and not identidad.get("project_path")
                 and (not titulo or desktop_identity.titulo_provisional(titulo)))
    if pendiente:
        espera = desktop_identity.esperar_identidad_de_ventana(
            identidad["desktop_pid"], objetivo, timeout=timeout)
        identidad["window_wait"] = espera
        if espera.get("settled"):
            identidad = {**desktop_identity.identify(abierto.instance,
                                                     target=objetivo),
                         "window_wait": espera}
    if identidad.get("path_match") is False:
        estado = (identidad.get("window_wait") or {}).get("status")
        if estado == desktop_identity.IDENTIDAD_TIMEOUT:
            raise PbixExportError(
                "La ventana de Power BI Desktop no llego a mostrar el "
                "documento pedido en el plazo: el titulo siguio siendo "
                "provisional. No se guarda desde una ventana que aun no "
                "termino de abrir.",
                details={"path": str(objetivo), "identity": identidad,
                         "reason": "desktop_window_not_settled"})
        raise PbixExportError(
            "La ventana identificada esta sirviendo OTRO documento. No se "
            "guarda desde ahi por mucho que sea la unica que aparecio durante "
            "el intervalo de lanzamiento.",
            details={"path": str(objetivo), "identity": identidad,
                     "reason": "desktop_serves_other_document"})
    return identidad


def _guardar_como(adapter: Any, *, pid: int, started: Optional[float],
                  destino: Path, timeout: float,
                  origen: Optional[Path] = None,
                  formato: str = FORMATO_PBIX,
                  desde: Optional[float] = None,
                  overwrite: bool = False) -> Dict[str, Any]:
    """El flujo oficial, paso a paso y sin aceptar nada por defecto.

    El adaptador real ofrece `save_as_completo`, que hace toda la interaccion
    en un proceso aparte: una llamada COM bloqueada solo se puede cancelar
    terminando el proceso que la hizo. El doble de las pruebas no la ofrece y
    entonces se recorre paso a paso, que es lo que permite comprobar la logica
    -tipo forzado, modal distinguido de timeout- sin abrir ninguna ventana.
    """
    from horizun_pbi_mcp.powerbi import desktop_ui

    extension = f".{formato}"
    if hasattr(adapter, "save_as_completo"):
        # `overwrite` solo viaja cuando es cierto: es la autorizacion para
        # aceptar el "¿reemplazar?" del propio cuadro, y nada mas.
        extra = {"overwrite": True} if overwrite else {}
        respuesta = adapter.save_as_completo(
            pid=pid, started=started, destino=str(destino),
            extension=extension, timeout=timeout, **extra)
        plantilla = respuesta.get("template_dialog")
        if formato == FORMATO_PBIT and plantilla and plantilla.get("seen") \
                and not plantilla.get("accepted"):
            raise desktop_ui.DesktopModalError(
                "Desktop abrio el dialogo de plantilla y no se pudo aceptar "
                "desde aqui. Atiendelo en la ventana y repite.",
                details={"template_dialog": plantilla,
                         "helper": respuesta.get("steps")})
        if respuesta.get("modals"):
            raise desktop_ui.DesktopModalError(
                "El guardado se detuvo en un dialogo. No se cierra "
                "automaticamente: alguno de estos implica perder datos.",
                details={"modals": respuesta["modals"]})
        if not respuesta.get("dialog_closed"):
            # Sin esto el fallo se degradaba a "el archivo nunca aparecio", que
            # es cierto y manda a mirar la carpeta equivocada: lo que pasa es
            # que el cuadro sigue en pantalla esperando algo.
            raise PbixExportError(
                "El cuadro de guardado sigue abierto y no hay ningun dialogo "
                "que lo explique. No se declara guardado lo que no termino.",
                details={"reason": "save_dialog_still_open",
                         "phase": "guardar_como", "timeout": timeout,
                         "helper": respuesta.get("steps")})

        espera = esperar_escritura_terminada(
            destino, timeout=timeout, adapter=adapter, pid=pid, origen=origen,
            desde=desde)
        if not espera.get("stable"):
            raise PbixExportError(
                "El cuadro de guardado se cerro pero la escritura no termino: "
                f"{espera.get('wait_reason')}. No se declara entregado lo que "
                "no se pudo ver terminar.",
                details={"reason": "save_did_not_finish",
                         "phase": "guardar_como",
                         "output_pbix": str(destino),
                         "helper": respuesta.get("steps"), **espera})
        return {"file_type_selected": respuesta.get("file_type_selected"),
                "commit_method": respuesta.get("commit_method"),
                "dialog_closed": respuesta.get("dialog_closed"),
                "filename_method": respuesta.get("filename_method"),
                "overwrite_confirmed": respuesta.get("overwrite_confirmed"),
                "template_dialog": plantilla,
                "helper_steps": respuesta.get("steps"),
                "write_wait": espera}

    ventana = adapter.ventana_principal(pid, started)
    previos = [m.hwnd for m in adapter.modales(pid)]
    adapter.abrir_guardar_como(ventana)

    try:
        dialogo = adapter.esperar_dialogo_guardado(pid, timeout=ESPERA_DIALOGO)
    except desktop_ui.DesktopModalError:
        raise
    except desktop_ui.DesktopUIError as exc:
        # Un dialogo VISIBLE jamas se reporta como timeout: se mira antes de
        # concluir que "no aparecio nada".
        modales = adapter.modales(pid, excluir=previos)
        if modales:
            raise desktop_ui.DesktopModalError(
                "Power BI Desktop tiene un dialogo abierto esperando una "
                "respuesta; no es que se haya agotado el tiempo.",
                details={"modals": [m.to_dict() for m in modales]}) from exc
        raise

    tipo = adapter.elegir_tipo(dialogo, extension)
    adapter.escribir_ruta(dialogo, str(destino))
    adapter.confirmar(dialogo)

    cerrado = adapter.esperar_cierre(dialogo, timeout=timeout)
    modales = adapter.modales(pid, excluir=list(previos) + [dialogo.hwnd])
    if modales:
        raise desktop_ui.DesktopModalError(
            "El guardado se detuvo en un dialogo. No se cierra "
            "automaticamente: alguno de estos implica perder datos.",
            details={"modals": [m.to_dict() for m in modales],
                     "dialog_closed": cerrado})
    if not cerrado:
        raise PbixExportError(
            "El cuadro de guardado sigue abierto y no hay ningun dialogo que "
            "lo explique. No se declara guardado lo que no termino.",
            details={"reason": "save_dialog_still_open", "timeout": timeout})

    # El dialogo se cerro: ahora empieza la escritura de verdad.
    espera = esperar_escritura_terminada(
        destino, timeout=timeout, adapter=adapter, pid=pid,
        excluir=list(previos) + [dialogo.hwnd])
    if not espera.get("stable"):
        raise PbixExportError(
            "El cuadro de guardado se cerro pero la escritura no termino: "
            f"{espera.get('wait_reason')}. No se declara entregado lo que "
            "no se pudo ver terminar.",
            details={"reason": "save_did_not_finish", "phase": "guardar_como",
                     "output_pbix": str(destino), **espera})
    return {"file_type_selected": tipo, "dialog_closed": True,
            "write_wait": espera}


def _estado_final(session: Session, *, abierto: Any, destino: Path,
                  leave_open: bool, timeout: int,
                  formato: str = FORMATO_PBIX,
                  origen: Optional[Path] = None) -> Dict[str, Any]:
    """Deja abierto EXACTAMENTE el archivo generado, o cierra solo lo nuestro.

    Lo que se AFIRMA sale de la evidencia que hay, y de ninguna otra parte:

    - descriptor abierto sobre el destino: la ventana siguio al archivo
      (confianza alta, `opened_path_verified`);
    - titulo de la ventana igual al nombre del destino, con un nombre
      DISTINTO al del proyecto: siguio (confianza media, no verificado);
    - mismo nombre que el proyecto y sin descriptor: el titulo no distingue
      el `.pbip` del `.pbix` y no se afirma nada; tampoco se abre otra
      ventana con esa duda;
    - plantilla `.pbit`: Desktop no cambia de documento al guardarla, asi que
      la ventana sigue sirviendo el proyecto de origen.

    `desktop_session` dice que documento sirve la ventana con la confianza
    que corresponde, o `null` con los candidatos cuando no se sabe.
    """
    from horizun_pbi_mcp.powerbi import (desktop_discovery, desktop_identity,
                                         desktop_launcher)

    salida: Dict[str, Any] = {"leave_open": leave_open}
    if not leave_open:
        # Solo se cierra lo que abrio ESTA operacion. Una ventana del usuario
        # no se toca ni aunque este sirviendo el mismo archivo.
        if abierto.launched_by_us:
            salida["closed"] = desktop_launcher.close(abierto)
        else:
            salida["closed"] = {
                "closed": False,
                "reason": "la sesion ya estaba abierta; es del usuario"}
        salida["opened_path_verified"] = False
        return salida

    pid = abierto.desktop_pid
    instancia = abierto.instance
    identidad: Dict[str, Any]
    documento: Optional[str] = None
    confianza = desktop_identity.UNKNOWN
    candidatos: List[str] = []

    if formato == FORMATO_PBIT:
        # Guardar una plantilla no reapunta la ventana: sigue en el origen.
        identidad = desktop_identity.identify(
            abierto.instance, target=origen) if origen else {}
        salida.update({
            "same_window_followed": False, "reopened": False,
            "desktop_pid": pid, "identity": identidad,
            "opened_path_verified": False,
            "window_follow": {
                "status": "not_applicable",
                "detail": "una plantilla no reemplaza al documento abierto; "
                          "la ventana sigue sirviendo el proyecto de origen"},
        })
        documento = str(origen) if origen else None
        confianza = str(identidad.get("identity_confidence") or desktop_identity.UNKNOWN)
    else:
        abierto_ahora = desktop_launcher.proceso_con_archivo_abierto(destino)
        evidencia = "open_file" if abierto_ahora else None
        seguimiento: Optional[Dict[str, Any]] = None
        if not abierto_ahora and pid:
            if origen is not None and destino.stem.casefold() == origen.stem.casefold():
                seguimiento = {
                    "status": "inconclusive",
                    "detail": "el destino se llama como el proyecto: el "
                              "titulo de la ventana no distingue el .pbip "
                              "del .pbix, y no hay descriptor sobre el "
                              "destino"}
            else:
                seguimiento = desktop_identity.esperar_identidad_de_ventana(
                    pid, destino, timeout=30.0)
                if seguimiento.get("settled"):
                    abierto_ahora = int(pid)
                    evidencia = "window_title"
        salida["same_window_followed"] = bool(
            abierto_ahora and int(abierto_ahora) == int(pid or -1))
        if seguimiento is not None:
            salida["window_follow"] = seguimiento

        if not abierto_ahora and seguimiento is not None \
                and seguimiento.get("status") == "inconclusive":
            # Con la duda no se lanza otra ventana ni se afirma nada.
            identidad = desktop_identity.identify(abierto.instance, target=destino)
            salida.update({"reopened": False, "desktop_pid": pid,
                           "identity": identidad,
                           "opened_path_verified": False,
                           "follow_note": (
                               "No se pudo demostrar si la ventana siguio al "
                               "archivo exportado; no se abrio otra. Cierra "
                               "por identidad con `desktop_session` si hace "
                               "falta.")})
            candidatos = [str(origen), str(destino)] if origen else [str(destino)]
        elif not abierto_ahora:
            # No siguio: se abre explicitamente el entregable. La ventana
            # anterior solo se cierra si la lanzamos nosotros y su identidad
            # sigue valiendo.
            if abierto.launched_by_us:
                salida["closed_previous"] = desktop_launcher.close(abierto)
            else:
                salida["closed_previous"] = {
                    "closed": False,
                    "reason": "la ventana anterior es del usuario; no se cierra"}
            nuevo = desktop_launcher.open_pbix(str(destino), timeout=timeout,
                                               reuse_open=True)
            identidad = desktop_identity.identify(nuevo.instance, target=destino)
            salida["reopened"] = True
            salida["desktop_pid"] = nuevo.desktop_pid
            pid = nuevo.desktop_pid
            salida["identity"] = identidad
            instancia = nuevo.instance
            confianza = str(identidad.get("identity_confidence") or desktop_identity.UNKNOWN)
            salida["opened_path_verified"] = (
                identidad.get("path_match") is True
                and confianza == desktop_identity.HIGH)
            documento = str(destino) if identidad.get("path_match") is True else None
        else:
            identidad = desktop_identity.identify(abierto.instance, target=destino)
            if evidencia == "window_title" and identidad.get("path_match") is None:
                identidad["path_match"] = True
                identidad["identity_confidence"] = desktop_identity.MEDIUM
            salida["reopened"] = False
            salida["desktop_pid"] = abierto_ahora
            salida["identity"] = identidad
            # La confianza la fija la EVIDENCIA con la que se decidio el
            # seguimiento, no lo que diga una identificacion posterior.
            confianza = (desktop_identity.HIGH if evidencia == "open_file"
                         else desktop_identity.MEDIUM)
            # Solo un descriptor abierto DEMUESTRA la ruta. Un titulo igual
            # es una coincidencia de nombre, no una ruta.
            salida["opened_path_verified"] = evidencia == "open_file"
            documento = str(destino)
        salida["path_evidence"] = evidencia
        salida["opened_path_confidence"] = confianza

    # Referencia UTILIZABLE por `pbi_close_desktop`: PID + hora de arranque
    # identifican la ventana sin adivinar y protegen frente a un PID
    # reutilizado. `document` es lo que la ventana sirve segun la evidencia,
    # o null con los candidatos si no se pudo determinar.
    salida["desktop_session"] = {
        "desktop_pid": pid,
        "desktop_started": desktop_launcher._process_started(pid),  # noqa: SLF001
        "document": documento,
        "document_confidence": confianza if documento else None,
        "document_candidates": candidatos or None,
        "window_title": (salida.get("identity") or {}).get(
            "desktop_window_title"),
        "close_with": (f"pbi_close_desktop(desktop_pid={pid}, "
                       "desktop_started=<desktop_started>, confirm=true)"),
    }
    recordar = getattr(session, "recordar_exportacion", None)
    if recordar is not None and pid:
        recordar({**salida["desktop_session"], "source": str(origen) if origen else None,
                  "output": str(destino)})

    try:
        modelo = desktop_discovery.select_model(
            session, port=instancia.get("port"))
        salida["active_model"] = modelo.to_dict()
        salida["selected"] = True
    except Exception as exc:                              # noqa: BLE001
        salida["selected"] = False
        salida["select_error"] = str(getattr(exc, "message", exc))[:200]
    return salida


def export(session: Session, *, pbip_path: Optional[str] = None,
           out_path: Optional[str] = None, overwrite: bool = False,
           refresh: str = REFRESH_AUTO, leave_open: bool = True,
           timeout: int = 600, confirm_reuse: bool = False,
           adapter: Optional[Any] = None,
           format: str = FORMATO_PBIX) -> Dict[str, Any]:
    """PBIP -> PBIX (o PBIT) por `Guardar como`, verificado a los dos lados.

    Con `format='pbit'` se produce una PLANTILLA: el mismo `Guardar como`,
    eligiendo el tipo de plantilla, atendiendo el dialogo de descripcion que
    Desktop abre despues y verificando que el archivo tenga forma de plantilla
    -informe y definicion, sin modelo de datos-. Nunca se fabrica quitando
    partes de un `.pbix`, y nunca se afirma que lleve datos.
    """
    from horizun_pbi_mcp.powerbi import desktop_ui

    formato = normalizar_formato(format)
    modo = str(refresh or REFRESH_AUTO).casefold()
    if modo not in MODOS_REFRESH:
        raise ValidationError(
            f"refresh='{refresh}' no existe. Usa {', '.join(MODOS_REFRESH)}.",
            details={"parameter": "refresh", "valid": list(MODOS_REFRESH)})

    previo = preflight(session, pbip_path=pbip_path, out_path=out_path,
                       overwrite=overwrite, formato=formato)
    destino = previo.output_pbix
    adapter = adapter or desktop_ui.adaptador_por_defecto()
    inicio = time.time()
    vecinos_antes = _vecinos(destino.parent)

    salida: Dict[str, Any] = {
        "source_pbip": str(previo.source_pbip),
        "output_pbix": str(destino),
        "output_format": formato,
        "preflight": previo.to_dict(),
        "saved_as_verified": False,
        "opened_path_verified": False,
        "warnings": list(previo.warnings),
    }
    if formato == FORMATO_PBIT:
        salida["warnings"].append(
            "format='pbit': el resultado es una PLANTILLA. Lleva informe y "
            "definicion del modelo, no datos; quien la abra tendra que "
            "cargarlos.")

    abierto = _abrir_o_reutilizar(previo.source_pbip, timeout=timeout,
                                  confirm_reuse=confirm_reuse)
    salida["reused_open_session"] = not abierto.launched_by_us
    try:
        identidad = _identidad_verificada(abierto, previo.source_pbip)
        salida["identity"] = identidad

        refresco = _refrescar(session, modo, abierto.instance)
        salida.update(refresco)
        salida["warnings"].extend(refresco.get("refresh_warnings") or [])

        guardado = _guardar_como(
            adapter, pid=identidad["desktop_pid"],
            started=identidad.get("desktop_process_started"),
            destino=destino, timeout=float(timeout),
            origen=previo.source_pbip, formato=formato, desde=inicio,
            overwrite=overwrite)
        salida.update(guardado)

        verificacion = verificar_salida(
            destino, desde=inicio, formato=formato,
            espera_modelo=bool((previo.validation or {}).get("tmdl_valid")
                               is not None))
        salida.update({k: v for k, v in verificacion.items()
                       if k != "checks"})
        salida["verification"] = verificacion["checks"]
        salida["saved_as_verified"] = True

        tocados = _colaterales(vecinos_antes, destino.parent, destino)
        salida["collateral_files_touched"] = tocados
        if tocados:
            salida["warnings"].append(
                f"El guardado dejo cambios en {len(tocados)} archivo(s) mas "
                "de la carpeta de destino; revisa "
                "'collateral_files_touched'.")
    except BaseException as fallo:
        restauracion = _restaurar(previo)
        salida["restore"] = restauracion
        if previo.existia and not restauracion.get("restored"):
            raise PbixRestoreFailed(
                "La exportacion fallo y el destino que existia NO se pudo "
                "restaurar. Requiere intervencion: la copia original esta en "
                "el respaldo.",
                details={**salida,
                         "cause": str(getattr(fallo, "message", fallo))[:300]}
            ) from fallo
        if isinstance(fallo, PowerBIMCPError):
            # El error original conserva su codigo y su mensaje -es el que
            # explica QUE fallo- pero se le anade que paso con el destino.
            # Sin esto, quien recibe el fallo no sabe si su entregable
            # anterior sigue ahi, que es lo primero que va a preguntar.
            fallo.details = {**(fallo.details or {}),
                             "restore": restauracion,
                             "output_pbix": str(destino),
                             "source_pbip": str(previo.source_pbip),
                             "target_existed": previo.existia}
        raise

    salida["final_state"] = _estado_final(
        session, abierto=abierto, destino=destino, leave_open=leave_open,
        timeout=timeout, formato=formato, origen=previo.source_pbip)
    salida["opened_path_verified"] = bool(
        salida["final_state"].get("opened_path_verified"))
    if salida["final_state"].get("desktop_session"):
        salida["desktop_session"] = salida["final_state"]["desktop_session"]
    if salida["final_state"].get("follow_note"):
        salida["warnings"].append(salida["final_state"]["follow_note"])
    if formato == FORMATO_PBIT and leave_open:
        salida["warnings"].append(
            "La ventana sigue sirviendo el proyecto de origen: guardar una "
            "plantilla no la reapunta al .pbit, y el .pbit no queda abierto.")
    log.info("Exportado %s -> %s (%s bytes)", previo.source_pbip.name,
             destino.name, salida.get("output_size"))
    return salida


def finalize_delivery(session: Session, *, path: Optional[str] = None,
                      format: str = "pbix", out_path: Optional[str] = None,
                      refresh: str = REFRESH_AUTO, overwrite: bool = False,
                      leave_open: bool = True,
                      adapter: Optional[Any] = None) -> Dict[str, Any]:
    """De un archivo cualquiera al entregable, en una sola llamada.

    Resuelve el archivo exacto, convierte si le das un `.pbix`, valida,
    exporta por Desktop, inspecciona el resultado y deja abierto justo el
    entregable.
    """
    from horizun_pbi_mcp.services import project_prepare

    formato = normalizar_formato(format)
    salida: Dict[str, Any] = {"format": formato}
    if path:
        preparado = project_prepare.prepare(session, path)
        salida["prepare"] = preparado
        pbip = preparado["active_project"]
    else:
        activo = getattr(session, "active_pbip", None)
        if activo is None:
            raise ValidationError(
                "No hay proyecto activo y no se indico `path`.",
                details={"parameter": "path"})
        pbip = activo.pbip_path
        salida["prepare"] = {"skipped": True,
                             "reason": "se uso el proyecto activo"}

    exportado = export(session, pbip_path=pbip, out_path=out_path,
                       overwrite=overwrite, refresh=refresh,
                       leave_open=leave_open, adapter=adapter,
                       format=formato)
    salida.update(exportado)
    salida["delivered"] = bool(exportado.get("saved_as_verified"))
    return salida
