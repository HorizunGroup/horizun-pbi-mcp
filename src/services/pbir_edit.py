"""Edicion PBIR: CRUD de visuales y paginas, y layout.

Toda mutacion pasa por el mismo camino: politica estricta de Desktop -> validar
TODO en memoria -> una sola transaccion -> verificacion -> rollback si falla.

Principio que no se rompe: NO se inventan estructuras de visual. Se clona una
existente y se le cambian los campos conocidos. Si una variante no se reconoce,
se devuelve `unsupported` con el motivo, en vez de escribir algo que Power BI
podria rechazar al abrir.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import ActivePbip
from logging_config import get_logger
from powerbi.errors import PowerBIMCPError, ValidationError
from pbip import pbir_reader, pbir_writer
from services import model_explorer
from services import paths as safe_paths
from services import project_state
from services import txn as txn_service
from utils.json_utils import read_json

log = get_logger("pbir_edit")


class UnsupportedPbirFeature(PowerBIMCPError):
    """La estructura encontrada no se reconoce y no se va a adivinar."""

    code = "pbir_feature_unsupported"


# ------------------------------------------------------------------ lectura ---
def _pages_dir(active: ActivePbip) -> Path:
    return pbir_reader.pages_dir(active)


def _visual_dir(active: ActivePbip, page: str, visual_id: str) -> Path:
    page_dir = pbir_reader.resolve_page_dir(active, page)
    safe_paths.safe_identifier(visual_id, kind="id de visual")
    return safe_paths.safe_join(page_dir, "visuals", visual_id,
                                kind="carpeta de visual")


def _visual_file(active: ActivePbip, page: str, visual_id: str) -> Path:
    return _visual_dir(active, page, visual_id) / "visual.json"


def get_visual(active: ActivePbip, page: str, visual_id: str) -> Dict[str, Any]:
    """Definicion normalizada y completa de un visual."""
    ruta = _visual_file(active, page, visual_id)
    if not ruta.exists():
        raise ValidationError(
            f"No existe el visual '{visual_id}' en la pagina '{page}'.",
            details={"page": page, "visual_id": visual_id})
    crudo = read_json(ruta)
    nodo = crudo.get("visual", {})
    normalizado = pbir_reader.read_visual_file(ruta)
    return {
        **normalizado,
        "z_order": crudo.get("position", {}).get("z"),
        "tab_order": crudo.get("position", {}).get("tabOrder"),
        "has_format": bool(nodo.get("objects")),
        "has_container_format": bool(nodo.get("visualContainerObjects")),
        "filters": crudo.get("filterConfig"),
        "raw": crudo,
    }


class PbirVersionUnsupported(PowerBIMCPError):
    """El informe declara una version de PBIR que este servidor no sabe editar."""

    code = "pbir_version_unsupported"


#: Versiones de PBIR cuyo formato conocemos y sabemos reescribir.
VERSIONES_SOPORTADAS = ("4.0",)


def leer_version_pbir(active: ActivePbip) -> Optional[str]:
    """Version declarada en `definition.pbir`, o None si no hay ninguna."""
    pbir_file = Path(active.report_dir) / "definition.pbir"
    if not pbir_file.exists():
        return None
    try:
        return read_json(pbir_file).get("version")
    except ValidationError:
        return None


def assert_pbir_soportado(active: ActivePbip, operation: str) -> str:
    """Bloquea la escritura si la version de PBIR no es una que conozcamos.

    Antes esto era solo informativo: `report_capabilities` calculaba
    `supported_version` y NADIE lo miraba, asi que el servidor reescribia
    igualmente un informe de un formato que no entiende. Escribir con la
    estructura equivocada corrompe el .pbip de una forma que Power BI no
    siempre sabe explicar.

    Fail-closed y con regla explicita para el caso sin version: si no se puede
    determinar, se bloquea. Un informe sin `definition.pbir` legible no es un
    PBIR que sepamos editar, aunque tenga carpetas parecidas.
    """
    version = leer_version_pbir(active)
    if version in VERSIONES_SOPORTADAS:
        return version

    if version is None:
        raise PbirVersionUnsupported(
            f"{operation}: no se pudo determinar la version de PBIR "
            "(definition.pbir ausente, ilegible o sin campo 'version'). No se "
            "escribe sobre un formato que no se puede identificar.",
            details={"pbir_version": None, "operation": operation,
                     "supported": list(VERSIONES_SOPORTADAS),
                     "rule": "sin_version_se_bloquea"})

    raise PbirVersionUnsupported(
        f"{operation}: el informe declara PBIR '{version}' y este servidor solo "
        f"sabe editar {', '.join(VERSIONES_SOPORTADAS)}. Actualiza el servidor o "
        "edita el informe en Power BI Desktop.",
        details={"pbir_version": version, "operation": operation,
                 "supported": list(VERSIONES_SOPORTADAS),
                 "rule": "version_desconocida_se_bloquea"})


def assert_escritura_pbir(active: ActivePbip, operation: str) -> None:
    """Puerta unica de toda escritura PBIR: formato soportado + Desktop cerrado.

    El orden importa: primero el formato, porque si no sabemos escribirlo da
    igual que Desktop este cerrado.
    """
    assert_pbir_soportado(active, operation)
    project_state.assert_writable(active, operation=operation)


def report_capabilities(active: ActivePbip) -> Dict[str, Any]:
    """Version PBIR observada, tipos presentes y plantillas clonables."""
    definition = pbir_reader.report_definition_dir(active)
    pbir_file = Path(active.report_dir) / "definition.pbir"
    version = None
    if pbir_file.exists():
        try:
            version = read_json(pbir_file).get("version")
        except ValidationError:
            version = None

    report_json = definition / "report.json"
    custom: List[str] = []
    tema = None
    if report_json.exists():
        try:
            datos = read_json(report_json)
            custom = datos.get("publicCustomVisuals") or []
            tema = (datos.get("themeCollection") or {}).get("baseTheme", {}).get("name")
        except ValidationError:
            pass

    tipos: Dict[str, Dict[str, Any]] = {}
    for pagina in pbir_reader.list_pages(active):
        for v in pbir_reader.list_visuals(active, pagina["name"]):
            t = v.get("type") or "?"
            entrada = tipos.setdefault(t, {"count": 0, "template": None})
            entrada["count"] += 1
            if entrada["template"] is None:
                entrada["template"] = {"page": pagina["name"], "visual_id": v["id"]}

    return {
        "pbir_version": version,
        # Coherente con assert_pbir_soportado: sin version NO es soportado.
        # Antes decia que si, contradiciendo al guard que ahora bloquea.
        "supported_version": version in VERSIONES_SOPORTADAS,
        "supported_versions": list(VERSIONES_SOPORTADAS),
        "writable": version in VERSIONES_SOPORTADAS,
        "theme": tema,
        "public_custom_visuals": custom,
        "visual_types_present": tipos,
        "clonable_types": sorted(tipos),
        "note": ("Solo se pueden crear visuales de tipos ya presentes en el "
                 "informe: se clona una estructura real en vez de inventarla."),
    }


# --------------------------------------------------------- CRUD de visuales ---
def duplicate_visual(active: ActivePbip, page: str, visual_id: str, *,
                     target_page: Optional[str] = None,
                     offset: Tuple[float, float] = (24, 24),
                     new_title: Optional[str] = None) -> Dict[str, Any]:
    """Duplica un visual, con un id nuevo y desplazado para que se vea.

    Se conserva TODO el contenido (campos, formato, filtros) y solo se regenera
    lo que debe ser unico: el `name` del visual.
    """
    origen = _visual_file(active, page, visual_id)
    if not origen.exists():
        raise ValidationError(f"No existe el visual '{visual_id}' en '{page}'.")

    datos = copy.deepcopy(read_json(origen))
    destino_pagina = target_page or page
    nuevo_id = pbir_writer.new_id()
    datos["name"] = nuevo_id

    pos = dict(datos.get("position", {}))
    pos["x"] = float(pos.get("x", 0)) + offset[0]
    pos["y"] = float(pos.get("y", 0)) + offset[1]
    datos["position"] = pos

    if new_title is not None:
        _fijar_titulo(datos, new_title)

    destino = _visual_file(active, destino_pagina, nuevo_id)
    assert_escritura_pbir(active, operation="Duplicar un visual")
    cm = txn_service.project_transaction(active, [destino],
                                         tool="pbi_duplicate_visual")
    with cm as t:
        t.write_json(destino, datos)
    return {"source_visual_id": visual_id, "visual_id": nuevo_id,
            "page": destino_pagina, "file": str(destino),
            "position": pos, "backup": cm.result["journal"],
            "transaction": cm.result}


def delete_visual(active: ActivePbip, page: str, visual_id: str,
                  confirm: bool = False) -> Dict[str, Any]:
    """Elimina un visual. Destructiva: exige confirm=true."""
    if not confirm:
        raise ValidationError(
            "Operacion destructiva: pasa confirm=true para eliminar el visual.")
    ruta = _visual_file(active, page, visual_id)
    if not ruta.exists():
        raise ValidationError(f"No existe el visual '{visual_id}' en '{page}'.")
    antes = pbir_reader.read_visual_file(ruta)

    assert_escritura_pbir(active, operation="Eliminar un visual")
    cm = txn_service.project_transaction(active, [ruta], tool="pbi_delete_visual")
    with cm as t:
        t.delete(ruta)
        # DENTRO de la transaccion: una carpeta de visual sin visual.json es un
        # informe invalido (`PBIR_VISUAL_DIR_WITHOUT_JSON` para el validador
        # oficial de Microsoft). Limpiarla despues del commit dejaba una
        # ventana en la que el informe estaba roto; si el proceso moria ahi, el
        # usuario se quedaba con esa carpeta huerfana.
        carpeta = ruta.parent
        try:
            if carpeta.exists() and not any(carpeta.iterdir()):
                carpeta.rmdir()
        except OSError:                               # pragma: no cover
            pass
    return {"deleted": visual_id, "page": page, "before": antes,
            "backup": cm.result["journal"], "transaction": cm.result}


def _fijar_titulo(datos: Dict[str, Any], titulo: str) -> None:
    """Cambia el TEXTO del titulo conservando su formato."""
    valor = "'" + str(titulo).replace("'", "''") + "'"
    vis = datos.setdefault("visual", {})
    vco = vis.setdefault("visualContainerObjects", {})
    arr = vco.get("title")
    if isinstance(arr, list) and arr and isinstance(arr[0], dict):
        arr[0].setdefault("properties", {})["text"] = {
            "expr": {"Literal": {"Value": valor}}}
    else:
        vco["title"] = [{"properties": {
            "text": {"expr": {"Literal": {"Value": valor}}}}}]


def set_visual_title(active: ActivePbip, page: str, visual_id: str,
                     title: str) -> Dict[str, Any]:
    """Cambia el titulo de un visual, preservando su formato."""
    plan = plan_set_visual_title(active, page, visual_id, title)

    assert_escritura_pbir(active, operation="Cambiar el titulo de un visual")
    cm = txn_service.project_transaction(
        active, [plan["path"]], tool="pbi_set_visual_title")
    with cm as t:
        t.write_json(plan["path"], plan["data"])
    return {"visual_id": visual_id, "page": page, "before": plan["before"],
            "after": title, "backup": cm.result["journal"],
            "transaction": cm.result}


def plan_set_visual_title(active: ActivePbip, page: str, visual_id: str,
                          title: str) -> Dict[str, Any]:
    """Calcula el visual final sin escribir ni abrir una transaccion."""
    ruta = _visual_file(active, page, visual_id)
    if not ruta.exists():
        raise ValidationError(f"No existe el visual '{visual_id}' en '{page}'.")
    datos = read_json(ruta)
    antes = pbir_reader.read_visual_file(ruta).get("title")
    _fijar_titulo(datos, title)
    return {"visual_id": visual_id, "page": page, "path": ruta,
            "data": datos, "before": antes, "after": title}


def set_conditional_format(active: ActivePbip, page: str, visual_id: str,
                           field_ref: str, min_color: str, max_color: str, *,
                           target: str = "background",
                           mid_color: Optional[str] = None,
                           null_strategy: str = "asZero",
                           measure_index: Optional[Dict[str, str]] = None
                           ) -> Dict[str, Any]:
    """Pinta un visual segun el valor de un campo (degradado).

    `field_ref` se resuelve igual que en el resto del servidor ('Tabla[Campo]'),
    de modo que la referencia del color y la de la consulta son la misma cosa.
    """
    from pbip import conditional_format, theme, visual_factory

    ruta = _visual_file(active, page, visual_id)
    if not ruta.exists():
        raise ValidationError(f"No existe el visual '{visual_id}' en '{page}'.")
    datos = read_json(ruta)

    avisos: List[str] = []
    nodo = visual_factory._field_node(                       # noqa: SLF001
        field_ref, visual_factory._infer_kind(field_ref, measure_index),  # noqa: SLF001
        measure_index, avisos)["field"]

    detalle = conditional_format.apply_to_visual(
        datos, nodo, min_color, max_color, target=target,
        mid_color=mid_color, null_strategy=null_strategy)
    avisos.extend(conditional_format.contrast_warnings(
        min_color, max_color, target=target,
        theme_data=theme.current_theme(active)))

    assert_escritura_pbir(active, operation="Aplicar formato condicional")
    cm = txn_service.project_transaction(active, [ruta],
                                         tool="pbi_set_conditional_format")
    with cm as t:
        t.write_json(ruta, datos)
    return {"visual_id": visual_id, "page": page, "field": field_ref,
            **detalle, "warnings": avisos,
            "backup": cm.result["journal"], "transaction": cm.result}


def set_visual_z_order(active: ActivePbip, page: str,
                       order: List[str]) -> Dict[str, Any]:
    """Fija el orden Z de los visuales de una pagina.

    `order`: ids de MENOR a MAYOR z (el ultimo queda encima). Los visuales no
    mencionados conservan su z relativo, por encima de los ordenados.
    """
    visuales = pbir_reader.list_visuals(active, page)
    existentes = {v["id"] for v in visuales}
    desconocidos = [v for v in order if v not in existentes]
    if desconocidos:
        raise ValidationError(
            f"Estos visuales no existen en '{page}': {desconocidos}",
            details={"available": sorted(existentes)})

    nuevo_z = {vid: i for i, vid in enumerate(order)}
    siguiente = len(order)
    for v in visuales:
        if v["id"] not in nuevo_z:
            nuevo_z[v["id"]] = siguiente
            siguiente += 1

    objetivos, contenidos = [], {}
    for v in visuales:
        ruta = _visual_file(active, page, v["id"])
        datos = read_json(ruta)
        pos = dict(datos.get("position", {}))
        if pos.get("z") == nuevo_z[v["id"]]:
            continue
        pos["z"] = nuevo_z[v["id"]]
        pos["tabOrder"] = nuevo_z[v["id"]]
        datos["position"] = pos
        objetivos.append(ruta)
        contenidos[ruta] = datos

    if not objetivos:
        return {"page": page, "changed": 0, "z_order": nuevo_z}

    assert_escritura_pbir(active, operation="Reordenar el eje Z")
    cm = txn_service.project_transaction(active, objetivos,
                                         tool="pbi_set_visual_z_order")
    with cm as t:
        for ruta, datos in contenidos.items():
            t.write_json(ruta, datos)
    return {"page": page, "changed": len(objetivos), "z_order": nuevo_z,
            "backup": cm.result["journal"], "transaction": cm.result}


class FieldNotFoundError(PowerBIMCPError):
    """El campo destino no existe en el modelo, o es ambiguo."""

    code = "field_not_found"


def _validar_destino(new_ref: str, model_data: Optional[Dict[str, Any]],
                     ) -> Optional[Dict[str, Any]]:
    """Comprueba `new_ref` contra el indice del modelo. None si no hay modelo.

    Sin esto la funcion escribia CUALQUIER referencia: bastaba una errata para
    dejar el visual apuntando a un campo inexistente, que Power BI no dibuja y
    que solo se descubre al abrir el informe.

    Se comprueba tabla, existencia, ambigüedad y —lo que mas duele— si es
    columna o medida: son nodos PBIR distintos (`Column` vs `Measure`) y
    confundirlos produce un archivo estructuralmente invalido aunque el nombre
    exista.
    """
    if not model_data:
        return None

    indice = model_explorer.build_index(model_data)
    limpio = new_ref.strip()
    consulta = limpio.strip("[]") if limpio.startswith("[") else limpio
    r = model_explorer.resolve_reference(consulta, indice)

    if not r["exists"]:
        tabla = limpio.split("[", 1)[0].strip() if "[" in limpio else None
        if tabla and tabla not in indice["tables"]:
            raise FieldNotFoundError(
                f"La tabla '{tabla}' no existe en el modelo.",
                details={"new_ref": new_ref, "table": tabla,
                         "available_tables": sorted(indice["tables"])[:40]})
        raise FieldNotFoundError(
            f"'{new_ref}' no existe en el modelo. No se inventa un campo.",
            details={"new_ref": new_ref,
                     "hint": "Usa 'Tabla[Columna]' o '[Medida]'. Consulta los "
                             "campos con pbi_page_building_blocks."})

    if r.get("note") == "resuelta por nombre de columna unico":
        coincidencias = [c for c in indice["columns"] if c.endswith(f"[{consulta}]")]
        if len(coincidencias) > 1:
            raise FieldNotFoundError(
                f"'{new_ref}' es ambiguo: existe en {coincidencias}. "
                "Cualifica la referencia con su tabla.",
                details={"new_ref": new_ref, "matches": coincidencias})
    return r


def replace_visual_field(active: ActivePbip, page: str, visual_id: str,
                         old_ref: str, new_ref: str,
                         model_data: Optional[Dict[str, Any]] = None,
                         ) -> Dict[str, Any]:
    """Sustituye una referencia de campo dentro de un visual.

    Trabaja sobre la estructura de proyecciones existente: cambia `Entity` y
    `Property` de las que apunten a `old_ref`. No crea proyecciones nuevas.

    Si se pasa `model_data`, el destino se valida contra el modelo ANTES de
    escribir: tabla existente, campo existente, sin ambigüedad, y del tipo
    correcto (una medida no puede ocupar un nodo `Column`).
    """
    plan = plan_replace_visual_field(active, page, visual_id, old_ref, new_ref,
                                     model_data)
    ruta, datos = plan["path"], plan["data"]

    assert_escritura_pbir(active, operation="Reemplazar un campo")
    cm = txn_service.project_transaction(active, [ruta],
                                         tool="pbi_replace_visual_field")
    with cm as t:
        t.write_json(ruta, datos)
    return {"visual_id": visual_id, "page": page,
            "replacements": plan["replacements"], "count": plan["count"],
            "backup": cm.result["journal"], "transaction": cm.result}


def plan_replace_visual_field(active: ActivePbip, page: str, visual_id: str,
                              old_ref: str, new_ref: str,
                              model_data: Optional[Dict[str, Any]] = None,
                              ) -> Dict[str, Any]:
    """Calcula el visual.json resultante SIN escribir ni abrir transaccion.

    Lo necesita `repair_broken_references`, que sustituye campos en VARIOS
    visuales: antes llamaba a `replace_visual_field` dentro de un bucle, con
    una transaccion por visual, y ademas capturaba la excepcion para seguir.
    Si el quinto fallaba, los cuatro anteriores quedaban confirmados y la tool
    devolvia ok:true con una lista de fallidos.
    """
    destino = _validar_destino(new_ref, model_data)
    ruta = _visual_file(active, page, visual_id)
    if not ruta.exists():
        raise ValidationError(f"No existe el visual '{visual_id}' en '{page}'.")
    datos = read_json(ruta)

    def partir(ref: str) -> Tuple[Optional[str], str]:
        r = ref.strip()
        if "[" in r and r.endswith("]"):
            return (r[:r.index("[")].strip() or None, r[r.index("[") + 1:-1])
        return (None, r.strip("[]"))

    tabla_vieja, campo_viejo = partir(old_ref)
    tabla_nueva, campo_nuevo = partir(new_ref)
    if not campo_nuevo:
        raise ValidationError(f"Referencia destino invalida: '{new_ref}'.")

    sustituciones: List[Dict[str, str]] = []
    query = datos.get("visual", {}).get("query", {}).get("queryState", {})
    for rol, spec in query.items():
        for proy in spec.get("projections", []):
            campo = proy.get("field", {})
            for kind in ("Measure", "Column"):
                if kind not in campo:
                    continue
                nodo = campo[kind]
                entidad = nodo.get("Expression", {}).get("SourceRef", {}).get("Entity")
                prop = nodo.get("Property")
                if prop != campo_viejo:
                    continue
                if tabla_vieja and entidad != tabla_vieja:
                    continue

                # H6: el nodo PBIR distingue Column de Measure. Escribir una
                # medida dentro de un nodo `Column` (o al reves) solo porque el
                # nombre encaja deja un visual que Power BI no sabe resolver.
                if destino is not None:
                    esperado = "Measure" if destino["kind"] == "measure" else "Column"
                    if esperado != kind:
                        raise FieldNotFoundError(
                            f"'{new_ref}' es una {destino['kind']} y la proyeccion "
                            f"'{rol}' del visual usa un nodo '{kind}'. Cambiar solo "
                            "el nombre dejaria el visual con una referencia que "
                            "Power BI no puede resolver.",
                            details={"new_ref": new_ref, "role": rol,
                                     "node_kind": kind,
                                     "field_kind": destino["kind"]})

                nodo["Property"] = campo_nuevo
                if tabla_nueva:
                    nodo.setdefault("Expression", {}).setdefault(
                        "SourceRef", {})["Entity"] = tabla_nueva
                nueva_entidad = tabla_nueva or entidad
                proy["queryRef"] = f"{nueva_entidad}.{campo_nuevo}" if nueva_entidad \
                    else campo_nuevo
                proy["nativeQueryRef"] = campo_nuevo
                sustituciones.append({"role": rol, "from": old_ref, "to": new_ref})

    if not sustituciones:
        raise ValidationError(
            f"El visual '{visual_id}' no referencia '{old_ref}'.",
            details={"page": page, "visual_id": visual_id})

    return {"path": ruta, "data": datos, "replacements": sustituciones,
            "count": len(sustituciones), "page": page, "visual_id": visual_id}


def copy_visual_format(active: ActivePbip, source_page: str, source_visual: str,
                       target_page: str, target_visuals: List[str]) -> Dict[str, Any]:
    """Copia el formato de un visual a otros del MISMO tipo.

    Se copian `objects` y `visualContainerObjects` (menos el texto del titulo,
    que es contenido, no formato). Copiar formato entre tipos distintos produce
    estructuras que Power BI puede rechazar, asi que se rechaza explicitamente.
    """
    origen = _visual_file(active, source_page, source_visual)
    if not origen.exists():
        raise ValidationError(
            f"No existe el visual origen '{source_visual}' en '{source_page}'.")
    datos_origen = read_json(origen)
    tipo_origen = datos_origen.get("visual", {}).get("visualType")

    plan: Dict[Path, Dict[str, Any]] = {}
    incompatibles: List[Dict[str, str]] = []
    for vid in target_visuals:
        ruta = _visual_file(active, target_page, vid)
        if not ruta.exists():
            raise ValidationError(f"No existe el visual '{vid}' en '{target_page}'.")
        datos = read_json(ruta)
        tipo = datos.get("visual", {}).get("visualType")
        if tipo != tipo_origen:
            incompatibles.append({"visual_id": vid, "type": tipo,
                                  "expected": tipo_origen})
            continue

        vis = datos.setdefault("visual", {})
        if datos_origen.get("visual", {}).get("objects") is not None:
            vis["objects"] = copy.deepcopy(datos_origen["visual"]["objects"])
        vco_origen = copy.deepcopy(
            datos_origen.get("visual", {}).get("visualContainerObjects") or {})
        # El TEXTO del titulo es contenido del visual destino, no formato.
        titulo_actual = (vis.get("visualContainerObjects", {})
                         .get("title", [{}])[0].get("properties", {}).get("text"))
        if "title" in vco_origen and titulo_actual is not None:
            vco_origen["title"][0].setdefault("properties", {})["text"] = titulo_actual
        if vco_origen:
            vis["visualContainerObjects"] = vco_origen
        plan[ruta] = datos

    if incompatibles:
        raise UnsupportedPbirFeature(
            "No se copia formato entre tipos de visual distintos: la estructura "
            "de formato no es intercambiable y Power BI podria rechazarla.",
            details={"source_type": tipo_origen, "incompatible": incompatibles})

    if not plan:
        return {"copied_to": [], "count": 0}

    assert_escritura_pbir(active, operation="Copiar formato")
    cm = txn_service.project_transaction(active, list(plan),
                                         tool="pbi_copy_visual_format")
    with cm as t:
        for ruta, datos in plan.items():
            t.write_json(ruta, datos)
    return {"source": {"page": source_page, "visual_id": source_visual,
                       "type": tipo_origen},
            "copied_to": target_visuals, "count": len(plan),
            "backup": cm.result["journal"], "transaction": cm.result}


# ---------------------------------------------------------- CRUD de paginas ---
def _leer_pages_json(active: ActivePbip) -> Tuple[Path, Dict[str, Any]]:
    ruta = safe_paths.safe_join(_pages_dir(active), "pages.json", kind="pages.json")
    datos = read_json(ruta) if ruta.exists() else {
        "$schema": pbir_writer.SCHEMA_PAGES, "pageOrder": [], "activePageName": None}
    return ruta, datos


def duplicate_page(active: ActivePbip, page: str,
                   new_name: str) -> Dict[str, Any]:
    """Duplica una pagina completa con todos sus visuales.

    Se regeneran los identificadores que deben ser unicos —el de la pagina y el
    de cada visual— y se conserva todo lo demas.
    """
    origen_dir = pbir_reader.resolve_page_dir(active, page)
    page_json_origen = origen_dir / "page.json"
    if not page_json_origen.exists():
        raise ValidationError(f"La pagina '{page}' no tiene page.json.")

    if pbir_writer._existing_page_id(active, new_name) is not None:  # noqa: SLF001
        raise ValidationError(f"Ya existe una pagina llamada '{new_name}'.")

    from services import page_clone

    nuevo_page_id = pbir_writer.new_id()
    pdir = _pages_dir(active)
    destino_page_json = safe_paths.safe_join(pdir, nuevo_page_id, "page.json",
                                             kind="ruta de pagina")

    # Mapa completo old_id -> new_id ANTES de tocar ningun documento: hay que
    # conocerlo entero para poder remapear referencias cruzadas entre visuales.
    visuales = pbir_reader.list_visuals(active, page)
    mapa, _ = page_clone.construir_mapa(visuales, nuevo_page_id)
    mapa_pagina = {origen_dir.name: nuevo_page_id}

    sin_remapear: List[Dict[str, str]] = []

    datos_pagina, pendientes = page_clone.remapear_documento(
        read_json(page_json_origen), mapa, mapa_pagina, "page.json")
    sin_remapear.extend(pendientes)
    datos_pagina["name"] = nuevo_page_id
    datos_pagina["displayName"] = new_name

    escrituras: Dict[Path, Dict[str, Any]] = {destino_page_json: datos_pagina}
    copiados: List[Dict[str, str]] = []
    for v in visuales:
        datos_visual, pendientes = page_clone.remapear_documento(
            read_json(Path(v["file"])), mapa, mapa_pagina,
            f"visuals/{v['id']}/visual.json")
        sin_remapear.extend(pendientes)
        nuevo_vid = mapa[v["id"]]
        datos_visual["name"] = nuevo_vid
        destino = safe_paths.safe_join(pdir, nuevo_page_id, "visuals", nuevo_vid,
                                       "visual.json", kind="ruta de visual")
        escrituras[destino] = datos_visual
        copiados.append({"source": v["id"], "new": nuevo_vid})

    # Si queda una sola referencia que no sabemos remapear, no se duplica: la
    # copia apuntaria a la pagina original y el destrozo solo se veria al abrir
    # el informe.
    page_clone.assert_remapeable(sin_remapear, pagina=page)

    # Red de seguridad: en los documentos COPIADOS no puede quedar ningun id
    # viejo, y los nuevos tienen que ser unicos. Se comprueba antes de anadir
    # pages.json, que es el indice del informe y contiene legitimamente el id
    # de la pagina original porque esta sigue existiendo.
    verificacion = page_clone.verificar_copia(escrituras, mapa, mapa_pagina)

    pages_json_path, meta = _leer_pages_json(active)
    meta.setdefault("pageOrder", [])
    if nuevo_page_id not in meta["pageOrder"]:
        meta["pageOrder"].append(nuevo_page_id)
    escrituras[pages_json_path] = meta

    if not verificacion["clean"]:
        raise page_clone.UnsupportedPageStructure(
            "La copia habria conservado identificadores de la pagina original.",
            details={"page": page, **verificacion})

    assert_escritura_pbir(active, operation="Duplicar una pagina")
    cm = txn_service.project_transaction(active, list(escrituras),
                                         tool="pbi_duplicate_page")
    with cm as t:
        for ruta, datos in escrituras.items():
            t.write_json(ruta, datos)
    (pdir / nuevo_page_id / "visuals").mkdir(parents=True, exist_ok=True)

    return {"source_page": page, "page_id": nuevo_page_id, "display_name": new_name,
            "visuals_copied": copiados, "count": len(copiados),
            "id_map": mapa, "page_id_map": mapa_pagina,
            "reference_check": verificacion,
            "backup": cm.result["journal"], "transaction": cm.result}


def delete_page(active: ActivePbip, page: str,
                confirm: bool = False) -> Dict[str, Any]:
    """Elimina una pagina y actualiza el orden y la pagina activa.

    Destructiva: exige confirm=true. Si era la pagina activa, la activa pasa a
    ser la primera que quede; si era la unica, se rechaza.
    """
    if not confirm:
        raise ValidationError(
            "Operacion destructiva: pasa confirm=true para eliminar la pagina.")
    page_dir = pbir_reader.resolve_page_dir(active, page)
    page_id = page_dir.name

    paginas = pbir_reader.list_pages(active)
    if len(paginas) <= 1:
        raise ValidationError(
            "No se elimina la unica pagina del informe: un informe sin paginas "
            "no abre en Power BI Desktop.")

    archivos = [p for p in page_dir.rglob("*") if p.is_file()]
    pages_json_path, meta = _leer_pages_json(active)
    meta["pageOrder"] = [p for p in meta.get("pageOrder", []) if p != page_id]
    activa_antes = meta.get("activePageName")
    if activa_antes == page_id:
        meta["activePageName"] = meta["pageOrder"][0] if meta["pageOrder"] else None

    objetivos = archivos + [pages_json_path]
    assert_escritura_pbir(active, operation="Eliminar una pagina")
    cm = txn_service.project_transaction(active, objetivos, tool="pbi_delete_page")
    with cm as t:
        for archivo in archivos:
            t.delete(archivo)
        t.write_json(pages_json_path, meta)
        # Dentro de la transaccion, por lo mismo que en delete_visual: una
        # carpeta de pagina vacia deja el informe en un estado que el validador
        # oficial rechaza.
        for d in sorted((p for p in page_dir.rglob("*") if p.is_dir()),
                        key=lambda p: len(p.parts), reverse=True):
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:                           # pragma: no cover
                pass
        try:
            if page_dir.exists() and not any(page_dir.iterdir()):
                page_dir.rmdir()
        except OSError:                               # pragma: no cover
            pass



    return {"deleted_page": page_id, "files_removed": len(archivos),
            "active_page_before": activa_antes,
            "active_page_after": meta.get("activePageName"),
            "page_order": meta["pageOrder"],
            "backup": cm.result["journal"], "transaction": cm.result}


def rename_page(active: ActivePbip, page: str, new_name: str) -> Dict[str, Any]:
    """Cambia el nombre visible de una pagina. El id interno no cambia."""
    if not new_name or not new_name.strip():
        raise ValidationError("El nuevo nombre no puede estar vacio.")
    safe_paths.assert_not_path_syntax(new_name, kind="nombre de pagina")

    page_dir = pbir_reader.resolve_page_dir(active, page)
    ruta = page_dir / "page.json"
    datos = read_json(ruta)
    antes = datos.get("displayName")
    if antes == new_name:
        return {"page_id": page_dir.name, "changed": False,
                "display_name": new_name}
    if pbir_writer._existing_page_id(active, new_name) is not None:  # noqa: SLF001
        raise ValidationError(f"Ya existe una pagina llamada '{new_name}'.")
    datos["displayName"] = new_name

    assert_escritura_pbir(active, operation="Renombrar una pagina")
    cm = txn_service.project_transaction(active, [ruta], tool="pbi_rename_page")
    with cm as t:
        t.write_json(ruta, datos)
    return {"page_id": page_dir.name, "changed": True, "before": antes,
            "display_name": new_name, "backup": cm.result["journal"],
            "transaction": cm.result}


def reorder_pages(active: ActivePbip, order: List[str]) -> Dict[str, Any]:
    """Fija el orden de las paginas. Acepta ids o nombres visibles."""
    paginas = pbir_reader.list_pages(active)
    por_id = {p["name"]: p["name"] for p in paginas}
    por_nombre = {str(p.get("display_name") or "").lower(): p["name"]
                  for p in paginas}

    resueltos: List[str] = []
    for entrada in order:
        pid = por_id.get(entrada) or por_nombre.get(str(entrada).lower())
        if pid is None:
            raise ValidationError(
                f"No existe la pagina '{entrada}'.",
                details={"available": [p["name"] for p in paginas]})
        if pid in resueltos:
            raise ValidationError(f"La pagina '{entrada}' aparece dos veces.")
        resueltos.append(pid)

    restantes = [p["name"] for p in paginas if p["name"] not in resueltos]
    nuevo_orden = resueltos + restantes

    pages_json_path, meta = _leer_pages_json(active)
    if meta.get("pageOrder") == nuevo_orden:
        return {"changed": False, "page_order": nuevo_orden}
    antes = list(meta.get("pageOrder", []))
    meta["pageOrder"] = nuevo_orden

    assert_escritura_pbir(active, operation="Reordenar paginas")
    cm = txn_service.project_transaction(active, [pages_json_path],
                                         tool="pbi_reorder_pages")
    with cm as t:
        t.write_json(pages_json_path, meta)
    return {"changed": True, "before": antes, "page_order": nuevo_orden,
            "unspecified_kept_at_end": restantes,
            "backup": cm.result["journal"], "transaction": cm.result}
