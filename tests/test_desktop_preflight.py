"""El launcher debe explicar un PBIP inválido antes de abrir Desktop."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from powerbi import desktop_launcher


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
