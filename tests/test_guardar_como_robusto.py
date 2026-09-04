"""El cuadro de guardado bajo contencion: carreras, no fallos de Power BI.

Cuarenta guardados seguidos con varias ventanas de Desktop abiertas dejaron
cuatro sintomas que se repetian y que el mismo request resolvia "al
reintentar": el nombre escrito a medias (`expected_len=182, actual_len=30`),
el foco robado a mitad del tecleo (`expected_len=66, actual_len=17`), el
desplegable de tipo leido antes de poblarse (`available=[]`) y una segunda
pulsacion de Guardar sobre un cuadro que ya estaba guardando.

Cada prueba de aqui reproduce uno de esos sintomas con un doble del cliente
de UI Automation y falla contra la implementacion anterior: la que escribia
solo por teclado, sin comprobar el foco, sin volver a localizar los controles
y sin distinguir "la lista no cargo" de "el formato no existe".
"""
from __future__ import annotations

import pytest

from horizun_pbi_mcp.powerbi import uia_helper
from tests.test_helper_sin_com import _Elemento, _UiaFalso


@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    monkeypatch.setattr(uia_helper.time, "sleep", lambda s: None)
    monkeypatch.setattr(uia_helper, "ESPERA_INTERFAZ", 0.05)


@pytest.fixture
def sin_teclado(monkeypatch):
    """Ni SendInput ni foco: lo que se prueba es la logica, no Windows."""
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t: tecleado.append(t))
    monkeypatch.setattr(uia_helper, "_primer_plano_es_de", lambda pid: True)
    return tecleado


# ============ 1) el nombre: SetValue primero, teclado de respaldo ==========
class _ConValuePattern(_UiaFalso):
    """El campo admite `ValuePattern.SetValue` y lo refleja al releer."""

    def __init__(self, *, acepta=True, **kw):
        super().__init__(**kw)
        self.acepta = acepta
        self.fijados = []

    def fijar_valor(self, elemento, texto):
        self.fijados.append(texto)
        if self.acepta:
            self.valor_nombre = texto
        return self.acepta


def test_la_ruta_se_pone_por_value_pattern_sin_tocar_el_teclado(sin_teclado):
    """`SetValue` no depende del foco ni de la cola de teclado."""
    ruta = r"C:\entrega\Informe.pbix"
    uia = _ConValuePattern()

    paso = uia_helper._escribir_ruta(uia, 22, ruta, 4321)   # noqa: SLF001

    assert paso["filename_verified"] is True
    assert paso["method"] == "value_pattern"
    assert uia.fijados == [ruta]
    assert sin_teclado == [], "se tecleo habiendo funcionado SetValue"
    assert paso["attempts_total"] == 1


def test_si_set_value_no_se_admite_se_teclea(sin_teclado):
    ruta = r"C:\entrega\Informe.pbix"
    uia = _ConValuePattern(acepta=False, valor_nombre=ruta)

    paso = uia_helper._escribir_ruta(uia, 22, ruta, 4321)   # noqa: SLF001

    assert paso["method"] == "keyboard"
    assert paso["methods_tried"] == ["keyboard"]
    assert sin_teclado == [ruta]


def test_una_escritura_parcial_se_reintenta_localizando_el_campo_de_nuevo(
        sin_teclado):
    """`expected_len=182, actual_len=30`: el primer intento quedo a medias.

    Antes era un error definitivo. Ahora es transitorio: se vuelve a buscar
    el control -no se reutiliza la referencia- y se escribe otra vez.
    """
    ruta = r"C:\entrega\Informe_con_nombre_largo.pbix"

    class _AMedias(_UiaFalso):
        def __init__(self):
            super().__init__()
            self.localizaciones = 0
            self.escrituras = 0

        def por_id(self, raiz, automation_id, tipo):
            if automation_id == uia_helper.AUTOMATION_ID_NOMBRE:
                self.localizaciones += 1
            return super().por_id(raiz, automation_id, tipo)

        def valor(self, elemento):
            # Primera vuelta: 30 caracteres. Segunda: entera.
            return ruta[:30] if self.escrituras < 2 else ruta

        def enfocar(self, elemento):
            self.escrituras += 1
            return True

    uia = _AMedias()
    paso = uia_helper._escribir_ruta(uia, 22, ruta, 4321)   # noqa: SLF001

    assert paso["filename_verified"] is True
    assert paso["attempts_total"] == 2
    assert paso["attempts"][0]["ok"] is False
    assert paso["attempts"][0]["reason"] == "partial_write"
    assert uia.localizaciones == 2, "el segundo intento reutilizo el control"


def test_tres_escrituras_parciales_seguidas_agotan_los_intentos(sin_teclado):
    ruta = r"C:\entrega\Informe.pbix"
    uia = _UiaFalso(valor_nombre=ruta[:17])

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(uia, 22, ruta, 4321)       # noqa: SLF001

    assert fallo.value.transitoria is True
    assert fallo.value.detalles["attempts_total"] == uia_helper.INTENTOS_POR_FASE
    assert fallo.value.detalles["actual_len"] == 17
    assert fallo.value.detalles["expected_len"] == len(ruta)
    assert all(a["reason"] == "partial_write"
               for a in fallo.value.detalles["attempts"])


def test_con_el_foco_en_otro_proceso_no_se_teclea(monkeypatch):
    """`expected_len=66, actual_len=17`: las teclas fueron a otra ventana.

    Sin el foco en el proceso verificado no se manda ni una tecla. Se intenta
    recuperar el frente; si no se puede, el intento se declara `focus_lost`.
    """
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t: tecleado.append(t))
    monkeypatch.setattr(uia_helper, "_primer_plano_es_de", lambda pid: False)
    monkeypatch.setattr(uia_helper, "traer_al_frente", lambda h, p: False)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._escribir_ruta(_UiaFalso(), 22,             # noqa: SLF001
                                  r"C:\entrega\Informe.pbix", 4321)

    assert tecleado == [], "se tecleo sin tener el foco"
    assert fallo.value.detalles["reason"] == "focus_lost"
    assert fallo.value.detalles["attempts_total"] == 3


def test_si_el_foco_se_recupera_se_teclea_y_se_verifica(monkeypatch):
    ruta = r"C:\entrega\Informe.pbix"
    tecleado = []
    monkeypatch.setattr(uia_helper, "seleccionar_todo", lambda: None)
    monkeypatch.setattr(uia_helper, "escribir_texto_real",
                        lambda t: tecleado.append(t))
    monkeypatch.setattr(uia_helper, "_primer_plano_es_de", lambda pid: False)
    frentes = []
    monkeypatch.setattr(uia_helper, "traer_al_frente",
                        lambda h, p: frentes.append((h, p)) or True)

    paso = uia_helper._escribir_ruta(_UiaFalso(valor_nombre=ruta), 22,  # noqa: SLF001
                                     ruta, 4321)

    assert paso["method"] == "keyboard"
    assert frentes == [(22, 4321)]
    assert tecleado == [ruta]


# ================ 2) controles que todavia no existen =====================
def test_un_control_ausente_se_espera_y_se_vuelve_a_buscar(sin_teclado):
    """El cuadro comun monta sus controles por partes."""
    ruta = r"C:\entrega\Informe.pbix"

    class _Tardio(_UiaFalso):
        def __init__(self):
            super().__init__(valor_nombre=ruta)
            self.consultas = 0

        def por_id(self, raiz, automation_id, tipo):
            if automation_id == uia_helper.AUTOMATION_ID_NOMBRE:
                self.consultas += 1
                if self.consultas < 3:
                    return None
            return super().por_id(raiz, automation_id, tipo)

    uia = _Tardio()
    paso = uia_helper._escribir_ruta(uia, 22, ruta, 4321)   # noqa: SLF001

    assert paso["filename_verified"] is True
    assert paso["attempts_total"] == 3
    assert [a["reason"] for a in paso["attempts"][:2]] == [
        "control_missing", "control_missing"]


# ============ 3) la lista sin cargar NO es "no ofrece el formato" =========
def test_una_lista_que_nunca_carga_es_transitoria_y_lo_dice():
    class _Vacia(_UiaFalso):
        def items(self, combo):
            return []

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(_Vacia(), 22, ".pbix")       # noqa: SLF001

    assert fallo.value.detalles["reason"] == "file_type_list_not_loaded"
    assert fallo.value.detalles["list_loaded"] is False
    assert fallo.value.transitoria is True
    assert fallo.value.detalles["attempts_total"] == 3


def test_un_formato_ausente_es_definitivo_y_no_se_reintenta():
    uia = _UiaFalso(opciones=["Archivos de plantilla de Power BI (*.pbit)"])

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._elegir_tipo(uia, 22, ".pbix")            # noqa: SLF001

    assert fallo.value.detalles["reason"] == "file_type_not_offered"
    assert fallo.value.detalles["list_loaded"] is True
    assert fallo.value.transitoria is False
    assert fallo.value.detalles["attempts_total"] == 1, (
        "un formato que no existe no aparece por insistir")
    assert uia.expandidos == 1


def test_una_lista_que_carga_al_segundo_intento_termina_bien():
    class _Lenta(_UiaFalso):
        def __init__(self):
            super().__init__(valor_tipo="Archivo de Power BI (*.pbix)",
                             estado_tras=uia_helper.ESTADO_CERRADO)
            self.vueltas = 0
            self._todas = list(self.opciones)

        def expandir(self, combo):
            self.vueltas += 1
            self.expandidos += 1

        def items(self, combo):
            return [] if self.vueltas < 2 else self._todas

    uia = _Lenta()
    paso = uia_helper._elegir_tipo(uia, 22, ".pbix")         # noqa: SLF001

    assert paso["file_type_selected"] == "Archivo de Power BI (*.pbix)"
    assert paso["attempts_total"] == 2
    assert paso["attempts"][0]["reason"] == "file_type_list_not_loaded"


# ========= 4) Guardar no se repite si ya paso algo; si no, si ===========
def test_no_se_repite_guardar_si_el_cuadro_ya_se_cerro(monkeypatch, tmp_path):
    """La primera pulsacion tardo en verse; pulsar otra vez abriria 'reemplazar'."""
    # La espera corta tras la primera pulsacion no ve el cierre; la
    # comprobacion posterior si: el cuadro ya no esta.
    estados = iter([False])
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto",
                        lambda h: next(estados, False))
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: False)
    confirmaciones = []
    monkeypatch.setattr(uia_helper, "_confirmar",
                        lambda u, h, p: confirmaciones.append(1) or
                        {"commit_method": "invoke", "attempts": []})

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0)

    assert len(confirmaciones) == 1, "se pulso Guardar dos veces"
    assert salida["commit_evidence"]["dialog_closed"] is True
    assert salida["already_committed"] is None


def test_una_repeticion_se_omite_si_al_ir_a_pulsar_ya_estaba_cerrado(
        monkeypatch, tmp_path):
    """Entre la espera y la segunda pulsacion, el cuadro se cerro: no se pulsa."""
    estados = iter([True, False])   # tras el 1er intento: abierto; al repetir: cerrado
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto",
                        lambda h: next(estados, False))
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: False)
    confirmaciones = []
    monkeypatch.setattr(uia_helper, "_confirmar",
                        lambda u, h, p: confirmaciones.append(1) or
                        {"commit_method": "invoke", "attempts": []})

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "a.pbix"), plazo=5, desde=0.0)

    assert len(confirmaciones) == 1
    assert salida["already_committed"] == {"attempt": 2, "dialog_closed": True,
                                           "file_appeared": False}


def test_no_se_repite_guardar_si_el_archivo_ya_aparecio(monkeypatch, tmp_path):
    destino = tmp_path / "a.pbix"
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: False)
    confirmaciones = []

    def _confirmar(u, h, p):
        confirmaciones.append(1)
        destino.write_bytes(b"PK")
        return {"commit_method": "invoke", "attempts": []}

    monkeypatch.setattr(uia_helper, "_confirmar", _confirmar)

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(destino), plazo=5, desde=0.0)

    assert len(confirmaciones) == 1
    assert salida["commit_evidence"]["file_appeared"] is True


def test_si_no_paso_nada_se_repite_hasta_tres_veces(monkeypatch, tmp_path):
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: False)
    confirmaciones = []
    monkeypatch.setattr(uia_helper, "_confirmar",
                        lambda u, h, p: confirmaciones.append(1) or
                        {"commit_method": "invoke", "attempts": []})

    salida = uia_helper._confirmar_con_verificacion(       # noqa: SLF001
        _UiaFalso(), 22, 4321, str(tmp_path / "nunca.pbix"), plazo=5, desde=0.0)

    assert len(confirmaciones) == uia_helper.INTENTOS_POR_FASE
    assert salida["dialog_closed"] is False
    assert salida["attempts_total"] == 3


# ========== 5) ante fallo definitivo, se cancela SOLO nuestro cuadro ========
def _secuencia(monkeypatch, uia):
    monkeypatch.setattr(uia_helper, "Uia", lambda: uia)
    monkeypatch.setattr(uia_helper, "verificar_proceso",
                        lambda pid, arranque: {"pid": pid, "create_time": 1.0})
    monkeypatch.setattr(uia_helper, "_ventana_principal",
                        lambda pid: {"hwnd": 11, "title": "Demo"})
    monkeypatch.setattr(uia_helper, "_enviar_teclas", lambda e: None)
    monkeypatch.setattr(uia_helper, "traer_al_frente", lambda h, p: True)
    monkeypatch.setattr(uia_helper, "_esperar_cuadro",
                        lambda u, pid, plazo: {"hwnd": 22})
    monkeypatch.setattr(uia_helper, "_modales", lambda u, pid, ex: [])


def test_un_fallo_definitivo_cancela_el_cuadro_y_lo_cuenta(monkeypatch):
    class _SinPbix(_UiaFalso):
        def __init__(self):
            super().__init__(opciones=["Archivos de plantilla (*.pbit)"])
            self.cancelados = 0

        def por_id(self, raiz, automation_id, tipo):
            if automation_id == uia_helper.AUTOMATION_ID_CANCELAR:
                return _Elemento("Cancelar", automation_id)
            return super().por_id(raiz, automation_id, tipo)

        def invocar(self, elemento):
            if elemento.CurrentAutomationId == uia_helper.AUTOMATION_ID_CANCELAR:
                self.cancelados += 1
                abierto["v"] = False
            return "invoke"

    abierto = {"v": True}
    uia = _SinPbix()
    _secuencia(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto",
                        lambda h: abierto["v"])
    monkeypatch.setattr(uia_helper, "_duenio_de_ventana", lambda h: 4321)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321,
                                 "out_path": r"C:\x\a.pbix"})

    limpieza = fallo.value.detalles["cleanup"]
    assert limpieza["attempted"] is True
    assert limpieza["dialog_closed"] is True
    assert uia.cancelados == 1
    assert fallo.value.detalles["steps"][-1]["phase"] == "cuadro"


def test_no_se_cancela_un_cuadro_que_ya_no_es_del_proceso(monkeypatch):
    """Si el hwnd cambio de dueño, pulsar Cancelar ahi es pulsar en otro sitio."""
    uia = _UiaFalso(opciones=["Archivos de plantilla (*.pbit)"])
    _secuencia(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: True)
    monkeypatch.setattr(uia_helper, "_duenio_de_ventana", lambda h: 9999)

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper.guardar_como({"desktop_pid": 4321,
                                 "out_path": r"C:\x\a.pbix"})

    limpieza = fallo.value.detalles["cleanup"]
    assert limpieza["attempted"] is False
    assert limpieza["dialog_closed"] is False
    assert uia.invocados == [], "se invoco algo en una ventana ajena"


# ================= 6) la evidencia viaja entera, con intentos ============
def test_la_respuesta_lleva_los_intentos_de_cada_fase(monkeypatch, sin_teclado):
    ruta = r"C:\entrega\a.pbix"
    uia = _ConValuePattern(valor_tipo="Archivo de Power BI (*.pbix)",
                           estado_tras=uia_helper.ESTADO_CERRADO)
    _secuencia(monkeypatch, uia)
    monkeypatch.setattr(uia_helper, "_cuadro_sigue_abierto", lambda h: False)
    monkeypatch.setattr(uia_helper, "_esperar_cierre", lambda h, p: True)

    salida = uia_helper.guardar_como({"desktop_pid": 4321,
                                      "desktop_started": 1.0,
                                      "out_path": ruta})

    fases = {p["phase"]: p for p in salida["steps"]}
    assert fases["tipo"]["attempts_total"] == 1
    assert fases["nombre"]["method"] == "value_pattern"
    assert fases["nombre"]["attempts"][0]["ok"] is True
    assert fases["guardar"]["attempts_total"] == 1
    assert salida["filename_method"] == "value_pattern"
    assert salida["template_dialog"] is None


def test_el_tope_de_tiempo_por_fase_corta_los_reintentos(monkeypatch):
    """Tres intentos, pero nunca mas alla del plazo de la fase."""
    reloj = iter([0.0, 0.0, 0.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0, 30.0])
    monkeypatch.setattr(uia_helper.time, "monotonic",
                        lambda: next(reloj, 30.0))
    intentos = []

    def _falla(n):
        intentos.append(n)
        raise uia_helper.HelperError("x", "no", transitoria=True, reason="r")

    with pytest.raises(uia_helper.HelperError) as fallo:
        uia_helper._con_intentos("x", _falla, plazo=10.0)     # noqa: SLF001

    assert intentos == [1], "siguio reintentando con el plazo agotado"
    assert fallo.value.detalles["attempts_total"] == 1


# ================= 7) el protocolo: `transient` sale al padre ==============
def test_el_padre_recibe_si_el_fallo_fue_transitorio(monkeypatch):
    import io
    import json
    import sys

    def _revienta(peticion):
        raise uia_helper.HelperError("nombre", "a medias", transitoria=True,
                                     reason="partial_write")

    monkeypatch.setitem(uia_helper.ACCIONES, "save_as", _revienta)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"action": "save_as"})))
    salida = io.StringIO()
    monkeypatch.setattr(sys, "stdout", salida)

    assert uia_helper.main() == 1
    datos = json.loads(salida.getvalue())
    assert datos["transient"] is True
    assert datos["details"]["reason"] == "partial_write"
