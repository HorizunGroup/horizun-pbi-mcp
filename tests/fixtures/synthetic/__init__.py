"""API de los fixtures SINTETICOS versionados de PowerBI-MCP.

Regla del repositorio: aqui NUNCA entra un proyecto real. Estos archivos son
100% inventados (tablas `Fact`/`Calendar`, medidas `TotalAmount`/`Ratio Pct`)
y no contienen datos, nombres comerciales ni informacion de ningun cliente.

Uso tipico en una prueba:

    from tests.fixtures import synthetic

    def test_algo(tmp_path):
        pbip = synthetic.materialize(tmp_path)      # copia mutable
        ...                                          # escribe sin miedo

`materialize()` COPIA el fixture a un directorio temporal. Nunca se muta el
fixture versionado: si una prueba escribiera sobre `minimal/`, el arbol de git
quedaria sucio y las pruebas dejarian de ser reproducibles.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

FIXTURES_DIR = Path(__file__).resolve().parent
MINIMAL_DIR = FIXTURES_DIR / "minimal"
BROKEN_DIR = FIXTURES_DIR / "broken"

#: Nombre del .pbip dentro del fixture minimo.
PBIP_NAME = "Demo.pbip"

#: Ids estables del fixture, para que las pruebas no dependan de descubrimiento.
PAGE_ID = "page01"
PAGE_DISPLAY_NAME = "Pagina Uno"
CARD_TEMPLATE_ID = "tmplcard0000000000"
COLUMN_TEMPLATE_ID = "tmplcol00000000000"

#: Referencias validas del modelo sintetico (existen en el TMDL).
VALID_MEASURE = "TotalAmount"
VALID_MEASURE_2 = "Ratio Pct"
VALID_COLUMN = "Calendar[Year]"
VALID_TABLE = "Fact"

#: Referencias que NO existen: sirven para probar que no se inventan campos.
MISSING_MEASURE = "MedidaQueNoExiste"
MISSING_COLUMN = "TablaFantasma[ColumnaFantasma]"


def materialize(dest_dir: Path, name: str = "minimal") -> Path:
    """Copia un fixture sintetico a `dest_dir` y devuelve la ruta del .pbip.

    `dest_dir` suele ser el `tmp_path` de pytest. La copia es mutable: las
    pruebas de escritura deben trabajar SIEMPRE sobre ella, nunca sobre el
    fixture versionado.
    """
    src = FIXTURES_DIR / name
    if not src.is_dir():
        raise FileNotFoundError(f"No existe el fixture sintetico '{name}' en {FIXTURES_DIR}")
    target = Path(dest_dir) / name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    pbip = target / PBIP_NAME
    if not pbip.exists():
        raise FileNotFoundError(f"El fixture '{name}' no contiene {PBIP_NAME}")
    return pbip


def broken_json(kind: str) -> str:
    """Devuelve el TEXTO de un JSON deliberadamente corrupto.

    `kind`: 'visual' | 'page'. Se entrega como texto (no como ruta) para que la
    prueba lo escriba donde quiera dentro de su copia temporal.
    """
    mapping = {
        "visual": BROKEN_DIR / "corrupt_visual.json",
        "page": BROKEN_DIR / "corrupt_page.json",
    }
    path = mapping.get(kind)
    if path is None:
        raise ValueError(f"kind invalido: '{kind}'. Usa 'visual' o 'page'.")
    return path.read_text(encoding="utf-8")


def traversal_payloads(depth: int = 6) -> list[str]:
    """Cadenas de path traversal para probar que la escritura queda acotada.

    Se generan como DATOS: la prueba que las use debe operar exclusivamente
    dentro de un directorio temporal aislado, nunca sobre rutas reales.
    """
    ups = "/".join([".."] * depth)
    return [
        f"{ups}/FUERA_DEL_PROYECTO",
        f"{ups}\\FUERA_DEL_PROYECTO",
        "../" * depth + "FUERA_DEL_PROYECTO",
        "..",
        "../..",
    ]


def outside_marker_dir(sandbox: Path, name: str = "FUERA_DEL_PROYECTO") -> Path:
    """Crea, DENTRO del sandbox, el directorio 'fuera del proyecto' del escenario.

    Existe para que una prueba de traversal nunca necesite apuntar a una ruta
    real del equipo: el 'afuera' es relativo al proyecto sintetico, pero sigue
    estando contenido en el tmp_path de pytest.
    """
    target = Path(sandbox) / name
    target.mkdir(parents=True, exist_ok=True)
    return target


def find_report_dir(pbip_path: Path) -> Optional[Path]:
    """Carpeta .Report hermana del .pbip materializado."""
    candidates = sorted(Path(pbip_path).parent.glob("*.Report"))
    return candidates[0] if candidates else None


def find_semantic_model_dir(pbip_path: Path) -> Optional[Path]:
    """Carpeta .SemanticModel hermana del .pbip materializado."""
    candidates = sorted(Path(pbip_path).parent.glob("*.SemanticModel"))
    return candidates[0] if candidates else None
