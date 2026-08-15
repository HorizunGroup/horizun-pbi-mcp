"""Los tres documentos del ciclo no pueden decir cosas distintas.

La matriz, la auditoría y los criterios de aceptación describen el MISMO estado
desde tres ángulos, y hasta ahora nada impedía que divergieran. Divergieron: la
matriz marcaba INSTALL-001, -002 y -010 como parcialmente cerradas mientras la
tabla principal de la auditoría seguía diciendo *abierta* de casi todas. Quien
abriera un documento u otro se llevaba una foto distinta, y ninguna de las dos
llevaba fecha que permitiera saber cuál era la vieja.

El conteo tenía el mismo problema por otra vía: estaba escrito a mano en el
párrafo de «Cuentas». Un número escrito a mano envejece en la primera edición
que alguien haga de una fila, y nadie se entera hasta que otro lo recuenta.

Aquí se recalcula todo desde las filas y se exige que coincida. Las pruebas no
opinan sobre qué estado debería tener cada hallazgo: solo exigen que los tres
documentos digan lo mismo y que los números salgan de contar.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
MATRIZ = RAIZ / "docs" / "MATRIZ_REMEDIACION.md"
AUDIT = RAIZ / "docs" / "audits" / "AUDIT_2026-08-14.md"
ACEPTACION = RAIZ / "docs" / "audits" / "ACCEPTANCE_10_OF_10.md"

ID = r"(?:CONTRACT|CORE|INSTALL|RELEASE|TEST|DOC|CLI)-\d{3}"
ESTADOS = ("parcialmente cerrada", "cerrada", "abierta")


def _limpiar(celda: str) -> str:
    return re.sub(r"[*`]", "", celda).strip()


def _celdas(linea: str) -> list[str]:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def _es_separador(linea: str) -> bool:
    return set(linea.replace("|", "").strip()) <= {"-", " ", ":"}


def _estado(bruto: str, ident: str) -> str:
    texto = _limpiar(bruto).lower()
    for etiqueta in ESTADOS:
        if texto.startswith(etiqueta):
            return etiqueta
    raise AssertionError(f"estado no reconocido en {ident}: {bruto[:80]!r}")


def estados_de_la_matriz() -> dict[str, str]:
    """Las SEIS tablas de estado, reconocidas por su cabecera.

    Por cabecera y no por la forma de la fila: la matriz tiene mas tablas que
    mencionan identificadores -la de pendientes, la de commits- y contarlas
    inflaria el total con filas que no son entradas.
    """
    salida: dict[str, str] = {}
    dentro = False
    for linea in MATRIZ.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| Id |") and linea.rstrip().endswith("Estado |"):
            dentro = True
            continue
        if not linea.startswith("|"):
            dentro = False
            continue
        if not dentro or _es_separador(linea):
            continue
        m = re.match(rf"^\|\s*({ID})\s*\|", linea)
        if not m:
            continue
        ident = m.group(1)
        assert ident not in salida, f"{ident} aparece dos veces en la matriz"
        salida[ident] = _estado(_celdas(linea)[-1], ident)
    return salida


def estados_vigentes_del_audit() -> dict[str, str]:
    """La columna «Estado vigente» de la tabla Resumen de la auditoria."""
    salida: dict[str, str] = {}
    dentro = False
    for linea in AUDIT.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| Id |") and "Estado vigente" in linea:
            dentro = True
            continue
        if not linea.startswith("|"):
            if dentro and salida:
                break
            continue
        if not dentro or _es_separador(linea):
            continue
        m = re.match(rf"^\|\s*({ID})\s*\|", linea)
        if not m:
            continue
        salida[m.group(1)] = _estado(_celdas(linea)[-1], m.group(1))
    return salida


def gates_declarados() -> dict[str, str]:
    """Los gates definidos en los bloques G1..G8 de ACCEPTANCE."""
    salida: dict[str, str] = {}
    dentro = False
    for linea in ACEPTACION.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| # | Gate |"):
            dentro = True
            continue
        if not linea.startswith("|"):
            dentro = False
            continue
        if not dentro or _es_separador(linea):
            continue
        celdas = _celdas(linea)
        m = re.fullmatch(r"G\d+\.\d+", celdas[0])
        if not m:
            continue
        assert celdas[0] not in salida, f"{celdas[0]} definido dos veces"
        salida[celdas[0]] = celdas[-1]
    return salida


def gates_citados_por_la_matriz() -> dict[str, set[str]]:
    """Gate(s) que cita cada entrada en su columna `Gate`."""
    salida: dict[str, set[str]] = {}
    dentro = False
    for linea in MATRIZ.read_text(encoding="utf-8").splitlines():
        if linea.startswith("| Id |") and linea.rstrip().endswith("Estado |"):
            dentro = "Gate" in linea
            continue
        if not linea.startswith("|"):
            dentro = False
            continue
        if not dentro or _es_separador(linea):
            continue
        m = re.match(rf"^\|\s*({ID})\s*\|", linea)
        if not m:
            continue
        celdas = _celdas(linea)
        salida[m.group(1)] = set(re.findall(r"G\d+\.\d+", celdas[3]))
    return salida


# ============================================================================
def test_la_matriz_y_la_auditoria_declaran_el_mismo_estado_vigente():
    """El defecto que hace falta cerrar: dos documentos, dos verdades.

    La matriz es un superconjunto a proposito: recoge todo el ciclo, incluidas
    las entradas `CONTRACT-`, que salieron de la ratificacion del contrato y no
    de la auditoria. Lo que no se admite es lo contrario -un hallazgo de la
    auditoria que la matriz no registre- ni que los dos hablen del mismo
    identificador y digan cosas distintas.
    """
    matriz, audit = estados_de_la_matriz(), estados_vigentes_del_audit()

    solo_audit = sorted(set(audit) - set(matriz))
    assert not solo_audit, (
        f"hallazgos de la auditoria que la matriz canonica no registra: "
        f"{solo_audit}")

    discrepan = {i: (matriz[i], audit[i]) for i in audit if matriz[i] != audit[i]}
    assert not discrepan, (
        "la matriz y la auditoria se contradicen (matriz, auditoria): "
        f"{discrepan}")

    solo_matriz = sorted(set(matriz) - set(audit))
    assert all(i.startswith("CONTRACT-") for i in solo_matriz), (
        "hay entradas fuera de la auditoria que no son del contrato y nadie "
        f"explica de donde salen: {solo_matriz}")


def test_las_cuentas_de_la_matriz_salen_de_contar_las_filas():
    """El parrafo de «Cuentas» no puede ser un numero escrito de memoria."""
    estados = estados_de_la_matriz()
    real = Counter(estados.values())
    texto = MATRIZ.read_text(encoding="utf-8")
    bloque = texto.split("## Cuentas", 1)[1].split("##", 1)[0]

    declarado_total = int(re.search(r"(\d+) entradas", bloque).group(1))
    declarado_cerradas = int(re.search(r"\*\*(\d+) cerradas\*\*", bloque).group(1))
    declarado_parciales = int(
        re.search(r"\*\*(\d+) parcialmente cerradas\*\*", bloque).group(1))
    # Singular tambien: el dia que quede UNA, «1 abiertas» seria una errata que
    # nadie corrige y una prueba que deja de encontrar el numero.
    declarado_abiertas = int(
        re.search(r"\*\*(\d+) abiertas?\*\*", bloque).group(1))

    assert declarado_total == len(estados), (
        f"«Cuentas» dice {declarado_total} entradas y hay {len(estados)}")
    assert declarado_cerradas == real["cerrada"], real
    assert declarado_parciales == real["parcialmente cerrada"], real
    assert declarado_abiertas == real["abierta"], real
    assert (declarado_cerradas + declarado_parciales + declarado_abiertas
            == declarado_total), "las tres cifras no suman el total"


def test_las_listas_de_cerradas_y_parciales_son_las_filas_de_verdad():
    """Enumerarlas y contarlas son dos formas de mentir por separado."""
    estados = estados_de_la_matriz()
    bloque = (MATRIZ.read_text(encoding="utf-8")
              .split("## Cuentas", 1)[1].split("##", 1)[0])

    # `\s+` y no un espacio: el parrafo se reajusta de linea cada vez que la
    # lista crece, y una prueba que exija un ancho concreto acaba dictando como
    # se escribe el documento en vez de comprobar lo que dice.
    for etiqueta, patron in (("cerrada", r"\*\*\d+ cerradas\*\*\s*\(([^)]+)\)"),
                             ("parcialmente cerrada",
                              r"\*\*\d+ parcialmente cerradas\*\*\s*\(([^)]+)\)")):
        citados = set(re.findall(ID, re.search(patron, bloque).group(1)))
        reales = {i for i, e in estados.items() if e == etiqueta}
        assert citados == reales, (
            f"la lista de «{etiqueta}» no coincide con las filas. "
            f"Sobran {sorted(citados - reales)}, faltan {sorted(reales - citados)}")


def test_todo_gate_citado_por_la_matriz_existe_en_aceptacion():
    definidos = set(gates_declarados())
    huerfanos = {i: sorted(g - definidos)
                 for i, g in gates_citados_por_la_matriz().items()
                 if g - definidos}
    assert not huerfanos, f"gates citados que nadie define: {huerfanos}"


def test_todo_gate_definido_cierra_un_hallazgo_de_la_matriz():
    """Un gate que no cierra nada es un criterio sin dueño."""
    ids = set(estados_de_la_matriz())
    sueltos = {}
    for gate, cierra in gates_declarados().items():
        citados = set(re.findall(ID, cierra))
        if not citados:
            continue                      # los declarados «— (ya cumplido)»
        if citados - ids:
            sueltos[gate] = sorted(citados - ids)
    assert not sueltos, f"gates que cierran hallazgos inexistentes: {sueltos}"


def test_el_computo_de_gates_cuadra_con_los_gates_definidos():
    texto = ACEPTACION.read_text(encoding="utf-8")
    definidos = gates_declarados()

    por_bloque = Counter(g.split(".")[0] for g in definidos)
    for linea in texto.splitlines():
        m = re.match(r"^\| (G\d) [^|]*\| (\d+) \|", linea)
        if not m:
            continue
        assert por_bloque[m.group(1)] == int(m.group(2)), (
            f"el computo dice {m.group(2)} gates en {m.group(1)} y hay "
            f"{por_bloque[m.group(1)]}")

    total = int(re.search(r"\| \*\*Total\*\* \| \*\*(\d+)\*\* \|", texto).group(1))
    assert total == len(definidos), (
        f"el computo declara {total} gates y hay {len(definidos)} definidos")

    ultimo = texto.split("### Cómputo actualizado", 1)[1]
    cumplidos = int(re.search(r"Cumplidos con evidencia \| \*\*(\d+)\*\*", ultimo).group(1))
    parciales = int(re.search(r"Parciales \| \*\*(\d+)\*\*", ultimo).group(1))
    pendientes = int(re.search(r"Pendientes \| \*\*(\d+)\*\*", ultimo).group(1))
    declarado = int(re.search(r"\*\*Total\*\* \| \*\*(\d+)\*\*", ultimo).group(1))
    assert cumplidos + parciales + pendientes == declarado == len(definidos), (
        f"el computo actualizado no suma: {cumplidos}+{parciales}+{pendientes} "
        f"!= {declarado} (gates definidos: {len(definidos)})")

    citados = set(re.findall(r"G\d+\.\d+",
                             re.search(r"Cumplidos con evidencia \| \*\*\d+\*\* \(([^)]+)\)",
                                       ultimo).group(1)))
    assert len(citados) == cumplidos, (
        f"dice {cumplidos} cumplidos y enumera {len(citados)}: {sorted(citados)}")
    assert citados <= set(definidos), (
        f"cumplidos que no existen: {sorted(citados - set(definidos))}")


@pytest.mark.parametrize("ident,gate", [("INSTALL-011", "G4.9")])
def test_el_hallazgo_nuevo_esta_en_los_tres_documentos(ident, gate):
    """INSTALL-011 no puede vivir solo en el commit que lo arregla."""
    assert ident in estados_de_la_matriz(), f"{ident} no esta en la matriz"
    assert ident in estados_vigentes_del_audit(), f"{ident} no esta en la auditoria"
    assert gate in gates_declarados(), f"{gate} no esta en los criterios"
    assert ident in gates_declarados()[gate], (
        f"{gate} no declara que cierra {ident}")
    assert gate in gates_citados_por_la_matriz()[ident], (
        f"la fila de {ident} no cita {gate}")


def test_ningun_documento_afirma_que_hay_una_release_publicada():
    """La mentira cómoda al final de un ciclo: dar por hecho lo que falta."""
    for doc in (MATRIZ, AUDIT, ACEPTACION):
        texto = doc.read_text(encoding="utf-8")
        assert "v1.5.5 publicada" not in texto, doc.name
        assert "release publicada" not in texto.lower(), doc.name


EXTERNOS = RAIZ / "docs" / "audits" / "EXTERNAL_GATES.md"


def gates_externos() -> set[str]:
    """Los gates de la tabla resumen de EXTERNAL_GATES, rangos expandidos.

    La tabla escribe `G5.1-G5.6` en una fila porque los seis comparten bloqueo.
    Contar filas daria 13 donde hay 22 gates, que es justo el error que esta
    funcion existe para no cometer.
    """
    salida: set[str] = set()
    dentro = False
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


def test_el_computo_deriva_la_columna_externa_de_la_particion():
    """La tabla de bloques no puede opinar sobre lo que otra lista decide.

    Antes decia «G4: 3 ejecutables hoy» meses despues de que seis gates de G4 se
    cerraran aqui mismo, y nadie lo notaba porque el numero estaba escrito a
    mano. Ahora sale de contar la particion: «requiere entorno real» son los
    `parcial` mas los `pendiente-externo`, y un `pendiente-local` **cuenta como
    ejecutable aqui** por bloqueado que parezca.
    """
    from tests.test_clasificacion_gates import clasificacion, gates_externos

    clas = clasificacion()
    fuera = {g for g, (c, _) in clas.items()
             if c in ("parcial", "pendiente-externo")}
    assert fuera == gates_externos(), (
        "las fichas de EXTERNAL_GATES y la particion no describen el mismo "
        f"conjunto. Solo en la particion: {sorted(fuera - gates_externos())}; "
        f"solo en las fichas: {sorted(gates_externos() - fuera)}")

    texto = ACEPTACION.read_text(encoding="utf-8")
    por_bloque = Counter(g.split(".")[0] for g in fuera)
    filas = 0
    for linea in texto.splitlines():
        m = re.match(r"^\| (G\d) [^|]*\| (\d+) \| (\d+) \| (\d+)", linea)
        if not m:
            continue
        filas += 1
        bloque, total, aqui, requiere = m.group(1), *map(int, m.groups()[1:])
        assert requiere == por_bloque[bloque], (
            f"{bloque}: la tabla dice {requiere} con entorno real y la "
            f"particion cuenta {por_bloque[bloque]}")
        assert aqui + requiere == total, (
            f"{bloque}: {aqui}+{requiere} no suma los {total} gates del bloque")
    assert filas == 8, f"la tabla de computo tiene {filas} bloques, no 8"

    m = re.search(r"\| \*\*Total\*\* \| \*\*(\d+)\*\* \| \*\*(\d+)\*\* \| \*\*(\d+)\*\* \|",
                  texto)
    assert int(m.group(3)) == len(fuera)
    assert int(m.group(2)) + int(m.group(3)) == int(m.group(1)) == len(clas)


def test_ningun_gate_esta_a_la_vez_cumplido_y_declarado_externo():
    """G4.6 salio de la lista externa porque se cerro; G3.6, antes que el.

    La contradiccion es facil de introducir -se cierra un gate y se olvida
    sacarlo- y deja el documento diciendo que algo esta bloqueado por una VM
    que ya no hace falta.
    """
    ultimo = ACEPTACION.read_text(encoding="utf-8").split("### Cómputo actualizado", 1)[1]
    cumplidos = set(re.findall(
        r"G\d+\.\d+",
        re.search(r"Cumplidos con evidencia \| \*\*\d+\*\* \(([^)]+)\)", ultimo).group(1)))
    choque = cumplidos & gates_externos()
    assert not choque, (
        f"cumplidos con evidencia y a la vez en EXTERNAL_GATES: {sorted(choque)}")


DOSSIER = RAIZ / "docs" / "audits" / "CONTRACT_003_RATIFICATION.md"

#: Lo que una decision de contrato necesita tener delante para poder tomarse.
#: Sin esto, "pendiente de ratificacion" es un aparcamiento indefinido.
APARTADOS = ("Contrato actual", "Contrato propuesto", "Diff de",
             "A quién puede romper", "Peligro de dejarlo como está",
             "Alternativa compatible", "Plan de deprecación",
             "Versión semántica recomendada",
             "Pruebas que se activarían tras ratificación")


def test_el_dossier_de_contract_003_esta_completo():
    """Un hallazgo bloqueado por una decision tiene que traer la decision hecha.

    CONTRACT-003 lleva abierto porque romper el contrato exige una firma
    humana. Lo que no puede pasar es que quien tenga que firmar no encuentre
    ahi lo que necesita para hacerlo.
    """
    assert DOSSIER.is_file(), "falta el dossier de ratificacion de CONTRACT-003"
    texto = DOSSIER.read_text(encoding="utf-8")
    faltan = [a for a in APARTADOS if a not in texto]
    assert not faltan, f"el dossier no cubre: {faltan}"
    assert texto.count("## Cambio ") == 3, (
        "el dossier tiene que tratar los TRES cambios de CORE-004(a)(b)(c)")


def test_el_dossier_declara_que_no_se_aplico():
    """La linea que impide que alguien lo lea como un registro de cambios."""
    texto = DOSSIER.read_text(encoding="utf-8")
    assert "NO aplicado" in texto and "Nada de lo que hay aquí está en el código" in texto


def test_contract_003_solo_se_cierra_con_la_ratificacion_registrada():
    """Un dossier no cierra el hallazgo; una firma, si —y tiene que constar—.

    Estuvo abierta todo el ciclo anterior a proposito. Se cerro el 2026-08-15,
    y lo que esta prueba exige es que el cierre **cite la ratificacion**: sin
    esa palabra en la fila, cerrarla seria exactamente lo que la regla prohibe.
    """
    matriz = MATRIZ.read_text(encoding="utf-8")
    fila = next(l for l in matriz.splitlines() if l.startswith("| CONTRACT-003 |"))
    if estados_de_la_matriz()["CONTRACT-003"] == "abierta":
        return                      # sin firma, abierta: tambien es correcto
    assert "ratificada" in fila.lower(), (
        "CONTRACT-003 se da por cerrada sin citar la ratificacion")
    assert "2.0.0" in fila, (
        "la ratificacion era EXCLUSIVAMENTE para una mayor: la fila tiene que "
        "decir en que version se aplico")
