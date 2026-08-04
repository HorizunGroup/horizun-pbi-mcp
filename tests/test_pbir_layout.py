"""Pruebas del motor de layout (geometria pura)."""
import pytest

from horizun_pbi_mcp.pbip.layout_engine import compute_layout
from horizun_pbi_mcp.powerbi.errors import ValidationError

CANVAS = {"width": 1280, "height": 720}


def _items(n):
    return [{"visual_id": f"v{i}", "type": "card" if i < 2 else "lineChart"}
            for i in range(n)]


def test_grid_count_and_keys():
    pos = compute_layout(_items(4), "grid", CANVAS, 16)
    assert len(pos) == 4
    for p in pos:
        assert {"visual_id", "x", "y", "width", "height"} <= set(p)


def test_grid_within_canvas():
    pos = compute_layout(_items(5), "grid", CANVAS, 16)
    for p in pos:
        assert p["x"] >= 0 and p["y"] >= 0
        assert p["x"] + p["width"] <= CANVAS["width"] + 1
        assert p["y"] + p["height"] <= CANVAS["height"] + 1


def test_dashboard_all_placed():
    pos = compute_layout(_items(5), "dashboard", CANVAS, 16)
    assert len(pos) == 5


def test_executive_summary_all_placed():
    pos = compute_layout(_items(4), "executive_summary", CANVAS, 16)
    assert len(pos) == 4


def test_custom_positions():
    pos = compute_layout([{"visual_id": "a"}], "custom", CANVAS, 16,
                         {"a": {"x": 1, "y": 2, "width": 3, "height": 4}})
    assert pos[0]["width"] == 3 and pos[0]["x"] == 1


def test_custom_requires_map():
    with pytest.raises(ValidationError):
        compute_layout([{"visual_id": "a"}], "custom", CANVAS, 16)


def test_unknown_layout_raises():
    with pytest.raises(ValidationError):
        compute_layout(_items(2), "inexistente", CANVAS, 16)


def test_empty_items():
    assert compute_layout([], "grid", CANVAS, 16) == []
