"""Una rama `release/X.Y.Z` no pasa el CI si los manifiestos no dicen X.Y.Z.

La v2.2.0 se etiqueto sobre un `main` que seguia en 2.1.3 (el commit que subia
la version estaba en un PR sin fusionar). `release_verify` freno la
publicacion a tiempo, pero el error solo aparecio despues de etiquetar. Esta
prueba lo adelanta al PR: en la rama `release/X.Y.Z` todos los sitios de
version tienen que decir X.Y.Z antes de poder fusionar.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
RAMA_RELEASE = re.compile(r"^release/v?(\d+\.\d+\.\d+)$")


def _release_verify():
    sys.path.insert(0, str(RAIZ / "scripts"))
    try:
        import release_verify
    finally:
        sys.path.remove(str(RAIZ / "scripts"))
    return release_verify


def _rama_actual() -> str:
    # En un PR, GITHUB_HEAD_REF es la rama de origen; en un push, GITHUB_REF_NAME.
    for var in ("GITHUB_HEAD_REF", "GITHUB_REF_NAME"):
        if os.environ.get(var):
            return os.environ[var]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=RAIZ,
            capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def version_de_rama(rama: str) -> str | None:
    m = RAMA_RELEASE.match(rama)
    return m.group(1) if m else None


@pytest.mark.parametrize("rama, esperada", [
    ("release/2.2.0", "2.2.0"),
    ("release/v2.2.0", "2.2.0"),
    ("release/2.2.0-firma", None),
    ("main", None),
    ("feat/release/2.2.0", None),
])
def test_la_version_sale_del_nombre_de_la_rama(rama, esperada):
    assert version_de_rama(rama) == esperada


def test_una_version_que_no_coincide_se_rechaza():
    rv = _release_verify()
    with pytest.raises(SystemExit, match="no todos lo declaran"):
        rv.verificar_version("0.0.1")


def test_en_una_rama_release_los_manifiestos_dicen_su_version():
    esperada = version_de_rama(_rama_actual())
    if esperada is None:
        pytest.skip("no es una rama release/X.Y.Z")
    _release_verify().verificar_version(esperada)
