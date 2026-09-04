"""Regresiones de la auditoria de escrituras y atomicidad."""
from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.pbip import backup, model_edit, pbix_to_pbip, project_locator, tmdl_reader
from horizun_pbi_mcp.powerbi.errors import BackupError, PathSecurityError
from horizun_pbi_mcp.services import project_publish, txn, workflows
from tests.fixtures import synthetic


def test_rename_dax_omite_strings_comentarios_y_escapa_cierre():
    dax = (
        '[Vieja] & "etiqueta [Vieja]" // [Vieja]\n'
        " + [VIEJA] -- [Vieja]\n"
        " + /* [Vieja] */ [Vieja] + Fact[Vieja] + 'Fact'[Vieja]"
    )
    cambiado, cuantos = workflows._reemplazar_ref_dax(
        dax, "Vieja", "Nueva]Final")

    assert cuantos == 3
    assert cambiado.count("[Nueva]]Final]") == 3
    assert '"etiqueta [Vieja]"' in cambiado
    assert "// [Vieja]" in cambiado and "-- [Vieja]" in cambiado
    assert "/* [Vieja] */" in cambiado
    assert "Fact[Vieja]" in cambiado and "'Fact'[Vieja]" in cambiado


def test_rename_dax_reconoce_nombre_viejo_con_cierre_escapado():
    cambiado, cuantos = workflows._reemplazar_ref_dax(
        "[Vieja]]Final] + 1", "Vieja]Final", "Nueva")
    assert (cambiado, cuantos) == ("[Nueva] + 1", 1)


def test_plan_prevalida_lote_sin_dejar_journal_parcial(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    dentro = project / "a.json"
    dentro.write_text("{}", encoding="utf-8")
    fuera = tmp_path / "outside" / "b.json"
    backups = tmp_path / "backups"
    backups.mkdir()
    operacion = txn.Transaction(project, backups, tool="audit")

    with pytest.raises(PathSecurityError):
        operacion.plan([dentro, fuera])

    assert not operacion.journal_dir.exists()
    assert list(backups.iterdir()) == []


def test_plan_limpia_journal_si_falla_snapshot(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "a.json"
    target.write_text("{}", encoding="utf-8")
    backups = tmp_path / "backups"
    backups.mkdir()
    operacion = txn.Transaction(project, backups, tool="audit")

    monkeypatch.setattr(txn.shutil, "copy2",
                        lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        operacion.plan([target])
    assert not operacion.journal_dir.exists()


def test_rollback_revalida_contencion_antes_de_borrar(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "nuevo.json"
    backups = tmp_path / "backups"
    backups.mkdir()
    operacion = txn.Transaction(project, backups, tool="audit")
    operacion.plan([target])
    operacion.write_bytes(target, b"{}")
    original = txn.safe_paths.assert_still_contained

    def bloquear(base, path, *, kind="ruta"):
        if kind == "objetivo de rollback":
            raise PathSecurityError("junction cambiado")
        return original(base, path, kind=kind)

    monkeypatch.setattr(txn.safe_paths, "assert_still_contained", bloquear)
    resultado = operacion.rollback("audit")
    assert resultado["clean"] is False
    assert target.read_bytes() == b"{}"
    assert resultado["by_outcome"]["rollback_failed"]


def test_parche_temporal_mantiene_lock_hasta_restaurar(
        tmp_path, isolated_settings, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "x.json"
    target.write_text('{"old": true}', encoding="utf-8")
    active = SimpleNamespace(project_dir=str(project), report_dir=None,
                             semantic_model_dir=None)
    estado = {"held": False, "salio": False}

    @contextlib.contextmanager
    def lock_falso(*_a, **_k):
        estado["held"] = True
        try:
            yield
        finally:
            estado["held"] = False
            estado["salio"] = True

    monkeypatch.setattr(txn._cerrojo, "exclusion", lock_falso)
    with txn.parche_temporal(active, {target: b'{"new": true}'}, tool="audit"):
        assert estado["held"] is True
        assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert estado == {"held": False, "salio": True}
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}


def test_publish_rollback_restaura_directorios_vacios(
        tmp_path, isolated_settings, monkeypatch):
    target = tmp_path / "Demo"
    vacio = target / "legacy" / "empty"
    vacio.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    stage = project_publish.create_stage(tmp_path)
    (stage / "new.txt").write_text("new", encoding="utf-8")
    monkeypatch.setattr(project_publish, "_assert_existing_target_writable",
                        lambda *_a, **_k: None)
    original_commit = txn.Transaction.commit

    def fallar_commit(self):
        raise OSError("commit inyectado")

    monkeypatch.setattr(txn.Transaction, "commit", fallar_commit)
    with pytest.raises(OSError, match="commit inyectado"):
        project_publish.publish_tree(stage, target, overwrite=True, tool="audit")
    monkeypatch.setattr(txn.Transaction, "commit", original_commit)

    assert vacio.is_dir()
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (target / "new.txt").exists()


def test_verify_backup_rechaza_archivo_no_manifestado(tmp_path):
    root = tmp_path / "backup"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({
        "manifest_version": 1, "algorithm": "sha256", "files": [],
    }), encoding="utf-8")
    (root / "extra.bin").write_bytes(b"not manifested")

    resultado = backup.verify_backup(root)
    assert resultado["clean"] is False
    assert resultado["by_status"] == {"unexpected": 1}


def test_verify_zip_rechaza_archivo_no_manifestado(tmp_path):
    import zipfile

    path = tmp_path / "backup.zip"
    manifest = {"files": []}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("extra.bin", b"x")
        zf.writestr("manifest.json", json.dumps(manifest))
    with pytest.raises(BackupError, match="no coincide exactamente"):
        backup._verify_zip(path, manifest)


def test_convert_many_rechaza_colision_antes_de_convertir(tmp_path, monkeypatch):
    a = tmp_path / "a" / "Demo.pbix"
    b = tmp_path / "b" / "demo.pbix"
    a.parent.mkdir(); b.parent.mkdir()
    a.write_bytes(b"a"); b.write_bytes(b"b")
    llamadas = []
    monkeypatch.setattr(pbix_to_pbip, "convert",
                        lambda *_a, **_k: llamadas.append(True))

    with pytest.raises(pbix_to_pbip.PbixConversionError) as exc:
        pbix_to_pbip.convert_many([a, b], tmp_path / "out", overwrite=True)
    assert llamadas == []
    assert len(exc.value.details["collisions"]) == 1
    assert not (tmp_path / "out").exists()


def test_edicion_pbip_resuelve_nombres_sin_distinguir_mayusculas(
        session, tmp_path, isolated_settings):
    pbip = synthetic.materialize(tmp_path)
    project_locator.open_project(session, str(pbip))
    active = session.require_active_pbip()

    assert tmdl_reader.find_table_file(active, "fAcT").name == "Fact.tmdl"
    resultado = model_edit.set_column_hidden_pbip(
        active, "fAcT", "aMoUnT", hidden=True, do_backup=False)
    assert resultado["changed"] is True
    relacion = model_edit.set_relationship_direction_pbip(
        active, "fAcT", "cAlEnDaR", direction="both", do_backup=False)
    assert relacion["matched"] == 1
