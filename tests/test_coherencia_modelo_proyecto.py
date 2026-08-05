"""Los dos estados del servidor pueden divergir, y nadie los cruzaba.

El servidor mantiene `active_model` (lo que Desktop tiene en memoria) y
`active_pbip` (la carpeta en disco). Pueden apuntar a archivos distintos, de
clientes distintos, y todo responde con normalidad porque cada mitad es valida
por separado.

Caso real: `pbi_select_model` servia el modelo de un `.pbix` mientras el
proyecto activo era otro `.pbip`. `pbi_report_capabilities` devolvia los
visuales del segundo. Se estuvo a punto de escribir cuatro paginas en el
informe equivocado y solo se detecto porque el conteo de visuales no cuadraba.

Las dos reglas que se vigilan:

1. **Se bloquea la escritura ante divergencia CONFIRMADA**, igual que se
   bloquea con Desktop abierto —ese precedente ya existia y funciona—.
2. **Nunca se bloquea por no poder comprobar.** `unknown` avisa y deja pasar:
   convertir un permiso denegado en un bloqueo dejaria el servidor inutil en
   cualquier maquina con politicas estrictas. Esa distincion es el corazon
   del diseno y por eso tiene su propio test.
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.services import coherencia


class _Modelo:
    def __init__(self, pid=4242):
        self.pid = pid
        self.port = 51000
        self.catalog = "cat"
        self.workspace = None


class _Proyecto:
    def __init__(self, tmp_path, nombre="Mio"):
        self.project_dir = str(tmp_path / nombre)
        self.pbip_path = str(tmp_path / nombre / f"{nombre}.pbip")
        self.report_dir = str(tmp_path / nombre / f"{nombre}.Report")
        self.semantic_model_dir = None
        self.report_name = nombre


class _Sesion:
    def __init__(self, modelo=None, proyecto=None):
        self.active_model = modelo
        self.active_pbip = proyecto


@pytest.fixture
def proyecto(tmp_path):
    p = _Proyecto(tmp_path)
    (tmp_path / "Mio" / "Mio.Report").mkdir(parents=True)
    (tmp_path / "Mio" / "Mio.pbip").write_text("{}", encoding="utf-8")
    return p


def _con_archivos(monkeypatch, rutas):
    monkeypatch.setattr(coherencia, "_archivos_del_proceso", lambda _pid: rutas)


# ------------------------------------------------------- no hay divergencia ---
def test_sin_los_dos_estados_no_hay_nada_que_comparar(proyecto):
    assert coherencia.check(_Sesion(proyecto=proyecto))["state"] == \
        coherencia.NOT_APPLICABLE
    assert coherencia.check(_Sesion(modelo=_Modelo()))["state"] == \
        coherencia.NOT_APPLICABLE


def test_el_mismo_archivo_se_reconoce(proyecto, monkeypatch):
    _con_archivos(monkeypatch, [proyecto.pbip_path])
    r = coherencia.check(_Sesion(_Modelo(), proyecto))
    assert r["state"] == coherencia.SAME
    assert r["evidence"] == proyecto.pbip_path


def test_un_archivo_bajo_el_report_dir_tambien_cuenta(proyecto, monkeypatch):
    _con_archivos(monkeypatch,
                  [str(__import__("pathlib").Path(proyecto.report_dir)
                       / "definition" / "report.json")])
    assert coherencia.check(_Sesion(_Modelo(), proyecto))["state"] == \
        coherencia.SAME


# ------------------------------------------------------- el caso peligroso ---
def test_otro_archivo_de_power_bi_es_divergencia(proyecto, monkeypatch):
    """El incidente exacto: el modelo sirve un .pbix de otro proyecto."""
    _con_archivos(monkeypatch, [r"C:\otra\ruta\Control_Acceso.pbix"])
    r = coherencia.check(_Sesion(_Modelo(), proyecto))
    assert r["state"] == coherencia.DIFFERENT
    assert "Control_Acceso.pbix" in str(r["evidence"])
    assert "how_to_fix" in r, "un bloqueo sin salida es un callejon"
    assert "pbi_list_desktop_models" in r["how_to_fix"] or \
        "pbi_select_model" in r["how_to_fix"]


def test_la_escritura_se_niega_ante_divergencia_confirmada(proyecto, monkeypatch):
    _con_archivos(monkeypatch, [r"C:\otra\Control_Acceso.pbix"])
    with pytest.raises(coherencia.ProyectoYModeloDivergenError) as exc:
        coherencia.assert_coherente(_Sesion(_Modelo(), proyecto),
                                    "Escribir una pagina")
    assert exc.value.code == "active_model_project_mismatch"
    assert "Escribir una pagina" in str(exc.value)


# ------------------------- no poder comprobar NO es lo mismo que estar mal ---
def test_sin_permisos_se_avisa_pero_no_se_bloquea(proyecto, monkeypatch):
    """La distincion que evita que esto estorbe.

    Si `open_files` devuelve None (permisos denegados, proceso desaparecido),
    no se sabe nada. Bloquear ahi convertiria una politica de seguridad
    estricta en un servidor inservible.
    """
    _con_archivos(monkeypatch, None)
    r = coherencia.check(_Sesion(_Modelo(), proyecto))
    assert r["state"] == coherencia.UNKNOWN
    # y sobre todo: NO lanza
    assert coherencia.assert_coherente(_Sesion(_Modelo(), proyecto), "X")


def test_un_proceso_sin_archivos_de_power_bi_es_desconocido(proyecto, monkeypatch):
    """No hay con que comparar: no se afirma divergencia."""
    _con_archivos(monkeypatch, [r"C:\Windows\System32\kernel32.dll"])
    assert coherencia.check(_Sesion(_Modelo(), proyecto))["state"] == \
        coherencia.UNKNOWN


def test_un_modelo_sin_pid_no_se_puede_comprobar(proyecto):
    r = coherencia.check(_Sesion(_Modelo(pid=None), proyecto))
    assert r["state"] == coherencia.UNKNOWN


# --------------------------------------------------- llega a los diagnosticos ---
def test_la_guia_lo_pone_lo_primero(proyecto, monkeypatch, tmp_path):
    """Un aviso grave al final de una lista larga no lo lee nadie."""
    from horizun_pbi_mcp.services import guide, project_state

    _con_archivos(monkeypatch, [r"C:\otra\Control_Acceso.pbix"])
    monkeypatch.setattr(project_state, "detect",
                        lambda _a, **_k: project_state.ProjectOpenState(
                            project_state.CLOSED, "high", "cerrado"))
    proyecto.has_tmdl = False
    proyecto.has_pbir = False
    s = guide.situacion(_Sesion(_Modelo(), proyecto))
    assert "DISTINTOS" in s["situation"]
    assert s["project"]["coherence"]["state"] == coherencia.DIFFERENT


# ============================ CRITICO 2: lo efimero se anuncia ==============
def test_una_escritura_en_vivo_grita_que_no_esta_persistida():
    """Antes era una nota al pie; se perdieron 5 medidas y 4 tarjetas.

    El aviso ya existia («usa Ctrl+S») pero viajaba como `note`, que se lee al
    final o no se lee. Ahora sale `persisted: False` Y un warning, que ademas
    hace que el envelope marque la respuesta como WARNING en vez de exito
    limpio.
    """
    from horizun_pbi_mcp.powerbi import model_writer
    from horizun_pbi_mcp.services import envelope

    salida = envelope.success({"action": "created", **model_writer._efimero()},
                              operation="pbi_create_measure",
                              request_id="r", duration_ms=1)
    assert salida["persisted"] is False
    assert salida["status"] == envelope.WARNING, (
        "un exito limpio se lee por encima; esto no puede pasar desapercibido")
    assert any("NO PERSISTIDO" in a for a in salida["warnings"])
    assert any("quedara rota" in a or "rota" in a for a in salida["warnings"]), (
        "hay que decir la CONSECUENCIA, no solo que no se guardo")


def test_el_aviso_de_fuente_viva_se_drena_una_sola_vez():
    """Se acumula al validar y lo vacia quien responde: no debe repetirse."""
    from horizun_pbi_mcp.tools import visual_tools

    visual_tools._AVISOS_DE_FUENTE.clear()
    visual_tools._AVISOS_DE_FUENTE.append(visual_tools._AVISO_FUENTE_VIVA)
    visual_tools._AVISOS_DE_FUENTE.append(visual_tools._AVISO_FUENTE_VIVA)

    primero = visual_tools.drenar_avisos_de_fuente()
    assert len(primero) == 1, "duplicados colapsados"
    assert "EN VIVO" in primero[0]
    assert visual_tools.drenar_avisos_de_fuente() == [], "ya se habia drenado"
