"""Serializacion del modelo en vivo a TMDL (la mitad `.SemanticModel` del .pbip).

El stream `DataModel` de un .pbix es un backup ABF comprimido con XPress9: no se
puede leer sin el motor de Analysis Services. Pero cuando Power BI Desktop tiene
el informe abierto, ese mismo modelo esta servido por un `msmdsrv` local, y TOM
puede escribirlo en TMDL con el serializador oficial de Microsoft
(`TmdlSerializer.SerializeDatabaseToFolder`), que es exactamente el que usa
Desktop al hacer "Guardar como .pbip".

Por eso la conversion del modelo pasa siempre por Desktop: es la unica forma
de obtener TMDL identico al oficial sin reimplementar el formato.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict

from config import ActiveModel
from logging_config import get_logger
from powerbi.clr_bootstrap import load_tom
from powerbi.errors import PowerBIMCPError
from powerbi.model_reader import connect

log = get_logger("tmdl_export")

#: Tablas que Power BI genera solo para la jerarquia automatica de fechas.
_TABLAS_AUTO_FECHA = ("LocalDateTable_", "DateTableTemplate_")


class TmdlExportError(PowerBIMCPError):
    code = "tmdl_export_failed"


def _mensaje_net(exc: Exception) -> str:
    return getattr(exc, "Message", None) or str(exc)


def _preparar_destino(destino: Path, overwrite: bool) -> None:
    if destino.exists():
        if any(destino.iterdir()):
            if not overwrite:
                raise TmdlExportError(
                    f"La carpeta de destino ya tiene contenido: {destino}. "
                    "Usa overwrite=true para reemplazarla.",
                    details={"path": str(destino)},
                )
            shutil.rmtree(destino)
        else:
            return
    destino.parent.mkdir(parents=True, exist_ok=True)


def _mover_arbol(origen: Path, destino: Path) -> None:
    """Traslada lo serializado al destino final, conservando la estructura.

    Los nombres de los TMDL salen de los nombres de tabla, asi que solo aqui se
    sabe cuanto miden. Si alguno se pasa del limite que impone Power BI Desktop
    a los .pbip, se aborta antes de mover: mejor no dejar nada que dejar un
    proyecto que Desktop se niegue a abrir.
    """
    from utils.file_utils import rutas_demasiado_largas

    relativas = [p.relative_to(origen).as_posix()
                 for p in origen.rglob("*") if p.is_file()]
    problemas = rutas_demasiado_largas(destino, relativas)
    if problemas:
        peor = max(problemas, key=lambda p: p["length"])
        raise TmdlExportError(
            f"El modelo no cabe en la ruta de destino: '{peor['path']}' mide "
            f"{peor['length']} caracteres y Power BI Desktop no abre un .pbip "
            f"con rutas de {peor['limit']} o mas. Usa un 'out_dir' mas corto.",
            details={"too_long": problemas[:10]},
        )
    destino.mkdir(parents=True, exist_ok=True)
    for archivo in sorted(origen.rglob("*")):
        if not archivo.is_file():
            continue
        final = destino / archivo.relative_to(origen)
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(archivo), str(final))


def export_to_tmdl(model: ActiveModel, destino: Path,
                   overwrite: bool = False) -> Dict[str, Any]:
    """Escribe el modelo activo como carpeta TMDL en `destino`.

    `destino` es la carpeta `definition/` del `.SemanticModel`. El serializador
    crea dentro `database.tmdl`, `model.tmdl`, `tables/`, `relationships.tmdl`
    y lo que el modelo tenga (perspectivas, roles, culturas).
    """
    TOM = load_tom()
    destino = Path(destino)
    _preparar_destino(destino, overwrite)

    # El serializador corre sobre .NET Framework, que rechaza cualquier ruta de
    # mas de 260 caracteres (ni siquiera admite el prefijo extendido \\?\).
    # Como el destino puede estar hondo, se escribe en un temporal corto y
    # despues se mueve con Python, que si maneja rutas largas. De paso, si algo
    # falla a mitad, el destino no queda con medio modelo escrito.
    temporal = Path(tempfile.mkdtemp(prefix="hpm-tmdl-"))
    intermedio = temporal / "d"
    try:
        with connect(model) as (_server, db, mdl):
            nombres_tabla = [t.Name for t in mdl.Tables]
            medidas = sum(t.Measures.Count for t in mdl.Tables)
            try:
                TOM.TmdlSerializer.SerializeDatabaseToFolder(db, str(intermedio))
            except Exception as exc:  # noqa: BLE001
                raise TmdlExportError(
                    f"TOM no pudo serializar el modelo a TMDL: {_mensaje_net(exc)}",
                    details={"path": str(destino), "database": db.Name},
                ) from exc
            nombre_bd = db.Name
            nivel = getattr(db, "CompatibilityLevel", None)
        _mover_arbol(intermedio, destino)
    finally:
        shutil.rmtree(temporal, ignore_errors=True)

    archivos = sorted(p.relative_to(destino).as_posix()
                      for p in destino.rglob("*") if p.is_file())
    if not archivos:
        raise TmdlExportError(
            "El serializador termino sin escribir ningun archivo TMDL.",
            details={"path": str(destino)},
        )

    usuario = [n for n in nombres_tabla if not n.startswith(_TABLAS_AUTO_FECHA)]
    auto = len(nombres_tabla) - len(usuario)
    if not nombres_tabla:
        # Red de seguridad: si se leyo el modelo antes de que terminara de
        # cargar, el TMDL saldria sin tablas y el .pbip pareceria correcto.
        raise TmdlExportError(
            "El modelo servido por Power BI Desktop no tenia ninguna tabla; se "
            "leyo antes de que terminara de cargar. No se escribe un modelo "
            "vacio: vuelve a intentarlo.",
            details={"path": str(destino), "database": nombre_bd},
        )
    log.info("TMDL exportado a %s (%s archivos, %s tablas)",
             destino, len(archivos), len(usuario))
    return {
        "path": str(destino),
        "database_name": nombre_bd,
        "compatibility_level": nivel,
        "files": archivos,
        "file_count": len(archivos),
        "table_count": len(usuario),
        "auto_date_tables": auto,
        "measure_count": medidas,
        "tables": usuario,
    }


def rename_database(definition_dir: Path, nuevo_nombre: str) -> bool:
    """Ajusta el nombre declarado en `database.tmdl` al del proyecto .pbip.

    El nombre que sirve Desktop es el del espacio de trabajo temporal (un GUID),
    no el del informe. Se corrige en NUESTRA salida; el modelo en memoria del
    usuario no se toca.
    """
    archivo = Path(definition_dir) / "database.tmdl"
    if not archivo.exists():
        return False
    lineas = archivo.read_text(encoding="utf-8").splitlines(keepends=True)
    for i, linea in enumerate(lineas):
        if linea.lstrip().startswith("database "):
            fin = "\r\n" if linea.endswith("\r\n") else "\n" if linea.endswith("\n") else ""
            lineas[i] = f"database {_citar_tmdl(nuevo_nombre)}{fin}"
            archivo.write_text("".join(lineas), encoding="utf-8")
            return True
    return False


def _citar_tmdl(nombre: str) -> str:
    """TMDL exige comillas cuando el nombre lleva espacios o caracteres raros."""
    if nombre and all(c.isalnum() or c in "_-" for c in nombre):
        return nombre
    return "'" + nombre.replace("'", "''") + "'"
