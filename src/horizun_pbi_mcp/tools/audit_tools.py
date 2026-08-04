"""Tools de auditoria integral y correcciones seleccionables (Macrofase E)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from horizun_pbi_mcp.config import get_session, get_settings
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import report_audit
from horizun_pbi_mcp.tools._common import guard, guard_mutation
from horizun_pbi_mcp.tools.visual_tools import _model_data
from horizun_pbi_mcp.utils.file_utils import atomic_write_text, timestamp


def _active():
    return get_session().require_active_pbip()


def _guardar_formatos(resultado: Dict[str, Any], formatos: List[str]) -> None:
    """Valida todo el lote antes de escribir y explicita fallos de artefactos."""
    normalizados = [str(fmt).lower() for fmt in formatos]
    invalidos = [f for f in normalizados
                 if f not in ("markdown", "md", "html", "json")]
    if invalidos:
        raise ValidationError(
            f"Formato no soportado: '{invalidos[0]}'. Usa markdown|html|json.")

    sello = timestamp()
    salidas: Dict[str, str] = {}
    fallos = []
    planes = []
    for f in normalizados:
        clave = "markdown" if f in ("markdown", "md") else f
        if any(p[0] == clave for p in planes):       # alias/repetido, una sola vez
            continue
        if clave == "markdown":
            ruta = get_settings().outputs_dir / f"audit_{sello}.md"
            contenido = report_audit.to_markdown(resultado)
        elif clave == "html":
            ruta = get_settings().outputs_dir / f"audit_{sello}.html"
            contenido = report_audit.to_html(resultado)
        else:
            ruta = get_settings().outputs_dir / f"audit_{sello}.json"
            contenido = json.dumps(
                resultado, indent=2, ensure_ascii=False, default=str)
        planes.append((clave, ruta, contenido))

    # Todas las serializaciones terminaron antes de tocar el primer archivo.
    for clave, ruta, contenido in planes:
        try:
            atomic_write_text(ruta, contenido)
            salidas[clave] = str(ruta)
        except OSError as exc:
            fallos.append({"format": clave, "path": str(ruta),
                           "error": f"{type(exc).__name__}: {exc}"})
    if salidas:
        resultado["outputs"] = salidas
    if fallos:
        resultado["output_failures"] = fallos
        resultado.setdefault("warnings", []).append(
            f"La auditoria termino, pero {len(fallos)} formato(s) no se "
            "pudieron guardar; revisa output_failures.")


def register(mcp) -> None:

    @mcp.tool()
    def pbi_profile_data(tables: Optional[List[str]] = None,
                         max_columns: int = 60) -> Dict[str, Any]:
        """Perfila los VALORES del modelo abierto y devuelve lo que no cuadra.

        Complementa a pbi_audit_model, que revisa la estructura: un porcentaje
        que vale -800 no es un defecto del modelo sino de los datos, y solo se
        ve consultandolos.

        Detecta porcentajes fuera de 0-100, columnas vacias, columnas de un
        solo valor y columnas mayormente vacias. Cada hallazgo trae la consulta
        que lo demuestra y la consecuencia concreta sobre el tablero.

        Solo lectura. `tables` acota el trabajo; `max_columns` evita que un
        modelo grande agote el timeout y devuelva un perfil a medias.
        """
        from horizun_pbi_mcp.services import data_profile

        return guard(lambda: data_profile.profile_model(
            get_session(), tables=tables, max_columns=max_columns))

    @mcp.tool()
    def pbi_audit_project(rules: Optional[List[str]] = None,
                          min_severity: str = "info",
                          formats: Optional[List[str]] = None) -> Dict[str, Any]:
        """Auditoria integral: modelo semantico + informe + layout.

        Devuelve puntaje global y por dominio, resumen ejecutivo, hallazgos
        priorizados con evidencia y recomendacion, y que reglas tienen
        correccion automatica.

        `formats`: ['markdown','html'] escribe tambien esos informes en
        outputs/ y devuelve sus rutas. `rules` y `min_severity` acotan.
        """
        def _impl():
            active = _active()
            resultado = report_audit.audit_project(
                active, _model_data(), rules=rules, min_severity=min_severity)

            _guardar_formatos(resultado, list(formats or []))
            return resultado
        return guard(_impl)

    @mcp.tool()
    def pbi_audit_report_only() -> Dict[str, Any]:
        """Audita solo el informe PBIR (sin las reglas del modelo semantico).

        Cubre paginas vacias, visuales sin titulo, campos rotos, duplicados,
        tamanos de lienzo inconsistentes y la geometria de cada pagina.
        """
        return guard(lambda: report_audit.audit_report(_active(), _model_data()))

    @mcp.tool()
    def pbi_plan_audit_fixes(rules: List[str],
                             objects: Optional[List[str]] = None
                             ) -> Dict[str, Any]:
        """Planifica correcciones para reglas CONCRETAS. No escribe nada.

        No existe "arreglar todo": hay que indicar `rules` explicitamente.
        `objects` acota mas todavia (ids de visual o de pagina). Devuelve las
        acciones exactas que se aplicarian, con su motivo.
        """
        def _impl():
            active = _active()
            auditoria = report_audit.audit_project(active, _model_data())
            return report_audit.plan_fixes(active, auditoria, rules, objects)
        return guard(_impl)

    @mcp.tool()
    def pbi_apply_audit_fixes(actions: List[Dict[str, Any]],
                              confirm: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Aplica las acciones devueltas por pbi_plan_audit_fixes.

        Requiere confirm=true. Cada accion se aplica por su propia via segura
        (transaccion, verificacion y rollback); si una falla, se reporta sin
        detener las demas y sin ocultarlo.
        """
        def _impl():
            if not confirm:
                raise ValidationError(
                    "Pasa confirm=true para aplicar las correcciones.")
            if not actions:
                raise ValidationError("No se recibio ninguna accion.")
            return report_audit.apply_fixes(_active(), actions)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_list_autofix_rules() -> Dict[str, Any]:
        """Reglas que tienen correccion automatica, y en que consiste cada una."""
        def _impl():
            return {"count": len(report_audit.AUTOFIXES),
                    "autofixes": [{"rule": k, **v}
                                  for k, v in sorted(report_audit.AUTOFIXES.items())]}
        return guard(_impl)

    @mcp.tool()
    def pbi_diagnose_data(tables: Optional[List[str]] = None,
                          request_id: str = "") -> Dict[str, Any]:
        """Diagnostico de CONTENIDO contra el modelo VIVO: lo que rompe
        tableros y ningun metadato ve.

        Cuatro chequeos deterministas, cada uno con la consulta DAX que lo
        demuestra y muestras de los valores culpables:

        - **claves_huerfanas**: filas del lado muchos cuya clave no existe en
          el lado uno (caen al Blank de la relacion; los totales cuadran de
          menos sin error). Incluye claves EN BLANCO.
        - **grano_duplicado**: el lado uno con claves repetidas (todo se
          multiplica al cruzar).
        - **calendario_con_huecos**: dias faltantes en la tabla de fechas.
        - **umbral_del_brief_violado** y **campo_critico_inexistente**: los
          `critical_fields` del brief contra los datos reales. La severidad
          la decide el dueño: lo que declaro critico sale como `error`.

        No hay heuristicas "inteligentes" de outliers ni escalas: lo generico
        es determinista y lo subjetivo viene del brief. Un chequeo que no se
        pudo correr sale en `skipped` con su motivo — "no se comprobo" y
        "esta bien" no son lo mismo.

        Requiere el modelo ABIERTO en Desktop (consulta datos, no archivos).
        `tables` acota a las relaciones que tocan esas tablas.
        """
        def _impl():
            session = get_session()
            from horizun_pbi_mcp.powerbi import model_reader
            from horizun_pbi_mcp.services import brief as brief_service
            from horizun_pbi_mcp.services import data_diagnose

            modelo = model_reader.read_model(session)
            try:
                el_brief = brief_service.read_brief(
                    session.require_active_pbip())
            except Exception:                            # noqa: BLE001
                el_brief = None
            return data_diagnose.diagnose(session, modelo, brief=el_brief,
                                          tables=tables)
        return guard(_impl)

    @mcp.tool()
    def pbi_define_port_contract(datasets: List[Dict[str, Any]],
                                 name: str = "",
                                 request_id: str = "") -> Dict[str, Any]:
        """Escribe el CONTRATO del puerto del ecosistema (pbi-port-contract.json).

        El puerto NO es un bus de APIs entre Revit/Navisworks/Project —eso es
        fragil sin arreglo—: es un contrato de datos. Cada herramienta EMITE
        un dataset normalizado con una llave compartida, y este MCP lo valida
        y lo consume.

        `datasets`: [{name, key, columns: [{name, type, required?}],
        emitted_by?, description?}]. La llave es obligatoria: es lo que
        permite cruzar los datasets entre si (p.ej. HRZ_COD_PRES entre el
        modelo BIM, el presupuesto y el cronograma).

        Vive versionado junto al .pbip, como el brief. Se valida con
        pbi_check_contract: archivos entrantes antes de cargar, y el modelo
        activo despues.
        """
        from horizun_pbi_mcp.services import port_contract

        def _impl():
            active = get_session().require_active_pbip()
            datos: Dict[str, Any] = {"datasets": datasets}
            if name:
                datos["name"] = name
            return port_contract.write_contract(active, datos)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_check_contract(source_path: str = "", dataset: str = "",
                           request_id: str = "") -> Dict[str, Any]:
        """Valida contra el contrato del puerto: un archivo entrante o el modelo.

        Con `source_path` (+ `dataset`): el export de Revit/Navisworks/Project
        ANTES de cargarlo — columnas que faltan, tipos incompatibles, llave
        ausente. Chequeo ESTRUCTURAL y honesto: unicidad y huerfanas de la
        llave exigen los datos completos, y eso es pbi_diagnose_data con la
        tabla ya cargada; la respuesta lo dice en `not_checked`.

        Sin `source_path`: el MODELO activo contra el contrato entero, y el
        circulo que cierra todo: `suggested_critical_fields` — las llaves del
        puerto listas para pbi_define_brief, de modo que el diagnostico las
        trate como criticas del dueño sin teclearlas dos veces.
        """
        from horizun_pbi_mcp.services import port_contract

        def _impl():
            active = get_session().require_active_pbip()
            contrato = port_contract.read_contract(active)
            if contrato is None:
                return {"defined": False,
                        "define_with": "pbi_define_port_contract",
                        "hint": ("No hay pbi-port-contract.json en el "
                                 "proyecto: el puerto aun no tiene contrato.")}
            if source_path:
                if not dataset:
                    raise ValidationError(
                        "Con source_path indica tambien 'dataset': contra "
                        "cual spec del contrato se valida el archivo.")
                return {"defined": True,
                        **port_contract.check_file(contrato, dataset,
                                                   source_path)}
            from horizun_pbi_mcp.pbip import tmdl_reader

            modelo = tmdl_reader.read_semantic_model(active, strict=False)
            return {"defined": True,
                    **port_contract.check_model(contrato, modelo)}
        return guard(_impl)
