"""Tools de edicion de modelo: visibilidad de columnas, direccion de relaciones,
auto fecha/hora. Complementan a las tools de medidas.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.config import get_session
from horizun_pbi_mcp.logging_config import get_logger
from horizun_pbi_mcp.powerbi import model_writer
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError, ValidationError
from horizun_pbi_mcp.pbip import model_edit
from horizun_pbi_mcp.services import dual_mode
from horizun_pbi_mcp.tools._common import guard, guard_mutation
from horizun_pbi_mcp.utils.validation import validate_object_name

log = get_logger("model_edit_tools")

# La normalizacion del modo y la precondicion viven en services.dual_mode: la
# decision de si `both` es ejecutable es una sola y no puede duplicarse por tool.
_check_mode = dual_mode.assert_mode_is_safely_executable


def _validar_entradas(columns: Any) -> List[Dict[str, str]]:
    """Valida y normaliza la lista de columnas ANTES de escribir nada.

    Devuelve las entradas unicas conservando el orden. Los duplicados exactos
    (misma tabla y misma columna) se descartan en silencio: la operacion es
    idempotente, pedir dos veces lo mismo no es contradictorio.
    """
    if not isinstance(columns, list):
        raise ValidationError(
            "columns debe ser una lista de {'table': ..., 'column': ...}.")

    unicas: List[Dict[str, str]] = []
    vistas: Dict[tuple, int] = {}
    duplicadas: List[Dict[str, Any]] = []

    for idx, item in enumerate(columns):
        if not isinstance(item, dict):
            raise ValidationError(
                f"Entrada {idx}: se esperaba un objeto con 'table' y 'column'.",
                details={"index": idx, "received": type(item).__name__})
        table, column = item.get("table"), item.get("column")
        for nombre, valor in (("table", table), ("column", column)):
            if not isinstance(valor, str) or not valor.strip():
                raise ValidationError(
                    f"Entrada {idx}: falta '{nombre}' o esta vacio.",
                    details={"index": idx, "table": table, "column": column})
        table, column = table.strip(), column.strip()
        validate_object_name(table, "tabla")
        validate_object_name(column, "columna")

        clave = (table, column)
        if clave in vistas:
            duplicadas.append({"index": idx, "table": table, "column": column,
                               "duplica_a": vistas[clave]})
            continue
        vistas[clave] = idx
        unicas.append({"table": table, "column": column})

    return unicas, duplicadas


_dual = dual_mode.run_dual


class BulkPartialError(PowerBIMCPError):
    """El lote quedo aplicado en un solo destino y la compensacion no fue limpia.

    Se define aqui para no ampliar `powerbi.errors` fuera del alcance de esta
    fase. `guard()` la serializa como cualquier error de dominio.
    """

    code = "bulk_partially_applied"


class BulkApplyFailedError(PowerBIMCPError):
    """El lote fallo y la compensacion dejo TODO como estaba.

    Se distingue de `BulkPartialError` a proposito: decir "parcial" cuando la
    restauracion fue completa induce a pensar que hay algo que arreglar a mano,
    y no lo hay. Aqui `applied_to` es siempre "ninguno".
    """

    code = "bulk_apply_failed"


def _reconstruir_resultados(solicitadas, por_columna, m, duplicadas,
                            consistente=None) -> Dict[str, Any]:
    """Respuesta compatible: una entrada por columna SOLICITADA, en orden."""
    resultados = []
    for item in solicitadas:
        clave = (str(item.get("table", "")).strip(),
                 str(item.get("column", "")).strip())
        resultados.append(por_columna.get(clave, {"ok": True, "mode": m}))
    salida: Dict[str, Any] = {"mode": m, "count": len(solicitadas),
                              "results": resultados}
    if duplicadas:
        salida["duplicates_ignored"] = duplicadas
    if consistente is not None:
        salida["consistent"] = consistente
    return salida


def _apply_both_compensated(session, unicas, solicitadas, duplicadas,
                            hidden: bool) -> Dict[str, Any]:
    """Coordinador compensado disco -> memoria. MECANISMO INTERNO.

    NO es accesible desde la tool publica: `assert_mode_is_safely_executable`
    rechaza `mode='both'` antes de llegar aqui, porque los dos destinos exigen
    estados de Power BI Desktop incompatibles.

    Se conserva como defensa —y con pruebas unitarias directas— porque la Fase
    1B tendra que decidir como coordinar los dos destinos, y este es el
    comportamiento correcto cuando esa coordinacion exista: escribir el disco
    con journal, aplicar en vivo y, si lo vivo falla, compensar el disco.
    """
    active = session.require_active_pbip()

    # Validacion previa de AMBOS destinos: si el modelo en vivo no admite el
    # lote, se descubre antes de escribir en disco y no hay nada que compensar.
    model_writer.validate_columns_live(session, unicas)
    model_edit.plan_columns_hidden_pbip(active, unicas, hidden)

    por_columna: Dict[tuple, Dict[str, Any]] = {
        (e["table"], e["column"]): {"ok": True, "mode": "both"} for e in unicas}

    pbip_res = model_edit.set_columns_hidden_pbip_bulk(active, unicas, hidden)
    for r in pbip_res["results"]:
        por_columna[(r["table"], r["column"])]["pbip"] = r

    try:
        live_res = model_writer.set_columns_hidden_bulk(session, unicas, hidden)
    except Exception as exc:  # noqa: BLE001
        # Se captura TODO, no solo PowerBIMCPError: una excepcion cruda del
        # motor .NET que se escapara aqui dejaria el disco modificado y el
        # modelo en vivo sin cambiar, que es justo lo que no puede pasar.
        detalle = (exc.to_dict() if isinstance(exc, PowerBIMCPError)
                   else {"error": type(exc).__name__, "message": str(exc)})
        txn = pbip_res.get("txn_object")
        compensacion = (txn.compensate(
            cause=f"fallo al aplicar en vivo: {detalle.get('error')}")
            if txn is not None else None)

        if compensacion is not None and not compensacion["clean"]:
            raise BulkPartialError(
                "El cambio se escribio en los archivos TMDL, fallo al aplicarse "
                "en el modelo en vivo, y la restauracion del disco NO quedo "
                "limpia. Requiere intervencion manual: el journal contiene los "
                "originales.",
                details={"live_error": detalle, "compensation": compensacion,
                         "applied_to": "solo_disco_parcialmente",
                         "journal": compensacion["journal"]}) from exc

        raise BulkApplyFailedError(
            "No se aplico el cambio: fallo en el modelo en vivo y los archivos "
            "TMDL se restauraron por completo a su estado original.",
            details={"live_error": detalle, "compensation": compensacion,
                     "applied_to": "ninguno"}) from exc

    for r in live_res["results"]:
        por_columna[(r["table"], r["column"])]["live"] = r
    return _reconstruir_resultados(solicitadas, por_columna, "both", duplicadas,
                                   consistente=True)


def hide_columns_service(session, columns: Any, hidden: bool,
                         mode: str) -> Dict[str, Any]:
    """Logica interna de `pbi_hide_columns`. SIN decorar.

    Una tool nunca debe llamar a otra tool decorada: `guard()` convertiria los
    errores en datos, el bucle continuaria y el lote devolveria `ok:true` con
    fallos escondidos dentro. Aqui los errores son excepciones y detienen todo.

    `mode='both'` se rechaza en la precondicion, ANTES de cualquier efecto.
    """
    # Lo primero: antes de conectar a TOM, de validar contra el motor, de crear
    # journal, de leer para planificar o de tocar un archivo.
    m = _check_mode(mode, get_session())

    unicas, duplicadas = _validar_entradas(columns)
    solicitadas = list(columns) if isinstance(columns, list) else []

    # Lista vacia: se conserva el comportamiento previo (no es un error).
    if not unicas:
        return {"mode": m, "count": len(solicitadas), "results": [],
                "duplicates_ignored": duplicadas}

    por_columna: Dict[tuple, Dict[str, Any]] = {
        (e["table"], e["column"]): {"ok": True, "mode": m} for e in unicas}

    if m == dual_mode.PBIP:
        active = session.require_active_pbip()
        res = model_edit.set_columns_hidden_pbip_bulk(active, unicas, hidden)
        for r in res["results"]:
            por_columna[(r["table"], r["column"])]["pbip"] = r
    else:
        try:
            res = model_writer.set_columns_hidden_bulk(session, unicas, hidden)
        except PowerBIMCPError as exc:
            # Misma pista que en `run_dual`: si el fallo es "Desktop no esta
            # ahi" y el proyecto .pbip si es escribible, se dice.
            dual_mode.relanzar_con_pista(exc, session)
        for r in res["results"]:
            por_columna[(r["table"], r["column"])]["live"] = r

    return _reconstruir_resultados(solicitadas, por_columna, m, duplicadas)


def _proyecto_activo():
    from horizun_pbi_mcp.config import get_session

    return get_session().require_active_pbip()


def register(mcp) -> None:

    @mcp.tool()
    def pbi_create_calculated_column(table: str, name: str, expression: str,
                                     data_type: str = "string",
                                     format_string: Optional[str] = None,
                                     display_folder: Optional[str] = None,
                                     description: Optional[str] = None,
                                     summarize_by: str = "none",
                                     is_hidden: bool = False,
                                     overwrite: bool = False,
                                     request_id: str = "") -> Dict[str, Any]:
        """Crea una columna calculada (DAX) en una tabla del modelo .pbip.

        `data_type`: string | int64 | double | decimal | boolean | dateTime.
        `summarize_by`: como se agrega por defecto; 'none' para clasificaciones
        y textos, que es lo que casi siempre se quiere.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import model_author

        return guard_mutation(lambda: model_author.create_calculated_column(
            _proyecto_activo(), table, name, expression, data_type=data_type,
            format_string=format_string, display_folder=display_folder,
            description=description, summarize_by=summarize_by,
            is_hidden=is_hidden, overwrite=overwrite))

    @mcp.tool()
    def pbi_create_calculated_table(name: str, expression: str,
                                    columns: Optional[List[Dict[str, str]]] = None,
                                    description: Optional[str] = None,
                                    overwrite: bool = False,
                                    request_id: str = "") -> Dict[str, Any]:
        """Crea una tabla calculada (DAX) en el modelo .pbip.

        Resuelve el caso tipico de tener diez metricas guardadas en diez
        COLUMNAS en vez de en filas: con una tabla calculada que las dinamice
        se obtiene una matriz de verdad, en lugar de escribir una medida por
        columna.

        TMDL exige declarar las columnas y no se pueden adivinar leyendo el
        DAX: si no pasas `columns`, se deducen EJECUTANDO la expresion contra
        el modelo abierto en Power BI Desktop y leyendo el esquema que devuelve
        el motor. Para eso hace falta el modelo abierto Y seleccionado.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import model_author

        return guard_mutation(lambda: model_author.create_calculated_table(
            _proyecto_activo(), name, expression, columns=columns,
            session=get_session() if not columns else None,
            description=description, overwrite=overwrite))

    @mcp.tool()
    def pbi_add_table_from_file(path: str, table_name: str = "",
                                sheet: str = "", culture: str = "",
                                description: str = "", overwrite: bool = False,
                                dry_run: bool = False, table_id: str = "",
                                skip_rows: Optional[int] = None,
                                request_id: str = "") -> Dict[str, Any]:
        """Carga un archivo al modelo como lo haria una persona: abrir, transformar, cargar.

        El mismo recorrido de Power Query —Obtener datos, promover encabezados,
        cambiar tipos, Cargar— pero escrito directo en el proyecto. Los pasos
        de la consulta se llaman como los pone Power BI ('Origen',
        'Encabezados promovidos', 'Tipo cambiado'), asi que se puede abrir y
        editar en el editor sin que desentone.

        Admite .csv, .txt, .tsv, .xlsx, .xlsm, .json, .html/.htm y '.xls'.
        Sin dependencias nuevas: el .xlsx se lee como lo que es, un zip con
        XML, y el HTML con `html.parser` de la biblioteca estandar.

        Un '.xls' NO se toma por la extension: se mira la firma real del
        archivo. En la practica, casi ningun '.xls' que exportan los ERPs es
        el binario OLE2 que la extension promete -- es una tabla HTML con esa
        extension porque Excel la abre igual. Si de verdad es OLE2 (Excel
        97-2003), se rechaza con un mensaje claro en vez de leerlo mal; si es
        un .xlsx renombrado, se lee como .xlsx.

        Para HTML: si el archivo trae varias `<table>`, se elige la mas
        grande y se avisa (usa `table_id` para elegir una en concreto, el
        `id` HTML de la tabla). Los encabezados solo se promueven si la
        tabla usa `<th>`; un reporte sin esa marca (el caso normal en un
        reporte de ERP, donde la fila 1 es el titulo del reporte, no un
        encabezado) carga con columnas 'Column1', 'Column2'...

        **La cultura se deduce del archivo**, mirando como escribe los
        decimales, y se emite SIEMPRE explicita en la consulta. Asumir la del
        modelo es lo que convierte 10527.52 en diez millones sin que nada
        falle: un informe que abre, pinta y miente. `culture` permite forzarla.

        Lo escrito se valida antes de darlo por bueno: si el TMDL generado no
        pasara `pbi_validate_tmdl`, se aborta en vez de dejar un proyecto que
        no abre.

        **Filas de basura antes del encabezado**: es el patron de export mas
        comun de un ERP -fila 1 con el titulo del reporte y el resto de la
        fila vacia, encabezado real en la fila 2-. Por defecto se AUTODETECTA
        la primera fila que pueda ser encabezado (sin huecos y sin nombres
        repetidos) y se dice cual se eligio en `warnings`. `skip_rows` fuerza
        cuantas saltar cuando la deteccion no acierta; `skip_rows=0` obliga a
        usar la fila 1 tal cual. Aplica a csv y xlsx.

        `dry_run=true` devuelve el TMDL y la M sin escribir nada.
        `sheet`: hoja del libro; si se omite, la primera. Solo aplica a xlsx.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import table_from_file

        return guard_mutation(lambda: table_from_file.agregar_tabla(
            _proyecto_activo(), path, table_name=table_name,
            sheet=sheet or None, culture=culture or None,
            description=description or None, overwrite=overwrite,
            dry_run=dry_run, table_id=table_id or None, skip_rows=skip_rows))

    @mcp.tool()
    def pbi_set_storage_mode(table: str, mode: str,
                             request_id: str = "") -> Dict[str, Any]:
        """Cambia el modo de almacenamiento de una tabla: import | directQuery | dual.

        Con directQuery el dato se consulta al origen en cada interaccion y
        desaparece el refresco, pero NO es un interruptor inocuo: la consulta M
        tiene que ser plegable al origen, las columnas y tablas calculadas
        dejan de estar disponibles, y cada visual pasa a ser una consulta al
        servidor.

        Devuelve el modo anterior y cuantas particiones cambiaron, para poder
        deshacerlo sabiendo exactamente que se toco.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import model_author

        return guard_mutation(lambda: model_author.set_storage_mode(
            _proyecto_activo(), table, mode))

    @mcp.tool()
    def pbi_create_relationship(from_table: str, from_column: str,
                                to_table: str, to_column: str,
                                from_cardinality: str = "many",
                                to_cardinality: str = "one",
                                cross_filtering: str = "oneDirection",
                                is_active: bool = True,
                                name: Optional[str] = None,
                                overwrite: bool = False,
                                request_id: str = "") -> Dict[str, Any]:
        """Crea una relacion entre dos columnas del modelo .pbip.

        Por defecto muchos-a-uno con filtro en un sentido: es lo que crea Power
        BI y lo unico que no introduce ambiguedad. `cross_filtering`
        'bothDirections' resuelve casos concretos y complica el modelo entero,
        asi que conviene justificarlo.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import model_author

        return guard_mutation(lambda: model_author.create_relationship(
            _proyecto_activo(), from_table, from_column, to_table, to_column,
            from_cardinality=from_cardinality, to_cardinality=to_cardinality,
            cross_filtering=cross_filtering, is_active=is_active,
            name=name, overwrite=overwrite))

    @mcp.tool()
    def pbi_create_hierarchy(table: str, name: str, levels: List[str],
                             display_folder: Optional[str] = None,
                             description: Optional[str] = None,
                             overwrite: bool = False,
                             request_id: str = "") -> Dict[str, Any]:
        """Crea una jerarquia sobre columnas de la misma tabla.

        `levels`: nombres de columna de MAYOR a MENOR granularidad (p.ej.
        ['Anio','Mes','Dia']). El orden es el de profundizacion y se respeta
        tal cual: no se ordena ni se deduplica porque es informacion.

        Escribe en TMDL: requiere el proyecto CERRADO en Power BI Desktop.
        """
        from horizun_pbi_mcp.pbip import model_author

        return guard_mutation(lambda: model_author.create_hierarchy(
            _proyecto_activo(), table, name, levels,
            display_folder=display_folder, description=description,
            overwrite=overwrite))

    @mcp.tool()
    def pbi_set_column_visibility(table: str, column: str, hidden: bool = True,
                                  mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Oculta o muestra una columna del modelo (p.ej. ocultar columnas de ID).

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip', o usa 'auto' y se mira el estado para elegir. Si estas
        construyendo desde cero, 'auto' o 'pbip': el defecto es 'live' y exige
        Desktop abierto.
        """
        def _impl():
            m = _check_mode(mode, get_session())
            session = get_session()
            return _dual(
                m,
                lambda: model_writer.set_column_hidden(session, table, column, hidden),
                lambda: model_edit.set_column_hidden_pbip(
                    session.require_active_pbip(), table, column, hidden),
                session,
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_hide_columns(columns: List[Dict[str, str]], hidden: bool = True,
                         mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Oculta/muestra VARIAS columnas como un solo lote.

        `columns`: lista de {"table": ..., "column": ...}.

        Valida todas las entradas antes de escribir: si alguna tabla o columna
        no existe, no se modifica nada y el error indica el indice. Los archivos
        TMDL se escriben en una sola transaccion y el modelo en vivo con un solo
        SaveChanges. `count` es el numero de entradas SOLICITADAS (incluidos
        duplicados); `results` trae una entrada por cada una, en el mismo orden.

        mode='both' esta temporalmente deshabilitado bajo la politica estricta:
        'live' necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado,
        asi que una sola llamada aplicaria solo uno de los dos destinos. Elige
        'live' o 'pbip', o usa 'auto' y se mira el estado para elegir. Si estas
        construyendo desde cero, 'auto' o 'pbip': el defecto es 'live' y exige
        Desktop abierto.
        """
        return guard_mutation(lambda: hide_columns_service(
            get_session(), columns, hidden, mode))

    @mcp.tool()
    def pbi_set_relationship_direction(from_table: str, to_table: str,
                                       direction: str = "single",
                                       mode: str = "live", request_id: str = "") -> Dict[str, Any]:
        """Cambia el filtro cruzado de una relacion.

        `direction`: 'single' (una direccion, recomendado) o 'both' (bidireccional).
        OJO: cambiar a 'single' puede alterar totales que dependian de la bidireccional;
        verifica el informe despues.

        No confundir `direction='both'` (bidireccional, valido) con `mode='both'`,
        que esta temporalmente deshabilitado bajo la politica estricta: 'live'
        necesita Power BI Desktop abierto y 'pbip' lo necesita cerrado, asi que
        una sola llamada aplicaria solo uno de los dos destinos.
        """
        def _impl():
            m = _check_mode(mode, get_session())
            session = get_session()
            return _dual(
                m,
                lambda: model_writer.set_relationship_crossfilter(
                    session, from_table, to_table, direction),
                lambda: model_edit.set_relationship_direction_pbip(
                    session.require_active_pbip(), from_table, to_table, direction),
                session,
            )
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_disable_auto_date_time(enabled: bool = False, request_id: str = "") -> Dict[str, Any]:
        """Activa/desactiva 'Auto fecha y hora' (solo modo pbip).

        Desactivarlo aligera el modelo: al reabrir el .pbip, Power BI elimina las
        tablas de fecha automaticas (LocalDateTable_*). Requiere proyecto .pbip activo.
        """
        def _impl():
            session = get_session()
            return model_edit.set_auto_datetime_pbip(
                session.require_active_pbip(), enabled)
        return guard_mutation(_impl)

    @mcp.tool()
    def pbi_add_table_from_source(source: str, table_name: str,
                                  columns: List[Dict[str, Any]],
                                  server: str = "", database: str = "",
                                  schema: str = "dbo", source_table: str = "",
                                  url: str = "",
                                  native_query: Optional[str] = None,
                                  json_path: Optional[List[str]] = None,
                                  description: str = "",
                                  overwrite: bool = False,
                                  dry_run: bool = False,
                                  request_id: str = "") -> Dict[str, Any]:
        """Crea una tabla apuntando a una BASE DE DATOS o API externa.

        `source`: sqlserver | postgresql | odata | web_json.
        - sqlserver/postgresql: `server` + `database` + (`schema`/`source_table`
          o, solo en sqlserver, `native_query` con plegado activado).
        - odata: `url` del ENTITY SET (…/odata/Presupuestos), no la raiz.
        - web_json: `url` que devuelve un array de objetos; `json_path`
          desciende hasta el (["data","rows"]). Tipado con cultura en-US fija:
          JSON escribe numeros sin cultura, y la del sistema es el bug del
          10527.52 que se vuelve diez millones.

        `columns` ([{name, type}]) es OBLIGATORIO: sin credenciales no se
        puede leer el esquema de la fuente, y las columnas no se inventan.

        **La verdad de las credenciales, por delante**: la consulta queda
        escrita y validada, pero el PRIMER refresh lo completa una persona en
        Desktop —pedira credenciales y nivel de privacidad, que viven en
        Desktop, no en el .pbip—. Hasta entonces la tabla existe sin datos y
        este servidor no puede verificar la conexion. Prometer otra cosa
        seria mentir.

        Escribe TMDL: requiere el proyecto CERRADO en Desktop.
        """
        from horizun_pbi_mcp.pbip import table_from_source

        return guard_mutation(lambda: table_from_source.agregar_tabla_desde_fuente(
            _proyecto_activo(), source, table_name, columns,
            server=server, database=database, schema=schema,
            source_table=source_table, url=url, native_query=native_query,
            json_path=json_path, description=description or None,
            overwrite=overwrite, dry_run=dry_run))
