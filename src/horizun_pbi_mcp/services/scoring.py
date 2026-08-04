"""Puntaje de auditoria comparable entre informes de distinto tamano.

El problema del puntaje anterior
--------------------------------
Era ``100 - (errores*10 + avisos*3 + infos*1)``, acotado a cero. Como la
penalizacion depende del NUMERO ABSOLUTO de hallazgos, y el numero de hallazgos
crece con el tamano del informe, el puntaje medía sobre todo el tamano:

- un modelo de 3 tablas con las mismas malas practicas sacaba 70;
- el PB4 real, con 21 tablas y 353 columnas, sacaba 35 en el modelo y **0** en
  el informe (201 hallazgos), aunque muchos fueran meramente informativos;
- una vez en cero, empeorar mas ya no se notaba, y mejorar tampoco.

Dos informes no eran comparables, que es justamente para lo que sirve un
puntaje.

Como se calcula ahora
---------------------
Se puntua el CUMPLIMIENTO de cada regla aplicable, no el recuento global:

1. cada regla pesa segun su severidad (error 5, aviso 2, info 1);
2. una regla que no dispara no penaliza;
3. una que dispara penaliza en proporcion a su DENSIDAD —cuantos de los objetos
   evaluados incumplen—, con un suelo: incumplir aunque sea una vez cuesta el
   40% del peso, porque no es lo mismo que cumplir;
4. **ninguna regla puede costar mas que su propio peso**: una sola regla ruidosa
   no puede hundir el puntaje entero;
5. el divisor es la suma de pesos de todas las reglas APLICABLES, disparen o no.

Asi el puntaje es "que fraccion del peso auditable esta limpia", que no depende
del tamano del informe.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

ERROR = "error"
WARNING = "warning"
INFO = "info"

#: Cuanto pesa cada severidad en el divisor y en la penalizacion.
PESOS = {ERROR: 5.0, WARNING: 2.0, INFO: 1.0}

#: Suelo: incumplir una regla, aunque sea una vez, cuesta esta fraccion del peso.
SUELO = 0.4


def peso_de(severidad: Optional[str]) -> float:
    return PESOS.get((severidad or WARNING).lower(), PESOS[WARNING])


def compute(hallazgos: Iterable[Dict[str, Any]],
            reglas_aplicables: Iterable[Dict[str, str]],
            objetos_evaluados: int) -> Dict[str, Any]:
    """Puntaje 0-100 normalizado, con el desglose que lo justifica.

    `reglas_aplicables`: ``[{"rule": id, "severity": sev}, ...]`` — TODAS las
    que se ejecutaron, hayan encontrado algo o no. Sin ellas el divisor
    dependeria de cuantas fallaron, y volveriamos al problema anterior.

    `objetos_evaluados`: cuantos objetos del modelo/informe se examinaron. Es lo
    que convierte un recuento en una densidad.
    """
    reglas = list(reglas_aplicables)
    universo = max(1, int(objetos_evaluados or 0))

    por_regla: Dict[str, int] = {}
    sev_observada: Dict[str, str] = {}
    for h in hallazgos:
        rid = h.get("rule", "?")
        por_regla[rid] = por_regla.get(rid, 0) + 1
        # Si una regla emite varias severidades, manda la mas grave.
        actual = sev_observada.get(rid)
        if actual is None or peso_de(h.get("severity")) > peso_de(actual):
            sev_observada[rid] = h.get("severity", WARNING)

    if not reglas:
        # Sin registro de reglas se puntua sobre las que dispararon: peor, pero
        # sigue siendo una densidad, no un recuento.
        reglas = [{"rule": r, "severity": sev_observada.get(r, WARNING)}
                  for r in por_regla]

    total_peso = sum(peso_de(r.get("severity")) for r in reglas) or 1.0
    penalizacion = 0.0
    detalle: List[Dict[str, Any]] = []

    for r in reglas:
        rid = r.get("rule", "?")
        n = por_regla.get(rid, 0)
        peso = peso_de(sev_observada.get(rid, r.get("severity")))
        if n == 0:
            detalle.append({"rule": rid, "findings": 0, "weight": peso,
                            "penalty": 0.0, "density": 0.0})
            continue
        densidad = min(1.0, n / universo)
        # Acotado a `peso` por construccion: SUELO + (1-SUELO)*densidad <= 1.
        castigo = peso * (SUELO + (1.0 - SUELO) * densidad)
        penalizacion += castigo
        detalle.append({"rule": rid, "findings": n, "weight": peso,
                        "penalty": round(castigo, 3), "density": round(densidad, 4)})

    limpio = max(0.0, 1.0 - penalizacion / total_peso)
    return {
        "score": int(round(100 * limpio)),
        "method": "compliance_weighted_v2",
        "rules_applicable": len(reglas),
        "rules_triggered": sum(1 for d in detalle if d["findings"]),
        "objects_evaluated": universo,
        "max_weight": round(total_peso, 3),
        "penalty": round(penalizacion, 3),
        "per_rule": sorted(detalle, key=lambda d: -d["penalty"]),
    }


def contar_objetos_modelo(model_data: Optional[Dict[str, Any]]) -> int:
    """Objetos del modelo que las reglas pueden examinar."""
    if not model_data:
        return 1
    tablas = model_data.get("tables") or []
    columnas = sum(len(t.get("columns") or []) for t in tablas)
    return max(1, len(tablas) + columnas + len(model_data.get("measures") or [])
               + len(model_data.get("relationships") or []))


def contar_objetos_informe(paginas: int, visuales: int) -> int:
    """Objetos del informe que las reglas pueden examinar."""
    return max(1, int(paginas or 0) + int(visuales or 0))
