"""Fase 1A — transaccion compensada, rollback y destino de backups.

El rollback NO debe pisar un cambio externo posterior a nuestra escritura: esa
es la propiedad central que se comprueba aqui, en sus cuatro variantes
(`restored`, `unchanged`, `rollback_conflict`, `rollback_failed`).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pbip import backup as backup_mod
from services import txn as txn_service
from services.txn import (COMMITTED, RESTORED, ROLLBACK_CONFLICT, ROLLBACK_FAILED,
                          UNCHANGED, BackupDestinationError, RollbackIncompleteError,
                          Transaction, durable_write, fingerprint, transaction)
from tests.fixtures import synthetic


@pytest.fixture
def entorno(tmp_path):
    """Proyecto sintetico + raiz de backups FUERA del proyecto."""
    pbip = synthetic.materialize(tmp_path)
    project = pbip.parent
    backups = tmp_path / "backups"
    backups.mkdir()
    return project, backups


def _visual(project: Path) -> Path:
    return (project / "Demo.Report" / "definition" / "pages" / "page01" /
            "visuals" / synthetic.CARD_TEMPLATE_ID / "visual.json")



#: Los `visual.json` que escriben estas pruebas viven en rutas PBIR reales, y
#: desde la Fase E3.1 se validan contra el esquema OFICIAL antes de escribirse.
#: Escribir `{"a": 1}` ahi ya no es un detalle irrelevante del andamiaje: es un
#: documento invalido. El esquema NO se relaja; se genera contenido valido.
def visual(nombre="x", **extra):
    doc = {
        "$schema": ("https://developer.microsoft.com/json-schemas/fabric/item/"
                    "report/definition/visualContainer/2.7.0/schema.json"),
        "name": nombre,
        "position": {"x": 0, "y": 0, "width": 100, "height": 100},
        "visual": {"visualType": "card"},
    }
    doc.update(extra)
    return doc


def test_validador_disponible_pero_inoperable_bloquea_antes_de_escribir(
        entorno, monkeypatch):
    from services import report_validator as rv

    project, backups = entorno
    target = _visual(project)
    antes = target.read_bytes()
    monkeypatch.setattr(rv, "estado", lambda: {"available": True})
    monkeypatch.setattr(
        rv, "validar_informe",
        lambda _ruta: rv.Resultado(status=rv.UNAVAILABLE,
                                   detail="fallo inyectado"))

    with pytest.raises(rv.ReportValidationFailed):
        with transaction(project, backups, [target], tool="prueba",
                         report_dir=project / "Demo.Report") as tx:
            tx.write_json(target, visual("nuevo"))

    assert target.read_bytes() == antes


def test_validador_que_falla_despues_de_escribir_fuerza_rollback(
        entorno, monkeypatch):
    from services import report_validator as rv

    project, backups = entorno
    target = _visual(project)
    antes = target.read_bytes()
    resultados = iter([
        rv.Resultado(status=rv.PASSED),
        rv.Resultado(status=rv.UNAVAILABLE, detail="fallo post-escritura"),
    ])
    monkeypatch.setattr(rv, "estado", lambda: {"available": True})
    monkeypatch.setattr(rv, "validar_informe", lambda _ruta: next(resultados))

    with pytest.raises(rv.ReportValidationFailed):
        with transaction(project, backups, [target], tool="prueba",
                         report_dir=project / "Demo.Report") as tx:
            tx.write_json(target, visual("nuevo"))

    assert target.read_bytes() == antes


# --------------------------------------------------------- escritura durable ---
def test_durable_write_escribe_y_no_deja_temporal(tmp_path):
    destino = tmp_path / "x.json"
    durable_write(destino, b'{"a":1}')
    assert json.loads(destino.read_text(encoding="utf-8")) == {"a": 1}
    assert list(tmp_path.glob("*.tmp")) == []


def test_temporal_se_limpia_si_falla_el_reemplazo(tmp_path):
    """En Windows, os.replace falla si otro proceso tiene el destino abierto.

    Antes de la Fase 1A eso dejaba un `.tmp` huerfano dentro del .pbip.
    """
    destino = tmp_path / "x.json"
    destino.write_text('{"original":true}', encoding="utf-8")
    retenedor = open(destino, "rb")
    try:
        with pytest.raises(OSError):
            durable_write(destino, b'{"nuevo":true}')
    finally:
        retenedor.close()
    assert list(tmp_path.glob("*.tmp")) == [], "no debe quedar ningun temporal"
    assert json.loads(destino.read_text(encoding="utf-8")) == {"original": True}, \
        "el original debe quedar intacto"


def test_validacion_del_temporal_evita_escribir_basura(tmp_path):
    destino = tmp_path / "x.json"
    destino.write_text('{"original":true}', encoding="utf-8")

    def validador(data: bytes) -> None:
        json.loads(data.decode("utf-8"))

    with pytest.raises(Exception):
        durable_write(destino, b"{esto no es json", validador)
    assert json.loads(destino.read_text(encoding="utf-8")) == {"original": True}
    assert list(tmp_path.glob("*.tmp")) == []


# ------------------------------------------------------- destino de backups ---
def test_backup_dentro_del_proyecto_se_rechaza(entorno):
    project, _ = entorno
    dentro = project / "backups_malos"
    with pytest.raises(BackupDestinationError) as exc:
        txn_service.resolve_backup_root(project, dentro)
    assert "DENTRO del proyecto" in exc.value.message


def test_backup_dentro_del_report_se_rechaza(entorno):
    project, _ = entorno
    report = project / "Demo.Report"
    with pytest.raises(BackupDestinationError):
        txn_service.resolve_backup_root(project, report / "bk",
                                        extra_forbidden=[report])


def test_backup_sin_configurar_falla_de_forma_accionable(entorno):
    project, _ = entorno
    with pytest.raises(BackupDestinationError) as exc:
        txn_service.resolve_backup_root(project, None)
    assert "PBI_MCP_BACKUPS_DIR" in exc.value.message


def test_proyectos_homonimos_no_comparten_backups(tmp_path):
    """Dos `Demo.pbip` en carpetas distintas deben quedar separados."""
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    p1 = synthetic.materialize(a).parent
    p2 = synthetic.materialize(b).parent
    assert p1.name == p2.name, "el escenario exige mismo nombre de carpeta"

    backups = tmp_path / "bk"; backups.mkdir()
    r1 = txn_service.resolve_backup_root(p1, backups)
    r2 = txn_service.resolve_backup_root(p2, backups)
    assert r1 != r2, "proyectos distintos no pueden compartir carpeta de backup"
    assert txn_service.project_id(p1) != txn_service.project_id(p2)


def test_project_id_es_estable(entorno):
    project, _ = entorno
    assert txn_service.project_id(project) == txn_service.project_id(project)
    assert len(txn_service.project_id(project)) == 12


# ----------------------------------------------------------------- commit ---
def test_commit_marca_los_archivos_escritos(entorno):
    project, backups = entorno
    destino = _visual(project)
    with transaction(project, backups, [destino], tool="t") as tx:
        tx.write_json(destino, visual("x"))
    # El resumen queda en el context manager
    assert json.loads(destino.read_text(encoding="utf-8"))["name"] == "x"


def test_escribir_un_archivo_no_planificado_falla(entorno):
    project, backups = entorno
    a = _visual(project)
    b = a.parent / "otro.json"
    with pytest.raises(Exception) as exc:
        with transaction(project, backups, [a], tool="t") as tx:
            tx.write_json(b, visual("b1"))
    assert "no estaba en el plan" in str(exc.value)


# --------------------------------------------------------------- rollback ---
def test_archivo_preexistente_se_restaura_byte_a_byte(entorno):
    project, backups = entorno
    destino = _visual(project)
    original = destino.read_bytes()
    huella_original = fingerprint(destino)

    with pytest.raises(RuntimeError):
        with transaction(project, backups, [destino], tool="t") as tx:
            tx.write_json(destino, visual("pisado"))
            raise RuntimeError("fallo posterior")

    assert destino.read_bytes() == original, "restauracion byte a byte"
    assert fingerprint(destino).matches(huella_original)


def test_archivo_creado_por_la_transaccion_se_elimina(entorno):
    project, backups = entorno
    nuevo = _visual(project).parent / "nuevo.json"
    assert not nuevo.exists()

    with pytest.raises(RuntimeError):
        with transaction(project, backups, [nuevo], tool="t") as tx:
            tx.write_json(nuevo, visual("a1"))
            raise RuntimeError("fallo posterior")

    assert not nuevo.exists(), "lo que creamos nosotros se retira"


def test_archivo_nuevo_modificado_externamente_se_preserva(entorno):
    """Si alguien toca DESPUES lo que creamos, no lo borramos."""
    project, backups = entorno
    nuevo = _visual(project).parent / "nuevo.json"
    txn = Transaction(project, backups, tool="t")
    txn.plan([nuevo])
    txn.write_json(nuevo, visual("a1"))
    nuevo.write_text('{"externo":true}', encoding="utf-8")   # cambio externo

    resumen = txn.rollback(cause="prueba")
    assert nuevo.exists(), "no se elimina un archivo que cambio despues"
    assert json.loads(nuevo.read_text(encoding="utf-8")) == {"externo": True}
    assert resumen["by_outcome"][ROLLBACK_CONFLICT] == [
        txn_service.safe_paths.relative_key(project, nuevo)]
    assert resumen["clean"] is False


def test_archivo_preexistente_modificado_externamente_se_preserva(entorno):
    project, backups = entorno
    destino = _visual(project)
    txn = Transaction(project, backups, tool="t")
    txn.plan([destino])
    txn.write_json(destino, visual("nuestro"))
    destino.write_text('{"externo":true}', encoding="utf-8")  # cambio externo

    resumen = txn.rollback(cause="prueba")
    assert json.loads(destino.read_text(encoding="utf-8")) == {"externo": True}, \
        "no se pisa el cambio externo para fingir atomicidad"
    assert ROLLBACK_CONFLICT in resumen["by_outcome"]
    assert resumen["clean"] is False


def test_archivo_no_escrito_queda_unchanged(entorno):
    project, backups = entorno
    a = _visual(project)
    b = a.parent / "nuevo.json"
    txn = Transaction(project, backups, tool="t")
    txn.plan([a, b])
    txn.write_json(a, visual("x"))
    resumen = txn.rollback(cause="prueba")
    outcomes = {f["path"]: f["outcome"] for f in resumen["files"]}
    assert set(outcomes.values()) == {RESTORED, UNCHANGED}
    assert resumen["clean"] is True


def test_fallo_en_el_segundo_de_tres_revierte_todo(entorno):
    project, backups = entorno
    base = _visual(project).parent
    a, b, c = base / "a.json", base / "b.json", base / "c.json"
    a.write_text('{"orig":"a"}', encoding="utf-8")
    b.write_text('{"orig":"b"}', encoding="utf-8")
    originales = {p: p.read_bytes() for p in (a, b)}

    with pytest.raises(RuntimeError):
        with transaction(project, backups, [a, b, c], tool="t") as tx:
            tx.write_json(a, visual("nuevo_a"))
            tx.write_json(b, visual("nuevo_b"))
            raise RuntimeError("fallo al llegar al tercero")

    assert a.read_bytes() == originales[a]
    assert b.read_bytes() == originales[b]
    assert not c.exists(), "el tercero nunca llego a escribirse"


def test_rollback_sucio_se_reporta_como_incompleto(entorno):
    """Si el rollback no queda limpio, NO se reporta un fallo normal."""
    project, backups = entorno
    destino = _visual(project)

    class Saboteador(RuntimeError):
        pass

    with pytest.raises(RollbackIncompleteError) as exc:
        with transaction(project, backups, [destino], tool="t") as tx:
            tx.write_json(destino, visual("nuestro"))
            destino.write_text('{"externo":true}', encoding="utf-8")
            raise Saboteador("fallo")
    assert exc.value.code == "rollback_incomplete"
    assert "journal" in exc.value.details


def test_falta_el_respaldo_del_journal_marca_rollback_failed(entorno):
    project, backups = entorno
    destino = _visual(project)
    txn = Transaction(project, backups, tool="t")
    txn.plan([destino])
    txn.write_json(destino, visual("nuestro"))
    # Se destruye el respaldo: el rollback ya no puede restaurar.
    for f in txn.files_dir.rglob("*.json"):
        f.unlink()

    resumen = txn.rollback(cause="prueba")
    assert ROLLBACK_FAILED in resumen["by_outcome"]
    assert resumen["clean"] is False


# --------------------------------------------------------------- journal ----
def test_el_journal_conserva_el_original_desde_el_principio(entorno):
    """Interrupcion entre el replace y el cierre del journal.

    Se escribe y NO se hace commit: simula que el proceso muere ahi. El journal
    debe seguir teniendo el original y el manifiesto marcado como abierto.
    """
    project, backups = entorno
    destino = _visual(project)
    original = destino.read_bytes()

    txn = Transaction(project, backups, tool="t")
    txn.plan([destino])
    txn.write_json(destino, visual("nuestro"))
    # ... aqui "muere" el proceso: no hay commit ni rollback.

    manifest = json.loads(txn.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "open"
    assert manifest["manifest_version"] == txn_service.MANIFEST_VERSION
    assert manifest["algorithm"] == "sha256"

    copia = txn.files_dir / txn_service.safe_paths.relative_key(project, destino)
    assert copia.exists() and copia.read_bytes() == original, \
        "el original sigue recuperable desde el journal"


def test_el_manifiesto_registra_hash_y_estado_por_archivo(entorno):
    project, backups = entorno
    a = _visual(project)
    b = a.parent / "nuevo.json"
    with transaction(project, backups, [a, b], tool="t") as tx:
        tx.write_json(a, visual("x"))
    manifest = json.loads(
        (Path(tx.journal_dir) / "manifest.json").read_text(encoding="utf-8"))
    estados = {f["path"]: f for f in manifest["files"]}
    preexistente = [v for v in estados.values() if v["state"] == "present"][0]
    ausente = [v for v in estados.values() if v["state"] == "absent"][0]
    assert preexistente["sha256"] and preexistente["size"] > 0
    assert "sha256" not in ausente
    assert manifest["status"] == "committed"


def test_si_no_se_puede_cerrar_el_manifiesto_el_commit_se_revierte(
        entorno, monkeypatch):
    project, backups = entorno
    destino = _visual(project)
    antes = destino.read_bytes()
    original = txn_service.durable_write

    def fallar_solo_al_cerrar(path, data, validator=None):
        if Path(path).name == "manifest.json" and b'"status": "committed"' in data:
            raise OSError("fallo inyectado al cerrar journal")
        return original(path, data, validator)

    monkeypatch.setattr(txn_service, "durable_write", fallar_solo_al_cerrar)
    with pytest.raises(txn_service.TransactionError,
                       match="persistir el estado"):
        with transaction(project, backups, [destino], tool="t") as tx:
            tx.write_json(destino, visual("nuevo"))

    assert destino.read_bytes() == antes
    manifest = json.loads(Path(tx.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "rolled_back"


# ------------------------------------------------- verificacion de backups ---
def test_verify_backup_detecta_manifiesto_ausente(tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    with pytest.raises(Exception) as exc:
        backup_mod.verify_backup(d)
    assert "manifest.json" in str(exc.value)


def test_verify_backup_detecta_manifiesto_corrupto(tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    (d / "manifest.json").write_text("{esto no es json", encoding="utf-8")
    with pytest.raises(Exception):
        backup_mod.verify_backup(d)


def test_verify_backup_detecta_archivo_alterado(tmp_path):
    d = tmp_path / "bk"
    (d / "sub").mkdir(parents=True)
    contenido = b'{"a":1}'
    (d / "sub" / "x.json").write_bytes(contenido)
    fp = fingerprint(d / "sub" / "x.json")
    (d / "manifest.json").write_text(json.dumps({
        "manifest_version": 1, "algorithm": "sha256",
        "files": [{"path": "sub/x.json", **fp.to_dict()}],
    }), encoding="utf-8")

    assert backup_mod.verify_backup(d)["clean"] is True

    (d / "sub" / "x.json").write_bytes(b'{"a":2}')      # backup corrupto
    res = backup_mod.verify_backup(d)
    assert res["clean"] is False
    assert res["by_status"]["mismatch"] == 1


def test_verify_backup_detecta_archivo_faltante(tmp_path):
    d = tmp_path / "bk"
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({
        "manifest_version": 1, "algorithm": "sha256",
        "files": [{"path": "no_existe.json", "state": "present",
                   "sha256": "0" * 64, "size": 1}],
    }), encoding="utf-8")
    res = backup_mod.verify_backup(d)
    assert res["by_status"]["missing"] == 1
    assert res["clean"] is False


# -------------------------------------------------------- concurrencia ------
def test_cambio_externo_antes_de_escribir_aborta(entorno):
    project, backups = entorno
    destino = _visual(project)
    txn = Transaction(project, backups, tool="t")
    txn.plan([destino])
    destino.write_text('{"cambio":"externo"}', encoding="utf-8")   # entre plan y write

    with pytest.raises(Exception) as exc:
        txn.write_json(destino, visual("nuestro"))
    assert "cambio despues de planificar" in str(exc.value)
    assert json.loads(destino.read_text(encoding="utf-8")) == {"cambio": "externo"}


def test_cambio_externo_en_operacion_multiarchivo_no_deja_exito(entorno):
    project, backups = entorno
    base = _visual(project).parent
    a, b = base / "a.json", base / "b.json"
    a.write_text('{"orig":"a"}', encoding="utf-8")
    b.write_text('{"orig":"b"}', encoding="utf-8")
    original_a = a.read_bytes()

    with pytest.raises(Exception):
        with transaction(project, backups, [a, b], tool="t") as tx:
            tx.write_json(a, visual("nuevo_a"))
            b.write_text('{"externo":true}', encoding="utf-8")   # cambio externo
            tx.write_json(b, visual("nuevo_b"))                     # debe abortar

    assert a.read_bytes() == original_a, "el primero vuelve a su estado"
    assert json.loads(b.read_text(encoding="utf-8")) == {"externo": True}, \
        "el cambio externo se respeta"
