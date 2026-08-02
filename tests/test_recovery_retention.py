"""Fase F / R5 — recuperar desde un journal y purgar backups con seguridad.

Se podian LISTAR e INSPECCIONAR journals, pero no restaurar: ante una
transaccion que quedo abierta porque el proceso murio, el usuario tenia los
originales delante y ninguna forma de devolverlos a su sitio. Y R5 seguia
abierto porque los backups crecian sin limite.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from pbip import pbir_reader, project_locator
from services import recovery
from services import txn as txn_service
from services.recovery import RecoveryConflict, RecoveryError, UnsafePurgeRoot
from tests.fixtures import synthetic


def huella(project: Path) -> dict:
    return {str(p.relative_to(project)).replace("\\", "/"):
            hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(project.rglob("*")) if p.is_file()}


@pytest.fixture
def proyecto(session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()
    return active, pbip.parent, isolated_settings


def una_edicion(active):
    """Hace un cambio real y devuelve (journal, huella previa)."""
    from services import pbir_edit

    pagina = pbir_reader.list_pages(active)[0]
    r = pbir_edit.rename_page(active, pagina["display_name"], "Renombrada")
    return Path(r["backup"])


# ============================================================ recuperacion ====
def test_preview_no_restaura_nada(proyecto):
    active, raiz, _s = proyecto
    journal = una_edicion(active)
    tras_editar = huella(raiz)

    p = recovery.preview(active, journal)
    assert p["state"] == recovery.RECOVERABLE
    assert p["to_restore"] >= 1
    assert huella(raiz) == tras_editar, "el preview no puede escribir"


def test_sin_confirm_tampoco_restaura(proyecto):
    active, raiz, _s = proyecto
    journal = una_edicion(active)
    tras_editar = huella(raiz)

    r = recovery.recover(active, journal)
    assert r["recovered"] is False
    assert huella(raiz) == tras_editar


def test_recuperacion_devuelve_el_original_byte_a_byte(proyecto):
    active, raiz, _s = proyecto
    antes = huella(raiz)
    journal = una_edicion(active)
    assert huella(raiz) != antes, "la edicion no llego a cambiar nada"

    r = recovery.recover(active, journal, confirm=True)
    assert r["state"] == recovery.RECOVERED
    assert r["verified_byte_for_byte"] is True

    despues = huella(raiz)
    for clave, valor in antes.items():
        assert despues.get(clave) == valor, f"{clave} no volvio a su original"


def test_recrea_los_directorios_eliminados(proyecto):
    """F2: al borrar el ultimo visual desaparece su carpeta."""
    from services import pbir_edit

    active, raiz, _s = proyecto
    pagina = pbir_reader.list_pages(active)[0]
    visual = pbir_reader.list_visuals(active, pagina["name"])[0]
    carpeta = Path(visual["file"]).parent
    antes = huella(raiz)

    r = pbir_edit.delete_visual(active, pagina["display_name"], visual["id"],
                                confirm=True)
    assert not carpeta.exists(), "la carpeta del visual deberia haberse retirado"

    recovery.recover(active, Path(r["backup"]), confirm=True)
    assert carpeta.exists(), "la recuperacion no recreo el directorio padre"
    assert huella(raiz) == antes


def test_no_se_recupera_dos_veces(proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    recovery.recover(active, journal, confirm=True)

    with pytest.raises(RecoveryConflict) as exc:
        recovery.recover(active, journal, confirm=True)
    assert exc.value.details["state"] == recovery.RECOVERED


def test_un_cambio_externo_posterior_es_conflicto(proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)

    plan = recovery.preview(active, journal)
    destino = Path(plan["source_root"]) / plan["files"][0]["path"]
    destino.write_text('{"lo cambio otro": true}', encoding="utf-8")

    p = recovery.preview(active, journal)
    assert p["state"] == recovery.CONFLICT

    with pytest.raises(RecoveryConflict):
        recovery.recover(active, journal, confirm=True)


def test_el_conflicto_se_puede_forzar(proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    plan = recovery.preview(active, journal)
    destino = Path(plan["source_root"]) / plan["files"][0]["path"]
    destino.write_text('{"lo cambio otro": true}', encoding="utf-8")

    r = recovery.recover(active, journal, confirm=True, force_conflict=True)
    assert r["state"] == recovery.RECOVERED


def test_un_respaldo_ausente_es_corrupted(proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    for f in (journal / "files").rglob("*"):
        if f.is_file():
            f.unlink()

    p = recovery.preview(active, journal)
    assert p["state"] == recovery.CORRUPTED
    with pytest.raises(RecoveryError):
        recovery.recover(active, journal, confirm=True)


def test_un_manifiesto_ilegible_es_corrupted(proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    (journal / "manifest.json").write_text("{roto", encoding="utf-8")

    with pytest.raises(RecoveryError) as exc:
        recovery.preview(active, journal)
    assert exc.value.details["state"] == recovery.CORRUPTED


def test_un_journal_de_otro_proyecto_se_rechaza(proyecto, tmp_path):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)

    datos = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    datos["project_id"] = "otroproyecto1"
    (journal / "manifest.json").write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(RecoveryError) as exc:
        recovery.preview(active, journal)
    assert "otro proyecto" in exc.value.message


def test_source_root_del_manifiesto_no_puede_redirigir_la_restauracion(
        proyecto, tmp_path):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    fuera = tmp_path / "destino_ajeno"
    fuera.mkdir()
    victima = fuera / "victima.json"
    victima.write_bytes(b"NO TOCAR")

    manifiesto = journal / "manifest.json"
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    datos["source_root"] = str(fuera)
    manifiesto.write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(RecoveryError) as exc:
        recovery.preview(active, journal)
    assert exc.value.details["state"] == recovery.CORRUPTED
    assert victima.read_bytes() == b"NO TOCAR"


def test_ruta_relativa_del_manifiesto_no_puede_escapar_del_proyecto(
        proyecto, tmp_path):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    fuera = Path(active.project_dir).parent / "fuera_recuperacion"
    fuera.mkdir()
    victima = fuera / "victima.json"
    victima.write_bytes(b"CAMBIO EXTERNO")

    manifiesto = journal / "manifest.json"
    datos = json.loads(manifiesto.read_text(encoding="utf-8"))
    entrada = next(f for f in datos["files"] if f.get("state") == "present")
    respaldo_original = journal / "files" / Path(entrada["path"])
    respaldo_escapado = journal / "fuera_recuperacion" / "victima.json"
    respaldo_escapado.parent.mkdir()
    respaldo_escapado.write_bytes(respaldo_original.read_bytes())
    entrada["path"] = "../fuera_recuperacion/victima.json"
    entrada["written_sha256"] = hashlib.sha256(victima.read_bytes()).hexdigest()
    datos["files"] = [entrada]
    manifiesto.write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(RecoveryError) as exc:
        recovery.recover(active, journal, confirm=True)
    assert exc.value.details["state"] == recovery.CORRUPTED
    assert victima.read_bytes() == b"CAMBIO EXTERNO"


def test_respaldo_con_hash_incorrecto_se_rechaza_antes_de_tocar_el_proyecto(
        proyecto):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    datos = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    entrada = next(f for f in datos["files"] if f.get("state") == "present")
    destino = Path(active.project_dir) / Path(entrada["path"])
    antes = destino.read_bytes()
    respaldo = journal / "files" / Path(entrada["path"])
    respaldo.write_bytes(b"RESPALDO CORRUPTO")

    plan = recovery.preview(active, journal)
    assert plan["state"] == recovery.CORRUPTED
    assert entrada["path"] in plan["corrupt_backups"]
    with pytest.raises(RecoveryError):
        recovery.recover(active, journal, confirm=True)
    assert destino.read_bytes() == antes


def test_un_respaldo_enlace_se_rechaza_aunque_apunte_a_bytes_validos(
        proyecto, tmp_path):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    datos = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    entrada = next(f for f in datos["files"] if f.get("state") == "present")
    respaldo = journal / "files" / Path(entrada["path"])
    externo = tmp_path / "respaldo_externo.bin"
    externo.write_bytes(respaldo.read_bytes())
    respaldo.unlink()
    try:
        respaldo.symlink_to(externo)
    except OSError as exc:
        pytest.skip(f"el equipo no permite crear symlinks: {exc}")

    with pytest.raises(RecoveryError) as exc:
        recovery.preview(active, journal)
    assert exc.value.details["state"] == recovery.CORRUPTED
    assert externo.exists()


def test_un_destino_enlace_no_permite_escribir_fuera_del_proyecto(
        proyecto, tmp_path):
    active, _raiz, _s = proyecto
    journal = una_edicion(active)
    datos = json.loads((journal / "manifest.json").read_text(encoding="utf-8"))
    entrada = next(f for f in datos["files"] if f.get("state") == "present")
    destino = Path(active.project_dir) / Path(entrada["path"])
    externo = tmp_path / "victima_externa.json"
    externo.write_bytes(b"NO TOCAR")
    destino.unlink()
    try:
        destino.symlink_to(externo)
    except OSError as exc:
        pytest.skip(f"el equipo no permite crear symlinks: {exc}")

    with pytest.raises(RecoveryError) as exc:
        recovery.recover(active, journal, confirm=True, force_conflict=True)
    assert exc.value.details["state"] == recovery.CORRUPTED
    assert externo.read_bytes() == b"NO TOCAR"


def test_un_journal_fuera_de_la_raiz_se_rechaza(proyecto, tmp_path):
    active, _raiz, _s = proyecto
    ajeno = tmp_path / "ajeno"
    ajeno.mkdir()
    (ajeno / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RecoveryError):
        recovery.preview(active, ajeno)


# ================================================================= purga ======
def journals_falsos(base: Path, n: int, edad_dias: float = 0):
    creados = []
    for i in range(n):
        d = base / f"journal_{i:03d}"
        (d / "files").mkdir(parents=True)
        (d / "manifest.json").write_text(
            json.dumps({"status": "committed", "tool": "t", "files": []}),
            encoding="utf-8")
        cuando = time.time() - edad_dias * 86400 - i
        os.utime(d, (cuando, cuando))
        creados.append(d)
    return creados


def test_la_purga_es_dry_run_por_defecto(proyecto):
    active, _raiz, settings = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    journals_falsos(base, 5, edad_dias=90)

    r = recovery.purge(active, days=30)
    assert r["dry_run"] is True
    assert r["delete_count"] >= 1
    assert all(Path(c["path"]).exists() for c in r["to_delete"]), (
        "un dry_run no puede borrar")


def test_purga_por_antiguedad(proyecto):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    viejos = journals_falsos(base, 4, edad_dias=90)

    r = recovery.purge(active, days=30, confirm=True)
    assert r["deleted_count"] >= 1
    assert not r["failed"]


def test_siempre_se_conserva_el_mas_reciente(proyecto):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    journals_falsos(base, 3, edad_dias=365)

    recovery.purge(active, days=1, max_journals=0, confirm=True)
    quedan = [d for d in base.iterdir() if (d / "manifest.json").exists()]
    assert len(quedan) >= 1, "se borraron TODOS los journals"


def test_los_pendientes_no_se_borran(proyecto):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    creados = journals_falsos(base, 3, edad_dias=365)

    pendiente = creados[1]
    (pendiente / "manifest.json").write_text(
        json.dumps({"status": "open", "tool": "t", "files": []}),
        encoding="utf-8")
    antiguo = time.time() - 400 * 86400
    os.utime(pendiente, (antiguo, antiguo))

    r = recovery.purge(active, days=1, max_journals=0, confirm=True)
    assert pendiente.exists(), (
        "un journal pendiente guarda los unicos originales de una transaccion "
        "sin cerrar")
    assert str(pendiente) in r["kept_pending"]


def test_no_se_borra_lo_que_no_es_nuestro(proyecto):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    journals_falsos(base, 3, edad_dias=90)

    intruso = base / "carpeta_del_usuario"
    intruso.mkdir()
    (intruso / "importante.txt").write_text("no borrar", encoding="utf-8")
    suelto = base / "notas.txt"
    suelto.write_text("tampoco", encoding="utf-8")

    recovery.purge(active, days=1, max_journals=0, confirm=True)
    assert intruso.exists() and (intruso / "importante.txt").exists()
    assert suelto.exists()


@pytest.mark.parametrize("destino", ["anchor", "home", "project"])
def test_raices_prohibidas(proyecto, destino):
    active, raiz, _s = proyecto
    rutas = {"anchor": Path(Path.cwd().anchor), "home": Path.home(),
             "project": Path(active.project_dir)}
    with pytest.raises(UnsafePurgeRoot):
        recovery.purge_preview(active, root=rutas[destino])


def test_una_raiz_fuera_de_backups_se_rechaza(proyecto, tmp_path):
    active, _raiz, _s = proyecto
    with pytest.raises(UnsafePurgeRoot) as exc:
        recovery.purge_preview(active, root=tmp_path / "cualquier_cosa")
    assert "dentro de la carpeta de backups" in exc.value.message


def test_los_enlaces_simbolicos_no_se_siguen(proyecto, tmp_path):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    journals_falsos(base, 2, edad_dias=90)

    fuera = tmp_path / "fuera_de_backups"
    (fuera / "files").mkdir(parents=True)
    (fuera / "manifest.json").write_text('{"status":"committed","files":[]}',
                                         encoding="utf-8")
    enlace = base / "enlace"
    try:
        enlace.symlink_to(fuera, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("crear enlaces requiere privilegios en este entorno")

    r = recovery.purge_preview(active)
    motivos = {s["reason"] for s in r["skipped"] if s["path"] == str(enlace)}
    assert motivos, "el enlace deberia haberse saltado explicitamente"
    assert fuera.exists(), "no se puede seguir un enlace fuera de backups"


def test_el_manifiesto_de_purga_dice_por_que(proyecto):
    active, _raiz, _s = proyecto
    base = txn_service.project_backup_root(active)
    base.mkdir(parents=True, exist_ok=True)
    journals_falsos(base, 4, edad_dias=90)

    r = recovery.purge_preview(active, days=30)
    assert r["policy"]["days"] == 30
    for c in r["to_delete"]:
        assert c["reason"] in ("antiguedad", "supera el maximo")
        assert "age_days" in c
