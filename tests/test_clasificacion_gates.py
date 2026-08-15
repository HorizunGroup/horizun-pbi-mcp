"""La partición de los 54 gates cuadra, y los demás documentos se derivan de ella.

El defecto que trajo este archivo: `ACCEPTANCE_10_OF_10.md` decía «30 cumplidos,
5 parciales, 19 pendientes» y `EXTERNAL_GATES.md` decía «22 externos», y las dos
cifras convivían sin contradecirse **porque no eran comparables**. Los 22
incluían cuatro parciales y dejaban fuera a G1.5 —que espera ratificación— y a
G2.2 —que tiene trabajo local—. Con esas dos cuentas se podía afirmar «no queda
trabajo local» sin que ningún documento lo desmintiera, que es exactamente lo
que pasó.

Lo que se exige aquí:

* cada uno de los 54 gates aparece **exactamente una vez** en la clasificación;
* las categorías son las cinco declaradas y ninguna más;
* la suma da 54 y coincide con las cuentas publicadas;
* `EXTERNAL_GATES.md` —donde los rangos `G5.1–G5.6` se expanden— no contradice a
  la clasificación: nada cumplido puede estar ahí, y nada clasificado como
  externo puro puede faltar;
* **ningún gate clasificado como externo puro confiesa trabajo local** en su
  propia ficha. Ese fue el caso de G4.7, que decía literalmente «construir el
  bundle sí es trabajo local y queda pendiente» dentro del documento cuyo
  encabezado promete lo contrario.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
CLASIFICACION = RAIZ / "docs" / "audits" / "CLASIFICACION_GATES.md"
ACEPTACION = RAIZ / "docs" / "audits" / "ACCEPTANCE_10_OF_10.md"
EXTERNOS = RAIZ / "docs" / "audits" / "EXTERNAL_GATES.md"

CATEGORIAS = ("cumplido", "parcial", "pendiente-local",
              "pendiente-ratificacion", "pendiente-externo")

#: Categorías cuyo trabajo puede hacerse en esta máquina. Si alguna tiene
#: gates, la frase «100% local» es falsa.
LOCALES = ("pendiente-local",)


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def _es_separador(linea: str) -> bool:
    return set(linea.replace("|", "").strip()) <= {"-", " ", ":"}


def clasificacion() -> dict[str, tuple[str, str]]:
    """`{gate: (categoria, motivo)}` leído de la tabla de la partición."""
    salida: dict[str, tuple[str, str]] = {}
    for linea in CLASIFICACION.read_text(encoding="utf-8").splitlines():
        if not linea.startswith("|") or _es_separador(linea):
            continue
        c = _celdas(linea)
        if len(c) < 3 or not re.fullmatch(r"G\d+\.\d+", c[0]):
            continue
        assert c[0] not in salida, f"{c[0]} clasificado dos veces"
        salida[c[0]] = (c[1], c[2])
    return salida


def gates_definidos() -> set[str]:
    """Los gates de los bloques G1..G8 de ACCEPTANCE: la lista autoritativa."""
    salida, dentro = set(), False
    for linea in ACEPTACION.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| # | Gate |"):
            dentro = True
            continue
        if not linea.startswith("|"):
            dentro = False
            continue
        if not dentro or _es_separador(linea):
            continue
        c = _celdas(linea)
        if re.fullmatch(r"G\d+\.\d+", c[0]):
            salida.add(c[0])
    return salida


def gates_externos() -> set[str]:
    """Los de EXTERNAL_GATES, con los rangos expandidos.

    La tabla escribe `G5.1-G5.6` en una fila porque los seis comparten bloqueo.
    Contar filas daría 13 donde hay 22 gates.
    """
    salida, dentro = set(), False
    for linea in EXTERNOS.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| Gate | Hallazgo |"):
            dentro = True
            continue
        if not linea.startswith("|"):
            dentro = False
            continue
        if not dentro or _es_separador(linea):
            continue
        celda = _celdas(linea)[0]
        m = re.fullmatch(r"(G(\d+)\.(\d+))[–\-—](G?\2\.(\d+))", celda)
        if m:
            salida.update(f"G{m.group(2)}.{n}"
                          for n in range(int(m.group(3)), int(m.group(5)) + 1))
        elif re.fullmatch(r"G\d+\.\d+", celda):
            salida.add(celda)
    return salida


# ------------------------------------------------------------- la partición --

def test_cada_gate_aparece_exactamente_una_vez():
    clas, definidos = clasificacion(), gates_definidos()
    sobran = sorted(set(clas) - definidos)
    faltan = sorted(definidos - set(clas))
    assert not sobran, f"clasificados y no definidos: {sobran}"
    assert not faltan, f"definidos y sin clasificar: {faltan}"


def test_la_suma_es_exactamente_54():
    clas = clasificacion()
    assert len(clas) == 54, f"la partición tiene {len(clas)} gates, no 54"
    assert len(gates_definidos()) == 54


def test_no_hay_categorias_inventadas():
    malas = {g: c for g, (c, _) in clasificacion().items() if c not in CATEGORIAS}
    assert not malas, (
        f"categorías fuera de las cinco declaradas: {malas}. Una categoría "
        "nueva sin declarar es una forma de esconder un gate")


def test_toda_fila_trae_motivo():
    """Una categoría sin motivo es una opinión con formato de tabla."""
    sin = [g for g, (_, motivo) in clasificacion().items() if len(motivo) < 10]
    assert not sin, f"clasificados sin motivo legible: {sin}"


def test_las_cuentas_publicadas_salen_de_contar():
    """El número escrito a mano envejece en la primera edición de una fila."""
    conteo = Counter(c for c, _ in clasificacion().values())
    texto = CLASIFICACION.read_text(encoding="utf-8")
    for categoria in CATEGORIAS:
        esperado = conteo.get(categoria, 0)
        assert f"| {categoria} | **{esperado}** |" in texto, (
            f"la tabla de cuentas no dice {esperado} para {categoria}; "
            f"contando salen {esperado}")
    assert f"| **Total** | **{sum(conteo.values())}** |" in texto


# -------------------------------------------- coherencia con los otros dos --

def test_la_aceptacion_no_contradice_a_la_particion():
    """`ACCEPTANCE` publica cumplidos/parciales/pendientes; tienen que cuadrar."""
    conteo = Counter(c for c, _ in clasificacion().values())
    ultimo = ACEPTACION.read_text(encoding="utf-8").split("### Cómputo actualizado", 1)[1]
    cumplidos = int(re.search(r"Cumplidos con evidencia \| \*\*(\d+)\*\*", ultimo).group(1))
    parciales = int(re.search(r"Parciales \| \*\*(\d+)\*\*", ultimo).group(1))
    pendientes = int(re.search(r"Pendientes \| \*\*(\d+)\*\*", ultimo).group(1))

    assert cumplidos == conteo["cumplido"], (
        f"ACCEPTANCE dice {cumplidos} cumplidos y la partición {conteo['cumplido']}")
    assert parciales == conteo["parcial"], (
        f"ACCEPTANCE dice {parciales} parciales y la partición {conteo['parcial']}")
    esperado = (conteo["pendiente-local"] + conteo["pendiente-ratificacion"]
                + conteo["pendiente-externo"])
    assert pendientes == esperado, (
        f"ACCEPTANCE dice {pendientes} pendientes y la partición {esperado} "
        f"(local {conteo['pendiente-local']} + ratificación "
        f"{conteo['pendiente-ratificacion']} + externo {conteo['pendiente-externo']})")


def test_ningun_cumplido_sigue_listado_como_externo():
    choque = {g for g, (c, _) in clasificacion().items()
              if c == "cumplido"} & gates_externos()
    assert not choque, (
        f"cumplidos y a la vez en EXTERNAL_GATES: {sorted(choque)}. Se cierra "
        "un gate y se olvida sacarlo, y el documento sigue diciendo que hace "
        "falta una VM que ya no hace falta")


def test_todo_externo_puro_esta_en_external_gates():
    clas = clasificacion()
    puros = {g for g, (c, _) in clas.items() if c == "pendiente-externo"}
    faltan = sorted(puros - gates_externos())
    assert not faltan, (
        f"clasificados como externos y sin ficha en EXTERNAL_GATES: {faltan}. "
        "Sin ficha, nadie sabe qué infraestructura hace falta")


def test_la_tabla_de_externos_solo_admite_parciales_y_externos_puros():
    """La contradiccion exacta que trajo esta prueba.

    El encabezado de `EXTERNAL_GATES.md` promete que ahi solo entra lo que
    **ninguna cantidad de trabajo local cierra**. G4.7 estaba en la tabla y su
    propia ficha decia «la parte construir el bundle si es trabajo local y queda
    pendiente». Un gate con trabajo local pendiente listado como imposible es
    como se afirma «no queda trabajo local» sin que ningun documento lo
    desmienta.

    Un `parcial` si puede estar: tiene el mecanismo demostrado aqui y lo que le
    falta es un entorno. Lo que no puede estar es un `pendiente-local`, un
    `cumplido` ni un `pendiente-ratificacion`.
    """
    clas = clasificacion()
    permitidas = {"parcial", "pendiente-externo"}
    intrusos = {g: clas[g][0] for g in gates_externos()
                if clas.get(g, ("", ""))[0] not in permitidas}
    assert not intrusos, (
        f"en la tabla de externos y no son externos ni parciales: {intrusos}. "
        "Mientras esten ahi, el documento dice que no se puede hacer nada y la "
        "particion dice que si")


def test_external_gates_no_esconde_trabajo_local():
    """El encabezado promete que ahí solo entra lo que no se puede hacer aquí.

    G4.7 decía literalmente «la parte *construir el bundle* sí es trabajo local
    y queda pendiente» **dentro** de ese documento. Una confesión de trabajo
    local en la lista de lo imposible es lo que sostuvo el «no queda trabajo
    local».
    """
    clas = clasificacion()
    texto = EXTERNOS.read_text(encoding="utf-8")
    secciones = re.split(r"^## ", texto, flags=re.M)[1:]
    sospechosas = []
    for seccion in secciones:
        gates = set(re.findall(r"G\d+\.\d+", seccion.split("\n", 1)[0]))
        if not gates:
            continue
        # Solo se exige a los que la partición declara externos PUROS: un
        # `parcial` puede mencionar trabajo local sin mentir, porque su
        # categoría ya lo dice.
        if not all(clas.get(g, ("", ""))[0] == "pendiente-externo" for g in gates):
            continue
        cuerpo = seccion.lower()
        for frase in ("es trabajo local", "sí es\ntrabajo local",
                      "queda pendiente bajo"):
            if frase in cuerpo:
                sospechosas.append((sorted(gates), frase))
    assert not sospechosas, (
        f"secciones de externos puros que confiesan trabajo local: {sospechosas}")


# --------------------------------------------------------- la frase honesta --

def test_la_frase_cien_por_cien_local_no_se_puede_decir_con_pendientes():
    """El invariante que este archivo existe para sostener.

    No comprueba prosa: comprueba que la partición y los documentos no permitan
    afirmar «100% local» mientras alguna categoría local tenga gates. Si algún
    día `pendiente-local` queda vacío, esta prueba se vuelve trivialmente cierta
    y hay que mirarla de nuevo.
    """
    conteo = Counter(c for c, _ in clasificacion().values())
    locales = sum(conteo.get(c, 0) for c in LOCALES)
    texto = CLASIFICACION.read_text(encoding="utf-8")

    if locales:
        pendientes = sorted(g for g, (c, _) in clasificacion().items()
                            if c in LOCALES)
        assert "no es «100% local»" in texto or "100% local" in texto, (
            "quedan gates con trabajo local y el documento no lo advierte")
        for g in pendientes:
            assert g in texto.split("## Cuentas", 1)[1], (
                f"{g} tiene trabajo local pendiente y no se nombra en las cuentas")
    else:
        assert "Trabajo local pendiente: ninguno" in texto, (
            "no quedan gates locales: dilo explícitamente en las cuentas")


@pytest.mark.parametrize("documento", [CLASIFICACION, ACEPTACION, EXTERNOS])
def test_los_tres_documentos_se_enlazan(documento):
    """Quien abra uno tiene que poder llegar a la partición."""
    texto = documento.read_text(encoding="utf-8")
    if documento is CLASIFICACION:
        assert "ACCEPTANCE_10_OF_10.md" in texto and "EXTERNAL_GATES.md" in texto
    else:
        assert "CLASIFICACION_GATES.md" in texto, (
            f"{documento.name} no enlaza la partición, que es de donde salen "
            "sus números")
