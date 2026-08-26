"""Nunca escoger otro proyecto: una ruta explicita manda, y la duda se dice.

El defecto era literal y silencioso::

    matches = sorted(p.glob("*.pbip"))
    return matches[0]

Con `Antiguo.pbip` y `Nuevo.pbip` en la misma carpeta se abria el primero por
orden alfabetico. Nadie se enteraba: la respuesta salia en verde y las
medidas, las paginas y la publicacion iban al proyecto equivocado. El mismo
patron estaba en los respaldos de `*.Report` y `*.SemanticModel`.
"""
from __future__ import annotations

import json
import shutil

import pytest

from horizun_pbi_mcp.pbip import project_locator
from horizun_pbi_mcp.powerbi.errors import PowerBIMCPError
from horizun_pbi_mcp.services import project_prepare, project_resolver
from tests.fixtures import synthetic


@pytest.fixture
def proyecto(tmp_path, session):
    return synthetic.materialize(tmp_path)


# ============================================ 1) dos .pbip en una carpeta =====
def test_dos_pbip_en_una_carpeta_fallan_por_ambiguedad(proyecto, session):
    carpeta = proyecto.parent
    shutil.copy(proyecto, carpeta / "Antiguo.pbip")

    with pytest.raises(PowerBIMCPError) as exc:
        project_locator.open_project(session, str(carpeta))

    assert exc.value.code == "ambiguous_pbip_project"
    assert sorted(exc.value.details["candidates"]) == ["Antiguo.pbip",
                                                       "Demo.pbip"]


def test_el_error_de_ambiguedad_dice_como_salir(proyecto, session):
    shutil.copy(proyecto, proyecto.parent / "Antiguo.pbip")

    with pytest.raises(PowerBIMCPError) as exc:
        project_locator.open_project(session, str(proyecto.parent))

    assert "ruta exacta" in exc.value.message
    assert exc.value.details["resolved_candidates"]


def test_con_un_solo_pbip_la_carpeta_si_se_resuelve(proyecto, session):
    resumen = project_locator.open_project(session, str(proyecto.parent))

    assert project_resolver.misma_ruta(resumen["pbip_path"], proyecto)


def test_la_ruta_exacta_funciona_aunque_haya_dos(proyecto, session):
    """La ambiguedad es de la CARPETA; el archivo exacto nunca es ambiguo."""
    shutil.copy(proyecto, proyecto.parent / "Antiguo.pbip")

    resumen = project_locator.open_project(session, str(proyecto))

    assert project_resolver.misma_ruta(resumen["pbip_path"], proyecto)


# ================================= 2) varias .Report / .SemanticModel ========
def test_dos_carpetas_report_no_se_resuelven_a_la_primera(proyecto, session):
    """Se rompe el `artifacts` del .pbip para forzar el respaldo."""
    proyecto.write_text(json.dumps({"version": "1.0", "artifacts": []}),
                        encoding="utf-8")
    original = synthetic.find_report_dir(proyecto)
    shutil.copytree(original, proyecto.parent / "AAA.Report")

    with pytest.raises(PowerBIMCPError) as exc:
        project_locator.open_project(session, str(proyecto))

    assert exc.value.code == "ambiguous_pbip_project"
    assert "AAA.Report" in exc.value.details["candidates"]


def test_dos_semantic_model_no_se_resuelven_a_la_primera(proyecto, session):
    original = synthetic.find_semantic_model_dir(proyecto)
    shutil.copytree(original, proyecto.parent / "AAA.SemanticModel")
    # Se rompe la referencia byPath del informe para llegar al respaldo.
    pbir = synthetic.find_report_dir(proyecto) / "definition.pbir"
    datos = json.loads(pbir.read_text(encoding="utf-8-sig"))
    datos["datasetReference"] = {"byConnection": {"connectionString": "x"}}
    pbir.write_text(json.dumps(datos), encoding="utf-8")

    with pytest.raises(PowerBIMCPError) as exc:
        project_locator.open_project(session, str(proyecto))

    assert exc.value.code == "ambiguous_pbip_project"
    assert "AAA.SemanticModel" in exc.value.details["candidates"]


def test_lo_declarado_en_el_pbip_gana_y_no_hay_ambiguedad(proyecto, session):
    """Con `artifacts` intacto no se llega al respaldo: no hay que elegir."""
    original = synthetic.find_report_dir(proyecto)
    shutil.copytree(original, proyecto.parent / "AAA.Report")

    resumen = project_locator.open_project(session, str(proyecto))

    assert resumen["report_dir"].endswith("Demo.Report")


# ============================== 3) el activo no suplanta la ruta explicita ====
def test_un_proyecto_activo_no_suplanta_la_ruta_pedida(tmp_path, session):
    viejo = synthetic.materialize(tmp_path / "viejo")
    nuevo = synthetic.materialize(tmp_path / "nuevo")
    project_locator.open_project(session, str(viejo))

    salida = project_prepare.prepare(session, str(nuevo))

    assert project_resolver.misma_ruta(salida["active_project"], nuevo)
    assert project_resolver.misma_ruta(salida["previous_active_project"], viejo)
    assert salida["path_match"] is True
    assert salida["selection_reason"] == project_resolver.EXPLICIT_FILE


def test_sin_ruta_no_se_cae_al_activo(tmp_path, session):
    """Aqui NO existe el modo 'usa lo que ya habia': es el defecto que mata."""
    viejo = synthetic.materialize(tmp_path / "viejo")
    project_locator.open_project(session, str(viejo))

    with pytest.raises(PowerBIMCPError) as exc:
        project_prepare.prepare(session, "")

    assert exc.value.code == "validation_error"


def test_un_fallo_no_cambia_el_proyecto_activo(tmp_path, session):
    viejo = synthetic.materialize(tmp_path / "viejo")
    project_locator.open_project(session, str(viejo))

    with pytest.raises(PowerBIMCPError):
        project_prepare.prepare(session, str(tmp_path / "no-existe"))

    assert project_resolver.misma_ruta(
        session.require_active_pbip().pbip_path, viejo)


# ============================ 4) convertir activa el pbip de ESA conversion ===
def test_convertir_un_pbix_activa_el_pbip_que_produjo(tmp_path, session):
    from tests.test_pbix_convert import _escribir_pbix, _layout

    # Un .pbip que YA estaba en la carpeta de destino y se llama antes por
    # orden alfabetico: es el que un `glob` habria elegido.
    destino = tmp_path / "salida"
    senuelo = synthetic.materialize(destino / "AAA")
    assert senuelo.exists()

    origen = _escribir_pbix(tmp_path / "Nuevo.pbix", layout=_layout())
    salida = project_prepare.prepare(session, str(origen),
                                     out_dir=str(destino),
                                     include_model=False)

    assert salida["converted"] is True
    assert salida["selection_reason"] == project_resolver.CONVERTED_FROM_PBIX
    assert salida["active_project"].endswith("Nuevo.pbip")
    assert project_resolver.misma_ruta(
        salida["active_project"], salida["conversion"]["pbip_path"])
    # Y NO el senuelo, que alfabeticamente iba primero.
    assert "AAA" not in salida["active_project"]


def test_al_convertir_path_match_es_falso_y_se_dice(tmp_path, session):
    """Se pidio un .pbix y se activa un .pbip: no es lo mismo, y se declara."""
    from tests.test_pbix_convert import _escribir_pbix, _layout

    origen = _escribir_pbix(tmp_path / "Informe.pbix", layout=_layout())
    salida = project_prepare.prepare(session, str(origen),
                                     out_dir=str(tmp_path / "out"),
                                     include_model=False)

    assert salida["path_match"] is False
    assert salida["requested_path"].endswith("Informe.pbix")
    assert salida["source_pbix"].endswith("Informe.pbix")
    assert salida["resolved_path"].endswith("Informe.pbip")


# ================== 5) mismo basename en carpetas distintas no se confunde ====
def test_dos_proyectos_con_el_mismo_nombre_no_son_el_mismo(tmp_path, session):
    a = synthetic.materialize(tmp_path / "a")
    b = synthetic.materialize(tmp_path / "b")
    assert a.name == b.name                    # mismo basename, otra carpeta

    assert project_resolver.misma_ruta(a, b) is False

    project_locator.open_project(session, str(a))
    salida = project_prepare.prepare(session, str(b))

    assert project_resolver.misma_ruta(salida["active_project"], b)
    assert not project_resolver.misma_ruta(salida["active_project"], a)


def test_una_plantilla_pbit_no_se_prepara(tmp_path, session):
    plantilla = tmp_path / "Modelo.pbit"
    plantilla.write_bytes(b"PK\x03\x04no-es-un-proyecto")

    with pytest.raises(PowerBIMCPError) as exc:
        project_prepare.prepare(session, str(plantilla))

    assert exc.value.code == "project_prepare_failed"
    assert "PLANTILLA" in exc.value.message


def test_una_carpeta_con_dos_pbix_tampoco_se_resuelve_sola(tmp_path, session):
    from tests.test_pbix_convert import _escribir_pbix, _layout

    carpeta = tmp_path / "entrada"
    carpeta.mkdir()
    _escribir_pbix(carpeta / "Uno.pbix", layout=_layout())
    _escribir_pbix(carpeta / "Dos.pbix", layout=_layout())

    with pytest.raises(PowerBIMCPError) as exc:
        project_prepare.prepare(session, str(carpeta))

    assert exc.value.code == "ambiguous_pbip_project"
    assert sorted(exc.value.details["candidates"]) == ["Dos.pbix", "Uno.pbix"]


# ======================================================== la clasificacion ====
def test_la_tool_nueva_no_es_de_solo_lectura():
    from horizun_pbi_mcp.tools import risk

    assert risk.RISK_BY_TOOL["pbi_prepare_project"] == risk.WRITE_REVERSIBLE
    assert risk.annotations_for("pbi_prepare_project")["readOnlyHint"] is False


# ============= 5-bis) el TITULO de una ventana no identifica una carpeta =====
class _ProcFalso:
    def __init__(self, pid, argumentos):
        self.pid = pid
        self._argumentos = argumentos

    def cmdline(self):
        return list(self._argumentos)


def test_una_ventana_de_otro_proyecto_con_el_mismo_nombre_no_cuenta(
        tmp_path, monkeypatch):
    """Dos `Demo.pbip` en carpetas distintas producen la MISMA ventana.

    Sin esto, abrir el proyecto de una carpeta reutilizaba la ventana del
    proyecto de la otra -mismo titulo, otro archivo- y todo lo que viniera
    despues iba al sitio equivocado. Lo descubrio la propia suite: una
    ventana suelta titulada `Demo` hizo fallar una prueba de preflight.
    """
    from horizun_pbi_mcp.powerbi import desktop_launcher

    mio = synthetic.materialize(tmp_path / "mio")
    ajeno = synthetic.materialize(tmp_path / "ajeno")
    assert mio.name == ajeno.name

    import psutil

    monkeypatch.setattr(psutil, "Process",
                        lambda pid: _ProcFalso(pid, ["PBIDesktop.exe",
                                                     str(ajeno)]))

    assert desktop_launcher._sirve_otro_proyecto(777, mio) is True
    assert desktop_launcher._sirve_otro_proyecto(777, ajeno) is False


def test_sin_linea_de_comandos_no_se_descarta_a_ciegas(tmp_path, monkeypatch):
    """Muchas ventanas se abren desde la lista de recientes y no traen ruta.

    Ahi no hay nada que demostrar, y descartar por falta de prueba seria
    lanzar una segunda ventana del mismo proyecto.
    """
    from horizun_pbi_mcp.powerbi import desktop_launcher

    mio = synthetic.materialize(tmp_path / "mio")
    import psutil

    monkeypatch.setattr(psutil, "Process",
                        lambda pid: _ProcFalso(pid, ["PBIDesktop.exe"]))

    assert desktop_launcher._sirve_otro_proyecto(777, mio) is False
