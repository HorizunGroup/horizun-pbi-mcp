"""Regresiones del alcance por tabla de las medidas en vivo."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from powerbi import model_writer
from powerbi.errors import MeasureNotFoundError


class FakeMeasure:
    def __init__(self, name: str, expression: str):
        self.Name = name
        self.Expression = expression
        self.FormatString = ""
        self.DisplayFolder = ""
        self.Description = ""


class FakeMeasures(list):
    def Find(self, name: str):  # noqa: N802 - imita la API TOM
        wanted = name.casefold()
        return next((m for m in self if m.Name.casefold() == wanted), None)

    def Remove(self, measure):  # noqa: N802 - imita la API TOM
        self.remove(measure)


class FakeTable:
    def __init__(self, name: str, *measures: FakeMeasure):
        self.Name = name
        self.Measures = FakeMeasures(measures)


class FakeModel:
    def __init__(self, *tables: FakeTable):
        self.Tables = list(tables)
        self.save_calls = 0

    def SaveChanges(self):  # noqa: N802 - imita la API TOM
        self.save_calls += 1


class FakeSession:
    def require_active_model(self):
        return object()


@pytest.fixture
def live_model(monkeypatch):
    measure = FakeMeasure("Total", "1")
    source = FakeTable("Ventas", measure)
    requested = FakeTable("Presupuesto")
    model = FakeModel(source, requested)

    @contextmanager
    def fake_connect(_model):
        yield object(), object(), model

    monkeypatch.setattr(model_writer, "connect", fake_connect)
    return model, source, requested, measure


def test_update_no_toca_medida_homonima_de_otra_tabla(live_model):
    model, _source, _requested, measure = live_model

    with pytest.raises(MeasureNotFoundError) as exc:
        model_writer.update_measure(
            FakeSession(), "Presupuesto", "Total", expression="2")

    assert measure.Expression == "1"
    assert model.save_calls == 0
    assert exc.value.details["table"] == "Presupuesto"
    assert exc.value.details["existing_table"] == "Ventas"


def test_delete_no_borra_medida_homonima_de_otra_tabla(live_model):
    model, source, _requested, measure = live_model

    with pytest.raises(MeasureNotFoundError) as exc:
        model_writer.delete_measure(FakeSession(), "Presupuesto", "Total")

    assert list(source.Measures) == [measure]
    assert model.save_calls == 0
    assert exc.value.details["existing_table"] == "Ventas"


def test_update_en_la_tabla_propietaria_guarda_una_vez(live_model):
    model, _source, _requested, measure = live_model

    result = model_writer.update_measure(
        FakeSession(), "Ventas", "Total", expression="2")

    assert measure.Expression == "2"
    assert model.save_calls == 1
    assert result["table"] == "Ventas"


def test_delete_en_la_tabla_propietaria_guarda_una_vez(live_model):
    model, source, _requested, measure = live_model

    result = model_writer.delete_measure(FakeSession(), "Ventas", "Total")

    assert measure not in source.Measures
    assert model.save_calls == 1
    assert result["table"] == "Ventas"
