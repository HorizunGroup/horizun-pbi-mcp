"""Regresiones de los gates mínimos de calidad del repositorio."""
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


RAIZ = Path(__file__).resolve().parents[1]


def _pyproject() -> dict:
    return tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))


def test_las_herramientas_de_calidad_son_dependencias_solo_de_test():
    datos = _pyproject()
    runtime = "\n".join(datos["project"]["dependencies"]).lower()
    test = "\n".join(datos["project"]["optional-dependencies"]["test"]).lower()
    for paquete in ("ruff", "mypy", "pytest-cov"):
        assert paquete in test
        assert paquete not in runtime, f"{paquete} no es dependencia del usuario"


def test_el_gate_de_cobertura_no_baja_del_baseline_adoptado():
    assert _pyproject()["tool"]["coverage"]["report"]["fail_under"] >= 85


def test_mypy_cubre_los_bordes_de_seguridad_y_transaccion():
    archivos = set(_pyproject()["tool"]["mypy"]["files"])
    exigidos = {
        "src/horizun_pbi_mcp/tools/risk.py",
        "src/horizun_pbi_mcp/services/paths.py",
        "src/horizun_pbi_mcp/services/recovery.py",
        "src/horizun_pbi_mcp/services/txn.py",
        "src/horizun_pbi_mcp/services/idempotency.py",
        "src/horizun_pbi_mcp/lifecycle/locking.py",
        "src/horizun_pbi_mcp/lifecycle/promotion.py",
    }
    assert exigidos <= archivos


def test_ci_ejecuta_lint_tipos_y_cobertura():
    ci = yaml.safe_load(
        (RAIZ / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    pasos = ci["jobs"]["test"]["steps"]
    comandos = "\n".join(str(p.get("run", "")) for p in pasos)
    assert "python -m ruff check" in comandos
    assert "python -m mypy" in comandos
    assert "--cov=horizun_pbi_mcp" in comandos
