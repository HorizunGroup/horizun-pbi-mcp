"""Fase E3.2 — backend de validacion PBIR de Microsoft.

El validador interno solo mira documentos SUELTOS contra su JSON Schema. No ve
lo que exige mirar el informe entero: un objeto de formato que no existe para
ese tipo de visual, una columna en un rol que solo admite medidas, un tema cuyo
nombre no cuadra con el que referencia `report.json`. Sobre el informe de
referencia el CLI oficial encuentra 44 errores y 12 avisos de esa clase.

Estas pruebas usan un CLI **falso y determinista**: la suite normal no necesita
Node ni red. La prueba contra el CLI real va marcada `live_validator`.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from services import report_validator as rv
from services.report_validator import Diagnostico


# ------------------------------------------------------------- CLI falso -----
def cli_falso(tmp_path: Path, *, salida: str = "", codigo: int = 0,
              stderr: str = "", dormir: float = 0.0,
              escribir_out: bool = True) -> Path:
    """Un `cli.js` de mentira, ejecutado por un `node` de mentira.

    Se escribe como script de Python porque es lo unico que se puede ejecutar
    con garantias en la suite sin depender de Node.
    """
    script = tmp_path / "cli_falso.py"
    script.write_text(
        "import sys, time, json\n"
        f"time.sleep({dormir})\n"
        f"sys.stderr.write({stderr!r})\n"
        "args = sys.argv[1:]\n"
        "destino = None\n"
        "if '--out' in args:\n"
        "    destino = args[args.index('--out') + 1]\n"
        f"salida = {salida!r}\n"
        f"if destino and {escribir_out!r}:\n"
        "    open(destino, 'w', encoding='utf-8').write(salida)\n"
        "    print(json.dumps({'data': {'result': 'resumen'}}))\n"
        "else:\n"
        "    sys.stdout.write(salida)\n"
        f"sys.exit({codigo})\n",
        encoding="utf-8")
    return script


@pytest.fixture
def con_cli_falso(tmp_path, monkeypatch):
    """Sustituye node + cli por el falso, y devuelve un configurador."""
    def _instalar(**kwargs):
        script = cli_falso(tmp_path, **kwargs)
        monkeypatch.setattr(rv, "_node", lambda: sys.executable)
        monkeypatch.setattr(rv, "_version_node", lambda: 20)
        monkeypatch.setattr(rv, "localizar", lambda: script)
        monkeypatch.setattr(rv, "_version_cli", lambda _c: rv.VERSION_REQUERIDA)
        return script
    return _instalar


def envelope(diagnosticos: dict, errores: int, avisos: int = 0) -> str:
    return json.dumps({"data": {"result": "failed" if errores else "passed",
                                "errorCount": errores, "warningCount": avisos,
                                "diagnostics": diagnosticos}})


@pytest.fixture
def informe(tmp_path):
    d = tmp_path / "Demo.Report" / "definition"
    d.mkdir(parents=True)
    return tmp_path / "Demo.Report"


# ============================================== disponibilidad del backend ====
def test_sin_node_no_esta_disponible(monkeypatch):
    monkeypatch.setattr(rv, "_node", lambda: None)
    est = rv.estado()
    assert est["available"] is False
    assert "Node" in est["reason"]


def test_node_antiguo_no_vale(monkeypatch, tmp_path):
    monkeypatch.setattr(rv, "_node", lambda: sys.executable)
    monkeypatch.setattr(rv, "_version_node", lambda: 18)
    monkeypatch.setattr(rv, "localizar", lambda: tmp_path / "cli.js")
    est = rv.estado()
    assert est["available"] is False
    assert "18" in est["reason"]


def test_cli_ausente_no_esta_disponible(monkeypatch):
    monkeypatch.setattr(rv, "_node", lambda: sys.executable)
    monkeypatch.setattr(rv, "_version_node", lambda: 22)
    monkeypatch.setattr(rv, "localizar", lambda: None)
    est = rv.estado()
    assert est["available"] is False
    assert "no esta instalado" in est["reason"]


def test_version_incorrecta_no_vale(monkeypatch, tmp_path):
    script = tmp_path / "cli.js"
    script.write_text("x", encoding="utf-8")
    monkeypatch.setattr(rv, "_node", lambda: sys.executable)
    monkeypatch.setattr(rv, "_version_node", lambda: 22)
    monkeypatch.setattr(rv, "localizar", lambda: script)
    monkeypatch.setattr(rv, "_version_cli", lambda _c: "0.0.1")
    est = rv.estado()
    assert est["available"] is False
    assert est["compatible"] is False
    assert "0.0.1" in est["reason"]


def test_estado_no_relanzar_procesos_si_los_binarios_no_cambiaron(
        monkeypatch, tmp_path):
    cli = tmp_path / "cli.js"
    cli.write_text("x", encoding="utf-8")
    llamadas = {"node": 0, "cli": 0}

    monkeypatch.setattr(rv, "_node", lambda: sys.executable)
    monkeypatch.setattr(rv, "localizar", lambda: cli)

    def version_node():
        llamadas["node"] += 1
        return 22

    def version_cli(_ruta):
        llamadas["cli"] += 1
        return rv.VERSION_REQUERIDA

    monkeypatch.setattr(rv, "_version_node", version_node)
    monkeypatch.setattr(rv, "_version_cli", version_cli)
    rv.invalidate_state_cache()

    assert rv.estado()["available"] is True
    assert rv.estado()["available"] is True
    assert llamadas == {"node": 1, "cli": 1}


def test_no_se_busca_en_el_path(monkeypatch, tmp_path):
    """Un ejecutable ajeno con ese nombre no puede acabar procesando el .pbip."""
    import inspect

    fuente = inspect.getsource(rv.localizar)
    assert "which(" not in fuente, (
        "localizar() no puede buscar en el PATH: un binario ajeno con ese "
        "nombre procesaria el proyecto del usuario")


def test_no_se_ejecuta_npx_ni_latest():
    """Ninguna operacion normal descarga codigo.

    Se mira el CODIGO, no el modulo entero: la docstring explica precisamente
    por que no se usa `npx -y` ni `@latest`, y mencionarlo no es usarlo.
    """
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(rv))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            if ast.get_docstring(arbol) == nodo.value:
                continue
            assert "npx" not in nodo.value, f"literal con npx: {nodo.value[:60]}"
            assert "@latest" not in nodo.value


# ==================================================== salida e interpretacion ==
def test_salida_valida_sin_diagnosticos(con_cli_falso, informe):
    con_cli_falso(salida=envelope({}, 0))
    r = rv.validar_informe(informe)
    assert r.status == rv.PASSED
    assert r.level == rv.NIVEL_COMPLETO
    assert r.errors == 0


def test_solo_avisos(con_cli_falso, informe):
    con_cli_falso(salida=envelope(
        {"PBIR_VISUAL_TYPE_UNKNOWN": {"severity": "warning",
                                      "items": [{"file": "a.json"}]}}, 0, 1))
    r = rv.validar_informe(informe)
    assert r.status == rv.PASSED_WITH_WARNINGS
    assert r.warnings == 1 and r.errors == 0


def test_con_errores(con_cli_falso, informe):
    con_cli_falso(salida=envelope(
        {"PBIR_ROLE_KIND_MISMATCH": {"severity": "error",
                                     "items": [{"file": "b.json"}]}}, 1))
    r = rv.validar_informe(informe)
    assert r.status == rv.FAILED
    assert r.errors == 1


def test_exit_code_de_error_no_manda(con_cli_falso, informe):
    """El CLI real devuelve 0 incluso con result='failed': manda el recuento."""
    con_cli_falso(salida=envelope({}, 0), codigo=3)
    r = rv.validar_informe(informe)
    assert r.status == rv.PASSED, "el exit code no debe decidir el resultado"


def test_salida_corrupta_es_unavailable(con_cli_falso, informe):
    con_cli_falso(salida="{esto no es json")
    r = rv.validar_informe(informe)
    assert r.status == rv.UNAVAILABLE
    assert "no devolvio JSON" in r.detail


def test_sin_salida_es_unavailable(con_cli_falso, informe):
    con_cli_falso(salida="", escribir_out=False)
    r = rv.validar_informe(informe)
    assert r.status == rv.UNAVAILABLE


def test_stderr_inesperado_no_rompe(con_cli_falso, informe):
    con_cli_falso(salida=envelope({}, 0), stderr="aviso ruidoso del CLI\n")
    r = rv.validar_informe(informe)
    assert r.status == rv.PASSED


def test_timeout(con_cli_falso, informe):
    con_cli_falso(salida=envelope({}, 0), dormir=5)
    r = rv.validar_informe(informe, timeout=1)
    assert r.status == rv.TIMEOUT


def test_salida_excesiva_se_rechaza(con_cli_falso, informe, monkeypatch):
    monkeypatch.setattr(rv, "MAX_BYTES_SALIDA", 50)
    con_cli_falso(salida=envelope(
        {"X": {"severity": "error",
               "items": [{"file": f"f{i}.json"} for i in range(200)]}}, 200))
    r = rv.validar_informe(informe)
    assert r.status == rv.UNAVAILABLE


def test_no_queda_el_archivo_temporal(con_cli_falso, informe):
    con_cli_falso(salida=envelope({}, 0))
    rv.validar_informe(informe)
    restos = list(informe.parent.glob(".hz_validate_*.json"))
    assert not restos, f"quedaron temporales: {restos}"


def test_cada_validacion_tiene_una_salida_temporal_propia(informe):
    primera = rv._ruta_salida_temporal(informe)
    segunda = rv._ruta_salida_temporal(informe)
    assert primera != segunda
    assert primera.parent == informe.parent == segunda.parent


def test_ruta_con_espacios(con_cli_falso, tmp_path):
    d = tmp_path / "carpeta con espacios" / "Demo.Report" / "definition"
    d.mkdir(parents=True)
    con_cli_falso(salida=envelope({}, 0))
    r = rv.validar_informe(d.parent)
    assert r.status == rv.PASSED


def test_no_hay_inyeccion_por_la_ruta(con_cli_falso, tmp_path):
    """Sin shell no hay metacaracteres que interpretar."""
    import inspect

    fuente = inspect.getsource(rv.validar_informe)
    assert "shell=True" not in fuente
    assert "shell=False" in fuente

    d = tmp_path / "raro & echo HACKED" / "Demo.Report" / "definition"
    d.mkdir(parents=True)
    con_cli_falso(salida=envelope({}, 0))
    r = rv.validar_informe(d.parent)
    assert r.status == rv.PASSED


def test_las_rutas_personales_no_salen_en_la_respuesta(con_cli_falso, informe):
    """Los diagnosticos traen rutas absolutas; se devuelven relativas."""
    absoluta = str(informe / "definition" / "pages" / "p1" / "visual.json")
    con_cli_falso(salida=envelope(
        {"X": {"severity": "error", "items": [{"file": absoluta}]}}, 1))
    r = rv.validar_informe(informe)

    texto = json.dumps([d.to_dict() for d in r.diagnostics])
    assert str(informe) not in texto
    assert "definition/pages/p1/visual.json" in texto


# ================================================ diagnosticos preexistentes ==
def d(code, sev="error", file="a.json", path=""):
    return Diagnostico(code=code, severity=sev, file=file, path=path)


def test_los_preexistentes_no_se_atribuyen_a_la_operacion():
    antes = [d("PBIR_THEME_FILE_NAME_MISMATCH"), d("PBIR_ROLE_KIND_MISMATCH")]
    c = rv.comparar(antes, list(antes))
    assert c["new_diagnostics"] == []
    assert c["blocks"] is False
    assert c["preexisting_diagnostics"] == 2


def test_un_error_nuevo_bloquea():
    antes = [d("A")]
    c = rv.comparar(antes, [d("A"), d("B")])
    assert c["new_error_count"] == 1
    assert c["blocks"] is True


def test_mas_errores_del_mismo_codigo_bloquean():
    c = rv.comparar([d("A")], [d("A"), d("A")])
    assert c["blocks"] is True


def test_el_mismo_error_en_otro_archivo_bloquea():
    """Mismo codigo y mismo recuento, pero se movio: es otro defecto."""
    c = rv.comparar([d("A", file="uno.json")], [d("A", file="otro.json")])
    assert c["new_error_count"] == 1
    assert c["blocks"] is True


def test_el_mismo_error_en_otra_ruta_json_bloquea():
    c = rv.comparar([d("A", path="$.a")], [d("A", path="$.b")])
    assert c["blocks"] is True


def test_un_aviso_nuevo_no_bloquea():
    c = rv.comparar([d("A")], [d("A"), d("W", sev="warning")])
    assert c["new_error_count"] == 0
    assert c["blocks"] is False


def test_resolver_un_error_preexistente_no_bloquea():
    c = rv.comparar([d("A"), d("B")], [d("A")])
    assert c["resolved_count"] == 1
    assert c["blocks"] is False


def test_no_se_comparan_mensajes_humanos():
    """Llevan rutas absolutas y texto variable; compararlos daria falsos."""
    import inspect

    fuente = inspect.getsource(rv.Diagnostico.clave)
    assert "message" not in fuente


# ================================================ integracion transaccional ===
def test_el_envelope_tiene_los_campos_nuevos(con_cli_falso, informe):
    con_cli_falso(salida=envelope({}, 0))
    e = rv.validar_informe(informe).to_envelope()
    for campo in ("validation_backend", "validator_name", "validator_version",
                  "validation_level", "validation_status", "errors",
                  "warnings", "duration_ms"):
        assert campo in e, f"falta {campo}"


def test_sin_backend_el_nivel_lo_dice(monkeypatch, informe):
    monkeypatch.setattr(rv, "_node", lambda: None)
    r = rv.validar_informe(informe)
    assert r.status == rv.UNAVAILABLE
    assert r.level == rv.NIVEL_NINGUNO, (
        "sin backend NO se puede decir que se valido el informe")


# ==================================================== contra el CLI real =====
@pytest.mark.live_validator
@pytest.mark.skipif(not rv.estado()["available"],
                    reason=("El CLI oficial de Microsoft no esta instalado. "
                            "Ejecuta: python scripts/fetch_report_validator.py "
                            "y repite con -m live_validator"))
def test_cli_real_sobre_un_fixture(tmp_path):
    from tests.fixtures import synthetic

    pbip = synthetic.materialize(tmp_path)
    rep = next(p for p in pbip.parent.iterdir() if p.name.endswith(".Report"))
    r = rv.validar_informe(rep)

    assert r.status in (rv.PASSED, rv.PASSED_WITH_WARNINGS, rv.FAILED)
    assert r.validator_version == rv.VERSION_REQUERIDA
    assert r.level == rv.NIVEL_COMPLETO
