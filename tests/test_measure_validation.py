"""Pruebas de validaciones de entrada y seguridad de rutas."""
import pytest

from horizun_pbi_mcp.powerbi.errors import PathSecurityError, ValidationError
from horizun_pbi_mcp.utils.validation import (
    ensure_within_base,
    tmdl_needs_quote,
    tmdl_quote_name,
    validate_dax_query,
    validate_object_name,
    validate_position,
)


def test_object_name_trims():
    assert validate_object_name("  Ventas  ") == "Ventas"


def test_object_name_empty_raises():
    with pytest.raises(ValidationError):
        validate_object_name("")


def test_object_name_control_char_raises():
    with pytest.raises(ValidationError):
        validate_object_name("mala\x00medida")


def test_position_ok():
    p = validate_position({"x": 0, "y": 1, "width": 10, "height": 20})
    assert p["width"] == 10 and p["height"] == 20


def test_position_missing_key():
    with pytest.raises(ValidationError):
        validate_position({"x": 0, "y": 0, "width": 10})


def test_position_non_positive():
    with pytest.raises(ValidationError):
        validate_position({"x": 0, "y": 0, "width": 0, "height": 5})


def test_tmdl_quote_simple():
    assert tmdl_quote_name("Simple") == "Simple"
    assert not tmdl_needs_quote("Simple")


def test_tmdl_quote_spaces():
    assert tmdl_quote_name("Con Espacio") == "'Con Espacio'"
    assert tmdl_needs_quote("Con Espacio")


def test_tmdl_quote_escapes_apostrophe():
    assert tmdl_quote_name("O'Brien") == "'O''Brien'"


def test_within_base_ok(tmp_path):
    inside = tmp_path / "a" / "b.txt"
    assert ensure_within_base(tmp_path, inside) == inside.resolve()


def test_within_base_escape_raises(tmp_path):
    outside = tmp_path.parent / "otro.txt"
    with pytest.raises(PathSecurityError):
        ensure_within_base(tmp_path, outside)


def test_dax_empty_raises():
    with pytest.raises(ValidationError):
        validate_dax_query("   ")
