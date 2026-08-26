"""La vista compacta de `pbi_audit_project`: aditiva y sin perder cuentas.

`priority` repetia los quince hallazgos mas graves COMPLETOS -evidencia y
recomendacion incluidas- cuando ya viajaban enteros en `findings`. En un
informe grande eso son decenas de miles de caracteres duplicados dentro de la
misma respuesta.

Lo que estas pruebas defienden:

1. **Sin el parametro, nada cambia.** El contrato historico de `findings`,
   `priority`, `by_domain` y `score_detail` sigue igual.
2. **En compacto no se pierde ninguna cuenta**: los puntajes y los recuentos
   por dominio son los mismos, y `groups` conserva cuantas veces salto cada
   regla aunque los hallazgos se trunquen.
"""
from __future__ import annotations

import json

from horizun_pbi_mcp.services import report_audit


def _hallazgo(rule, severidad, dominio, nombre):
    return {"rule": rule, "severity": severidad, "domain": dominio,
            "object": {"kind": "visual", "name": nombre},
            "evidence": {"x": 1}, "recommendation": "texto largo " * 20,
            "auto_fix_available": rule == "layout_overlap"}


def _auditoria(repeticiones: int = 60) -> dict:
    hallazgos = [_hallazgo("layout_overlap", "warning", "layout", f"v{i}")
                 for i in range(repeticiones)]
    hallazgos += [
        _hallazgo("measure_broken_reference", "error", "measures", "M1"),
        _hallazgo("report_visual_without_title", "info", "report", "v99"),
    ]
    orden = {"info": 0, "warning": 1, "error": 2}
    hallazgos.sort(key=lambda h: (-orden[h["severity"]], h["rule"]))
    return {
        "score": 71, "score_detail": {"score": 71, "rules": 12},
        "finding_count": len(hallazgos),
        "by_severity": {"error": 1, "warning": repeticiones, "info": 1},
        "by_domain": {"layout": {"findings": repeticiones, "score": 40},
                      "measures": {"findings": 1, "score": 90},
                      "report": {"findings": 1, "score": 95}},
        "findings": hallazgos,
        "priority": hallazgos[:15],
        "auto_fixable": ["layout_overlap"],
        "executive_summary": "resumen",
        "warnings": [],
    }


# ======================================================= el contrato historico =
def test_compactar_no_muta_lo_que_recibe():
    """Quien no pide compacto no puede notar que existe.

    Antes esto se comprobaba repitiendo `original == copia` antes y despues,
    y esa segunda comparacion no demostraba nada: es identica a la primera y
    pasa igual mutase o no. Ahora se fotografia la entrada, se llama, y se
    compara contra la foto.
    """
    original = _auditoria()
    antes = json.dumps(original, sort_keys=True, default=str)

    report_audit.compactar(original)

    assert json.dumps(original, sort_keys=True, default=str) == antes, (
        "compactar modifico el diccionario que recibio")


def test_el_control_de_mutacion_puede_fallar_de_verdad():
    """El oraculo de la prueba anterior, comprobado contra una mutacion real.

    Una comprobacion que nunca puede fallar es peor que no tenerla: da verde
    y cubre el hueco. Aqui se muta a mano lo mismo que mutaria una regresion
    de `compactar` y se exige que la foto lo delate.
    """
    original = _auditoria()
    antes = json.dumps(original, sort_keys=True, default=str)

    original["findings"][0]["finding_id"] = "F001"        # la regresion tipica

    assert json.dumps(original, sort_keys=True, default=str) != antes


def test_los_hallazgos_del_llamador_no_ganan_finding_id():
    """`finding_id` es de la vista compacta, no del hallazgo del llamador.

    Se sostiene sobre `dict(h, finding_id=...)`, que crea uno nuevo. Si algun
    dia se cambiara por `h.update(...)` -mas corto y mas rapido- el
    identificador se colaria en la respuesta de quien pidio el formato de
    siempre. Esto lo caza.
    """
    original = _auditoria()

    compacto = report_audit.compactar(original)

    assert compacto["findings"][0] is not original["findings"][0]
    assert all("finding_id" not in h for h in original["findings"])
    assert all("finding_id" in h for h in compacto["findings"])


def test_en_compacto_no_se_tocan_puntajes_ni_dominios():
    completo = _auditoria()
    compacto = report_audit.compactar(completo)

    assert compacto["score"] == completo["score"]
    assert compacto["score_detail"] == completo["score_detail"]
    assert compacto["by_domain"] == completo["by_domain"]
    assert compacto["by_severity"] == completo["by_severity"]
    assert compacto["finding_count"] == completo["finding_count"]


# ============================================================== lo compacto ===
def test_cada_hallazgo_lleva_un_id_estable_en_la_ejecucion():
    compacto = report_audit.compactar(_auditoria())

    ids = [h["finding_id"] for h in compacto["findings"]]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    # Y el mismo calculo sobre la misma auditoria da los mismos ids.
    assert [h["finding_id"] for h in
            report_audit.compactar(_auditoria())["findings"]] == ids


def test_priority_lleva_identificadores_y_no_copias():
    compacto = report_audit.compactar(_auditoria())

    assert compacto["priority"]
    assert all(isinstance(p, str) for p in compacto["priority"])
    conocidos = {h["finding_id"] for h in compacto["findings"]}
    assert set(compacto["priority"]) <= conocidos


def test_el_detalle_de_un_hallazgo_viaja_una_sola_vez():
    completo = _auditoria()
    compacto = report_audit.compactar(completo)

    veces = sum(1 for h in compacto["findings"]
                if h.get("recommendation")) + \
        sum(1 for p in compacto["priority"] if isinstance(p, dict))
    assert veces == len(compacto["findings"])


def test_las_repeticiones_se_agrupan_por_regla():
    compacto = report_audit.compactar(_auditoria(repeticiones=60))

    grupo = next(g for g in compacto["groups"] if g["rule"] == "layout_overlap")
    assert grupo["count"] == 60
    assert grupo["max_severity"] == "warning"
    assert len(grupo["sample_objects"]) == report_audit.COMPACT_MUESTRAS
    assert grupo["sample_truncated"] is True
    assert grupo["auto_fix_available"] is True


def test_los_grupos_se_ordenan_por_severidad_y_volumen():
    compacto = report_audit.compactar(_auditoria())
    reglas = [g["rule"] for g in compacto["groups"]]

    assert reglas[0] == "measure_broken_reference"     # el unico error
    assert reglas.index("layout_overlap") < reglas.index(
        "report_visual_without_title")


def test_el_truncamiento_se_declara_con_el_total():
    compacto = report_audit.compactar(_auditoria(repeticiones=60))

    assert compacto["truncated"] is True
    assert compacto["total_findings"] == 62
    assert compacto["returned_findings"] == report_audit.COMPACT_MAX_FINDINGS
    assert any("62" in w for w in compacto["warnings"])
    # Y lo truncado sigue contado en los grupos.
    assert sum(g["count"] for g in compacto["groups"]) == 62


def test_sin_truncamiento_no_se_avisa_de_nada():
    compacto = report_audit.compactar(_auditoria(repeticiones=3))

    assert compacto["truncated"] is False
    assert compacto["returned_findings"] == compacto["total_findings"] == 5
    assert compacto["warnings"] == []


# ================================================== la tool sigue por defecto ==
def test_la_tool_declara_compact_con_default_compatible():
    import inspect

    from horizun_pbi_mcp.tools import audit_tools

    registradas = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                registradas[fn.__name__] = fn
                return fn
            return deco

    audit_tools.register(_Mcp())
    firma = inspect.signature(registradas["pbi_audit_project"])

    assert firma.parameters["compact"].default is False
    # Y los parametros de siempre no cambian de default.
    assert firma.parameters["min_severity"].default == "info"
    assert firma.parameters["rules"].default is None
    assert firma.parameters["formats"].default is None
