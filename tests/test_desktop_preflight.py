"""El launcher debe explicar un PBIP inválido antes de abrir Desktop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import desktop_launcher


def _pbip_con_modelo(tmp_path: Path) -> Path:
    proyecto = tmp_path / "Demo"
    semantico = proyecto / "Demo.SemanticModel" / "definition"
    semantico.mkdir(parents=True)
    (semantico / "model.tmdl").write_text("model Demo\n", encoding="utf-8")
    pbip = proyecto / "Demo.pbip"
    pbip.write_text(
        json.dumps({"artifacts": [{"report": {"path": "Demo.Report"}}]}),
        encoding="utf-8")
    return pbip


def test_pbip_invalido_falla_antes_de_lanzar_desktop(tmp_path, monkeypatch):
    pbip = _pbip_con_modelo(tmp_path)
    expected = [{
        "rule": "tmdl_measure_column_collision",
        "severity": "error",
        "object": {"kind": "measure", "name": "Ejecutado"},
        "evidence": {"column": "Ejecutado"},
    }]

    monkeypatch.setattr(
        desktop_launcher, "_preflight_pbip_model",
        lambda _path: (_ for _ in ()).throw(
            desktop_launcher.DesktopPreflightError(
                "modelo inválido", details={"findings": expected})))
    monkeypatch.setattr(
        desktop_launcher, "find_executable",
        lambda: pytest.fail("no debe lanzar Desktop si falla el preflight"))

    with pytest.raises(desktop_launcher.DesktopPreflightError) as exc:
        desktop_launcher.open_pbix(pbip)

    assert exc.value.code == "desktop_preflight_failed"
    assert exc.value.details["findings"][0]["rule"] == \
        "tmdl_measure_column_collision"


def test_pbip_ya_abierto_se_reutiliza_sin_validar_el_disco(tmp_path, monkeypatch):
    pbip = _pbip_con_modelo(tmp_path)
    instancia = {"port": 54321, "catalog": "Demo", "table_count": 1}
    monkeypatch.setattr(
        desktop_launcher, "proceso_con_archivo_abierto", lambda _path: 888)
    monkeypatch.setattr(
        desktop_launcher, "_instancia_de_proceso", lambda _pid: instancia)
    monkeypatch.setattr(
        desktop_launcher, "_preflight_pbip_model",
        lambda _path: pytest.fail("no debe validar disco al reutilizar Desktop"))

    opened = desktop_launcher.open_pbix(pbip, reuse_open=True)

    assert opened.instance == instancia
    assert opened.desktop_pid == 888
    assert opened.launched_by_us is False


def test_preflight_real_detecta_colision_sin_abrir_desktop(tmp_path):
    pbip = _pbip_con_modelo(tmp_path)
    definition = pbip.parent / "Demo.SemanticModel" / "definition"
    (definition / "tables").mkdir()
    (definition / "tables" / "Avance.tmdl").write_text(
        "table Avance\n\n"
        "\tmeasure Ejecutado = SUM(Avance[Ejecutado])\n\n"
        "\tcolumn Ejecutado\n"
        "\t\tdataType: int64\n"
        "\t\tsourceColumn: Ejecutado\n",
        encoding="utf-8")

    with pytest.raises(desktop_launcher.DesktopPreflightError) as exc:
        desktop_launcher._preflight_pbip_model(pbip)  # noqa: SLF001

    assert any(f["rule"] == "tmdl_measure_column_collision"
               for f in exc.value.details["findings"])


def test_preflight_bloquea_modelo_semantico_vacio_antes_del_timeout(tmp_path):
    proyecto = tmp_path / "Vacio"
    semantic = proyecto / "Vacio.SemanticModel" / "definition"
    semantic.mkdir(parents=True)
    pbip = proyecto / "Vacio.pbip"
    pbip.write_text(json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": "Vacio.Report"}}],
    }), encoding="utf-8")
    (proyecto / "Vacio.Report").mkdir()

    with pytest.raises(desktop_launcher.DesktopPreflightError) as exc:
        desktop_launcher._preflight_pbip_model(pbip)

    assert exc.value.details["rule"] == "tmdl_empty_model"
    assert exc.value.details["findings"][0]["severity"] == "error"
