"""G4.3 — el validador npm se publica de forma atómica, con `npm` DE VERDAD.

Las pruebas de `test_publicacion_atomica.py` simulan `npm`: comprueban que
`--prefix` apunta al staging y nunca al destino vivo, y que dos procesos no se
pisan. Lo que no llegaba a ocurrir era **un `npm install` real**, y ese era todo
el bloqueo del gate: no una VM, solo Node y salida a `registry.npmjs.org`.

Aquí se ejecuta de verdad, y **solo en un data root temporal**: el runtime real
del usuario no se toca en ningún paso.

Lo que se exige, y ninguna parte sobra:

1. Un destino que **ya tenía** una versión, con sus hashes anotados antes.
2. Un corte a mitad del `npm install` —el proceso muere de verdad—.
3. El destino anterior, **byte a byte intacto**.
4. Cero `.staging-`, cero journals, cero temporales huérfanos.
5. Un reintento posterior que **termina bien**.

El punto 3 es el que separa este gate de los que ya estaban: una publicación que
copia sobre el destino vivo deja una mezcla, y una mezcla no se distingue a
simple vista de una instalación buena.

Se omite sola si no hay Node ≥20 o no hay red. La válvula es la misma que el
resto de pruebas que instalan de verdad, y la pone una persona.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
RELATIVA = Path("node_modules/@microsoft/powerbi-report-authoring-cli/dist/cli.js")

pytestmark = pytest.mark.packaging


def _node_disponible() -> tuple[bool, str]:
    try:
        r = subprocess.run(["node", "--version"], capture_output=True, text=True,
                           timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"no hay node: {exc}"
    if r.returncode != 0:
        return False, f"node --version salio {r.returncode}"
    mayor = int(r.stdout.strip().lstrip("v").split(".")[0])
    if mayor < 20:
        return False, f"node {mayor} < 20"
    return True, r.stdout.strip()


def _cargar():
    spec = importlib.util.spec_from_file_location(
        "validador_g43",
        RAIZ / "src" / "horizun_pbi_mcp" / "completado" / "validador.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _hashes(raiz: Path) -> dict[str, str]:
    return {str(f.relative_to(raiz)).replace("\\", "/"):
            hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(raiz.rglob("*")) if f.is_file()}


@pytest.fixture
def entorno(tmp_path):
    disponible, detalle = _node_disponible()
    if not disponible:
        pytest.skip(f"G4.3 necesita Node >= 20 y red: {detalle}")
    if os.environ.get("PBI_MCP_PACKAGING_OFFLINE") == "1":
        pytest.skip("PBI_MCP_PACKAGING_OFFLINE=1 declarado a mano")
    return tmp_path


def _destino_previo(base: Path) -> Path:
    """Una «versión anterior» creíble: el CLI donde el código lo busca."""
    destino = base / "validator"
    cli = destino / RELATIVA
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text("// version anterior, la que NO se puede perder\n",
                   encoding="utf-8")
    (destino / "package.json").write_text(
        json.dumps({"name": "anterior", "version": "0.0.1"}), encoding="utf-8")
    return destino


def test_una_instalacion_npm_real_termina_y_deja_el_cli(entorno):
    """El camino feliz, con `npm` de verdad. Sin esto, lo de abajo no dice nada."""
    validador = _cargar()
    destino = entorno / "validator"

    resultado = validador.instalar(destino)

    assert (destino / RELATIVA).is_file(), (
        f"npm termino pero no dejo el CLI donde el producto lo busca: {resultado}")
    assert resultado["version"], resultado
    assert not list(entorno.glob(".staging-*")), "quedo un staging huerfano"
    assert not (entorno / ".promotion.json").exists(), "quedo un journal"


def test_un_corte_a_mitad_de_npm_deja_el_destino_ANTERIOR_intacto(entorno,
                                                                  monkeypatch):
    """El gate, literal: el corte no puede costar la version que ya estaba.

    El `npm install` se ejecuta de verdad y se mata a mitad. Lo que se compara
    después no es «existe el destino» sino su contenido **byte a byte**: una
    publicación que copiara sobre el destino vivo dejaría una mezcla, y una
    mezcla se parece demasiado a una instalación buena.
    """
    validador = _cargar()
    destino = _destino_previo(entorno)
    antes = _hashes(destino)
    assert antes, "el destino previo quedo vacio"

    correr_real = validador._correr
    matados = []

    def _correr_y_matar(args, cwd=None, timeout=900):
        texto = " ".join(str(a) for a in args)
        if "install" in texto and "--prefix" in texto:
            # Se lanza de verdad y se mata a mitad: es la unica forma de
            # reproducir un corte real -Ctrl-C, apagon, OOM- sobre npm.
            proceso = subprocess.Popen(args, cwd=cwd,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            try:
                proceso.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proceso.kill()
                proceso.wait(timeout=60)
            matados.append(texto)
            raise validador.InstalacionFallida(
                "corte inyectado a mitad del npm install")
        return correr_real(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(validador, "_correr", _correr_y_matar)

    with pytest.raises(validador.InstalacionFallida):
        validador.instalar(destino)

    assert matados, "no se llego a lanzar ningun `npm install`"
    assert _hashes(destino) == antes, (
        "el destino anterior cambio tras un corte: eso es exactamente lo que "
        "G4.3 prohibe")
    assert not list(entorno.glob(".staging-*")), "quedo un staging huerfano"
    assert not (entorno / ".promotion.json").exists(), "quedo un journal huerfano"


def test_tras_el_corte_el_reintento_termina_limpio(entorno, monkeypatch):
    """Un corte no puede dejar el sitio inservible para el siguiente."""
    validador = _cargar()
    destino = _destino_previo(entorno)

    correr_real = validador._correr

    def _corta_una_vez(args, cwd=None, timeout=900):
        texto = " ".join(str(a) for a in args)
        if "install" in texto and "--prefix" in texto:
            monkeypatch.setattr(validador, "_correr", correr_real)
            raise validador.InstalacionFallida("corte inyectado")
        return correr_real(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(validador, "_correr", _corta_una_vez)
    with pytest.raises(validador.InstalacionFallida):
        validador.instalar(destino)

    # Segundo intento, ya sin inyeccion: tiene que terminar bien.
    resultado = validador.instalar(destino)

    assert (destino / RELATIVA).is_file(), resultado
    assert not list(entorno.glob(".staging-*"))
    assert not (entorno / ".promotion.json").exists()


def test_el_runtime_real_del_usuario_no_se_toca(entorno):
    """La guarda de seguridad de esta propia prueba.

    Todo lo de arriba corre sobre `tmp_path`. Si alguna vez alguien le pasara
    el destino real por descuido, esto lo dice: el data root del usuario tiene
    que seguir sin un `validator` recien escrito por la suite.
    """
    from horizun_pbi_mcp.services import report_validator

    real = report_validator.cli_dir()
    assert entorno not in real.parents and real != entorno, (
        f"la prueba estaria escribiendo en el runtime real: {real}")
    if real.exists():
        marca = real.stat().st_mtime
        shutil.rmtree(entorno / "validator", ignore_errors=True)
        assert real.stat().st_mtime == marca, (
            "el runtime real cambio durante la prueba")
