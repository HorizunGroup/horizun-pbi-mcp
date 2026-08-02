"""Regresiones de escritura de medidas que antes dejaban TMDL inabrible."""
from pathlib import Path

import pytest

from config import ActivePbip
from pbip import tmdl_writer
from powerbi.errors import MeasureExistsError
from services import tmdl_validate
from tests.fixtures import synthetic


def _active(pbip: Path) -> ActivePbip:
    root = pbip.parent
    return ActivePbip(
        pbip_path=str(pbip),
        project_dir=str(root),
        report_dir=str(root / "Demo.Report"),
        semantic_model_dir=str(root / "Demo.SemanticModel"),
        report_name="Demo",
        has_pbir=True,
        has_tmdl=True,
    )


def _assert_tom_parses(active: ActivePbip) -> None:
    definition = Path(active.semantic_model_dir) / "definition"
    try:
        result = tmdl_validate.parse_with_tom(definition)
    except Exception as exc:  # pragma: no cover - depende del runtime local
        pytest.skip(f"TmdlSerializer no disponible: {exc}")
    assert result["parsed"] is True, result["error"]


def test_medida_duplicada_case_insensitive_se_rechaza_sin_escribir(
        tmp_path, isolated_settings):
    """`TotalAmount` y `totalamount` son el mismo nombre para el motor."""
    active = _active(synthetic.materialize(tmp_path))
    table_file = (Path(active.semantic_model_dir) / "definition" / "tables" /
                  "Fact.tmdl")
    before = table_file.read_bytes()

    with pytest.raises(MeasureExistsError):
        tmdl_writer.create_measure_pbip(
            active, "Fact", "totalamount", "SUM(Fact[Amount])")

    assert table_file.read_bytes() == before
    _assert_tom_parses(active)


def test_nueva_medida_no_separa_doc_comment_del_primer_child(
        tmp_path, isolated_settings):
    """El comentario de TotalAmount debe seguir pegado a TotalAmount."""
    active = _active(synthetic.materialize(tmp_path))
    table_file = (Path(active.semantic_model_dir) / "definition" / "tables" /
                  "Fact.tmdl")

    tmdl_writer.create_measure_pbip(active, "Fact", "Nueva Medida", "1")

    lines = table_file.read_text(encoding="utf-8-sig").splitlines()
    comment = next(i for i, line in enumerate(lines)
                   if "Suma del importe" in line)
    assert lines[comment + 1].strip().startswith("measure TotalAmount =")
    assert next(i for i, line in enumerate(lines)
                if "measure 'Nueva Medida'" in line) < comment
    _assert_tom_parses(active)
