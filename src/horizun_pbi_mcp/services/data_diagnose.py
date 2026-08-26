"""Diagnostico de CONTENIDO: lo que rompe tableros y ningun metadato ve.

La auditoria existente mira metadatos (medidas sin carpeta, relaciones
bidireccionales); `pbi_profile_data` mira columnas sueltas (vacia, constante,
porcentaje fuera de rango). Lo que faltaba es lo que de verdad tumba un
tablero en produccion y no deja error en ningun sitio:

- **Claves huerfanas**: filas del lado "muchos" cuya clave no existe en el
  lado "uno". Caen al (Blank) de la relacion y los totales cuadran de menos
  sin que nada falle.
- **Grano duplicado**: la tabla que se creia unica por clave tiene dos filas
  por clave. Todo se duplica al cruzar.
- **Huecos de calendario**: dias faltantes en la tabla de fechas. El time
  intelligence devuelve blancos o acumulados que saltan.
- **Umbrales del dueño**: los `critical_fields` del brief, con sus min/max.
  Un 5% de nulos es ruido en un campo de notas e incendio en la clave que
  une con presupuesto: la severidad la decide quien declaro el campo critico,
  no una heuristica.

Reglas de la casa que este modulo respeta a rajatabla:

1. **Ningun veredicto sin prueba**: cada hallazgo lleva la consulta DAX que lo
   demuestra y muestras de los valores culpables.
2. **No se adivina**: no hay deteccion "inteligente" de escalas ni outliers
   estadisticos. Lo generico es determinista (huerfanas, duplicados, huecos);
   lo subjetivo (que es grave) viene del brief.
3. **Un chequeo que no se pudo correr se DICE** (`skipped`, con motivo), no se
   omite en silencio: "no se comprobo" y "esta bien" no son lo mismo.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from horizun_pbi_mcp.logging_config import get_logger

log = get_logger("data_diagnose")

#: Muestras maximas de valores culpables por hallazgo.
MAX_MUESTRAS = 5


def _t(nombre: str) -> str:
    """'Tabla' escapada para DAX."""
    return "'" + str(nombre).replace("'", "''") + "'"


def _col(tabla: str, columna: str) -> str:
    return f"{_t(tabla)}[{columna}]"


def _consultar(session, dax: str, max_rows: int = 50) -> Tuple[List[str], List[List[Any]]]:
    """Ejecuta una consulta interna. Devuelve (columnas_limpias, filas)."""
    from horizun_pbi_mcp.powerbi import dax_runner

    r = dax_runner.run_dax(session, dax, max_rows=max_rows)
    columnas = [str(c).lstrip("[").rstrip("]") for c in (r.get("columns") or [])]
    return columnas, list(r.get("rows") or [])


def _escalar(session, dax: str) -> Dict[str, Any]:
    columnas, filas = _consultar(session, dax, max_rows=1)
    if not filas:
        return {}
    return dict(zip(columnas, filas[0]))


# ------------------------------------------------------------- el brief ---
def _indice_criticos(brief: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """{referencia_casefold: entrada} de los critical_fields del brief."""
    salida: Dict[str, Dict[str, Any]] = {}
    for c in (brief or {}).get("critical_fields") or []:
        ref = str(c.get("field") or "").strip()
        if ref:
            salida[ref.casefold()] = c
    return salida


def _es_critico(criticos: Dict[str, Dict[str, Any]], *refs: str
                ) -> Optional[Dict[str, Any]]:
    for ref in refs:
        hit = criticos.get(str(ref).casefold())
        if hit:
            return hit
    return None


def _hallazgo(rule: str, severity: str, impact: str, query: str,
              evidence: Dict[str, Any], critico: Optional[Dict[str, Any]],
              **extra: Any) -> Dict[str, Any]:
    salida = {"rule": rule, "severity": severity, "impact": impact,
              "query": query, "evidence": evidence, **extra}
    if critico:
        # La severidad sube porque EL DUEÑO declaro el campo critico: la
        # decision es suya, aqui solo se aplica y se cita su porque.
        salida["severity"] = "error"
        salida["declared_critical"] = {"field": critico.get("field"),
                                       "why": critico.get("why")}
    return salida


# ------------------------------------------------------------ chequeos ---
def _chequeo_huerfanas(session, rel: Dict[str, Any],
                       criticos: Dict[str, Dict[str, Any]]
                       ) -> Optional[Dict[str, Any]]:
    """Claves del lado muchos que no existen en el lado uno."""
    ft, fc = rel["from_table"], rel["from_column"]
    tt, tc = rel["to_table"], rel["to_column"]
    consulta = (
        'EVALUATE ROW('
        f'"huerfanas", COUNTROWS(EXCEPT(DISTINCT({_col(ft, fc)}), '
        f'DISTINCT({_col(tt, tc)}))), '
        f'"filas_afectadas", COUNTROWS(FILTER({_t(ft)}, '
        f'NOT ISBLANK({_col(ft, fc)}) && NOT {_col(ft, fc)} '
        f'IN DISTINCT({_col(tt, tc)}))), '
        f'"claves_en_blanco", CALCULATE(COUNTROWS({_t(ft)}), '
        f'ISBLANK({_col(ft, fc)})))')
    datos = _escalar(session, consulta)
    huerfanas = int(datos.get("huerfanas") or 0)
    en_blanco = int(datos.get("claves_en_blanco") or 0)
    if not huerfanas and not en_blanco:
        return None

    muestras: List[Any] = []
    if huerfanas:
        _, filas = _consultar(session, (
            f"EVALUATE TOPN({MAX_MUESTRAS}, "
            f"EXCEPT(DISTINCT({_col(ft, fc)}), DISTINCT({_col(tt, tc)})))"),
            max_rows=MAX_MUESTRAS)
        muestras = [f[0] for f in filas]

    # `filas_afectadas` puede volver en blanco: el motor devuelve BLANK cuando
    # el FILTER no encuentra nada o cuando la columna no admite el operador IN.
    # Interpolarlo a secas producia "None fila(s) de 'X' caen al (Blank)", y
    # convertirlo a cero seria peor: cero AFIRMA que no hay ninguna, y lo
    # cierto es que no se pudo contar.
    afectadas = datos.get("filas_afectadas")
    afectadas = None if afectadas is None else int(afectadas)
    cuantificador = (f"{afectadas} fila(s) de '{ft}' caen"
                     if afectadas is not None
                     else f"Hay filas de '{ft}' que caen")

    return _hallazgo(
        "claves_huerfanas", "warning",
        (f"{cuantificador} al (Blank) de la relacion con '{tt}': los totales "
         "cuadran de menos sin que nada falle."
         + ("" if afectadas is not None else
            " No se pudo determinar CUANTAS filas: el conteo volvio vacio.")
         + (f" Ademas {en_blanco} fila(s) tienen la clave EN BLANCO."
            if en_blanco else "")),
        consulta,
        {"orphan_keys": huerfanas, "affected_rows": afectadas,
         "blank_keys": en_blanco, "sample_orphans": muestras,
         "affected_rows_determined": afectadas is not None},
        _es_critico(criticos, f"{ft}[{fc}]", f"{tt}[{tc}]"),
        relationship=f"{ft}[{fc}] -> {tt}[{tc}]",
        is_active=rel.get("is_active", True),
        partial=afectadas is None,
        undetermined=([] if afectadas is not None else ["affected_rows"]))


def _chequeo_grano(session, rel: Dict[str, Any],
                   criticos: Dict[str, Dict[str, Any]]
                   ) -> Optional[Dict[str, Any]]:
    """El lado uno debe ser unico por su clave; si no, todo se duplica."""
    tt, tc = rel["to_table"], rel["to_column"]
    consulta = ('EVALUATE ROW('
                f'"filas", COUNTROWS({_t(tt)}), '
                f'"claves", DISTINCTCOUNT({_col(tt, tc)}))')
    datos = _escalar(session, consulta)
    filas_n = int(datos.get("filas") or 0)
    claves = int(datos.get("claves") or 0)
    if filas_n <= claves:
        return None

    _, filas = _consultar(session, (
        f"EVALUATE TOPN({MAX_MUESTRAS}, "
        f"FILTER(ADDCOLUMNS(VALUES({_col(tt, tc)}), "
        f'"n", CALCULATE(COUNTROWS({_t(tt)}))), [n] > 1), [n], DESC)'),
        max_rows=MAX_MUESTRAS)
    return _hallazgo(
        "grano_duplicado", "warning",
        (f"'{tt}' tiene {filas_n} filas pero solo {claves} valores de "
         f"'{tc}': hay claves repetidas en el lado UNO de la relacion, y al "
         "cruzar TODO se multiplica (costos al doble, avances imposibles)."),
        consulta,
        {"rows": filas_n, "distinct_keys": claves,
         "duplicated_keys": filas_n - claves,
         "sample_duplicates": [{"key": f[0], "count": f[1]} for f in filas]},
        _es_critico(criticos, f"{tt}[{tc}]"),
        table=tt, column=tc)


def _chequeo_calendario(session, tabla: str, columna: str
                        ) -> Optional[Dict[str, Any]]:
    """Dias faltantes en la tabla de fechas."""
    consulta = ('EVALUATE ROW('
                f'"dias", COUNTROWS(DISTINCT({_col(tabla, columna)})), '
                f'"rango", INT(MAX({_col(tabla, columna)}) '
                f'- MIN({_col(tabla, columna)})) + 1)')
    datos = _escalar(session, consulta)
    dias = int(datos.get("dias") or 0)
    rango = int(datos.get("rango") or 0)
    if not dias or rango <= dias:
        return None
    return _hallazgo(
        "calendario_con_huecos", "warning",
        (f"'{tabla}' cubre {rango} dias de rango pero solo tiene {dias}: "
         f"faltan {rango - dias}. El time intelligence sobre esos huecos "
         "devuelve blancos o acumulados que saltan."),
        consulta,
        {"days_present": dias, "days_span": rango, "days_missing": rango - dias},
        None, table=tabla, column=columna)


def _chequeo_umbrales(session, criticos: Dict[str, Dict[str, Any]],
                      model_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Los min/max que el dueño declaro en el brief, contra los datos reales."""
    hallazgos: List[Dict[str, Any]] = []
    medidas = {str(m.get("name", "")).casefold()
               for m in model_data.get("measures") or []}
    for ref_cf, entrada in criticos.items():
        ref = str(entrada.get("field") or "")
        tiene_umbral = entrada.get("min") is not None or entrada.get("max") is not None
        if not tiene_umbral:
            continue
        es_medida = ref.startswith("[") and ref.endswith("]")
        if es_medida:
            nombre = ref[1:-1]
            if nombre.casefold() not in medidas:
                continue  # el campo critico inexistente lo reporta _criticos_perdidos
            consulta = f'EVALUATE ROW("vmin", [{nombre}], "vmax", [{nombre}])'
        else:
            tabla, _, resto = ref.partition("[")
            columna = resto.rstrip("]")
            consulta = ('EVALUATE ROW('
                        f'"vmin", MIN({_col(tabla, columna)}), '
                        f'"vmax", MAX({_col(tabla, columna)}))')
        datos = _escalar(session, consulta)
        vmin, vmax = datos.get("vmin"), datos.get("vmax")
        fuera = []
        if (entrada.get("min") is not None and vmin is not None
                and float(vmin) < float(entrada["min"])):
            fuera.append(f"minimo {vmin} < {entrada['min']}")
        if (entrada.get("max") is not None and vmax is not None
                and float(vmax) > float(entrada["max"])):
            fuera.append(f"maximo {vmax} > {entrada['max']}")
        if fuera:
            hallazgos.append(_hallazgo(
                "umbral_del_brief_violado", "error",
                (f"'{ref}' esta fuera del umbral que el dueño declaro en el "
                 f"brief: {'; '.join(fuera)}."),
                consulta, {"min": vmin, "max": vmax,
                           "declared": {k: entrada[k] for k in ("min", "max")
                                        if entrada.get(k) is not None}},
                entrada, field=ref))
    return hallazgos


def _criticos_perdidos(criticos: Dict[str, Dict[str, Any]],
                       model_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Un campo declarado critico que YA NO EXISTE es un hallazgo, no un skip."""
    medidas = {f"[{m.get('name')}]".casefold()
               for m in model_data.get("measures") or []}
    columnas = set()
    for t in model_data.get("tables") or []:
        for c in t.get("columns") or []:
            columnas.add(f"{t.get('name')}[{c.get('name')}]".casefold())
    hallazgos = []
    for ref_cf, entrada in criticos.items():
        if ref_cf not in medidas and ref_cf not in columnas:
            hallazgos.append({
                "rule": "campo_critico_inexistente", "severity": "error",
                "impact": (f"El brief declara critico '{entrada.get('field')}' "
                           "y ese campo ya no existe en el modelo: o se "
                           "renombro sin actualizar el brief, o se borro lo "
                           "que el dueño considera vital."),
                "declared_critical": {"field": entrada.get("field"),
                                      "why": entrada.get("why")}})
    return hallazgos


# ------------------------------------------------------------- orquestador ---
def diagnose(session, model_data: Dict[str, Any],
             brief: Optional[Dict[str, Any]] = None,
             tables: Optional[List[str]] = None) -> Dict[str, Any]:
    """Corre los chequeos de contenido contra el modelo VIVO."""
    criticos = _indice_criticos(brief)
    filtro = {t.casefold() for t in tables} if tables else None

    hallazgos: List[Dict[str, Any]] = []
    saltados: List[Dict[str, Any]] = []
    corridos = 0

    relaciones = model_data.get("relationships") or []
    tipos_col: Dict[Tuple[str, str], str] = {}
    for t in model_data.get("tables") or []:
        for c in t.get("columns") or []:
            tipos_col[(str(t.get("name", "")).casefold(),
                       str(c.get("name", "")).casefold())] = str(
                c.get("data_type") or "").casefold()

    calendarios_vistos = set()
    for rel in relaciones:
        if not all(rel.get(k) for k in ("from_table", "from_column",
                                        "to_table", "to_column")):
            continue
        if filtro and (rel["from_table"].casefold() not in filtro
                       and rel["to_table"].casefold() not in filtro):
            continue
        for chequeo, nombre in ((_chequeo_huerfanas, "claves_huerfanas"),
                                (_chequeo_grano, "grano_duplicado")):
            try:
                corridos += 1
                h = chequeo(session, rel, criticos)
                if h:
                    hallazgos.append(h)
            except Exception as exc:                     # noqa: BLE001
                saltados.append({
                    "check": nombre,
                    "relationship": f"{rel['from_table']}[{rel['from_column']}]"
                                    f" -> {rel['to_table']}[{rel['to_column']}]",
                    "reason": f"{type(exc).__name__}: {exc}"[:200]})

        # Calendario: el lado uno con clave de tipo fecha, una vez por tabla.
        clave = (rel["to_table"].casefold(), rel["to_column"].casefold())
        if (tipos_col.get(clave, "").startswith("datetime")
                and rel["to_table"].casefold() not in calendarios_vistos):
            calendarios_vistos.add(rel["to_table"].casefold())
            try:
                corridos += 1
                h = _chequeo_calendario(session, rel["to_table"],
                                        rel["to_column"])
                if h:
                    hallazgos.append(h)
            except Exception as exc:                     # noqa: BLE001
                saltados.append({"check": "calendario_con_huecos",
                                 "table": rel["to_table"],
                                 "reason": f"{type(exc).__name__}: {exc}"[:200]})

    if criticos:
        hallazgos.extend(_criticos_perdidos(criticos, model_data))
        try:
            corridos += 1
            hallazgos.extend(_chequeo_umbrales(session, criticos, model_data))
        except Exception as exc:                         # noqa: BLE001
            saltados.append({"check": "umbral_del_brief_violado",
                             "reason": f"{type(exc).__name__}: {exc}"[:200]})

    por_severidad: Dict[str, int] = {}
    for h in hallazgos:
        por_severidad[h["severity"]] = por_severidad.get(h["severity"], 0) + 1

    parciales = [h for h in hallazgos if h.get("partial")]
    return {
        "findings": hallazgos,
        "by_severity": por_severidad,
        "checks_run": corridos,
        # Un chequeo que corrio pero no pudo cuantificar algo no es un chequeo
        # limpio ni uno saltado: es un tercer estado, y se dice.
        "partial_checks": len(parciales),
        "skipped": saltados,
        "relationships_checked": len(relaciones),
        "brief_applied": bool(criticos),
        "clean": not hallazgos and not saltados,
        "note": (None if criticos else
                 "Sin brief con critical_fields: severidades genericas. "
                 "Define umbrales con pbi_define_brief para que 'grave' lo "
                 "decida el dueño del tablero."),
    }
