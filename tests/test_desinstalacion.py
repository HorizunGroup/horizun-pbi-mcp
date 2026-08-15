"""INSTALL-008 / G4.4 y G4.5 — desinstalar y purgar, sin sorpresas.

No existía ninguno de los dos. El procedimiento manual está escrito en
`docs/RUNBOOK_INSTALACION.md`, y eso es mejor que nada, pero un procedimiento
manual de cuatro pasos con rutas largas es un procedimiento que alguien hará mal
un martes por la tarde — y el paso que se hace mal es siempre el de borrar.

Dos exigencias, y las dos son sobre lo que pasa **antes** de borrar:

  - **G4.5 — enumerar primero.** El seco es el comportamiento por defecto:
    quien escribe `--purge` ve la lista y todavía puede arrepentirse. Pedir la
    confirmación aparte convierte un error de dedo en un susto en vez de en una
    pérdida.
  - **G4.4 — solo queda lo que se eligió conservar.** `outputs/` y `backups/`
    son del usuario: sus exportaciones y los respaldos de SUS proyectos.
    Borrarlos al desinstalar convertiría «quitar el plugin» en «perder tu
    trabajo», así que sobreviven salvo que se pidan explícitamente.

Todo ocurre bajo `tmp_path`. Ninguna prueba toca la instalación real.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture
def bootstrap():
    spec = importlib.util.spec_from_file_location(
        f"_bs_{uuid.uuid4().hex}", RAIZ / "scripts" / "plugin_bootstrap.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def instalado(tmp_path, bootstrap):
    """Un data root con la forma de una instalación usada."""
    root = tmp_path / "datos"
    for rel, texto in (
            ("1.5.5/runtime/Scripts/python.exe", "interprete"),
            ("1.5.5/libs/Microsoft.AnalysisServices.dll", "dll" * 400),
            ("1.5.5/install-status.json", '{"state":"ready"}'),
            (".previous-1.5.4-9-abc/runtime/Scripts/python.exe", "viejo"),
            ("runtime-state.json", '{"esquema":1}'),
            ("outputs/informe.md", "lo que exporto el usuario" * 50),
            ("backups/proyecto/journal.json", "un respaldo suyo" * 50)):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(texto, encoding="utf-8")
    return root


def _huella(root: Path) -> set:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


# ============================================================================
# G4.5 — enumerar antes de borrar
# ============================================================================
def test_el_inventario_dice_que_es_cada_cosa_y_cuanto_ocupa(bootstrap, instalado):
    inv = bootstrap.inventario(instalado)

    assert inv["exists"] is True
    por_nombre = {e["name"]: e for e in inv["entries"]}
    assert por_nombre["1.5.5"]["kind"] == "runtime"
    assert por_nombre[".previous-1.5.4-9-abc"]["kind"] == "runtime-anterior"
    assert por_nombre["outputs"]["kind"] == "datos-del-usuario"
    assert por_nombre["backups"]["user_data"] is True
    assert por_nombre["1.5.5"]["user_data"] is False
    assert all(e["bytes"] > 0 for e in inv["entries"]), inv
    assert inv["total_bytes"] == sum(e["bytes"] for e in inv["entries"])
    assert 0 < inv["user_bytes"] < inv["total_bytes"]


def test_sin_confirmar_no_se_borra_ni_un_archivo(bootstrap, instalado):
    """El seco es el DEFECTO. Es toda la diferencia entre un susto y una pérdida."""
    antes = _huella(instalado)

    plan = bootstrap.desinstalar(instalado)

    assert plan["confirmed"] is False
    assert plan["would_remove"], "no dijo que borraria"
    assert plan["freed_bytes"] > 0
    assert _huella(instalado) == antes, "borro algo sin que nadie confirmara"


def test_lo_mismo_para_purge(bootstrap, instalado):
    antes = _huella(instalado)
    plan = bootstrap.desinstalar(instalado, incluir_datos=True)
    assert plan["confirmed"] is False
    assert "outputs" in plan["would_remove"] and "backups" in plan["would_remove"]
    assert _huella(instalado) == antes


# ============================================================================
# G4.4 — solo queda lo que se eligió conservar
# ============================================================================
def test_desinstalar_deja_los_datos_del_usuario_y_nada_mas(bootstrap, instalado):
    r = bootstrap.desinstalar(instalado, confirmado=True)

    queda = _huella(instalado)
    assert queda == {str(Path("outputs/informe.md")),
                     str(Path("backups/proyecto/journal.json"))}, queda
    assert set(r["removed"]) >= {"1.5.5", ".previous-1.5.4-9-abc",
                                 "runtime-state.json"}
    assert sorted(r["kept"]) == ["backups", "outputs"]
    assert r["residual_bytes"] == r["user_bytes"], (
        "lo que queda no es exactamente lo del usuario")


def test_purge_se_lleva_tambien_los_datos_cuando_se_pide(bootstrap, instalado):
    r = bootstrap.desinstalar(instalado, incluir_datos=True, confirmado=True)
    assert _huella(instalado) == set()
    assert r["residual_bytes"] == 0
    assert r["kept"] == []


def test_desinstalar_dos_veces_no_falla(bootstrap, instalado):
    bootstrap.desinstalar(instalado, confirmado=True)
    r = bootstrap.desinstalar(instalado, confirmado=True)
    assert r["confirmed"] is True
    assert r["residual_bytes"] == r["user_bytes"]


def test_sobre_una_ruta_que_no_existe_lo_dice_en_vez_de_reventar(bootstrap, tmp_path):
    r = bootstrap.desinstalar(tmp_path / "no-existe")
    assert r["exists"] is False
    assert r["removed"] == []


# ============================================================================
# Contención y concurrencia: borrar es la operación que menos perdona
# ============================================================================
def test_no_se_borra_nada_fuera_del_data_root(bootstrap, instalado, tmp_path):
    """Se recorren los HIJOS del root y se borra por nombre, como la promoción."""
    hermana = tmp_path / "carpeta-ajena"
    hermana.mkdir()
    (hermana / "importante.txt").write_text("de otro", encoding="utf-8")

    bootstrap.desinstalar(instalado, incluir_datos=True, confirmado=True)

    assert (hermana / "importante.txt").read_text(encoding="utf-8") == "de otro"


def test_no_se_desinstala_mientras_otro_proceso_instala(bootstrap, instalado):
    """Borrar mientras alguien publica deja al instalador escribiendo en el aire."""
    ajeno = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    try:
        lock = bootstrap.paths(instalado)["lock"]
        lock.write_text(json.dumps({
            "pid": ajeno.pid, "token": "del-dueno", "started": time.time(),
            "proc_creado": bootstrap._cerrojos.creacion_de_proceso(ajeno.pid)}),
            encoding="utf-8")
        antes = _huella(instalado)

        r = bootstrap.desinstalar(instalado, confirmado=True)

        assert r.get("error"), "desinstalo con el cerrojo en manos de otro"
        assert "en curso" in r["error"].lower()
        assert _huella(instalado) == antes
    finally:
        ajeno.kill()
        ajeno.wait(timeout=30)


# ============================================================================
# La CLI, que es por donde entra una persona
# ============================================================================
def _cli(*args, cwd=RAIZ):
    return subprocess.run(
        [sys.executable, str(RAIZ / "scripts" / "plugin_bootstrap.py"), *args],
        capture_output=True, text=True, timeout=300, cwd=str(cwd))


def test_la_cli_enumera_sin_borrar(instalado):
    antes = _huella(instalado)
    r = _cli("--data-dir", str(instalado), "--purge")

    assert r.returncode == 0, r.stderr[-400:]
    salida = json.loads(r.stdout)
    assert salida["confirmed"] is False
    assert "no se ha borrado nada" in salida["note"].lower()
    assert _huella(instalado) == antes


def test_la_cli_borra_solo_con_confirm(instalado):
    r = _cli("--data-dir", str(instalado), "--uninstall", "--confirm")
    assert r.returncode == 0, r.stderr[-400:]
    salida = json.loads(r.stdout)
    assert salida["confirmed"] is True
    assert sorted(salida["kept"]) == ["backups", "outputs"]


def test_el_runbook_ya_no_dice_que_no_existen():
    """El runbook decia «no existe un comando de desinstalacion». Ya existe."""
    texto = (RAIZ / "docs" / "RUNBOOK_INSTALACION.md").read_text(encoding="utf-8")
    assert "--uninstall" in texto and "--purge" in texto, (
        "el runbook no menciona los comandos que ahora si existen")
