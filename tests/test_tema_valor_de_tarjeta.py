"""El valor de una tarjeta no hereda el tamano de las etiquetas de datos.

Auditoria del 2026-11-07 contra Desktop real: con el tema aplicado, la tarjeta
"Monto total" mostraba "1.551,50" a 9 pt. En la tarjeta clasica `labels` es el
valor principal, y el comodin `visualStyles["*"]["*"]["labels"]` del tema
(9 pt, pensado para graficas) la alcanzaba tambien.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.pbip import theme


@pytest.mark.parametrize("preset", sorted(theme.PRESETS))
def test_la_tarjeta_tiene_su_propio_tamano_de_valor(preset):
    estilos = theme.PRESETS[preset]["tema"]["visualStyles"]
    comodin = estilos["*"]["*"]["labels"][0]["fontSize"]
    tarjeta = estilos["card"]["*"]["labels"][0]
    assert tarjeta["fontSize"] >= 20 > comodin
