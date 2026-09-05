"""INSTALL-005 — `pip install` deja un servidor que arranca y no puede trabajar.

El wheel lleva el paquete y el **manifiesto** de esquemas. No lleva las DLL de
Analysis Services ni los esquemas PBIR, y la razón es legítima: las DLL son
binarios de Microsoft y los esquemas no declaran permiso de redistribución. El
defecto no es la restricción, es que **nadie lo dice**.

Quien instala por `pip` obtiene un servidor que supera el handshake MCP y
anuncia sus 134 tools. La capa EN VIVO no funciona —sin DLL— y la escritura PBIR
falla en la primera llamada —sin esquemas—, y ninguna de las dos cosas aparece
hasta que se intenta usarlas. `pbi_health_check` miraba las DLL y **no miraba
los esquemas en absoluto**.

Lo que se exige aquí es la mitad que faltaba del criterio de cierre: que la
respuesta distinga *instalado* de *operativo* y que, por cada pieza que falte,
diga **el comando exacto** que la completa. Un diagnóstico que dice «falta algo»
sin decir qué hacer no ahorra el viaje a la documentación.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from horizun_pbi_mcp.tools import ops_tools


class _Mcp:
    """Recolector mínimo: `register` decora, y aquí nos quedamos la función."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def health(isolated_settings, monkeypatch, tmp_path):
    """`pbi_health_check` sobre un entorno donde falta TODO lo descargable.

    `isolated_settings` ya apunta `libs_dir` a un `tmp_path` vacío —o sea, sin
    DLL—; aquí se apunta además la caché de esquemas a otro directorio vacío y
    se declara el validador ausente. Es exactamente la forma de una instalación
    por `pip` recién hecha.
    """
    vacio = tmp_path / "sin-esquemas"
    vacio.mkdir()
    monkeypatch.setenv("HORIZUN_PBI_MCP_SCHEMAS_DIR", str(vacio))

    from horizun_pbi_mcp.services import report_validator

    monkeypatch.setattr(report_validator, "estado", lambda: {
        "available": False, "reason": "el CLI no esta instalado"})

    mcp = _Mcp()
    ops_tools.register(mcp)
    return mcp.tools["pbi_health_check"]


def _carga(salida):
    """La tool devuelve un envelope; lo que se juzga es su `result`."""
    return salida["result"] if isinstance(salida, dict) and "result" in salida else salida


# ============================================================================
def test_una_instalacion_incompleta_no_se_declara_operativa(health):
    datos = _carga(health())

    assert "completeness" in datos, (
        "la respuesta no distingue «instalado» de «operativo»: quien instala "
        "por pip ve un servidor que arranca y no puede trabajar")
    assert datos["completeness"]["state"] == "incomplete", datos["completeness"]


def test_dice_QUE_falta_y_no_solo_que_algo_falta(health):
    faltan = {m["component"] for m in _carga(health())["completeness"]["missing"]}

    assert "analysis_services_dlls" in faltan, faltan
    assert "pbir_schemas" in faltan, (
        f"los esquemas PBIR no se comprobaban en absoluto: {faltan}")


def test_cada_pieza_que_falta_trae_el_comando_que_la_completa(health):
    """Un diagnostico que no dice que hacer manda a la documentacion."""
    for pieza in _carga(health())["completeness"]["missing"]:
        assert pieza.get("fix"), f"{pieza['component']} no dice como completarse"
        assert pieza.get("impact"), (
            f"{pieza['component']} no dice que deja de funcionar sin el")


def test_el_comando_que_se_ofrece_EXISTE_donde_se_da_el_diagnostico(health):
    """INSTALL-005, y esta prueba afirmaba lo contrario.

    Exigia `scripts/` en el comando, o sea que codificaba el defecto: quien
    instala por `pip` **no tiene** `scripts/`, y se le estaba diciendo que
    ejecutara un archivo que no existe en su maquina. El diagnostico y su
    remedio tienen que viajar juntos.

    El oraculo es `pyproject.toml`: si el comando no esta declarado como
    `console_script`, no lo tendra quien instale del wheel, por mucho que aqui
    funcione desde el clon.
    """
    raiz = Path(__file__).resolve().parent.parent
    pyproject = (raiz / "pyproject.toml").read_text(encoding="utf-8")
    declarados = {l.split("=")[0].strip()
                  for l in pyproject.split("[project.scripts]", 1)[1]
                  .split("[", 1)[0].splitlines()
                  if "=" in l and not l.strip().startswith("#")}

    for pieza in _carga(health())["completeness"]["missing"]:
        comando = pieza["fix"].split()[0]
        assert comando in declarados, (
            f"{pieza['component']} ofrece `{comando}`, que no esta en "
            f"[project.scripts]: quien instale por pip no lo tendra. "
            f"Declarados: {sorted(declarados)}")


def test_lo_opcional_se_distingue_de_lo_obligatorio(health):
    """El validador PBIR es opcional (INSTALL-002): faltar no es lo mismo."""
    piezas = {m["component"]: m for m in _carga(health())["completeness"]["missing"]}

    assert piezas["analysis_services_dlls"]["required"] is True
    assert piezas["pbir_schemas"]["required"] is True
    if "report_validator" in piezas:
        assert piezas["report_validator"]["required"] is False, (
            "un componente que el producto declara prescindible no puede "
            "presentarse como obligatorio")


def test_el_estado_es_serializable_y_no_lleva_rutas_del_usuario(health):
    """Va a un log y a una conversacion con un agente: JSON y sin rutas."""
    completo = _carga(health())["completeness"]
    json.dumps(completo)                      # no lanza
    texto = json.dumps(completo)
    assert "C:\\Users\\" not in texto and "/Users/" not in texto, texto


def test_healthy_sigue_significando_lo_que_significaba(health):
    """`completeness` se añade AL LADO de `healthy`, no lo sustituye.

    Son dos preguntas distintas —¿el servidor esta sano? ¿puede trabajar?— y
    fundirlas en una sola bandera fue el origen del hallazgo.
    """
    datos = _carga(health())
    assert "healthy" in datos and "checks" in datos
    assert isinstance(datos["healthy"], bool)


def test_con_todo_presente_se_declara_operativa(isolated_settings, monkeypatch,
                                                tmp_path):
    """Contener no puede significar decir siempre que falta algo."""
    libs = tmp_path / "libs"
    libs.mkdir()
    for n in ("Microsoft.AnalysisServices.dll", "Microsoft.AnalysisServices.Core.dll",
              "Microsoft.AnalysisServices.AdomdClient.dll",
              "Microsoft.AnalysisServices.Tabular.dll"):
        (libs / n).write_bytes(b"dll")
    esquemas = tmp_path / "esquemas"
    esquemas.mkdir()
    for n in range(6):
        (esquemas / f"schema{n}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(isolated_settings, "libs_dir", libs, raising=False)
    monkeypatch.setenv("HORIZUN_PBI_MCP_SCHEMAS_DIR", str(esquemas))
    from horizun_pbi_mcp.services import report_validator

    monkeypatch.setattr(report_validator, "estado", lambda: {"available": True})

    mcp = _Mcp()
    ops_tools.register(mcp)
    datos = _carga(mcp.tools["pbi_health_check"]())

    assert datos["completeness"]["state"] == "operational", datos["completeness"]
    assert datos["completeness"]["missing"] == []
