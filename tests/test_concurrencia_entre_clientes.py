"""CORE-006 — dos clientes MCP sobre el mismo `.pbip` se pisan sin decirlo.

El mecanismo existe y está bien hecho: `services/idempotency.py` toma un cerrojo
**interproceso** con `msvcrt.locking` / `fcntl.flock`, y hasta documenta el
diseño. No se aplicaba al camino que escribe el proyecto. `services/txn.py` y
`services/planning.py` no tenían ninguno.

Con Codex y Claude apuntando al mismo proyecto —que es el escenario que este
producto tiene por delante, no uno hipotético— dos transacciones sobre el mismo
archivo se entrelazan: los dos leen, los dos escriben, y el segundo se lleva por
delante el cambio del primero. **Las dos respuestas salen en verde.** Un error
sería mejor que eso: al menos se vería.

El oráculo es un contador. Dos procesos leen `0`, cada uno escribe `1`, y el
archivo acaba en `1` habiendo declarado los dos que aplicaron un incremento.
Serializados, acaba en `2`. No hace falta interpretar nada: o sale el número o
no sale.

Son procesos DE VERDAD (`tests/escritor_de_prueba.py`). Lo que falta es un
cerrojo entre procesos, y un `threading.Lock` no dice nada sobre eso.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
ESCRITOR = RAIZ / "tests" / "escritor_de_prueba.py"


@pytest.fixture
def proyecto(tmp_path):
    """Un `.pbip` mínimo y un destino de backups fuera de él."""
    p = tmp_path / "proyecto"
    (p / "p.Report").mkdir(parents=True)
    (p / "p.pbip").write_text('{"version":"1.0"}', encoding="utf-8")
    objetivo = p / "p.Report" / "contador.txt"
    objetivo.write_text("0", encoding="utf-8")
    backups = tmp_path / "backups"
    backups.mkdir()
    return {"dir": p, "backups": backups, "objetivo": objetivo}


def _lanzar(proyecto, pausa: float):
    return subprocess.Popen(
        [sys.executable, str(ESCRITOR), str(proyecto["dir"]),
         str(proyecto["backups"]), str(proyecto["objetivo"]), str(pausa)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _cosechar(proc):
    salida, error = proc.communicate(timeout=300)
    linea = next((l for l in reversed(salida.splitlines()) if l.strip().startswith("{")),
                 None)
    assert linea, f"el escritor no reporto nada. stderr:\n{error[-800:]}"
    return json.loads(linea)


# ============================================================================
def test_dos_escritores_simultaneos_no_pierden_un_cambio(proyecto):
    """El *lost update* del hallazgo, medido con un contador.

    El primero se queda dentro de su transacción mientras el segundo entra. Sin
    cerrojo, los dos leen `0` y el archivo acaba en `1`; con cerrojo, el segundo
    espera su turno, lee `1` y el archivo acaba en `2`.
    """
    primero = _lanzar(proyecto, pausa=3.0)
    time.sleep(0.8)
    segundo = _lanzar(proyecto, pausa=0.0)

    r1, r2 = _cosechar(primero), _cosechar(segundo)
    final = proyecto["objetivo"].read_text(encoding="utf-8").strip()

    aplicados = [r for r in (r1, r2) if r["resultado"] == "aplicado"]
    assert aplicados, f"ninguno pudo escribir: {r1} / {r2}"

    if len(aplicados) == 2:
        assert final == "2", (
            f"los dos declararon exito y el contador quedo en {final}: se "
            f"perdio un cambio sin que nadie lo dijera. {r1} / {r2}")
    else:
        # La otra salida valida: uno falla limpio en vez de esperar.
        fallado = next(r for r in (r1, r2) if r["resultado"] == "fallo")
        assert final == "1", f"{fallado} y el contador quedo en {final}"
        assert fallado["mensaje"], "fallo sin decir por que"


def test_los_dos_trabajos_sobreviven_en_vez_de_perderse_uno(proyecto):
    """La mejora concreta del cerrojo, y merece medirse aparte.

    ANTES de tomarlo, esto NO era un *lost update*: `Transaction` compara la
    huella de cada archivo entre planificar y escribir, así que el segundo
    cliente se encontraba la huella cambiada y **fallaba** con
    `transaction_failed`. Nada se perdía en silencio —eso hay que decirlo, el
    hallazgo lo describía peor de lo que era— pero el trabajo del segundo sí se
    perdía, y el conflicto era evitable: solo hacía falta esperar el turno.

    Medido: sin cerrojo, uno falla y el contador queda en 1. Con cerrojo, los
    dos aplican y queda en 2.
    """
    primero = _lanzar(proyecto, pausa=3.0)
    time.sleep(0.8)
    segundo = _lanzar(proyecto, pausa=0.0)
    r1, r2 = _cosechar(primero), _cosechar(segundo)

    assert [r["resultado"] for r in (r1, r2)] == ["aplicado", "aplicado"], (
        f"un cliente perdio su trabajo por un conflicto evitable: {r1} / {r2}")
    assert proyecto["objetivo"].read_text(encoding="utf-8").strip() == "2"


def test_el_segundo_espera_su_turno_en_vez_de_entrelazarse(proyecto):
    """No basta con que el numero salga: hay que ver que hubo espera."""
    primero = _lanzar(proyecto, pausa=3.0)
    time.sleep(0.8)
    segundo = _lanzar(proyecto, pausa=0.0)
    r1, r2 = _cosechar(primero), _cosechar(segundo)

    ambos = [r for r in (r1, r2) if r["resultado"] == "aplicado"]
    if len(ambos) < 2:
        pytest.skip("este sistema resuelve la carrera fallando, no esperando")

    espera = max(r["espero_s"] for r in ambos)
    assert espera >= 1.0, (
        f"ninguno espero: entraron a la vez y se entrelazaron. {r1} / {r2}")
    assert {r["leyo"] for r in ambos} == {0, 1}, (
        f"los dos leyeron el mismo valor: {r1} / {r2}")


def test_proyectos_distintos_no_se_bloquean_entre_si(tmp_path):
    """El cerrojo es POR PROYECTO. Serializar todo el servidor seria otro defecto."""
    proyectos = []
    for n in range(2):
        p = tmp_path / f"proyecto{n}"
        (p / "p.Report").mkdir(parents=True)
        (p / "p.pbip").write_text('{"version":"1.0"}', encoding="utf-8")
        obj = p / "p.Report" / "contador.txt"
        obj.write_text("0", encoding="utf-8")
        b = tmp_path / f"backups{n}"
        b.mkdir()
        proyectos.append({"dir": p, "backups": b, "objetivo": obj})

    inicio = time.monotonic()
    procs = [_lanzar(pr, pausa=2.0) for pr in proyectos]
    resultados = [_cosechar(p) for p in procs]
    transcurrido = time.monotonic() - inicio

    assert all(r["resultado"] == "aplicado" for r in resultados), resultados
    assert transcurrido < 5.0, (
        f"dos proyectos distintos se serializaron ({transcurrido:.1f}s): el "
        "cerrojo no es por proyecto")
    for pr in proyectos:
        assert pr["objetivo"].read_text(encoding="utf-8").strip() == "1"


def test_el_cerrojo_no_se_deja_dentro_del_proyecto(proyecto):
    """Un archivo nuestro dentro del `.pbip` es un archivo que Power BI lee."""
    _cosechar(_lanzar(proyecto, pausa=0.0))

    intrusos = [p for p in proyecto["dir"].rglob("*")
                if p.is_file() and "lock" in p.name.lower()]
    assert not intrusos, (
        f"el cerrojo se escribio dentro del proyecto del usuario: {intrusos}")


def test_una_sola_implementacion_de_cerrojo_entre_procesos():
    """Dos implementaciones significan dos formas distintas de quedarse a medias.

    `idempotency` ya tenia una bien hecha; lo que faltaba era APLICARLA al
    camino que escribe, no escribir otra.
    """
    import re

    fuentes = RAIZ / "src" / "horizun_pbi_mcp" / "services"
    # Se buscan LLAMADAS, no menciones: el docstring de `idempotency` explica el
    # diseño y nombra las dos primitivas, y prohibir la palabra obligaria a
    # borrar la explicacion junto con el duplicado.
    llamada = re.compile(r"\b(?:msvcrt\.locking|fcntl\.flock)\s*\(")
    con_primitiva = sorted(f.name for f in fuentes.glob("*.py")
                           if llamada.search(f.read_text(encoding="utf-8")))
    assert con_primitiva == ["cerrojo.py"], (
        f"la primitiva de cerrojo esta duplicada en {con_primitiva}")
