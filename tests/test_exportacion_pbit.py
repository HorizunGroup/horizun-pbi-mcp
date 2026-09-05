"""Exportar una PLANTILLA `.pbit` por el mismo `Guardar como` de Desktop.

`pbi_finalize_delivery` solo entregaba `pbix` aunque el cuadro de guardado
ofrece `.pbit`. La plantilla se produce eligiendo ESE tipo en el cuadro,
atendiendo el dialogo de descripcion que Desktop abre despues y verificando
que el archivo tenga forma de plantilla: informe y definicion, SIN modelo de
datos. Nunca se fabrica quitando `DataModel` de un zip, y nunca se afirma
que lleve datos.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from horizun_pbi_mcp.powerbi import desktop_ui, uia_helper
from horizun_pbi_mcp.powerbi.errors import ValidationError
from horizun_pbi_mcp.services import pbix_export
from tests.test_exportacion_pbix import entorno  # noqa: F401


def _escribir_pbit(ruta: Path, *, con_datos: bool = False) -> None:
    with zipfile.ZipFile(ruta, "w") as zf:
        zf.writestr("Version", "1.28".encode("utf-16-le"))
        zf.writestr("Report/definition/report.json", json.dumps({"a": 1}))
        zf.writestr("Report/definition/pages/pages.json", json.dumps({}))
        zf.writestr("DataModelSchema", json.dumps({"name": "Model"}))
        if con_datos:
            zf.writestr("DataModel", b"\x00" * 64)


class _AdaptadorPlantilla:
    """Un helper que guarda como .pbit y atiende el dialogo de plantilla."""

    def __init__(self, *, con_datos=False, plantilla_aceptada=True):
        self.con_datos = con_datos
        self.plantilla_aceptada = plantilla_aceptada
        self.peticiones = []

    def save_as_completo(self, *, pid, started, destino, extension=".pbix",
                         timeout=180.0):
        self.peticiones.append({"destino": destino, "extension": extension})
        _escribir_pbit(Path(destino), con_datos=self.con_datos)
        return {"file_type_selected": "Archivos de plantilla de Power BI (*.pbit)",
                "commit_method": "invoke", "dialog_closed": True,
                "filename_method": "value_pattern",
                "template_dialog": {"seen": True,
                                    "accepted": self.plantilla_aceptada,
                                    "dialog_closed": self.plantilla_aceptada},
                "steps": [], "modals": []}

    def modales(self, pid, *, excluir=()):
        return []


# ================================ 1) el destino ============================
def test_el_destino_de_una_plantilla_termina_en_pbit(tmp_path):
    pbip = tmp_path / "Demo.pbip"
    assert pbix_export._resolver_destino(pbip, None, "pbit") == \
        tmp_path / "Demo.pbit"                             # noqa: SLF001
    with pytest.raises(ValidationError) as fallo:
        pbix_export._resolver_destino(pbip, str(tmp_path / "x.pbix"), "pbit")  # noqa: SLF001
    assert fallo.value.details["format"] == "pbit"


@pytest.mark.parametrize("formato", ["pbit", ".PBIT", "PbIt"])
def test_el_formato_se_normaliza(formato):
    assert pbix_export.normalizar_formato(formato) == "pbit"


def test_un_formato_desconocido_se_rechaza_con_los_validos():
    with pytest.raises(ValidationError) as fallo:
        pbix_export.normalizar_formato("docx")
    assert fallo.value.details["valid"] == ["pbix", "pbit"]


# ============================= 2) la exportacion ===========================
def test_la_plantilla_se_pide_como_pbit_y_se_verifica_como_plantilla(entorno):  # noqa: F811
    adapter = _AdaptadorPlantilla()
    destino = entorno["tmp"] / "salida" / "Demo.pbit"
    entorno["estado"]["sigue_abierto"] = str(destino)

    salida = pbix_export.export(entorno["session"], adapter=adapter,
                                out_path=str(destino), timeout=5,
                                format="pbit")

    assert adapter.peticiones[0]["extension"] == ".pbit"
    assert salida["saved_as_verified"] is True
    assert salida["output_format"] == "pbit"
    assert salida["output_pbix"].endswith(".pbit")
    assert salida["verification"]["template"] is True
    assert salida["verification"]["has_data_model"] is False
    assert salida["pbix_summary"]["template"] is True
    assert salida["template_dialog"]["accepted"] is True
    assert any("PLANTILLA" in w for w in salida["warnings"]), (
        "la respuesta tiene que decir que NO lleva datos")


def test_un_pbit_con_modelo_de_datos_no_se_entrega_como_plantilla(entorno):  # noqa: F811
    """Si Desktop guardo otra cosa con extension .pbit, no se disimula."""
    destino = entorno["tmp"] / "salida" / "Demo.pbit"
    entorno["estado"]["sigue_abierto"] = str(destino)

    with pytest.raises(pbix_export.PbixExportNotVerified) as fallo:
        pbix_export.export(entorno["session"],
                           adapter=_AdaptadorPlantilla(con_datos=True),
                           out_path=str(destino), timeout=5, format="pbit")

    assert fallo.value.details["reason"] == "template_has_data_model"


def test_el_dialogo_de_plantilla_sin_aceptar_es_un_modal(entorno):  # noqa: F811
    destino = entorno["tmp"] / "salida" / "Demo.pbit"

    with pytest.raises(desktop_ui.DesktopModalError) as fallo:
        pbix_export.export(entorno["session"],
                           adapter=_AdaptadorPlantilla(plantilla_aceptada=False),
                           out_path=str(destino), timeout=5, format="pbit")

    assert fallo.value.details["template_dialog"]["seen"] is True


def test_finalize_delivery_entrega_pbit_de_extremo_a_extremo(entorno):  # noqa: F811
    destino = entorno["tmp"] / "salida" / "Demo.pbit"
    entorno["estado"]["sigue_abierto"] = str(destino)

    salida = pbix_export.finalize_delivery(
        entorno["session"], format="pbit", out_path=str(destino),
        adapter=_AdaptadorPlantilla())

    assert salida["format"] == "pbit"
    assert salida["delivered"] is True


# ========================= 3) el helper y el dialogo =======================
def test_el_helper_atiende_el_dialogo_de_plantilla_y_no_otros(monkeypatch):
    from tests.test_helper_sin_com import _Elemento, _UiaFalso

    class _Uia(_UiaFalso):
        def __init__(self):
            super().__init__()
            self.aceptados = 0

        def por_id(self, raiz, automation_id, tipo):
            if raiz == "elemento-77" and automation_id == uia_helper.AUTOMATION_ID_GUARDAR:
                return _Elemento("Aceptar", automation_id)
            return super().por_id(raiz, automation_id, tipo)

        def invocar(self, elemento):
            self.aceptados += 1
            abiertas.remove(77)
            return "invoke"

    abiertas = [77, 78]
    monkeypatch.setattr(uia_helper, "ventanas_de", lambda pid: [
        {"hwnd": 77, "class": "#32770", "title": "Exportar una plantilla"},
        {"hwnd": 78, "class": "#32770", "title": "Credenciales"}])
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto",
                        lambda h: h in abiertas)
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    uia = _Uia()

    salida = uia_helper._atender_dialogo_de_plantilla(     # noqa: SLF001
        uia, 4321, [22], plazo=2.0)

    assert salida["seen"] is True and salida["accepted"] is True
    assert salida["dialog_closed"] is True
    assert uia.aceptados == 1, "se pulso en el dialogo de credenciales"


def test_sin_dialogo_de_plantilla_se_dice_que_no_se_vio(monkeypatch):
    from tests.test_helper_sin_com import _UiaFalso

    monkeypatch.setattr(uia_helper, "ventanas_de", lambda pid: [])
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    reloj = iter([0.0, 0.0, 5.0, 5.0, 5.0])
    monkeypatch.setattr(uia_helper.time, "monotonic",
                        lambda: next(reloj, 5.0))

    salida = uia_helper._atender_dialogo_de_plantilla(     # noqa: SLF001
        _UiaFalso(), 4321, [22], plazo=1.0)

    assert salida["seen"] is False and salida["accepted"] is False


# ================================ 4) la tool ===============================
def test_la_tool_de_exportacion_acepta_format_con_default_pbix():
    import inspect

    from horizun_pbi_mcp.tools import workflow_tools

    class _Mcp:
        def __init__(self):
            self.tools = {}

        def tool(self):
            def dec(fn):
                self.tools[fn.__name__] = fn
                return fn
            return dec

    mcp = _Mcp()
    workflow_tools.register(mcp)
    firma = inspect.signature(mcp.tools["pbi_export_pbix"])
    assert firma.parameters["format"].default == "pbix"
    assert firma.parameters["project_path"].default == ""
