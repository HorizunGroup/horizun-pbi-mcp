"""CORE-004(d) / G1.6 — el validador dejaba su temporal dentro del proyecto.

`pbi_validate_pbip_project` va anotada `read_only`, y `readOnlyHint` es la señal
con la que un cliente decide ejecutar **sin preguntar**. Mientras tanto,
`_ruta_salida_temporal(raiz)` devolvía `report_dir.parent / ".hz_validate_….json"`
— o sea, el directorio del proyecto del usuario.

El archivo se borra después, pero eso no lo arregla del todo:

  - si el proceso muere entre medias, el archivo se queda;
  - el proyecto suele estar en OneDrive o en Git, así que un temporal que vive
    un segundo puede acabar sincronizado o apareciendo en `git status`;
  - y una operación anunciada como *de solo lectura* no debería escribir en el
    árbol del usuario ni un instante.

El gate G1.6 lo dice sin rodeos: cero archivos nuevos bajo el `.Report`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from horizun_pbi_mcp.services import report_validator as rv


@pytest.fixture
def proyecto(tmp_path):
    """Un proyecto con la forma que mira el validador."""
    raiz = tmp_path / "MiInforme"
    report = raiz / "MiInforme.Report"
    (report / "definition").mkdir(parents=True)
    (report / "definition" / "report.json").write_text("{}", encoding="utf-8")
    (raiz / "MiInforme.pbip").write_text("{}", encoding="utf-8")
    return {"raiz": raiz, "report": report}


def _huella(raiz: Path) -> set:
    return {str(p.relative_to(raiz)) for p in raiz.rglob("*")}


def test_el_temporal_no_cae_dentro_del_arbol_del_usuario(proyecto):
    salida = rv._ruta_salida_temporal(proyecto["report"])

    arbol = proyecto["raiz"].resolve()
    assert arbol not in salida.resolve().parents, (
        f"el temporal del validador vive dentro del proyecto: {salida}")


def test_una_validacion_no_deja_ni_un_archivo_nuevo_en_el_proyecto(
        proyecto, monkeypatch):
    """G1.6 literal, con el ciclo completo y el CLI simulado.

    No hace falta Node: lo que se mide es DONDE escribe el envelope, no si el
    validador de Microsoft acierta.
    """
    antes = _huella(proyecto["raiz"])

    monkeypatch.setattr(rv, "estado", lambda: {
        "available": True, "cli_path": "C:/falso/cli.js", "version": "0.1.4",
        "node_version": "v20.0.0"})
    monkeypatch.setattr(rv, "_node", lambda: "C:/falso/node.exe")

    def _falso_run(argumentos, **kw):
        # El CLI escribe su envelope donde le digan con `--out`.
        destino = Path(argumentos[argumentos.index("--out") + 1])
        destino.write_text(json.dumps({
            "result": "passed", "diagnostics": []}), encoding="utf-8")
        return subprocess.CompletedProcess(argumentos, 0, b"", b"")

    monkeypatch.setattr(rv.subprocess, "run", _falso_run)

    rv.validar_informe(proyecto["report"])

    assert _huella(proyecto["raiz"]) == antes, (
        "una operacion anunciada como de solo lectura dejo archivos en el "
        f"proyecto: {sorted(_huella(proyecto['raiz']) - antes)}")


def test_dos_validaciones_a_la_vez_no_comparten_el_temporal(proyecto):
    """Lo que el nombre ya resolvia y no se puede perder al mover la ruta."""
    a = rv._ruta_salida_temporal(proyecto["report"])
    b = rv._ruta_salida_temporal(proyecto["report"])
    assert a != b, "dos validaciones simultaneas se pisarian el envelope"
