"""Regresiones de seguridad descubiertas al auditar el catalogo MCP completo."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from horizun_pbi_mcp.pbip import backup, pbir_writer, project_locator
from horizun_pbi_mcp.services import pbir_edit, plan_contract
from horizun_pbi_mcp.tools import audit_tools, convert_tools, dax_tools, ops_tools, workflow_tools
from horizun_pbi_mcp.tools import _common


class _McpCaptura:
    def __init__(self):
        self.tools = {}

    def tool(self):
        def decorar(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorar


@pytest.mark.parametrize(
    "nombre,servicio",
    [
        ("pbi_build_dashboard", "build_dashboard"),
        ("pbi_build_executive_page", "build_executive_page"),
        ("pbi_build_evm_page", "build_evm_page"),
    ],
)
def test_workflows_que_aplican_usan_guard_mutation(monkeypatch, nombre, servicio):
    """Antes escribian con ``guard`` y un reintento duplicaba la pagina."""
    mcp = _McpCaptura()
    workflow_tools.register(mcp)
    rutas = []

    monkeypatch.setattr(workflow_tools, "_active", lambda: object())
    monkeypatch.setattr(workflow_tools, "_model_data", lambda: {})
    monkeypatch.setattr(
        workflow_tools.workflows, servicio,
        lambda *args, **kwargs: {"dry_run": kwargs["dry_run"]})
    monkeypatch.setattr(
        workflow_tools, "guard",
        lambda fn: rutas.append("read") or fn())
    monkeypatch.setattr(
        workflow_tools, "guard_mutation",
        lambda fn: rutas.append("mutation") or fn())

    argumentos = {"measures": ["M"], "dry_run": False,
                  "request_id": "misma-peticion"}
    if nombre == "pbi_build_dashboard":
        argumentos["name"] = "Pagina"
    salida = mcp.tools[nombre](**argumentos)

    assert salida["dry_run"] is False
    assert rutas == ["mutation"]


def test_lote_de_conversion_parcial_no_puede_ser_exito():
    resultado = {
        "converted": [{"source": "a.pbix", "pages": 1, "visuals": 2,
                       "model_status": "skipped", "warnings": [], "dropped": []}],
        "failed": [{"source": "b.pbix", "error": "x", "message": "fallo"}],
        "total": 2, "ok_count": 1, "failed_count": 1,
    }

    with pytest.raises(convert_tools.BatchConversionPartialError) as exc:
        convert_tools._exigir_lote_completo(resultado)  # noqa: SLF001

    assert exc.value.code == "bulk_partially_applied"
    assert exc.value.details["ok_count"] == 1
    assert exc.value.details["failed_count"] == 1


def test_purga_parcial_no_puede_ser_exito():
    resultado = {
        "deleted": ["journal_1"], "deleted_count": 1,
        "failed": [{"path": "journal_2", "reason": "en uso"}],
    }

    with pytest.raises(ops_tools.PurgePartialError) as exc:
        ops_tools._exigir_purga_completa(resultado)  # noqa: SLF001

    assert exc.value.code == "bulk_partially_applied"
    assert exc.value.details == resultado


def _estado_arbol(raiz: Path):
    return {
        "files": {p.relative_to(raiz).as_posix(): p.read_bytes()
                  for p in raiz.rglob("*") if p.is_file()},
        "dirs": sorted(p.relative_to(raiz).as_posix()
                       for p in raiz.rglob("*") if p.is_dir()),
    }


def test_fallo_al_crear_directorio_obligatorio_revierte_la_pagina(
        session, sample_pbip, monkeypatch):
    """El directorio ``visuals`` se creaba despues de confirmar los JSON."""
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    raiz = Path(active.project_dir)
    antes = _estado_arbol(raiz)

    from horizun_pbi_mcp.services import txn as txn_service

    def falla_directorio(self, target):
        raise OSError(f"no se pudo crear {target}")

    monkeypatch.setattr(txn_service.Transaction, "ensure_directory",
                        falla_directorio)
    with pytest.raises(OSError):
        pbir_writer.create_page(active, "No debe quedar")

    assert _estado_arbol(raiz) == antes


@pytest.mark.parametrize("mode", ["folder", "zip"])
def test_backup_se_publica_solo_despues_de_releerlo(
        session, sample_pbip, mode):
    project_locator.open_project(session, str(sample_pbip))

    salida = backup.backup_project(session, mode=mode)
    destino = Path(salida["backup_path"])

    assert destino.exists()
    assert not list(destino.parent.glob(".hz_backup_tmp_*"))
    if mode == "folder":
        assert backup.verify_backup(destino)["clean"] is True


def test_fallo_de_copia_no_deja_backup_parcial(
        session, sample_pbip, monkeypatch):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    from horizun_pbi_mcp.services import txn as txn_service

    root = txn_service.project_backup_root(active)

    def copia_y_falla(src, dst):
        Path(dst).mkdir(parents=True)
        (Path(dst) / "parcial.txt").write_text("incompleto", encoding="utf-8")
        raise OSError("disco lleno")

    monkeypatch.setattr(backup, "copy_tree", copia_y_falla)
    with pytest.raises(backup.BackupError):
        backup.backup_project(session, mode="folder")

    assert not list(root.glob(".hz_backup_tmp_*"))
    assert not [p for p in root.iterdir() if p.name[0].isdigit()]


@pytest.mark.parametrize(
    "kwargs", [{"mode": "rar"}, {"scope": "cualquier_cosa"}])
def test_backup_rechaza_opciones_ambiguas(session, kwargs):
    with pytest.raises(backup.BackupError):
        backup.backup_project(session, **kwargs)


def test_mutacion_no_omite_un_visual_ilegible(
        session, sample_pbip):
    """Antes el visual corrupto se ignoraba y el resto se confirmaba."""
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    page = Path(active.report_dir) / "definition" / "pages" / "pg1"
    corrupto = page / "visuals" / "corrupto" / "visual.json"
    corrupto.parent.mkdir(parents=True)
    corrupto.write_text("{esto no es json", encoding="utf-8")
    antes = _estado_arbol(Path(active.project_dir))

    from horizun_pbi_mcp.powerbi.errors import ValidationError

    with pytest.raises(ValidationError) as exc:
        pbir_edit.set_visual_z_order(active, "pg1", [])

    assert "No se pudieron leer" in exc.value.message
    assert _estado_arbol(Path(active.project_dir)) == antes


def test_open_desktop_compensa_si_no_puede_seleccionar(monkeypatch):
    mcp = _McpCaptura()
    dax_tools.register(mcp)
    from horizun_pbi_mcp.powerbi import desktop_launcher

    abierto = SimpleNamespace(
        pbix_path="x.pbix", instance={"port": 1234}, desktop_pid=77,
        launched_by_us=True, waited_seconds=1.0)
    cerrados = []
    monkeypatch.setattr(desktop_launcher, "open_pbix", lambda *a, **k: abierto)
    monkeypatch.setattr(desktop_launcher, "close",
                        lambda x: cerrados.append(x) or {"closed": True})
    monkeypatch.setattr(
        dax_tools.desktop_discovery, "select_model",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sin catalogo")))

    salida = mcp.tools["pbi_open_in_desktop"]("x.pbix")

    assert salida["ok"] is False
    assert cerrados == [abierto]


def test_huella_de_plan_incluye_archivos_a_borrar(tmp_path):
    escrito = tmp_path / "nuevo.json"
    borrado = tmp_path / "viejo.json"
    sobre = plan_contract.build_envelope(
        operation="prueba", project_root=tmp_path, payload={},
        affected_files=[plan_contract.archivo_afectado(
            escrito, {}, kind="json", estado_previo="absent")],
        preconditions={"state_fingerprint": "x"},
        expected_effects={"files_deleted": [str(borrado)]})

    assert plan_contract.rutas(sobre) == [escrito, borrado]


def test_inventario_de_paginas_no_oculta_page_json_corrupto(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    page_json = (Path(active.report_dir) / "definition" / "pages" /
                 "pg1" / "page.json")
    page_json.write_text("{roto", encoding="utf-8")

    from horizun_pbi_mcp.powerbi.errors import ValidationError
    from horizun_pbi_mcp.pbip import pbir_reader

    with pytest.raises(ValidationError) as exc:
        pbir_reader.list_pages(active)

    assert exc.value.details["unreadable_pages"][0]["page"] == "pg1"


def test_fallo_del_registro_idempotente_no_convierte_commit_en_error(
        monkeypatch, tmp_path):
    """La escritura principal ya ocurrio; reintentar seria peor."""
    from horizun_pbi_mcp.services import idempotency

    monkeypatch.setattr(idempotency, "store_por_defecto", lambda: object())
    monkeypatch.setattr(
        idempotency, "comenzar_intento",
        lambda _s, rid, *a, **k: idempotency.Intento(request_id=rid,
                                                     attempt_id="intento-1"))
    monkeypatch.setattr(
        idempotency, "terminar_ok",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))

    ejecutadas = []

    def pbi_mutacion(request_id="rid"):
        return _common.guard_mutation(
            lambda: ejecutadas.append(True) or {"applied": 1})

    salida = pbi_mutacion()

    assert salida["ok"] is True
    assert salida["idempotency_persisted"] is False
    assert salida["safe_to_retry"] is False
    assert salida["status"] == "warning"
    assert ejecutadas == [True]


def test_formatos_de_auditoria_se_validan_antes_de_escribir(
        isolated_settings):
    resultado = {"score": 100, "warnings": []}

    with pytest.raises(Exception):
        audit_tools._guardar_formatos(  # noqa: SLF001
            resultado, ["markdown", "formato_inventado"])

    assert not list(isolated_settings.outputs_dir.iterdir())


def test_scaffold_no_confunde_validador_roto_con_validador_ausente(
        tmp_path, monkeypatch):
    from horizun_pbi_mcp.pbip import pbip_scaffold
    from horizun_pbi_mcp.services import report_validator

    monkeypatch.setattr(
        report_validator, "validar_informe",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("CLI roto")))

    with pytest.raises(pbip_scaffold.ScaffoldError):
        pbip_scaffold.crear_proyecto(tmp_path, "NoPublicar")

    assert not (tmp_path / "NoPublicar").exists()
    assert not list(tmp_path.glob(".hz_stage_*"))


def test_validate_project_no_declara_valido_un_report_json_corrupto(
        session, sample_pbip, monkeypatch):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    report_json = Path(active.report_dir) / "definition" / "report.json"
    report_json.write_text("{corrupto", encoding="utf-8")
    from horizun_pbi_mcp.services import report_validator

    monkeypatch.setattr(
        report_validator, "validar_informe",
        lambda *_a, **_k: SimpleNamespace(
            status=report_validator.UNAVAILABLE, detail="sin CLI"))

    salida = project_locator.validate_project(session)

    assert salida["valid"] is False
    assert salida["checks"]["pbir_valid"] is False
    assert salida["report"]["parse_errors"]


def test_modelo_ilegible_no_se_convierte_en_validacion_omitida(
        session, sample_pbip, monkeypatch):
    project_locator.open_project(session, str(sample_pbip))
    from horizun_pbi_mcp import config
    monkeypatch.setattr(config, "_session", session)
    monkeypatch.setattr(
        __import__("horizun_pbi_mcp.pbip.tmdl_reader", fromlist=["x"]),
        "read_semantic_model",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("TMDL roto")))

    from horizun_pbi_mcp.powerbi.errors import ValidationError
    from horizun_pbi_mcp.tools import visual_tools

    with pytest.raises(ValidationError) as exc:
        visual_tools._model_data()  # noqa: SLF001

    assert "modelo autoritativo" in exc.value.message


def test_propiedad_de_medida_no_puede_inyectar_tmdl(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    from horizun_pbi_mcp.pbip import tmdl_writer
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    antes = _estado_arbol(Path(active.project_dir))
    with pytest.raises(ValidationError):
        tmdl_writer.create_measure_pbip(
            active, "Ventas", "Inyectada", "1",
            format_string="0\n\tcolumn Colada")

    assert _estado_arbol(Path(active.project_dir)) == antes


def test_lector_tmdl_no_oculta_una_tabla_ilegible(
        session, sample_pbip):
    project_locator.open_project(session, str(sample_pbip))
    active = session.require_active_pbip()
    tablas = Path(active.semantic_model_dir) / "definition" / "tables"
    (tablas / "Rota.tmdl").write_bytes(b"\xff\xfe\x00")
    from horizun_pbi_mcp.pbip import tmdl_reader
    from horizun_pbi_mcp.powerbi.errors import ValidationError

    with pytest.raises(ValidationError) as exc:
        tmdl_reader.read_semantic_model(active)

    assert exc.value.details["unreadable_tables"]


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), float("-inf")])
def test_posicion_rechaza_numeros_no_json(valor):
    from horizun_pbi_mcp.powerbi.errors import ValidationError
    from horizun_pbi_mcp.utils.validation import validate_position

    with pytest.raises(ValidationError):
        validate_position({"x": valor, "y": 0, "width": 1, "height": 1})


def test_escritor_json_no_emite_nan():
    from horizun_pbi_mcp.utils.json_utils import dumps

    with pytest.raises(ValueError):
        dumps({"x": float("nan")})


def test_nombres_de_salida_no_colisionan_en_el_mismo_segundo():
    from horizun_pbi_mcp.utils.file_utils import timestamp

    assert timestamp() != timestamp()
